from __future__ import annotations

from pathlib import Path

from app.schemas.domain import (
    CalculationResult,
    CalculationCase,
    ConstructionStage,
    Point2D,
    Polyline2D,
    PressureProfile,
    Project,
    StageCalculationResult,
    WaleBeamInternalForcePoint,
    WaleBeamInternalForceResult,
)
from app.services.beam_design_recovery import recover_missing_beam_designs
from app.services.calculation_assurance import build_calculation_contract
from app.services.calculation_state import mark_calculation_state_current
from app.services.deepening_readiness import calculation_readiness
from app.services.design_intake import apply_guided_design_intake, build_design_intake
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme
from app.services.support_topology_contract import support_topology_hash


def _project_with_one_direct_and_one_geometry_only_wale() -> tuple[Project, list]:
    excavation = make_excavation_model(
        "V3.60 配筋入口自愈测试",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=30, y=0),
                Point2D(x=30, y=20), Point2D(x=0, y=20),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation))
    level = retaining.wale_beams[0].support_level
    beams = [beam for beam in retaining.wale_beams if beam.support_level == level][:2]
    assert len(beams) == 2
    retaining.wale_beams = beams
    retaining.crown_beams = []
    retaining.ring_beams = []
    direct = WaleBeamInternalForceResult(
        waleBeamCode=beams[0].code,
        faceCode="S1",
        levelIndex=level,
        elevation=beams[0].elevation,
        stageId="stage-final",
        pressureLineLoad=35.0,
        beamLength=30.0,
        supportNodeCount=2,
        points=[
            WaleBeamInternalForcePoint(chainage=0.0, shear=30.0, moment=0.0, deflection=0.0),
            WaleBeamInternalForcePoint(chainage=15.0, shear=0.0, moment=50.0, deflection=0.001),
            WaleBeamInternalForcePoint(chainage=30.0, shear=-30.0, moment=0.0, deflection=0.0),
        ],
        maxMoment=50.0,
        maxShear=30.0,
        maxDeflection=0.001,
    )
    stage = StageCalculationResult(
        stageId="stage-final",
        segmentId=excavation.segments[0].id,
        pressureProfile=PressureProfile(points=[]),
        waleBeamResults=[direct],
    )
    project = Project(name="V3.60 配筋入口自愈测试", excavation=excavation, retainingSystem=retaining)
    project.design_settings.design_basis_confirmed = True
    project.calculation_results = [CalculationResult(
        projectId=project.id,
        caseId="case-v360",
        stageResults=[stage],
        checks=[],
        checkSummary={"pass": 0, "fail": 0, "warning": 0},
    )]
    return project, beams


def test_geometry_only_wale_uses_same_level_envelope_without_changing_section() -> None:
    project, beams = _project_with_one_direct_and_one_geometry_only_wale()
    before = (beams[1].section.width, beams[1].section.height)

    recovery = recover_missing_beam_designs(project)

    assert recovery["status"] == "complete"
    assert recovery["recoveredCount"] == 2
    assert recovery["sameLevelEnvelopeCount"] == 1
    assert recovery["unresolvedCount"] == 0
    assert beams[1].design_result is not None
    assert beams[1].design_result.check_status == "manual_review"
    assert "同一道支撑" in beams[1].design_result.method
    assert (beams[1].section.width, beams[1].section.height) == before
    assert {group.bar_type for group in beams[1].reinforcement} == {
        "longitudinal", "stirrup", "distribution", "tie", "additional",
    }


def test_recovered_beams_no_longer_block_p3_entry_after_rebar_is_applied() -> None:
    project, _beams = _project_with_one_direct_and_one_geometry_only_wale()
    recover_missing_beam_designs(project)

    scheme = apply_rebar_design_scheme(project, mode="balanced")
    gate = scheme["diagnostics"]["deepeningGate"]

    assert not any(row["reasonCode"] == "BEAM_DESIGN_RESULT_MISSING" for row in gate["blockers"])
    assert not any(row["reasonCode"] == "REBAR_SCHEME_NOT_APPLIED" for row in gate["blockers"])
    assert gate["canRunP3"] is True
    assert scheme["beamRebarContractSummary"]["incompleteCount"] == 0


def test_current_calculation_prioritizes_rebar_closure_over_deferred_geology_model() -> None:
    project, _beams = _project_with_one_direct_and_one_geometry_only_wale()
    apply_guided_design_intake(
        project,
        goal="standard_design",
        environment_level="一般",
        objective="balanced",
        design_stage="temporary",
    )
    project.boreholes = []
    project.strata = []
    project.geological_model = None

    intake = build_design_intake(
        project,
        calculation_current=True,
        detailing_ready=False,
        deliverable_ready=False,
    )

    assert intake["primaryAction"]["key"] == "close_rebar_entry"
    assert intake["primaryAction"]["target"] == "reinforcement"
    assert "地质模型" not in intake["primaryAction"]["label"]


def test_v359_stage_contract_remains_usable_for_v360_derived_beam_recovery() -> None:
    project, _beams = _project_with_one_direct_and_one_geometry_only_wale()
    stage = ConstructionStage(
        name="最终开挖",
        excavationElevation=project.excavation.bottom_elevation,
        activeSupportIds=[support.id for support in project.retaining_system.supports],
        activeSupportLevels=sorted({support.level_index for support in project.retaining_system.supports}),
    )
    case = CalculationCase(name="V3.59 已确认施工阶段", stages=[stage])
    project.calculation_cases = [case]
    latest = project.calculation_results[-1]
    latest.case_id = case.id
    current = build_calculation_contract(project, case)
    stored = {
        **current,
        "algorithmVersion": "3.59.0-intent-driven-progressive-design",
        "ruleSetVersion": "2026.07-v3.59-intent-driven-design",
        "adoptedDesignSnapshotHash": current["inputSnapshotHash"],
    }
    latest.calculation_contract_id = "calc-contract-v359-final"
    latest.input_snapshot_hash = current["inputSnapshotHash"]
    latest.adopted_design_snapshot_hash = current["inputSnapshotHash"]
    latest.support_topology_hash = support_topology_hash(project)
    latest.calculation_assurance = {"status": "pass", "contract": stored}
    mark_calculation_state_current(project, latest.id)

    readiness = calculation_readiness(project)

    assert readiness["valid"] is True
    assert readiness["contract"]["verificationMode"] == "v3.60_rebar_postprocessor_compatible_migration"
    assert readiness["contract"]["rebarPostprocessorMigration"] is True


def test_frontend_exposes_executable_rebar_recovery_instead_of_navigation_only() -> None:
    root = Path(__file__).resolve().parents[3]
    workspace = (root / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    assert "一键关闭配筋入口" in workspace
    assert "补齐梁设计并应用" in workspace
    assert "recalculate: true" in workspace
    assert "系统可自动处理" in workspace
    assert "handlePrimaryAction" in workspace
