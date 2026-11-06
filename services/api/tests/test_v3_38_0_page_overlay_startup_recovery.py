from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.domain import ProjectSummary
from app.storage.database import SQLiteProjectStore


def test_workspace_only_summary_is_valid_and_does_not_break_project_list(tmp_path, monkeypatch) -> None:
    import app.storage.database as database

    store = SQLiteProjectStore(tmp_path / "startup.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    store.upsert({
        "id": "large-project",
        "name": "Large project",
        "createdAt": now,
        "updatedAt": now,
        "calculationResults": [],
        "advancedEngineering": {},
    })
    with store._connect() as conn:
        conn.execute(
            "UPDATE projects SET payload_bytes=?, workspace_bytes=? WHERE id=?",
            (510 * 1024**2, 8 * 1024**2, "large-project"),
        )
        conn.commit()

    monkeypatch.setattr(database, "adaptive_resource_policy", lambda role=None: {
        "apiFullLoadLimitBytes": 96 * 1024**2,
        "workspaceLimitBytes": 32 * 1024**2,
    })
    rows = store.list_summaries()
    assert rows[0]["storage_status"] == "workspace_only"
    summary = ProjectSummary.model_validate(rows[0])
    assert summary.storage_status == "workspace_only"
    assert summary.payload_bytes == 510 * 1024**2


def test_project_summary_normalizes_legacy_and_unknown_storage_status() -> None:
    base = {
        "id": "p1",
        "name": "P1",
        "updatedAt": "2026-07-16T00:00:00Z",
    }
    assert ProjectSummary.model_validate({**base, "storageStatus": "workspace-only"}).storage_status == "workspace_only"
    assert ProjectSummary.model_validate({**base, "storageStatus": "future-policy-state"}).storage_status == "elevated"


def test_projects_endpoint_starts_with_workspace_only_project(tmp_path, monkeypatch) -> None:
    import app.storage.database as database
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage.repository import ProjectRepository, get_repository

    store = SQLiteProjectStore(tmp_path / "endpoint.sqlite3")
    now = datetime.now(timezone.utc).isoformat()
    store.upsert({"id": "p-large", "name": "Large", "createdAt": now, "updatedAt": now})
    with store._connect() as conn:
        conn.execute("UPDATE projects SET payload_bytes=? WHERE id=?", (510 * 1024**2, "p-large"))
        conn.commit()
    monkeypatch.setattr(database, "adaptive_resource_policy", lambda role=None: {
        "apiFullLoadLimitBytes": 96 * 1024**2,
        "workspaceLimitBytes": 32 * 1024**2,
    })
    repo = ProjectRepository(store=store)
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.get("/api/projects")
        assert response.status_code == 200
        assert response.json()[0]["storageStatus"] == "workspace_only"
    finally:
        app.dependency_overrides.pop(get_repository, None)
