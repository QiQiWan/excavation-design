from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "services/api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.geology.idw import interpolate_surface_idw
from app.main import app
from app.version import SOFTWARE_VERSION
from app.rules.enterprise.preliminary_design_rules import select_support_count, select_wall_thickness
from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
from app.services.excavation_service import is_self_intersecting, validate_outline
from app.schemas.domain import Point2D, Polyline2D

SAMPLE_CSV = ROOT / "packages/sample-data/boreholes/sample_boreholes.csv"
INVALID_CSV = ROOT / "packages/sample-data/boreholes/invalid_boreholes.csv"
SAMPLE_VTU = ROOT / "packages/sample-data/vtu/sample.vtu"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PITGUARD_DB_PATH", str(tmp_path / "pitguard-test.sqlite3"))
    with TestClient(app) as test_client:
        yield test_client


def create_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"name": "Test Project", "location": "Test Site"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def import_sample_boreholes(client: TestClient, project_id: str):
    with SAMPLE_CSV.open("rb") as f:
        response = client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (SAMPLE_CSV.name, f, "text/csv")})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    return response.json()


def create_excavation(client: TestClient, project_id: str):
    payload = {
        "name": "Rect pit",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [{"x": 5, "y": 5}, {"x": 55, "y": 5}, {"x": 55, "y": 35}, {"x": 5, "y": 35}]},
    }
    response = client.post(f"/api/projects/{project_id}/excavation", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def full_design_workflow(client: TestClient) -> str:
    project_id = create_project(client)
    import_sample_boreholes(client, project_id)
    assert client.post(f"/api/projects/{project_id}/geology/build-model").status_code == 200
    create_excavation(client, project_id)
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
    assert client.post(f"/api/projects/{project_id}/calculation/build-cases").status_code == 200
    return project_id


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "pitguard-api"}


