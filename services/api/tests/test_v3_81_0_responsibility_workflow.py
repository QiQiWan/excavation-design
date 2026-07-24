from __future__ import annotations

from types import SimpleNamespace

from app.schemas.domain import (
    ConstructionPlanStage,
    FieldExecutionSnapshot,
    Point2D,
    Polyline2D,
    Project,
)
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.standards_matrix import build_online_documentation
from app.services.statutory_workflow import evaluate_statutory_workflow
from app.services.support_layout import SupportLayoutConfig
from app.services.workflow_v381 import (
    apply_design_scenario,
    assess_field_snapshot,
    build_scenario_envelope,
    design_control_signature,
    evaluate_construction_plan_stage,
    evaluate_construction_preparation_gate,
    evaluate_design_issue_gate,
    generate_design_scenarios,
    migrate_legacy_stages,
    run_design_scenario_suite,
    set_design_scenario_approval,
    synchronize_design_control_case,
    workflow_overview,
)


def _project() -> Project:
    outline = [(0, 0), (60, 0), (60, 20), (35, 20), (35, 45), (0, 45)]
    excavation = make_excavation_model(
        "V3.81 responsibility",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in outline], closed=True),
        0.0,
        -16.0,
    )
    system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="zoned_direct", concave_transfer_template="junction_hub_frame"),
    )
    return Project(name="V3.81 responsibility", excavation=excavation, retainingSystem=system)


def test_legacy_stages_migrate_to_designer_control_semantics() -> None:
    project = _project()
    migration = migrate_legacy_stages(project)
    assert migration["migrated"] is True
    assert project.design_control_stages
    assert all(row.value_type in {"design_value", "design_limit", "design_assumption"} for row in project.design_control_stages)
    assert all(row.hold_points for row in project.design_control_stages)
    case, sync = synchronize_design_control_case(project)
    assert sync["synchronized"] is True
    assert case is not None and case.locked is True and case.source == "synchronized"
    assert "现场实测" in (case.synchronization_note or "")


def test_design_scenarios_cover_baseline_and_adverse_cases() -> None:
    project = _project()
    migrate_legacy_stages(project)
    result = generate_design_scenarios(project)
    categories = {row.category for row in project.design_scenarios}
    assert result["scenarioCount"] == len(project.design_control_stages) * 9
    assert {"baseline", "support_delay", "overexcavation", "groundwater", "member_anomaly"} <= categories
    assert all(row.design_scenario_ids for row in project.design_control_stages)


def test_construction_plan_is_checked_without_overwriting_design() -> None:
    project = _project()
    migrate_legacy_stages(project)
    design = project.design_control_stages[-1]
    original_lower = design.excavation_elevation_lower
    acceptable = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=(design.excavation_elevation_lower + design.excavation_elevation_upper) / 2.0,
        planned_support_ids=list(design.required_support_ids),
        planned_groundwater_level=(design.groundwater_level_limit - 1.0) if design.groundwater_level_limit is not None else None,
        planned_surcharge=(0.8 * design.surcharge_limit) if design.surcharge_limit is not None else None,
        approval_status="submitted",
    )
    good = evaluate_construction_plan_stage(project, acceptable)
    assert good["grade"] == "A"
    prohibited = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=design.excavation_elevation_lower - float(design.overexcavation_limit or 0.0) - 1.0,
        planned_support_ids=[],
        planned_surcharge=(float(design.surcharge_limit or 1.0) * 1.5),
    )
    bad = evaluate_construction_plan_stage(project, prohibited)
    assert bad["grade"] == "E"
    assert bad["prohibited"] is True
    near_limit = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=(design.excavation_elevation_lower + design.excavation_elevation_upper) / 2.0,
        planned_support_ids=list(design.required_support_ids),
        planned_surcharge=(0.95 * design.surcharge_limit) if design.surcharge_limit is not None else None,
    )
    assert evaluate_construction_plan_stage(project, near_limit)["grade"] == "B"
    recalc = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=design.excavation_elevation_lower - min(0.1, 0.5 * float(design.overexcavation_limit or 0.2)),
        planned_support_ids=list(design.required_support_ids),
    )
    assert evaluate_construction_plan_stage(project, recalc)["grade"] == "C"
    substitution = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=(design.excavation_elevation_lower + design.excavation_elevation_upper) / 2.0,
        planned_support_ids=list(design.required_support_ids) + ["support-substitute"],
    )
    assert evaluate_construction_plan_stage(project, substitution)["grade"] == "D"
    assert design.excavation_elevation_lower == original_lower


