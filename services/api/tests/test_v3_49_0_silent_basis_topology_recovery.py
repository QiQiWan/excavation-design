from __future__ import annotations

from app.schemas.domain import Point2D, Polyline2D, Project, SupportElement
from app.services.design_service import auto_diaphragm_wall
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import _nearest_face_hit, _wall_bearing_alignment, normalize_existing_support_wall_connections
from app.services.support_layout_repair import auto_repair_support_layout


def _stepped_project() -> Project:
    points = [
        (-115, -14), (-99, -14), (-99, -12), (-39, -12), (-39, -16.5),
        (-13, -16.5), (-13, -13), (98, -13), (98, -14.5), (115, -14.5),
        (115, 14.5), (98, 14.5), (98, 13), (-13, 13), (-13, 16.5),
        (-39, 16.5), (-39, 12), (-99, 12), (-99, 14), (-115, 14),
    ]
    excavation = make_excavation_model(
        "harvest-lake-stepped",
        Polyline2D(points=[Point2D(x=x, y=y) for x, y in points], closed=True),
        0.0,
        -12.0,
    )
    project = Project(name="harvest-lake-stepped", excavation=excavation)
    project.design_settings.design_basis_confirmed = True
    project.design_settings.bearing_capacity_kpa = 220.0
    project.retaining_system = auto_diaphragm_wall(excavation)
    return project


def test_direction_aware_face_selection_rejects_tangent_return_wall() -> None:
    project = _stepped_project()
    point = Point2D(x=-39.0, y=-11.0)
    toward = Point2D(x=-39.0, y=11.5)
    hit = _nearest_face_hit(point, project.excavation, tolerance=1.10, toward=toward)
    assert hit is not None
    segment = next(item for item in project.excavation.segments if item.name == hit.face_code)
    assert abs(float(segment.end.y) - float(segment.start.y)) < 1.0e-6
    assert _wall_bearing_alignment(point, toward, segment) > 0.95


def test_legacy_tangent_face_is_normalized_to_valid_wall_bearing() -> None:
    project = _stepped_project()
    assert project.retaining_system is not None
    vertical_return = next(
        item for item in project.excavation.segments
        if abs(float(item.start.x) + 39.0) < 1.0e-6
        and abs(float(item.end.x) + 39.0) < 1.0e-6
        and min(item.start.y, item.end.y) >= 12.0
    )
    support = SupportElement(
        code="SP-L1-11",
        levelIndex=1,
        elevation=-3.0,
        start=Point2D(x=-39.0, y=-11.0),
        end=Point2D(x=-39.0, y=11.5),
        startFaceCode="S3",
        endFaceCode=vertical_return.name,
        startWallClearanceM=1.0,
        endWallClearanceM=0.5,
    )
    project.retaining_system.supports = [support]
    result = normalize_existing_support_wall_connections(project)
    assert result["changed"] is True
    assert support.end_face_code != vertical_return.name
    end_segment = next(item for item in project.excavation.segments if item.name == support.end_face_code)
    assert _wall_bearing_alignment(support.end_wall_connection, support.start, end_segment) > 0.95
    assert float(support.end_wall_clearance_m or 0.0) >= 0.9


def test_stepped_strip_generates_formal_candidates_after_bearing_fix(monkeypatch) -> None:
    monkeypatch.setenv("PITGUARD_PRODUCT_MODE", "core")
    monkeypatch.setenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "12")
    project = _stepped_project()
    repair = auto_repair_support_layout(
        project,
        max_candidates=3,
        preset="balanced",
        search_config={"requireDiverseSchemes": True, "coreMode": True, "maxTrials": 48},
    )
    formal = [candidate for candidate in repair.candidates if bool((candidate.hard_constraints or {}).get("passed"))]
    assert len(formal) >= 1
    assert repair.selected_candidate_id
    assert project.retaining_system is not None
    assert len(project.retaining_system.supports) > 0


def test_legacy_bearing_normalization_is_idempotent() -> None:
    project = _stepped_project()
    assert project.retaining_system is not None
    project.retaining_system.supports = [
        SupportElement(
            code="SP-L1-IDEMPOTENT",
            levelIndex=1,
            elevation=-3.0,
            start=Point2D(x=-39.0, y=-11.0),
            end=Point2D(x=-39.0, y=11.5),
            startFaceCode="S3",
            endFaceCode="S16",
            startWallClearanceM=1.0,
            endWallClearanceM=0.5,
        )
    ]
    first = normalize_existing_support_wall_connections(project)
    second = normalize_existing_support_wall_connections(project)
    assert first["changed"] is True
    assert second["changed"] is False


def test_memory_debug_snapshot_exposes_metric_availability() -> None:
    from app.services.system_resources import memory_debug_snapshot

    snapshot = memory_debug_snapshot()
    assert "processMetricsAvailable" in snapshot
    assert "processMetricsSource" in snapshot
    assert "processRssBytes" in snapshot
    assert "processPrivateBytes" in snapshot
