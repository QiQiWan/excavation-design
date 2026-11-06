from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.domain import Project
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def test_auth_bootstrap_combines_policy_and_identity(monkeypatch) -> None:
    monkeypatch.delenv("PITGUARD_USERS", raising=False)
    monkeypatch.delenv("PITGUARD_API_KEYS", raising=False)
    client = TestClient(app)
    response = client.get("/api/auth/bootstrap")
    assert response.status_code == 200
    payload = response.json()
    assert payload["loginRequired"] is False
    assert payload["authenticated"] is True
    assert payload["identity"]["role"] == "admin"
    assert "server-timing" in response.headers
    assert "x-pitguard-duration-ms" in response.headers


def test_workspace_patch_returns_bounded_workspace_without_second_get(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "pitguard.sqlite3"
    monkeypatch.setenv("PITGUARD_DB_PATH", str(db))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    SQLiteProjectStore._initialized_paths.discard(str(db.resolve()))
    store = SQLiteProjectStore(db)
    project = Project(name="fast-save")
    ProjectRepository(store).create(project)

    # Override the shared store created by the application process.
    from app.storage import repository as repository_module
    repository_module.shared_project_store.cache_clear()
    client = TestClient(app)
    response = client.patch(
        f"/api/projects/{project.id}/workspace?actor=tester",
        json={"location": "Nanchang"},
    )
    assert response.status_code == 200
    assert response.headers["x-pitguard-project-profile"] == "workspace"
    assert response.json()["location"] == "Nanchang"


def test_store_schema_initialization_runs_once_per_process_path(tmp_path: Path, monkeypatch) -> None:
    db = (tmp_path / "once.sqlite3").resolve()
    SQLiteProjectStore._initialized_paths.discard(str(db))
    calls = 0
    original = SQLiteProjectStore._ensure_schema

    def counted(self: SQLiteProjectStore) -> None:
        nonlocal calls
        calls += 1
        original(self)

    monkeypatch.setattr(SQLiteProjectStore, "_ensure_schema", counted)
    SQLiteProjectStore(db)
    SQLiteProjectStore(db)
    SQLiteProjectStore(db)
    assert calls == 1


def test_project_summary_query_does_not_parse_full_payload(tmp_path: Path) -> None:
    db = tmp_path / "summary.sqlite3"
    SQLiteProjectStore._initialized_paths.discard(str(db.resolve()))
    store = SQLiteProjectStore(db)
    project = Project(name="summary-fast")
    store.upsert(project.model_dump(mode="json", by_alias=True))
    with store._connect() as conn:
        conn.execute("UPDATE projects SET data = ? WHERE id = ?", (json.dumps({"large": "x" * 2_000_000}), project.id))
        conn.commit()
    rows = store.list_summaries()
    assert rows[0]["name"] == "summary-fast"
