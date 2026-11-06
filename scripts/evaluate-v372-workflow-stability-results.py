#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation import engine
from app.calculation.engine import build_default_construction_cases, run_calculation
from app.calculation.opensees_benchmark import (
    run_independent_reference_benchmark_suite,
    run_opensees_planar_benchmark_suite,
)
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.concave_transfer_delivery import _benchmark_certificate
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.version import version_manifest

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def project_for(template: str) -> Project:
    excavation = make_excavation_model(
        f"V3.72 evaluation {template}",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template=template),
    )
    project = Project(name=f"V3.72 evaluation {template}", excavation=excavation, retainingSystem=system)
    project.calculation_cases = build_default_construction_cases(project)
    return project


def summarize(template: str) -> dict[str, Any]:
    project = project_for(template)
    started = time.perf_counter()
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False)
    elapsed = time.perf_counter() - started
    execution = dict(result.calculation_execution or {})
    health = dict(result.numerical_health or {})
    completeness = dict(result.result_completeness or {})
    catalog = dict(result.result_catalog or {})
    iteration = dict((project.advanced_engineering or {}).get("wallWaleTransferReactionIteration") or {})
    transfer = dict((project.retaining_system.layout_summary or {}).get("transferSystem") or {})
    return {
        "template": template,
        "topologyClass": transfer.get("topologyClass"),
        "elapsedSeconds": round(elapsed, 3),
        "transaction": dict((project.advanced_engineering or {}).get("lastCalculationTransaction") or {}),
        "executionStatus": execution.get("status"),
        "executionPhaseCount": execution.get("phaseCount"),
        "executionBottleneck": execution.get("bottleneckPhase"),
        "executionPhases": execution.get("phases") or [],
        "engineeringCompletenessPercent": completeness.get("engineeringCompletenessPercent"),
        "formalIssueCompletenessPercent": completeness.get("formalIssueCompletenessPercent"),
        "engineeringReadinessPercent": completeness.get("engineeringReadinessPercent"),
        "formalIssueReadinessPercent": completeness.get("formalIssueReadinessPercent"),
        "criticalBlockingDomains": completeness.get("criticalBlockingDomains") or [],
        "completenessDomains": completeness.get("domains") or [],
        "numericalStatus": health.get("status"),
        "maximumScaledConditionNumber": health.get("maximumScaledConditionNumber"),
        "maximumRelativeResidual": health.get("maximumRelativeResidual"),
        "fallbackCount": health.get("fallbackCount"),
        "blockedSystemCount": health.get("blockedSystemCount"),
        "reactionIteration": health.get("reactionIteration"),
        "adaptiveIterationDiagnostics": {
            "schema": iteration.get("schema"),
            "status": iteration.get("status"),
            "iterationCount": iteration.get("iterationCount"),
            "convergenceQuality": iteration.get("convergenceQuality"),
            "forceRelativeResidual": iteration.get("finalForceRelativeResidual"),
            "forceAbsoluteResidualKn": iteration.get("finalForceAbsoluteResidualKn"),
            "displacementRelativeResidual": iteration.get("finalDisplacementRelativeResidual"),
            "displacementAbsoluteResidualMm": iteration.get("finalDisplacementAbsoluteResidualMm"),
            "relaxationHistory": iteration.get("relaxationHistory"),
            "oscillationDetected": iteration.get("oscillationDetected"),
            "stagnationDetected": iteration.get("stagnationDetected"),
        },
        "resultCatalogCounts": catalog.get("counts") or {},
        "criticalStages": (catalog.get("criticalStages") or [])[:5],
        "topWallEnvelopes": (catalog.get("wallEnvelopes") or [])[:5],
        "topSupportEnvelopes": (catalog.get("supportEnvelopes") or [])[:5],
        "topWaleEnvelopes": (catalog.get("waleEnvelopes") or [])[:5],
        "topNodeHotspots": (catalog.get("nodeHotspots") or [])[:5],
        "columnFoundationEnvelopes": (catalog.get("columnFoundationEnvelopes") or [])[:10],
        "stabilityModes": catalog.get("stabilityModes") or [],
        "reinforcementInventory": catalog.get("reinforcementInventory") or {},
        "formalGateAllowed": bool(result.formal_report_gate and result.formal_report_gate.allowed_for_official_issue),
        "formalBlockingCategories": sorted({
            item.category for item in (result.formal_report_gate.blocking_items if result.formal_report_gate else [])
        }),
        "resultHash": result.result_hash,
    }


def rollback_probe() -> dict[str, Any]:
    project = project_for("junction_hub_frame")
    original_name = project.name
    original_support_count = len(project.retaining_system.supports)
    original = engine._run_calculation_impl

    def fail_after_mutation(trial: Project, *args: Any, **kwargs: Any):
        trial.name = "mutated-in-failed-trial"
        trial.retaining_system.supports.clear()
        raise RuntimeError("V3.72 rollback evaluation probe")

    engine._run_calculation_impl = fail_after_mutation
    try:
        try:
            run_calculation(project, project.calculation_cases[0], auto_repair=False)
        except RuntimeError as exc:
            error = str(exc)
        else:
            error = None
    finally:
        engine._run_calculation_impl = original
    failure = dict((project.advanced_engineering or {}).get("lastCalculationFailure") or {})
    passed = (
        project.name == original_name
        and len(project.retaining_system.supports) == original_support_count
        and failure.get("status") == "rolled_back"
        and failure.get("projectMutationCommitted") is False
    )
    return {
        "status": "pass" if passed else "fail",
        "error": error,
        "projectNamePreserved": project.name == original_name,
        "supportCountPreserved": len(project.retaining_system.supports) == original_support_count,
        "failureRecord": failure,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "releases" / "V3_72_0_WORKFLOW_STABILITY_RICH_RESULTS_EVALUATION.json",
    )
    args = parser.parse_args()
    started = time.perf_counter()
    rows = [summarize(template) for template in TEMPLATES]
    payload = {
        "schema": "pitguard-v372-workflow-stability-rich-results-evaluation-v1",
        "version": version_manifest(),
        "scope": {
            "geometry": "synthetic L-shaped excavation, 60 m x 45 m envelope, 16 m excavation depth",
            "templates": TEMPLATES,
            "dataBoundary": "No real investigation data or licensed professional signoff is asserted; formal issue remains blocked.",
            "frontendBoundary": "Frontend dependency installation was unavailable in the evaluation runtime because the configured npm registry returned HTTP 503; backend payload generation and Python regression tests were executed.",
        },
        "transactionRollbackProbe": rollback_probe(),
        "topologyCalculations": rows,
        "independentReferenceBenchmark": run_independent_reference_benchmark_suite(),
        "externalRuntimeBenchmark": run_opensees_planar_benchmark_suite(),
        "preservedExternalKernelCertificate": _benchmark_certificate(),
        "elapsedSeconds": round(time.perf_counter() - started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
