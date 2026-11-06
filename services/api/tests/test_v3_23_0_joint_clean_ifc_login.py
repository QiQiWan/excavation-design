from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.drawings.cad_export import export_construction_cad_package
from app.ifc.exporter import export_simplified_ifc
from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.main import app
from app.quality.ifc_compatibility import validate_ifc_file
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import (
    MaterialDefinition,
    Point2D,
    Polyline2D,
    Project,
    RetainingSystem,
    SectionDefinition,
    SupportElement,
)
from app.services.access_control import hash_password
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.integrated_retaining_optimizer import build_integrated_retaining_candidates
from app.services.rebar_detailing import build_rebar_detailing
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.version import SOFTWARE_VERSION


def _excavation_project(name: str = "v323") -> Project:
    excavation = make_excavation_model(
        name,
        Polyline2D(
            points=[
                Point2D(x=0.0, y=0.0),
                Point2D(x=36.0, y=0.0),
                Point2D(x=36.0, y=16.0),
                Point2D(x=0.0, y=16.0),
            ],
            closed=True,
        ),
        0.0,
        -14.0,
        0.5,
    )
    project = Project(name=name, excavation=excavation)
    project.retaining_system = auto_supports(
        excavation,
        auto_diaphragm_wall(excavation, settings=project.design_settings),
    )
    return project


