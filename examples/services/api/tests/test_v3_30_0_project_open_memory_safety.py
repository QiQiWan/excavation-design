from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers.projects import get_project
from app.schemas.domain import Project
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def _project() -> Project:
    project = Project(name="Large opening safety")
    project.advanced_engineering["industrialDetailing"] = {"blob": "x" * 2_000_000}
    project.advanced_engineering["latestSuite"] = {"blob": "y" * 1_000_000}
    project.messages = [f"message-{i}" for i in range(140)]
    return project


def test_workspace_projection_is_bounded_and_returned_without_full_model_load(tmp_path: Path) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    full_info = store.get_payload_info(project.id)
    assert full_info is not None
    assert full_info["payloadBytes"] > 2_500_000
    assert full_info["workspaceBytes"] < full_info["payloadBytes"] / 20

    repo = ProjectRepository(store)
    response = get_project(project.id, profile="workspace", result_history_limit=1, repo=repo)
    payload = json.loads(response.body)
    assert payload["id"] == project.id
    assert "industrialDetailing" not in payload["advancedEngineering"]
    assert len(payload["messages"]) == 100
    assert response.headers["x-pitguard-project-profile"] == "workspace"


def test_api_full_load_guard_blocks_before_reading_large_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = Project(name="Guarded")
    store.upsert(project.model_dump(mode="json", by_alias=True))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE projects SET payload_bytes = ? WHERE id = ?", (200 * 1024 * 1024, project.id))
        conn.commit()
    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "api")
    monkeypatch.setenv("PITGUARD_API_FULL_PROJECT_LIMIT_MB", "96")
    repo = ProjectRepository(store)
    with pytest.raises(HTTPException) as exc:
        repo.require(project.id)
    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "PROJECT_FULL_LOAD_BLOCKED"
    assert repo.require_workspace(project.id).id == project.id


def test_worker_role_can_load_full_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = Project(name="Worker")
    store.upsert(project.model_dump(mode="json", by_alias=True))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE projects SET payload_bytes = ? WHERE id = ?", (200 * 1024 * 1024, project.id))
        conn.commit()
    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "worker")
    assert ProjectRepository(store).require(project.id).name == "Worker"


def test_opening_project_is_read_only_and_does_not_create_revision(tmp_path: Path) -> None:
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    before = store.get_revision_number(project.id)
    repo = ProjectRepository(store)
    repo.require_workspace(project.id)
    get_project(project.id, profile="workspace", result_history_limit=1, repo=repo)
    after = store.get_revision_number(project.id)
    assert before == after


def test_legacy_database_backfills_workspace_in_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite3"
    project = Project(name="Legacy")
    data = project.model_dump(mode="json", by_alias=True)
    data["calculationResults"] = [{"id": "calc", "stageResults": [{"blob": "z" * 500_000}]}]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "revision INTEGER NOT NULL DEFAULT 0, content_hash TEXT NOT NULL DEFAULT '', "
            "summary TEXT NOT NULL DEFAULT '{}', data TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO projects(id,name,updated_at,revision,content_hash,summary,data) VALUES(?,?,?,?,?,?,?)",
            (project.id, project.name, project.updated_at, 1, "", "{}", json.dumps(data)),
        )
        conn.commit()
    store = SQLiteProjectStore(db)
    workspace, metadata = store.get_workspace_json(project.id) or ("", {})
    parsed = json.loads(workspace)
    assert parsed["id"] == project.id
    assert parsed["calculationResults"] == []
    assert metadata["workspaceBytes"] < metadata["payloadBytes"]


def test_workspace_hard_limit_removes_large_candidate_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITGUARD_WORKSPACE_PAYLOAD_LIMIT_MB", "4")
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    project = Project(name="Bounded workspace")
    raw = project.model_dump(mode="json", by_alias=True)
    raw["retainingSystem"] = {
        "id": "ret-1",
        "type": "diaphragm_wall_with_internal_bracing",
        "supportLayoutRepair": {
            "candidates": [{
                "id": "candidate-1",
                "planGeometry": {"blob": "p" * 5_000_000},
                "deltaGeometry": {"blob": "d" * 1_000_000},
                "fullCalculation": {},
            }],
        },
    }
    store.upsert(raw)
    payload, metadata = store.get_workspace_json(project.id) or ("", {})
    parsed = json.loads(payload)
    candidate = parsed["retainingSystem"]["supportLayoutRepair"]["candidates"][0]
    assert candidate["planGeometry"] == {}
    assert candidate["deltaGeometry"] == {}
    assert metadata["workspaceBytes"] < 4 * 1024 * 1024


def test_production_deployment_prepares_workspace_before_api_start() -> None:
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts" / "build-and-start-production.sh").read_text(encoding="utf-8")
    assert "prepare-project-workspace-storage.py" in script
    assert "Environment=PITGUARD_PROCESS_ROLE=api" in script
    assert "Environment=PITGUARD_PROCESS_ROLE=worker" in script
    assert "PITGUARD_API_FULL_PROJECT_LIMIT_MB" in script
    assert "pre_v330_workspace_" in script
