from __future__ import annotations

from copy import deepcopy

import pytest

from app.calculation import engine
from app.schemas.domain import CalculationCase, ConstructionStage, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig


def project_fixture() -> Project:
    excavation = make_excavation_model(
        "Reliability rectangle", Polyline2D(points=[Point2D(x=x, y=y) for x, y in
        [(0, 0), (24, 0), (24, 18), (0, 18)]], closed=True), 0.0, -6.0,
    )
    system = auto_supports(excavation, auto_diaphragm_wall(excavation), SupportLayoutConfig())
    project = Project(name="Reliability rectangle", excavation=excavation, retainingSystem=system)
    project.design_settings.auto_wall_embedment_design_enabled = True
    project.design_settings.auto_intelligent_design_closure_enabled = True
    project.calculation_cases = engine.build_default_construction_cases(project)
    return project


def test_default_stages_expose_unsupported_excavation_before_installation():
    project = project_fixture()
    stages = project.calculation_cases[0].stages
    assert stages[0].excavation_elevation == project.excavation.top_elevation
    assert not stages[0].active_support_ids
    previous_active: set[str] = set()
    for index, stage in enumerate(stages):
        added = set(stage.active_support_ids) - previous_active
        if added:
            previous = stages[index - 1]
            assert stage.stage_type == "support_installation"
            assert previous.stage_type == "excavation"
            assert previous.excavation_elevation == stage.excavation_elevation
            assert not added.intersection(previous.active_support_ids)
        previous_active = set(stage.active_support_ids)
    assert not any(s.stage_type in {"replacement", "support_removal", "bottom_slab"} for s in stages)


def test_verify_does_not_publish_design_mutations_and_preserves_rebar(monkeypatch):
    project = project_fixture()
    before = deepcopy(project.retaining_system.model_dump(mode="json"))
    before_cases = deepcopy([c.model_dump(mode="json") for c in project.calculation_cases])
    from app.schemas.domain import CalculationResult

    def trial_calculation(trial, case, **kwargs):
        assert kwargs["mode"] == "verify"
        assert kwargs["auto_repair"] is False
        # A derived proposal is permitted on the isolated working copy.
        trial.retaining_system.diaphragm_walls[0].reinforcement.clear()
        return CalculationResult(projectId=trial.id, caseId=case.id)

    monkeypatch.setattr(engine, "_run_calculation_impl", trial_calculation)
    result = engine.run_calculation(project, project.calculation_cases[0], mode="verify")
    assert project.retaining_system.model_dump(mode="json") == before
    assert [c.model_dump(mode="json") for c in project.calculation_cases] == before_cases
    assert result.calculation_mode == "verify"
    assert result.input_before_hash == result.input_after_hash
    assert result.derived_design["adopted"] is False


def test_verify_rejects_no_saved_case_without_generating_one():
    project = project_fixture()
    project.calculation_cases.clear()
    with pytest.raises(ValueError, match="工况"):
        engine.run_calculation(project, mode="verify")
    assert project.calculation_cases == []


def test_zero_depth_stage_and_final_have_explicit_state_without_slab():
    from app.calculation.stage_state import build_stage_states
    project = project_fixture()
    case = CalculationCase(name="zero stage", stages=[
        ConstructionStage(name="未开挖", excavationElevation=0.0, stageType="excavation"),
        ConstructionStage(name="坑底", excavationElevation=-6.0, stageType="final"),
    ])
    states = build_stage_states(project, case)
    assert states[case.stages[0].id]["excavationDepthM"] == 0.0
    assert not states[case.stages[-1].id]["slabActive"]
    assert states[case.stages[-1].id]["historyTransfer"] == "not_implemented"


