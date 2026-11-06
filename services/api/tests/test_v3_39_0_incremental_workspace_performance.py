from __future__ import annotations

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.design_workspace_bootstrap import build_design_workspace_bootstrap, invalidate_design_workspace_bootstrap
from app.services.excavation_service import make_excavation_model
from app.storage.artifact_store import ProjectArtifactStore
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


def _project() -> Project:
    excavation = make_excavation_model(
        "bootstrap",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=80, y=0),
                Point2D(x=80, y=26), Point2D(x=0, y=26),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    return Project(name="bootstrap", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def test_design_bootstrap_is_single_flight_and_reuses_workspace_model(tmp_path, monkeypatch) -> None:
    import app.services.design_workspace_bootstrap as bootstrap
    import app.services.design_qualification as qualification

    store = SQLiteProjectStore(tmp_path / "bootstrap.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    repo = ProjectRepository(store=store)
    calls = {"shape": 0}
    real = bootstrap.plan_shape_diagnostics

    def counted(*args, **kwargs):
        calls["shape"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "plan_shape_diagnostics", counted)
    monkeypatch.setattr(
        qualification,
        "evaluate_support_layout_quality",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full quality evaluation must be deferred")),
    )
    invalidate_design_workspace_bootstrap(project.id, db_path=str(store.db_path))
    first = build_design_workspace_bootstrap(repo, project.id, force=True)
    second = build_design_workspace_bootstrap(repo, project.id)

    assert calls["shape"] == 1
    assert first["qualification"]["projectId"] == project.id
    assert first["progressive"]["projectId"] == project.id
    assert first["performance"]["topologyEvaluation"] == "summary_on_open_full_on_demand"
    assert second["cache"]["hit"] is True


def test_artifact_manifest_never_parses_full_project_snapshot(tmp_path, monkeypatch) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(artifact_root))
    store = SQLiteProjectStore(tmp_path / "artifacts.sqlite3")
    project = _project().model_dump(mode="json", by_alias=True)
    ref = ProjectArtifactStore(artifact_root).write_json(project["id"], "calculation-stage-results", [{"x": 1}])
    project.setdefault("advancedEngineering", {})["artifactStorage"] = {
        "schemaVersion": "1.0",
        "artifacts": [ref],
    }
    store.upsert(project)
    # If list_artifacts touches the full snapshot this invalid JSON will fail.
    with store._connect() as conn:
        conn.execute("UPDATE projects SET data=? WHERE id=?", ("{invalid-large-snapshot", project["id"]))
        conn.commit()

    rows = store.list_artifacts(project["id"])
    assert len(rows) == 1
    assert rows[0]["kind"] == "calculation-stage-results"
    assert rows[0]["available"] is True


def test_workspace_metadata_cache_invalidates_on_revision_change(tmp_path) -> None:
    store = SQLiteProjectStore(tmp_path / "workspace-cache.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    repo = ProjectRepository(store=store)
    first = repo.require_workspace(project.id)
    second = repo.require_workspace(project.id)
    assert first is second

    project.name = "updated"
    store.upsert(project.model_dump(mode="json", by_alias=True))
    third = repo.require_workspace(project.id)
    assert third is not first
    assert third.name == "updated"


def test_workspace_bootstrap_endpoint_serves_legacy_panels_from_one_snapshot(tmp_path) -> None:
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage.repository import get_repository

    store = SQLiteProjectStore(tmp_path / "endpoint.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    repo = ProjectRepository(store=store)
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            bootstrap = client.get(f"/api/projects/{project.id}/design/workspace-bootstrap")
            qualification = client.get(f"/api/projects/{project.id}/design/qualification")
            progressive = client.get(f"/api/projects/{project.id}/design/progressive")
        assert bootstrap.status_code == 200
        assert qualification.status_code == 200
        assert progressive.status_code == 200
        assert bootstrap.json()["qualification"]["projectId"] == project.id
        assert bootstrap.json()["progressive"]["projectId"] == project.id
    finally:
        app.dependency_overrides.pop(get_repository, None)
