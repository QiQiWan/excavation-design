from __future__ import annotations

import math

import pytest

from app.calculation.engine import _support_topology_hash, build_default_construction_cases, run_calculation, synchronize_calculation_case_supports
from app.geology.idw import interpolate_surface_idw
from app.geology.model_builder import ensure_geological_model_covers_excavation, geological_coverage_audit
from app.schemas.domain import (
    Borehole,
    BoreholeLayer,
    CalculationCase,
    CalculationResult,
    ConstructionStage,
    Point2D,
    Polyline2D,
    Project,
    SupportLayoutOptimizationCandidate,
    SupportLayoutRepairSummary,
)
from app.services.candidate_result_cache import candidate_input_hash
from app.services.calculation_diagnostics import build_calculation_diagnostics
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig, generate_support_layout_lines, plan_shape_diagnostics
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository
from app.version import SOFTWARE_VERSION


def _rotated_rectangle(length: float, width: float, angle_deg: float) -> list[Point2D]:
    angle = math.radians(angle_deg)
    ca, sa = math.cos(angle), math.sin(angle)
    raw = [(-length / 2, -width / 2), (length / 2, -width / 2), (length / 2, width / 2), (-length / 2, width / 2)]
    return [Point2D(x=x * ca - y * sa, y=x * sa + y * ca) for x, y in raw]


def _single_layer_borehole(code: str, x: float, y: float, bottom: float = -30.0) -> Borehole:
    return Borehole(
        code=code,
        x=x,
        y=y,
        collarElevation=0.0,
        depth=abs(bottom),
        layers=[BoreholeLayer(stratumCode="S1", stratumName="soil", topDepth=0.0, bottomDepth=abs(bottom), topElevation=0.0, bottomElevation=bottom)],
    )


def test_rotated_rectangle_supports_follow_local_principal_axes() -> None:
    points = _rotated_rectangle(60.0, 20.0, 32.0)
    excavation = make_excavation_model("rotated", Polyline2D(points=points, closed=True), 0.0, -18.0)
    lines, warnings = generate_support_layout_lines(excavation, SupportLayoutConfig(topology_strategy="direct_grid"))
    mains = [line for line in lines if line.role == "main_strut"]
    assert mains
    diagnostics = plan_shape_diagnostics(points)
    assert diagnostics["longSpanM"] == pytest.approx(60.0, abs=0.2)
    assert diagnostics["shortSpanM"] == pytest.approx(20.0, abs=0.2)
    expected_angle = math.radians(32.0 + 90.0)
    for line in mains[:5]:
        angle = math.atan2(line.end.y - line.start.y, line.end.x - line.start.x)
        alignment = abs(math.cos(angle - expected_angle))
        assert alignment > 0.97
        assert line.span_length < 22.0
    assert any("不再依赖全局 X/Y 包围盒" in warning for warning in warnings)


def test_large_square_is_not_misclassified_as_ring_shaft() -> None:
    points = _rotated_rectangle(60.0, 60.0, 27.0)
    excavation = make_excavation_model("square", Polyline2D(points=points, closed=True), 0.0, -20.0)
    lines, _warnings = generate_support_layout_lines(excavation, SupportLayoutConfig(topology_strategy="bidirectional_grid"))
    assert lines
    assert not [line for line in lines if line.role == "ring_strut"]
    assert [line for line in lines if line.role == "main_strut"]
    assert plan_shape_diagnostics(points)["circularShaftLike"] is False