def test_project_crud(client):
    project_id = create_project(client)
    assert client.get(f"/api/projects/{project_id}").json()["name"] == "Test Project"
    updated = client.put(f"/api/projects/{project_id}", json={"name": "Updated"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated"
    assert client.delete(f"/api/projects/{project_id}").json()["deleted"] is True


def test_csv_borehole_import(client):
    project_id = create_project(client)
    result = import_sample_boreholes(client, project_id)
    assert result["boreholeCount"] == 4
    assert result["layerCount"] == 12
    project = client.get(f"/api/projects/{project_id}").json()
    assert len(project["boreholes"]) == 4
    assert len(project["strata"]) == 3


def test_invalid_csv_import_returns_errors(client):
    project_id = create_project(client)
    with INVALID_CSV.open("rb") as f:
        response = client.post(f"/api/projects/{project_id}/boreholes/import-csv", files={"file": (INVALID_CSV.name, f, "text/csv")})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["errors"]


def test_stratum_parameter_merge_average():
    rows = read_csv_bytes(SAMPLE_CSV.read_bytes())
    result = parse_borehole_rows(rows, source_file="sample.csv")
    assert result.success
    clay = next(s for s in result.strata if s.code == "2-1")
    assert clay.parameters.cohesion == pytest.approx((22 + 24 + 23 + 22) / 4)


def test_outline_validation_and_segments(client):
    project_id = create_project(client)
    excavation = create_excavation(client, project_id)
    assert excavation["area"] == pytest.approx(1500)
    assert len(excavation["segments"]) == 4
    assert excavation["segments"][0]["name"] == "S1"


def test_self_intersection_detection():
    bow = Polyline2D(closed=True, points=[Point2D(x=0, y=0), Point2D(x=10, y=10), Point2D(x=0, y=10), Point2D(x=10, y=0)])
    assert is_self_intersecting(bow) is True
    errors = validate_outline(bow, top_elevation=0, bottom_elevation=-5)
    assert any("自交" in err for err in errors)


def test_idw_interpolation():
    grid = interpolate_surface_idw([(0, 0, 0), (10, 0, -10)], (0, 0, 10, 10), 10)
    assert grid.x_values == [0.0, 10.0]
    assert grid.y_values == [0.0, 10.0]
    assert grid.z_values[0][0] == 0
    assert grid.z_values[0][1] == -10


def test_support_count_and_wall_thickness_rules():
    assert select_wall_thickness(12)[0] == 1.0
    assert select_wall_thickness(21)[0] == 1.2
    assert select_wall_thickness(21)[1]
    assert select_support_count(12)[0] == 3
    assert select_support_count(16)[0] == 3
    assert select_support_count(21)[0] == 5


def test_vtu_import(client):
    project_id = create_project(client)
    with SAMPLE_VTU.open("rb") as f:
        response = client.post(f"/api/projects/{project_id}/geology/import-vtu", files={"file": (SAMPLE_VTU.name, f, "application/xml")})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["points"]) == 4
    assert data["suggestedMapping"]["mat_id"] == "stratum_id"


def test_calculation_result_schema(client):
    project_id = full_design_workflow(client)
    response = client.post(f"/api/projects/{project_id}/calculation/run")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["projectId"] == project_id
    assert data["professionalReviewRequired"] is True
    assert data["governingValues"]["maxTotalPressure"] > 0
    assert data["stageResults"]


def test_ifc_export_file_exists(client):
    project_id = full_design_workflow(client)
    client.post(f"/api/projects/{project_id}/calculation/run")
    response = client.post(f"/api/projects/{project_id}/export/ifc")
    assert response.status_code == 200, response.text
    assert b"IFCPROJECT" in response.content
    assert len(response.content) > 200


def test_docx_report_export_file_exists(client):
    project_id = full_design_workflow(client)
    client.post(f"/api/projects/{project_id}/calculation/run")
    response = client.post(f"/api/projects/{project_id}/export/report")
    assert response.status_code == 200, response.text
    assert response.content[:2] == b"PK"  # DOCX zip header
    assert len(response.content) > 1000


def test_jgj120_rankine_pressure_formula_subset():
    from app.calculation.earth_pressure import calculate_lateral_pressure_profile, earth_pressure_coefficients
    from app.schemas.domain import GeologicalLayer, SoilParameters

    ka, kp, k0 = earth_pressure_coefficients(30)
    assert ka == pytest.approx(1 / 3, rel=1e-3)
    assert kp == pytest.approx(3.0, rel=1e-3)
    assert k0 == pytest.approx(0.5, rel=1e-3)
    layer = GeologicalLayer(
        stratum_code="S1",
        stratum_name="Clean sand",
        top_elevation=0,
        bottom_elevation=-10,
        thickness=10,
        parameters=SoilParameters(unit_weight=18, cohesion=0, friction_angle=30),
    )
    profile = calculate_lateral_pressure_profile([layer], excavation_depth=6, groundwater_level=-100, surcharge=0, top_elevation=0, step=1)
    assert profile.points[-1].earth_pressure == pytest.approx(36.0, rel=1e-3)
    assert profile.points[-1].total_pressure == pytest.approx(36.0, rel=1e-3)


def test_gb50010_rectangular_flexure_formula_subset():
    from app.rules.gb50010.rc_section_rules import design_rectangular_flexural_reinforcement, rectangular_flexural_capacity_knm_per_m

    result = design_rectangular_flexural_reinforcement(350, 1.0, "C35", "HRB400")
    assert result["asRequired"] > 0
    assert result["barArrangement"]["providedAs"] >= result["asRequired"]
    capacity = rectangular_flexural_capacity_knm_per_m(result["barArrangement"]["providedAs"], 1.0, "C35", "HRB400")
    assert capacity >= 350


def test_complete_workflow_has_formula_checks(client):
    project_id = full_design_workflow(client)
    response = client.post(f"/api/projects/{project_id}/calculation/run")
    assert response.status_code == 200, response.text
    data = response.json()
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert any(rule.startswith("JGJ120") for rule in rule_ids)
    assert any(rule.startswith("GB50010") or rule.startswith("GBT50010") for rule in rule_ids)
    assert data["governingValues"]["maxWallMoment"] > 0
    assert data["checkSummary"]


def test_engineering_ifc_contains_geometry_and_property_sets(client):
    project_id = full_design_workflow(client)
    client.post(f"/api/projects/{project_id}/calculation/run")
    response = client.post(f"/api/projects/{project_id}/export/ifc")
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8", errors="ignore")
    assert "IFCEXTRUDEDAREASOLID" in text
    assert "Pset_RetainingWallDesign" in text
    assert "Pset_InternalSupportDesign" in text
    assert "IFCBEAM" in text
    assert "IFCCOLUMN" in text


def test_v1_complete_workflow_has_no_open_software_gaps(client):
    project_id = full_design_workflow(client)
    with SAMPLE_VTU.open("rb") as f:
        client.post(f"/api/projects/{project_id}/geology/import-vtu", files={"file": (SAMPLE_VTU.name, f, "application/xml")})
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    assert data["checkSummary"]["fail"] == 0
    assert data["checkSummary"]["manualReview"] == 0
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert "JGJ120-2012-OVERALL-STABILITY-CIRCULAR-SCREEN" in rule_ids
    assert "GBT50010-2024-SERVICEABILITY-CRACK-SCREEN" in rule_ids
    assurance = client.get(f"/api/projects/{project_id}/assurance/gap-analysis")
    assert assurance.status_code == 200, assurance.text
    assert assurance.json()["completionPercent"] == 100.0
    assert assurance.json()["closedLoopComplete"] is True


def test_ifc_v1_exports_representative_reinforcing_bars(client):
    project_id = full_design_workflow(client)
    client.post(f"/api/projects/{project_id}/calculation/run")
    response = client.post(f"/api/projects/{project_id}/export/ifc")
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8", errors="ignore")
    assert "IFCREINFORCINGBAR" in text
    assert "Pset_ReinforcementGroup" in text
    assert "representative_group_entities_generated" in text


def test_ifc_v1_1_has_valid_extrusion_direction_for_viewers(client):
    project_id = full_design_workflow(client)
    client.post(f"/api/projects/{project_id}/calculation/run")
    response = client.post(f"/api/projects/{project_id}/export/ifc")
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8", errors="ignore")
    assert "IFCEXTRUDEDAREASOLID" in text
    assert ",$," not in "\n".join(line for line in text.splitlines() if "IFCEXTRUDEDAREASOLID" in line)
    assert "IFCDIRECTION((0.,0.,1.))" in text
    assert "DesignFaceCode" in text
    assert "SupportRole" in text


def test_support_layout_spans_short_direction_and_adds_corner_diagonals(client):
    project_id = create_project(client)
    payload = {
        "name": "Long rectangular pit",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [{"x": 0, "y": 0}, {"x": 90, "y": 0}, {"x": 90, "y": 24}, {"x": 0, "y": 24}]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-supports")
    assert response.status_code == 200, response.text
    supports = response.json()["supports"]
    main = [s for s in supports if s["supportRole"] == "main_strut"]
    diagonal = [s for s in supports if s["supportRole"] == "corner_diagonal"]
    assert main, supports
    assert diagonal, "large aspect-ratio rectangular pit should receive corner diagonal braces"
    for support in main:
        # X is the long side, so short-span struts should run from y=min to y=max at constant x.
        assert support["start"]["x"] == pytest.approx(support["end"]["x"])
        # The structural centreline is offset from the wall to leave room for the wale.
        # The stored wall-connection points retain the full 24 m geometric short span.
        assert abs(support["end"]["y"] - support["start"]["y"]) == pytest.approx(22.0, abs=0.25)
        assert abs(support["endWallConnection"]["y"] - support["startWallConnection"]["y"]) == pytest.approx(24.0)


def test_diaphragm_wall_same_straight_face_has_unified_design_length(client):
    project_id = create_project(client)
    payload = {
        "name": "Collinear-node pit",
        "topElevation": 0,
        "bottomElevation": -10,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 60, "y": 0}, {"x": 60, "y": 24}, {"x": 0, "y": 24}
        ]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall")
    assert response.status_code == 200, response.text
    walls = response.json()["diaphragmWalls"]
    bottom_face_walls = [w for w in walls if w["segmentId"] in {"S1", "S2"}]
    assert len(bottom_face_walls) == 2
    assert len({w["designFaceCode"] for w in bottom_face_walls}) == 1
    assert all(w["designLength"] == pytest.approx(60.0) for w in bottom_face_walls)


def test_geology_model_auto_extends_when_excavation_exceeds_borehole_range(client):
    project_id = create_project(client)
    import_sample_boreholes(client, project_id)
    payload = {
        "name": "Oversized pit",
        "topElevation": 0,
        "bottomElevation": -8,
        "outline": {"closed": True, "points": [{"x": -40, "y": -30}, {"x": 110, "y": -30}, {"x": 110, "y": 80}, {"x": -40, "y": 80}]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    model = client.get(f"/api/projects/{project_id}/geology/model").json()
    surface = model["surfaces"][0]
    assert min(surface["grid"]["xValues"]) <= -50
    assert max(surface["grid"]["xValues"]) >= 120
    assert min(surface["grid"]["yValues"]) <= -40
    assert max(surface["grid"]["yValues"]) >= 90
    assert any("自动外扩" in msg for msg in model["warnings"])


def test_v1_2_column_foundation_auto_expands_until_bearing_passes():
    from app.calculation.engine import design_column_foundation
    from app.rules.gb50007.foundation_rules import check_foundation_bearing_pressure

    foundation = design_column_foundation("STC-001", vertical_force_kN=2206.604, fa_kpa=220.0)
    assert foundation.width > 3.0
    assert foundation.length > 3.0
    assert foundation.area > 9.0
    assert foundation.average_pressure <= foundation.fa
    assert foundation.max_pressure <= 1.2 * foundation.fa
    check = check_foundation_bearing_pressure(
        object_id="col-test",
        vertical_force_kN=foundation.vertical_force,
        foundation_self_weight_kN=foundation.foundation_self_weight,
        area_m2=foundation.area,
        fa_kpa=foundation.fa,
        pkmax_kpa=foundation.max_pressure,
    )
    assert check.status == "pass"


def test_run_sample_workflow_equivalent_v1_2_has_no_bearing_fail(client):
    response = client.post(
        "/api/projects",
        json={
            "name": "PitGuard 全流程示例：矩形深基坑",
            "location": "示例场地 / 本地坐标",
            "designSettings": {
                "safetyGrade": "二级",
                "environmentGrade": "严格",
                "groundwaterLevel": -1.5,
                "groundwaterLevelInside": -12.0,
                "surcharge": 20.0,
                "minimumSegmentLength": 0.5,
                "ruleSet": "jgj120_gbt50010_gb50007_gb50009_v0_2",
                "pressureMethod": "active",
                "waterSoilMethod": "separate",
            },
        },
    )
    assert response.status_code == 200, response.text
    project_id = response.json()["id"]
    import_sample_boreholes(client, project_id)
    assert client.post(f"/api/projects/{project_id}/geology/build-model?grid_size=10").status_code == 200
    with SAMPLE_VTU.open("rb") as f:
        vtu = client.post(f"/api/projects/{project_id}/geology/import-vtu", files={"file": (SAMPLE_VTU.name, f, "application/xml")})
    assert vtu.status_code == 200, vtu.text
    create_excavation(client, project_id)
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
    assert client.post(f"/api/projects/{project_id}/calculation/build-cases").status_code == 200
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    assert data["checkSummary"]["fail"] == 0
    assert data["governingValues"]["governingCheckStatus"] in {"pass", "warning"}
    foundation_checks = [c for c in data["checks"] if c["ruleId"] in {"GB50007-2011-BEARING-SUBSET", "GB50007-2011-COLUMN-PILE-CAPACITY-SUBSET"}]
    assert foundation_checks
    assert all(c["status"] == "pass" for c in foundation_checks)
    assert all((c.get("foundationArea", 9.0) >= 9.0) or (c.get("pileCapacity", 0) >= c.get("calculatedValue", 1)) for c in foundation_checks)

    project = client.get(f"/api/projects/{project_id}").json()
    columns = project["retainingSystem"]["columns"]
    assert len(columns) >= 2
    assert all(c["foundationDesign"]["checkStatus"] == "pass" for c in columns)
    assert any(c.get("supportCodes") for c in columns)

    assurance = client.get(f"/api/projects/{project_id}/assurance/gap-analysis")
    assert assurance.status_code == 200, assurance.text
    assurance_data = assurance.json()
    assert assurance_data["softwareVersion"] == SOFTWARE_VERSION
    assert assurance_data["capabilityCompleteness"] == 100.0
    assert assurance_data["completionPercent"] == 100.0
    assert assurance_data["softwareFlowComplete"] is True
    assert assurance_data["engineeringCheckStatus"] in {"pass", "warning"}
    assert assurance_data["closedLoopComplete"] is True



def test_v1_4_concave_pit_supports_do_not_cross_reentrant_void(client):
    project_id = create_project(client)
    payload = {
        "name": "L-shaped pit",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 60, "y": 0}, {"x": 60, "y": 20},
            {"x": 30, "y": 20}, {"x": 30, "y": 40}, {"x": 0, "y": 40}
        ]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-supports")
    assert response.status_code == 200, response.text
    data = response.json()
    supports = data["supports"]
    main = [s for s in supports if s["supportRole"] == "main_strut"]
    assert main
    # Any support on the right-hand leg of the L-shaped pit must terminate at y=20,
    # not cross the missing upper-right void from y=20 to y=40.
    right_leg = [s for s in main if s["start"]["x"] > 30 and s["end"]["x"] > 30]
    assert right_leg
    assert all(max(s["start"]["y"], s["end"]["y"]) <= 20.5 for s in right_leg)
    design_notes = data.get("layoutSummary", {}).get("designNotes", [])
    messages = list(data.get("warnings", [])) + list(design_notes)
    assert any("凹形" in msg or "空区" in msg or "回折" in msg for msg in messages)


def test_v1_4_support_layout_generates_columns_from_strut_spans(client):
    project_id = create_project(client)
    payload = {
        "name": "Wide rectangular pit",
        "topElevation": 0,
        "bottomElevation": -14,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 72, "y": 0}, {"x": 72, "y": 32}, {"x": 0, "y": 32}
        ]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-supports")
    assert response.status_code == 200, response.text
    data = response.json()
    supports = data["supports"]
    columns = data["columns"]
    assert supports
    assert columns
    assert all(c.get("supportCodes") for c in columns)
    assert any((s.get("spanLength") or 0) > 0 and (s.get("baySpacing") or 0) > 0 for s in supports if s["supportRole"] == "main_strut")


def test_v1_5_support_nodes_column_piles_and_tributary_widths(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert "GB50010-NODE-BEARING-SUBSET" in rule_ids
    assert "GB50007-2011-COLUMN-PILE-CAPACITY-SUBSET" in rule_ids
    project = client.get(f"/api/projects/{project_id}").json()
    retaining = project["retainingSystem"]
    assert retaining["supportNodes"]
    assert all(node["bearingPlate"]["checkStatus"] == "pass" for node in retaining["supportNodes"])
    assert retaining["columns"]
    assert all(col["foundationDesign"]["foundationType"] == "column_pile" for col in retaining["columns"])
    main = [s for s in retaining["supports"] if s["supportRole"] == "main_strut"]
    assert main
    assert all(s.get("startTributaryWidth") or s.get("endTributaryWidth") for s in main)
    support_force_methods = [force["method"] for result in data["stageResults"] for force in result["supportForces"]]
    assert any("global wall-wale-support stiffness matrix" in method.lower() for method in support_force_methods)


def test_v1_5_center_island_ring_support_system(client):
    project_id = create_project(client)
    payload = {
        "name": "Square pit with center island",
        "topElevation": 0,
        "bottomElevation": -14,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 60, "y": 0}, {"x": 60, "y": 55}, {"x": 0, "y": 55}
        ]},
        "obstacles": [{
            "name": "中心岛保留区",
            "obstacleType": "center_island",
            "center": {"x": 30, "y": 27.5},
            "width": 14,
            "length": 12,
            "clearance": 1.5,
            "active": True,
        }]
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-supports")
    assert response.status_code == 200, response.text
    retaining = response.json()
    assert retaining["ringBeams"]
    assert any(s["supportRole"] == "ring_strut" for s in retaining["supports"])
    assert retaining["layoutSummary"]["ringBeamCount"] == len(retaining["ringBeams"])


