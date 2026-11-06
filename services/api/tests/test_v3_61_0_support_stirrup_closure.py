from __future__ import annotations

from pathlib import Path

import pytest

from app.ifc.rebar_visualization import (
    _stratified_stirrup_zone_sample,
    build_rebar_ifc_visualization,
)
from app.schemas.domain import Point2D, Polyline2D, Project, ReinforcementGroup
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.rebar_scheme_optimizer import (
    _design_support_stirrups,
    apply_rebar_design_scheme,
    rebar_scheme_is_current,
)


def _project() -> Project:
    excavation = make_excavation_model(
        "V3.61 水平支撑箍筋闭环测试",
        Polyline2D(
            points=[
                Point2D(x=0, y=0), Point2D(x=28, y=0),
                Point2D(x=28, y=18), Point2D(x=0, y=18),
            ],
            closed=True,
        ),
        0.0,
        -9.0,
    )
    retaining = auto_supports(excavation, auto_diaphragm_wall(excavation))
    project = Project(name="V3.61 水平支撑箍筋闭环测试", excavation=excavation, retainingSystem=retaining)
    assert project.retaining_system is not None
    support = max(project.retaining_system.supports, key=lambda item: float(item.span_length or 0.0))
    project.retaining_system.supports = [support]
    support.design_axial_force = 4200.0
    support.eccentricity_moment = 180.0
    return project


def test_support_transverse_design_is_traceable_and_zoned() -> None:
    result = _design_support_stirrups(
        axial_force_kn=4200.0,
        eccentricity_moment_knm=180.0,
        span_m=18.0,
        width_m=0.8,
        height_m=0.9,
        concrete_grade="C35",
        rebar_grade="HRB400",
        longitudinal_diameter_mm=28,
        axial_utilization=0.72,
        mode="balanced",
        has_formal_stage_force=True,
    )
    assert result["evidenceStatus"] == "calculated_stage_envelope"
    assert result["designShearKn"] > 0
    assert result["totalCapacityKn"] >= result["designShearKn"]
    assert result["effectiveLegCount"] == 2
    assert result["geometricLegCount"] == 4
    assert result["endZone"]["lengthM"] > 0
    assert result["middleZone"]["lengthM"] > 0
    assert result["endZone"]["spacingMm"] <= result["middleZone"]["spacingMm"]
    assert "0.7*ft*b*h0" in result["formula"]


def test_apply_scheme_persists_distinct_end_and_middle_stirrup_groups() -> None:
    project = _project()
    scheme = apply_rebar_design_scheme(project, mode="balanced")
    support = project.retaining_system.supports[0]
    stirrups = [group for group in support.reinforcement if group.bar_type == "stirrup"]
    assert {group.zone_type for group in stirrups} == {"end_zones", "middle_zone"}
    assert all(group.design_source == "support_transverse_design" for group in stirrups)
    assert all((group.stirrup_legs or 0) == 4 for group in stirrups)
    row = scheme["supportSchemes"][0]
    assert row["transverseDesign"]["designShearKn"] > 0
    assert row["rebarContract"]["stirrupZoneStatus"] == "complete"


def test_single_support_preview_contains_three_non_overlapping_stirrup_regions() -> None:
    project = _project()
    apply_rebar_design_scheme(project, mode="balanced")
    support = project.retaining_system.supports[0]
    payload = build_rebar_ifc_visualization(project, max_bars=520, focus_host_id=support.id)
    stirrups = [row for row in payload["bars"] if row["barType"] == "stirrup"]
    zones = {row.get("stirrupZoneType") for row in stirrups}
    assert zones == {"end_left", "middle", "end_right"}
    contract = payload["supportContracts"][0]
    assert contract["stirrupPreviewStatus"] == "complete"
    assert contract["missingSampledStirrupZones"] == []
    end_limit = contract["transverseDesign"]["endZone"]["lengthM"]
    span = project.retaining_system.rebar_design_scheme["supportSchemes"][0]["spanM"]
    assert max(row["previewStationM"] for row in stirrups if row["stirrupZoneType"] == "end_left") <= end_limit + 1e-6
    assert min(row["previewStationM"] for row in stirrups if row["stirrupZoneType"] == "end_right") >= span - end_limit - 1e-6


