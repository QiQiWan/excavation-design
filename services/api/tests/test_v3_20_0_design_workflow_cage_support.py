from __future__ import annotations

import pytest

from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import ExcavationModel, Point2D, Polyline2D, Project
from app.services.design_pipeline import evaluate_design_pipeline
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.services.support_layout import SupportLayoutConfig, generate_support_layout_lines
from app.services.support_layout_optimizer import _available_topology_strategies
from app.version import SOFTWARE_VERSION


def _step_excavation() -> ExcavationModel:
    points = [
        Point2D(x=0, y=0), Point2D(x=42, y=0), Point2D(x=42, y=12),
        Point2D(x=32, y=12), Point2D(x=32, y=18), Point2D(x=18, y=18),
        Point2D(x=18, y=12), Point2D(x=0, y=12),
    ]
    return make_excavation_model("step", Polyline2D(points=points, closed=True), 0.0, -14.0, 0.5)


def test_elongated_pit_does_not_offer_bidirectional_frame_by_default() -> None:
    excavation = make_excavation_model(
        "long",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=100, y=0), Point2D(x=100, y=20), Point2D(x=0, y=20)], closed=True),
        0.0, -16.0, 0.5,
    )
    project = Project(name="long", excavation=excavation)
    strategies = _available_topology_strategies(project)
    assert "bidirectional_grid" not in strategies
    assert set(strategies) == {"direct_grid", "hybrid_diagonal"}


def test_transition_zones_receive_denser_support_stations() -> None:
    excavation = _step_excavation()
    lines, _ = generate_support_layout_lines(
        excavation,
        SupportLayoutConfig(topology_strategy="direct_grid", target_main_support_spacing_m=5.0),
    )
    mains = [row for row in lines if row.role == "main_strut"]
    assert mains
    assert any(row.design_zone == "transition" for row in mains)
    assert all(row.station_chainage_m is not None for row in mains)
    assert all(row.load_path_class == "wall_to_wall" for row in mains)


def test_wall_calculation_segments_are_split_into_constructible_panels() -> None:
    excavation = make_excavation_model(
        "long",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=100, y=0), Point2D(x=100, y=20), Point2D(x=0, y=20)], closed=True),
        0.0, -16.0, 0.5,
    )
    project = Project(name="panels", excavation=excavation)
    system = auto_diaphragm_wall(excavation, settings=project.design_settings)
    long_wall = max(system.diaphragm_walls, key=lambda wall: sum(((b.x-a.x)**2+(b.y-a.y)**2)**0.5 for a,b in zip(wall.axis.points[:-1], wall.axis.points[1:])))
    assert len(long_wall.construction_panels) > 10
    assert all(3.0 <= float(row["lengthM"]) <= 7.0 for row in long_wall.construction_panels)


def test_rebar_visualization_contains_construction_panel_cages() -> None:
    excavation = make_excavation_model(
        "cage",
        Polyline2D(points=[Point2D(x=0, y=0), Point2D(x=36, y=0), Point2D(x=36, y=16), Point2D(x=0, y=16)], closed=True),
        0.0, -14.0, 0.5,
    )
    project = Project(name="cage", excavation=excavation)
    project.retaining_system = auto_supports(excavation, auto_diaphragm_wall(excavation, settings=project.design_settings))
    project.retaining_system.rebar_design_scheme = build_rebar_design_scheme(project, mode="balanced")
    payload = build_rebar_ifc_visualization(project, max_bars=300)
    assert payload["summary"]["cageCount"] == sum(len(wall.construction_panels) for wall in project.retaining_system.diaphragm_walls)
    assert payload["cages"]
    cage = payload["cages"][0]
    assert len(cage["faces"]) == 2
    assert float(cage["horizontal"]["spacingMm"]) > 0
    assert cage["representation"] == "construction_panel_rebar_cage_grid_with_joints_lifting_and_splice_zones"


def test_pipeline_exposes_ordered_design_institute_gates() -> None:
    excavation = _step_excavation()
    project = Project(name="pipeline", excavation=excavation)
    project.retaining_system = auto_supports(excavation, auto_diaphragm_wall(excavation, settings=project.design_settings))
    payload = evaluate_design_pipeline(project)
    assert payload["stageCount"] == 8
    assert payload["operatingSequence"][0] == "P1_DATA_BASIS"
    assert payload["operatingSequence"][-1] == "P8_REVIEW_ISSUE"
    assert next(row for row in payload["stages"] if row["stageId"] == "P7_DELIVERABLES")["status"] == "blocked"


def test_v320_version() -> None:
    assert tuple(int(part) for part in SOFTWARE_VERSION.split(".")) >= (3, 20, 0)


def test_reference_support_csv_import_preserves_wall_to_wall_geometry() -> None:
    from app.services.support_layout_import import import_support_layout_csv

    excavation = make_excavation_model(
        "reference",
        Polyline2D(points=[Point2D(x=-20, y=-10), Point2D(x=20, y=-10), Point2D(x=20, y=10), Point2D(x=-20, y=10)], closed=True),
        0.0, -12.0, 0.5,
    )
    project = Project(name="reference", excavation=excavation)
    project.retaining_system = auto_diaphragm_wall(project.excavation, settings=project.design_settings)
    payload = (
        "code,levelIndex,elevationM,startX,startY,endX,endY,supportRole,sourceMaterial\n"
        "REF-1,1,-2.0,-20.0,0.0,20.0,0.0,main_strut,Brace_E\n"
        "REF-2,1,-2.0,0.0,-10.0,0.0,10.0,main_strut,Brace_E\n"
    ).encode("utf-8")
    result = import_support_layout_csv(project, payload)
    assert result["supportCount"] == 2
    assert project.retaining_system.layout_summary["supportLayoutSource"] == "imported_reference_csv"
    for support in project.retaining_system.supports:
        assert support.start_face_code
        assert support.end_face_code
        assert support.start_wall_connection is not None
        assert support.end_wall_connection is not None
        assert support.start_wall_clearance_m == pytest.approx(project.design_settings.support_wall_clearance_m)
        assert support.optimization_locked is True


def test_seepage_screen_is_reported_as_risk_index_not_safety_factor() -> None:
    from app.calculation.engine import _max_value, _stability_min
    from app.schemas.domain import CalculationResult, GoverningValues

    checks = [{"ruleId": "JGJ120-LAYERED-SEEPAGE", "calculatedValue": 0.32}]
    assert _max_value(checks, "SEEPAGE") == pytest.approx(0.32)
    result = CalculationResult(
        projectId="project-test",
        caseId="case-test",
        governingValues=GoverningValues(
            embedmentSafetyFactorMin=1.5,
            heaveSafetyFactorMin=1.8,
            seepageRiskIndexMax=0.32,
        ),
    )
    assert _stability_min(result) == pytest.approx(1.5)