def test_slab_activation_persists_and_removed_support_is_not_a_transfer_spring():
    from app.calculation.stage_state import build_stage_states
    project = project_fixture()
    support = project.retaining_system.supports[0]
    case = CalculationCase(name="slab history", stages=[
        ConstructionStage(name="安装", excavationElevation=-6.0, stageType="support_installation", activeSupportIds=[support.id]),
        ConstructionStage(name="底板", excavationElevation=-6.0, stageType="bottom_slab", activeSupportIds=[support.id]),
        ConstructionStage(name="拆撑", excavationElevation=-6.0, stageType="support_removal", deactivatedSupportIds=[support.id], transferredSupportLevels=[support.level_index]),
        ConstructionStage(name="后续", excavationElevation=-6.0, stageType="final"),
    ])
    states = build_stage_states(project, case)
    state = states[case.stages[-1].id]
    assert state["slabActive"] is True
    assert state["slabActivationDepthM"] == 6.0
    assert support.id not in state["activeSupportIds"]
    assert support.id in state["removedSupportIds"]


def test_final_envelope_uses_updated_stage_results_and_tracks_governing_stage():
    from app.calculation.final_envelope import build_final_wall_envelopes
    from app.schemas.domain import PressureProfile, StageCalculationResult, WallInternalForceResult
    rows = []
    for stage, moment, design, displacement in [("before", 10.0, 15.0, 3.0), ("after", 80.0, 130.0, 9.0)]:
        rows.append(StageCalculationResult(stageId=stage, segmentId="face", pressureProfile=PressureProfile(points=[]),
            wallInternalForce=WallInternalForceResult(segmentId="face", stageId=stage,
                maxMoment=moment, maxShear=moment / 2, maxMomentDesign=design,
                maxShearDesign=design / 2, maxDisplacement=displacement)))
    # Represents the final reaction iteration replacing its earlier response.
    rows[0].wall_internal_force.max_moment_design = 180.0
    envelopes = build_final_wall_envelopes(rows)
    assert envelopes["face"]["design"]["moment"] == 180.0
    assert envelopes["face"]["governing"]["momentDesignStageId"] == "before"
    assert envelopes["face"]["standard"]["displacement"] == 9.0
    rows[0].wall_internal_force.max_displacement = None
    assert build_final_wall_envelopes(rows)["face"]["standard"]["displacement"] is None


def test_full_verification_preserves_inputs_and_uses_final_demand_for_proposal():
    from app.calculation.input_identity import canonical_hash, full_design_input
    from app.services.calculation_assurance import verify_current_calculation_contract
    project = project_fixture()
    case = project.calculation_cases[0]
    before = canonical_hash(full_design_input(project, case))
    original = project.retaining_system.model_dump(mode="json")
    result = engine.run_calculation(project, case, mode="verify")
    assert canonical_hash(full_design_input(project, case)) == before
    assert project.retaining_system.model_dump(mode="json") == original
    assert result.input_before_hash == result.input_after_hash == before
    assert verify_current_calculation_contract(project, result)["current"] is True
    proposals = result.derived_design["retainingSystem"]
    for wall in proposals["diaphragmWalls"]:
        rows = [r for r in result.stage_results if r.segment_id == wall["segmentId"]]
        assert wall["designResults"]["maxMomentDesign"] == pytest.approx(max(r.wall_internal_force.max_moment_design for r in rows), abs=0.001)
        assert all(r.wall_internal_force.solve_id == r.solve_id for r in rows)
    assert all(col["foundationDesign"] is None for col in proposals["columns"])
    assert result.formal_report_gate.allowed_for_official_issue is False


def test_global_consolidation_keeps_unknown_stage_separate_from_known_warning():
    project = Project(name="unknown aggregation")
    rows = [{"ruleId": "GB50010-FLEXURE-SUBSET", "objectId": "wall", "objectType": "Wall",
        "status": "manual_review", "calculatedValue": 50.0, "limitValue": None, "stageId": "unknown"},
        {"ruleId": "GB50010-FLEXURE-SUBSET", "objectId": "wall", "objectType": "Wall",
        "status": "warning", "calculatedValue": 50.0, "limitValue": 60.0, "stageId": "known"}]
    consolidated = engine._consolidate_global_checks(project, rows)
    assert any(row["limitValue"] is None and row["stageId"] == "unknown" for row in consolidated)