def test_v1_5_obstacle_avoidance_skips_crossing_supports(client):
    project_id = create_project(client)
    payload = {
        "name": "Pit with ramp opening",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 80, "y": 0}, {"x": 80, "y": 30}, {"x": 0, "y": 30}
        ]},
        "obstacles": [{
            "name": "出土坡道",
            "obstacleType": "ramp",
            "center": {"x": 32, "y": 15},
            "width": 12,
            "length": 24,
            "clearance": 1.0,
            "active": True,
        }]
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-supports")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["supports"]
    assert any("避让" in msg for msg in list(data.get("warnings", [])) + list(data.get("layoutSummary", {}).get("designNotes", [])))
    # No generated support midpoint should be inside the ramp rectangle x=[25,39], y=[2,28].
    for support in data["supports"]:
        mx = (support["start"]["x"] + support["end"]["x"]) / 2
        my = (support["start"]["y"] + support["end"]["y"]) / 2
        assert not (25 <= mx <= 39 and 2 <= my <= 28)


def test_v1_6_continuous_wale_beam_reaction_model_is_used(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    forces = [force for result in data["stageResults"] for force in result["supportForces"]]
    assert forces
    continuous = [f for f in forces if "continuous_wale_beam_elastic_supports" in str(f.get("distributionMethod")) or "global_wall_wale_support_matrix" in str(f.get("distributionMethod"))]
    assert continuous, "support forces should be distributed by continuous wale/global matrix model"
    assert all(f.get("continuousBeamReaction", 0) >= 0 for f in continuous)
    assert all(f.get("elasticSupportStiffness", 0) > 0 for f in continuous)
    assert all(f.get("waleChainage") is not None for f in continuous)
    assert any("wale" in f.get("method", "").lower() for f in continuous)


def test_v1_6_continuous_beam_solver_balances_wall_line_load(client):
    from app.calculation.earth_pressure import calculate_lateral_pressure_profile
    from app.calculation.support_forces import estimate_support_axial_forces
    from app.schemas.domain import GeologicalLayer, SoilParameters, Point2D, SectionDefinition, MaterialDefinition, SupportElement

    class Segment:
        name = "S1"
        start = Point2D(x=0, y=0)
        end = Point2D(x=60, y=0)
        length = 60.0

    layer = GeologicalLayer(stratum_code="S", stratum_name="sand", top_elevation=0, bottom_elevation=-15, thickness=15, parameters=SoilParameters(unit_weight=18, cohesion=0, friction_angle=30))
    profile = calculate_lateral_pressure_profile([layer], excavation_depth=8, groundwater_level=-100, surcharge=20, top_elevation=0, step=1)
    supports = []
    for idx, x in enumerate([15, 30, 45], start=1):
        supports.append(SupportElement(
            code=f"SP-L1-{idx}", level_index=1, elevation=-2.0,
            start=Point2D(x=x, y=0), end=Point2D(x=x, y=28),
            start_face_code="S1", end_face_code="S3", start_tributary_width=20,
            section=SectionDefinition(width=1.2, height=1.2, name="1200x1200 RC"),
            material=MaterialDefinition(name="Concrete", grade="C40"),
        ))
    forces = estimate_support_axial_forces(profile, supports, 60, 0, -8, segment_name="S1", segment=Segment())
    assert len(forces) == 3
    assert all(f.distribution_method == "balanced_wale_bay_tributary_reaction" for f in forces)
    # The symmetric model should produce matching edge reactions and a positive interior reaction.
    assert forces[0].continuous_beam_reaction == pytest.approx(forces[2].continuous_beam_reaction, rel=1e-4)
    assert forces[1].continuous_beam_reaction > 0


def test_v1_7_wale_beam_design_and_support_construction_effects(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    assert data["checkSummary"]["fail"] == 0
    wale_results = [wale for sr in data["stageResults"] for wale in sr.get("waleBeamResults", [])]
    assert wale_results, "V1.7 should export wale beam internal-force results on stage results"
    assert all(wale["maxMoment"] >= 0 for wale in wale_results)
    assert all(wale["maxShear"] >= 0 for wale in wale_results)
    forces = [force for sr in data["stageResults"] for force in sr["supportForces"] if force.get("preloadEffect") is not None]
    assert forces, "support forces should carry preload/temperature/gap construction-effect fields"
    assert any((force.get("effectiveAxialForce") or 0) >= force["axialForce"] for force in forces)
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert "GB50010-WALE-FLEXURE-SUBSET" in rule_ids
    assert "GB50010-WALE-SHEAR-SUBSET" in rule_ids
    assert "GB50010-WALE-NODE-REBAR-COORDINATION-SUBSET" in rule_ids
    assert "JGJ120-SUPPORT-CONSTRUCTION-EFFECTS-SUBSET" in rule_ids
    project = client.get(f"/api/projects/{project_id}").json()
    wale_beams = [beam for beam in project["retainingSystem"]["waleBeams"] if beam.get("designResult")]
    assert wale_beams
    assert all(beam["designResult"]["checkStatus"] in {"pass", "warning"} for beam in wale_beams)
    assert any(beam.get("reinforcement") for beam in wale_beams)
    supports = project["retainingSystem"]["supports"]
    assert any(s.get("preload") and s.get("thermalAxialForce") is not None and s.get("gapClosureForce") is not None for s in supports)


def test_v1_8_p0_to_p5_iteration_outputs_envelopes_lifecycle_and_report_data(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    summary = data.get("designIterationSummary") or {}
    assert summary.get("version") == SOFTWARE_VERSION
    assert all(summary.get(key) is True for key in [
        "p6GlobalCoupledMatrix", "p7ReportCharts", "p8CadGeometryKernel",
        "p9GroundwaterStabilitySpecials", "p10DesignReviewSummary",
        "p11SpatialFrameKernel", "p12ReviewableStabilityPackage", "p13ConstructionDrawingOutput",
    ])
    assert data.get("reportDiagramData", {}).get("waleEnvelopes"), "P4 should export wale-envelope diagram data"
    assert any((sr.get("coupledSystemResult") or {}).get("method") for sr in data["stageResults"]), "P5 coupled summary should be present"

    project = client.get(f"/api/projects/{project_id}").json()
    wale_beams = [beam for beam in project["retainingSystem"]["waleBeams"] if beam.get("designResult")]
    assert wale_beams
    design = wale_beams[0]["designResult"]
    assert design.get("envelope")
    assert design.get("deflectionLimit") is not None
    assert design.get("optimizationHistory")
    assert design.get("wallConnectionNote")
    supports = project["retainingSystem"]["supports"]
    assert any(s.get("lifecycleNote") and s.get("preloadStageId") and s.get("removalStageId") for s in supports)
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert "WALE-DEFLECTION-ENVELOPE-SUBSET" in rule_ids
    assert "JGJ120-SUPPORT-LIFECYCLE-PATH-SUBSET" in rule_ids


def test_v1_9_global_matrix_charts_and_design_review_outputs(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    assert data.get("designReviewSummary")
    review = data["designReviewSummary"]
    assert review["strengthStatus"] in {"pass", "warning", "manual_review"}
    assert review["stiffnessStatus"] in {"pass", "warning", "manual_review"}
    assert review["stabilityStatus"] in {"pass", "warning", "manual_review"}
    global_results = [sr.get("globalCoupledResult") for sr in data["stageResults"] if sr.get("globalCoupledResult")]
    assert global_results, "V1.9 should export typed global coupled matrix results"
    assert any((g.get("matrixSize") or 0) > 0 for g in global_results)
    assert any(g.get("supportReactions") for g in global_results)
    diagram = data.get("reportDiagramData") or {}
    assert diagram.get("globalCoupledSystems")
    assert diagram.get("supportAxialSummary")
    assert diagram.get("designReviewSummary")
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert "JGJ120-2012-DEWATERING-STAGE-SUBSET" in rule_ids
    assert "JGJ120-2012-LAYERED-SEEPAGE-GRADIENT-SUBSET" in rule_ids
    assert "JGJ120-2012-WEAK-UNDERLYING-LAYER-SUBSET" in rule_ids


def test_v2_0_spatial_frame_stability_and_drawing_outputs(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    summary = data.get("designIterationSummary") or {}
    assert summary.get("p11SpatialFrameKernel") is True
    assert summary.get("p12ReviewableStabilityPackage") is True
    assert summary.get("p13ConstructionDrawingOutput") is True
    global_results = [sr.get("globalCoupledResult") for sr in data["stageResults"] if sr.get("globalCoupledResult")]
    assert any((g.get("spatialMatrixSize") or 0) > 0 for g in global_results)
    assert any(g.get("wallRotationProfile") for g in global_results)
    assert any(g.get("waleNodeProfile") for g in global_results)
    assert any(g.get("columnVerticalDofs") for g in global_results)
    stability = data.get("stabilityDetailedResult")
    assert stability and stability.get("circularSlipSurfaces") and stability.get("seepagePaths")
    assert stability.get("dewateringWells") and stability.get("improvementOptions")
    assert data.get("drawingSheets")
    diagram = data.get("reportDiagramData") or {}
    assert diagram.get("reviewableStabilityPackage")
    assert diagram.get("drawingSheets")



def test_v2_0_2_support_spacing_is_practical_and_dense(client):
    project_id = create_project(client)
    payload = {
        "name": "Dense support spacing pit",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 30}, {"x": 0, "y": 30}
        ]},
        "explicitPlacement": True,
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/auto-supports")
    assert response.status_code == 200, response.text
    supports = response.json()["supports"]
    main = [s for s in supports if s["levelIndex"] == 1 and s.get("supportRole") == "main_strut"]
    # End zones may be carried by wall-to-wall corner/transition members, so a
    # fixed main-strut count is not a stable topology contract.  Verify the
    # actual engineering requirement: every generated main bay remains in the
    # practical 3-6 m range.
    assert len(main) >= 1
    assert all(3.0 <= float(s["baySpacing"]) <= 6.0 for s in main)


def test_v2_0_2_ifc_escapes_non_ascii_step_strings(client, tmp_path):
    response_project = client.post("/api/projects", json={"name": "中文基坑项目", "location": "测试场地"})
    assert response_project.status_code == 200, response_project.text
    project_id = response_project.json()["id"]
    payload = {
        "name": "Main excavation",
        "topElevation": 0,
        "bottomElevation": -8,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 20}, {"x": 0, "y": 20}
        ]},
        "explicitPlacement": True,
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    response = client.post(f"/api/projects/{project_id}/export/ifc")
    assert response.status_code == 200, response.text
    text = response.text
    assert "\\X2\\" in text
    assert "中文" not in text


def test_v2_0_2_unlocked_excavation_centers_on_geological_model(client):
    project_id = create_project(client)
    import_sample_boreholes(client, project_id)
    assert client.post(f"/api/projects/{project_id}/geology/build-model?grid_size=10").status_code == 200
    payload = {
        "name": "Unlocked local pit",
        "topElevation": 0,
        "bottomElevation": -8,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 20, "y": 10}, {"x": 0, "y": 10}
        ]},
        "explicitPlacement": False,
    }
    response = client.post(f"/api/projects/{project_id}/excavation", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["centeredOnGeology"] is True
    assert "地质模型中心" in data["placementNote"]


def test_v2_0_3_quality_gates_support_ifc_and_formal_report(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    assert data.get("supportLayoutQuality")
    assert data["supportLayoutQuality"]["score"] >= 0
    assert data.get("ifcCompatibility")
    assert data["ifcCompatibility"]["status"] in {"pass", "warning", "manual_review"}
    assert data.get("formalReportGate")
    gate = data["formalReportGate"]
    assert "blockingItems" in gate and "warningItems" in gate and "missingItems" in gate
    assert data["reportDiagramData"].get("supportLayoutQuality")
    assert data["reportDiagramData"].get("ifcCompatibility")
    assert data["reportDiagramData"].get("formalReportGate")
    rule_ids = {check["ruleId"] for check in data["checks"]}
    assert any(rule.startswith("QUALITY-") for rule in rule_ids)


def test_v2_0_3_ifc_check_endpoint_and_sidecar(client):
    project_id = full_design_workflow(client)
    assert client.post(f"/api/projects/{project_id}/calculation/run").status_code == 200
    response = client.post(f"/api/projects/{project_id}/export/ifc-check")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] in {"pass", "warning", "manual_review"}
    assert data["rawUnicodeFound"] is False
    assert data["entityCounts"].get("IFCWALL", 0) > 0
    assert "missingMaterialAssociationCount" in data


def test_v2_0_3_assurance_explains_gate_items(client):
    project_id = full_design_workflow(client)
    assert client.post(f"/api/projects/{project_id}/calculation/run").status_code == 200
    assurance = client.get(f"/api/projects/{project_id}/assurance/gap-analysis")
    assert assurance.status_code == 200, assurance.text
    data = assurance.json()
    assert data["softwareVersion"] == SOFTWARE_VERSION
    assert "softwareFlowMissingItems" in data
    assert "officialIssueGateStatus" in data
    assert "officialIssueBlockingItems" in data
    assert "supportLayoutQuality" in data
    assert "ifcCompatibility" in data
    assert isinstance(data["closedLoopComplete"], bool)


def test_v2_0_4_support_crossing_quality_and_viewer_profiles(client):
    project_id = create_project(client)
    payload = {
        "name": "Crossing support pit",
        "topElevation": 0,
        "bottomElevation": -10,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 30}, {"x": 0, "y": 30}
        ]},
        "explicitPlacement": True,
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
    project = client.get(f"/api/projects/{project_id}").json()
    ret = project["retainingSystem"]
    ret["supports"] = [
        {
            "id": "support-cross-a", "code": "X-A", "levelIndex": 1, "elevation": -2,
            "start": {"x": 0, "y": 0}, "end": {"x": 30, "y": 30}, "supportRole": "manual",
            "spanLength": 42.426, "sectionType": "rc_rectangular", "section": {"width": 1.0, "height": 1.0, "name": "1000x1000"},
            "material": {"name": "Concrete", "grade": "C40"}, "reinforcement": []
        },
        {
            "id": "support-cross-b", "code": "X-B", "levelIndex": 1, "elevation": -2,
            "start": {"x": 0, "y": 30}, "end": {"x": 30, "y": 0}, "supportRole": "manual",
            "spanLength": 42.426, "sectionType": "rc_rectangular", "section": {"width": 1.0, "height": 1.0, "name": "1000x1000"},
            "material": {"name": "Concrete", "grade": "C40"}, "reinforcement": []
        },
    ]
    update = client.put(f"/api/projects/{project_id}", json={"retainingSystem": ret})
    assert update.status_code == 200, update.text
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    q = data["supportLayoutQuality"]
    repair = data.get("supportLayoutRepair")
    assert repair
    assert repair["scoreAfter"] >= repair["scoreBefore"]
    assert q["metrics"]["supportCrossingCount"] == 0
    assert any(a.get("action") in {"regenerate_dense_bays_and_repair_layout", "objective_function_support_layout_optimization"} for a in repair.get("actions", []))
    assert data["ifcCompatibility"].get("viewerProfiles")
    viewers = {p["viewer"] for p in data["ifcCompatibility"]["viewerProfiles"]}
    assert {"BlenderBIM / Bonsai", "BIMVision", "Solibri", "Autodesk Revit", "Navisworks"}.issubset(viewers)


