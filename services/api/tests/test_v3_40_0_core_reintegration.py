from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.calculation_trace import _entry
from app.services.core_workspace import build_core_workspace_status
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository, get_repository


def _project() -> Project:
    excavation = make_excavation_model(
        "core",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=60, y=0),
                Point2D(x=60, y=24), Point2D(x=0, y=24),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    return Project(name="core", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def test_calculation_trace_accepts_structured_rule_values() -> None:
    row = _entry(
        index=1,
        category="test",
        title="structured",
        object_type="wall",
        object_id="W1",
        stage_id=None,
        stage_name="global",
        demand_name="demand",
        demand_value={"value": 12.5, "source": "envelope"},
        capacity_value={"limitValue": 25.0},
    )
    assert row["demandValue"] == 12.5
    assert row["capacityValue"] == 25.0
    assert row["utilization"] == 0.5
    assert row["demandEvidence"]["source"] == "envelope"


def test_core_status_exposes_six_design_stages_with_basis_first() -> None:
    project = _project()
    result = build_core_workspace_status(project, {"workspaceBytes": 1024})
    assert [row["key"] for row in result["stages"]] == [
        "basis", "input", "scheme", "calculation", "reinforcement", "deliverables"
    ]
    assert result["mode"] == "core"
    assert result["storage"]["workspaceBytes"] == 1024


def test_progressive_stale_patch_is_rebased_instead_of_returning_409(tmp_path) -> None:
    store = SQLiteProjectStore(tmp_path / "core.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    repo = ProjectRepository(store)
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            first = client.put(
                f"/api/projects/{project.id}/design/progressive",
                json={"decisions": {"constructionMethod": "bottom_up"}},
            )
            assert first.status_code == 200
            stale_version = first.json()["config"]["sessionVersion"]
            second = client.put(
                f"/api/projects/{project.id}/design/progressive",
                json={"expectedVersion": stale_version, "constraints": {"maximumCandidateCount": 3}},
            )
            assert second.status_code == 200
            third = client.put(
                f"/api/projects/{project.id}/design/progressive",
                json={"expectedVersion": stale_version, "decisions": {"supportSystem": "direct_grid"}},
            )
            assert third.status_code == 200
            assert third.json()["conflictResolved"] is True
    finally:
        app.dependency_overrides.pop(get_repository, None)


def test_core_status_endpoint_uses_workspace_projection(tmp_path) -> None:
    store = SQLiteProjectStore(tmp_path / "status.sqlite3")
    project = _project()
    store.upsert(project.model_dump(mode="json", by_alias=True))
    repo = ProjectRepository(store)
    app.dependency_overrides[get_repository] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/projects/{project.id}/design/core-status")
        assert response.status_code == 200
        assert response.json()["mode"] == "core"
    finally:
        app.dependency_overrides.pop(get_repository, None)


def test_default_core_product_mode_does_not_register_extension_routes() -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PITGUARD_PRODUCT_MODE"] = "core"
    env["PYTHONPATH"] = str(api_dir)
    code = (
        "from app.main import app; "
        "paths={getattr(r,'path','') for r in app.routes}; "
        "assert '/api/documentation' in paths; "
        "assert not any('/advanced/' in p for p in paths); "
        "assert not any('/industrial/' in p for p in paths); "
        "print(len([p for p in paths if p.startswith('/api/')]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=api_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) <= 105