def test_model_block_and_unknown_global_displacement_cannot_report_summary_pass():
    from app.schemas.domain import PressureProfile, StageCalculationResult, GlobalCoupledSystemResult
    stage = StageCalculationResult(stageId="s", segmentId="f", pressureProfile=PressureProfile(points=[]),
        globalCoupledResult=GlobalCoupledSystemResult(method="failed", maxWallDisplacement=None, fallback=True))
    summary = engine._design_review_summary([{"ruleId": "PITGUARD-SOIL-RESISTANCE-MODEL", "status": "fail"}], [stage])
    assert summary.stability_status == "fail"
    assert summary.strength_status == "fail"
    assert summary.stiffness_status == "fail"


def test_transfer_case_final_iterations_envelopes_and_rebar_share_response():
    from test_v3_71_0_numerical_coupling import _project
    from app.calculation.input_identity import canonical_hash, full_design_input
    project = _project()
    case = project.calculation_cases[0]
    fingerprint = canonical_hash(full_design_input(project, case))
    result = engine.run_calculation(project, case, mode="verify")
    assert canonical_hash(full_design_input(project, case)) == fingerprint
    iteration = project.advanced_engineering["wallWaleTransferReactionIteration"]
    assert iteration["status"] in {"pass", "fail"}
    assert iteration["iterationCount"] >= 1
    for wall in result.derived_design["retainingSystem"]["diaphragmWalls"]:
        rows = [row for row in result.stage_results if row.segment_id == wall["segmentId"]]
        final_design_moment = max(row.wall_internal_force.max_moment_design for row in rows)
        assert wall["designResults"]["maxMomentDesign"] == pytest.approx(final_design_moment, abs=.001)
        for row in rows:
            assert row.wall_internal_force.solve_id == row.solve_id
            assert row.load_evidence["consumers"]["solveId"] == row.solve_id
            assert row.wall_internal_force_placeholder["maxMomentDesign"] == pytest.approx(row.wall_internal_force.max_moment_design)
            assert row.coupled_system_result["globalMaxSupportAxialForce"] == row.global_coupled_result.max_support_axial_force
            assert row.coupled_system_result["globalMaxWallDisplacement"] == row.global_coupled_result.max_wall_displacement
            assert row.coupled_system_result["globalEquilibriumDiagnostics"] == row.global_coupled_result.equilibrium_diagnostics
    assert result.design_review_summary.stability_status == "fail"
    assert result.formal_report_gate.allowed_for_official_issue is False


def test_transfer_execution_flag_is_part_of_frozen_input_identity():
    from app.calculation.input_identity import canonical_hash, full_design_input, solver_configuration_hash
    from app.services.calculation_assurance import build_calculation_contract
    project = project_fixture()
    case = project.calculation_cases[0]
    project.retaining_system.layout_summary.setdefault("transferSystem", {})["required"] = False
    before = (canonical_hash(full_design_input(project, case)), solver_configuration_hash(project, case),
        build_calculation_contract(project, case)["inputSnapshotHash"])
    project.retaining_system.layout_summary["transferSystem"]["required"] = True
    after = (canonical_hash(full_design_input(project, case)), solver_configuration_hash(project, case),
        build_calculation_contract(project, case)["inputSnapshotHash"])
    assert all(a != b for a, b in zip(before, after))


def test_iteration_displacement_units_cannot_hide_large_relative_change():
    from app.calculation.reaction_iteration import _displacement_deltas
    relative, absolute_mm = _displacement_deltas({"wall": .009}, {"wall": .001})
    assert relative == pytest.approx(8.0)
    assert absolute_mm == pytest.approx(8.0)
    assert relative > .01


