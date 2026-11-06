#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "services" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.calculation.engine import build_default_construction_cases, run_calculation
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.version import version_manifest

SHAPES = {
    "L": [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)],
    "U": [(0, 0), (80, 0), (80, 50), (55, 50), (55, 20), (25, 20), (25, 50), (0, 50)],
    "T": [(0, 0), (80, 0), (80, 20), (50, 20), (50, 60), (30, 60), (30, 20), (0, 20)],
    "Z": [(0, 0), (50, 0), (50, 40), (80, 40), (80, 60), (30, 60), (30, 20), (0, 20)],
    "H": [(0, 0), (20, 0), (20, 25), (60, 25), (60, 0), (80, 0), (80, 60), (60, 60), (60, 35), (20, 35), (20, 60), (0, 60)],
}
TEMPLATES = ["compact_elbow_ring", "balanced_elbow_ring", "extended_elbow_ring"]


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


def quality_payload(project: Project) -> dict:
    quality = evaluate_support_layout_quality(project)
    audit = dict(project.retaining_system.layout_summary.get("transferSystem") or {})
    return {
        "supportCount": len(project.retaining_system.supports),
        "columnCount": len(project.retaining_system.columns),
        "transferBeamCount": len(project.retaining_system.ring_beams),
        "qualityStatus": quality.status,
        "qualityScore": quality.score,
        "failCount": sum(item.severity == "fail" for item in quality.issues),
        "warningCount": sum(item.severity == "warning" for item in quality.issues),
        "failCategories": sorted({item.category for item in quality.issues if item.severity == "fail"}),
        "metrics": {
            key: quality.metrics.get(key)
            for key in (
                "waleSupportBayFailCount",
                "supportCrossingCount",
                "supportOutsideExcavationCount",
                "unsupportedInternalEndpointCount",
                "supportToSupportTerminalCount",
                "supportStationClusterCount",
            )
        },
        "transferSystem": {
            key: audit.get(key)
            for key in (
                "templateId",
                "templateLabel",
                "calculationReady",
                "officialIssueReady",
                "junctionCount",
                "coveredJunctionCount",
                "requiredFaceCount",
                "coveredFaceCountByLevel",
                "faceCoverageComplete",
                "ringClosed",
                "ringClosureByLevel",
                "blockingReasons",
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate PitGuard V3.66 concave support closure")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-calculation", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "12")
    started = time.perf_counter()

    before = make_project("L-before", SHAPES["L"])
    after = make_project("L-after", SHAPES["L"], "balanced_elbow_ring")
    result = {
        "version": version_manifest(),
        "sample": "orthogonal L-shaped excavation, top 0.0 m, bottom -16.0 m, three support levels",
        "before": quality_payload(before),
        "after": quality_payload(after),
        "shapeCoverage": {},
        "candidates": [],
    }
    for name, raw in SHAPES.items():
        result["shapeCoverage"][name] = quality_payload(make_project(f"{name}-shape", raw, "balanced_elbow_ring"))

    _best, candidates = optimize_support_layout_candidates(
        after,
        max_candidates=3,
        preset="balanced",
        search_config={
            "enableConcaveTransferTemplates": True,
            "concaveTransferTemplates": TEMPLATES,
            "requireDiverseSchemes": True,
            "maxTrials": 12,
            "candidatePoolLimit": 6,
        },
    )
    result["candidates"] = [
        {
            "rank": item.rank,
            "id": item.id,
            "score": item.score,
            "status": item.status,
            "hardConstraintPassed": bool(item.hard_constraints.get("passed")),
            "template": item.variable_summary.get("transferSystemTemplate"),
            "templateLabel": item.variable_summary.get("schemeLabel"),
            "supportCount": item.support_count,
            "columnCount": item.column_count,
            "transferBeamCount": len(item.plan_geometry.get("transferBeams") or []),
            "failCount": item.fail_count,
            "warningCount": item.warning_count,
        }
        for item in candidates
    ]

    if args.full_calculation:
        after.calculation_cases = build_default_construction_cases(after)
        calculation_started = time.perf_counter()
        calculation = run_calculation(after, after.calculation_cases[0], auto_repair=False, include_candidate_comparison=False)
        result["fullCalculation"] = {
            "elapsedSeconds": round(time.perf_counter() - calculation_started, 3),
            "stageCount": len(calculation.stage_results),
            "checkSummary": calculation.check_summary,
            "governingValues": calculation.governing_values.model_dump(mode="json", by_alias=True),
            "supportTopologyHash": calculation.support_topology_hash,
            "formalGateAllowed": bool(calculation.formal_report_gate and calculation.formal_report_gate.allowed_for_official_issue),
            "formalGateBlockingCategories": [
                item.category for item in (calculation.formal_report_gate.blocking_items if calculation.formal_report_gate else [])
            ],
        }
    result["elapsedSeconds"] = round(time.perf_counter() - started, 3)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(text)


if __name__ == "__main__":
    main()
