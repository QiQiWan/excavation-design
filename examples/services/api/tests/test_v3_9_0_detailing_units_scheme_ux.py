from __future__ import annotations

from app.schemas.domain import ExcavationModel, Point2D, Polyline2D, Project, RetainingSystem, SupportElement
from app.services import coordination_optimizer, crane_logistics, node_submodel
from app.services.unit_registry import unit_registry


def _project() -> Project:
    support = SupportElement(
        code="S1-L01",
        level_index=1,
        elevation=-3.0,
        start=Point2D(x=0.0, y=5.0),
        end=Point2D(x=20.0, y=5.0),
        design_axial_force=6500.0,
    )
    excavation = ExcavationModel(
        name="unit-test-pit",
        outline=Polyline2D(points=[
            Point2D(x=0.0, y=0.0), Point2D(x=20.0, y=0.0),
            Point2D(x=20.0, y=10.0), Point2D(x=0.0, y=10.0),
        ], closed=True),
        top_elevation=0.0,
        bottom_elevation=-10.0,
        depth=10.0,
    )
    return Project(name="v3.9", excavation=excavation, retaining_system=RetainingSystem(supports=[support]))


def test_unit_registry_exposes_engineering_units() -> None:
    registry = unit_registry()
    assert registry["system"] == "SI-engineering"
    assert registry["quantities"]["stiffness"]["symbol"] == "kN/m"
    assert registry["quantities"]["elevation"]["symbol"] == "m"
    assert registry["quantities"]["wall_moment"]["symbol"] == "kN·m/m"
    assert registry["rules"]["rawUnitlessNumberForbiddenForEngineeringQuantity"] is True


def test_coordination_optimizer_builds_and_applies_four_solution_families(monkeypatch) -> None:
    fake_detailing = {
        "deepDetailing": {
            "embeddedItemCollisionChecks": [
                {
                    "checkId": "CHK-1", "embeddedItemId": "EMB-1", "hostCode": "W1",
                    "barGroupId": "BG-1", "barType": "longitudinal", "status": "warning",
                },
                {
                    "checkId": "CHK-2", "embeddedItemId": "EMB-1", "hostCode": "W1",
                    "barGroupId": "BG-1", "barType": "longitudinal", "status": "warning",
                },
            ]
        }
    }
    monkeypatch.setattr(coordination_optimizer, "build_rebar_detailing", lambda project, mode="balanced": fake_detailing)
    project = _project()
    result = coordination_optimizer.build_coordination_optimization(project)
    assert result["summary"]["issueGroupCount"] == 1
    actions = {item["action"] for item in result["issues"][0]["candidates"]}
    assert actions == {"rebar_reroute", "embedded_shift", "embedded_opening", "local_reinforcement"}
    issue = result["issues"][0]
    candidate = next(item for item in issue["candidates"] if item["candidateId"] == issue["recommendedCandidateId"])
    assert candidate["geometryDelta"]["type"]
    assert candidate["verification"]
    assert candidate["predictedClearanceM"] >= candidate["requiredClearanceM"]
    applied = coordination_optimizer.apply_coordination_candidate(project, issue["issueId"], issue["recommendedCandidateId"])
    assert applied["summary"]["appliedSolutionCount"] == 1
    assert project.advanced_engineering["detailingOverrides"]


def test_node_submodel_selects_high_risk_node_and_reports_units(monkeypatch) -> None:
    monkeypatch.setattr(node_submodel, "evaluate_node_local_response", lambda project: {
        "nodes": [{
            "nodeId": "N1", "nodeCode": "NODE-1", "supportCode": "S1-L01",
            "designForceKn": 6500.0, "governingUtilization": 0.96,
            "eccentricityUtilization": 0.45, "requiresNonlinearFE": True, "status": "warning",
        }]
    })
    result = node_submodel.build_node_submodels(_project(), top_n=1)
    assert result["summary"]["submodelCount"] == 1
    row = result["submodels"][0]
    assert row["modelClass"] == "reduced_3d_solid_contact_screen"
    assert row["designForceKn"] == 6500.0
    assert row["results"]["maxContactPressureMpa"] > 0.0
    assert row["mesh"]["solidElementCount"] > 0
    assert row["designVariants"]
    assert row["solverDeckFilename"].endswith(".inp")
    deck = node_submodel.build_calculix_input_deck(row)
    assert "*CONTACT PAIR" in deck
    assert "*STEP, NLGEOM=YES" in deck


def test_crane_logistics_uses_capacity_curve_and_returns_ranked_stand(monkeypatch) -> None:
    monkeypatch.setattr(crane_logistics, "build_rebar_detailing", lambda project, mode="balanced": {
        "deepDetailing": {"cageHoisting": [{
            "segmentId": "CAGE-1", "hostCode": "W1", "weightT": 8.0, "lengthM": 10.0,
        }]}
    })
    monkeypatch.setattr(crane_logistics, "_load_cranes", lambda project=None: [{
        "id": "TEST-120", "name": "测试120t履带吊", "maxBoomLengthM": 60.0,
        "groundPressureKpa": 110.0,
        "capacityCurve": [[5.0, 40.0], [15.0, 20.0], [30.0, 10.0]],
    }])
    result = crane_logistics.optimize_cage_crane_logistics(_project())
    assert result["summary"]["caseCount"] == 1
    case = result["cases"][0]
    assert case["recommended"]["craneId"] == "TEST-120"
    assert case["recommended"]["workingRadiusM"] > 0
    assert case["recommended"]["availableCapacityT"] > 0
    assert len(case["alternatives"]) >= 1
    assert "groundUtilization" in case["recommended"]
    assert "windUtilization" in case["recommended"]
    assert "swingOverPitM" in case["recommended"]
