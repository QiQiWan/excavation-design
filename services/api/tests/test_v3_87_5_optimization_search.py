from __future__ import annotations

from pathlib import Path

from app.schemas.domain import CalculationResult, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.intelligent_design_closure import apply_intervention_action
from app.services.intelligent_design_optimizer import run_bounded_optimization_search


ROOT = Path(__file__).resolve().parents[3]


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.87.5 optimization",
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
    project = Project(name="V3.87.5 optimization", excavation=excavation, retainingSystem=retaining)
    project.design_settings.design_basis_confirmed = True
    wall = project.retaining_system.diaphragm_walls[0]
    wall.thickness = 0.80
    previous = CalculationResult(projectId=project.id, caseId="before")
    previous.design_iteration_summary = {
        "intelligentDesignClosure": {
            "status": "needs_intervention",
            "interventionOptions": [{
                "actionId": f"strengthen-wall:{wall.id}",
                "label": f"增厚 {wall.panel_code} 并重新配筋",
                "automaticAllowed": True,
                "proposedValue": 0.90,
            }],
        }
    }
    project.calculation_results = [previous]
    return project


def test_repeated_wall_action_uses_current_persisted_value_not_stale_proposal() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    stale_proposal = 0.90

    first = apply_intervention_action(project, f"strengthen-wall:{wall.id}", stale_proposal)
    second = apply_intervention_action(project, f"strengthen-wall:{wall.id}", stale_proposal)

    assert first["before"] == 0.80
    assert first["after"] == 0.90
    assert second["before"] == 0.90
    assert second["after"] == 1.00
    assert wall.thickness == 1.00
    assert second["valuePolicy"] == "relative_from_current"


def test_bounded_search_ranks_and_applies_best_candidate(monkeypatch) -> None:
    project = _project()

    def fake_closure(trial: Project, calculation_case=None, *, auto_repair=True, strategy=None, max_iterations=None):
        closed = strategy == "stiffness_first"
        result = CalculationResult(projectId=trial.id, caseId="trial")
        closure = {
            "status": "closed" if closed else "needs_intervention",
            "strategy": strategy,
            "executedIterations": 2,
            "calculationClosed": closed,
            "structuralClosed": closed,
            "hardFailCount": 0 if closed else 2,
            "structuralFailCount": 0 if closed else 2,
            "quantitativeOpenCount": 0 if closed else 2,
            "reviewCount": 0,
            "reserveShortfallCount": 0 if closed else 2,
            "remainingChecks": [] if closed else [{"safetyFactor": 0.9, "targetSafetyFactor": 1.1}],
            "remainingReviewItems": [],
            "interventionOptions": [],
            "history": [{"actions": []}],
        }
        result.design_iteration_summary = {"intelligentDesignClosure": closure}
        return result, closure

    monkeypatch.setattr(
        "app.services.intelligent_design_optimizer.run_intelligent_design_closure",
        fake_closure,
    )

    result, search = run_bounded_optimization_search(project, max_candidates=4, max_iterations=4)

    assert search["selectedStrategy"] == "stiffness_first"
    assert search["feasibleCandidateCount"] == 1
    assert search["status"] == "closed"
    assert search["evaluatedCandidateCount"] == 4
    assert any(row.get("selected") and row.get("rank") == 1 for row in search["candidates"])
    assert result.design_iteration_summary["optimizationSearch"]["selectedStrategy"] == "stiffness_first"
    assert project.advanced_engineering["calculationOptimizationSearch"]["selectedStrategy"] == "stiffness_first"


def test_frontend_and_task_manager_expose_persistent_action_and_one_click_search() -> None:
    workspace = (ROOT / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")

    assert "calculation_auto_close" in workspace
    assert "一键计算、优化并闭合" in workspace
    assert "修复阻断并复算" not in workspace
    assert "一键优化并复算" not in workspace
    assert "按当前值递增并复算" not in workspace
    assert "value: option.proposedValue" not in workspace
    assert 'task.operation in {"calculation_optimize_search", "calculation_auto_close"}' in manager
    assert "_run_calculation_optimization_search" in manager
    assert "lastCalculationClosureAction" in manager
