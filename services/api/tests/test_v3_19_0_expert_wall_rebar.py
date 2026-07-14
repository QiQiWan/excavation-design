from __future__ import annotations

import json
from pathlib import Path

from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import (
    DiaphragmWallPanel,
    ExcavationModel,
    Point2D,
    Polyline2D,
    Project,
    ReinforcementGroup,
    RetainingSystem,
    WallDesignResult,
)
from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
from app.services.design_expert import build_expert_design_review
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.rebar_detailing import build_rebar_mark_entries
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.services.support_layout import SupportLayoutConfig
from app.services.wall_vertical_length_optimizer import analyze_wall_vertical_length
from app.version import SOFTWARE_VERSION


def _long_wall_project() -> Project:
    wall = DiaphragmWallPanel(
        segmentId="S-LONG",
        panelCode="DW-LONG",
        axis=Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=100, y=0)], closed=False),
        designLength=20.0,
        thickness=1.2,
        topElevation=0.0,
        bottomElevation=-28.0,
        reinforcement=[
            ReinforcementGroup(name="坑内侧竖向主筋", barType="longitudinal", diameter=22, spacing=200, grade="HRB400", locationDescription="inner face", checkStatus="pass"),
            ReinforcementGroup(name="坑外侧竖向主筋", barType="longitudinal", diameter=22, spacing=200, grade="HRB400", locationDescription="outer face", checkStatus="pass"),
            ReinforcementGroup(name="水平分布筋", barType="distribution", diameter=16, spacing=200, grade="HRB400", locationDescription="two faces", checkStatus="pass"),
        ],
    )
    project = Project(
        name="long-wall-density",
        excavation=ExcavationModel(
            name="pit",
            outline=Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=100, y=0), Point2D(x=100, y=20), Point2D(x=0, y=20)], closed=True),
            topElevation=0.0,
            bottomElevation=-16.0,
            depth=16.0,
        ),
        retainingSystem=RetainingSystem(diaphragmWalls=[wall]),
    )
    project.retaining_system.rebar_design_scheme = {
        "wallZones": [{
            "zoneId": "WZ-LONG-01", "hostId": wall.id, "hostCode": wall.panel_code,
            "zoneType": "field_zone", "topElevation": 0.0, "bottomElevation": -28.0,
            "faces": [
                {"face": "inner", "barDiameterMm": 22, "barSpacingMm": 200, "providedAsMm2PerM": 1900, "requiredAsMm2PerM": 1500, "status": "pass", "token": "HRB400 D22@200"},
                {"face": "outer", "barDiameterMm": 22, "barSpacingMm": 200, "providedAsMm2PerM": 1900, "requiredAsMm2PerM": 1500, "status": "pass", "token": "HRB400 D22@200"},
            ],
            "horizontalDistribution": {"diameterMm": 16, "spacingMm": 200},
            "tieBars": {"diameterMm": 12, "spacingMm": 450},
            "drawingRefs": ["R-01", "R-02"],
        }]
    }
    return project


def _actual_project() -> Project:
    root = Path(__file__).resolve().parents[3] / "packages" / "sample-data" / "actual-project"
    imported = parse_borehole_rows(
        read_csv_bytes((root / "actual_project_boreholes_24x6layers.csv").read_bytes()),
        source_file="actual_project_boreholes_24x6layers.csv",
    )
    assert imported.success
    payload = json.loads((root / "actual_project_excavation_payload.json").read_text(encoding="utf-8"))
    project = Project(name="V3.19 expert regression")
    project.boreholes = imported.boreholes
    project.strata = imported.strata
    project.design_settings.groundwater_level = -20.0
    project.design_settings.support_level_depths_m = [0.0, 4.0, 7.2, 10.3, 13.3]
    project.excavation = make_excavation_model(
        payload["name"],
        Polyline2D(points=[Point2D(**item) for item in payload["outline"]["points"]], closed=True),
        0.0, -16.6, 0.5,
    )
    project.retaining_system = auto_supports(
        project.excavation,
        auto_diaphragm_wall(project.excavation),
        SupportLayoutConfig(support_level_depths_m=(0.0, 4.0, 7.2, 10.3, 13.3)),
    )
    return project


def test_long_wall_schedule_uses_physical_axis_length_not_short_design_segment() -> None:
    entries = build_rebar_mark_entries(_long_wall_project())
    vertical = [row for row in entries if row["barType"] == "longitudinal"]
    assert len(vertical) == 2
    assert all(row["quantity"] == 501 for row in vertical)