def test_v2_0_4_formal_gate_has_template_sections(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    gate = calc.json()["formalReportGate"]
    assert gate.get("checklistSections")
    titles = [s["title"] for s in gate["checklistSections"]]
    assert "一、计算结果状态" in titles
    assert "二、支撑布置合理性" in titles
    assert "三、IFC 兼容性" in titles
    assert "五、正式出图阻断项" in titles


def test_v2_0_5_dual_mode_ifc_and_report_support_plan(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    assert data.get("supportLayoutRepair")
    light = client.post(f"/api/projects/{project_id}/export/ifc-light")
    detailed = client.post(f"/api/projects/{project_id}/export/ifc-detailed")
    assert light.status_code == 200, light.text
    assert detailed.status_code == 200, detailed.text
    assert b"IFCREINFORCINGBAR" not in light.content
    assert b"IFCREINFORCINGBAR" in detailed.content
    check_light = client.post(f"/api/projects/{project_id}/export/ifc-check?mode=coordination_light")
    assert check_light.status_code == 200, check_light.text
    c = check_light.json()
    assert c["exportMode"] == "coordination_light"
    assert c["entityCounts"].get("IFCREINFORCINGBAR", 0) == 0
    report = client.post(f"/api/projects/{project_id}/export/report")
    assert report.status_code == 200, report.text
    assert len(report.content) > 1000


def test_v2_0_6_support_objective_optimizer_and_analysis_ifc(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    repair = data.get("supportLayoutRepair")
    assert repair
    assert repair.get("optimizationMethod")
    assert repair.get("candidateCount", 0) > 0
    assert repair.get("candidates")
    best = repair["candidates"][0]
    assert best["score"] >= 0
    assert "spacingDeviation" in best["objectiveTerms"]
    assert "supportCrossing" in best["objectiveTerms"]
    analysis = client.post(f"/api/projects/{project_id}/export/ifc-analysis")
    assert analysis.status_code == 200, analysis.text
    assert b"IFCREINFORCINGBAR" not in analysis.content
    assert b"Pset_AnalysisSupportSpring" in analysis.content
    assert b"Pset_AnalysisConstructionStage" in analysis.content
    check = client.post(f"/api/projects/{project_id}/export/ifc-check?mode=analysis_model")
    assert check.status_code == 200, check.text
    check_data = check.json()
    assert check_data["exportMode"] == "analysis_model"
    assert check_data["entityCounts"].get("IFCREINFORCINGBAR", 0) == 0


def test_v2_0_6_optimize_supports_endpoint(client):
    project_id = full_design_workflow(client)
    response = client.post(f"/api/projects/{project_id}/design/optimize-supports")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("candidateCount", 0) > 0
    assert data.get("bestCandidateId")
    assert data.get("objectiveWeights", {}).get("supportCrossing")


def test_v2_0_7_constrained_support_line_optimizer_outputs_ranked_plans(client):
    project_id = full_design_workflow(client)
    response = client.post(f"/api/projects/{project_id}/design/optimize-supports")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("optimizationPhase") in {"V2.0.7 constrained line-position optimization", "V2.0.8 interactive candidate selection and weighted constrained optimization", "V2.0.9 local locks, animated candidate delta, and candidate calculation comparison"}
    assert data.get("candidateCount", 0) >= 3
    assert data.get("hardConstraintLabels")
    assert data.get("softObjectiveLabels")
    candidates = data.get("candidates", [])
    assert 3 <= len(candidates) <= 5
    best = candidates[0]
    assert "hardConstraints" in best
    assert "softObjectives" in best
    assert "variableSummary" in best
    assert "exportReadiness" in best
    assert "supportNoCrossing" in best["hardConstraints"]
    assert "spacingCloseTo3To6m" in best["softObjectives"]


def test_v2_0_7_calculation_embeds_constrained_candidates(client):
    project_id = full_design_workflow(client)
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    data = calc.json()
    repair = data.get("supportLayoutRepair")
    assert repair and repair.get("optimizationPhase") in {"V2.0.7 constrained line-position optimization", "V2.0.8 interactive candidate selection and weighted constrained optimization", "V2.0.9 local locks, animated candidate delta, and candidate calculation comparison"}
    assert repair.get("candidates")
    assert data.get("reportDiagramData", {}).get("supportLayoutRepair")


def test_v2_0_8_interactive_candidate_adoption_weights_and_locks(client):
    project_id = full_design_workflow(client)
    project = client.get(f"/api/projects/{project_id}").json()
    supports = project["retainingSystem"]["supports"]
    locked = [supports[0]["id"]] if supports else []
    lock = client.post(f"/api/projects/{project_id}/design/lock-support-lines", json={"supportIds": locked, "locked": True, "reason": "unit test lock"})
    assert lock.status_code == 200, lock.text
    response = client.post(f"/api/projects/{project_id}/design/optimize-supports", json={"preset": "low_axial_force", "objectiveWeights": {"axialPeakProxy": 35, "columnCount": 3}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("optimizationPhase") in {"V2.0.8 interactive candidate selection and weighted constrained optimization", "V2.0.9 local locks, animated candidate delta, and candidate calculation comparison"}
    assert data.get("objectiveWeights", {}).get("axialPeakProxy", 0) >= 35
    assert data.get("lockedSupportIds")
    candidates = data.get("candidates", [])
    assert candidates and candidates[0].get("planGeometry")
    assert candidates[0].get("deltaGeometry") is not None
    assert candidates[0].get("weightSummary")
    candidate_id = candidates[-1]["id"]
    adopted = client.post(f"/api/projects/{project_id}/design/adopt-support-candidate", json={"candidateId": candidate_id})
    assert adopted.status_code == 200, adopted.text
    adopted_data = adopted.json()
    assert adopted_data.get("selectedCandidateId") == candidate_id


def test_v2_0_8_report_contains_candidate_score_chart(client):
    project_id = full_design_workflow(client)
    opt = client.post(f"/api/projects/{project_id}/design/optimize-supports", json={"preset": "balanced"})
    assert opt.status_code == 200, opt.text
    calc = client.post(f"/api/projects/{project_id}/calculation/run")
    assert calc.status_code == 200, calc.text
    report = client.post(f"/api/projects/{project_id}/export/report")
    assert report.status_code == 200, report.text
    assert len(report.content) > 1500


def test_v2_0_12_system_diagnostics_endpoint(client):
    response = client.get("/api/system/diagnostics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "2.0.12"
    assert payload["pythonExecutable"]
    assert any(item["packageName"] == "fastapi" for item in payload["modules"])
    assert any(item["packageName"] == "python-multipart" for item in payload["modules"])
    assert "missingModules" in payload


def test_v2_0_14_support_candidate_spacing_changes_geometry(client):
    project_id = create_project(client)
    payload = {
        "name": "Rect pit candidate diversity",
        "topElevation": 0,
        "bottomElevation": -12,
        "outline": {"closed": True, "points": [
            {"x": 0, "y": 0}, {"x": 80, "y": 0}, {"x": 80, "y": 32}, {"x": 0, "y": 32}
        ]},
    }
    assert client.post(f"/api/projects/{project_id}/excavation", json=payload).status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
    response = client.post(f"/api/projects/{project_id}/design/optimize-supports", json={})
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert len(candidates) >= 3
    signatures = {(c["supportCount"], c["columnCount"], c.get("maxBaySpacing"), c.get("maxSpanLength")) for c in candidates[:3]}
    assert len(signatures) >= 2
    assert len({c["targetSpacing"] for c in candidates[:3]}) >= 2


def test_v2_0_14_construction_visual_ifc_uses_rebar_proxy(client):
    project_id = create_project(client)
    create_excavation(client, project_id)
    assert client.post(f"/api/projects/{project_id}/design/auto-diaphragm-wall").status_code == 200
    assert client.post(f"/api/projects/{project_id}/design/auto-supports").status_code == 200
    response = client.post(f"/api/projects/{project_id}/export/ifc-check?mode=construction_visual")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["exportMode"] == "construction_visual"
    assert data["entityCounts"].get("IFCREINFORCINGBAR", 0) == 0
    assert data["entityCounts"].get("IFCBUILDINGELEMENTPROXY", 0) > 0
