from __future__ import annotations

from app.geology.model_builder import ensure_geological_model_covers_excavation, geological_coverage_audit
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Borehole, BoreholeLayer, Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.storage.database import SQLiteProjectStore
from app.storage.repository import ProjectRepository
from app.storage.task_store import SQLiteTaskStore
from app.version import SOFTWARE_VERSION


def _borehole(code: str, x: float, y: float) -> Borehole:
    return Borehole(
        code=code,
        x=x,
        y=y,
        collarElevation=0.0,
        depth=40.0,
        layers=[
            BoreholeLayer(
                stratumCode="S1",
                stratumName="soil",
                topDepth=0.0,
                bottomDepth=40.0,
                topElevation=0.0,
                bottomElevation=-40.0,
            )
        ],
    )


def test_project_and_task_records_can_be_deleted(tmp_path) -> None:
    db_path = tmp_path / "delete.sqlite3"
    repo = ProjectRepository(SQLiteProjectStore(db_path))
    project = repo.create(Project(name="delete-me"))
    task_store = SQLiteTaskStore(db_path)
    task_store.upsert(
        {
            "id": "task-delete-1",
            "projectId": project.id,
            "operation": "export_json",
            "status": "success",
            "updatedAt": "2026-07-13T00:00:00+00:00",
        }
    )
    assert task_store.list(project.id)
    assert task_store.delete_by_project(project.id) == 1
    assert task_store.list(project.id) == []
    assert repo.delete(project.id) is True
    assert repo.get(project.id) is None


def test_corner_braces_are_direct_wall_to_wall_and_near_corner() -> None:
    project = Project(name="corner-brace-contract")
    points = [
        Point2D(x=0.0, y=0.0),
        Point2D(x=80.0, y=0.0),
        Point2D(x=80.0, y=20.0),
        Point2D(x=0.0, y=20.0),
    ]
    project.excavation = make_excavation_model("pit", Polyline2D(points=points, closed=True), 0.0, -16.0)
    config = SupportLayoutConfig(
        topology_strategy="hybrid_diagonal",
        corner_diagonal_min_offset_m=3.5,
        corner_diagonal_max_offset_m=8.0,
        corner_diagonal_max_wall_fraction=0.30,
    )
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation), config)
    corners = [item for item in project.retaining_system.supports if item.support_role == "corner_diagonal"]
    assert corners
    vertices = list(project.excavation.outline.points)
    for brace in corners:
        assert brace.start_face_code
        assert brace.end_face_code
        assert brace.start_face_code != brace.end_face_code
        assert brace.start_wall_connection is not None
        assert brace.end_wall_connection is not None
        assert "不得截断至另一水平支撑" in (brace.layout_note or "")
        for connection in (brace.start_wall_connection, brace.end_wall_connection):
            nearest = min(((connection.x - vertex.x) ** 2 + (connection.y - vertex.y) ** 2) ** 0.5 for vertex in vertices)
            assert nearest <= 8.01
    quality = evaluate_support_layout_quality(project)
    assert quality.metrics.get("nonRingCrossingCount") == 0
    assert not [issue for issue in quality.issues if issue.category == "corner_brace_bearing"]


def test_geology_coverage_recomputes_stale_fail_and_splits_extrapolation_status() -> None:
    project = Project(name="coverage-state")
    project.boreholes = [
        _borehole("BH1", -5.0, -5.0),
        _borehole("BH2", 5.0, -5.0),
        _borehole("BH3", 5.0, 5.0),
        _borehole("BH4", -5.0, 5.0),
    ]
    points = [
        Point2D(x=-25.0, y=-15.0),
        Point2D(x=25.0, y=-15.0),
        Point2D(x=25.0, y=15.0),
        Point2D(x=-25.0, y=15.0),
    ]
    project.excavation = make_excavation_model("pit", Polyline2D(points=points, closed=True), 0.0, -15.0)
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    assert ensure_geological_model_covers_excavation(project, grid_size=5.0) is True
    project.geological_model.coverage_audit["status"] = "fail"  # legacy state from V3.15/V3.16
    audit = geological_coverage_audit(project)
    assert audit["designDomainCovered"] is True
    assert audit["coverageStatus"] == "pass"
    assert audit["status"] == "warning"
    assert audit["extrapolationStatus"] in {"warning", "manual_review"}


