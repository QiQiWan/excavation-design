from __future__ import annotations

import json
import os
from pathlib import Path

from app.routers.artifacts import download_project_artifact, list_calculation_stage_chunks, list_project_artifacts
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository
from app.version import SOFTWARE_VERSION


def _heavy_project() -> dict:
    return {
        "id": "project-v331",
        "name": "External dataset",
        "createdAt": "2026-07-15T00:00:00+00:00",
        "updatedAt": "2026-07-15T00:00:00+00:00",
        "advancedEngineering": {},
        "calculationResults": [{
            "id": "calc-v331",
            "projectId": "project-v331",
            "caseId": "case-1",
            "stageResults": [
                {"stageId": f"stage-{index}", "segmentId": "wall-1", "payload": "x" * 10000}
                for index in range(220)
            ],
            "governingValues": {},
            "professionalReviewRequired": True,
        }],
        "geologicalModel": {"vtuMesh": {"points": list(range(100000))}, "surfaces": [], "volumes": []},
        "retainingSystem": {},
        "monitoringRecords": [],
    }


def test_heavy_results_are_externalized_and_worker_rehydrates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "api")
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    store.upsert(_heavy_project())
    info = store.get_payload_info("project-v331") or {}
    assert info["payloadBytes"] < 100_000
    assert info["artifactCount"] >= 4
    assert info["externalBytes"] > info["payloadBytes"]
    workspace = store.get_workspace("project-v331") or {}
    assert workspace["calculationResults"][0]["stageResults"] == []
    assert workspace["geologicalModel"]["vtuMesh"] is None

    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "worker")
    full = store.get("project-v331") or {}
    assert len(full["calculationResults"][0]["stageResults"]) == 220
    assert len(full["geologicalModel"]["vtuMesh"]["points"]) == 100000


def test_compact_api_save_preserves_existing_artifact_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "api")
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    store.upsert(_heavy_project())
    before = {item["storageKey"] for item in store.list_artifacts("project-v331")}
    workspace = store.get_workspace("project-v331") or {}
    workspace["updatedAt"] = "2026-07-15T01:00:00+00:00"
    store.upsert(workspace)
    after = {item["storageKey"] for item in store.list_artifacts("project-v331")}
    assert before == after
    monkeypatch.setenv("PITGUARD_PROCESS_ROLE", "worker")
    assert len((store.get("project-v331") or {})["calculationResults"][0]["stageResults"]) == 220


def test_artifact_manifest_and_nginx_accel_download(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    store.upsert(_heavy_project())
    repo = ProjectRepository(store)
    manifest = list_project_artifacts("project-v331", kind=None, repo=repo)
    assert manifest["artifactCount"] >= 4
    artifact_id = manifest["artifacts"][0]["artifactId"]
    response = download_project_artifact("project-v331", artifact_id, repo=repo)
    assert response.headers["x-accel-redirect"].startswith("/protected-artifacts/")
    assert response.headers["content-encoding"] == "gzip"


def test_calculation_stage_chunks_are_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PITGUARD_STAGE_RESULT_CHUNK_SIZE", "100")
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    store.upsert(_heavy_project())
    payload = list_calculation_stage_chunks("project-v331", "calc-v331", repo=ProjectRepository(store))
    assert payload["chunkCount"] == 3
    assert payload["recordCount"] == 220


def test_project_delete_removes_external_objects(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard.sqlite3"))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    store = SQLiteProjectStore(tmp_path / "pitguard.sqlite3")
    store.upsert(_heavy_project())
    project_dir = tmp_path / "artifacts" / "project-v331"
    assert project_dir.exists()
    assert store.delete("project-v331") is True
    assert not project_dir.exists()


def test_v331_deployment_uses_internal_nginx_artifact_transfer() -> None:
    root = Path(__file__).resolve().parents[3]
    deploy = (root / "scripts" / "build-and-start-production.sh").read_text(encoding="utf-8")
    assert "prepare-project-artifact-storage.py" in deploy
    assert "location /protected-artifacts/" in deploy
    assert "internal;" in deploy
    assert "PITGUARD_ARTIFACT_ROOT" in deploy
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 31, 0)