def test_field_snapshot_outside_design_creates_hold_and_recalculation() -> None:
    project = _project()
    migrate_legacy_stages(project)
    design = project.design_control_stages[-1]
    plan = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=design.excavation_elevation_lower,
        planned_support_ids=list(design.required_support_ids),
        approval_status="approved",
    )
    project.construction_plan_stages = [plan]
    snapshot = FieldExecutionSnapshot(
        construction_plan_stage_id=plan.id,
        actual_excavation_elevation=design.excavation_elevation_lower - float(design.overexcavation_limit or 0.0) - 0.5,
        active_support_ids=[],
        quality="verified",
    )
    result = assess_field_snapshot(project, snapshot, persist=True)
    assert result["status"] == "fail"
    assert result["withinDesignEnvelope"] is False
    assert result["workHoldRecommended"] is True
    assert result["recalculationRequired"] is True
    assert project.deviation_events
    assert any(row.severity == "critical" for row in project.deviation_events)
    event_count = len(project.deviation_events)
    assess_field_snapshot(project, snapshot, persist=True)
    assert len(project.deviation_events) == event_count


def test_design_issue_gate_excludes_future_construction_and_field_data(monkeypatch) -> None:
    project = _project()
    project.design_settings.design_basis_confirmed = True
    migrate_legacy_stages(project)
    for stage in project.design_control_stages:
        stage.data_status = "approved"
    # Only the truthiness is used by the gate. These sentinels model already
    # qualified design-source data without pulling a heavy geological fixture.
    project.boreholes = [object()]  # type: ignore[list-item]
    project.strata = [object()]  # type: ignore[list-item]
    project.geological_model = object()  # type: ignore[assignment]
    monkeypatch.setattr("app.services.workflow_v381._latest_calculation_status", lambda _p: (SimpleNamespace(check_summary={"fail": 0}), {"current": True}))
    monkeypatch.setattr("app.services.workflow_v381.review_status", lambda _p: {"status": "approved", "approvalValid": True, "registeredStructuralApproverValid": True})
    design = evaluate_design_issue_gate(project)
    construction = evaluate_construction_preparation_gate(project)
    assert design["eligible"] is True
    assert construction["eligible"] is False
    assert "专项施工方案" in design["explicitExclusions"]
    assert "CONSTRUCTION_PLAN" in construction["blockingCodes"]


def test_statutory_evidence_is_partitioned_by_responsibility_phase() -> None:
    project = _project()
    project.design_settings.hazardous_work_classification = "large_scale_hazardous"
    result = evaluate_statutory_workflow(project)
    assert "design_source_data" in result["missingByPhase"]["design"]
    assert "special_construction_plan" in result["missingByPhase"]["construction"]
    assert "stage_acceptance" in result["missingByPhase"]["field"]
    assert "special_construction_plan" not in result["missingByPhase"]["design"]
    assert result["formalIssueEligible"] == result["designIssueEligible"]