def test_long_wall_browser_visualization_is_not_capped_at_five_bars_per_face() -> None:
    payload = build_rebar_ifc_visualization(_long_wall_project(), max_bars=500)
    wall_host = next(row for row in payload["hosts"] if row["hostCode"] == "DW-LONG")
    assert wall_host["sampledBarCount"] >= 20
    wall_vertical = [bar for bar in payload["bars"] if bar["hostCode"] == "DW-LONG" and bar["barType"] == "longitudinal"]
    assert len(wall_vertical) >= 16


def test_actual_project_wall_rebar_has_depth_and_plan_direction_zones() -> None:
    project = _actual_project()
    scheme = build_rebar_design_scheme(project, mode="balanced")
    assert scheme["summary"]["twoDirectionWallZoning"] is True
    assert scheme["summary"]["wallPlanZoneCount"] > 0
    assert any(row["zoneType"] == "support_node_plan_zone" for row in scheme["wallPlanZones"])
    long_walls = [wall for wall in project.retaining_system.diaphragm_walls if sum(((b.x-a.x)**2+(b.y-a.y)**2)**0.5 for a,b in zip(wall.axis.points[:-1], wall.axis.points[1:])) >= 60]
    assert long_walls
    for wall in long_walls:
        assert [row for row in scheme["wallPlanZones"] if row["hostId"] == wall.id]



def test_long_wall_base_cage_has_design_reserve_and_does_not_relax_existing_spacing() -> None:
    project = _long_wall_project()
    wall = project.retaining_system.diaphragm_walls[0]
    wall.reinforcement = [
        ReinforcementGroup(name="坑内侧竖向主筋", barType="longitudinal", diameter=22, spacing=150, areaPerMeter=2534.22, grade="HRB400", locationDescription="inner face", checkStatus="preliminary"),
        ReinforcementGroup(name="坑外侧竖向主筋", barType="longitudinal", diameter=22, spacing=150, areaPerMeter=2534.22, grade="HRB400", locationDescription="outer face", checkStatus="preliminary"),
    ]
    wall.design_results = WallDesignResult(
        maxMoment=714.0, maxMomentDesign=892.0, requiredReinforcementArea=2400.0,
        providedReinforcementArea=2534.22, checkStatus="pass",
    )
    scheme = build_rebar_design_scheme(project, mode="balanced")
    faces = [face for zone in scheme["wallZones"] if zone["hostId"] == wall.id for face in zone["faces"]]
    assert faces
    assert all(float(face["utilization"]) <= 0.88 + 1e-9 for face in faces)
    assert all(float(face["barSpacingMm"]) <= 150.0 for face in faces)
    assert all(float(face["providedAsMm2PerM"]) >= 2534.22 for face in faces)
    assert all(face["noDowngradeExistingCage"] is True for face in faces)
    standard_diameters = {12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40}
    assert all(int((zone.get("additionalReinforcement") or {}).get("diameterMm") or 16) in standard_diameters for zone in scheme["wallPlanZones"])

def test_imported_wall_toe_is_never_auto_shortened() -> None:
    project = _actual_project()
    for wall in project.retaining_system.diaphragm_walls:
        wall.bottom_elevation = -32.8
        wall.bottom_elevation_source = "imported"
        wall.bottom_elevation_locked = True
        wall.source_bottom_elevation = -32.8
    analysis = analyze_wall_vertical_length(project, mode="economic")
    assert analysis["summary"]["lockedWallCount"] == len(project.retaining_system.diaphragm_walls)
    assert all(float(candidate["estimatedConcreteSavingM3"]) == 0.0 for candidate in analysis["candidates"])
    assert all(all(float(zone["bottomElevationM"]) <= -32.8 + 1e-9 for zone in candidate["zones"]) for candidate in analysis["candidates"])


def test_expert_review_couples_support_rebar_and_vertical_wall_length() -> None:
    review = build_expert_design_review(_actual_project(), mode="balanced")
    assert review["supportSystem"]["preferredTopology"]
    assert review["wallReinforcement"]["wallCount"] > 0
    assert review["wallVerticalLength"]["candidates"]
    assert len(review["requiredSequence"]) >= 8


def test_v319_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 19, 0)
