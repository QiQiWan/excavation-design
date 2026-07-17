from __future__ import annotations

from pathlib import Path

import pytest

from app.calculation.engine import _design_crown_beams
from app.schemas.domain import (
    CalculationResult,
    Point2D,
    Polyline2D,
    PressureProfile,
    Project,
    StageCalculationResult,
    WaleBeamDesignResult,
    WallInternalForcePoint,
    WallInternalForceResult,
)
from app.services.core_engineering_presentation import build_verification_distribution
from app.services.deepening_readiness import build_deepening_readiness
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.intelligent_design_closure import _assessment, _quantitative_open_records, _strengthen_for_records
from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme


def _project(*, supports: bool = True) -> Project:
    excavation = make_excavation_model(
        "V3.55 智能闭环测试",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=24, y=0),
                Point2D(x=24, y=16), Point2D(x=0, y=16),
            ],
            closed=True,
        ),
        0.0,
        -8.0,
    )
    retaining = auto_diaphragm_wall(excavation)
    if supports:
        retaining = auto_supports(excavation, retaining)
    project = Project(name="V3.55 智能闭环测试", excavation=excavation, retainingSystem=retaining)
    project.design_settings.design_basis_confirmed = True
    return project


def test_verification_semantics_do_not_invert_limits_or_invent_review_safety_factors() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    support = project.retaining_system.supports[0]
    result = CalculationResult(
        projectId=project.id,
        caseId="case-v355",
        stageResults=[StageCalculationResult(stageId="stage-v355", segmentId=wall.segment_id, pressureProfile=PressureProfile(points=[]))],
        checks=[
            {
                "ruleId": "GBT50010-2024-SERVICEABILITY-CRACK-SCREEN", "objectId": wall.id,
                "objectType": "DiaphragmWallPanel", "status": "pass", "calculatedValue": 0.03,
                "limitValue": 0.30, "unit": "mm", "message": "裂缝宽度验算，保护层另行说明。",
            },
            {
                "ruleId": "PITGUARD-SUPPORT-DEEP-DESIGN-STABILITY", "objectId": project.retaining_system.id,
                "objectType": "RetainingSystem", "status": "pass", "calculatedValue": 0.25,
                "limitValue": 1.0, "unit": "utilization", "message": "最大组合利用率 0.25。",
            },
            {
                "ruleId": "GBT50010-2024-RC-MINREBAR-001", "objectId": wall.id,
                "objectType": "DiaphragmWallPanel", "status": "pass", "calculatedValue": 3000.0,
                "limitValue": 1600.0, "unit": "mm2/m", "message": "实配不小于最小配筋。",
            },
            {
                "ruleId": "JGJ120-SUPPORT-CONSTRUCTION-EFFECTS-SUBSET", "objectId": support.id,
                "objectType": "SupportElement", "status": "warning", "calculatedValue": 1000.0,
                "limitValue": 300.0, "unit": "kN", "message": "记录预加轴力和温度增量，不是承载力比值。",
            },
            {
                "ruleId": "JGJ120-2012-WEAK-UNDERLYING-LAYER-SUBSET", "objectId": project.excavation.id,
                "objectType": "ExcavationModel", "status": "pass", "calculatedValue": 2.51,
                "limitValue": 1.35, "unit": "-", "message": "软弱下卧层筛查指标越大越安全。",
            },
        ],
        checkSummary={"pass": 4, "warning": 1, "fail": 0},
    )
    project.calculation_results = [result]

    rows = {item["ruleId"]: item for item in build_verification_distribution(project)["records"]}
    assert rows["WALL_CRACK_CONTROL"]["direction"] == "maximum"
    assert rows["WALL_CRACK_CONTROL"]["safetyFactor"] == pytest.approx(10.0)
    assert rows["SUPPORT_STABILITY"]["safetyFactor"] == pytest.approx(4.0)
    assert rows["WALL_MIN_REBAR"]["direction"] == "minimum"
    assert rows["WALL_MIN_REBAR"]["targetSafetyFactor"] == pytest.approx(1.0)
    assert rows["SUPPORT_PRELOAD"]["safetyFactor"] is None
    assert rows["SUPPORT_PRELOAD"]["status"] == "warning"
    assert rows["LOCAL_WEAK_LAYER"]["direction"] == "minimum"
    assert rows["LOCAL_WEAK_LAYER"]["safetyFactor"] == pytest.approx(2.51 / 1.35, rel=1e-3)
    assert rows["LOCAL_WEAK_LAYER"]["status"] == "pass"


