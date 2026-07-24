from __future__ import annotations

from pathlib import Path

from app.calculation.engine import build_default_construction_cases
from app.schemas.domain import DesignControlStage, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.intelligent_design_optimizer import _case_for_trial
from app.services.workflow_v381 import (
    migrate_legacy_stages,
    repair_design_control_support_references,
    synchronize_design_control_case,
)


ROOT = Path(__file__).resolve().parents[3]


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.87.7 transfer recovery",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=30, y=0),
                Point2D(x=30, y=20), Point2D(x=0, y=20),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation))
    project = Project(name="V3.87.7 transfer recovery", excavation=excavation, retainingSystem=retaining)
    project.design_settings.design_basis_confirmed = True
    return project


def test_generated_replacement_stages_survive_support_id_regeneration() -> None:
    project = _project()
    project.calculation_cases = build_default_construction_cases(project)
    migrated = migrate_legacy_stages(project)
    assert migrated["migrated"] is True
    assert any(row.stage_type == "replacement" for row in project.design_control_stages)

    # Regenerate the full support topology. Support IDs change while level
    # semantics and the declared bottom-up replacement path remain equivalent.
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    result = repair_design_control_support_references(project)

    valid_ids = {row.id for row in project.retaining_system.supports}
    transfer = [row for row in project.design_control_stages if row.stage_type == "replacement"]
    assert result["manualRequired"] is False
    assert result["automaticTransferStageCount"] > 0 or result["transferSequenceRebuilt"] is True
    assert transfer
    assert all(set(row.required_support_ids) <= valid_ids for row in transfer)
    assert all(set(row.permitted_inactive_support_ids) <= valid_ids for row in transfer)
    assert all(row.permitted_inactive_support_levels for row in transfer)

    case, sync = synchronize_design_control_case(project)
    assert sync["synchronized"] is True
    assert case is not None
    replacement = [row for row in case.stages if row.stage_type == "replacement"]
    assert replacement
    assert all(row.transferred_support_levels for row in replacement)
    assert replacement[-1].transferred_support_levels == sorted({s.level_index for s in project.retaining_system.supports})


def test_new_support_level_rebuilds_complete_bottom_up_transfer_sequence() -> None:
    project = _project()
    project.calculation_cases = build_default_construction_cases(project)
    migrate_legacy_stages(project)

    supports = project.retaining_system.supports
    clone = supports[-1].model_copy(deep=True)
    new_level = max(row.level_index for row in supports) + 1
    clone.id = f"support-added-L{new_level}"
    clone.code = f"AUTO-L{new_level}"
    clone.level_index = new_level
    clone.elevation = min(row.elevation for row in supports) - 1.5
    supports.append(clone)

    result = repair_design_control_support_references(project)
    transfer = [row for row in project.design_control_stages if row.stage_type == "replacement"]
    covered = {
        level
        for row in transfer
        for level in row.permitted_inactive_support_levels
    }
    assert result["manualRequired"] is False
    assert result["transferSequenceRebuilt"] is True
    assert covered == {row.level_index for row in project.retaining_system.supports}
    assert len(transfer) == len(covered)


def test_frozen_ambiguous_transfer_stage_uses_screening_case_instead_of_zero_candidates() -> None:
    project = _project()
    project.design_control_stages = [
        DesignControlStage(
            name="用户冻结的专项换撑阶段",
            excavationElevationLower=-12.0,
            excavationElevationUpper=-12.0,
            requiredSupportIds=["obsolete-support"],
            permittedInactiveSupportIds=["obsolete-support"],
            stageType="replacement",
            dataStatus="frozen",
        )
    ]
    repair = repair_design_control_support_references(project)
    assert repair["manualRequired"] is True

    case, selection = _case_for_trial(project, None)
    valid_ids = {row.id for row in project.retaining_system.supports}
    assert selection["source"] == "current_topology_transfer_screening"
    assert selection["formalTransferReviewRequired"] is True
    assert case.stages
    assert all(set(row.active_support_ids) <= valid_ids for row in case.stages)


def test_workspace_explains_transfer_recovery_and_pending_formal_closure() -> None:
    workspace = (ROOT / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    assert "自动恢复阶段" in workspace
    assert "换撑序列重建" in workspace
    assert "calculated_pending_transfer_review" in workspace
    assert "current_topology_transfer_screening" in manager
    assert "candidateCount\": 0" not in manager


def test_default_stage_builder_groups_members_by_level_not_raw_elevation() -> None:
    project = _project()
    supports = project.retaining_system.supports
    for index, support in enumerate(supports):
        support.level_index = 1
        support.elevation = -2.0 - index * 0.01
    case = build_default_construction_cases(project)[0]
    # One installation stage + final stage + one replacement stage.
    assert len(case.stages) == 3
    assert case.stages[0].active_support_levels == [1]


def test_pathological_legacy_vertical_levels_are_normalized_before_search() -> None:
    from app.services.intelligent_design_optimizer import _normalize_pathological_vertical_levels

    project = _project()
    supports = project.retaining_system.supports
    seed = supports[0]
    project.retaining_system.supports = []
    for level in range(1, 11):
        clone = seed.model_copy(deep=True)
        clone.id = f"legacy-level-{level}"
        clone.code = f"LEGACY-L{level}"
        clone.level_index = level
        clone.elevation = -0.8 - level * 0.9
        project.retaining_system.supports.append(clone)
    project.retaining_system.optimization_locks = []

    result = _normalize_pathological_vertical_levels(project)
    assert result["changed"] is True
    assert len({row.level_index for row in project.retaining_system.supports}) <= 6
    assert len(project.design_settings.support_level_depths_m) <= 6


def test_analysis_diagnostics_separates_stage_count_from_stage_segment_results() -> None:
    engine = (ROOT / "services/api/app/calculation/engine.py").read_text(encoding="utf-8")
    assert "stageCount=len(case.stages)" in engine
    assert "stageSegmentResultCount=len(stage_results)" in engine


def test_task_manager_keeps_screening_case_after_candidate_adoption() -> None:
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    assert "候选采用后正式换撑工况仍需人工确认" in manager
    assert manager.count('"source": "current_topology_transfer_screening"') >= 2


def test_compacted_legacy_project_recovers_standard_transfer_path_without_manifest() -> None:
    project = _project()
    project.calculation_cases = build_default_construction_cases(project)
    migrate_legacy_stages(project)
    project.retaining_system.replacement_path = []
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    project.retaining_system.replacement_path = []
    result = repair_design_control_support_references(project)
    assert result["manualRequired"] is False
    assert all(
        row.transfer_path_status == "mapped"
        for row in project.design_control_stages
        if row.stage_type in {"replacement", "support_removal"}
    )
