#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.drawings.detail_sheets import generate_construction_detail_sheets
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_core_v387 import (
    add_external_collaboration,
    build_delivery_quality,
    build_design_core_workflow,
    build_member_envelopes,
    build_parameter_confirmation,
    build_reinforcement_closure,
    build_release_qualification,
    build_rule_evidence,
    build_scheme_search_assurance,
    prepare_design_snapshot,
)
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.services.support_layout import SupportLayoutConfig
from app.version import version_manifest

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
REPORT_SECTIONS = [
    "project_overview", "design_basis", "geology_groundwater", "surroundings", "parameters",
    "scheme", "analysis_model", "loads", "design_stages", "wall_results", "support_results",
    "stability", "reinforcement", "adverse_scenarios", "conclusions", "manual_review",
]


def build_project() -> Project:
    excavation = make_excavation_model(
        "V3.87 design-core evaluation",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    project = Project(name="V3.87 design-core evaluation", excavation=excavation, retainingSystem=retaining)
    project.calculation_cases = build_default_construction_cases(project)
    return project


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "releases" / "V3_87_0_DESIGN_CORE_EVALUATION.json",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ROOT / "runtime" / "v387-evaluation",
    )
    args = parser.parse_args()
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    project = build_project()
    print("[v387] project built", flush=True)

    started = time.perf_counter()
    calculation = run_calculation(project, project.calculation_cases[0], auto_repair=False)
    calculation_seconds = time.perf_counter() - started
    print("[v387] calculation complete", flush=True)

    if not any(row.id == calculation.id for row in project.calculation_results):
        project.calculation_results.append(calculation)

    drawing_dir = args.artifact_dir / "drawings"
    drawings = generate_construction_detail_sheets(project, drawing_dir)
    calculation.drawing_sheets = drawings
    print("[v387] drawings complete", flush=True)
    project.advanced_engineering["calculationReportSections"] = list(REPORT_SECTIONS)

    search_started = time.perf_counter()
    worker_dir = args.artifact_dir / "candidate-workers"
    worker_script = ROOT / "scripts" / "evaluate-v387-candidate-worker.py"
    candidate_rows = []
    candidate_full_summaries = []
    candidate_worker_runtime = []
    for template in ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]:
        output_path = worker_dir / f"{template}.json"
        if not output_path.exists():
            subprocess.run(
                [sys.executable, str(worker_script), "--template", template, "--output", str(output_path)],
                check=True, timeout=90, cwd=str(ROOT),
            )
        worker_payload = json.loads(output_path.read_text(encoding="utf-8"))
        candidate_worker_runtime.append(dict(worker_payload.get("runtime") or {}))
        candidate_row = dict(worker_payload["candidate"])
        raw_summary = dict(worker_payload["summary"])
        keep = [
            "schemeLabel", "rank", "score", "supportCount", "columnCount", "transferBeamCount",
            "transferSystemTemplate", "transferTopologyClass", "formalCalculationReady",
            "maxDisplacement", "maxSupportAxialForce", "maxWallMoment", "maxWaleMoment",
            "minStabilitySafetyFactor", "strengthStatus", "stiffnessStatus", "stabilityStatus",
            "formalGateStatus", "formalGateAllowed", "failCount", "warningCount", "calculationResultId",
        ]
        compact_summary = {key: raw_summary.get(key) for key in keep}
        compact_summary["candidateId"] = candidate_row["id"]
        candidate_row["fullCalculation"] = compact_summary
        candidate_rows.append(candidate_row)
        candidate_full_summaries.append(compact_summary)
    selected_candidate = next(
        (row for row in candidate_rows if (row.get("variableSummary") or {}).get("transferSystemTemplate") == "junction_hub_frame"),
        candidate_rows[0] if candidate_rows else None,
    )
    if project.retaining_system:
        layout_summary = dict(project.retaining_system.layout_summary or {})
        layout_summary["candidateSchemes"] = candidate_rows
        layout_summary["selectedCandidateId"] = selected_candidate.get("id") if selected_candidate else None
        layout_summary["candidateSearchMethod"] = "v3.87-five-level-design-search-isolated-workers"
        project.retaining_system.layout_summary = layout_summary
    cached_candidate_load_seconds = time.perf_counter() - search_started
    actual_candidate_seconds = sum(float(row.get("totalSeconds", 0.0) or 0.0) for row in candidate_worker_runtime)
    print("[v387] candidates complete", flush=True)

    report_started = time.perf_counter()
    report_project_path = args.artifact_dir / "report-project.json"
    report_project_path.write_text(project.model_dump_json(by_alias=True), encoding="utf-8")
    report_worker = ROOT / "scripts" / "evaluate-v387-report-worker.py"
    subprocess.run(
        [
            sys.executable, str(report_worker),
            "--project-json", str(report_project_path),
            "--output-dir", str(args.artifact_dir / "report"),
        ],
        check=True, timeout=120, cwd=str(ROOT),
    )
    report_path = args.artifact_dir / "report" / f"{project.id}_calculation_report.docx"
    if not report_path.exists():
        report_candidates = sorted((args.artifact_dir / "report").glob("*_calculation_report.docx"))
        if not report_candidates:
            raise RuntimeError("V3.87 report worker did not produce a DOCX report")
        report_path = report_candidates[-1]
    report_seconds = time.perf_counter() - report_started
    print("[v387] report complete", flush=True)
    project.advanced_engineering["calculationReportManifest"] = {
        "path": str(report_path),
        "sectionIds": list(REPORT_SECTIONS),
        "sourceCalculationResultId": calculation.id,
        "resultHash": calculation.result_hash,
    }

    print("[v387] core services start", flush=True)
    parameter = build_parameter_confirmation(project)
    rules = build_rule_evidence(project)
    schemes = build_scheme_search_assurance(project)
    envelopes = build_member_envelopes(project)
    rebar = build_reinforcement_closure(project)
    delivery = build_delivery_quality(project)
    print("[v387] core services partial", flush=True)
    workflow = build_design_core_workflow(project)
    print("[v387] workflow complete", flush=True)
    snapshot_result = prepare_design_snapshot(project, purpose="internal_review", actor="evaluation", persist=True)
    print("[v387] snapshot complete", flush=True)
    snapshot = snapshot_result["manifest"]
    collaboration = add_external_collaboration(
        project,
        {
            "category": "field_feedback",
            "title": "示例外部反馈",
            "sourceParty": "建设单位",
            "summary": "现场反馈仅作为设计复核输入，不进入首次设计发行必填条件。",
            "designReviewRequired": True,
        }
    )
    docs = build_online_documentation()
    print("[v387] docs complete", flush=True)
    release = build_release_qualification(project)

    execution = dict(calculation.calculation_execution or {})
    health = dict(calculation.numerical_health or {})
    completeness = dict(calculation.result_completeness or {})
    formal_gate = calculation.formal_report_gate
    stage_status = {row["stageId"]: row for row in workflow["stages"]}

    payload = {
        "schema": "pitguard-v3.87-design-core-evaluation-v1",
        "release": version_manifest(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "candidateWorkerActualSeconds": round(actual_candidate_seconds, 3),
            "candidateCachedLoadSeconds": round(cached_candidate_load_seconds, 3),
            "candidateWorkerRuntime": candidate_worker_runtime,
            "calculationSeconds": round(calculation_seconds, 3),
            "reportSeconds": round(report_seconds, 3),
        },
        "project": {
            "shape": "L orthogonal concave",
            "depthM": 16.0,
            "stageCount": len(project.calculation_cases[0].stages),
            "wallCount": len(project.retaining_system.diaphragm_walls),
            "supportCount": len(project.retaining_system.supports),
            "waleCount": len(project.retaining_system.wale_beams),
            "transferBeamCount": len(project.retaining_system.ring_beams),
            "columnCount": len(project.retaining_system.columns),
        },
        "calculation": {
            "engineeringStatus": execution.get("engineeringStatus"),
            "deliveryStatus": execution.get("deliveryStatus"),
            "numericalHealthStatus": health.get("status"),
            "maximumScaledConditionNumber": health.get("maximumScaledConditionNumber"),
            "maximumRelativeResidual": health.get("maximumRelativeResidual"),
            "formalIssueAllowed": bool(formal_gate and formal_gate.allowed_for_official_issue),
            "formalGateStatus": formal_gate.status if formal_gate else None,
            "engineeringCompletenessPercent": completeness.get("engineeringCompletenessPercent"),
            "engineeringReadinessPercent": completeness.get("engineeringReadinessPercent"),
            "formalIssueReadinessPercent": completeness.get("formalIssueReadinessPercent"),
        },
        "parameterGovernance": {
            "status": parameter["status"],
            "recordCount": parameter["total"],
            "confirmedCount": parameter["confirmed"],
            "formalAllowedCount": parameter["formalAllowed"],
            "formalBlockerCount": parameter["formalBlockerCount"],
            "sourceCounts": parameter["sourceCounts"],
        },
        "ruleEvidence": {
            "ruleCount": rules["ruleCount"],
            "executedRuleCount": rules["executedRuleCount"],
            "coverageRatio": rules["coverageRatio"],
            "unmappedCheckCount": rules["unmappedCheckCount"],
            "boundary": rules["boundary"],
        },
        "schemeSearch": {
            "status": schemes["status"],
            "candidateCount": schemes["candidateCount"],
            "familyCount": schemes["familyCount"],
            "familyDiversityRatio": schemes["familyDiversityRatio"],
            "fullyCalculatedCount": schemes["fullyCalculatedCount"],
            "selectedCandidateFullyCalculated": schemes["selectedCandidateFullyCalculated"],
            "blockers": schemes["blockers"],
        },
        "memberEnvelope": {
            "status": envelopes["status"],
            "recordCount": envelopes["recordCount"],
            "objectCount": envelopes.get("objectCount", 0),
            "responseCounts": envelopes.get("responseCounts", {}),
        },
        "reinforcementClosure": {
            "status": rebar["status"],
            "componentCount": rebar["componentCount"],
            "failCount": rebar["failCount"],
            "warningCount": rebar["warningCount"],
            "sectionFeedbackRequiredCount": rebar["sectionFeedbackRequiredCount"],
        },
        "deliveryQuality": {
            "status": delivery["status"],
            "drawingGeneratedCount": len(drawings),
            "requiredDrawingTypeCount": len(delivery["requiredDrawingTypes"]),
            "presentDrawingTypeCount": len(delivery["presentDrawingTypes"]),
            "missingDrawingTypes": delivery["missingDrawingTypes"],
            "requiredReportSectionCount": len(delivery["requiredReportSections"]),
            "presentReportSectionCount": len(delivery["presentReportSections"]),
            "missingReportSections": delivery["missingReportSections"],
            "blockers": delivery["blockers"],
            "reportPath": str(report_path.relative_to(ROOT)),
        },
        "designCoreWorkflow": {
            "status": workflow["status"],
            "overallReadiness": workflow["overallReadiness"],
            "stageCount": len(workflow["stages"]),
            "stageReadiness": {key: value["readiness"] for key, value in stage_status.items()},
            "externalCollaborationBoundary": workflow["externalCollaboration"]["boundary"],
            "legacyPrimaryWorkflowUsage": workflow["legacyConstructionFieldModules"]["primaryWorkflowUsage"],
        },
        "snapshot": {
            "id": snapshot.get("id"),
            "status": snapshot.get("status"),
            "purpose": snapshot.get("purpose"),
            "blockerCount": len(snapshot.get("blockers") or []),
            "consistencyHash": snapshot.get("consistencyHash"),
        },
        "externalCollaboration": {
            "recordId": collaboration["record"]["id"],
            "designReviewRequestCreated": bool(collaboration.get("reviewRequest")),
            "constructionPlanRequired": False,
            "fieldSnapshotRequired": False,
            "deviationEventRequired": False,
        },
        "onlineDocumentation": {
            "version": docs.get("version"),
            "chapterIds": [row.get("id") for row in docs.get("chapters") or []],
            "containsDesignCore": "design-core" in [row.get("id") for row in docs.get("chapters") or []],
            "containsSchemeSearch": "scheme-search" in [row.get("id") for row in docs.get("chapters") or []],
            "containsRebarClosure": "rebar-closure" in [row.get("id") for row in docs.get("chapters") or []],
            "containsDeliveryQc": "delivery-qc" in [row.get("id") for row in docs.get("chapters") or []],
        },
        "productionQualification": release,
        "limitations": [
            "The evaluation uses a synthetic L-shaped pit without verified boreholes, strata, groundwater evidence or professional approval.",
            "Generated drawing sheets are traceable engineering source sheets; final construction issue remains controlled by calculation, reinforcement, drawing and review gates.",
            "Frontend dependency installation, Vitest and Vite production build are not asserted by this evaluation.",
            "Full historical backend test suite and real-project/external-software batch validation remain incomplete.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
