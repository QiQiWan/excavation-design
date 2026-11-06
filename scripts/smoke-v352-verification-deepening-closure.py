from __future__ import annotations

"""Verify the complete verification programme and deepening-entry diagnosis."""

import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project, StageCalculationResult
from app.services.core_engineering_presentation import build_stability_distribution, build_verification_distribution
from app.services.deepening_readiness import build_deepening_readiness
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.verification_coverage import INPUT_REQUIREMENTS, VERIFICATION_CATALOG
from app.version import SOFTWARE_VERSION


def main() -> int:
    started = time.perf_counter()
    excavation = make_excavation_model(
        "v352-smoke",
        Polyline2D(points=[
            Point2D(x=0, y=0), Point2D(x=36, y=0),
            Point2D(x=36, y=20), Point2D(x=0, y=20),
        ], closed=True),
        0.0,
        -11.0,
    )
    project = Project(name="V3.52 verification smoke", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.design_settings.design_basis_confirmed = True

    verification = build_verification_distribution(project)
    stability = build_stability_distribution(project)
    if len(VERIFICATION_CATALOG) != 51:
        raise RuntimeError(f"unexpected verification catalogue size: {len(VERIFICATION_CATALOG)}")
    if len(verification.get("wallObjects") or []) != len(project.retaining_system.diaphragm_walls):
        raise RuntimeError("per-wall verification projection is incomplete")
    if not verification.get("missingInputSummary"):
        raise RuntimeError("missing-input register is empty")
    if int((stability.get("summary") or {}).get("pendingCount") or 0) < 10:
        raise RuntimeError("stability/hydraulic pending directory is incomplete")

    wall = project.retaining_system.diaphragm_walls[0]
    project.calculation_results = [CalculationResult(
        projectId=project.id,
        caseId="legacy-smoke-case",
        stageResults=[StageCalculationResult(
            stageId="stage-1",
            segmentId=wall.segment_id,
            pressureProfile={"points": []},
            checks=[{
                "ruleId": "GB50010-FLEXURE-SUBSET", "objectId": wall.id,
                "calculatedValue": 800.0, "limitValue": 1200.0, "unit": "kN·m", "status": "pass",
            }],
        )],
        checkSummary={"pass": 1, "warning": 0, "fail": 0},
    )]
    verification_with_result = build_verification_distribution(project)
    flexure = next(row for row in verification_with_result["records"] if row["ruleId"] == "WALL_FLEXURE")
    if flexure.get("evidenceState") != "calculated" or not flexure.get("objectResults"):
        raise RuntimeError("wall result did not retain object evidence")

    deepening = build_deepening_readiness(project, checks=[{
        "checkId": "RB-SMOKE-S1", "category": "support_reinforcement",
        "failureReasonCode": "SUPPORT_REBAR_CAPACITY", "hostCode": "S-SMOKE-1",
        "status": "fail", "message": "支撑截面承载力不足。",
        "recommendedAction": "增大支撑截面并重新计算。",
    }], scheme_applied=True)
    blocker = next((row for row in deepening.get("blockers") or [] if row.get("reasonCode") == "SUPPORT_REBAR_CAPACITY"), None)
    if blocker is None or blocker.get("objects") != ["S-SMOKE-1"]:
        raise RuntimeError("deepening blocker grouping lost the affected object")
    if deepening.get("canRunP3"):
        raise RuntimeError("P3 must remain blocked while member hard failures exist")

    summary = {
        "status": "success",
        "version": SOFTWARE_VERSION,
        "elapsedSeconds": round(time.perf_counter() - started, 3),
        "verificationCatalogCount": len(VERIFICATION_CATALOG),
        "inputRequirementCount": len(INPUT_REQUIREMENTS),
        "wallObjectCount": len(verification.get("wallObjects") or []),
        "stabilityHydraulicDirectoryCount": len(stability.get("factors") or []),
        "missingInputClassCount": len(verification.get("missingInputSummary") or []),
        "mappedWallSafetyFactor": flexure.get("safetyFactor"),
        "deepeningBlockerCount": deepening.get("blockerCount"),
        "deepeningBlockerObject": blocker.get("objects")[0],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