def test_intelligent_feedback_expands_and_strengthens_every_wall_object() -> None:
    project = _project(supports=False)
    walls = project.retaining_system.diaphragm_walls[:2]
    distribution = {
        "records": [{
            "ruleId": "WALL_FLEXURE", "label": "围护墙抗弯承载力", "category": "strength",
            "scope": "wall", "targetSafetyFactor": 1.10, "objectResults": [
                {
                    "ruleId": "WALL_FLEXURE", "rawRuleId": "GB50010-FLEXURE-SUBSET",
                    "label": "围护墙抗弯承载力", "category": "strength", "scope": "wall",
                    "objectId": walls[0].id, "objectCode": walls[0].panel_code,
                    "safetyFactor": 1.03, "status": "warning", "originalStatus": "pass", "evidenceState": "calculated",
                },
                {
                    "ruleId": "WALL_FLEXURE", "rawRuleId": "GB50010-FLEXURE-SUBSET",
                    "label": "围护墙抗弯承载力", "category": "strength", "scope": "wall",
                    "objectId": walls[1].id, "objectCode": walls[1].panel_code,
                    "safetyFactor": 1.05, "status": "warning", "originalStatus": "pass", "evidenceState": "calculated",
                },
            ],
        }],
    }
    open_rows = _quantitative_open_records(distribution)
    assert {row["objectId"] for row in open_rows} == {walls[0].id, walls[1].id}
    before = {wall.id: wall.thickness for wall in walls}
    actions = _strengthen_for_records(project, open_rows, "economic_zoned")
    assert {item["objectId"] for item in actions} == {walls[0].id, walls[1].id}
    assert all(wall.thickness > before[wall.id] for wall in walls)


def test_engine_warning_is_a_review_item_not_an_automatic_section_failure() -> None:
    project = _project(supports=False)
    wall = project.retaining_system.diaphragm_walls[0]
    result = CalculationResult(
        projectId=project.id,
        caseId="case-review-v355",
        checks=[{
            "ruleId": "JGJ120-LOCAL-WEAK-LAYER-SCREEN",
            "objectId": wall.id,
            "objectType": "DiaphragmWallPanel",
            "status": "warning",
            "calculatedValue": 0.92,
            "limitValue": 1.0,
            "unit": "factor",
            "message": "软弱下卧层需要工程师选择地基加固方案。",
        }],
        checkSummary={"pass": 0, "warning": 1, "fail": 0},
    )

    assessment = _assessment(project, result)
    assert assessment["quantitativeOpenCount"] == 0
    assert assessment["hardFailCount"] == 0
    assert assessment["calculationClosed"] is True
    assert assessment["structuralClosed"] is True
    assert assessment["reviewCount"] == 1


