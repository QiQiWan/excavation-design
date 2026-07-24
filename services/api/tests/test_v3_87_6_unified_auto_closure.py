from __future__ import annotations

from pathlib import Path

from app.schemas.domain import CalculationResult, DesignControlStage, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.intelligent_design_optimizer import run_bounded_optimization_search
from app.services.workflow_v381 import repair_design_control_support_references


ROOT = Path(__file__).resolve().parents[3]


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.87.6 unified closure",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=28, y=0),
                Point2D(x=28, y=18), Point2D(x=0, y=18),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation))
    project = Project(name="V3.87.6 unified closure", excavation=excavation, retainingSystem=retaining)
    project.design_settings.design_basis_confirmed = True
    return project


def test_regular_design_control_stage_stale_supports_are_remapped() -> None:
    project = _project()
    project.design_control_stages = [
        DesignControlStage(
            name="开挖至第一道支撑以下",
            excavationElevationLower=-4.0,
            excavationElevationUpper=0.0,
            requiredSupportIds=["old-support-1", "old-support-2"],
            stageType="excavation",
            dataStatus="approved",
        ),
        DesignControlStage(
            name="最终开挖",
            excavationElevationLower=-10.0,
            excavationElevationUpper=-4.0,
            requiredSupportIds=["old-support-3"],
            stageType="final",
            dataStatus="approved",
        ),
    ]
    result = repair_design_control_support_references(project)
    valid = {row.id for row in project.retaining_system.supports}
    assert result["changed"] is True
    assert result["manualRequired"] is False
    assert set(project.design_control_stages[0].required_support_ids) <= valid
    assert set(project.design_control_stages[1].required_support_ids) == valid


def test_replacement_stage_stale_supports_require_manual_confirmation() -> None:
    project = _project()
    project.design_control_stages = [
        DesignControlStage(
            name="地下室换撑",
            excavationElevationLower=-10.0,
            excavationElevationUpper=-9.0,
            requiredSupportIds=["old-support"],
            permittedInactiveSupportIds=["old-support"],
            stageType="replacement",
            dataStatus="approved",
        )
    ]
    result = repair_design_control_support_references(project)
    assert result["changed"] is False
    assert result["manualRequired"] is True
    assert result["manualStageCount"] == 1


def test_search_reports_cannot_close_when_no_candidate_is_feasible(monkeypatch) -> None:
    project = _project()

    def fake_closure(trial: Project, calculation_case=None, *, auto_repair=True, strategy=None, max_iterations=None):
        result = CalculationResult(projectId=trial.id, caseId="trial")
        closure = {
            "status": "needs_intervention",
            "strategy": strategy,
            "executedIterations": 2,
            "calculationClosed": False,
            "structuralClosed": False,
            "hardFailCount": 1,
            "structuralFailCount": 1,
            "quantitativeOpenCount": 1,
            "reviewCount": 0,
            "reserveShortfallCount": 1,
            "remainingChecks": [{"ruleId": "WALL_DISPLACEMENT", "safetyFactor": 0.9, "targetSafetyFactor": 1.1}],
            "remainingReviewItems": [],
            "interventionOptions": [],
            "history": [{"actions": []}],
        }
        result.design_iteration_summary = {"intelligentDesignClosure": closure}
        return result, closure

    monkeypatch.setattr("app.services.intelligent_design_optimizer.run_intelligent_design_closure", fake_closure)
    result, search = run_bounded_optimization_search(project, max_candidates=2, max_iterations=2)
    assert search["status"] == "cannot_close"
    assert search["closureOutcome"]["status"] == "cannot_close"
    assert search["closureOutcome"]["closed"] is False
    assert search["feasibleCandidateCount"] == 0
    assert "WALL_DISPLACEMENT" in search["closureOutcome"]["reasonCodes"]
    assert result.design_iteration_summary["optimizationSearch"]["status"] == "cannot_close"


def test_workspace_has_one_unified_calculation_button_and_monotonic_task_progress() -> None:
    workspace = (ROOT / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    assert workspace.count("一键计算、优化并闭合") >= 2
    assert "修复阻断并复算" not in workspace
    assert "一键优化并复算" not in workspace
    assert "计算当前方案" not in workspace
    assert "按当前值递增并复算" not in workspace
    assert "Math.max(previousProgress" in workspace
    assert "safe_progress = max(int(task.progress or 0)" in manager
