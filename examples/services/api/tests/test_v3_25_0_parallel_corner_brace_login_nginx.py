from __future__ import annotations

import math
from pathlib import Path

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.support_layout import SupportLayoutConfig
from app.version import SOFTWARE_VERSION


def _project() -> Project:
    project = Project(name="parallel-corner-family")
    points = [Point2D(x=0, y=0), Point2D(x=80, y=0), Point2D(x=80, y=20), Point2D(x=0, y=20)]
    project.excavation = make_excavation_model("pit", Polyline2D(points=points, closed=True), 0.0, -16.0)
    project.design_settings.corner_diagonal_family_count = 2
    project.design_settings.corner_diagonal_family_spacing_m = 3.0
    project.design_settings.corner_diagonal_max_wall_fraction = 0.40
    config = SupportLayoutConfig(
        topology_strategy="hybrid_diagonal",
        corner_diagonal_family_count=2,
        corner_diagonal_family_spacing_m=3.0,
        corner_diagonal_max_wall_fraction=0.40,
    )
    project.retaining_system = auto_supports(project.excavation, auto_diaphragm_wall(project.excavation), config)
    return project


def _angle(item) -> float:
    a = item.start_wall_connection or item.start
    b = item.end_wall_connection or item.end
    return math.degrees(math.atan2(b.y-a.y, b.x-a.x)) % 180.0


def test_corner_braces_are_parallel_families_with_independent_wall_nodes() -> None:
    project = _project()
    braces = [item for item in project.retaining_system.supports if item.support_role == "corner_diagonal"]
    assert len(braces) >= 8
    groups = {}
    for item in braces:
        groups.setdefault((item.level_index, tuple(sorted((item.start_face_code, item.end_face_code)))), []).append(item)
    paired = [items for items in groups.values() if len(items) >= 2]
    assert paired
    for items in paired:
        angles = [_angle(item) for item in items]
        assert max(angles) - min(angles) <= 1.0
        by_face = {}
        for item in items:
            by_face.setdefault(item.start_face_code, []).append(item.start_wall_connection)
            by_face.setdefault(item.end_face_code, []).append(item.end_wall_connection)
        for points in by_face.values():
            for i, first in enumerate(points):
                for second in points[i+1:]:
                    assert math.hypot(first.x-second.x, first.y-second.y) >= 2.4
    quality = evaluate_support_layout_quality(project)
    assert quality.metrics["cornerBraceParallelismIssueCount"] == 0
    assert quality.metrics["cornerBraceEndpointCongestionCount"] == 0
    assert quality.metrics["wallJunctionCount"] == 0


def test_production_nginx_explicitly_disables_basic_auth_and_checks_challenge() -> None:
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts" / "build-and-start-production.sh").read_text(encoding="utf-8")
    assert "auth_basic off;" in script
    assert "cleanup-nginx-domain.py" in script
    assert "WWW-Authenticate" in script
    assert "auth_basic_user_file" not in script


def test_v325_version() -> None:
    assert tuple(map(int, SOFTWARE_VERSION.split("."))) >= (3, 25, 0)