def _support(
    code: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    start_face: str | None = None,
    end_face: str | None = None,
) -> SupportElement:
    return SupportElement(
        code=code,
        levelIndex=1,
        elevation=-4.0,
        start=Point2D(x=start[0], y=start[1]),
        end=Point2D(x=end[0], y=end[1]),
        startFaceCode=start_face,
        endFaceCode=end_face,
        supportRole="main_strut",
        spanLength=((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5,
        sectionType="rc_rectangular",
        section=SectionDefinition(width=1.0, height=1.0, name="1000x1000 RC"),
        material=MaterialDefinition(name="Concrete", grade="C40"),
    )


def test_wall_endpoint_connections_are_recorded_and_shared_nodes_are_penalized() -> None:
    project = _excavation_project("wall-junction")
    first = _support("SP-A", (0.0, 8.0), (36.0, 8.0), start_face="S4", end_face="S2")
    second = _support("SP-B", (0.0, 8.0), (18.0, 16.0), start_face="S4", end_face="S3")
    project.retaining_system = RetainingSystem(supports=[first, second], columns=[])

    quality = evaluate_support_layout_quality(project)

    assert quality.metrics["supportCrossingCount"] == 0
    assert quality.metrics["wallConnectionPointCount"] == 3
    assert quality.metrics["wallJunctionCount"] == 1
    assert quality.metrics["highDegreeWallJunctionCount"] == 0
    assert quality.metrics["totalJunctionCount"] >= 1
    assert quality.metrics["planIntersectionComplexity"] >= 1.5
    wall_node = quality.metrics["wallJunctionPoints"][0]
    assert wall_node["nodeType"] == "wall"
    assert wall_node["supportCodes"] == ["SP-A", "SP-B"]


def test_three_supports_converging_on_wall_are_high_degree_junction() -> None:
    project = _excavation_project("wall-junction-high")
    supports = [
        _support("SP-A", (0.0, 8.0), (36.0, 8.0), start_face="S4", end_face="S2"),
        _support("SP-B", (0.0, 8.0), (18.0, 16.0), start_face="S4", end_face="S3"),
        _support("SP-C", (0.0, 8.0), (18.0, 0.0), start_face="S4", end_face="S1"),
    ]
    project.retaining_system = RetainingSystem(supports=supports, columns=[])

    quality = evaluate_support_layout_quality(project)

    assert quality.metrics["wallJunctionCount"] == 1
    assert quality.metrics["highDegreeWallJunctionCount"] == 1
    assert quality.metrics["maxWallJunctionBranchDegree"] == 3
    assert quality.metrics["planIntersectionComplexity"] >= 4.5


def test_integrated_candidates_include_wall_plan_and_vertical_lengths_as_variables() -> None:
    project = _excavation_project("joint-optimizer")
    payload = build_integrated_retaining_candidates(project, mode="balanced", max_candidates=8)

    assert payload["summary"]["wallDesignLengthIncludedAsVariable"] is True
    assert payload["summary"]["wallEndpointJunctionIncludedInObjective"] is True
    assert payload["primaryObjectiveOrder"][1:4] == [
        "minimum_illegal_support_crossings",
        "minimum_high_degree_wall_junctions",
        "minimum_wall_junctions",
    ]
    assert payload["candidates"]
    assert len(payload["candidates"]) <= 8
    assert len({row["wallPlanSchemeId"] for row in payload["candidates"]}) >= 3
    assert any(row["wallPlanSchemeId"] != "WLP-KEEP" for row in payload["candidates"])
    for row in payload["candidates"]:
        variables = row["designVariables"]
        assert variables["wallPlanDesignLengthVariable"] is True
        assert variables["wallVerticalLengthVariable"] is True
        assert "wallToeZones" in variables


def test_detailed_ifc_panels_rebar_cages_and_manifest_share_codes(tmp_path: Path) -> None:
    project = _excavation_project("ifc-trace")
    project.retaining_system.rebar_design_scheme = build_rebar_design_scheme(project, mode="balanced")

    path = export_simplified_ifc(project, tmp_path, "design_detailed")
    check = validate_ifc_file(path)
    manifest = json.loads(path.with_suffix(".ifc_manifest.json").read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    visualization = build_rebar_ifc_visualization(project, max_bars=300)
    detailing = build_rebar_detailing(project)

    panel_codes = {
        str(panel["panelCode"])
        for wall in project.retaining_system.diaphragm_walls
        for panel in wall.construction_panels
    }
    manifest_panel_codes = {str(row["panelCode"]) for row in manifest["constructionPanels"]}
    cage_panel_codes = {str(row["constructionPanelCode"]) for row in detailing["cageSegments"]}
    visual_panel_codes = {str(row["panelCode"]) for row in visualization["cages"]}

    assert check.status == "pass"
    assert check.score == 100.0
    assert text.count("IFCELEMENTASSEMBLY(") == len(project.retaining_system.diaphragm_walls)
    assert text.count("IFCRELAGGREGATES(") >= len(project.retaining_system.diaphragm_walls) + 3
    assert text.count("IFCGROUP(") == len(panel_codes)
    assert text.count("IFCRELASSIGNSTOGROUP(") == len(panel_codes)
    assert text.count("IFCRELCONNECTSELEMENTS(") == len(panel_codes) - len(project.retaining_system.diaphragm_walls)
    assert manifest["rebarCageGroupCount"] == len(panel_codes)
    assert manifest["constructionJointCount"] == len(panel_codes) - len(project.retaining_system.diaphragm_walls)
    assert panel_codes == manifest_panel_codes == cage_panel_codes == visual_panel_codes
    assert manifest["optimizationTrace"]["wallPlanDesignLengthIsVariable"] is True
    assert manifest["optimizationTrace"]["wallEndpointJunctionIncludedInObjective"] is True
    cage = visualization["cages"][0]
    assert cage["representation"] == "construction_panel_rebar_cage_grid_with_joints_lifting_and_splice_zones"
    assert cage["jointMarkers"]
    assert cage["liftingPoints"]
    assert cage["spliceZones"]


def test_drawing_package_exports_support_and_wall_panel_traceability(tmp_path: Path) -> None:
    project = _excavation_project("drawing-trace")
    project.retaining_system.rebar_design_scheme = build_rebar_design_scheme(project, mode="balanced")

    archive = export_construction_cad_package(
        project, tmp_path, scope="details", rebar_mode="balanced", issue_mode="review"
    )
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert "90_schedules/support_junction_schedule.csv" in names
        assert "90_schedules/wall_panel_cage_traceability.csv" in names
        assert "90_schedules/cross_artifact_traceability.json" in names
        trace = json.loads(package.read("90_schedules/cross_artifact_traceability.json"))
        assert trace["optimizationObjectives"]["wallEndpointJunctionsIncluded"] is True
        assert trace["optimizationObjectives"]["wallPlanDesignLengthVariable"] is True
        assert trace["wallPanelCageTraceability"]["panelCodesMissingFromCageVisualization"] == []
        assert trace["status"] == "pass"


def test_application_login_uses_http_only_session_and_role_guard(monkeypatch) -> None:
    password = "PitGuard-test-password-2026"
    users = {
        "engineer": {
            "passwordHash": hash_password(password, iterations=20_000, salt=b"v323-test-login-salt"),
            "role": "designer",
            "actor": "test-engineer",
            "userId": "user-test-1",
        }
    }
    monkeypatch.setenv("PITGUARD_USERS", json.dumps(users))
    monkeypatch.setenv("PITGUARD_SESSION_SECRET", "v323-test-session-secret-that-is-long-enough")
    monkeypatch.setenv("PITGUARD_COOKIE_SECURE", "false")
    monkeypatch.delenv("PITGUARD_API_KEYS", raising=False)

    with TestClient(app) as client:
        status = client.get("/api/auth/status")
        assert status.status_code == 200
        assert status.json()["loginRequired"] is True
        assert status.headers.get("cache-control") == "no-store"
        assert client.get("/api/system/diagnostics").status_code == 401
        assert client.get("/docs").status_code == 401
        assert client.get("/openapi.json").status_code == 401
        assert client.get("/api/system/readiness").status_code == 401
        failed = client.post("/api/auth/login", json={"username": "engineer", "password": "wrong"})
        assert failed.status_code == 401
        logged_in = client.post("/api/auth/login", json={"username": "engineer", "password": password})
        assert logged_in.status_code == 200
        assert logged_in.headers.get("cache-control") == "no-store"
        cookie = logged_in.headers.get("set-cookie", "")
        assert "pitguard_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        identity = client.get("/api/auth/me")
        assert identity.status_code == 200
        assert identity.headers.get("cache-control") == "no-store"
        assert identity.json()["identity"]["role"] == "designer"
        assert client.get("/api/system/diagnostics").status_code == 200
        assert client.get("/docs").status_code == 200
        openapi_response = client.get("/openapi.json")
        assert openapi_response.status_code == 200
        operation_ids = [
            operation["operationId"]
            for path_item in openapi_response.json()["paths"].values()
            for operation in path_item.values()
            if isinstance(operation, dict) and "operationId" in operation
        ]
        assert len(operation_ids) == len(set(operation_ids))
        assert client.get("/api/system/readiness").status_code == 200
        assert client.post("/api/system/backup").status_code == 403
        logged_out = client.post("/api/auth/logout")
        assert logged_out.status_code == 200
        assert logged_out.headers.get("cache-control") == "no-store"
        assert client.get("/api/system/diagnostics").status_code == 401


def test_v323_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 23, 0)