def test_crown_beams_receive_stage_design_and_complete_five_family_rebar() -> None:
    project = _project(supports=False)
    stage_results: list[StageCalculationResult] = []
    for index, segment in enumerate(project.excavation.segments, start=1):
        points = [
            WallInternalForcePoint(depth=0.0, elevation=0.0, shear=85.0 + index, moment=0.0, displacement=0.0),
            WallInternalForcePoint(depth=2.0, elevation=-2.0, shear=65.0, moment=90.0, displacement=0.001),
            WallInternalForcePoint(depth=8.0, elevation=-8.0, shear=10.0, moment=20.0, displacement=0.0),
        ]
        stage_results.append(StageCalculationResult(
            stageId="stage-final", segmentId=segment.id, pressureProfile=PressureProfile(points=[]),
            wallInternalForce=WallInternalForceResult(
                segmentId=segment.id, stageId="stage-final", points=points,
                maxMoment=90.0, maxShear=85.0 + index, maxDisplacement=0.001,
            ),
        ))

    checks = _design_crown_beams(project, stage_results, gamma0=1.0)
    assert checks
    assert all(beam.design_result is not None for beam in project.retaining_system.crown_beams)
    assert all(beam.design_result.check_status == "pass" for beam in project.retaining_system.crown_beams)
    expected = {"longitudinal", "stirrup", "distribution", "tie", "additional"}
    assert all(expected.issubset({group.bar_type for group in beam.reinforcement}) for beam in project.retaining_system.crown_beams)


def test_support_scheme_persists_stirrups_and_construction_bar_contract() -> None:
    project = _project()
    for support in project.retaining_system.supports:
        support.design_axial_force = 3000.0
    scheme = apply_rebar_design_scheme(project, mode="balanced")
    expected = {"longitudinal", "stirrup", "distribution", "tie", "additional"}
    assert scheme["supportRebarContractSummary"]["incompleteCount"] == 0
    assert scheme["supportSchemes"]
    assert all(item["rebarContract"]["status"] == "complete" for item in scheme["supportSchemes"])
    assert all(expected.issubset({group.bar_type for group in support.reinforcement}) for support in project.retaining_system.supports)
    immediate_contract = scheme["diagnostics"]["deepeningGate"]["structuralClosure"]["supportRebarContract"]
    assert immediate_contract["completeCount"] == len(project.retaining_system.supports)
    assert immediate_contract["incompleteCount"] == 0


def test_p3_review_items_do_not_reopen_a_closed_structure(monkeypatch) -> None:
    project = _project(supports=False)
    design = WaleBeamDesignResult(waleBeamCode="test", checkStatus="pass", momentCapacity=100.0, shearCapacity=100.0)
    for beam in project.retaining_system.crown_beams:
        beam.design_result = design.model_copy(update={"wale_beam_code": beam.code})
    project.retaining_system.rebar_design_scheme = {
        "wallZones": [{"zoneId": "test"}],
        "supportRebarContractSummary": {"incompleteCount": 0},
        "beamRebarContractSummary": {"incompleteCount": 0},
    }
    monkeypatch.setattr(
        "app.services.deepening_readiness.calculation_readiness",
        lambda _project: {"valid": True, "messages": ["当前计算有效"], "failCount": 0},
    )
    gate = build_deepening_readiness(
        project,
        checks=[{
            "checkId": "lap-1", "category": "lap_splice", "status": "fail",
            "hostCode": "CB-S1", "message": "接头位置需在 P3 内协调。",
        }],
        scheme_applied=True,
    )
    assert gate["canEnterDetailing"] is True
    assert gate["structuralClosure"]["closed"] is True
    assert gate["blockerCount"] == 0
    assert gate["warningCount"] >= 1


def test_frontend_and_worker_keep_closure_actions_and_full_support_rebar_visible() -> None:
    root = Path(__file__).resolve().parents[3]
    panel = (root / "apps/web/src/components/RebarDesignPanel.tsx").read_text(encoding="utf-8")
    preview = (root / "apps/web/src/components/RebarDrawingPreview.tsx").read_text(encoding="utf-8")
    recovery = (root / "apps/web/src/components/CalculationRecoveryPanel.tsx").read_text(encoding="utf-8")
    manager = (root / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    assert "allSupportRows" in panel
    assert "distributionBars" in panel and "tieBars" in panel and "lapAdditionalBars" in panel
    assert "五类钢筋合同完整" in preview
    assert "applyCalculationClosureAction" in recovery
    assert "require_with_latest_calculation(task.project_id)" in manager