def test_v317_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 19, 0)


def test_actual_outline_secondary_ties_are_wall_normal_and_corner_braces_are_wall_to_wall() -> None:
    import json
    import math
    from pathlib import Path
    from app.services.excavation_service import make_excavation_model
    from app.services.design_service import auto_diaphragm_wall, auto_supports
    from app.schemas.domain import Point2D, Polyline2D

    payload_path = Path(__file__).resolve().parents[3] / "packages" / "sample-data" / "actual-project" / "actual_project_excavation_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    excavation = make_excavation_model(
        payload["name"],
        Polyline2D(points=[Point2D(**item) for item in payload["outline"]["points"]], closed=True),
        0.0,
        -16.6,
        0.5,
    )
    system = auto_supports(excavation, auto_diaphragm_wall(excavation))
    segments = {str(item.name): item for item in excavation.segments}
    for support in system.supports:
        if support.support_role == "corner_diagonal":
            assert support.start_face_code
            assert support.end_face_code
            assert support.start_face_code != support.end_face_code
        if support.support_role != "secondary_strut":
            continue
        face = support.start_face_code or support.end_face_code
        if not face or (support.start_face_code and support.end_face_code):
            continue
        segment = segments[face]
        dx = support.end.x - support.start.x
        dy = support.end.y - support.start.y
        length = max(math.hypot(dx, dy), 1e-9)
        inward_x = -float(segment.outward_normal.x)
        inward_y = -float(segment.outward_normal.y)
        alignment = abs((dx / length) * inward_x + (dy / length) * inward_y)
        assert alignment >= math.cos(math.radians(2.0))


def test_candidate_preflight_keeps_corner_braces_wall_to_wall_and_ty_ties_wall_normal() -> None:
    import json
    import math
    from pathlib import Path
    from app.services.support_layout_optimizer import optimize_support_layout_candidates

    payload_path = Path(__file__).resolve().parents[3] / "packages" / "sample-data" / "actual-project" / "actual_project_excavation_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    project = Project(name="actual-candidate-contract")
    project.excavation = make_excavation_model(
        payload["name"],
        Polyline2D(points=[Point2D(**item) for item in payload["outline"]["points"]], closed=True),
        0.0,
        -16.6,
        0.5,
    )
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation))
    _system, candidates = optimize_support_layout_candidates(project, max_candidates=3)
    assert len(candidates) == 3
    segments = list(project.excavation.segments)

    def nearest_segment(point: dict[str, float]):
        best = None
        best_dist = 1e18
        px, py = float(point["x"]), float(point["y"])
        for segment in segments:
            ax, ay = float(segment.start.x), float(segment.start.y)
            bx, by = float(segment.end.x), float(segment.end.y)
            dx, dy = bx - ax, by - ay
            den = max(dx * dx + dy * dy, 1e-12)
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
            qx, qy = ax + t * dx, ay + t * dy
            dist = math.hypot(px - qx, py - qy)
            if dist < best_dist:
                best, best_dist = segment, dist
        return best

    for candidate in candidates:
        for support in candidate.plan_geometry.get("supports", []):
            start_wall = support.get("wallConnectionStart")
            end_wall = support.get("wallConnectionEnd")
            if support.get("role") == "corner_diagonal":
                assert start_wall and end_wall
                continue
            if support.get("role") != "secondary_strut" or bool(start_wall) == bool(end_wall):
                continue
            wall = start_wall or end_wall
            segment = nearest_segment(wall)
            assert segment is not None
            start, end = support["start"], support["end"]
            dx = float(end["x"]) - float(start["x"])
            dy = float(end["y"]) - float(start["y"])
            length = max(math.hypot(dx, dy), 1e-9)
            tx = float(segment.end.x) - float(segment.start.x)
            ty = float(segment.end.y) - float(segment.start.y)
            tangent_len = max(math.hypot(tx, ty), 1e-9)
            normal_alignment = abs((dx / length) * (-ty / tangent_len) + (dy / length) * (tx / tangent_len))
            assert normal_alignment >= math.cos(math.radians(2.0))
