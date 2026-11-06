from __future__ import annotations

from app.schemas.domain import Point2D, Polyline2D, Project, StabilityDetailedResult, SupportElement, SupportWaleNode, BearingPlateDesign
from app.services.adverse_scenarios import build_adverse_scenario_screening
from app.services.core_engineering_presentation import build_verification_distribution
from app.services.core_workspace import build_core_workspace_status
from app.services.design_basis import build_design_basis
from app.services.design_service import auto_diaphragm_wall
from app.services.engineering_templates import ensure_design_basis_defaults
from app.services.excavation_service import make_excavation_model
from app.services.local_node_submodel import build_local_node_submodel_checks
from app.services.pareto_scheme import apply_pareto_ranking
from app.services.rebar_constructability import build_rebar_constructability
from app.services.section_catalog import load_steel_support_catalog, recommend_steel_support_profiles


def _project() -> Project:
    excavation = make_excavation_model(
        "v346",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=36, y=0), Point2D(x=36, y=20), Point2D(x=0, y=20)], closed=True),
        0.0,
        -11.0,
    )
    project = Project(name="v346", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))
    project.design_settings.design_basis_confirmed = True
    project.design_settings.bearing_capacity_kpa = 180
    return project


def test_old_project_receives_unconfirmed_transparent_defaults() -> None:
    project = _project()
    project.design_settings.design_basis_confirmed = False
    project.design_settings.action_group_catalog = []
    migration = ensure_design_basis_defaults(project)
    assert migration["requiresConfirmation"] is True
    assert project.design_settings.design_basis_confirmed is False
    assert len(project.design_settings.action_group_catalog) >= 6
    assert project.design_settings.safety_factor_overrides["strength"] >= 1.05


def test_design_basis_exposes_templates_actions_targets_and_analysis_contract() -> None:
    basis = build_design_basis(_project())
    assert len(basis["templateCatalog"]) == 3
    assert any(row["id"] == "temperature" for row in basis["actionGroups"])
    assert basis["safetyTargets"]["support_stability"] >= 1.1
    assert basis["analysisModel"]["model"] == "engineering_spatial"


def test_pareto_ranking_separates_non_dominated_alternatives() -> None:
    rows = [
        {"candidateId": "A", "maxDisplacement": 12, "maxSupportAxialForce": 4000, "maxWaleMoment": 1000, "supportCount": 40, "columnCount": 8, "maxSpanLength": 20, "warningCount": 1},
        {"candidateId": "B", "maxDisplacement": 10, "maxSupportAxialForce": 4300, "maxWaleMoment": 950, "supportCount": 44, "columnCount": 7, "maxSpanLength": 18, "warningCount": 0},
        {"candidateId": "C", "maxDisplacement": 16, "maxSupportAxialForce": 4800, "maxWaleMoment": 1250, "supportCount": 48, "columnCount": 10, "maxSpanLength": 24, "warningCount": 4},
    ]
    apply_pareto_ranking(rows)
    assert rows[2]["paretoRank"] > 1
    assert any(row["paretoFront"] for row in rows[:2])
    assert all("materialIndex" in row for row in rows)


def test_adverse_scenario_screening_covers_water_overexcavation_and_seepage() -> None:
    project = _project()
    stability = StabilityDetailedResult(heaveFactor=1.35, confinedUpliftFactor=1.30, seepageFactor=1.25, overallStabilityFactor=1.28)
    result = build_adverse_scenario_screening(project, stability, max_displacement_mm=12, max_support_force_kn=4200)
    codes = {row["code"] for row in result["scenarios"]}
    assert {"DEWATERING_FAILURE", "OVEREXCAVATION", "LOCAL_SEEPAGE", "CONFINED_HEAD_RISE"}.issubset(codes)
    assert result["summary"]["count"] >= 4


def test_rebar_constructability_adds_congestion_coupler_and_anchor_checks() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    scheme = {
        "wallZones": [{
            "zoneId": "Z1", "hostId": wall.id, "hostCode": wall.panel_code, "heightM": 3.0,
            "faces": [{"face": "inner", "barDiameterMm": 32, "barSpacingMm": 100, "layerCount": 2, "clearSpacingMm": 68}],
        }],
        "supportSchemes": [],
    }
    result = build_rebar_constructability(project, scheme)
    categories = {row.get("category") for row in result["checks"]}
    assert {"rebar_congestion", "mechanical_coupler", "anchorage", "lap_splice"}.issubset(categories)
    assert result["summary"]["checkCount"] >= 4


def test_local_node_submodel_produces_reviewable_utilization() -> None:
    project = _project()
    support = SupportElement(code="S1", levelIndex=1, elevation=-3, start=Point2D(x=0, y=10), end=Point2D(x=36, y=10), designAxialForce=3500)
    project.retaining_system.supports = [support]
    project.retaining_system.support_nodes = [SupportWaleNode(
        code="N1", supportId=support.id, supportCode=support.code, levelIndex=1, elevation=-3,
        location=Point2D(x=0, y=10), bearingPlate=BearingPlateDesign(plateWidth=1.0, plateHeight=1.0, plateThickness=0.05, bearingArea=1.0, bearingCapacity=14.0),
    )]
    checks = build_local_node_submodel_checks(project)
    assert len(checks) == 1
    assert checks[0]["implementationState"] == "screening_implemented"
    assert checks[0]["calculatedValue"] is not None


def test_steel_profile_catalog_is_available_to_engineering_workflow() -> None:
    catalog = load_steel_support_catalog()
    assert len(catalog["profiles"]) >= 4
    assert recommend_steel_support_profiles(required_area_mm2=20000, limit=2)


def test_core_status_exposes_section_catalog_and_evidence_coverage() -> None:
    status = build_core_workspace_status(_project())
    assert status["sectionCatalog"]["profileCount"] >= 4
    assert "evidenceCoverage" in status["verificationDistribution"]["summary"]
