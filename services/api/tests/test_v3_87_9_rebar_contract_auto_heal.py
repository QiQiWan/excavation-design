from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_contract_diagnostics_name_changed_fields() -> None:
    assurance = (ROOT / "services/api/app/services/calculation_assurance.py").read_text(encoding="utf-8")
    readiness = (ROOT / "services/api/app/services/deepening_readiness.py").read_text(encoding="utf-8")
    assert '"mismatches": mismatches' in assurance
    assert '"adoptedDesignSnapshotHash"' in assurance
    assert '"supportTopologyHash"' in assurance
    assert '计算合同与当前设计快照不一致' in readiness
    assert '差异：' in readiness
    assert 'auto_resolvable=True' in readiness


def test_p3_hydrates_stage_evidence_and_auto_heals_recoverable_gate() -> None:
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    method = manager.split("def _run_p3_detailing_closure", 1)[1].split("def _run_support_layout_optimization", 1)[0]
    assert "repo.require_with_latest_calculation(task.project_id)" in method
    assert "P3 前自动恢复当前计算合同并重新闭合配筋" in method
    assert "self._run_rebar_design(task" in method
    assert "autoResolvable" in method


def test_rebar_worker_finalizes_gate_from_persisted_authoritative_evidence() -> None:
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    method = manager.split("def _run_rebar_design", 1)[1].split("def _run_formal_adverse_scenarios", 1)[0]
    assert "task.rebar_design.finalize_contract" in method
    assert "repo.require_with_latest_calculation(project.id)" in method
    assert "scheme = final_scheme" in method


def test_frontend_uses_auto_recalculation_and_suppresses_transient_false_blocker() -> None:
    workspace = (ROOT / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "apps/web/src/components/RebarDesignPanel.tsx").read_text(encoding="utf-8")
    assert "recalculate: true" in workspace
    assert "calculationAutoHealing" in panel
    assert "处理中间状态，不代表最终阻断" in panel
    assert "for (let attempt = 0; attempt < 6" in panel
