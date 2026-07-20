from __future__ import annotations

import pytest

from app.calculation.engine import build_default_construction_cases
from app.calculation.support_forces import estimate_support_axial_forces
from app.schemas.domain import Point2D, Polyline2D, PressurePoint, PressureProfile, Project, Stratum
from app.services.construction_stages import build_construction_stage_workspace
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.design_workflow import PHASE_ORDER, build_design_workflow
from app.services.excavation_service import make_excavation_model
from app.tasks.manager import TaskManager


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.58 工作流",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=36, y=0),
                Point2D(x=36, y=22), Point2D(x=0, y=22),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation))
    project = Project(
        name="V3.58 工作流",
        excavation=excavation,
        retainingSystem=retaining,
        strata=[Stratum(code="S1", name="粉质黏土")],
    )
    project.design_settings.design_basis_confirmed = True
    project.calculation_cases = build_default_construction_cases(project)
    return project


def test_workflow_places_construction_path_before_verification_and_rebar() -> None:
    project = _project()
    workflow = build_design_workflow(
        project,
        design_basis={"confirmed": True},
        construction_workspace=build_construction_stage_workspace(project),
        calculation_gate={"valid": False, "messages": ["尚未计算"], "failCount": 0},
        deepening_readiness={"canRunP3": False, "canIssueConstructionDrawings": False, "blockerCount": 1},
    )
    assert [row["key"] for row in workflow["phases"]] == PHASE_ORDER
    assert PHASE_ORDER.index("construction") < PHASE_ORDER.index("calculation") < PHASE_ORDER.index("reinforcement")
    assert workflow["currentPhase"] == "calculation"
    assert workflow["primaryAction"]["operation"] == "design_workflow_closure"
    assert workflow["primaryAction"]["payload"]["preserveConstructionStages"] is True


def test_unsaved_recommended_construction_path_is_an_explicit_review_decision() -> None:
    project = _project()
    project.calculation_cases = []
    workspace = build_construction_stage_workspace(project)
    workflow = build_design_workflow(
        project,
        design_basis={"confirmed": True},
        construction_workspace=workspace,
        calculation_gate={"valid": False, "messages": ["尚未计算"], "failCount": 0},
        deepening_readiness={"canRunP3": False, "canIssueConstructionDrawings": False, "blockerCount": 1},
    )
    construction = next(row for row in workflow["phases"] if row["key"] == "construction")
    assert workspace["saved"] is False
    assert construction["status"] == "review"
    assert construction["reviewRequired"] is True
    assert workflow["currentPhase"] == "construction"
    assert workflow["primaryAction"]["targetStage"] == "construction"


def test_wall_face_without_direct_strut_gets_wale_member_evidence_but_no_fake_reaction() -> None:
    project = _project()
    system = project.retaining_system
    assert system is not None and system.supports and system.wale_beams
    evidence_support = system.supports[0]
    level = int(evidence_support.level_index)
    target_segment = next(
        segment for segment in project.excavation.segments
        if segment.name not in {evidence_support.start_face_code, evidence_support.end_face_code}
    )
    profile = PressureProfile(points=[
        PressurePoint(depth=0.0, elevation=0.0, earthPressure=0.0, waterPressure=0.0, totalPressure=0.0),
        PressurePoint(depth=12.0, elevation=-12.0, earthPressure=96.0, waterPressure=0.0, totalPressure=96.0),
    ])
    collector = []
    support_forces = estimate_support_axial_forces(
        profile,
        [],
        target_segment.length,
        0.0,
        -12.0,
        segment_name=target_segment.name,
        segment=target_segment,
        wale_beams=system.wale_beams,
        stage_id="stage-corner-transfer",
        wale_result_collector=collector,
        evidence_supports=[evidence_support],
    )
    assert support_forces == []
    assert len(collector) == 1
    result = collector[0]
    assert result.level_index == level
    assert result.support_node_count == 0
    assert result.wale_beam_code == f"WB-L{level}-{target_segment.name}"
    assert result.max_moment > 0 and result.max_shear > 0 and result.max_deflection > 0
    assert "不生成虚构支撑轴力" in result.warnings[0]


