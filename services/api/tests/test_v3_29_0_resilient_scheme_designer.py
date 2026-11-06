from __future__ import annotations

from pathlib import Path

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.calculation_resource_estimator import estimate_calculation_resources
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.services.support_scheme_designer_audit import audit_support_scheme_designer
from app.storage.database import SQLiteProjectStore
from app.tasks.manager import TaskRecord
from app.version import SOFTWARE_VERSION


def _project() -> Project:
    outline = Polyline2D(
        points=[Point2D(x=0, y=0), Point2D(x=80, y=0), Point2D(x=80, y=22), Point2D(x=0, y=22)],
        closed=True,
    )
    excavation = make_excavation_model("v329", outline, 0.0, -15.0)
    retaining = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation),
        SupportLayoutConfig(topology_strategy="direct_grid"),
    )
    return Project(name="v329", excavation=excavation, retainingSystem=retaining)


def test_scheme_designer_audit_covers_full_decision_chain() -> None:
    project = _project()
    audit = audit_support_scheme_designer(project)
    section_ids = {item["id"] for item in audit["sections"]}
    assert {"shape", "system", "topology", "candidates", "staging", "runtime"} <= section_ids
    assert audit["resourceEstimate"]["calculationAllowed"] is True
    assert "modelCompatibility" in audit
    assert len(audit["workflow"]) >= 8


def test_resource_estimator_returns_a_bounded_preflight_contract() -> None:
    estimate = estimate_calculation_resources(_project(), candidate_count=3)
    assert estimate["estimatedPeakMemoryMb"] > 0
    assert estimate["workerMemoryMaxMb"] >= 2048
    assert estimate["status"] in {"normal", "elevated", "high", "blocked"}
    assert isinstance(estimate["recommendations"], list)


def test_project_list_uses_persisted_lightweight_summary(tmp_path: Path) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = _project().model_dump(mode="json", by_alias=True)
    store.upsert(project)
    summaries = store.list_summaries()
    assert summaries[0]["name"] == "v329"
    assert summaries[0]["has_excavation"] is True
    assert summaries[0]["has_retaining_system"] is True


def test_task_list_can_omit_large_result_payload() -> None:
    record = TaskRecord(id="task-1", project_id="p-1", operation="calculation_full", title="calc", result={"rows": list(range(1000))})
    assert record.as_dict(include_result=False)["result"] is None
    assert record.as_dict(include_result=True)["result"]["rows"][-1] == 999


def test_production_resilience_hooks_are_installed() -> None:
    root = Path(__file__).resolve().parents[3]
    manager = (root / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    deploy = (root / "scripts/build-and-start-production.sh").read_text(encoding="utf-8")
    app = (root / "apps/web/src/app/App.tsx").read_text(encoding="utf-8")
    workspace = (root / "apps/web/src/pages/ProjectWorkspace.tsx").read_text(encoding="utf-8")
    assert "_start_resource_watchdog" in manager
    assert "PITGUARD_WORKER_RSS_HARD_LIMIT_MB" in deploy
    assert "location = /api/auth/status" in deploy
    assert "authRetryNonce" in app
    assert "布设方案设计器完整性审计" in workspace
    assert "pitguard-active-task" in workspace
    assert "interrupted" in workspace


def test_v329_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 29, 0)