def test_v381_project_roundtrip_preserves_responsibility_objects() -> None:
    project = _project()
    migrate_legacy_stages(project)
    generate_design_scenarios(project)
    design = project.design_control_stages[-1]
    plan = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=design.excavation_elevation_lower,
        planned_support_ids=list(design.required_support_ids),
    )
    project.construction_plan_stages = [plan]
    snapshot = FieldExecutionSnapshot(construction_plan_stage_id=plan.id, active_support_ids=list(plan.planned_support_ids))
    project.field_execution_snapshots = [snapshot]
    restored = Project.model_validate(project.model_dump(mode="json", by_alias=True))
    assert len(restored.design_control_stages) == len(project.design_control_stages)
    assert len(restored.design_scenarios) == len(project.design_scenarios)
    assert restored.construction_plan_stages[0].design_control_stage_id == design.id
    assert restored.field_execution_snapshots[0].construction_plan_stage_id == plan.id

def test_workflow_overview_and_online_docs_expose_v381_contract() -> None:
    project = _project()
    overview = workflow_overview(project)
    assert overview["schema"] == "pitguard-business-workflow-v381"
    assert {row["domain"] for row in overview["responsibilityBoundary"]} == {"design", "construction", "field"}
    docs = build_online_documentation()
    chapter_ids = {row["id"] for row in docs["chapters"]}
    assert "responsibility" in chapter_ids
    assert any(row["version"] == "3.81" for row in docs["releaseRoadmap"])
    assert docs["releaseRoadmap"][-1]["version"] == "3.87.4"
    assert len(docs["responsibilityWorkflow"]["domains"]) == 3


def test_scenario_approval_and_envelope_read_formal_summary_contract() -> None:
    project = _project()
    migrate_legacy_stages(project)
    generate_design_scenarios(project)
    adverse = [row for row in project.design_scenarios if row.category != "baseline"][:2]
    approval = set_design_scenario_approval(project, [row.id for row in adverse], approval_status="approved", enabled=True)
    assert approval["approvedCount"] >= 2
    project.advanced_engineering["formalAdverseScenarioSuite"] = {
        "summaries": [
            {
                "scenarioCode": adverse[0].code,
                "status": "warning",
                "maxWallDisplacementMm": 12.5,
                "maxSupportForceKn": 1800.0,
                "maxWallMomentKnM": 950.0,
                "minimumSafetyFactor": 1.31,
                "evidenceLevel": "formal_staged_rerun",
            }
        ]
    }
    envelope = build_scenario_envelope(project)
    assert envelope["candidateResultCount"] == 1
    assert envelope["envelope"]["maxWallDisplacement"]["value"] == 12.5
    assert envelope["envelope"]["minSafetyFactor"]["value"] == 1.31
    assert adverse[1].code in envelope["pendingFormalScenarioCodes"]


def test_construction_and_field_release_require_approved_verified_evidence(monkeypatch) -> None:
    project = _project()
    migrate_legacy_stages(project)
    for stage in project.design_control_stages:
        stage.data_status = "approved"
    design = project.design_control_stages[-1]
    plan = ConstructionPlanStage(
        design_control_stage_id=design.id,
        planned_excavation_elevation=design.excavation_elevation_lower,
        planned_support_ids=list(design.required_support_ids),
        approval_status="submitted",
    )
    project.construction_plan_stages = [plan]
    monkeypatch.setattr("app.services.workflow_v381.evaluate_design_issue_gate", lambda _p: {"eligible": True})
    construction = evaluate_construction_preparation_gate(project)
    assert "CONSTRUCTION_PLAN_APPROVAL" in construction["blockingCodes"]
    plan.approval_status = "approved"
    project.field_execution_snapshots = [
        FieldExecutionSnapshot(
            construction_plan_stage_id=plan.id,
            actual_excavation_elevation=design.excavation_elevation_lower,
            active_support_ids=list(design.required_support_ids),
            quality="provisional",
        )
    ]
    monkeypatch.setattr("app.services.workflow_v381.evaluate_construction_preparation_gate", lambda _p: {"eligible": True})
    from app.services.workflow_v381 import evaluate_field_release_gate
    field = evaluate_field_release_gate(project, plan.id)
    assert "FIELD_SNAPSHOT_QUALITY" in field["blockingCodes"]


