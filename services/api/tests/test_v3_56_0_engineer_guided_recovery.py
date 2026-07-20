from __future__ import annotations

from pathlib import Path

from app.schemas.domain import Point2D, Polyline2D, Project, WaleBeamDesignResult
from app.services.core_engineering_presentation import _catalog_spec_for_check
from app.services.deepening_readiness import build_deepening_readiness
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.verification_coverage import missing_evidence_record
from app.tasks.manager import TaskManager, TaskRecord


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.56 恢复链测试",
        Polyline2D(
            points=[Point2D(x=0, y=0), Point2D(x=24, y=0), Point2D(x=24, y=16), Point2D(x=0, y=16)],
            closed=True,
        ),
        0.0,
        -8.0,
    )
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation))
    project = Project(name="V3.56 恢复链测试", excavation=excavation, retainingSystem=retaining)
    project.design_settings.design_basis_confirmed = True
    return project


def test_unresolved_verification_rows_have_distinct_engineering_resolution_types() -> None:
    project = _project()
    available = {"calculation": True, "column": True, "support": True, "support_force": True}
    automatic = missing_evidence_record(project, {
        "requires": ["calculation", "support_force"], "implementation": "implemented",
    }, availability=available)
    screening = missing_evidence_record(project, {
        "requires": ["calculation", "column"], "implementation": "screening",
    }, availability=available)
    specialist = missing_evidence_record(project, {
        "requires": ["support"], "implementation": "specialist_review",
    }, availability=available)

    assert automatic["evidenceState"] == "not_calculated"
    assert automatic["resolutionType"] == "automatic_recalculation"
    assert automatic["automaticActionAvailable"] is True
    assert screening["evidenceState"] == "manual_review"
    assert screening["resolutionType"] == "engineering_screening_review"
    assert specialist["resolutionType"] == "specialist_review"
    assert "重复计算" in specialist["whyUnresolved"]


def test_node_bearing_and_support_combined_checks_map_to_separate_catalog_rows() -> None:
    assert _catalog_spec_for_check(
        {"ruleId": "PITGUARD-SUPPORT-COMBINED-INTERACTION-SCREEN"}, {"objectScope": "support"},
    )["ruleId"] == "SUPPORT_COMBINED"
    assert _catalog_spec_for_check(
        {"ruleId": "GB50010-WALE-NODE-REBAR-COORDINATION-SUBSET"}, {"objectScope": "wale"},
    )["ruleId"] == "SUPPORT_NODE"
    assert _catalog_spec_for_check(
        {"ruleId": "GB50010-NODE-BEARING-SUBSET"}, {"objectScope": "node"},
    )["ruleId"] == "WALE_BEARING"


def test_missing_beam_gate_explains_reason_and_exposes_executable_recovery(monkeypatch) -> None:
    project = _project()
    project.retaining_system.rebar_design_scheme = {
        "wallZones": [{"zoneId": "WZ-1"}],
        "supportRebarContractSummary": {"incompleteCount": 0},
        "beamRebarContractSummary": {"incompleteCount": 0},
    }
    monkeypatch.setattr(
        "app.services.deepening_readiness.calculation_readiness",
        lambda _project: {"valid": True, "messages": ["当前计算有效"], "failCount": 0, "contract": {"legacy": False}},
    )

    gate = build_deepening_readiness(project, checks=[], scheme_applied=True)
    blocker = next(item for item in gate["blockers"] if item["reasonCode"] == "BEAM_DESIGN_RESULT_MISSING")
    assert blocker["automaticActionAvailable"] is True
    assert blocker["actionOperation"] == "rebar_design"
    assert blocker["actionPayload"]["repairMissingDesignEvidence"] is True
    assert "为什么" not in blocker["whyBlocked"]  # text is the answer itself, not a UI token
    assert blocker["expectedOutcome"]
    assert next(step for step in gate["steps"] if step["id"] == "beam_design")["status"] == "fail"
    assert gate["automaticRecovery"]["available"] is True


def test_rebar_worker_recovers_calculation_and_missing_beam_results_before_applying(monkeypatch) -> None:
    project = _project()
    state = {"calculation_valid": False}

    class Repo:
        def require_with_latest_calculation(self, _project_id: str) -> Project:
            return project

        def save(self, *_args, **_kwargs) -> None:
            return None

    manager = TaskManager.__new__(TaskManager)
    manager._repo = lambda: Repo()  # type: ignore[method-assign]
    manager._stage = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    manager._append_log = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    manager._enforce_memory_budget = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    manager._check_cancel = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    manager._memory_checkpoint = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    def fake_calculation(_task: TaskRecord, _payload: dict) -> dict:
        state["calculation_valid"] = True
        for beam in [
            *project.retaining_system.crown_beams,
            *project.retaining_system.wale_beams,
            *(project.retaining_system.ring_beams or []),
        ]:
            beam.design_result = WaleBeamDesignResult(
                waleBeamCode=beam.code, checkStatus="pass", momentCapacity=1000.0, shearCapacity=1000.0,
            )
        return {"calculationResultId": "recovered"}

    manager._run_calculation_full = fake_calculation  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.deepening_readiness.calculation_readiness",
        lambda _project: {"valid": state["calculation_valid"], "messages": ["当前计算有效" if state["calculation_valid"] else "计算结果已过期"], "failCount": 0},
    )
    monkeypatch.setattr("app.tasks.manager.append_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.rebar_scheme_optimizer.apply_rebar_design_scheme",
        lambda _project, mode: {
            "status": "pass", "checks": [], "summary": {"failCount": 0, "warningCount": 0},
            "diagnostics": {"canIssueConstructionDrawings": False, "deepeningGate": {"status": "review", "blockerCount": 0, "warningCount": 1, "blockers": [], "nextActions": []}},
            "requiresRecalculation": False, "supportRebarContractSummary": {"incompleteCount": 0},
        },
    )

    before = sum(
        beam.design_result is None
        for beam in [*project.retaining_system.crown_beams, *project.retaining_system.wale_beams, *(project.retaining_system.ring_beams or [])]
    )
    result = manager._run_rebar_design(
        TaskRecord(id="task-v356", project_id=project.id, operation="rebar_design", title="恢复配筋"),
        {"mode": "balanced", "apply": True, "recalculate": True, "repairMissingDesignEvidence": True},
    )
    assert before > 0
    assert result["recoveredCalculationContract"] is True
    assert result["missingBeamDesignCountBeforeRecovery"] == before
    assert result["recoveredMissingBeamDesignCount"] == before
    assert result["remainingMissingBeamDesignCount"] == 0


def test_frontend_keeps_complete_support_rebar_and_actionable_recovery_source() -> None:
    root = Path(__file__).resolve().parents[3]
    panel = (root / "apps/web/src/components/RebarDesignPanel.tsx").read_text(encoding="utf-8")
    workspace = (root / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    assert "repairMissingDesignEvidence: true" in panel
    assert "allSupportRows.map" in panel
    assert "端部 / 跨中箍筋" in panel and "侧面构造筋" in panel and "拉结 / 附加筋" in panel
    assert "repairMissingDesignEvidence: true" in workspace
    assert "为什么：" in workspace and "完成标准：" in workspace
