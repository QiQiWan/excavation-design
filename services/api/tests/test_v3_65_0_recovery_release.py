from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.calculation.earth_pressure import calculate_lateral_pressure_profile
from app.calculation.support_forces import estimate_support_axial_forces
from app.routers.projects import _build_updated_project
from app.rules.gb50010.detailing_rules import (
    required_rebar_anchorage_length_mm,
    required_rebar_lap_length_mm,
)
from app.rules.gb50010.rc_section_rules import design_rectangular_shear_reinforcement
from app.schemas.domain import (
    CalculationResult,
    GeologicalLayer,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    Project,
    SectionDefinition,
    SoilParameters,
    SupportElement,
)
from app.services.design_service import auto_diaphragm_wall
from app.services.engineering_templates import enforce_safety_target_floors, safety_targets
from app.services.excavation_service import make_excavation_model
from app.services.rebar_constructability import build_rebar_constructability
from app.services.rebar_scheme_optimizer import _design_support_stirrups, _optimize_support_section
from app.services.runtime_resource_policy import adaptive_resource_policy
from app.services.wall_restraint import build_effective_wall_restraints
from app.storage.artifact_store import ProjectArtifactStore, rehydrate_geological_evidence


def _rectangular_project() -> Project:
    excavation = make_excavation_model(
        "V3.65 recovery",
        Polyline2D(
            points=[
                Point2D(x=0, y=0),
                Point2D(x=28, y=0),
                Point2D(x=28, y=18),
                Point2D(x=0, y=18),
            ],
            closed=True,
        ),
        0.0,
        -12.0,
    )
    return Project(
        name="V3.65 recovery",
        excavation=excavation,
        retainingSystem=auto_diaphragm_wall(excavation),
    )


def test_large_support_contract_matches_total_and_single_side_minimums() -> None:
    longitudinal = _optimize_support_section(
        force=8000.0,
        width=1.6,
        height=1.6,
        concrete_grade="C35",
        rebar_grade="HRB400",
        role="main_strut",
        mode="balanced",
    )
    assert longitudinal["count"] == 24
    assert longitudinal["diameterMm"] == 32
    assert longitudinal["steelRatio"] == pytest.approx(0.00754, rel=1.0e-3)
    assert longitudinal["singleSideSteelRatio"] == pytest.approx(0.00220, rel=1.0e-3)

    transverse = _design_support_stirrups(
        axial_force_kn=8000.0,
        eccentricity_moment_knm=0.0,
        span_m=28.0,
        width_m=1.6,
        height_m=1.6,
        concrete_grade="C35",
        rebar_grade="HRB400",
        longitudinal_diameter_mm=32.0,
        axial_utilization=float(longitudinal["utilization"]),
        mode="balanced",
        has_formal_stage_force=True,
    )
    assert transverse["geometricLegCount"] == 8
    assert transverse["endZone"]["token"] == "D14@120"
    assert transverse["middleZone"]["token"] == "D14@180"


def test_wale_shear_capacity_includes_six_leg_stirrups() -> None:
    result = design_rectangular_shear_reinforcement(
        9650.9,
        2.4,
        2.6,
        "C35",
        "HRB400",
    )
    assert result["status"] == "pass"
    assert result["legCount"] == 6
    assert result["diameterMm"] == 10
    assert result["spacingMm"] == 140
    assert result["totalShearCapacity"] == pytest.approx(
        result["concreteShearCapacity"] + result["stirrupShearCapacity"],
        abs=0.002,
    )
    assert result["totalShearCapacity"] >= 9650.9


def test_wall_anchor_lap_contract_uses_full_cage_not_one_metre_zone() -> None:
    project = _rectangular_project()
    wall = project.retaining_system.diaphragm_walls[0]
    scheme = {
        "wallZones": [
            {
                "zoneId": "Z-1",
                "hostId": wall.id,
                "hostCode": wall.panel_code,
                "heightM": 1.0,
                "faces": [
                    {
                        "face": "inner",
                        "barDiameterMm": 22,
                        "barSpacingMm": 150,
                        "layerCount": 1,
                        "clearSpacingMm": 128,
                    }
                ],
            }
        ]
    }
    result = build_rebar_constructability(project, scheme)
    anchor = next(item for item in result["checks"] if item.get("category") == "anchorage")
    lap = next(item for item in result["checks"] if item.get("category") == "lap_splice")
    assert required_rebar_anchorage_length_mm(22, "HRB400") == 770.0
    assert required_rebar_lap_length_mm(22, "HRB400") == 924.0
    assert anchor["status"] == "pass"
    assert lap["status"] == "pass"
    assert anchor["lengthContractSource"] == "full_wall_cage"
    assert anchor["analysisZoneHeightMm"] == 1000.0
    assert anchor["wallCageHeightMm"] > anchor["analysisZoneHeightMm"]


def test_safety_targets_are_maximum_of_level_enterprise_and_project() -> None:
    project = Project(name="safety floor")
    project.design_settings.excavation_safety_level = "二级"
    project.design_settings.safety_factor_overrides = {
        "strength": 1.01,
        "overall_stability": 1.02,
    }
    audit = enforce_safety_target_floors(project, actor="regression")
    targets = safety_targets(project)
    assert targets["strength"] >= 1.10
    assert targets["overall_stability"] >= 1.25
    assert project.design_settings.safety_factor_overrides["overall_stability"] >= 1.25
    assert audit["adjusted"]
    assert project.advanced_engineering["safetyTargetEnforcementAudit"][-1]["actor"] == "regression"


