#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.engine import (
    _rank_full_candidate_calculations,
    build_default_construction_cases,
    run_calculation,
    run_single_candidate_calculation,
)
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.services.support_topology_contract import support_topology_hash
from app.version import version_manifest

SHAPES = {
    "L": [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)],
    "U": [(0, 0), (80, 0), (80, 50), (55, 50), (55, 20), (25, 20), (25, 50), (0, 50)],
    "T": [(0, 0), (80, 0), (80, 20), (50, 20), (50, 60), (30, 60), (30, 20), (0, 20)],
    "Z": [(0, 0), (50, 0), (50, 40), (80, 40), (80, 60), (30, 60), (30, 20), (0, 20)],
    "H": [(0, 0), (20, 0), (20, 25), (60, 25), (60, 0), (80, 0), (80, 60), (60, 60), (60, 35), (20, 35), (20, 60), (0, 60)],
}
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def make_project(name: str, raw: list[tuple[float, float]], template: str = "none") -> Project:
    excavation = make_excavation_model(
        name,
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in raw], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template=template),
    )
    return Project(name=name, excavation=excavation, retainingSystem=system)


def compact_quality(project: Project) -> dict[str, Any]:
    quality = evaluate_support_layout_quality(project)
    system = project.retaining_system
    audit = dict((system.layout_summary or {}).get("transferSystem") or {})
    readiness = dict(audit.get("readiness") or {})
    frame = dict(audit.get("frameAnalysis") or {})
    role_counts = Counter(str(beam.beam_role) for beam in system.ring_beams)
    return {
        "supportCount": len(system.supports),
        "columnCount": len(system.columns),
        "transferBeamCount": len(system.ring_beams),
        "transferBeamRoles": dict(sorted(role_counts.items())),
        "qualityStatus": quality.status,
        "qualityScore": quality.score,
        "failCount": sum(item.severity == "fail" for item in quality.issues),
        "warningCount": sum(item.severity == "warning" for item in quality.issues),
        "failCategories": sorted({item.category for item in quality.issues if item.severity == "fail"}),
        "readiness": readiness,
        "templateId": audit.get("templateId"),
        "topologyClass": audit.get("topologyClass"),
        "faceCoverageComplete": audit.get("faceCoverageComplete"),
        "ringClosed": audit.get("ringClosed"),
        "nominalFrame": {
            "status": frame.get("status"),
            "levelCount": frame.get("levelCount"),
            "solvedLevelCount": frame.get("solvedLevelCount"),
            "maximumDisplacementM": frame.get("maximumDisplacementM"),
            "maximumRelativeResidual": frame.get("maximumRelativeResidual"),
            "maximumConditionNumber": frame.get("maximumConditionNumber"),
        },
    }