def test_assurance_rejects_failed_design_solve_and_engineering_failure():
    from app.services.calculation_assurance import assess_calculation_result
    from app.schemas.domain import GlobalCoupledSystemResult
    project = project_fixture()
    case = project.calculation_cases[0]
    result = engine.run_calculation(project, case, mode="verify")
    for row in result.stage_results:
        row.design_global_coupled_result = GlobalCoupledSystemResult(method="failed design", fallback=True,
            reason="injected design-basis failure", maxWallDisplacement=None)
    result.checks = [{"ruleId": "PITGUARD-NUMERICAL-EQUILIBRIUM", "objectId": "face",
        "objectType": "GlobalCoupledSystem", "status": "fail", "loadBasis": "design",
        "calculatedValue": None, "limitValue": 1e-8, "message": "design solve failed", "clauseReference": "numerical gate"}]
    audit = {"status": "pass", "failCount": 0, "warningCount": 0, "issues": [], "checks": []}
    assurance = assess_calculation_result(project, case, result, input_audit=audit)
    assert assurance["status"] == "fail"
    assert assurance["eligibleForEngineeringUse"] is False
    assert assurance["eligibleForOfficialIssue"] is False


def test_supplied_reinforcement_selects_lowest_capacity_across_different_grades():
    from app.calculation.final_checks import supplied_wall_rebar
    from app.schemas.domain import ReinforcementGroup
    wall = project_fixture().retaining_system.diaphragm_walls[0]
    wall.thickness, wall.concrete_grade = .8, "C35"
    wall.reinforcement = [ReinforcementGroup(name="face A", barType="longitudinal", diameter=20,
        spacing=200, grade="HRB500", locationDescription="A"),
        ReinforcementGroup(name="face B", barType="longitudinal", diameter=20,
        spacing=180, grade="HRB335", locationDescription="B")]
    _, controlling = supplied_wall_rebar(wall)
    assert controlling.grade == "HRB335"


def test_wale_without_current_stage_result_cannot_retain_old_pass():
    from app.schemas.domain import WaleBeamDesignResult
    project = project_fixture()
    beam = project.retaining_system.wale_beams[0]
    beam.design_result = WaleBeamDesignResult(waleBeamCode=beam.code, maxMomentDesign=999.0, checkStatus="pass")
    beam.design_moment = 999.0
    checks = engine._design_wale_beams(project, [], 1.0)
    assert beam.design_result is None
    assert beam.design_moment is None
    assert any(row["objectId"] == beam.id and row["status"] == "manual_review" for row in checks)


def test_failed_design_wall_solve_is_published_as_incomplete_without_rebar_proposal(monkeypatch):
    import numpy as np
    from app.calculation import stage_solver, wall_internal_force
    real_wall = stage_solver.analyze_wall_on_elastic_foundation
    real_solve = np.linalg.solve
    def fail_design(**parameters):
        if "factored pressure combination" not in parameters["pressure_profile"].method:
            return real_wall(**parameters)
        def failure(*args, **kwargs):
            raise np.linalg.LinAlgError("injected design wall failure")
        monkeypatch.setattr(wall_internal_force.np.linalg, "solve", failure)
        try:
            return real_wall(**parameters)
        finally:
            monkeypatch.setattr(wall_internal_force.np.linalg, "solve", real_solve)
    monkeypatch.setattr(stage_solver, "analyze_wall_on_elastic_foundation", fail_design)
    project = project_fixture()
    before = project.retaining_system.model_dump(mode="json")
    result = engine.run_calculation(project, project.calculation_cases[0], mode="verify")
    assert project.retaining_system.model_dump(mode="json") == before
    assert all(row.wall_internal_force.max_moment_design is None for row in result.stage_results)
    assert all(row.wall_internal_force_placeholder["status"] == "partial" for row in result.stage_results)
    assert all(wall["designResults"] is None for wall in result.derived_design["retainingSystem"]["diaphragmWalls"])
    assert any(check["ruleId"] == "PITGUARD-WALL-DESIGN-ENVELOPE" and check["status"] == "fail" for check in result.checks)
    assert result.calculation_assurance["eligibleForEngineeringUse"] is False
    numerical = next(item for item in result.calculation_assurance["issues"] if item["code"] == "RESULT-NUMERICAL-QUALITY")
    assert numerical["status"] == "fail"