def test_geology_auto_expands_to_retaining_design_domain_and_clamps_extrapolation() -> None:
    project = Project(name="small-geology-domain")
    project.boreholes = [
        _single_layer_borehole("BH1", -5.0, -5.0, -28.0),
        _single_layer_borehole("BH2", 5.0, -5.0, -30.0),
        _single_layer_borehole("BH3", 5.0, 5.0, -32.0),
        _single_layer_borehole("BH4", -5.0, 5.0, -29.0),
    ]
    points = [Point2D(x=-30, y=-20), Point2D(x=30, y=-20), Point2D(x=30, y=20), Point2D(x=-30, y=20)]
    project.excavation = make_excavation_model("large-pit", Polyline2D(points=points, closed=True), 0.0, -16.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    changed = ensure_geological_model_covers_excavation(project, grid_size=5.0)
    audit = geological_coverage_audit(project)
    assert changed is True
    assert audit["designDomainCovered"] is True
    assert audit["autoExtended"] is True
    required = audit["requiredBounds"]
    model = audit["modelBounds"]
    assert model["minX"] <= required["minX"] and model["maxX"] >= required["maxX"]
    assert model["minY"] <= required["minY"] and model["maxY"] >= required["maxY"]
    assert audit["maximumExtrapolationDistanceM"] > 0.0

    grid = interpolate_surface_idw(
        [(-5.0, 0.0, -10.0), (5.0, 0.0, -20.0)],
        (-20.0, 0.0, 20.0, 0.0),
        5.0,
        trusted_bounds=(-5.0, -1.0, 5.0, 1.0),
    )
    row = grid.z_values[0]
    # Values outside the trusted rectangle remain equal to their nearest trusted boundary.
    assert row[0] == pytest.approx(row[3])
    assert row[-1] == pytest.approx(row[-4])


def test_candidate_hash_changes_for_wall_or_borehole_geometry() -> None:
    project = Project(name="cache-safety")
    project.boreholes = [_single_layer_borehole("BH1", 0.0, 0.0)]
    points = [Point2D(x=0, y=0), Point2D(x=20, y=0), Point2D(x=20, y=10), Point2D(x=0, y=10)]
    project.excavation = make_excavation_model("pit", Polyline2D(points=points, closed=True), 0.0, -10.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    project.calculation_cases = build_default_construction_cases(project)
    candidate = SupportLayoutOptimizationCandidate(targetSpacing=5.0, columnMaxSpan=18.0)
    first = candidate_input_hash(project, candidate)
    project.retaining_system.diaphragm_walls[0].thickness += 0.1
    second = candidate_input_hash(project, candidate)
    project.retaining_system.diaphragm_walls[0].thickness -= 0.1
    project.boreholes[0].layers[0].bottom_elevation -= 1.0
    third = candidate_input_hash(project, candidate)
    assert first != second
    assert first != third


def test_stage_support_sync_uses_semantics_and_does_not_leave_stale_ids() -> None:
    project = Project(name="stage-sync")
    project.boreholes = [_single_layer_borehole("BH1", 0.0, 0.0)]
    points = [Point2D(x=0, y=0), Point2D(x=30, y=0), Point2D(x=30, y=12), Point2D(x=0, y=12)]
    project.excavation = make_excavation_model("pit", Polyline2D(points=points, closed=True), 0.0, -14.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    old_case = CalculationCase(
        name="legacy",
        supportTopologyHash="old-hash",
        stages=[
            ConstructionStage(name="deep excavation", excavationElevation=-12.0, stageType="excavation", activeSupportIds=["obsolete-id"], surcharge=35.0),
            ConstructionStage(name="support one", excavationElevation=-3.0, stageType="support_installation", activeSupportIds=["obsolete-id"], surcharge=25.0),
        ],
    )
    synced, evidence = synchronize_calculation_case_supports(project, old_case)
    valid = {support.id for support in project.retaining_system.supports}
    referenced = {sid for stage in synced.stages for sid in stage.active_support_ids + stage.deactivated_support_ids}
    assert referenced <= valid
    assert evidence["synchronized"] is True
    assert evidence["after"]["requiresSynchronization"] is False
    install = next(stage for stage in synced.stages if stage.stage_type == "support_installation")
    assert install.surcharge == pytest.approx(25.0)

    diagnostics = build_calculation_diagnostics(project, synced, [], [], support_case_sync=evidence)
    sync_causes = [item for item in diagnostics["rootCauses"] if item["code"] == "STALE_STAGE_SUPPORT_REFERENCES"]
    assert sync_causes and sync_causes[0]["severity"] == "warning"


def test_v315_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 19, 0)


def _sample_segment_inside(start: Point2D, end: Point2D, polygon: list[Point2D]) -> bool:
    from app.services.support_layout import _point_in_polygon

    return all(
        _point_in_polygon(
            Point2D(x=start.x + (end.x - start.x) * t, y=start.y + (end.y - start.y) * t),
            polygon,
        )
        for t in (0.05, 0.15, 0.25, 0.50, 0.75, 0.85, 0.95)
    )


def test_general_polygon_layouts_keep_support_centrelines_inside() -> None:
    shapes = {
        "rotated_rectangle": _rotated_rectangle(52.0, 22.0, 31.0),
        "trapezoid": [Point2D(x=-30, y=-12), Point2D(x=30, y=-9), Point2D(x=22, y=14), Point2D(x=-24, y=12)],
        "l_shape": [
            Point2D(x=-30, y=-20), Point2D(x=30, y=-20), Point2D(x=30, y=-4),
            Point2D(x=5, y=-4), Point2D(x=5, y=20), Point2D(x=-30, y=20),
        ],
        "u_shape": [
            Point2D(x=-30, y=-20), Point2D(x=30, y=-20), Point2D(x=30, y=20),
            Point2D(x=12, y=20), Point2D(x=12, y=-2), Point2D(x=-12, y=-2),
            Point2D(x=-12, y=20), Point2D(x=-30, y=20),
        ],
    }
    for name, points in shapes.items():
        excavation = make_excavation_model(name, Polyline2D(points=points, closed=True), 0.0, -16.0)
        lines, _warnings = generate_support_layout_lines(excavation, SupportLayoutConfig(topology_strategy="balanced_grid"))
        assert lines, name
        assert all(_sample_segment_inside(line.start, line.end, points) for line in lines), name


def test_one_click_support_design_requires_explicit_transfer_system_for_concave_shapes() -> None:
    from app.quality.support_layout_quality import evaluate_support_layout_quality

    shapes = {
        "rotated_rectangle": _rotated_rectangle(52.0, 22.0, 31.0),
        "trapezoid": [Point2D(x=-30, y=-12), Point2D(x=30, y=-9), Point2D(x=22, y=14), Point2D(x=-24, y=12)],
        "l_shape": [
            Point2D(x=-30, y=-20), Point2D(x=30, y=-20), Point2D(x=30, y=-4),
            Point2D(x=6, y=-4), Point2D(x=6, y=20), Point2D(x=-30, y=20),
        ],
        "u_shape": [
            Point2D(x=-30, y=-20), Point2D(x=30, y=-20), Point2D(x=30, y=20),
            Point2D(x=12, y=20), Point2D(x=12, y=-2), Point2D(x=-12, y=-2),
            Point2D(x=-12, y=20), Point2D(x=-30, y=20),
        ],
        "square": _rotated_rectangle(50.0, 50.0, 22.0),
    }
    for name, points in shapes.items():
        project = Project(name=name)
        project.excavation = make_excavation_model(name, Polyline2D(points=points, closed=True), 0.0, -16.0)
        project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
        summary = evaluate_support_layout_quality(project)
        preflight = project.retaining_system.layout_summary.get("strengthTopologyPreflight", {})
        assert preflight.get("executed") is True, name
        if name in {"l_shape", "u_shape"}:
            assert preflight.get("status") == "fail", name
            assert preflight.get("requiresAlternativeSupportSystem") is True, name
            assert preflight.get("shapeTransferSystemRequired") is True, name
            assert preflight.get("shapeTransferSystemComplete") is False, name
            assert preflight.get("recommendedSupportSystems"), name
            assert summary.status == "fail", name
        else:
            assert preflight.get("status") != "fail", name
            assert summary.status != "fail", (name, [issue.message for issue in summary.issues if issue.severity == "fail"])
        assert summary.metrics.get("supportOutsideExcavationCount") == 0, name


def test_quality_gate_blocks_support_that_leaves_excavation_polygon() -> None:
    from app.quality.support_layout_quality import evaluate_support_layout_quality

    project = Project(name="containment-gate")
    points = [
        Point2D(x=-20, y=-15), Point2D(x=20, y=-15), Point2D(x=20, y=15),
        Point2D(x=5, y=15), Point2D(x=5, y=0), Point2D(x=-5, y=0),
        Point2D(x=-5, y=15), Point2D(x=-20, y=15),
    ]
    project.excavation = make_excavation_model("u-pit", Polyline2D(points=points, closed=True), 0.0, -12.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    assert project.retaining_system.supports
    support = project.retaining_system.supports[0]
    support.start = Point2D(x=-10.0, y=10.0)
    support.end = Point2D(x=10.0, y=10.0)  # crosses the open notch of the U-shaped pit
    summary = evaluate_support_layout_quality(project)
    categories = [issue.category for issue in summary.issues]
    assert "support_outside_excavation" in categories
    assert summary.metrics["supportOutsideExcavationCount"] >= 1
    assert summary.status == "fail"



def _general_shape_project(name: str = "general-shape") -> Project:
    project = Project(name=name)
    project.boreholes = [
        _single_layer_borehole("BH1", -35.0, -25.0, -40.0),
        _single_layer_borehole("BH2", 35.0, -25.0, -40.0),
        _single_layer_borehole("BH3", 35.0, 25.0, -40.0),
        _single_layer_borehole("BH4", -35.0, 25.0, -40.0),
    ]
    points = [
        Point2D(x=-30, y=-20), Point2D(x=30, y=-20), Point2D(x=30, y=-4),
        Point2D(x=6, y=-4), Point2D(x=6, y=20), Point2D(x=-30, y=20),
    ]
    project.excavation = make_excavation_model(name, Polyline2D(points=points, closed=True), 0.0, -16.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    project.calculation_cases = build_default_construction_cases(project)
    return project


def test_optimizer_keeps_concave_candidates_blocked_until_transfer_system_is_explicit() -> None:
    project = _general_shape_project("optimizer-concave")
    _system, candidates = optimize_support_layout_candidates(project, max_candidates=3)
    assert len(candidates) == 3
    assert all(candidate.score == 0.0 for candidate in candidates)
    assert all(candidate.fail_count > 0 for candidate in candidates)
    assert all(candidate.hard_constraints.get("passed") is False for candidate in candidates)
    assert all(candidate.hard_constraints.get("shapeTransferSystemRequired") is True for candidate in candidates)
    assert all(candidate.hard_constraints.get("shapeTransferSystemComplete") is False for candidate in candidates)
    assert all(
        int(candidate.metrics.get("supportOutsideExcavationCount", 0) or 0) == 0
        for candidate in candidates
    )
    assert all(
        "strengthTopologyPreflight" in candidate.variable_summary
        for candidate in candidates
    )
    assert all("不得作为正式采用方案" in candidate.constructability_note for candidate in candidates)


def test_repository_invalidates_legacy_results_and_candidate_full_rows(tmp_path) -> None:
    project = _general_shape_project("legacy-state")
    assert project.retaining_system is not None
    project.retaining_system.support_layout_repair = SupportLayoutRepairSummary(
        candidates=[
            SupportLayoutOptimizationCandidate(
                id="candidate-A",
                rank=1,
                score=88.0,
                fullCalculation={"failCount": 33, "maxDisplacement": 503.95},
            )
        ],
        candidateFullCalculations=[{"candidateId": "candidate-A", "failCount": 33}],
    )
    project.calculation_results = [
        CalculationResult(
            id="legacy-result",
            projectId=project.id,
            caseId=project.calculation_cases[0].id,
            # Intentionally no supportTopologyHash: legacy V3.14 payload.
            checkSummary={"pass": 10, "fail": 33, "warning": 5},
        )
    ]
    store = SQLiteProjectStore(tmp_path / "projects.sqlite3")
    store.upsert(project.model_dump(mode="json", by_alias=True))
    loaded = ProjectRepository(store).require(project.id)
    assert loaded.calculation_results == []
    state = loaded.advanced_engineering.get("calculationState", {})
    assert state.get("requiresRecalculation") is True
    assert "topology hash" in str(state.get("reason", ""))
    assert loaded.retaining_system.support_layout_repair.candidate_full_calculations == []
    assert loaded.retaining_system.support_layout_repair.candidates[0].full_calculation == {}
    assert loaded.advanced_engineering.get("invalidatedCalculationArchive")
    assert loaded.calculation_cases


def test_calculation_result_binds_current_support_topology_hash() -> None:
    project = _general_shape_project("calculation-hash")
    ensure_geological_model_covers_excavation(project, grid_size=8.0)
    result = run_calculation(project, project.calculation_cases[0], include_candidate_comparison=False)
    assert result.support_topology_hash
    assert result.support_topology_hash == _support_topology_hash(project)