def test_design_scenario_application_is_isolated_and_stage_specific() -> None:
    project = _project()
    migrate_legacy_stages(project)
    generate_design_scenarios(project)
    source_active = [list(row.required_support_ids) for row in project.design_control_stages]
    trial = project.model_copy(deep=True)
    scenario = next(row for row in trial.design_scenarios if row.category == "support_delay" and row.stage_id == trial.design_control_stages[-1].id)
    case, assumptions = apply_design_scenario(trial, scenario)
    assert assumptions["method"] == "stage-local support activation delay"
    assert len(case.stages[-1].active_support_ids) <= len(source_active[-1])
    assert [list(row.required_support_ids) for row in project.design_control_stages] == source_active


def test_approved_design_scenario_suite_runs_on_isolated_clones(monkeypatch) -> None:
    project = _project()
    migrate_legacy_stages(project)
    generate_design_scenarios(project)
    selected = [row for row in project.design_scenarios if row.category in {"surcharge", "groundwater"}][:2]
    set_design_scenario_approval(project, [row.id for row in selected], approval_status="approved", enabled=True)

    class FakeResult:
        id = "calc-fake"
        case_id = "case-fake"
        check_summary = {"pass": 1, "warning": 0, "fail": 0}
        governing_values = SimpleNamespace(max_displacement=0.012, max_support_axial_force=1800.0, max_wall_moment=950.0, max_wall_shear=600.0)
        stability_detailed_result = SimpleNamespace(min_safety_factor=1.31)
        calculated_at = "2026-07-22T00:00:00+00:00"
        def model_dump(self, **_kwargs):
            return {"id": self.id, "caseId": self.case_id}

    monkeypatch.setattr("app.calculation.engine.run_calculation", lambda *_args, **_kwargs: FakeResult())
    suite = run_design_scenario_suite(project, [row.id for row in selected], max_scenarios=2)
    assert suite["summary"]["completedCount"] == 2
    assert suite["summary"]["failedExecutionCount"] == 0
    assert len(suite["fullResults"]) == 2
    assert not project.calculation_results



def test_design_control_signature_ignores_approval_metadata_only() -> None:
    project = _project()
    migrate_legacy_stages(project)
    before = design_control_signature(project.design_control_stages)
    for stage in project.design_control_stages:
        stage.data_status = "approved"
        stage.updated_at = "2026-07-22T12:00:00+00:00"
    assert design_control_signature(project.design_control_stages) == before
    project.design_control_stages[-1].surcharge_limit = float(project.design_control_stages[-1].surcharge_limit or 0.0) + 1.0
    assert design_control_signature(project.design_control_stages) != before


def test_design_control_approval_only_save_preserves_current_calculation() -> None:
    from app.routers.workflow import save_design_control_stages

    project = _project()
    migrate_legacy_stages(project)
    synchronize_design_control_case(project)

    class Repo:
        def require(self, _project_id: str):
            return project
        def save(self, *_args, **_kwargs):
            return project

    approval_only = [row.model_copy(deep=True) for row in project.design_control_stages]
    for stage in approval_only:
        stage.data_status = "approved"
    response = save_design_control_stages(project.id, approval_only, Repo())
    assert response["numericalInputsChanged"] is False
    assert response["calculationInvalidated"] is False
    assert response["synchronization"]["reason"] == "approval_metadata_only"

    changed = [row.model_copy(deep=True) for row in project.design_control_stages]
    changed[-1].surcharge_limit = float(changed[-1].surcharge_limit or 0.0) + 1.0
    response = save_design_control_stages(project.id, changed, Repo())
    assert response["numericalInputsChanged"] is True
    assert response["calculationInvalidated"] is True

def test_design_scenario_envelope_is_registered_as_heavy_background_task() -> None:
    from app.tasks.manager import task_manager
    assert "design_scenario_envelope" in task_manager._heavy_operations
    assert "设计允许域情景包络复算" in task_manager._title_for("design_scenario_envelope")