def test_project_patch_fallback_keeps_result_invalidation() -> None:
    project = Project(name="invalidate")
    project.calculation_results = [CalculationResult(projectId=project.id, caseId="case-old")]
    settings = project.design_settings.model_dump(mode="json", by_alias=True)
    settings["surcharge"] = float(settings.get("surcharge") or 20.0) + 5.0
    updated, changed = _build_updated_project(project, {"designSettings": settings}, actor="regression")
    assert changed == ["designSettings"]
    assert updated.calculation_results == []
    assert updated.advanced_engineering["requiresRecalculation"] is True
    assert updated.advanced_engineering["invalidationReason"]["keys"] == ["designSettings"]


def test_adaptive_api_policy_respects_legacy_admin_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.runtime_resource_policy as policy_module

    monkeypatch.setenv("PITGUARD_RESOURCE_POLICY_MODE", "adaptive")
    monkeypatch.setenv("PITGUARD_API_FULL_PROJECT_LIMIT_MB", "96")
    monkeypatch.setattr(
        policy_module,
        "runtime_memory_snapshot",
        lambda: {
            "hostTotalBytes": 32 * 1024**3,
            "hostAvailableBytes": 24 * 1024**3,
            "cgroupLimitBytes": None,
            "cgroupCurrentBytes": None,
            "effectiveTotalBytes": 32 * 1024**3,
            "effectiveAvailableBytes": 24 * 1024**3,
            "processRssBytes": 512 * 1024**2,
            "processEffectiveBytes": 512 * 1024**2,
            "cpuCount": 16,
            "loadAverage1m": 0.5,
            "loadAverage5m": 0.5,
            "loadAverage15m": 0.5,
            "diskRoot": "/tmp",
            "diskTotalBytes": 200 * 1024**3,
            "diskUsedBytes": 20 * 1024**3,
            "diskFreeBytes": 180 * 1024**3,
        },
    )
    result = adaptive_resource_policy(role="api")
    assert result["apiFullLoadLimitBytes"] <= 96 * 1024**2


def test_calculation_geology_rehydrates_full_surfaces(tmp_path) -> None:
    store = ProjectArtifactStore(tmp_path / "artifacts")
    surfaces = [{"id": "surface-1", "grid": {"xValues": [0, 1], "yValues": [0, 1], "zValues": [[0, 0], [0, 0]]}}]
    ref = store.write_json("project-1", "geology-surfaces", surfaces)
    ref["storageKey"] = "geology:surfaces"
    project = {
        "id": "project-1",
        "geologicalModel": {"surfaces": [], "volumes": [], "vtuMesh": None},
        "advancedEngineering": {"artifactStorage": {"artifacts": [ref]}},
    }
    hydrated = rehydrate_geological_evidence(project, store)
    assert hydrated["geologicalModel"]["surfaces"] == surfaces
    assert hydrated["advancedEngineering"]["calculationGeologyEvidence"]["state"] == "loaded"


def test_short_return_proxy_generates_formal_wale_evidence() -> None:
    a = SimpleNamespace(name="A", start=Point2D(x=0, y=0), end=Point2D(x=10, y=0), length=10.0)
    b = SimpleNamespace(name="B", start=Point2D(x=10, y=0), end=Point2D(x=15, y=0), length=5.0, midpoint=Point2D(x=12.5, y=0), outward_normal=Point2D(x=0, y=-1))
    c = SimpleNamespace(name="C", start=Point2D(x=15, y=0), end=Point2D(x=15, y=10), length=10.0)
    excavation = SimpleNamespace(segments=[a, b, c])
    section = SectionDefinition(width=1.2, height=1.2, name="1200x1200 RC")
    material = MaterialDefinition(name="Concrete", grade="C35")
    previous = SupportElement(
        code="SP-A-L1", levelIndex=1, elevation=-2.0,
        start=Point2D(x=10, y=0), end=Point2D(x=10, y=20),
        startFaceCode="A", startWallConnection=Point2D(x=10, y=0),
        section=section, material=material,
    )
    following = SupportElement(
        code="SP-C-L1", levelIndex=1, elevation=-2.0,
        start=Point2D(x=15, y=0), end=Point2D(x=0, y=0),
        startFaceCode="C", startWallConnection=Point2D(x=15, y=0),
        section=section, material=material,
    )
    proxies, audit = build_effective_wall_restraints(excavation, b, [previous, following])
    assert audit["status"] == "pass"
    assert audit["analyticalTransferLevels"] == [1]
    assert len(proxies) == 1

    layer = GeologicalLayer(
        stratumCode="S", stratumName="sand", topElevation=0.0, bottomElevation=-12.0, thickness=12.0,
        parameters=SoilParameters(unitWeight=18.0, cohesion=0.0, frictionAngle=30.0),
    )
    profile = calculate_lateral_pressure_profile(
        [layer], excavation_depth=8.0, groundwater_level=-100.0, surcharge=20.0, top_elevation=0.0, step=1.0,
    )
    collector: list[object] = []
    estimate_support_axial_forces(
        profile, proxies, b.length, 0.0, -8.0,
        segment_name="B", segment=b, stage_id="stage-1", wale_result_collector=collector,
    )
    assert len(collector) == 1
    assert collector[0].wale_beam_code == "WB-L1-B"
    assert collector[0].max_moment > 0.0

