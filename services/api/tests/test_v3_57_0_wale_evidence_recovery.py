from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.calculation.engine import _design_wale_beams
from app.calculation.support_forces import estimate_support_axial_forces
from app.schemas.domain import (
    BeamElement,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    PressurePoint,
    PressureProfile,
    Project,
    RetainingSystem,
    SectionDefinition,
    SupportElement,
    WaleBeamDesignResult,
)
from app.services.excavation_service import make_excavation_model
from app.tasks.manager import TaskManager, TaskRecord


def _pressure_profile() -> PressureProfile:
    return PressureProfile(points=[
        PressurePoint(depth=0.0, elevation=0.0, earthPressure=0.0, waterPressure=0.0, totalPressure=0.0),
        PressurePoint(depth=3.0, elevation=-3.0, earthPressure=30.0, waterPressure=0.0, totalPressure=30.0),
        PressurePoint(depth=6.0, elevation=-6.0, earthPressure=70.0, waterPressure=0.0, totalPressure=70.0),
        PressurePoint(depth=10.0, elevation=-10.0, earthPressure=120.0, waterPressure=0.0, totalPressure=120.0),
    ])


def _adjacent_support(level: int, elevation: float) -> SupportElement:
    return SupportElement(
        code=f"SP-L{level}-ADJ",
        levelIndex=level,
        elevation=elevation,
        start=Point2D(x=0.0, y=0.0),
        end=Point2D(x=10.0, y=0.0),
        startFaceCode="S2",
        endFaceCode="S4",
        spanLength=10.0,
        sectionType="rc_rectangular",
        section=SectionDefinition(width=0.9, height=0.9, name="900x900 RC"),
        material=MaterialDefinition(name="Concrete", grade="C35"),
    )


def _wale(level: int, elevation: float, segment) -> BeamElement:
    return BeamElement(
        code=f"WB-L{level}-{segment.name}",
        axis=Polyline2D(points=[segment.start, segment.end], closed=False),
        elevation=elevation,
        section=SectionDefinition(width=0.9, height=0.8, name="900x800 RC"),
        material=MaterialDefinition(name="Concrete", grade="C35"),
        beamRole="wale_beam",
        supportLevel=level,
    )


