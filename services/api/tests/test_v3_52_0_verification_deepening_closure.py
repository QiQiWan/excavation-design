from __future__ import annotations

from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project, StageCalculationResult
from app.services.core_engineering_presentation import build_stability_distribution, build_verification_distribution
from app.services.deepening_readiness import build_deepening_readiness
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model


def _project() -> Project:
    excavation = make_excavation_model(
        "v352",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=32, y=0),
                Point2D(x=32, y=18), Point2D(x=0, y=18),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    project = Project(name="v352", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.design_settings.design_basis_confirmed = True
    return project


def _add_legacy_current_calculation(project: Project, *, with_wall_check: bool = False) -> None:
    wall = project.retaining_system.diaphragm_walls[0]
    checks = []
    if with_wall_check:
        checks.extend([{
            "ruleId": "GB50010-FLEXURE-SUBSET",
            "objectId": wall.id,
            "calculatedValue": 800.0,
            "limitValue": 1200.0,
            "unit": "kN·m",
            "status": "pass",
            "message": "围护墙抗弯承载力满足。",
        }, {
            "ruleId": "GB50010-SHEAR-SUBSET", "objectId": wall.id,
            "calculatedValue": 200.0, "limitValue": 350.0, "unit": "kN/m", "status": "pass",
        }, {
            "ruleId": "JGJ120-2012-DEFORMATION-SUBSET", "objectId": wall.id,
            "calculatedValue": 12.0, "limitValue": 30.0, "unit": "mm", "status": "pass",
        }, {
            "ruleId": "GBT50010-2024-RC-MINREBAR-001", "objectId": wall.id,
            "calculatedValue": 0.35, "limitValue": 0.20, "unit": "%", "status": "pass",
        }, {
            "ruleId": "JGJ120-2012-4.5-DIAPHRAGM-CONSTRUCTION-CHECK-THK", "objectId": wall.id,
            "calculatedValue": wall.thickness, "limitValue": 0.8, "unit": "m", "status": "pass",
        }])
    stage = StageCalculationResult(
        stageId="stage-1",
        segmentId=wall.segment_id,
        pressureProfile={"points": []},
        checks=checks,
    )
    project.calculation_results = [CalculationResult(
        projectId=project.id,
        caseId="legacy-case",
        stageResults=[stage],
        checkSummary={"pass": len(checks), "warning": 0, "fail": 0},
    )]


def test_complete_programme_lists_five_categories_walls_and_missing_input_actions() -> None:
    project = _project()
    result = build_verification_distribution(project)
    categories = {row["category"] for row in result["records"]}
    assert {"strength", "stiffness", "stability", "hydraulic", "constructability"}.issubset(categories)
    assert result["summary"]["overall"]["catalogCount"] >= 50
    assert len(result["wallObjects"]) == len(project.retaining_system.diaphragm_walls)
    assert all(wall["checks"] for wall in result["wallObjects"])
    assert result["missingInputSummary"]
    assert any(row["designStageAvailable"] for row in result["missingInputSummary"])
    assert all(row.get("label") and row.get("action") and row.get("provider") for row in result["missingInputSummary"])


def test_stability_view_keeps_the_full_directory_when_only_two_or_no_factors_exist() -> None:
    result = build_stability_distribution(_project())
    labels = {row["label"] for row in result["factors"]}
    assert {"围护墙嵌固稳定", "坑底抗隆起稳定", "支护体系整体圆弧滑动稳定", "坑底渗流稳定与出口坡降"}.issubset(labels)
    assert result["summary"]["calculatedCount"] == 0
    assert result["summary"]["pendingCount"] == len(result["factors"])
    assert any(row.get("missingInputDetails") for row in result["factors"])


def test_solver_wall_check_is_mapped_without_losing_wall_and_stage_evidence() -> None:
    project = _project()
    _add_legacy_current_calculation(project, with_wall_check=True)
    result = build_verification_distribution(project)
    calculated_rules = {row["ruleId"] for row in result["records"] if row.get("evidenceState") == "calculated"}
    assert {"WALL_FLEXURE", "WALL_SHEAR", "WALL_DISPLACEMENT", "WALL_MIN_REBAR", "DIAPHRAGM_WALL_THICKNESS"}.issubset(calculated_rules)
    flexure = next(row for row in result["records"] if row["ruleId"] == "WALL_FLEXURE")
    assert flexure["evidenceState"] == "calculated"
    assert flexure["safetyFactor"] == 1.5
    assert flexure["objectResults"][0]["wallCode"]
    wall = next(row for row in result["wallObjects"] if row["wallId"] == flexure["objectResults"][0]["wallId"])
    wall_flexure = next(row for row in wall["checks"] if row["ruleId"] == "WALL_FLEXURE")
    assert len(wall_flexure["stageResults"]) == 1


def test_deepening_gate_groups_blockers_and_keeps_p3_entry_actionable() -> None:
    project = _project()
    missing = build_deepening_readiness(project, checks=[], scheme_applied=False)
    assert any(row["reasonCode"] == "CALCULATION_NOT_CURRENT" for row in missing["blockers"])
    assert missing["canGenerateScheme"] is False

    _add_legacy_current_calculation(project)
    blocked = build_deepening_readiness(project, checks=[{
        "checkId": "RB-S1",
        "category": "support_reinforcement",
        "failureReasonCode": "SUPPORT_REBAR_CAPACITY",
        "hostCode": "S1",
        "status": "fail",
        "message": "支撑轴压承载力不足。",
        "recommendedAction": "增大支撑截面并重新计算。",
    }], scheme_applied=True)
    assert blocked["canApplyScheme"] is True
    assert blocked["canRunP3"] is False
    group = next(row for row in blocked["blockers"] if row["reasonCode"] == "SUPPORT_REBAR_CAPACITY")
    assert group["objects"] == ["S1"]
    assert group["requiredAction"] == "增大支撑截面并重新计算。"

    ready = build_deepening_readiness(project, checks=[], scheme_applied=True)
    assert ready["canEnterDetailing"] is True
    assert ready["canRunP3"] is True
    assert ready["canIssueConstructionDrawings"] is False
    assert any(row["reasonCode"] == "P3_NOT_RUN" for row in ready["releaseBlockers"])