def full_calculation_summary(project: Project) -> dict[str, Any]:
    project.calculation_cases = build_default_construction_cases(project)
    before_hash = support_topology_hash(project)
    started = time.perf_counter()
    result = run_calculation(
        project,
        project.calculation_cases[0],
        auto_repair=False,
        include_candidate_comparison=False,
    )
    elapsed = time.perf_counter() - started
    audit = dict((project.retaining_system.layout_summary or {}).get("transferSystem") or {})
    frame = dict(project.advanced_engineering.get("concaveTransferFrameAnalysis") or {})
    detailing = dict(project.advanced_engineering.get("concaveTransferAutoDetailing") or {})
    metrics = dict(detailing.get("metrics") or {})
    beam_rows = list(detailing.get("beamSchedule") or [])
    maxima = {
        "axialForceKn": max((abs(float(row.get("axialForceKn") or 0.0)) for row in beam_rows), default=0.0),
        "momentKnm": max((abs(float(row.get("momentKnm") or 0.0)) for row in beam_rows), default=0.0),
        "shearKn": max((abs(float(row.get("shearKn") or 0.0)) for row in beam_rows), default=0.0),
    }
    return {
        "elapsedSeconds": round(elapsed, 3),
        "stageCount": len(result.stage_results),
        "stageFrameStatus": frame.get("status"),
        "stageFrameCount": frame.get("stageCount"),
        "maximumFrameDisplacementM": frame.get("maximumDisplacementM"),
        "maximumRelativeResidual": frame.get("maximumRelativeResidual"),
        "maximumConditionNumber": frame.get("maximumConditionNumber"),
        "governingSupportAxialForceKn": result.governing_values.max_support_axial_force,
        "transferBeamEnvelope": maxima,
        "transferBeamCount": metrics.get("transferBeamCount"),
        "designedTransferBeamCount": metrics.get("designedTransferBeamCount"),
        "ringNodeCount": metrics.get("ringNodeCount"),
        "detailedRingNodeCount": metrics.get("detailedRingNodeCount"),
        "autoDetailingStatus": detailing.get("status"),
        "formalCalculationReady": audit.get("formalCalculationReady"),
        "officialIssueReady": audit.get("officialIssueReady"),
        "formalGateAllowed": bool(result.formal_report_gate and result.formal_report_gate.allowed_for_official_issue),
        "formalGateBlockingCategories": sorted({
            item.category for item in (result.formal_report_gate.blocking_items if result.formal_report_gate else [])
        }),
        "topologyHashStableAcrossCalculation": before_hash == support_topology_hash(project) == result.support_topology_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate PitGuard V3.70 planar transfer and delivery closure")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-full-calculation", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "15")
    started = time.perf_counter()

    print("[v370-eval] baseline", flush=True)
    baseline = make_project("L legacy zoned direct", SHAPES["L"], "none")
    result: dict[str, Any] = {
        "version": version_manifest(),
        "evaluationScope": {
            "shapeSet": list(SHAPES),
            "transferTemplates": TEMPLATES,
            "excavationTopElevationM": 0.0,
            "excavationBottomElevationM": -16.0,
            "supportLevelCount": 3,
            "note": "Synthetic geometry regression without boreholes; formal project delivery remains gated by missing geology and professional approval.",
        },
        "phaseAcceptance": {
            "V3.67.0": "four-level readiness, explicit transfer identities, geometry regularisation and safe gates",
            "V3.68.0": "2D planar frame/truss solver, stage envelope, transfer beam design and frame columns",
            "V3.69.0": "topology-diverse A/B/C generation and complete-calculation-ready candidate metadata",
            "V3.70.0": "automatic detailing evidence, topology-current professional approval gate and UI disclosure",
        },
        "legacyBaseline": compact_quality(baseline),
        "lShapeTopologies": {},
        "shapeCoverage": {},
        "optimizer": {},
        "completeCandidateComparison": {},
        "fullCalculations": {},
    }

    print("[v370-eval] L topologies", flush=True)
    for template in TEMPLATES:
        result["lShapeTopologies"][template] = compact_quality(make_project(f"L {template}", SHAPES["L"], template))

    print("[v370-eval] shape coverage", flush=True)
    for shape_name, raw in SHAPES.items():
        print(f"[v370-eval] shape {shape_name}", flush=True)
        result["shapeCoverage"][shape_name] = {}
        for template in TEMPLATES:
            result["shapeCoverage"][shape_name][template] = compact_quality(
                make_project(f"{shape_name} {template}", raw, template)
            )

    print("[v370-eval] optimizer", flush=True)
    optimizer_project = make_project("L optimizer", SHAPES["L"], "compact_elbow_ring")
    optimizer_started = time.perf_counter()
    _best, candidates = optimize_support_layout_candidates(
        optimizer_project,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 15,
            "candidatePoolLimit": 10,
        },
    )
    result["optimizer"] = {
        "elapsedSeconds": round(time.perf_counter() - optimizer_started, 3),
        "candidateCount": len(candidates),
        "topologyDiversityCount": len({row.variable_summary.get("schemeFamily") for row in candidates}),
        "allHardConstraintsPassed": all(bool(row.hard_constraints.get("passed")) for row in candidates),
        "candidates": [
            {
                "rank": row.rank,
                "id": row.id,
                "score": row.score,
                "status": row.status,
                "templateId": row.variable_summary.get("transferSystemTemplate"),
                "topologyClass": row.variable_summary.get("transferTopologyClass"),
                "schemeFamily": row.variable_summary.get("schemeFamily"),
                "supportCount": row.support_count,
                "columnCount": row.column_count,
                "transferBeamCount": len(row.plan_geometry.get("transferBeams") or []),
                "hardConstraintPassed": bool(row.hard_constraints.get("passed")),
                "failCount": row.fail_count,
                "warningCount": row.warning_count,
            }
            for row in candidates
        ],
    }

    if not args.skip_full_calculation:
        print("[v370-eval] complete candidate comparison", flush=True)
        comparison_started = time.perf_counter()
        comparison_rows = []
        for index, candidate in enumerate(candidates):
            print(f"[v370-eval] candidate {index + 1}/3", flush=True)
            comparison_rows.append(run_single_candidate_calculation(optimizer_project, candidate, index=index, use_cache=False))
        _rank_full_candidate_calculations(comparison_rows)
        result["completeCandidateComparison"] = {
            "elapsedSeconds": round(time.perf_counter() - comparison_started, 3),
            "candidateCount": len(comparison_rows),
            "allStageFramesPassed": all(row.get("transferFrameStatus") == "pass" for row in comparison_rows),
            "rows": [
                {
                    key: row.get(key)
                    for key in (
                        "schemeLabel", "candidateId", "transferSystemTemplate", "transferTopologyClass",
                        "transferBeamCount", "supportCount", "columnCount", "decisionRank", "decisionScore",
                        "paretoFront", "paretoRank", "maxSupportAxialForce", "maxDisplacement",
                        "maxWaleMoment", "maxTransferFrameDisplacement", "maxTransferFrameResidual",
                        "transferFrameStatus", "formalCalculationReady", "autoDetailingStatus",
                        "failCount", "warningCount", "formalGateAllowed", "comparisonExecutionMode",
                    )
                }
                for row in sorted(comparison_rows, key=lambda item: item.get("schemeLabel", "Z"))
            ],
        }
        result["fullCalculations"] = {
            str(row.get("transferSystemTemplate") or row.get("schemeLabel")): {
                key: row.get(key)
                for key in (
                    "schemeLabel", "candidateId", "transferTopologyClass", "transferBeamCount",
                    "supportCount", "columnCount", "maxSupportAxialForce", "maxDisplacement",
                    "maxWaleMoment", "maxTransferFrameDisplacement", "maxTransferFrameResidual",
                    "maxTransferFrameConditionNumber", "transferFrameStatus", "transferFrameStageCount",
                    "formalCalculationReady", "autoDetailingStatus", "failCount", "warningCount",
                    "formalGateAllowed", "decisionRank", "decisionScore", "paretoFront", "paretoRank",
                )
            }
            for row in comparison_rows
        }

    result["elapsedSeconds"] = round(time.perf_counter() - started, 3)
    print("[v370-eval] serialize", flush=True)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(text)


if __name__ == "__main__":
    main()
