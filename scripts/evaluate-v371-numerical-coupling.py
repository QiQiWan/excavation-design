#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.calculation.numerical_conditioning import solve_scaled_symmetric
from app.calculation.opensees_benchmark import run_opensees_planar_benchmark_suite
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.version import version_manifest

L_SHAPE = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
TEMPLATES = ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]


def project_for(template: str) -> Project:
    excavation = make_excavation_model(
        f"V3.71 evaluation {template}",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in L_SHAPE], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template=template),
    )
    project = Project(name=f"V3.71 evaluation {template}", excavation=excavation, retainingSystem=system)
    project.calculation_cases = build_default_construction_cases(project)
    return project


def summarize(template: str) -> dict[str, Any]:
    project = project_for(template)
    started = time.perf_counter()
    result = run_calculation(project, project.calculation_cases[0], auto_repair=False, include_candidate_comparison=False)
    elapsed = time.perf_counter() - started
    frame = dict(project.advanced_engineering.get("concaveTransferFrameAnalysis") or {})
    sensitivity = dict(frame.get("sensitivity") or {})
    iteration = dict(project.advanced_engineering.get("wallWaleTransferReactionIteration") or {})
    spatial = dict(project.advanced_engineering.get("concaveTransferSpatialAnalysis") or {})
    detailing = dict(project.advanced_engineering.get("concaveTransferAutoDetailing") or {})
    data = dict(project.advanced_engineering.get("transferEngineeringDataAssurance") or {})
    transfer = dict((project.retaining_system.layout_summary or {}).get("transferSystem") or {})
    return {
        "template": template,
        "topologyClass": transfer.get("topologyClass"),
        "elapsedSeconds": round(elapsed, 3),
        "supportCount": len(project.retaining_system.supports),
        "columnCount": len(project.retaining_system.columns),
        "transferBeamCount": len(project.retaining_system.ring_beams),
        "stageCount": len(result.stage_results),
        "frameStatus": frame.get("status"),
        "maximumRawConditionNumber": frame.get("maximumRawConditionNumber"),
        "maximumScaledConditionNumber": frame.get("maximumScaledConditionNumber"),
        "conditionReductionFactor": (
            float(frame.get("maximumRawConditionNumber") or 0.0) / max(float(frame.get("maximumScaledConditionNumber") or 0.0), 1.0e-30)
        ),
        "maximumNodeStiffnessRatio": frame.get("maximumNodeStiffnessRatio"),
        "maximumFrameDisplacementM": frame.get("maximumDisplacementM"),
        "maximumFrameResidual": frame.get("maximumRelativeResidual"),
        "sensitivityStatus": sensitivity.get("status"),
        "sensitivityCaseCount": sensitivity.get("caseCount"),
        "maximumSensitivityRelativeChange": sensitivity.get("maximumRelativeChange"),
        "reactionIterationStatus": iteration.get("status"),
        "reactionIterationCount": iteration.get("iterationCount"),
        "reactionForceResidual": iteration.get("finalForceRelativeResidual"),
        "reactionDisplacementResidual": iteration.get("finalDisplacementRelativeResidual"),
        "spatialStatus": spatial.get("status"),
        "spatialNodeCount": spatial.get("nodeCount"),
        "maximumTorsionKnm": spatial.get("maximumTorsionKnm"),
        "maximumOutOfPlaneMomentKnm": spatial.get("maximumOutOfPlaneMomentKnm"),
        "maximumInPlaneEccentricMomentKnm": spatial.get("maximumInPlaneEccentricMomentKnm"),
        "maximumSpatialJointRotationRad": spatial.get("maximumJointRotationRad"),
        "maximumSpatialScaledConditionNumber": spatial.get("maximumScaledConditionNumber"),
        "maximumSpatialEquilibriumResidual": spatial.get("maximumRelativeEquilibriumResidual"),
        "spatialWarningCount": spatial.get("warningCount"),
        "spatialFailCount": spatial.get("failCount"),
        "autoDetailingStatus": detailing.get("status"),
        "detailingMetrics": detailing.get("metrics"),
        "formalCalculationReady": transfer.get("formalCalculationReady"),
        "officialIssueReady": transfer.get("officialIssueReady"),
        "realEngineeringDataStatus": data.get("status"),
        "realEngineeringDataReady": data.get("formalDataReady"),
        "realEngineeringDataMissingInputs": data.get("missingInputs"),
        "formalGateAllowed": bool(result.formal_report_gate and result.formal_report_gate.allowed_for_official_issue),
        "formalGateBlockingCategories": sorted({
            item.category for item in (result.formal_report_gate.blocking_items if result.formal_report_gate else [])
        }),
    }


def numerical_gate_examples() -> dict[str, Any]:
    scaled_solution, scaled = solve_scaled_symmetric(
        np.array([[1.0e12, 0.0], [0.0, 1.0]], dtype=float),
        np.array([1.0e12, 1.0], dtype=float),
    )
    singular_solution, singular = solve_scaled_symmetric(
        np.array([[1.0, 1.0], [1.0, 1.0]], dtype=float),
        np.array([1.0, 1.0], dtype=float),
    )
    return {
        "scaleSeparationExample": {
            "solution": scaled_solution.tolist() if scaled_solution is not None else None,
            "rawConditionNumber": scaled.get("rawConditionNumber"),
            "scaledConditionNumber": scaled.get("scaledConditionNumber"),
            "conditionGrade": scaled.get("conditionGrade"),
            "blocked": scaled.get("blocked"),
        },
        "rankDeficientExample": {
            "solution": singular_solution.tolist() if singular_solution is not None else None,
            "rankDeficient": singular.get("rankDeficient"),
            "blocked": singular.get("blocked"),
            "message": singular.get("message"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "releases" / "V3_71_0_NUMERICAL_COUPLING_EVALUATION.json")
    args = parser.parse_args()
    started = time.perf_counter()
    rows = [summarize(template) for template in TEMPLATES]
    benchmark = run_opensees_planar_benchmark_suite()
    payload = {
        "schema": "pitguard-v371-numerical-coupling-evaluation-v2",
        "version": version_manifest(),
        "scope": {
            "geometry": "synthetic L-shaped excavation, 60 m x 45 m envelope, 16 m excavation depth",
            "templates": TEMPLATES,
            "dataBoundary": "No real investigation or licensed signoff is asserted; formal issue remains blocked.",
            "spatialBoundary": "Node-level eccentricity/torsion/rigid-zone/semi-rigid submodel, not a full global 6-DOF verification.",
        },
        "numericalGateExamples": numerical_gate_examples(),
        "topologyCalculations": rows,
        "openSeesBenchmark": benchmark,
        "elapsedSeconds": round(time.perf_counter() - started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
