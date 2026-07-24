from __future__ import annotations

from app.geometry.wall_path import project_point_to_polyline
from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model


def _project() -> Project:
    outline = [
        (0.0, 0.0),
        (78.0, 0.0),
        (78.0, 18.0),
        (52.0, 18.0),
        (52.0, 30.0),
        (32.0, 30.0),
        (32.0, 18.0),
        (0.0, 18.0),
    ]
    excavation = make_excavation_model(
        "wall-cage-geometry",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in outline], closed=True),
        0.0,
        -15.0,
    )
    return Project(name="wall-cage-geometry", excavation=excavation, retainingSystem=auto_diaphragm_wall(excavation))


def test_rebar_cages_ignore_stale_panel_endpoint_coordinates() -> None:
    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    assert wall.construction_panels
    # Simulate a saved panel schedule from an older excavation outline.
    for panel in wall.construction_panels:
        panel["start"] = {"x": 500.0, "y": 500.0}
        panel["end"] = {"x": 510.0, "y": 500.0}

    payload = build_rebar_ifc_visualization(project, max_bars=1200)
    cages = [row for row in payload["cages"] if row["hostId"] == wall.id]
    assert cages
    segment = next(row for row in project.excavation.segments if row.id == wall.segment_id)
    canonical = [segment.start, segment.end]
    for cage in cages:
        assert len(cage["planPath"]) >= 2
        for raw in cage["planPath"]:
            point = Point2D(x=raw["x"], y=raw["y"])
            _, deviation, _ = project_point_to_polyline(point, canonical)
            assert deviation <= 1.0e-6
        assert cage["geometryStatus"] == "repaired"
    assert payload["summary"]["repairedPanelGeometryCount"] == len(cages)
    assert payload["summary"]["wallPanelGeometryMismatchCount"] >= 1
    assert payload["summary"]["maximumPanelGeometryDeviationM"] > 100.0


def test_regenerating_walls_rebases_existing_panel_geometry_to_current_segments() -> None:
    project = _project()
    original = project.retaining_system
    wall = original.diaphragm_walls[0]
    codes = [row["panelCode"] for row in wall.construction_panels]
    for panel in wall.construction_panels:
        panel["start"] = {"x": -80.0, "y": -40.0}
        panel["end"] = {"x": -70.0, "y": -40.0}

    regenerated = auto_diaphragm_wall(project.excavation, existing_system=original, settings=project.design_settings)
    refreshed = regenerated.diaphragm_walls[0]
    assert [row["panelCode"] for row in refreshed.construction_panels] == codes
    segment = next(row for row in project.excavation.segments if row.id == refreshed.segment_id)
    canonical = [segment.start, segment.end]
    for panel in refreshed.construction_panels:
        for key in ("start", "end"):
            point = Point2D(**panel[key])
            _, deviation, _ = project_point_to_polyline(point, canonical)
            assert deviation <= 1.0e-6
        assert panel["geometrySource"] == "canonical_wall_path_chainage"


def test_applying_rebar_scheme_persists_canonical_panel_geometry() -> None:
    from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme

    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    for panel in wall.construction_panels:
        panel["start"] = {"x": 900.0, "y": 900.0}
        panel["end"] = {"x": 906.0, "y": 900.0}
    scheme = apply_rebar_design_scheme(project, mode="balanced")
    sync = scheme["wallPlanGeometrySynchronization"]
    assert sync["status"] == "auto_repaired"
    assert sync["repairedConstructionPanelCount"] == len(wall.construction_panels)
    segment = next(row for row in project.excavation.segments if row.id == wall.segment_id)
    for panel in wall.construction_panels:
        for key in ("start", "end"):
            _, deviation, _ = project_point_to_polyline(Point2D(**panel[key]), [segment.start, segment.end])
            assert deviation <= 1.0e-6


def test_ifc_panel_spans_use_current_wall_path_not_saved_endpoints() -> None:
    from app.ifc.exporter import _wall_construction_spans

    project = _project()
    wall = project.retaining_system.diaphragm_walls[0]
    for panel in wall.construction_panels:
        panel["start"] = {"x": 321.0, "y": 654.0}
        panel["end"] = {"x": 327.0, "y": 654.0}
    spans = _wall_construction_spans(project, wall)
    assert spans
    segment = next(row for row in project.excavation.segments if row.id == wall.segment_id)
    for panel in spans:
        for point in panel["planPath"]:
            _, deviation, _ = project_point_to_polyline(point, [segment.start, segment.end])
            assert deviation <= 1.0e-6