def test_legacy_generic_stirrup_does_not_fake_zone_completion() -> None:
    project = _project()
    support = project.retaining_system.supports[0]
    support.reinforcement = [
        ReinforcementGroup(name="旧版支撑纵筋", barType="longitudinal", diameter=28, count=12, grade="HRB400", locationDescription="旧版全长纵筋"),
        ReinforcementGroup(name="旧版通长箍筋", barType="stirrup", diameter=12, spacing=150, grade="HRB400", locationDescription="旧版未分区箍筋"),
    ]
    project.retaining_system.rebar_design_scheme = {}
    payload = build_rebar_ifc_visualization(project, max_bars=120, focus_host_id=support.id)
    contract = payload["supportContracts"][0]
    assert contract["stirrupZoneStatus"] == "generic_or_incomplete"
    assert set(contract["missingStirrupZones"]) == {"end_left", "middle", "end_right"}
    assert contract["status"] == "incomplete"


def test_short_support_does_not_create_a_rounding_only_middle_zone() -> None:
    project = _project()
    support = project.retaining_system.supports[0]
    support.end = Point2D(x=support.start.x + 2.1, y=support.start.y)
    support.span_length = 2.1
    apply_rebar_design_scheme(project, mode="balanced")
    payload = build_rebar_ifc_visualization(project, max_bars=200, focus_host_id=support.id)
    zones = {row.get("stirrupZoneType") for row in payload["bars"] if row["barType"] == "stirrup"}
    assert zones == {"end_left", "end_right"}
    assert payload["supportContracts"][0]["requiredStirrupZones"] == ["end_left", "end_right"]


def test_zone_sampler_reserves_a_middle_and_b_end_preview() -> None:
    rows = []
    for zone in ("end_left", "middle", "end_right"):
        for index in range(12):
            rows.append({"id": f"{zone}-{index}", "hostId": "support-1", "barType": "stirrup", "stirrupZoneType": zone})
    sampled = _stratified_stirrup_zone_sample(rows, 6)
    assert {row["stirrupZoneType"] for row in sampled} == {"end_left", "middle", "end_right"}


def test_frontend_exposes_single_support_stirrup_workflow_in_engineering_language() -> None:
    root = Path(__file__).resolve().parents[3]
    viewer = (root / "apps/web/src/viewers/RebarIfcViewer.tsx").read_text(encoding="utf-8")
    client = (root / "apps/web/src/api/client.ts").read_text(encoding="utf-8")
    styles = (root / "apps/web/src/app/styles.css").read_text(encoding="utf-8")
    for text in ("仅看该支撑箍筋", "A端加密区", "跨中普通区", "B端加密区", "斜截面受剪验算"):
        assert text in viewer
    assert "focusHostId" in client
    assert ".supportStirrupDesignCard" in styles
    assert "Fail</span>" not in viewer


def test_stale_applied_support_scheme_is_regenerated_for_current_topology() -> None:
    project = _project()
    support = project.retaining_system.supports[0]
    project.retaining_system.rebar_design_scheme = {
        "mode": "balanced",
        "supportSchemes": [{"hostId": "old-support-id", "hostCode": "OLD-SUPPORT", "status": "warning"}],
    }
    support.reinforcement = [
        ReinforcementGroup(name="旧版支撑纵筋", barType="longitudinal", diameter=28, count=12, grade="HRB400", locationDescription="旧版")
    ]
    payload = build_rebar_ifc_visualization(project, max_bars=220, focus_host_id=support.id)
    contract = payload["supportContracts"][0]
    assert contract["schemeRowFound"] is True
    assert contract["status"] == "complete"
    assert set(contract["sampledStirrupZones"]) == {"end_left", "middle", "end_right"}
    assert payload["summary"]["regeneratedSupportSchemeCount"] == 1


def test_invalid_wall_axis_is_recovered_from_excavation_segment() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    wall.axis = Polyline2D(points=[], closed=False)
    payload = build_rebar_ifc_visualization(project, max_bars=300)
    assert payload["summary"]["expectedWallHostCount"] == len(project.retaining_system.diaphragm_walls)
    assert payload["summary"]["representedWallHostCount"] == len(project.retaining_system.diaphragm_walls)
    assert payload["summary"]["repairedWallAxisCount"] >= 1
    assert wall.panel_code not in payload["summary"]["missingWallHostCodes"]


def test_stored_scheme_is_rejected_after_support_topology_changes() -> None:
    project = _project()
    project.advanced_engineering = {
        "calculationState": {"status": "current", "resultId": "calc-current"}
    }
    scheme = apply_rebar_design_scheme(project, mode="balanced")
    scheme["sourceCalculation"]["resultId"] = "calc-current"
    assert rebar_scheme_is_current(project, scheme, "balanced") is True
    support = project.retaining_system.supports[0]
    support.end = Point2D(x=support.end.x + 0.5, y=support.end.y)
    assert rebar_scheme_is_current(project, scheme, "balanced") is False