def _case(length: float = 8.0):
    excavation = make_excavation_model(
        "围檩无直接支点墙段",
        Polyline2D(
            points=[
                Point2D(x=0.0, y=0.0), Point2D(x=length, y=0.0),
                Point2D(x=length, y=10.0), Point2D(x=0.0, y=10.0),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    segment = excavation.segments[0]
    elevations = {1: -2.0, 2: -5.0, 3: -8.0}
    beams = [_wale(level, elevation, segment) for level, elevation in elevations.items()]
    evidence_supports = [_adjacent_support(level, elevation) for level, elevation in elevations.items()]
    collector = []
    forces = estimate_support_axial_forces(
        _pressure_profile(),
        [],
        segment.length,
        0.0,
        -10.0,
        segment_name=segment.name,
        segment=segment,
        wale_beams=beams,
        stage_id="stage-final",
        wale_result_collector=collector,
        evidence_supports=evidence_supports,
    )
    return excavation, segment, beams, collector, forces


def test_face_without_direct_strut_gets_all_level_wale_envelopes_without_fake_reactions() -> None:
    _excavation, segment, beams, collector, forces = _case()

    assert forces == []
    assert {item.wale_beam_code for item in collector} == {beam.code for beam in beams}
    assert len(collector) == 3
    assert all(item.face_code == segment.name for item in collector)
    assert all(item.support_node_count == 0 for item in collector)
    assert all(item.max_moment > 0.0 and item.max_shear > 0.0 for item in collector)
    assert all("未虚构支撑反力" in item.warnings[0] for item in collector)


def test_reported_eight_faces_by_three_levels_recover_exactly_24_wale_design_records() -> None:
    # Mirrors the uploaded diagnostic pattern:
    # S2/S4/S6/S8/S12/S14/S16/S18 at three support levels.
    points = [
        Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0), Point2D(x=4.0, y=0.0),
        Point2D(x=6.0, y=0.0), Point2D(x=8.0, y=0.0), Point2D(x=10.0, y=0.0),
        Point2D(x=10.0, y=2.0), Point2D(x=10.0, y=4.0), Point2D(x=10.0, y=6.0),
        Point2D(x=10.0, y=8.0), Point2D(x=10.0, y=10.0), Point2D(x=8.0, y=10.0),
        Point2D(x=6.0, y=10.0), Point2D(x=4.0, y=10.0), Point2D(x=2.0, y=10.0),
        Point2D(x=0.0, y=10.0), Point2D(x=0.0, y=8.0), Point2D(x=0.0, y=6.0),
        Point2D(x=0.0, y=4.0), Point2D(x=0.0, y=2.0),
    ]
    excavation = make_excavation_model("日志拓扑复现", Polyline2D(points=points, closed=True), 0.0, -10.0)
    target_faces = {f"S{index}" for index in (2, 4, 6, 8, 12, 14, 16, 18)}
    target_segments = [segment for segment in excavation.segments if segment.name in target_faces]
    elevations = {1: -2.0, 2: -5.0, 3: -8.0}
    beams = [_wale(level, elevation, segment) for segment in target_segments for level, elevation in elevations.items()]
    evidence_supports = [_adjacent_support(level, elevation) for level, elevation in elevations.items()]
    collector = []
    for segment in target_segments:
        forces = estimate_support_axial_forces(
            _pressure_profile(), [], segment.length, 0.0, -10.0,
            segment_name=segment.name, segment=segment, wale_beams=beams,
            stage_id="stage-final", wale_result_collector=collector,
            evidence_supports=evidence_supports,
        )
        assert forces == []

    assert len(target_segments) == 8
    assert len(collector) == 24
    assert {item.wale_beam_code for item in collector} == {beam.code for beam in beams}
    project = Project(name="日志拓扑闭合", excavation=excavation, retainingSystem=RetainingSystem(waleBeams=beams))
    _design_wale_beams(project, collector, gamma0=1.0)
    assert all(beam.design_result is not None for beam in beams)
    assert all(len(beam.reinforcement) >= 5 for beam in beams)


def test_corner_transfer_envelopes_form_formal_design_and_five_rebar_families() -> None:
    excavation, _segment, beams, collector, _forces = _case()
    project = Project(
        name="围檩证据闭环",
        excavation=excavation,
        retainingSystem=RetainingSystem(waleBeams=beams),
    )
    checks = _design_wale_beams(project, collector, gamma0=1.0)

    assert all(beam.design_result is not None for beam in beams)
    assert all(beam.internal_force_results for beam in beams)
    expected = {"longitudinal", "stirrup", "distribution", "tie", "additional"}
    assert all(expected.issubset({group.bar_type for group in beam.reinforcement}) for beam in beams)
    transfer_checks = [row for row in checks if row["ruleId"] == "PITGUARD-WALE-CORNER-TRANSFER-EVIDENCE"]
    assert len(transfer_checks) == 3
    assert all(row["status"] == "pass" for row in transfer_checks)


def test_corner_transfer_evidence_does_not_hide_excessive_unsupported_length() -> None:
    excavation, _segment, beams, collector, _forces = _case(length=12.0)
    project = Project(
        name="超长无直接支点围檩",
        excavation=excavation,
        retainingSystem=RetainingSystem(waleBeams=beams),
    )
    project.design_settings.hard_max_wale_support_bay_m = 9.0
    checks = _design_wale_beams(project, collector, gamma0=1.0)
    transfer_checks = [row for row in checks if row["ruleId"] == "PITGUARD-WALE-CORNER-TRANSFER-EVIDENCE"]

    assert transfer_checks
    assert all(row["status"] == "fail" for row in transfer_checks)
    assert all(row["calculatedValue"] == pytest.approx(12.0) for row in transfer_checks)
    assert all(beam.design_result is not None for beam in beams)
    assert all(beam.design_result.check_status == "fail" for beam in beams)


def test_rebar_task_recovers_missing_beams_then_writes_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    excavation, _segment, beams, _collector, _forces = _case()
    project = Project(
        name="配筋自动恢复",
        excavation=excavation,
        retainingSystem=RetainingSystem(waleBeams=beams),
    )
    project.design_settings.design_basis_confirmed = True
    project.advanced_engineering = {"testCalculationReady": False}

    class FakeRepo:
        def require_with_latest_calculation(self, _project_id: str):
            return project

        def save(self, *_args, **_kwargs):
            return project

    repo = FakeRepo()
    stages: list[str] = []

    def fake_calculation(_task, _payload):
        for beam in beams:
            beam.design_result = WaleBeamDesignResult(
                waleBeamCode=beam.code,
                faceCode="S1",
                levelIndex=beam.support_level,
                checkStatus="pass",
            )
        project.advanced_engineering["testCalculationReady"] = True
        return {"projectId": project.id}

    def fake_apply(current_project, mode="balanced"):
        scheme = {
            "mode": mode,
            "status": "pass",
            "checks": [],
            "summary": {"failCount": 0, "warningCount": 0},
            "diagnostics": {
                "canIssueConstructionDrawings": False,
                "deepeningGate": {"status": "review", "blockerCount": 0, "warningCount": 1, "canEnterDetailing": True, "canRunP3": True},
            },
            "requiresRecalculation": False,
            "supportRebarContractSummary": {"supportCount": 0, "completeCount": 0, "incompleteCount": 0},
        }
        current_project.retaining_system.rebar_design_scheme = scheme
        return scheme

    monkeypatch.setattr(
        "app.services.deepening_readiness.calculation_readiness",
        lambda current_project: {
            "valid": bool((current_project.advanced_engineering or {}).get("testCalculationReady")),
            "messages": ["测试计算合同已就绪" if (current_project.advanced_engineering or {}).get("testCalculationReady") else "测试计算合同待刷新"],
            "failCount": 0,
        },
    )
    monkeypatch.setattr("app.services.rebar_scheme_optimizer.apply_rebar_design_scheme", fake_apply)
    monkeypatch.setattr("app.tasks.manager.append_event", lambda *_args, **_kwargs: None)

    manager = SimpleNamespace(
        _repo=lambda: repo,
        _run_calculation_full=fake_calculation,
        _stage=lambda _task, _progress, label: stages.append(label),
        _enforce_memory_budget=lambda *_args, **_kwargs: None,
        _check_cancel=lambda *_args, **_kwargs: None,
        _memory_checkpoint=lambda *_args, **_kwargs: None,
    )
    task = TaskRecord(id="task-v357", project_id=project.id, operation="rebar_design", title="配筋自动恢复")
    result = TaskManager._run_rebar_design(
        manager,
        task,
        {"mode": "balanced", "apply": True, "recalculate": True, "repairMissingDesignEvidence": True},
    )

    assert result["recoveredCalculationContract"] is True
    assert result["missingBeamDesignCountBeforeRecovery"] == 3
    assert result["recoveredMissingBeamDesignCount"] == 3
    assert result["remainingMissingBeamDesignCount"] == 0
    assert result["applied"] is True
    assert project.retaining_system.rebar_design_scheme
    assert any("自动恢复配筋入口" in label for label in stages)
