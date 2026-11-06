from __future__ import annotations

from pathlib import Path

from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.borehole_import_workflow import execute_borehole_import
from app.services.calculation_blocker_recovery import apply_safe_input_recovery
from app.services.design_qualification import build_design_qualification
from app.services.excavation_service import make_excavation_model
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository


ROOT = Path(__file__).resolve().parents[3]


def test_frontend_keeps_project_route_and_uses_worker_import() -> None:
    app = (ROOT / "apps/web/src/app/App.tsx").read_text(encoding="utf-8")
    navigation = (ROOT / "apps/web/src/app/navigation.ts").read_text(encoding="utf-8")
    borehole = (ROOT / "apps/web/src/components/BoreholeImport.tsx").read_text(encoding="utf-8")
    workspace = (ROOT / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    design_basis = (ROOT / "apps/web/src/components/DesignBasisPanel.tsx").read_text(encoding="utf-8")
    manager = (ROOT / "services/api/app/tasks/manager.py").read_text(encoding="utf-8")
    assert "projectIdFromPath" in app and "projectPath(project.id)" in app
    assert "/projects/${encodeURIComponent(projectId)}" in navigation
    assert "importBoreholesTask" in borehole
    assert "waitForTaskWithHealth" in borehole
    assert "calculation_recovery" in workspace
    assert "calculation_closure_action" in workspace
    assert "forceClosure: true" in workspace
    assert "自动诊断、修复并复算" in workspace
    assert "window.scrollTo" in design_basis and "api.getProject(project.id)" in design_basis
    assert 'task.operation == "borehole_import"' in manager
    assert '"calculation_closure_action"' in manager
    assert "automaticInterventionsApplied" in manager


def test_safe_geometry_recovery_rebuilds_wall_map_and_baseline_supports() -> None:
    excavation = make_excavation_model(
        "pit",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=30, y=0),
                Point2D(x=30, y=18), Point2D(x=0, y=18),
            ],
            closed=True,
        ),
        0.0,
        -10.0,
    )
    project = Project(name="recovery", excavation=excavation)
    project.design_settings.design_basis_confirmed = True
    qualification = build_design_qualification(project, topology_detail="full")
    assert any(item.get("code") == "Q-GEOMETRY" and item.get("status") == "fail" for item in qualification["gates"])

    recovery = apply_safe_input_recovery(project, qualification)
    assert recovery["changed"] is True
    assert project.retaining_system is not None
    assert len(project.retaining_system.diaphragm_walls) == len(project.excavation.segments)
    assert len(project.retaining_system.supports) > 0


def test_borehole_import_workflow_updates_project_and_cleans_staging(tmp_path, monkeypatch) -> None:
    database = tmp_path / "pitguard.sqlite3"
    staging_root = tmp_path / "imports"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PITGUARD_DB_PATH", str(database))
    monkeypatch.setenv("PITGUARD_IMPORT_STAGING_ROOT", str(staging_root))
    monkeypatch.setenv("PITGUARD_ARTIFACT_ROOT", str(artifact_root))

    repo = ProjectRepository(SQLiteProjectStore(database))
    project = repo.create(Project(name="import"))
    staging_root.mkdir(parents=True, exist_ok=True)
    source = staging_root / "sample.csv"
    source.write_text(
        "borehole_code,x,y,collar_elevation,borehole_depth,layer_index,stratum_code,stratum_name,top_depth,bottom_depth,unit_weight,cohesion,friction_angle,elastic_modulus\n"
        "ZK1,0,0,10,20,1,S1,Fill,0,5,18,10,15,12\n"
        "ZK1,0,0,10,20,2,S2,Clay,5,20,19,25,18,20\n",
        encoding="utf-8",
    )
    import hashlib
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = execute_borehole_import(
        project.id,
        {
            "stagingPath": str(source),
            "originalFilename": "sample.csv",
            "contentType": "text/csv",
            "importType": "csv",
            "sha256": digest,
        },
        repo,
    )
    assert result["importResult"]["success"] is True
    assert result["refreshProject"] is True
    assert not source.exists()
    updated = repo.require(project.id)
    assert len(updated.boreholes) == 1
    assert len(updated.strata) == 2
    assert updated.geological_model is None
    assert updated.advanced_engineering["calculationState"]["status"] == "invalidated"
