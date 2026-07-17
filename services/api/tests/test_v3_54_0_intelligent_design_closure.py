from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi.testclient import TestClient

from app.calculation.engine import build_default_construction_cases
from app.main import app
from app.schemas.domain import (
    CalculationResult,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    Project,
    SectionDefinition,
    SupportElement,
)
from app.services.construction_stages import (
    build_construction_stage_workspace,
    normalize_user_calculation_case,
    require_confirmed_calculation_case,
)
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.safety_design_closure import apply_safety_design_feedback
from app.services.wall_embedment_design import _evaluate, auto_design_wall_embedment
from app.rules.gb50010.rc_section_rules import design_rectangular_flexural_reinforcement


def _project() -> Project:
    excavation = make_excavation_model(
        "v354",
        Polyline2D(
            points=[
                Point2D(x=0, y=0),
                Point2D(x=36, y=0),
                Point2D(x=36, y=24),
                Point2D(x=0, y=24),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    project = Project(name="v354", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.retaining_system.supports.append(SupportElement(
        code="ZC-L1-01",
        levelIndex=1,
        elevation=-3.0,
        start=Point2D(x=0.5, y=12.0),
        end=Point2D(x=35.5, y=12.0),
        sectionType="rc_rectangular",
        section=SectionDefinition(width=0.8, height=0.8, name="800×800 钢筋混凝土支撑"),
        material=MaterialDefinition(name="混凝土", grade="C35"),
    ))
    project.design_settings.design_basis_confirmed = True
    project.calculation_cases = build_default_construction_cases(project)
    return project


def test_recommended_stages_are_chinese_editable_and_require_confirmation() -> None:
    project = _project()
    case = project.calculation_cases[-1]
    assert "系统推荐" in case.name
    assert all("Stage" not in stage.name and "Replacement" not in stage.name for stage in case.stages)

    workspace = build_construction_stage_workspace(project)
    assert workspace["workflow"]["state"] == "confirmation_required"
    assert workspace["workflow"]["canCalculate"] is False
    assert "最终开挖深度" in workspace["generationBasis"]["description"] or any(
        "设计开挖深度" in row for row in workspace["generationBasis"]["rules"]
    )
    assert "换撑生效条件" in workspace["generationBasis"]["editableFields"]

    with pytest.raises(ValueError, match="CONSTRUCTION_STAGE_CONFIRMATION_REQUIRED"):
        require_confirmed_calculation_case(project)

    project.calculation_cases = [normalize_user_calculation_case(project, case)]
    confirmed, decision = require_confirmed_calculation_case(project)
    assert confirmed.locked is True
    assert decision["confirmed"] is True


def test_failed_wall_capacity_feeds_back_into_bounded_section_strengthening() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    before = wall.thickness
    result = CalculationResult(
        projectId=project.id,
        caseId=project.calculation_cases[-1].id,
        checks=[{
            "ruleId": "GB50010-WALL-FLEXURE",
            "objectId": wall.id,
            "status": "fail",
            "calculatedValue": 1350.0,
            "limitValue": 900.0,
            "message": "围护墙抗弯承载力不足。",
        }],
    )

    feedback = apply_safety_design_feedback(project, result, iteration=1)
    assert feedback["changed"] is True
    assert feedback["changedObjectCount"] == 1
    assert feedback["actions"][0]["type"] == "wall_section_strengthening"
    assert wall.thickness > before
    assert wall.thickness <= 1.80


def test_numeric_reserve_shortfall_also_enters_the_design_feedback_loop() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    before = wall.thickness
    result = CalculationResult(
        projectId=project.id,
        caseId=project.calculation_cases[-1].id,
        checkSummary={"pass": 1, "warning": 0, "fail": 0},
        checks=[{
            "ruleId": "GB50010-SHEAR-SUBSET",
            "objectId": wall.id,
            "objectType": "DiaphragmWallPanel",
            "status": "pass",
            "calculatedValue": 950.0,
            "limitValue": 1000.0,
            "unit": "kN/m",
            "message": "满足规范限值，但尚未达到项目储备目标。",
        }],
    )

    feedback = apply_safety_design_feedback(project, result, iteration=1)
    assert feedback["failCountBefore"] == 0
    assert feedback["reserveShortfallCountBefore"] == 1
    assert feedback["changed"] is True
    assert wall.thickness > before


def test_rebar_selection_and_wall_toe_search_share_project_reserve_targets() -> None:
    flex = design_rectangular_flexural_reinforcement(
        900.0,
        1.0,
        capacity_reserve_factor=1.10,
    )
    assert flex["barArrangement"]["providedAs"] + 1.0e-6 >= flex["asRequired"] * 1.10

    project = _project()
    audit = auto_design_wall_embedment(project, project.calculation_cases[-1])
    assert audit["projectReserveMultiplier"] == pytest.approx(1.10)
    assert audit["designTarget"] + 0.002 >= audit["screeningLimit"] * 1.10


def test_wall_toe_design_does_not_stop_at_code_limit_before_project_reserve(monkeypatch) -> None:
    import app.services.wall_embedment_design as service

    project = _project()
    original = {wall.id: float(wall.bottom_elevation) for wall in project.retaining_system.diaphragm_walls}

    def fake_evaluate(value, _case, proposed_common_bottom=None, proposed_bottom_by_wall=None):
        rows = []
        for wall in value.retaining_system.diaphragm_walls:
            bottom = float(wall.bottom_elevation)
            if proposed_common_bottom is not None:
                bottom = min(bottom, float(proposed_common_bottom))
            if proposed_bottom_by_wall and wall.id in proposed_bottom_by_wall:
                bottom = min(bottom, float(proposed_bottom_by_wall[wall.id]))
            factor = 1.35 if bottom < original[wall.id] - 0.01 else 1.25
            rows.append({
                "segmentId": wall.segment_id, "segmentCode": wall.panel_code,
                "wallId": wall.id, "wallCode": wall.panel_code,
                "bottomElevationM": bottom, "embedmentDepthM": 8.0,
                "factor": factor, "limit": 1.20, "status": "pass",
                "locked": False, "source": "unknown",
            })
        context = SimpleNamespace(
            stage_id=None, stage_name="最终开挖", excavation_depth_m=12.0,
            groundwater_outside_elevation_m=-1.0, groundwater_inside_elevation_m=-10.0,
            surcharge_kpa=20.0,
        )
        return rows, context

    monkeypatch.setattr(service, "_evaluate", fake_evaluate)
    audit = service.auto_design_wall_embedment(project)
    assert audit["beforeMinimumFactor"] >= audit["screeningLimit"]
    assert audit["beforeMinimumFactor"] < audit["designTarget"]
    assert audit["changed"] is True
    assert audit["afterMinimumFactor"] >= audit["designTarget"]


def test_per_wall_toe_trial_changes_only_the_selected_wall() -> None:
    project = _project()
    case = project.calculation_cases[-1]
    first, second = project.retaining_system.diaphragm_walls[:2]
    first_before = first.bottom_elevation
    second_before = second.bottom_elevation

    rows, _context = _evaluate(
        project,
        case,
        proposed_bottom_by_wall={first.id: first_before - 1.0},
    )
    by_id = {row["wallId"]: row for row in rows}
    assert by_id[first.id]["bottomElevationM"] == pytest.approx(first_before - 1.0)
    assert by_id[second.id]["bottomElevationM"] == pytest.approx(second_before)


def test_confirmed_generated_stages_execute_end_to_end(tmp_path: Path, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[3]
    sample = root / "packages" / "sample-data" / "boreholes" / "sample_boreholes.csv"
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"name": "V3.54 施工阶段闭环"})
        assert created.status_code == 200, created.text
        project_id = created.json()["id"]
        settings = dict(created.json()["designSettings"])
        settings["designBasisConfirmed"] = True
        settings["bearingCapacityKpa"] = 180
        confirmed_basis = client.patch(
            f"/api/projects/{project_id}/workspace",
            json={"designSettings": settings},
        )
        assert confirmed_basis.status_code == 200, confirmed_basis.text
        with sample.open("rb") as handle:
            imported = client.post(
                f"/api/projects/{project_id}/boreholes/import-csv",
                files={"file": (sample.name, handle, "text/csv")},
            )
        assert imported.status_code == 200, imported.text
        assert client.post(f"/api/projects/{project_id}/geology/build-model").status_code == 200
        excavation = {
            "name": "12 米矩形基坑",
            "topElevation": 0.0,
            "bottomElevation": -12.0,
            "outline": {"closed": True, "points": [
                {"x": 5, "y": 5}, {"x": 55, "y": 5},
                {"x": 55, "y": 35}, {"x": 5, "y": 35},
            ]},
        }
        assert client.post(f"/api/projects/{project_id}/excavation", json=excavation).status_code == 200
        assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
        assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
        assert client.post(f"/api/projects/{project_id}/calculation/build-cases").status_code == 200

        workspace = client.get(f"/api/projects/{project_id}/calculation/construction-stages")
        assert workspace.status_code == 200, workspace.text
        assert workspace.json()["workflow"]["state"] == "confirmation_required"
        confirmed = client.put(
            f"/api/projects/{project_id}/calculation/construction-stages",
            json=workspace.json()["case"],
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["workflow"]["canCalculate"] is True

        calculation = client.post(f"/api/projects/{project_id}/calculation/run")
        assert calculation.status_code == 200, calculation.text
        result = calculation.json()
        assert result["stageResults"]
        closure = result["designIterationSummary"]["intelligentSafetyDesignClosure"]
        assert closure["finalFailCount"] == 0
        assert closure["finalReserveShortfallCount"] == 0

        rebar_task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={"operation": "rebar_design", "payload": {"mode": "balanced", "apply": True, "recalculate": True}},
        )
        assert rebar_task.status_code == 200, rebar_task.text
        task_id = rebar_task.json()["id"]
        finished = rebar_task.json()
        for _ in range(600):
            finished = client.get(f"/api/tasks/{task_id}").json()
            if finished["status"] in {"success", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert finished["status"] == "success", f"{finished.get('error')} | {finished.get('logs')}"
        assert finished["result"]["applied"] is True
        assert finished["result"]["requiresRecalculation"] is False

        core = client.get(f"/api/projects/{project_id}/design/core-status")
        assert core.status_code == 200, core.text
        assert core.json()["calculationReadiness"]["valid"] is True
        readiness = core.json()["deepeningReadiness"]
        blocker_text = " | ".join(
            f"{item.get('reasonCode')}: {item.get('message')} => {item.get('requiredAction')}"
            for item in readiness.get("blockers", [])
        )
        assert readiness["canRunP3"] is True, blocker_text

        p3_task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={"operation": "p3_detailing_closure", "payload": {"mode": "balanced", "topNodeCount": 4}},
        )
        assert p3_task.status_code == 200, p3_task.text
        p3_task_id = p3_task.json()["id"]
        p3_finished = p3_task.json()
        for _ in range(600):
            p3_finished = client.get(f"/api/tasks/{p3_task_id}").json()
            if p3_finished["status"] in {"success", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert p3_finished["status"] == "success", f"{p3_finished.get('error')} | {p3_finished.get('logs')}"
        assert p3_finished["result"]["status"] in {"pass", "warning", "fail"}

        delivery_task = client.post(
            f"/api/projects/{project_id}/tasks",
            json={
                "operation": "export_coordinated_delivery",
                "payload": {
                    "issueMode": "review",
                    "rebarMode": "balanced",
                    "includeIfcProfiles": False,
                },
            },
        )
        assert delivery_task.status_code == 200, delivery_task.text
        delivery_task_id = delivery_task.json()["id"]
        delivery_finished = delivery_task.json()
        for _ in range(1200):
            delivery_finished = client.get(f"/api/tasks/{delivery_task_id}").json()
            if delivery_finished["status"] in {"success", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert delivery_finished["status"] == "success", (
            f"{delivery_finished.get('error')} | {delivery_finished.get('logs')}"
        )
        assert delivery_finished["result"]["packageType"] == "coordinated_delivery"
        assert delivery_finished["result"]["issueMode"] == "review"