def test_eight_return_faces_across_three_levels_recover_all_twenty_four_wale_records() -> None:
    excavation = make_excavation_model(
        "八墙面围檩证据恢复",
        Polyline2D(points=[
            Point2D(x=0, y=6), Point2D(x=4, y=0), Point2D(x=20, y=0), Point2D(x=24, y=6),
            Point2D(x=24, y=16), Point2D(x=20, y=22), Point2D(x=4, y=22), Point2D(x=0, y=16),
        ], closed=True),
        0.0,
        -12.0,
    )
    system = auto_supports(excavation, auto_diaphragm_wall(excavation))
    level_representatives = []
    for level in sorted({int(item.level_index) for item in system.supports}):
        level_representatives.append(next(item for item in system.supports if int(item.level_index) == level))
    assert len(excavation.segments) == 8
    assert len(level_representatives) == 3
    profile = PressureProfile(points=[
        PressurePoint(depth=0.0, elevation=0.0, earthPressure=0.0, waterPressure=0.0, totalPressure=0.0),
        PressurePoint(depth=12.0, elevation=-12.0, earthPressure=96.0, waterPressure=0.0, totalPressure=96.0),
    ])
    member_records = []
    fake_reactions = []
    for segment in excavation.segments:
        local_records = []
        fake_reactions.extend(estimate_support_axial_forces(
            profile,
            [],
            segment.length,
            0.0,
            -12.0,
            segment_name=segment.name,
            segment=segment,
            wale_beams=system.wale_beams,
            stage_id="stage-24-record-recovery",
            wale_result_collector=local_records,
            evidence_supports=level_representatives,
        ))
        member_records.extend(local_records)
    assert fake_reactions == []
    assert len(member_records) == 24
    assert len({row.wale_beam_code for row in member_records}) == 24
    assert all(row.support_node_count == 0 and row.max_moment > 0 and row.max_shear > 0 for row in member_records)


def test_workflow_contract_explains_automatic_and_human_boundaries() -> None:
    project = _project()
    workflow = build_design_workflow(
        project,
        design_basis={"confirmed": True},
        construction_workspace=build_construction_stage_workspace(project),
        calculation_gate={"valid": False, "messages": ["计算已过期"], "failCount": 0},
        deepening_readiness={"canRunP3": False, "canIssueConstructionDrawings": False, "blockerCount": 1},
    )
    policy = workflow["automationPolicy"]
    assert policy["preserveAdoptedScheme"] is True
    assert policy["preserveLockedConstructionStages"] is True
    assert policy["neverRelaxLoadsOrSoilParameters"] is True
    assert len(workflow["humanDecisionPoints"]) >= 4


def test_phase_gated_calculation_never_replaces_an_adopted_topology(monkeypatch) -> None:
    project = _project()

    class Store:
        @staticmethod
        def get_payload_info(_project_id: str) -> dict:
            return {}

    class Repo:
        store = Store()

        @staticmethod
        def save(*_args, **_kwargs) -> None:
            return None

    monkeypatch.setattr(
        "app.services.design_qualification.build_design_qualification",
        lambda *_args, **_kwargs: {
            "calculationAllowed": False,
            "gates": [{
                "code": "Q-TOPOLOGY", "title": "支撑拓扑", "message": "控制墙面尚未形成直接传力路径。",
                "blocks": ["calculation"], "evidence": {"currentHardFailureCategories": ["return_wall"]},
            }],
        },
    )
    manager = object.__new__(TaskManager)
    called = []
    monkeypatch.setattr(manager, "_attempt_legacy_topology_recovery", lambda *_args, **_kwargs: called.append(True) or {})
    with pytest.raises(ValueError, match="保护工程师采用的墙体和支撑拓扑"):
        manager._assert_calculation_qualified(Repo(), project, allow_topology_recovery=False)
    assert called == []
