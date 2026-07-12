from __future__ import annotations

import os
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.calculation.engine import run_calculation
from app.drawing_rules import build_drawing_plan, get_effective_drawing_rule_set
from app.drawings.cad_export import export_construction_cad_package
from app.schemas.domain import Project
from app.main import app
from app.rules.gb50010.reinforcement_rules import recommend_bar_spacing
from app.services.support_layout import unrestrained_concave_face_codes

ROOT = Path(__file__).resolve().parents[3]
SAMPLE_CSV = ROOT / "packages/sample-data/boreholes/sample_boreholes.csv"


@pytest.fixture(scope="module")
def lshape_result(tmp_path_factory: pytest.TempPathFactory):
    db = tmp_path_factory.mktemp("v35-lshape") / "pitguard.sqlite3"
    os.environ["PITGUARD_DB_PATH"] = str(db)
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "V3.5 L-shaped calculation recovery", "location": "regression"}).json()
        project_id = project["id"]
        with SAMPLE_CSV.open("rb") as handle:
            imported = client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (SAMPLE_CSV.name, handle, "text/csv")})
        assert imported.status_code == 200, imported.text
        assert client.post(f"/api/projects/{project_id}/geology/build-model").status_code == 200
        excavation = {
            "name": "L-shaped pit",
            "topElevation": 0,
            "bottomElevation": -12,
            "outline": {"closed": True, "points": [
                {"x": 75, "y": 85}, {"x": 125, "y": 85}, {"x": 125, "y": 115},
                {"x": 100, "y": 115}, {"x": 100, "y": 100}, {"x": 75, "y": 100},
            ]},
        }
        assert client.post(f"/api/projects/{project_id}/excavation", json=excavation).status_code == 200
        assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
        supports = client.post(f"/api/projects/{project_id}/design/auto-supports")
        assert supports.status_code == 200, supports.text
        assert client.post(f"/api/projects/{project_id}/calculation/build-cases").status_code == 200
        calculation = client.post(f"/api/projects/{project_id}/calculation/run")
        assert calculation.status_code == 200, calculation.text
        stored = client.get(f"/api/projects/{project_id}").json()
        yield client, project_id, supports.json(), calculation.json(), stored


def test_v3_5_lshape_generates_direct_return_wall_supports(lshape_result) -> None:
    _client, _project_id, retaining, _calculation, _stored = lshape_result
    secondary = [item for item in retaining["supports"] if item["supportRole"] == "secondary_strut"]
    assert len(secondary) >= 2
    direct_faces = {face for item in secondary for face in (item.get("startFaceCode"), item.get("endFaceCode")) if face}
    assert "S4" in direct_faces
    assert len(retaining["columns"]) >= 1


def test_v3_5_lshape_calculation_has_no_hard_failure_and_compact_issue_register(lshape_result) -> None:
    _client, _project_id, _retaining, calculation, _stored = lshape_result
    assert calculation["checkSummary"]["fail"] == 0
    assert calculation["checkSummary"]["warning"] < 20
    assert calculation["governingValues"]["maxDisplacement"] < 10.0
    diagnostics = calculation["designIterationSummary"]["calculationDiagnostics"]
    assert diagnostics["status"] in {"pass", "warning"}
    s4 = next(item for item in diagnostics["wallCoverage"] if item["segmentId"] == "S4")
    assert s4["directSupportCount"] >= 1
    assert s4["supportCoverageStatus"] == "pass"




def test_v3_5_existing_v34_lshape_is_repaired_before_calculation(lshape_result) -> None:
    _client, _project_id, _retaining, _calculation, stored = lshape_result
    project = Project.model_validate(stored).model_copy(deep=True)
    old_count = len(project.retaining_system.supports)
    project.retaining_system.supports = [item for item in project.retaining_system.supports if item.support_role != "secondary_strut"]
    project.retaining_system.columns = []
    project.retaining_system.support_nodes = []
    assert "S4" in unrestrained_concave_face_codes(project.excavation, project.retaining_system.supports)
    result = run_calculation(project, project.calculation_cases[-1], auto_repair=True)
    diagnostics = result.design_iteration_summary["calculationDiagnostics"]
    assert diagnostics["topologyPreflight"]["changed"] is True
    assert diagnostics["topologyPreflight"]["addedSupportCount"] >= 3
    assert diagnostics["supportTopologySynchronization"]["synchronized"] is True
    assert result.check_summary["fail"] == 0
    assert len(project.retaining_system.supports) >= old_count


def test_v3_5_diagnose_and_repair_endpoint_is_idempotent(lshape_result) -> None:
    client, project_id, _retaining, _calculation, _stored = lshape_result
    response = client.post(f"/api/projects/{project_id}/calculation/diagnose-and-repair")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["checkSummary"]["fail"] == 0
    assert payload["diagnostics"]["topologyPreflight"]["changed"] is False
    assert payload["diagnostics"]["supportTopologySynchronization"]["reason"] == "topology_current"


def test_v3_5_drawing_intelligence_adds_concave_detail_and_quality_score(lshape_result) -> None:
    client, project_id, _retaining, _calculation, _stored = lshape_result
    preview = client.get(f"/api/projects/{project_id}/drawing-rules/preview").json()
    assert any((item.get("ruleId") or item.get("id")) == "D09" for item in preview["sheets"])
    intelligence = preview["drawingIntelligence"]
    assert intelligence["facts"]["concaveVertexCount"] == 1
    assert any(item["id"] == "CONCAVE_RETURN_SUPPORT_DETAIL" and item["satisfied"] for item in intelligence["recommendations"])
    assert intelligence["quality"]["overall"] >= 80




def test_v3_5_cad_package_contains_concave_detail_diagnostics_and_intelligence(lshape_result, tmp_path: Path) -> None:
    _client, _project_id, _retaining, _calculation, stored = lshape_result
    project = Project.model_validate(stored)
    package = export_construction_cad_package(project, tmp_path, scope="full", rebar_mode="balanced", issue_mode="review")
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert "40_details/D-09_concave_return_wall_support_detail.dxf" in names
        assert "90_schedules/calculation_diagnostics.json" in names
        assert "90_schedules/calculation_diagnostics.csv" in names
        assert "90_schedules/drawing_intelligence.json" in names
        intelligence = json.loads(archive.read("90_schedules/drawing_intelligence.json"))
        assert intelligence["facts"]["concaveVertexCount"] == 1
        assert any(item["id"] == "CONCAVE_RETURN_SUPPORT_DETAIL" for item in intelligence["recommendations"])


def test_v3_5_bar_recommendation_does_not_violate_minimum_clear_spacing() -> None:
    diameter, spacing, _provided = recommend_bar_spacing(12_000.0, preferred_diameters=(28, 32, 36, 40))
    diameter = float(diameter)
    spacing = float(spacing)
    assert spacing - diameter >= 75.0
