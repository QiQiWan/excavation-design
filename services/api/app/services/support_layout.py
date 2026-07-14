from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean
from typing import Iterable

from app.schemas.domain import (
    BeamElement,
    BearingPlateDesign,
    ColumnElement,
    ConstructionObstacle,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    ReinforcementGroup,
    SectionDefinition,
    SupportElement,
    SupportWaleNode,
)

EPS = 1e-7
RIGHT_ANGLE_MIN_DEG = 70.0
RIGHT_ANGLE_MAX_DEG = 115.0
TARGET_MAIN_SUPPORT_SPACING_M = 5.0
MIN_PRACTICAL_MAIN_SUPPORT_SPACING_M = 3.0
MAX_PRACTICAL_MAIN_SUPPORT_SPACING_M = 6.0
MAX_AUTO_MAIN_STRUTS_PER_LEVEL = 40
MIN_MAIN_STRUT_SPAN_M = 5.0
MIN_CORNER_BRACE_LEG_M = 2.5
COLUMN_MAX_UNBRACED_SPAN_M = 18.0
COLUMN_DEDUP_GRID_M = 0.25
RING_SUPPORT_MIN_SHORT_SPAN_M = 38.0
RING_SUPPORT_MAX_ASPECT = 1.35


@dataclass(frozen=True)
class SupportLayoutConfig:
    target_main_support_spacing_m: float = TARGET_MAIN_SUPPORT_SPACING_M
    column_max_unbraced_span_m: float = COLUMN_MAX_UNBRACED_SPAN_M
    support_wall_clearance_m: float = 1.0
    max_direct_strut_span_m: float = 24.0
    max_wale_support_bay_m: float = 7.5
    hard_max_wale_support_bay_m: float = 9.0
    diagonal_brace_min_wall_length_m: float = 18.0
    corner_diagonal_min_offset_m: float = 3.5
    corner_diagonal_max_offset_m: float = 8.0
    corner_diagonal_max_wall_fraction: float = 0.40
    corner_diagonal_family_count: int = 2
    corner_diagonal_family_spacing_m: float = 3.0
    corner_diagonal_parallel_tolerance_deg: float = 5.0
    prefer_diagonal_braces: bool = True
    allow_wale_repair_t_y_nodes: bool = False
    topology_strategy: str = "balanced_grid"
    transition_zone_spacing_factor: float = 0.72
    transition_zone_influence_m: float = 8.0
    support_min_station_separation_m: float = 2.8
    support_level_depths_m: tuple[float, ...] = ()

    def normalized(self) -> "SupportLayoutConfig":
        strategy = str(self.topology_strategy or "balanced_grid")
        if strategy not in {"direct_grid", "hybrid_diagonal", "bidirectional_grid", "balanced_grid"}:
            strategy = "balanced_grid"
        return SupportLayoutConfig(
            target_main_support_spacing_m=max(MIN_PRACTICAL_MAIN_SUPPORT_SPACING_M, min(MAX_PRACTICAL_MAIN_SUPPORT_SPACING_M, float(self.target_main_support_spacing_m))),
            column_max_unbraced_span_m=max(6.0, min(30.0, float(self.column_max_unbraced_span_m))),
            support_wall_clearance_m=max(0.35, min(3.0, float(self.support_wall_clearance_m))),
            max_direct_strut_span_m=max(12.0, min(45.0, float(self.max_direct_strut_span_m))),
            max_wale_support_bay_m=max(4.0, min(15.0, float(self.max_wale_support_bay_m))),
            hard_max_wale_support_bay_m=max(
                max(4.5, min(15.0, float(self.max_wale_support_bay_m))),
                min(20.0, float(self.hard_max_wale_support_bay_m)),
            ),
            diagonal_brace_min_wall_length_m=max(8.0, min(40.0, float(self.diagonal_brace_min_wall_length_m))),
            corner_diagonal_min_offset_m=max(2.5, min(8.0, float(self.corner_diagonal_min_offset_m))),
            corner_diagonal_max_offset_m=max(
                max(3.0, min(8.0, float(self.corner_diagonal_min_offset_m))),
                min(12.0, float(self.corner_diagonal_max_offset_m)),
            ),
            corner_diagonal_max_wall_fraction=max(0.15, min(0.50, float(self.corner_diagonal_max_wall_fraction))),
            corner_diagonal_family_count=max(1, min(3, int(self.corner_diagonal_family_count))),
            corner_diagonal_family_spacing_m=max(2.5, min(6.0, float(self.corner_diagonal_family_spacing_m))),
            corner_diagonal_parallel_tolerance_deg=max(2.0, min(12.0, float(self.corner_diagonal_parallel_tolerance_deg))),
            prefer_diagonal_braces=bool(self.prefer_diagonal_braces),
            allow_wale_repair_t_y_nodes=bool(self.allow_wale_repair_t_y_nodes),
            topology_strategy=strategy,
            transition_zone_spacing_factor=max(0.55, min(1.0, float(self.transition_zone_spacing_factor))),
            transition_zone_influence_m=max(3.0, min(15.0, float(self.transition_zone_influence_m))),
            support_min_station_separation_m=max(2.2, min(4.5, float(self.support_min_station_separation_m))),
            support_level_depths_m=tuple(sorted({round(float(value), 4) for value in self.support_level_depths_m if float(value) >= 0.0})),
        )


@dataclass
class SupportLayoutLine:
    role: str
    start: Point2D
    end: Point2D
    span_length: float
    bay_spacing: float | None
    layout_note: str
    start_face_code: str | None = None
    end_face_code: str | None = None
    start_tributary_width: float | None = None
    end_tributary_width: float | None = None
    start_wall_connection: Point2D | None = None
    end_wall_connection: Point2D | None = None
    centerline_offset_m: float | None = None
    start_wall_clearance_m: float | None = None
    end_wall_clearance_m: float | None = None
    topology_family: str = "direct_grid"
    design_zone: str | None = None
    station_chainage_m: float | None = None
    local_clear_span_m: float | None = None
    placement_reason: str | None = None
    load_path_class: str = "wall_to_wall"


def _partition_repair_messages(messages: list[str]) -> tuple[list[str], list[str]]:
    """Keep successful topology actions out of the unresolved warning channel."""

    evidence: list[str] = []
    unresolved: list[str] = []
    for raw in messages:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.startswith(("已将", "已增加", "已剔除", "已根据", "检测到", "支撑布置为")) and not any(
            token in text for token in ("未能", "无法", "需人工复核", "不足")
        ):
            evidence.append(text)
        else:
            unresolved.append(text)
    return list(dict.fromkeys(evidence)), list(dict.fromkeys(unresolved))


@dataclass
class ColumnPlanPoint:
    location: Point2D
    support_codes: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SegmentFaceHit:
    face_code: str
    t: float
    length: float


@dataclass(frozen=True)
class PlanAxes:
    origin: Point2D
    long_axis: tuple[float, float]
    short_axis: tuple[float, float]
    long_min: float
    long_max: float
    short_min: float
    short_max: float
    method: str

    @property
    def long_span(self) -> float:
        return self.long_max - self.long_min

    @property
    def short_span(self) -> float:
        return self.short_max - self.short_min

    @property
    def rotation_deg(self) -> float:
        return math.degrees(math.atan2(self.long_axis[1], self.long_axis[0]))


def _distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def _dedup_points(points: list[Point2D]) -> list[Point2D]:
    if len(points) > 1 and _distance(points[0], points[-1]) <= EPS:
        return points[:-1]
    return points


def _bounds(points: list[Point2D]) -> tuple[float, float, float, float, float, float]:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return min_x, min_y, max_x, max_y, max_x - min_x, max_y - min_y


def _local_coordinates(point: Point2D, axes: PlanAxes) -> Point2D:
    dx = float(point.x) - float(axes.origin.x)
    dy = float(point.y) - float(axes.origin.y)
    return Point2D(
        x=dx * axes.long_axis[0] + dy * axes.long_axis[1],
        y=dx * axes.short_axis[0] + dy * axes.short_axis[1],
    )


def _global_coordinates(local: Point2D, axes: PlanAxes) -> Point2D:
    return Point2D(
        x=axes.origin.x + local.x * axes.long_axis[0] + local.y * axes.short_axis[0],
        y=axes.origin.y + local.x * axes.long_axis[1] + local.y * axes.short_axis[1],
    )


def _plan_axes(points: list[Point2D]) -> PlanAxes:
    """Return stable local long/short axes for rotated and irregular polygons."""
    pts = _dedup_points(points)
    if len(pts) < 2:
        return PlanAxes(Point2D(x=0.0, y=0.0), (1.0, 0.0), (0.0, 1.0), 0.0, 1.0, 0.0, 1.0, "fallback")
    edges = [(a, b, max(_distance(a, b), EPS)) for a, b in zip(pts, pts[1:] + pts[:1])]
    perimeter = sum(item[2] for item in edges)
    cx = sum(((a.x + b.x) * 0.5) * length for a, b, length in edges) / max(perimeter, EPS)
    cy = sum(((a.y + b.y) * 0.5) * length for a, b, length in edges) / max(perimeter, EPS)
    cxx = cyy = cxy = 0.0
    for a, b, length in edges:
        mx = (a.x + b.x) * 0.5 - cx
        my = (a.y + b.y) * 0.5 - cy
        cxx += length * mx * mx
        cyy += length * my * my
        cxy += length * mx * my
    cxx /= max(perimeter, EPS)
    cyy /= max(perimeter, EPS)
    cxy /= max(perimeter, EPS)
    anisotropy = math.hypot(cxx - cyy, 2.0 * cxy) / max(cxx + cyy, EPS)
    if anisotropy < 0.08:
        # A square has an isotropic covariance matrix.  Use its longest boundary
        # edge so a rotated square still receives edge-normal support families.
        edge = max(edges, key=lambda item: item[2])
        ux, uy = _unit_vector(edge[0], edge[1])
        method = "longest_boundary_edge"
    else:
        angle = 0.5 * math.atan2(2.0 * cxy, cxx - cyy)
        ux, uy = math.cos(angle), math.sin(angle)
        method = "boundary_weighted_principal_axis"
    vx, vy = -uy, ux
    origin = Point2D(x=cx, y=cy)
    raw = PlanAxes(origin, (ux, uy), (vx, vy), 0.0, 0.0, 0.0, 0.0, method)
    local = [_local_coordinates(point, raw) for point in pts]
    u0, u1 = min(p.x for p in local), max(p.x for p in local)
    v0, v1 = min(p.y for p in local), max(p.y for p in local)
    if (u1 - u0) + EPS < (v1 - v0):
        ux, uy, vx, vy = vx, vy, -ux, -uy
        raw = PlanAxes(origin, (ux, uy), (vx, vy), 0.0, 0.0, 0.0, 0.0, method + "_swapped")
        local = [_local_coordinates(point, raw) for point in pts]
        u0, u1 = min(p.x for p in local), max(p.x for p in local)
        v0, v1 = min(p.y for p in local), max(p.y for p in local)
    return PlanAxes(origin, raw.long_axis, raw.short_axis, u0, u1, v0, v1, raw.method)


def plan_shape_diagnostics(points: list[Point2D]) -> dict[str, object]:
    pts = _dedup_points(points)
    axes = _plan_axes(pts)
    area = abs(_signed_area(pts)) if len(pts) >= 3 else 0.0
    perimeter = sum(_distance(a, b) for a, b in zip(pts, pts[1:] + pts[:1])) if len(pts) >= 2 else 0.0
    concave_count = len(_concave_vertex_indices(pts)) if len(pts) >= 4 else 0
    circularity = 4.0 * math.pi * area / max(perimeter * perimeter, EPS)
    cx = sum(p.x for p in pts) / max(len(pts), 1)
    cy = sum(p.y for p in pts) / max(len(pts), 1)
    radii = [math.hypot(p.x - cx, p.y - cy) for p in pts]
    radial_cv = (math.sqrt(sum((r - mean(radii)) ** 2 for r in radii) / len(radii)) / max(mean(radii), EPS)) if len(radii) > 1 else 1.0
    aspect = axes.long_span / max(axes.short_span, EPS)
    circular_shaft_like = len(pts) >= 6 and concave_count == 0 and aspect <= 1.25 and circularity >= 0.72 and radial_cv <= 0.20
    if circular_shaft_like:
        classification = "circular_or_multisided_shaft"
    elif concave_count:
        classification = "concave_or_stepped_polygon"
    elif len(pts) == 4:
        classification = "rotated_or_orthogonal_quadrilateral"
    else:
        classification = "general_convex_polygon"
    return {
        "classification": classification,
        "vertexCount": len(pts),
        "concaveVertexCount": concave_count,
        "principalAxisMethod": axes.method,
        "principalAxisRotationDeg": round(axes.rotation_deg, 3),
        "longSpanM": round(axes.long_span, 3),
        "shortSpanM": round(axes.short_span, 3),
        "aspectRatio": round(aspect, 4),
        "circularity": round(circularity, 4),
        "radialCoefficientOfVariation": round(radial_cv, 4),
        "circularShaftLike": circular_shaft_like,
    }


def _signed_area(points: list[Point2D]) -> float:
    area = 0.0
    for a, b in zip(points, points[1:] + points[:1]):
        area += a.x * b.y - b.x * a.y
    return 0.5 * area


def _unit_vector(a: Point2D, b: Point2D) -> tuple[float, float]:
    length = _distance(a, b)
    if length <= EPS:
        return 0.0, 0.0
    return (b.x - a.x) / length, (b.y - a.y) / length


def _point_at(a: Point2D, b: Point2D, distance_from_a: float) -> Point2D:
    ux, uy = _unit_vector(a, b)
    return Point2D(x=a.x + ux * distance_from_a, y=a.y + uy * distance_from_a)


def _point_on_segment(p: Point2D, a: Point2D, b: Point2D, tol: float = 1e-6) -> bool:
    cross = abs((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x))
    if cross > tol * max(1.0, _distance(a, b)):
        return False
    return min(a.x, b.x) - tol <= p.x <= max(a.x, b.x) + tol and min(a.y, b.y) - tol <= p.y <= max(a.y, b.y) + tol


def _point_segment_projection(p: Point2D, a: Point2D, b: Point2D) -> tuple[float, float]:
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    if length <= EPS:
        return 0.0, _distance(p, a)
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (length * length)
    t_clamped = max(0.0, min(1.0, t))
    proj = Point2D(x=a.x + t_clamped * dx, y=a.y + t_clamped * dy)
    return t_clamped * length, _distance(p, proj)


def _point_in_polygon(p: Point2D, points: list[Point2D]) -> bool:
    for a, b in zip(points, points[1:] + points[:1]):
        if _point_on_segment(p, a, b):
            return True
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        pi = points[i]
        pj = points[j]
        if (pi.y > p.y) != (pj.y > p.y):
            denominator = pj.y - pi.y
            if abs(denominator) <= EPS:
                j = i
                continue
            x_intersection = (pj.x - pi.x) * (p.y - pi.y) / denominator + pi.x
            if p.x < x_intersection:
                inside = not inside
        j = i
    return inside


def _obstacle_polygon(obstacle: ConstructionObstacle) -> list[Point2D] | None:
    if not obstacle.active:
        return None
    if obstacle.outline and len(obstacle.outline.points) >= 3:
        return _dedup_points(list(obstacle.outline.points))
    if obstacle.center and obstacle.width and obstacle.length:
        cx, cy = obstacle.center.x, obstacle.center.y
        hw = obstacle.width / 2.0 + obstacle.clearance
        hl = obstacle.length / 2.0 + obstacle.clearance
        return [
            Point2D(x=cx - hw, y=cy - hl),
            Point2D(x=cx + hw, y=cy - hl),
            Point2D(x=cx + hw, y=cy + hl),
            Point2D(x=cx - hw, y=cy + hl),
        ]
    return None


def _active_obstacle_polygons(obstacles: Iterable[ConstructionObstacle]) -> list[tuple[ConstructionObstacle, list[Point2D]]]:
    result: list[tuple[ConstructionObstacle, list[Point2D]]] = []
    for obstacle in obstacles:
        poly = _obstacle_polygon(obstacle)
        if poly:
            result.append((obstacle, poly))
    return result


def _line_segment_samples_inside(start: Point2D, end: Point2D, polygon: list[Point2D]) -> bool:
    for t in (0.20, 0.40, 0.60, 0.80):
        p = Point2D(x=start.x + (end.x - start.x) * t, y=start.y + (end.y - start.y) * t)
        if not _point_in_polygon(p, polygon):
            return False
    return True


def _line_avoids_obstacles(start: Point2D, end: Point2D, obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> bool:
    for _, poly in obstacles:
        for t in (0.15, 0.30, 0.45, 0.60, 0.75, 0.90):
            p = Point2D(x=start.x + (end.x - start.x) * t, y=start.y + (end.y - start.y) * t)
            if _point_in_polygon(p, poly):
                return False
    return True


def _point_avoids_obstacles(point: Point2D, obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> bool:
    return all(not _point_in_polygon(point, poly) for _, poly in obstacles)


def _scan_coordinate_away_from_vertices(coord: float, vertex_coords: list[float], span: float) -> float:
    nudge = max(0.05, span * 1e-4)
    shifted = coord
    for v in vertex_coords:
        if abs(shifted - v) <= nudge:
            shifted += nudge
    return shifted


def _vertical_line_intervals(points: list[Point2D], x: float) -> list[tuple[float, float]]:
    ys: list[float] = []
    for a, b in zip(points, points[1:] + points[:1]):
        x1, x2 = a.x, b.x
        if abs(x1 - x2) <= EPS:
            continue
        if min(x1, x2) <= x < max(x1, x2):
            t = (x - x1) / (x2 - x1)
            ys.append(a.y + t * (b.y - a.y))
    ys = sorted(ys)
    return [(y1, y2) for y1, y2 in zip(ys[0::2], ys[1::2]) if y2 - y1 >= MIN_MAIN_STRUT_SPAN_M]


def _horizontal_line_intervals(points: list[Point2D], y: float) -> list[tuple[float, float]]:
    xs: list[float] = []
    for a, b in zip(points, points[1:] + points[:1]):
        y1, y2 = a.y, b.y
        if abs(y1 - y2) <= EPS:
            continue
        if min(y1, y2) <= y < max(y1, y2):
            t = (y - y1) / (y2 - y1)
            xs.append(a.x + t * (b.x - a.x))
    xs = sorted(xs)
    return [(x1, x2) for x1, x2 in zip(xs[0::2], xs[1::2]) if x2 - x1 >= MIN_MAIN_STRUT_SPAN_M]


def _concave_vertex_indices(points: list[Point2D]) -> set[int]:
    """Return polygon vertex indices whose interior angle is re-entrant.

    The outline may be clockwise or counter-clockwise.  The signed-area
    orientation is therefore included in the cross-product test.  This helper
    is intentionally local to the support-layout kernel so concave-wall
    treatment is based on the same cleaned outline used by every other layout
    routine.
    """
    if len(points) < 4:
        return set()
    orientation = 1.0 if _signed_area(points) >= 0 else -1.0
    result: set[int] = set()
    for index, current in enumerate(points):
        previous = points[(index - 1) % len(points)]
        following = points[(index + 1) % len(points)]
        incoming = (current.x - previous.x, current.y - previous.y)
        outgoing = (following.x - current.x, following.y - current.y)
        cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        if cross * orientation < -EPS:
            result.add(index)
    return result


def _ray_segment_intersection(
    origin: Point2D,
    direction: tuple[float, float],
    a: Point2D,
    b: Point2D,
) -> tuple[float, Point2D] | None:
    """Return the forward ray parameter and intersection point, if any."""
    rx, ry = direction
    sx, sy = b.x - a.x, b.y - a.y
    denominator = rx * sy - ry * sx
    if abs(denominator) <= EPS:
        return None
    qx, qy = a.x - origin.x, a.y - origin.y
    ray_parameter = (qx * sy - qy * sx) / denominator
    segment_parameter = (qx * ry - qy * rx) / denominator
    if ray_parameter <= 1.0e-4 or segment_parameter < -1.0e-7 or segment_parameter > 1.0 + 1.0e-7:
        return None
    point = Point2D(x=origin.x + ray_parameter * rx, y=origin.y + ray_parameter * ry)
    return ray_parameter, point


def _face_endpoint_count(lines: list[SupportLayoutLine], excavation) -> dict[str, int]:
    """Count direct strut endpoints per excavation face for a trial layout."""
    counts = {str(segment.name): 0 for segment in getattr(excavation, "segments", [])}
    for line in lines:
        endpoints = (
            (line.start, line.start_face_code),
            (line.end, line.end_face_code),
        )
        for point, stored_face_code in endpoints:
            face_code = stored_face_code
            if not face_code:
                hit = _nearest_face_hit(point, excavation)
                face_code = hit.face_code if hit else None
            if face_code in counts:
                counts[str(face_code)] += 1
    return counts


def _concave_return_wall_layout(
    points: list[Point2D],
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    excavation,
    existing_lines: list[SupportLayoutLine],
    target_spacing: float,
) -> tuple[list[SupportLayoutLine], list[str]]:
    """Generate local face-normal struts for unsupported re-entrant walls.

    A global short-span scan works well for convex rectangles but can leave the
    return wall of an L/T-shaped excavation without a direct endpoint.  The
    wall then behaves as an unintended cantilever in every construction stage.
    This routine identifies faces adjacent to a concave vertex, checks whether
    the current trial layout directly restrains them, and casts short local
    struts along the inward face normal to the first opposite wall.

    The generated members use ``secondary_strut`` because they participate in
    the same main/secondary crossing-node and temporary-column workflow.
    """
    concave_vertices = _concave_vertex_indices(points)
    if not concave_vertices:
        return [], []
    endpoint_counts = _face_endpoint_count(existing_lines, excavation)
    orientation = 1.0 if _signed_area(points) >= 0 else -1.0
    generated: list[SupportLayoutLine] = []
    skipped: list[str] = []
    short_return_resolved: list[str] = []
    segment_count = len(points)
    candidate_segment_indices = {
        (vertex_index - 1) % segment_count for vertex_index in concave_vertices
    } | {
        vertex_index % segment_count for vertex_index in concave_vertices
    }
    segments = list(getattr(excavation, "segments", []))
    for segment_index in sorted(candidate_segment_indices):
        if segment_index >= len(segments):
            continue
        segment = segments[segment_index]
        face_code = str(segment.name)
        # Existing direct endpoints already provide a load path for this face.
        if endpoint_counts.get(face_code, 0) > 0:
            continue
        a, b = points[segment_index], points[(segment_index + 1) % segment_count]
        length = _distance(a, b)
        if length < MIN_MAIN_STRUT_SPAN_M:
            # Very short re-entrant returns cannot accommodate an independent
            # face-normal strut. Treat them as a corner transfer zone only when
            # an existing support endpoint is close to either return-wall end;
            # otherwise retain an unresolved warning.
            nearby_limit = max(float(target_spacing), 6.0)
            endpoints = [point for line in existing_lines for point in (line.start, line.end)]
            served = any(min(_distance(point, a), _distance(point, b)) <= nearby_limit for point in endpoints)
            if served:
                short_return_resolved.append(face_code)
            else:
                skipped.append(face_code)
            continue
        ux, uy = _unit_vector(a, b)
        inward = (-uy, ux) if orientation > 0 else (uy, -ux)
        # Keep supports in a practical 3-6 m bay while avoiding endpoints where
        # wall joints and corner reinforcement are usually congested.
        count = max(1, int(math.ceil(length / max(target_spacing, 1.0))) - 1)
        count = min(count, 8)
        bay = length / (count + 1)
        face_lines: list[SupportLayoutLine] = []
        for index in range(1, count + 1):
            start = Point2D(x=a.x + ux * bay * index, y=a.y + uy * bay * index)
            probe_origin = Point2D(x=start.x + inward[0] * 0.02, y=start.y + inward[1] * 0.02)
            intersections: list[tuple[float, Point2D, int]] = []
            for other_index, (edge_a, edge_b) in enumerate(zip(points, points[1:] + points[:1])):
                if other_index == segment_index:
                    continue
                hit = _ray_segment_intersection(probe_origin, inward, edge_a, edge_b)
                if hit:
                    intersections.append((hit[0], hit[1], other_index))
            if not intersections:
                continue
            _, end, _ = min(intersections, key=lambda item: item[0])
            start = Point2D(x=round(start.x, 3), y=round(start.y, 3))
            end = Point2D(x=round(end.x, 3), y=round(end.y, 3))
            span = _distance(start, end)
            if span < MIN_MAIN_STRUT_SPAN_M:
                continue
            if not _line_segment_samples_inside(start, end, points):
                continue
            if not _line_avoids_obstacles(start, end, obstacles):
                continue
            face_lines.append(
                SupportLayoutLine(
                    "secondary_strut",
                    start,
                    end,
                    round(span, 3),
                    round(bay, 3),
                    f"凹形基坑回墙 {face_code} 局部法向对撑：补足直接传力路径，避免回墙按无支撑悬臂计算。",
                )
            )
        if face_lines:
            generated.extend(face_lines)
        else:
            skipped.append(face_code)
    warnings: list[str] = []
    if generated:
        warnings.append(
            f"检测到凹形基坑回墙缺少直接支点，已增加 {len(generated)} 条局部法向次对撑；"
            "与主对撑交点自动设置共享节点/临时立柱。"
        )
    if short_return_resolved:
        warnings.append(
            f"已将凹角短回墙 {', '.join(sorted(set(short_return_resolved)))} 识别为角部传力区；"
            "邻近主支撑端点承担回墙约束，施工图需在围檩刚域和节点大样中明确加强构造。"
        )
    if skipped:
        warnings.append(f"凹角相邻墙面 {', '.join(sorted(set(skipped)))} 未能自动形成有效法向对撑，需人工布置或调整障碍边界。")
    return generated, warnings


def _main_support_count(long_span: float, target_spacing: float | None = None) -> int:
    if long_span <= EPS:
        return 0
    if target_spacing is None:
        target_spacing = TARGET_MAIN_SUPPORT_SPACING_M
    # Practical layout target: main strut bays are normally dense enough for
    # formwork, excavation logistics and deformation control.  Keep automatic
    # bay spacing in the 3-6 m band instead of the previous very sparse 18 m
    # engineering-screening layout.
    count = max(1, int(math.ceil(long_span / target_spacing)) - 1)
    spacing = long_span / (count + 1)
    while spacing > MAX_PRACTICAL_MAIN_SUPPORT_SPACING_M and count < MAX_AUTO_MAIN_STRUTS_PER_LEVEL:
        count += 1
        spacing = long_span / (count + 1)
    while count > 1 and spacing < MIN_PRACTICAL_MAIN_SUPPORT_SPACING_M:
        candidate = count - 1
        candidate_spacing = long_span / (candidate + 1)
        if candidate_spacing > MAX_PRACTICAL_MAIN_SUPPORT_SPACING_M:
            break
        count = candidate
        spacing = candidate_spacing
    return max(1, min(MAX_AUTO_MAIN_STRUTS_PER_LEVEL, count))




def _candidate_shift_offsets(span: float) -> list[float]:
    step = 0.5
    limit = min(3.0, max(1.0, span * 0.04))
    offsets = [0.0]
    k = 1
    while k * step <= limit + EPS:
        offsets.extend([k * step, -k * step])
        k += 1
    return offsets


def _find_viable_main_line(
    *,
    points: list[Point2D],
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    long_is_x: bool,
    base_coord: float,
    min_coord: float,
    max_coord: float,
    span: float,
) -> tuple[list[tuple[Point2D, Point2D]], float, bool]:
    """Return valid short-span line segments, shifting away from obstacles when necessary."""
    for offset in _candidate_shift_offsets(span):
        coord = min(max(base_coord + offset, min_coord + 0.25), max_coord - 0.25)
        segments: list[tuple[Point2D, Point2D]] = []
        if long_is_x:
            intervals = _vertical_line_intervals(points, coord)
            for y1, y2 in intervals:
                start = Point2D(x=round(coord, 3), y=round(y1, 3))
                end = Point2D(x=round(coord, 3), y=round(y2, 3))
                if _line_segment_samples_inside(start, end, points) and _line_avoids_obstacles(start, end, obstacles):
                    segments.append((start, end))
        else:
            intervals = _horizontal_line_intervals(points, coord)
            for x1, x2 in intervals:
                start = Point2D(x=round(x1, 3), y=round(coord, 3))
                end = Point2D(x=round(x2, 3), y=round(coord, 3))
                if _line_segment_samples_inside(start, end, points) and _line_avoids_obstacles(start, end, obstacles):
                    segments.append((start, end))
        if segments:
            return segments, coord, abs(offset) > EPS
    return [], base_coord, False


def _find_viable_oriented_line(
    *,
    points: list[Point2D],
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    axes: PlanAxes,
    base_coord: float,
    scan_along_long_axis: bool,
) -> tuple[list[tuple[Point2D, Point2D]], float, bool]:
    """Cast a support scanline in the polygon's local principal coordinates."""
    local_points = [_local_coordinates(point, axes) for point in points]
    span = axes.long_span if scan_along_long_axis else axes.short_span
    low = axes.long_min if scan_along_long_axis else axes.short_min
    high = axes.long_max if scan_along_long_axis else axes.short_max
    for offset in _candidate_shift_offsets(span):
        coord = min(max(base_coord + offset, low + 0.25), high - 0.25)
        intervals = _vertical_line_intervals(local_points, coord) if scan_along_long_axis else _horizontal_line_intervals(local_points, coord)
        segments: list[tuple[Point2D, Point2D]] = []
        for first, second in intervals:
            a_local = Point2D(x=coord, y=first) if scan_along_long_axis else Point2D(x=first, y=coord)
            b_local = Point2D(x=coord, y=second) if scan_along_long_axis else Point2D(x=second, y=coord)
            start = _global_coordinates(a_local, axes)
            end = _global_coordinates(b_local, axes)
            start = Point2D(x=round(start.x, 3), y=round(start.y, 3))
            end = Point2D(x=round(end.x, 3), y=round(end.y, 3))
            if _line_segment_samples_inside(start, end, points) and _line_avoids_obstacles(start, end, obstacles):
                segments.append((start, end))
        if segments:
            return segments, coord, abs(offset) > EPS
    return [], base_coord, False


def _nearest_face_hit(point: Point2D, excavation, tolerance: float = 0.75) -> SegmentFaceHit | None:
    """Return the connected wall face only when the point is actually near it.

    Earlier versions returned the nearest face even for an internal grid node.
    That silently converted support-to-support joints into false wall endpoints,
    duplicated tributary widths and could leave one fail per wall face after an
    optimized scheme was adopted.
    """
    best: SegmentFaceHit | None = None
    best_dist = float("inf")
    for segment in getattr(excavation, "segments", []):
        t, dist = _point_segment_projection(point, segment.start, segment.end)
        if dist < best_dist:
            best_dist = dist
            best = SegmentFaceHit(face_code=segment.name, t=t, length=float(segment.length))
    return best if best and best_dist <= max(float(tolerance), EPS) else None


def _endpoint_on_other_support_interior(
    point: Point2D,
    owner: SupportLayoutLine,
    lines: list[SupportLayoutLine],
    *,
    tolerance: float = 2.5e-3,
) -> bool:
    """Return True when an endpoint is an internal T/Y support node.

    Endpoint metadata must follow structural connectivity.  A clipped diagonal
    may finish close to a return wall while its actual reaction is delivered to
    the interior of a main support.  Distance-to-wall alone would misclassify
    that node as a wall bearing and corrupt tributary widths.
    """
    for other in lines:
        if other is owner:
            continue
        length = _distance(other.start, other.end)
        if length <= EPS:
            continue
        station, distance = _point_segment_projection(point, other.start, other.end)
        if distance <= tolerance and tolerance < station < length - tolerance:
            return True
    return False


def _attach_faces(
    lines: list[SupportLayoutLine],
    excavation,
    *,
    wall_capture_tolerance_m: float = 1.10,
) -> None:
    """Attach actual wall endpoints and clear internal T/Y node metadata.

    Raw corner braces can be snapped to a neighbouring wall node before the
    nominal 1.0 m centre-line clearance is applied.  Their coordinates may be
    about 0.8 m from the exact wall line, so a narrow 0.75 m search loses a
    legitimate wall connection.  A wider capture band is safe only when an
    endpoint lying on another support interior is explicitly classified as a
    T/Y node first.
    """
    tolerance = max(0.75, float(wall_capture_tolerance_m))
    for line in lines:
        start_is_ty = _endpoint_on_other_support_interior(line.start, line, lines)
        end_is_ty = _endpoint_on_other_support_interior(line.end, line, lines)
        s_hit = None if start_is_ty else _nearest_face_hit(line.start, excavation, tolerance=tolerance)
        e_hit = None if end_is_ty else _nearest_face_hit(line.end, excavation, tolerance=tolerance)
        # Reattachment follows the current clipped geometry.  An endpoint that
        # has been shortened to a T/Y node must lose its historical wall-face
        # metadata; retaining the old face silently creates a zero-clearance
        # rigid arm and corrupts tributary-width assignment.
        line.start_face_code = s_hit.face_code if s_hit else None
        line.end_face_code = e_hit.face_code if e_hit else None


def _trim_endpoint_from_wall(point: Point2D, other: Point2D, segment, clearance: float) -> tuple[Point2D, float]:
    ux, uy = _unit_vector(point, other)
    inward_x = -float(segment.outward_normal.x)
    inward_y = -float(segment.outward_normal.y)
    projection = ux * inward_x + uy * inward_y
    if projection <= 0.08:
        projection = abs(projection)
    trim = clearance / max(projection, 0.25)
    trim = min(trim, max(0.25, _distance(point, other) * 0.45))
    shifted = Point2D(x=round(point.x + ux * trim, 4), y=round(point.y + uy * trim, 4))
    _chainage, actual = _point_segment_projection(shifted, segment.start, segment.end)
    return shifted, actual


def _apply_support_wall_clearance(lines: list[SupportLayoutLine], excavation, config: SupportLayoutConfig) -> list[str]:
    warnings: list[str] = []
    by_code = {str(segment.name): segment for segment in getattr(excavation, "segments", [])}
    adjusted = 0
    for line in lines:
        line.topology_family = config.topology_strategy
        line.centerline_offset_m = config.support_wall_clearance_m
        original_start = line.start
        original_end = line.end
        start_segment = by_code.get(str(line.start_face_code or ""))
        end_segment = by_code.get(str(line.end_face_code or ""))
        line.start_wall_connection = original_start if start_segment else None
        line.end_wall_connection = original_end if end_segment else None
        line.start_wall_clearance_m = None
        line.end_wall_clearance_m = None
        if start_segment:
            line.start, line.start_wall_clearance_m = _trim_endpoint_from_wall(original_start, original_end, start_segment, config.support_wall_clearance_m)
        if end_segment:
            line.end, line.end_wall_clearance_m = _trim_endpoint_from_wall(original_end, original_start, end_segment, config.support_wall_clearance_m)
        line.span_length = round(_distance(line.start, line.end), 3)
        if start_segment or end_segment:
            adjusted += 1
            line.layout_note = (line.layout_note or "") + f" 支撑中心线已从围护墙/围檩连接线向坑内退让约 {config.support_wall_clearance_m:.2f}m，采用刚臂节点传力。"
    if adjusted:
        warnings.append(f"已将 {adjusted} 条平面支撑中心线向坑内偏移，避免与围护墙重合；墙面连接点作为刚臂节点保留。")
    return warnings


def _finalize_retained_support_endpoints(
    lines: list[SupportLayoutLine],
    excavation,
    config: SupportLayoutConfig,
) -> list[str]:
    """Rebuild endpoint semantics after crossing/constructability filtering.

    A line used as the T/Y blocker can itself be removed by a later
    constructability gate.  Its former terminal stub would then become a
    dangling endpoint.  When that endpoint is still within the wall connection
    band, recover it as a real wall endpoint and enforce the configured
    perpendicular clearance.  Endpoints that remain on another retained member
    stay as T/Y nodes and never receive false wall metadata.
    """
    if not lines:
        return []
    target = float(config.support_wall_clearance_m)
    _attach_faces(
        lines,
        excavation,
        wall_capture_tolerance_m=max(1.10, target + 0.20),
    )
    by_code = {str(segment.name): segment for segment in getattr(excavation, "segments", [])}
    recovered = 0
    shifted = 0
    for line in lines:
        for side in ("start", "end"):
            point = getattr(line, side)
            other = line.end if side == "start" else line.start
            face_code = getattr(line, f"{side}_face_code")
            if not face_code:
                setattr(line, f"{side}_wall_connection", None)
                setattr(line, f"{side}_wall_clearance_m", None)
                continue
            segment = by_code.get(str(face_code))
            if segment is None:
                setattr(line, f"{side}_face_code", None)
                setattr(line, f"{side}_wall_connection", None)
                setattr(line, f"{side}_wall_clearance_m", None)
                continue
            station, distance = _point_segment_projection(point, segment.start, segment.end)
            length = max(float(getattr(segment, "length", 0.0) or _distance(segment.start, segment.end)), EPS)
            ratio = max(0.0, min(1.0, station / length))
            projected_wall_point = Point2D(
                x=round(segment.start.x + (segment.end.x - segment.start.x) * ratio, 4),
                y=round(segment.start.y + (segment.end.y - segment.start.y) * ratio, 4),
            )
            previous_connection = getattr(line, f"{side}_wall_connection")
            # Preserve the original wale bearing station created before the
            # support centreline was trimmed inward. Re-projecting the trimmed
            # endpoint moves the connection along the wall and can falsely open
            # a wale bay, triggering an unnecessary third/fan brace.
            if previous_connection is not None:
                _old_station, old_distance = _point_segment_projection(previous_connection, segment.start, segment.end)
                wall_point = previous_connection if old_distance <= 0.10 else projected_wall_point
            else:
                wall_point = projected_wall_point
                recovered += 1
            if distance + 1e-6 < target * 0.90:
                adjusted, actual = _trim_endpoint_from_wall(wall_point, other, segment, target)
                setattr(line, side, adjusted)
                distance = actual
                shifted += 1
            setattr(line, f"{side}_wall_connection", wall_point)
            setattr(line, f"{side}_wall_clearance_m", round(float(distance), 4))
        line.centerline_offset_m = target
        line.topology_family = config.topology_strategy
        line.span_length = round(_distance(line.start, line.end), 3)
    messages: list[str] = []
    if recovered:
        messages.append(f"构造筛选后恢复 {recovered} 个有效围檩/墙面连接端点，清除已失效的悬空T/Y端点语义。")
    if shifted:
        messages.append(f"其中 {shifted} 个端点重新满足 {target:.2f}m 支撑中心线净距。")
    return messages


def _endpoint_connected_to_retained_support(
    point: Point2D,
    owner: SupportLayoutLine,
    lines: list[SupportLayoutLine],
    *,
    tolerance: float = 1.0e-2,
) -> bool:
    for other in lines:
        if other is owner:
            continue
        if _point_on_segment(point, other.start, other.end, tol=tolerance):
            return True
    return False


def _prune_dangling_non_ring_supports(
    lines: list[SupportLayoutLine],
) -> tuple[list[SupportLayoutLine], list[str]]:
    """Remove members whose endpoint has neither wall bearing nor support node.

    Principal-axis scan lines can occasionally terminate exactly at a concave
    polygon vertex.  Applying wall clearance at that near-tangent corner may
    leave a centre-line endpoint several metres from both adjacent walls.  Such
    a member has no valid reaction point and must not survive into calculation.
    The pruning is iterative because removing one invalid member can expose a
    dependent terminal stub.
    """
    retained = list(lines)
    removed: list[SupportLayoutLine] = []
    while True:
        invalid: list[SupportLayoutLine] = []
        for line in retained:
            if line.role == "ring_strut":
                continue
            start_ok = bool(line.start_face_code) or _endpoint_connected_to_retained_support(line.start, line, retained)
            end_ok = bool(line.end_face_code) or _endpoint_connected_to_retained_support(line.end, line, retained)
            if not (start_ok and end_ok):
                invalid.append(line)
        if not invalid:
            break
        invalid_ids = {id(item) for item in invalid}
        retained = [item for item in retained if id(item) not in invalid_ids]
        removed.extend(invalid)
    messages: list[str] = []
    if removed:
        roles: dict[str, int] = {}
        for line in removed:
            roles[line.role] = roles.get(line.role, 0) + 1
        role_text = "、".join(f"{role} {count} 条" for role, count in sorted(roles.items()))
        messages.append(
            f"已删除 {len(removed)} 条无有效墙端或T/Y节点的悬空非环形支撑（{role_text}），"
            "避免凹角切线端点进入计算模型。"
        )
    return retained, messages


def _filter_unconstructible_trimmed_lines(
    lines: list[SupportLayoutLine],
    excavation,
    config: SupportLayoutConfig,
) -> tuple[list[SupportLayoutLine], list[str]]:
    """Reject members that collapse while their centreline is trimmed from walls.

    A very short corner fan can geometrically intersect two adjacent wall faces,
    yet it cannot provide the requested centreline clearance at both ends.  The
    previous implementation kept such a member after the trim was capped at 45%
    of its length.  The resulting 1--2 m pseudo brace was then reported as a hard
    wall-clearance failure by the quality gate.

    Keeping that member is unsafe and also misleading: the corresponding short
    corner segment is already transferred through the closed wale/corner node.
    This post-trim constructability gate therefore removes only members that:

    * cannot reach 90% of the requested wall clearance at a connected end;
    * collapse below the minimum practical corner-brace leg; or
    * leave the excavation polygon after trimming.

    The wall-connection points remain available on every retained member, so the
    rigid-arm node used by the calculation model is unaffected.
    """

    points = _dedup_points(list(excavation.outline.points))
    target = float(config.support_wall_clearance_m)
    minimum_clearance = max(0.20, target * 0.90)
    minimum_span = max(MIN_CORNER_BRACE_LEG_M, target * 2.0 + 0.25)
    retained: list[SupportLayoutLine] = []
    rejected: list[SupportLayoutLine] = []
    for line in lines:
        connected_clearances = [
            value
            for face, value in (
                (line.start_face_code, line.start_wall_clearance_m),
                (line.end_face_code, line.end_wall_clearance_m),
            )
            if face and value is not None
        ]
        clearance_ok = all(float(value) + 1e-6 >= minimum_clearance for value in connected_clearances)
        local_node_tie = any(token in str(line.layout_note or "") for token in (
            "T/Y节点", "止于首个既有支撑节点", "止于既有主支撑节点",
        ))
        required_span = max(1.50, target + 0.50) if local_node_tie else minimum_span
        span_ok = float(line.span_length or _distance(line.start, line.end)) + 1e-6 >= required_span
        inside_ok = _line_segment_samples_inside(line.start, line.end, points)
        if clearance_ok and span_ok and inside_ok:
            retained.append(line)
        else:
            rejected.append(line)
    warnings: list[str] = []
    if rejected:
        warnings.append(
            f"已剔除 {len(rejected)} 条净距退让后失去可施工长度的短角撑候选；"
            "对应短回墙由闭合围檩与角部刚性节点传力，不生成伪构件。"
        )
    return retained, warnings


def _hybridize_long_struts(lines: list[SupportLayoutLine], points: list[Point2D], config: SupportLayoutConfig) -> tuple[list[SupportLayoutLine], list[str]]:
    """Keep the continuous short-span strut grid and add corner braces as local members.

    Earlier versions deleted long main struts near the two ends whenever a
    corner brace existed.  In stepped elongated pits that created 20--30 m
    unsupported wale bays: a short corner brace cannot replace the direct wall
    reaction of several main struts.  Long members are now retained and their
    effective unbraced length is controlled by columns; corner braces remain
    local supplementary load paths.
    """
    if config.topology_strategy != "hybrid_diagonal":
        return lines, []
    diagonal_count = sum(line.role == "corner_diagonal" for line in lines)
    if diagonal_count == 0:
        return lines, ["混合斜撑策略未找到满足墙长和角度条件的斜撑，保留原对撑体系。"]
    return lines, [
        f"混合斜撑策略保留完整短跨直对撑网格，并叠加 {diagonal_count} 条局部墙—墙角撑；"
        "角撑不再替代端部主对撑，长支撑通过临时立柱控制有效无侧向长度。"
    ]

def _assign_tributary_widths(supports: list[SupportElement], excavation) -> None:
    endpoint_items: dict[tuple[int, str], list[tuple[SupportElement, str, float, float]]] = {}
    # key: (level, face), value: (support, endpoint, chainage, face length)
    for support in supports:
        for endpoint_name, point, face_code in (
            ("start", support.start, support.start_face_code),
            ("end", support.end, support.end_face_code),
        ):
            if not face_code:
                continue
            segment = next((s for s in excavation.segments if s.name == face_code), None)
            if not segment:
                continue
            chainage, _ = _point_segment_projection(point, segment.start, segment.end)
            endpoint_items.setdefault((support.level_index, face_code), []).append((support, endpoint_name, chainage, float(segment.length)))
    for (_level, _face), items in endpoint_items.items():
        items = sorted(items, key=lambda item: item[2])
        n = len(items)
        for idx, (support, endpoint_name, chainage, face_length) in enumerate(items):
            if n == 1:
                width = face_length
            else:
                left = 0.0 if idx == 0 else 0.5 * (items[idx - 1][2] + chainage)
                right = face_length if idx == n - 1 else 0.5 * (chainage + items[idx + 1][2])
                width = max(0.5, right - left)
            if endpoint_name == "start":
                support.start_tributary_width = round(width, 3)
            else:
                support.end_tributary_width = round(width, 3)
            support.force_distribution_note = "V1.6 支撑轴力由围檩连续梁-弹性支座节点反力计算；tributary width 仅作为节点位置和结果解释的参考。"


def _design_station_coordinates(points: list[Point2D], axes: PlanAxes, target_spacing: float, config: SupportLayoutConfig | None = None) -> list[tuple[float, str]]:
    """Generate support stations with denser transition zones at plan steps.

    A uniform array is acceptable in a prismatic strip, but stepped/necked pits
    need explicit stations around width changes.  The planner keeps field bays
    close to the requested spacing and adds transition stations on both sides of
    significant outline vertices.  Nearby stations are merged to avoid short,
    unconstructible bays.
    """
    cfg = (config or SupportLayoutConfig()).normalized()
    span = max(axes.long_span, EPS)
    base_count = _main_support_count(span, target_spacing)
    base = [axes.long_min + (i + 1) * span / (base_count + 1) for i in range(base_count)] if base_count > 0 else []
    local = [_local_coordinates(p, axes) for p in _dedup_points(points)]
    transition: list[float] = []
    influence = max(3.0, min(12.0, float(getattr(cfg, 'transition_zone_influence_m', 8.0) if hasattr(cfg, 'transition_zone_influence_m') else 8.0)))
    # Detect vertices where adjacent edges have materially different short-axis
    # coordinates.  These are necks, returns or local width transitions.
    n = len(local)
    for i, cur in enumerate(local):
        prev = local[(i - 1) % n]
        nxt = local[(i + 1) % n]
        dx1, dy1 = cur.x - prev.x, cur.y - prev.y
        dx2, dy2 = nxt.x - cur.x, nxt.y - cur.y
        turn = abs(dx1 * dy2 - dy1 * dx2)
        width_change = abs(dy1) + abs(dy2)
        if turn <= 0.15 and width_change <= 0.25:
            continue
        for sign in (-1.0, 1.0):
            value = cur.x + sign * min(influence * 0.45, target_spacing * 0.55)
            if axes.long_min + 0.8 < value < axes.long_max - 0.8:
                transition.append(value)
    rows = [(x, 'field') for x in base] + [(x, 'transition') for x in transition]
    rows.sort(key=lambda item: item[0])
    minimum = max(2.5, min(4.0, float(getattr(cfg, 'support_min_station_separation_m', 2.8) if hasattr(cfg, 'support_min_station_separation_m') else 2.8)))
    merged: list[tuple[float, str]] = []
    for value, kind in rows:
        if not merged or value - merged[-1][0] >= minimum:
            merged.append((value, kind))
        elif kind == 'transition' and merged[-1][1] != 'transition':
            merged[-1] = ((merged[-1][0] + value) * 0.5, 'transition')
    return merged[:max(1, MAX_AUTO_MAIN_STRUTS_PER_LEVEL * 2)]


def _main_strut_layout(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]], target_spacing: float = TARGET_MAIN_SUPPORT_SPACING_M, config: SupportLayoutConfig | None = None) -> tuple[list[SupportLayoutLine], list[str]]:
    axes = _plan_axes(points)
    warnings: list[str] = []
    stations = _design_station_coordinates(points, axes, target_spacing, config=config)
    count = len(stations)
    if count <= 0:
        return [], ["基坑主轴方向尺寸过小，未自动生成主对撑。"]
    bay_spacing = axes.long_span / (count + 1)
    local_vertices = [_local_coordinates(point, axes) for point in points]
    vertex_coords = [point.x for point in local_vertices]
    lines: list[SupportLayoutLine] = []
    skipped_for_obstacle = 0
    shifted_count = 0
    for i, (planned_coordinate, station_kind) in enumerate(stations):
        coordinate = _scan_coordinate_away_from_vertices(
            planned_coordinate,
            vertex_coords,
            axes.long_span,
        )
        segments, used, shifted = _find_viable_oriented_line(
            points=points,
            obstacles=obstacles,
            axes=axes,
            base_coord=coordinate,
            scan_along_long_axis=True,
        )
        if not segments:
            skipped_for_obstacle += 1
            continue
        shifted_count += int(shifted)
        for start, end in segments:
            note = (
                "主对撑按基坑平面主轴识别结果沿局部短跨方向布置，沿局部长轴按 3-6m 工程常用分仓间距布置；"
                f"主轴旋转角 {axes.rotation_deg:.2f}°，识别方法 {axes.method}。"
            )
            if shifted:
                note += f" 扫描线已沿局部长轴移动至 {used:.2f}m 以避让障碍/出土口。"
            lines.append(
                SupportLayoutLine(
                    "main_strut",
                    start,
                    end,
                    round(_distance(start, end), 3),
                    round(bay_spacing, 3),
                    note,
                    design_zone="transition" if station_kind == "transition" else "field",
                    station_chainage_m=round(float(used - axes.long_min), 3),
                    local_clear_span_m=round(_distance(start, end), 3),
                    placement_reason=("平面台阶/颈缩过渡区加密" if station_kind == "transition" else "规则场区短跨对撑"),
                    load_path_class="wall_to_wall",
                )
            )
    if not lines:
        warnings.append("未能从基坑轮廓自动生成有效主对撑；请检查凹多边形、障碍避让或手动布置支撑。")
    if shifted_count:
        warnings.append(f"支撑布置自动修复器已移动 {shifted_count} 条主支撑扫描线，以避让柱网、坡道或出土口。")
    if skipped_for_obstacle:
        warnings.append(f"已因障碍或轮廓限制跳过 {skipped_for_obstacle} 条候选主支撑。")
    if len(lines) > count:
        warnings.append("检测到凹形或分叉基坑，部分主轴扫描线已拆分为多个独立对撑，避免跨越坑外空区。")
    warnings.append(
        f"平面主轴诊断：长轴 {axes.long_span:.2f}m、短轴 {axes.short_span:.2f}m、旋转角 {axes.rotation_deg:.2f}°；支撑方向不再依赖全局 X/Y 包围盒。"
    )
    return lines, warnings

def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    l1 = math.hypot(*v1)
    l2 = math.hypot(*v2)
    if l1 <= EPS or l2 <= EPS:
        return 0.0
    dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
    return math.degrees(math.acos(dot))




def _should_use_bidirectional_grid(points: list[Point2D], excavation) -> bool:
    """Return True when a deep elongated pit needs direct restraint on return walls."""
    axes = _plan_axes(points)
    aspect = axes.long_span / max(axes.short_span, EPS)
    depth = abs(float(getattr(excavation, "top_elevation", 0.0)) - float(getattr(excavation, "bottom_elevation", 0.0)))
    diagnostics = plan_shape_diagnostics(points)
    return (
        not bool(diagnostics.get("circularShaftLike"))
        and axes.short_span >= 28.0
        and depth >= 15.0
        and aspect <= 1.35
    )


def _secondary_grid_layout(
    points: list[Point2D],
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    excavation,
    force: bool = False,
) -> tuple[list[SupportLayoutLine], list[str]]:
    """Generate an orthogonal support family in the local principal-axis frame."""
    if not force and not _should_use_bidirectional_grid(points, excavation):
        return [], []
    axes = _plan_axes(points)
    count = max(1, min(4, int(math.ceil(axes.short_span / 15.0)) - 1))
    spacing = axes.short_span / (count + 1)
    local_vertices = [_local_coordinates(point, axes) for point in points]
    vertex_coords = [point.y for point in local_vertices]
    lines: list[SupportLayoutLine] = []
    shifted_count = 0
    skipped = 0
    for idx in range(count):
        coordinate = _scan_coordinate_away_from_vertices(
            axes.short_min + (idx + 1) * spacing,
            vertex_coords,
            axes.short_span,
        )
        segments, used, shifted = _find_viable_oriented_line(
            points=points,
            obstacles=obstacles,
            axes=axes,
            base_coord=coordinate,
            scan_along_long_axis=False,
        )
        if not segments:
            skipped += 1
            continue
        shifted_count += int(shifted)
        for start, end in segments:
            note = (
                "深大或长条形基坑局部主轴正交次对撑：直接约束回墙；"
                "与主对撑交点设置临时立柱/刚性节点，避免角撑独担整面回墙荷载。"
            )
            if shifted:
                note += f" 已沿局部短轴移动至 {used:.2f}m 以避让障碍。"
            lines.append(SupportLayoutLine("secondary_strut", start, end, round(_distance(start, end), 3), round(spacing, 3), note))
    warnings: list[str] = []
    if lines:
        warnings.append(f"已增加 {len(lines)} 条局部主轴正交次对撑，形成双向支撑网格并缩短回墙围檩无支点跨度。")
    if shifted_count:
        warnings.append(f"其中 {shifted_count} 条次对撑已自动平移避让障碍。")
    if skipped:
        warnings.append(f"有 {skipped} 条次对撑因障碍或轮廓限制未生成，需人工复核回墙传力。")
    return lines, warnings

def _corner_family_offsets(min_leg: float, config: SupportLayoutConfig, target_bay: float) -> list[float]:
    """Return staggered offsets for a parallel corner-brace family.

    Equal chainages on the two adjacent walls generate a set of parallel members
    for any corner angle.  This matches conventional end-bay detailing: each
    diagonal has its own bearing node and no V/fan convergence is introduced.
    """
    max_offset = min(
        float(config.corner_diagonal_max_offset_m),
        float(config.corner_diagonal_max_wall_fraction) * min_leg,
        max(1.15 * target_bay, float(config.corner_diagonal_min_offset_m)),
    )
    min_offset = min(float(config.corner_diagonal_min_offset_m), max_offset)
    if max_offset < MIN_CORNER_BRACE_LEG_M:
        return []
    spacing = float(config.corner_diagonal_family_spacing_m)
    requested = int(config.corner_diagonal_family_count)
    available = 1 + int(max(0.0, max_offset - min_offset) // spacing)
    count = max(1, min(requested, available))
    if count == 1:
        return [round(max(min_offset, min(0.60 * target_bay, max_offset)), 3)]
    first = max(min_offset, min(0.55 * target_bay, max_offset - spacing * (count - 1)))
    values = [first + idx * spacing for idx in range(count)]
    if values[-1] > max_offset + EPS:
        values = [max_offset - spacing * (count - 1) + idx * spacing for idx in range(count)]
    return [round(value, 3) for value in values if min_offset - EPS <= value <= max_offset + EPS]


def _corner_diagonal_layout(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]], config: SupportLayoutConfig | None = None) -> list[SupportLayoutLine]:
    """Generate independent parallel wall-to-wall braces in eligible corner bays.

    A valid corner family is composed of one or more parallel compression
    members.  Every member has a distinct bearing point on each adjacent wale.
    Fan-shaped braces sharing a wall node are prohibited because they create a
    concentrated wale reaction, congested bearing hardware and an unclear axial
    load path.
    """
    config = (config or SupportLayoutConfig()).normalized()
    if len(points) < 4 or not config.prefer_diagonal_braces:
        return []
    _, _, _, _, span_x, span_y = _bounds(points)
    short_span, long_span = min(span_x, span_y), max(span_x, span_y)
    if short_span < 12.0:
        return []
    orientation = 1.0 if _signed_area(points) >= 0 else -1.0
    elongated = (long_span / max(short_span, EPS) >= 1.35) or long_span >= 36.0
    if not elongated and config.topology_strategy not in {"hybrid_diagonal", "balanced_grid"}:
        return []

    lines: list[SupportLayoutLine] = []
    n = len(points)
    target_bay = float(config.max_wale_support_bay_m)
    for idx, curr in enumerate(points):
        prev = points[(idx - 1) % n]
        nxt = points[(idx + 1) % n]
        edge_prev = (curr.x - prev.x, curr.y - prev.y)
        edge_next = (nxt.x - curr.x, nxt.y - curr.y)
        cross = edge_prev[0] * edge_next[1] - edge_prev[1] * edge_next[0]
        if cross * orientation <= 0:
            continue
        angle = _angle_between((prev.x - curr.x, prev.y - curr.y), (nxt.x - curr.x, nxt.y - curr.y))
        if not (RIGHT_ANGLE_MIN_DEG <= angle <= RIGHT_ANGLE_MAX_DEG):
            continue
        len_prev, len_next = _distance(curr, prev), _distance(curr, nxt)
        min_leg = min(len_prev, len_next)
        if min_leg < max(config.diagonal_brace_min_wall_length_m * 0.45, 2.0 * MIN_CORNER_BRACE_LEG_M):
            continue

        offsets = _corner_family_offsets(min_leg, config, target_bay)
        for family_index, offset in enumerate(offsets, start=1):
            p1 = _point_at(curr, prev, offset)
            p2 = _point_at(curr, nxt, offset)
            if not (_line_segment_samples_inside(p1, p2, points) and _line_avoids_obstacles(p1, p2, obstacles)):
                continue
            note = (
                f"凸角平行角撑组第 {family_index}/{len(offsets)} 道：两端分别支承于相邻围檩/围护墙，"
                f"两侧端点距转角均约 {offset:.2f}m；各道角撑不得共用墙上节点、不得形成V形扇撑、"
                "不得截断至另一水平支撑。"
            )
            lines.append(
                SupportLayoutLine(
                    "corner_diagonal",
                    Point2D(x=round(p1.x, 3), y=round(p1.y, 3)),
                    Point2D(x=round(p2.x, 3), y=round(p2.y, 3)),
                    round(_distance(p1, p2), 3),
                    round(float(config.corner_diagonal_family_spacing_m), 3) if len(offsets) > 1 else None,
                    note,
                    topology_family="hybrid_diagonal",
                    design_zone=f"corner-{idx + 1}",
                    placement_reason="parallel_corner_brace_family",
                )
            )
    return lines

def _center_island_polygon(obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> list[Point2D] | None:
    for obstacle, poly in obstacles:
        if obstacle.obstacle_type == "center_island":
            return poly
    return None


def _should_use_ring(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> bool:
    # A large square or near-square excavation must not be classified as a shaft
    # merely from its bounding-box aspect ratio.  Ring support is selected only
    # for an explicit centre island or a geometrically shaft-like polygon.
    return bool(_center_island_polygon(obstacles)) or bool(plan_shape_diagnostics(points).get("circularShaftLike"))


def _ring_rectangle(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y, span_x, span_y = _bounds(points)
    island = _center_island_polygon(obstacles)
    if island:
        ix0, iy0, ix1, iy1, _, _ = _bounds(island)
        margin = 3.0
        return ix0 - margin, iy0 - margin, ix1 + margin, iy1 + margin
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    hx, hy = max(5.0, span_x * 0.18), max(5.0, span_y * 0.18)
    return cx - hx, cy - hy, cx + hx, cy + hy


def _ring_layout(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> tuple[list[SupportLayoutLine], list[str]]:
    min_x, min_y, max_x, max_y, span_x, span_y = _bounds(points)
    lines: list[SupportLayoutLine] = []
    # For circular or multi-sided shaft approximations, bounding-box radials may
    # start exactly at vertices and be rejected by the polygon-inclusion sampler.
    # Use edge-midpoint radials toward an inner ring proxy instead; this preserves
    # the normative workflow without requiring a finite-element shaft model.
    if len(points) > 4 and max(span_x, span_y) / max(min(span_x, span_y), EPS) <= 1.25:
        cx = sum(p.x for p in points) / len(points)
        cy = sum(p.y for p in points) / len(points)
        inner_ratio = 0.42
        for a, b in zip(points, points[1:] + points[:1]):
            mid = Point2D(x=(a.x + b.x) / 2.0, y=(a.y + b.y) / 2.0)
            end = Point2D(x=cx + (mid.x - cx) * inner_ratio, y=cy + (mid.y - cy) * inner_ratio)
            if _distance(mid, end) >= MIN_MAIN_STRUT_SPAN_M and _line_segment_samples_inside(mid, end, points) and _line_avoids_obstacles(mid, end, obstacles):
                lines.append(SupportLayoutLine("ring_strut", Point2D(x=round(mid.x, 3), y=round(mid.y, 3)), Point2D(x=round(end.x, 3), y=round(end.y, 3)), round(_distance(mid, end), 3), None, "圆形/多边形竖井环撑体系：由边中点径向传力至内环梁代理，当前按规范算法回归算例处理。"))
        return lines, ["已启用圆形/多边形竖井环撑布置原型：生成边中点径向支撑，正式工程需结合双墙体系和竖井专项设计复核。"]

    rx0, ry0, rx1, ry1 = _ring_rectangle(points, obstacles)
    cx, cy = (rx0 + rx1) / 2.0, (ry0 + ry1) / 2.0
    candidates = [
        (Point2D(x=min_x, y=cy), Point2D(x=rx0, y=cy)),
        (Point2D(x=rx1, y=cy), Point2D(x=max_x, y=cy)),
        (Point2D(x=cx, y=min_y), Point2D(x=cx, y=ry0)),
        (Point2D(x=cx, y=ry1), Point2D(x=cx, y=max_y)),
    ]
    for start, end in candidates:
        if _distance(start, end) >= MIN_MAIN_STRUT_SPAN_M and _line_segment_samples_inside(start, end, points) and _line_avoids_obstacles(start, end, obstacles):
            lines.append(SupportLayoutLine("ring_strut", Point2D(x=round(start.x, 3), y=round(start.y, 3)), Point2D(x=round(end.x, 3), y=round(end.y, 3)), round(_distance(start, end), 3), None, "中心岛/环撑体系：外围围檩通过径向支撑传力至内环梁，适合大平面或中心岛施工方案。"))
    return lines, ["已启用中心岛/环撑布置原型：生成内环梁和径向支撑，正式工程需结合栈桥、出土口、分区开挖专项复核。"]



def _support_line_shares_endpoint(a: SupportLayoutLine, b: SupportLayoutLine, tol: float = 1e-6) -> bool:
    return any(_distance(p, q) <= tol for p in (a.start, a.end) for q in (b.start, b.end))


def _orientation2(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _support_lines_cross(a: SupportLayoutLine, b: SupportLayoutLine) -> bool:
    if _support_line_shares_endpoint(a, b):
        return False
    # Coordinates are rounded during wall-clearance and support-node generation.
    # Treat an endpoint that lies on the other member within 1 mm as a T/Y node,
    # not as a proper crossing.
    node_tol = 1.0e-3
    if any(_point_on_segment(p, b.start, b.end, tol=node_tol) for p in (a.start, a.end)):
        return False
    if any(_point_on_segment(p, a.start, a.end, tol=node_tol) for p in (b.start, b.end)):
        return False
    o1 = _orientation2(a.start, a.end, b.start)
    o2 = _orientation2(a.start, a.end, b.end)
    o3 = _orientation2(b.start, b.end, a.start)
    o4 = _orientation2(b.start, b.end, a.end)
    if abs(o1) <= EPS or abs(o2) <= EPS or abs(o3) <= EPS or abs(o4) <= EPS:
        return False
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _proper_layout_intersection(a: SupportLayoutLine, b: SupportLayoutLine) -> Point2D | None:
    """Return a proper interior intersection for two support centre lines."""
    if not _support_lines_cross(a, b):
        return None
    x1, y1, x2, y2 = a.start.x, a.start.y, a.end.x, a.end.y
    x3, y3, x4, y4 = b.start.x, b.start.y, b.end.x, b.end.y
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) <= EPS:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denominator
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denominator
    return Point2D(x=round(px, 4), y=round(py, 4))


def _line_station(line: SupportLayoutLine, point: Point2D) -> float:
    length = max(_distance(line.start, line.end), EPS)
    dx = line.end.x - line.start.x
    dy = line.end.y - line.start.y
    return max(0.0, min(length, ((point.x - line.start.x) * dx + (point.y - line.start.y) * dy) / length))


def _layout_line_segment(
    source: SupportLayoutLine,
    start: Point2D,
    end: Point2D,
    note_suffix: str,
) -> SupportLayoutLine:
    return SupportLayoutLine(
        role=source.role,
        start=Point2D(x=round(start.x, 4), y=round(start.y, 4)),
        end=Point2D(x=round(end.x, 4), y=round(end.y, 4)),
        span_length=round(_distance(start, end), 3),
        bay_spacing=source.bay_spacing,
        layout_note=(source.layout_note or "") + note_suffix,
        topology_family=source.topology_family,
    )


def _terminate_at_existing_support_nodes(
    lines: list[SupportLayoutLine],
    blockers: list[SupportLayoutLine],
    *,
    minimum_stub_length_m: float = MIN_CORNER_BRACE_LEG_M,
) -> tuple[list[SupportLayoutLine], int, int]:
    """Convert crossing braces into wall-to-node terminal stubs.

    Non-ring horizontal supports must not pass through another support.  A line
    that would cross the established main family is therefore shortened to the
    first/last structural node.  The resulting T/Y nodes are explicit endpoints
    and receive a temporary column in ``make_column_elements``.  Interior pieces
    between two main supports are omitted because they do not provide a direct
    wall reaction in the current design model.
    """
    output: list[SupportLayoutLine] = []
    converted = 0
    omitted = 0
    for line in lines:
        if line.role == "ring_strut":
            output.append(line)
            continue
        points: list[tuple[float, Point2D]] = []
        for blocker in blockers:
            if blocker.role == "ring_strut":
                continue
            point = _proper_layout_intersection(line, blocker)
            if point is None:
                continue
            points.append((_line_station(line, point), point))
        if not points:
            output.append(line)
            continue
        points.sort(key=lambda item: item[0])
        first = points[0][1]
        last = points[-1][1]
        candidates = [(line.start, first, " 起点侧短撑止于既有主支撑节点，不穿越主支撑。"), (last, line.end, " 终点侧短撑止于既有主支撑节点，不穿越主支撑。")]
        retained_here = 0
        for start, end, suffix in candidates:
            if _distance(start, end) + EPS < minimum_stub_length_m:
                omitted += 1
                continue
            output.append(_layout_line_segment(line, start, end, suffix))
            retained_here += 1
        if retained_here:
            converted += 1
        else:
            omitted += 1
    return output, converted, omitted


def _terminate_from_preferred_wall(
    line: SupportLayoutLine,
    blockers: list[SupportLayoutLine],
    preferred_face_code: str,
    *,
    minimum_stub_length_m: float = MIN_CORNER_BRACE_LEG_M,
    excavation=None,
) -> tuple[list[SupportLayoutLine], int, int]:
    """Keep a non-crossing wall-to-node tie for a targeted failing face.

    The first support line encountered from the wall is a topological barrier:
    the repair member may terminate on it, but may not pass through it to a
    more distant support.  When the perpendicular hit is too close to form a
    constructible member, the endpoint is shifted *along that same support* to
    form a T/Y node of adequate length.
    """
    if line.role == "ring_strut":
        return [line], 0, 0

    prefer_start = str(line.start_face_code or "") == str(preferred_face_code or "")
    prefer_end = str(line.end_face_code or "") == str(preferred_face_code or "")
    if not prefer_start and not prefer_end:
        prefer_start = True

    intersections: list[tuple[float, Point2D, SupportLayoutLine]] = []
    for blocker in blockers:
        if blocker.role == "ring_strut":
            continue
        point = _proper_layout_intersection(line, blocker)
        if point is not None:
            intersections.append((_line_station(line, point), point, blocker))
    if not intersections:
        return [line], 0, 0

    intersections.sort(key=lambda item: item[0])
    total_length = max(_distance(line.start, line.end), EPS)
    station, node, blocker = intersections[0] if prefer_start else intersections[-1]
    wall_point = line.start if prefer_start else line.end
    direct_length = _distance(wall_point, node)

    def _candidate_is_clear(candidate: SupportLayoutLine, target_blocker: SupportLayoutLine) -> bool:
        if excavation is not None:
            polygon = _dedup_points(list(excavation.outline.points))
            if polygon and not _line_segment_samples_inside(candidate.start, candidate.end, polygon):
                return False
        for other in blockers:
            if other is target_blocker:
                continue
            if _proper_layout_intersection(candidate, other) is not None:
                return False
        return True

    if direct_length + EPS >= minimum_stub_length_m:
        start, end = (wall_point, node) if prefer_start else (node, wall_point)
        retained = _layout_line_segment(
            line, start, end,
            " 目标墙侧短撑止于首个既有支撑节点，不穿越主支撑。",
        )
    else:
        # Move the terminal node along the first blocking support.  This creates
        # a buildable T/Y node while preserving the no-crossing topology.
        blocker_length = max(_distance(blocker.start, blocker.end), EPS)
        blocker_station, _ = _point_segment_projection(node, blocker.start, blocker.end)
        bx = (blocker.end.x - blocker.start.x) / blocker_length
        by = (blocker.end.y - blocker.start.y) / blocker_length
        required_along = math.sqrt(max(minimum_stub_length_m ** 2 - direct_length ** 2, 0.0))
        candidate_lines: list[SupportLayoutLine] = []
        for factor in (1.02, 1.20, 1.50, 2.00):
            delta = max(0.25, required_along * factor)
            for sign in (-1.0, 1.0):
                target_station = blocker_station + sign * delta
                if target_station <= 0.10 or target_station >= blocker_length - 0.10:
                    continue
                shifted = Point2D(
                    x=round(blocker.start.x + bx * target_station, 4),
                    y=round(blocker.start.y + by * target_station, 4),
                )
                start, end = (wall_point, shifted) if prefer_start else (shifted, wall_point)
                if _distance(start, end) + EPS < minimum_stub_length_m:
                    continue
                candidate = _layout_line_segment(
                    line, start, end,
                    " 目标墙侧短撑沿首个既有支撑调整至T/Y节点，不穿越任何非环形支撑。",
                )
                if _candidate_is_clear(candidate, blocker):
                    candidate_lines.append(candidate)
            if candidate_lines:
                break
        if not candidate_lines:
            return [], 0, 1
        retained = min(candidate_lines, key=lambda item: item.span_length)

    if prefer_start:
        retained.start_face_code = preferred_face_code
        retained.end_face_code = None
    else:
        retained.start_face_code = None
        retained.end_face_code = preferred_face_code
    return [retained], 1, 0


def _remove_crossing_lines(
    lines: list[SupportLayoutLine],
    *,
    preserve_wall_to_wall_corner_braces: bool = True,
) -> tuple[list[SupportLayoutLine], list[str]]:
    """Remove residual proper crossings outside an explicit ring system.

    Direct wall-to-wall corner braces take precedence inside the corner zone.
    Any main/secondary line crossing such a brace is removed or regenerated by
    the caller; the diagonal itself is never converted into a branch terminating
    on another horizontal support.
    """
    if not lines:
        return lines, []
    priority = {
        "corner_diagonal": 0 if preserve_wall_to_wall_corner_braces else 2,
        "main_strut": 1 if preserve_wall_to_wall_corner_braces else 0,
        "secondary_strut": 2 if preserve_wall_to_wall_corner_braces else 1,
        "ring_strut": 3,
    }
    kept: list[SupportLayoutLine] = []
    skipped = 0
    skipped_by_role: dict[str, int] = {}
    for line in sorted(lines, key=lambda item: (priority.get(item.role, 9), item.span_length, item.start.x, item.start.y)):
        def incompatible_crossing(other: SupportLayoutLine) -> bool:
            if line.role == "ring_strut" and other.role == "ring_strut":
                return False
            return _support_lines_cross(line, other)
        if any(incompatible_crossing(other) for other in kept):
            skipped += 1
            skipped_by_role[line.role] = skipped_by_role.get(line.role, 0) + 1
            continue
        kept.append(line)
    kept.sort(key=lambda item: (priority.get(item.role, 9), item.start.x, item.start.y, item.end.x, item.end.y))
    warnings: list[str] = []
    if skipped:
        role_text = "、".join(f"{role} {count} 条" for role, count in sorted(skipped_by_role.items()))
        warnings.append(
            f"已剔除 {skipped} 条形成平面穿越的非环形支撑候选（{role_text}）；"
            "墙—墙角撑优先保留，普通对撑/次撑不得穿越角撑。"
        )
    return kept, warnings

def _snap_corner_diagonals_to_main_nodes(lines: list[SupportLayoutLine], tolerance: float = 1.75) -> tuple[list[SupportLayoutLine], int]:
    """Snap corner-brace ends to nearby main-strut wall nodes.

    Dense 3--6 m main-strut bays often place the first strut only a few
    centimetres away from the rule-generated diagonal endpoint. Treating those
    lines as independent makes the crossing filter delete the diagonal and can
    leave the return wall without any direct restraint. Snapping creates a
    constructible shared node and preserves the corner load path.
    """
    main_endpoints = [point for line in lines if line.role == "main_strut" for point in (line.start, line.end)]
    if not main_endpoints:
        return lines, 0
    snapped = 0
    output: list[SupportLayoutLine] = []
    for line in lines:
        if line.role != "corner_diagonal":
            output.append(line)
            continue
        start = line.start
        end = line.end
        nearest_start = min(main_endpoints, key=lambda point: _distance(point, start))
        nearest_end = min(main_endpoints, key=lambda point: _distance(point, end))
        if _distance(nearest_start, start) <= tolerance:
            start = Point2D(x=nearest_start.x, y=nearest_start.y)
            snapped += 1
        if _distance(nearest_end, end) <= tolerance:
            end = Point2D(x=nearest_end.x, y=nearest_end.y)
            snapped += 1
        output.append(
            SupportLayoutLine(
                line.role,
                start,
                end,
                round(_distance(start, end), 3),
                line.bay_spacing,
                (line.layout_note or "") + (" 角撑端部已吸附至相邻主对撑节点，形成共享传力节点。" if start != line.start or end != line.end else ""),
                start_face_code=line.start_face_code,
                end_face_code=line.end_face_code,
                start_tributary_width=line.start_tributary_width,
                end_tributary_width=line.end_tributary_width,
                start_wall_connection=line.start_wall_connection,
                end_wall_connection=line.end_wall_connection,
                centerline_offset_m=line.centerline_offset_m,
                start_wall_clearance_m=line.start_wall_clearance_m,
                end_wall_clearance_m=line.end_wall_clearance_m,
                topology_family=line.topology_family,
            )
        )
    return output, snapped

def generate_support_layout_lines(excavation, config: SupportLayoutConfig | None = None) -> tuple[list[SupportLayoutLine], list[str]]:
    config = (config or SupportLayoutConfig()).normalized()
    points = _dedup_points(list(excavation.outline.points))
    if len(points) < 3:
        return [], ["基坑轮廓点数不足，无法生成水平支撑。"]
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    shape = plan_shape_diagnostics(points)
    if _should_use_ring(points, obstacles):
        lines, warnings = _ring_layout(points, obstacles)
    else:
        main_lines, warnings = _main_strut_layout(points, obstacles, config.target_main_support_spacing_m, config=config)
        balanced_near_square = (
            config.topology_strategy == "balanced_grid"
            and float(shape.get("aspectRatio") or 999.0) <= 1.35
            and float(shape.get("shortSpanM") or 0.0) >= 24.0
        )
        force_secondary = config.topology_strategy == "bidirectional_grid" or balanced_near_square
        secondary_lines, secondary_warnings = _secondary_grid_layout(points, obstacles, excavation, force=force_secondary)
        if balanced_near_square and secondary_lines:
            secondary_warnings.append(
                "近方形大平面已采用非交叉双向T/Y网格：正交支撑在首个主支撑节点终止，不允许平面穿越。"
            )
        if config.topology_strategy == "direct_grid":
            secondary_lines = []
            secondary_warnings = []
        # Orthogonal wall-to-wall lines are converted into terminal short struts
        # that stop at a main-strut node.  This preserves return-wall restraint
        # without allowing a support to pass through another support in plan.
        secondary_lines, converted_secondary, omitted_secondary = _terminate_at_existing_support_nodes(
            secondary_lines, main_lines
        )
        if converted_secondary:
            secondary_warnings.append(
                f"已将 {converted_secondary} 条正交次对撑转换为墙端至主对撑节点的非交叉短撑。"
            )
        if omitted_secondary:
            secondary_warnings.append(
                f"有 {omitted_secondary} 个交叉后残余短段小于可施工长度，已不纳入结构方案。"
            )
        warnings.extend(secondary_warnings)
        return_wall_lines, return_wall_warnings = _concave_return_wall_layout(
            points,
            obstacles,
            excavation,
            [*main_lines, *secondary_lines],
            config.target_main_support_spacing_m,
        )
        return_wall_lines, converted_returns, omitted_returns = _terminate_at_existing_support_nodes(
            return_wall_lines, [*main_lines, *secondary_lines]
        )
        if converted_returns:
            return_wall_warnings.append(
                f"已将 {converted_returns} 条回墙法向支撑转换为止于既有支撑节点的非交叉短撑。"
            )
        if omitted_returns:
            return_wall_warnings.append(f"已删除 {omitted_returns} 个过短回墙支撑残段。")
        warnings.extend(return_wall_warnings)
        diagonal_lines = _corner_diagonal_layout(points, obstacles, config)
        if diagonal_lines:
            warnings.append(
                "已在凸角局部影响区生成墙—墙角撑；角撑两端直接支承于相邻围檩/围护墙，"
                "不允许截断至另一水平支撑。"
            )
        lines = main_lines + secondary_lines + return_wall_lines + diagonal_lines
        lines, hybrid_warnings = _hybridize_long_struts(lines, points, config)
        warnings.extend(hybrid_warnings)
    lines, crossing_warnings = _remove_crossing_lines(lines)
    warnings.extend(crossing_warnings)
    _attach_faces(lines, excavation)
    warnings.extend(_orthogonalize_wall_to_node_secondary(lines, excavation))
    warnings.extend(_apply_support_wall_clearance(lines, excavation, config))
    lines, trim_warnings = _filter_unconstructible_trimmed_lines(lines, excavation, config)
    warnings.extend(trim_warnings)
    warnings.extend(_finalize_retained_support_endpoints(lines, excavation, config))
    lines, dangling_warnings = _prune_dangling_non_ring_supports(lines)
    warnings.extend(dangling_warnings)
    # Removing a dangling member can change whether a nearby endpoint is a T/Y
    # node or a wall endpoint.  Rebuild endpoint semantics once more on the
    # final retained topology.
    warnings.extend(_finalize_retained_support_endpoints(lines, excavation, config))
    lines, final_crossing_warnings = _remove_crossing_lines(lines)
    warnings.extend(final_crossing_warnings)
    lines, final_dangling_warnings = _prune_dangling_non_ring_supports(lines)
    warnings.extend(final_dangling_warnings)
    warnings.append(
        "平面类型识别=" + str(shape.get("classification"))
        + f"；凹角 {shape.get('concaveVertexCount')} 个；局部长/短跨 {shape.get('longSpanM')}/{shape.get('shortSpanM')}m。"
    )
    return lines, warnings


def _orthogonalize_wall_to_node_secondary(
    lines: list[SupportLayoutLine],
    excavation,
    *,
    maximum_projection_shift_m: float = 12.0,
    reference_lines: list[SupportLayoutLine] | None = None,
) -> list[str]:
    """Canonicalize ordinary wall-to-node ties as wall-normal T/Y members.

    A corner diagonal is a separate wall-to-wall family.  An ordinary
    secondary member that terminates at another support must preserve its
    intended wale station and meet the nearest main strut along the inward
    normal of that wall.  Moving the wall station to match an oblique internal
    node can reopen the very wale bay that the member was added to repair, so
    the wall endpoint is never translated here.
    """
    del maximum_projection_shift_m  # retained for API compatibility
    segments = {str(item.name): item for item in getattr(excavation, "segments", []) or []}
    polygon = _dedup_points(list(excavation.outline.points))
    adjusted = 0
    unresolved = 0
    topology_lines = [*lines, *(reference_lines or [])]

    def _inward_unit(segment, wall_point: Point2D) -> tuple[float, float] | None:
        tx, ty = _unit_vector(segment.start, segment.end)
        normals = [(-ty, tx), (ty, -tx)]
        for nx, ny in normals:
            probe = Point2D(x=wall_point.x + nx * 0.20, y=wall_point.y + ny * 0.20)
            if _point_in_polygon(probe, polygon):
                return nx, ny
        return None

    for line in lines:
        if line.role != "secondary_strut":
            continue
        start_face = str(line.start_face_code or "")
        end_face = str(line.end_face_code or "")
        if bool(start_face) == bool(end_face):
            continue
        wall_is_start = bool(start_face)
        face_code = start_face if wall_is_start else end_face
        segment = segments.get(face_code)
        if segment is None:
            unresolved += 1
            continue
        wall_point = (line.start_wall_connection or line.start) if wall_is_start else (line.end_wall_connection or line.end)
        normal = _inward_unit(segment, wall_point)
        if normal is None:
            unresolved += 1
            continue
        nx, ny = normal
        origin = Point2D(x=wall_point.x + nx * 0.05, y=wall_point.y + ny * 0.05)
        hits: list[tuple[float, Point2D, SupportLayoutLine]] = []
        for blocker in topology_lines:
            if blocker is line:
                continue
            is_direct_bearing_line = blocker.role == "main_strut" or bool(blocker.start_face_code and blocker.end_face_code)
            if not is_direct_bearing_line:
                continue
            hit = _ray_segment_intersection(origin, (nx, ny), blocker.start, blocker.end)
            if hit is None or hit[0] <= 0.20:
                continue
            span = _distance(wall_point, hit[1])
            if span < MIN_CORNER_BRACE_LEG_M:
                continue
            hits.append((hit[0], hit[1], blocker))
        if not hits:
            unresolved += 1
            line.layout_note = (line.layout_note or "") + " 未找到墙面法向上的主对撑节点，禁止按角撑表达。"
            continue
        _, node, intended_blocker = min(hits, key=lambda item: item[0])
        candidate_start, candidate_end = (wall_point, node) if wall_is_start else (node, wall_point)
        if polygon and not _line_segment_samples_inside(candidate_start, candidate_end, polygon):
            unresolved += 1
            continue
        blocked = False
        for other in topology_lines:
            if other is line or other is intended_blocker:
                continue
            point = _proper_layout_intersection(
                SupportLayoutLine("secondary_strut", candidate_start, candidate_end, _distance(candidate_start, candidate_end), None, "candidate"),
                other,
            )
            if point is not None and _distance(point, node) > 0.15:
                blocked = True
                break
        if blocked:
            unresolved += 1
            continue
        line.start = Point2D(x=round(candidate_start.x, 4), y=round(candidate_start.y, 4))
        line.end = Point2D(x=round(candidate_end.x, 4), y=round(candidate_end.y, 4))
        line.span_length = round(_distance(line.start, line.end), 3)
        if wall_is_start:
            line.start_wall_connection = Point2D(x=round(wall_point.x, 4), y=round(wall_point.y, 4))
            line.end_wall_connection = None
            line.end_face_code = None
        else:
            line.end_wall_connection = Point2D(x=round(wall_point.x, 4), y=round(wall_point.y, 4))
            line.start_wall_connection = None
            line.start_face_code = None
        line.layout_note = (line.layout_note or "") + " 已保持原围檩站位并正交连接至最近主对撑 T/Y 节点。"
        line.topology_family = "bidirectional_grid"
        adjusted += 1
    messages: list[str] = []
    if adjusted:
        messages.append(f"已将 {adjusted} 条墙—节点次撑规范化为保留围檩站位的墙面法向 T/Y 短撑。")
    if unresolved:
        messages.append(f"有 {unresolved} 条墙—节点次撑未找到可施工的法向主节点，已保留为拓扑复核项且不得按角撑表达。")
    return messages


def _support_reinforcement(level_idx: int, section_type: str = "rc_rectangular") -> list[ReinforcementGroup]:
    """Rule-based preliminary reinforcement for cast-in-place RC struts.

    Steel struts intentionally return no reinforcement; they are tagged by
    section/material and checked by steel-member rules in the calculation layer.
    """
    if section_type != "rc_rectangular":
        return []
    # First-level cast-in-place concrete supports receive a fuller detailing
    # proxy because they are normally visible in the construction drawing set.
    # Lower-level concrete supports keep the same family, with closer stirrups
    # and slightly denser distribution/tie bars for construction-stage control.
    stirrup_spacing = 180 if level_idx <= 1 else 150
    distribution_spacing = 200 if level_idx <= 1 else 180
    tie_spacing = 450 if level_idx <= 1 else 400
    return [
        ReinforcementGroup(
            name="支撑纵向主筋",
            bar_type="longitudinal",
            diameter=25 if level_idx <= 1 else 28,
            count=12 if level_idx <= 1 else 14,
            grade="HRB400",
            location_description="cast-in-place concrete strut perimeter longitudinal bars with staggered lap and anchorage zones",
            check_status="manual_review",
        ),
        ReinforcementGroup(
            name="支撑封闭箍筋",
            bar_type="stirrup",
            diameter=12,
            spacing=stirrup_spacing,
            grade="HRB400",
            location_description="closed stirrups along concrete strut; node-end densification is shown in rebar viewer",
            check_status="manual_review",
        ),
        ReinforcementGroup(
            name="支撑分布筋",
            bar_type="distribution",
            diameter=16 if level_idx <= 1 else 18,
            spacing=distribution_spacing,
            grade="HRB400",
            location_description="top/bottom and side-face distribution bars for concrete support crack control",
            check_status="manual_review",
        ),
        ReinforcementGroup(
            name="支撑拉结/架立筋",
            bar_type="tie",
            diameter=12,
            spacing=tie_spacing,
            grade="HRB400",
            location_description="tie and erection bars connecting longitudinal cages and maintaining support cage geometry",
            check_status="manual_review",
        ),
        ReinforcementGroup(
            name="搭接加强筋",
            bar_type="additional",
            diameter=20 if level_idx <= 1 else 22,
            count=4,
            grade="HRB400",
            location_description="additional bars around staggered lap and support anchorage zones; exact splice length requires review",
            check_status="manual_review",
        ),
    ]


def make_support_elements(excavation, elevations: list[float], config: SupportLayoutConfig | None = None) -> tuple[list[SupportElement], list[str]]:
    layout_lines, warnings = generate_support_layout_lines(excavation, config=config)
    supports: list[SupportElement] = []
    for level_idx, elevation in enumerate(elevations, start=1):
        role_counts = {"main_strut": 0, "secondary_strut": 0, "corner_diagonal": 0, "ring_strut": 0}
        for line in layout_lines:
            role_counts[line.role] = role_counts.get(line.role, 0) + 1
            prefix = {"main_strut": "SP", "secondary_strut": "GS", "corner_diagonal": "DB", "ring_strut": "RS"}.get(line.role, "SP")
            support = SupportElement(
                code=f"{prefix}-L{level_idx}-{role_counts[line.role]}",
                level_index=level_idx,
                elevation=elevation,
                start=line.start,
                end=line.end,
                support_role=line.role,  # type: ignore[arg-type]
                layout_note=line.layout_note,
                span_length=line.span_length,
                bay_spacing=line.bay_spacing,
                start_face_code=line.start_face_code,
                end_face_code=line.end_face_code,
                start_tributary_width=line.start_tributary_width,
                end_tributary_width=line.end_tributary_width,
                start_wall_connection=line.start_wall_connection,
                end_wall_connection=line.end_wall_connection,
                centerline_offset_m=line.centerline_offset_m,
                start_wall_clearance_m=line.start_wall_clearance_m,
                end_wall_clearance_m=line.end_wall_clearance_m,
                topology_family=line.topology_family if line.topology_family in {"direct_grid", "hybrid_diagonal", "bidirectional_grid", "ring_radial"} else "direct_grid",
                design_zone=line.design_zone,
                station_chainage_m=line.station_chainage_m,
                local_clear_span_m=line.local_clear_span_m or line.span_length,
                placement_reason=line.placement_reason,
                load_path_class=line.load_path_class if line.load_path_class in {"wall_to_wall", "wall_to_ring", "supported_frame_node", "manual"} else "wall_to_wall",
                force_distribution_note="V3.20 支撑轴力由围檩连续梁-弹性支座节点反力计算；场区/过渡区站位和局部净跨写入构件台账。",
                section_type="rc_rectangular",
                section=SectionDefinition(
                    width=(1.2 if line.span_length <= 18.0 else 1.4 if line.span_length <= 24.0 else 1.6 if line.span_length <= 32.0 else 1.8),
                    height=(1.2 if line.span_length <= 18.0 else 1.4 if line.span_length <= 24.0 else 1.6 if line.span_length <= 32.0 else 1.8),
                    name=("1200x1200 RC" if line.span_length <= 18.0 else "1400x1400 RC" if line.span_length <= 24.0 else "1600x1600 RC" if line.span_length <= 32.0 else "1800x1800 RC"),
                ),
                material=MaterialDefinition(name="Concrete", grade="C40"),
                reinforcement=_support_reinforcement(level_idx, "rc_rectangular"),
            )
            supports.append(support)
    _assign_tributary_widths(supports, excavation)
    return supports, warnings



def wale_support_bay_audit(
    excavation,
    supports: list[SupportElement],
    *,
    target_bay_m: float = 7.5,
    hard_max_bay_m: float = 9.0,
) -> dict[str, object]:
    """Audit direct wale support stations for every wall face and support level.

    The check uses wall-connection points rather than trimmed member centrelines.
    Short step faces below the target-bay length are handled by the corner-transfer
    wall analysis and are therefore excluded from this direct wale-bay audit.
    """
    target = max(4.0, float(target_bay_m))
    hard = max(target, float(hard_max_bay_m))
    levels = sorted({int(item.level_index) for item in supports})
    rows: list[dict[str, object]] = []
    if not levels:
        return {"status": "fail", "targetBayM": target, "hardMaxBayM": hard, "rows": [], "failCount": 1, "warningCount": 0}
    for segment in getattr(excavation, "segments", []) or []:
        length = float(getattr(segment, "length", 0.0) or _distance(segment.start, segment.end))
        if length < max(8.0, target * 1.05):
            continue
        face_code = str(segment.name)
        for level in levels:
            stations: list[float] = []
            support_codes: list[str] = []
            for item in supports:
                if int(item.level_index) != level:
                    continue
                for endpoint, point, wall_point, stored_face in (
                    ("start", item.start, item.start_wall_connection, item.start_face_code),
                    ("end", item.end, item.end_wall_connection, item.end_face_code),
                ):
                    if str(stored_face or "") != face_code:
                        continue
                    source = wall_point or point
                    chainage, distance_to_face = _point_segment_projection(source, segment.start, segment.end)
                    if distance_to_face <= max(1.5, float(getattr(item, f"{endpoint}_wall_clearance_m", 0.0) or 0.0) + 0.75):
                        stations.append(max(0.0, min(length, float(chainage))))
                        support_codes.append(str(item.code))
            stations = sorted({round(value, 4) for value in stations})
            boundaries = [0.0, *stations, length]
            bays = [round(b - a, 4) for a, b in zip(boundaries[:-1], boundaries[1:])]
            max_bay = max(bays, default=length)
            status = "fail" if max_bay > hard + 1e-6 else "warning" if max_bay > target + 1e-6 else "pass"
            rows.append({
                "faceCode": face_code,
                "levelIndex": level,
                "faceLengthM": round(length, 3),
                "supportNodeCount": len(stations),
                "supportCodes": sorted(set(support_codes)),
                "stationsM": stations,
                "bayLengthsM": bays,
                "maxBayM": round(max_bay, 3),
                "targetBayM": round(target, 3),
                "hardMaxBayM": round(hard, 3),
                "status": status,
            })
    fail_count = sum(row["status"] == "fail" for row in rows)
    warning_count = sum(row["status"] == "warning" for row in rows)
    return {
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "targetBayM": round(target, 3),
        "hardMaxBayM": round(hard, 3),
        "rows": rows,
        "failCount": fail_count,
        "warningCount": warning_count,
        "maxBayM": max((float(row["maxBayM"]) for row in rows), default=0.0),
    }


def _targeted_wale_bay_repair_lines(
    excavation,
    audit: dict[str, object],
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    config: SupportLayoutConfig,
) -> list[SupportLayoutLine]:
    """Create a local brace at the centre of every still-oversized wale bay.

    Corner fans are effective near convex corners, but an L/T-shaped pit can
    have a long intermediate gap on a return face.  For each failing bay this
    helper casts an inward angular fan and selects the shortest valid connection
    to another wall face.  Existing members remain untouched.
    """
    segments = {str(item.name): item for item in getattr(excavation, "segments", []) or []}
    points = _dedup_points(list(excavation.outline.points))
    rows = [row for row in audit.get("rows", []) if row.get("status") in {"warning", "fail"}]
    targets: list[tuple[str, float]] = []
    for row in rows:
        face_code = str(row.get("faceCode") or "")
        segment = segments.get(face_code)
        if not segment:
            continue
        stations = [0.0, *[float(value) for value in row.get("stationsM", [])], float(row.get("faceLengthM") or segment.length)]
        for left, right in zip(stations[:-1], stations[1:]):
            bay = right - left
            if bay <= config.max_wale_support_bay_m + 1e-6:
                continue
            insertion_count = max(1, int(math.ceil(bay / config.max_wale_support_bay_m)) - 1)
            for index in range(1, insertion_count + 1):
                targets.append((face_code, left + bay * index / (insertion_count + 1)))

    # The audit contains one row per support level, but the plan topology is
    # shared by all levels.  Deduplicate face/chainage targets before creating
    # geometry; otherwise three identical repair lines compete in the crossing
    # resolver and can suppress a valid wall support station.
    unique_targets: list[tuple[str, float]] = []
    seen_targets: set[tuple[str, float]] = set()
    for face_code, chainage in targets:
        key = (str(face_code), round(float(chainage), 3))
        if key in seen_targets:
            continue
        seen_targets.add(key)
        unique_targets.append((str(face_code), float(chainage)))

    lines: list[SupportLayoutLine] = []
    for face_code, chainage in unique_targets:
        segment = segments.get(face_code)
        if not segment:
            continue
        length = max(float(segment.length), EPS)
        start_wall = _point_at(segment.start, segment.end, max(0.05, min(length - 0.05, chainage)))
        outward = getattr(segment, "outward_normal", None)
        if outward is not None:
            inward_x, inward_y = -float(outward.x), -float(outward.y)
        else:
            tx, ty = _unit_vector(segment.start, segment.end)
            orientation = 1.0 if _signed_area(points) > 0 else -1.0
            inward_x, inward_y = -orientation * ty, orientation * tx
        norm = max(math.hypot(inward_x, inward_y), EPS)
        inward_x, inward_y = inward_x / norm, inward_y / norm
        origin = Point2D(x=start_wall.x + inward_x * 0.08, y=start_wall.y + inward_y * 0.08)
        candidates: list[tuple[float, float, Point2D]] = []
        for angle_deg in (0, 15, -15, 30, -30, 45, -45, 60, -60, 75, -75):
            angle = math.radians(angle_deg)
            dx = inward_x * math.cos(angle) - inward_y * math.sin(angle)
            dy = inward_x * math.sin(angle) + inward_y * math.cos(angle)
            hits: list[tuple[float, Point2D, int]] = []
            for edge_index, (edge_a, edge_b) in enumerate(zip(points, points[1:] + points[:1])):
                other_face = str(getattr(excavation.segments[edge_index], "name", "")) if edge_index < len(excavation.segments) else ""
                if other_face == face_code:
                    continue
                hit = _ray_segment_intersection(origin, (dx, dy), edge_a, edge_b)
                if hit and hit[0] > 0.25:
                    hits.append((hit[0], hit[1], edge_index))
            if not hits:
                continue
            ray_t, end_wall, _ = min(hits, key=lambda item: item[0])
            span = _distance(start_wall, end_wall)
            if span < MIN_MAIN_STRUT_SPAN_M or span > min(45.0, config.max_direct_strut_span_m * 1.35):
                continue
            if not _line_segment_samples_inside(origin, Point2D(x=end_wall.x - dx * 0.08, y=end_wall.y - dy * 0.08), points):
                continue
            if not _line_avoids_obstacles(origin, Point2D(x=end_wall.x - dx * 0.08, y=end_wall.y - dy * 0.08), obstacles):
                continue
            candidates.append((span, abs(angle_deg), end_wall))
        if not candidates:
            continue
        # Prefer a member close to the wall normal.  Selecting the geometrically
        # shortest ray favoured near-tangent 60--75 degree ties at corners; after
        # applying the required wall clearance those ties collapsed into 1--2 m
        # pseudo-members.  Normality is the primary structural criterion, with
        # span used only as the secondary tie-breaker.
        span, angle_abs, end_wall = min(candidates, key=lambda item: (item[1], item[0]))
        # This member is generated from a wale-bay station, rather than from a
        # convex-corner brace rule.  Keep it as a secondary strut even when the
        # shortest valid ray is oblique to the local wall normal.  This
        # distinction matters for drawing symbols, return-wall diagnostics and
        # construction-node classification.
        role = "secondary_strut"
        lines.append(SupportLayoutLine(
            role=role,
            start=start_wall,
            end=end_wall,
            span_length=span,
            bay_spacing=None,
            layout_note=f"围檩超限跨中增补：墙面 {face_code} 里程 {chainage:.2f}m，角度偏转 {angle_abs:.0f}°。",
            topology_family="hybrid_diagonal",
        ))
    _attach_faces(lines, excavation)
    return lines


def _direct_adjacent_wall_parallel_repair(
    line: SupportLayoutLine,
    *,
    preferred_face_code: str,
    excavation,
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    blockers: list[SupportLayoutLine],
    config: SupportLayoutConfig,
) -> SupportLayoutLine | None:
    """Return one independent member of a parallel corner-brace family.

    The target wale bay only selects the nearest corner and preferred family
    station.  Both brace endpoints are then moved to equal chainage from that
    corner.  This avoids the historical fan/V repair in which several diagonals
    converged on one point of the failing wall.
    """
    segments = list(getattr(excavation, "segments", []) or [])
    if not segments:
        return None
    index_by_code = {str(segment.name): index for index, segment in enumerate(segments)}
    face_index = index_by_code.get(str(preferred_face_code or ""))
    if face_index is None:
        return None
    segment = segments[face_index]
    if str(line.start_face_code or "") == str(preferred_face_code):
        target_wall_point = line.start_wall_connection or line.start
    elif str(line.end_face_code or "") == str(preferred_face_code):
        target_wall_point = line.end_wall_connection or line.end
    else:
        target_wall_point = line.start_wall_connection or line.start

    station, distance_to_face = _point_segment_projection(target_wall_point, segment.start, segment.end)
    if distance_to_face > 1.5:
        return None
    segment_length = max(float(segment.length), EPS)
    station = max(0.0, min(segment_length, float(station)))
    corner_options = [
        (station, segment.start, segment.end, segments[(face_index - 1) % len(segments)]),
        (segment_length - station, segment.end, segment.start, segments[(face_index + 1) % len(segments)]),
    ]
    points = _dedup_points(list(excavation.outline.points))
    candidates: list[tuple[float, float, float, SupportLayoutLine]] = []
    minimum_node_spacing = float(config.corner_diagonal_family_spacing_m) * 0.85

    def endpoint_for_face(item: SupportLayoutLine, face_code: str) -> Point2D | None:
        if str(item.start_face_code or "") == face_code:
            return item.start_wall_connection or item.start
        if str(item.end_face_code or "") == face_code:
            return item.end_wall_connection or item.end
        return None

    for target_corner_distance, corner, preferred_other, adjacent in sorted(corner_options, key=lambda item: item[0]):
        if _distance(adjacent.start, corner) <= 0.05:
            adjacent_other = adjacent.end
        elif _distance(adjacent.end, corner) <= 0.05:
            adjacent_other = adjacent.start
        else:
            continue
        preferred_leg = _distance(corner, preferred_other)
        adjacent_leg = _distance(corner, adjacent_other)
        min_leg = min(preferred_leg, adjacent_leg)
        if min_leg < MIN_CORNER_BRACE_LEG_M * 1.5:
            continue
        family_offsets = _corner_family_offsets(min_leg, config, float(config.max_wale_support_bay_m))
        desired = max(float(config.corner_diagonal_min_offset_m), min(float(target_corner_distance), max(family_offsets or [float(config.corner_diagonal_max_offset_m)])))
        offset_trials = sorted(
            set(family_offsets + [
                round(desired, 3),
                round(desired - float(config.corner_diagonal_family_spacing_m), 3),
                round(desired + float(config.corner_diagonal_family_spacing_m), 3),
            ]),
            key=lambda value: abs(value - desired),
        )
        for offset in offset_trials:
            if offset < float(config.corner_diagonal_min_offset_m) - EPS or offset >= min_leg - 0.10:
                continue
            preferred_point = _point_at(corner, preferred_other, offset)
            adjacent_point = _point_at(corner, adjacent_other, offset)
            span = _distance(preferred_point, adjacent_point)
            if span < MIN_MAIN_STRUT_SPAN_M or span > min(45.0, config.max_direct_strut_span_m * 1.50):
                continue
            if not _line_segment_samples_inside(preferred_point, adjacent_point, points):
                continue
            if not _line_avoids_obstacles(preferred_point, adjacent_point, obstacles):
                continue
            face_a = str(preferred_face_code)
            face_b = str(adjacent.name)
            crowded = False
            for blocker in blockers:
                if blocker.role != "corner_diagonal":
                    continue
                for face_code, candidate_point in ((face_a, preferred_point), (face_b, adjacent_point)):
                    existing_point = endpoint_for_face(blocker, face_code)
                    if existing_point is not None and _distance(existing_point, candidate_point) < minimum_node_spacing:
                        crowded = True
                        break
                if crowded:
                    break
            if crowded:
                continue
            candidate = SupportLayoutLine(
                role="corner_diagonal",
                start=Point2D(x=round(preferred_point.x, 4), y=round(preferred_point.y, 4)),
                end=Point2D(x=round(adjacent_point.x, 4), y=round(adjacent_point.y, 4)),
                span_length=round(span, 3),
                bay_spacing=round(float(config.corner_diagonal_family_spacing_m), 3),
                layout_note=(
                    f"围檩超限跨平行角撑修复：墙面 {preferred_face_code} 与相邻墙 {adjacent.name} "
                    f"在距共同转角约 {offset:.2f}m 处分别设置独立支承节点；"
                    "与同组角撑保持平行且不得共用墙上节点，不得截断至另一水平支撑。"
                ),
                start_face_code=face_a,
                end_face_code=face_b,
                start_wall_connection=Point2D(x=round(preferred_point.x, 4), y=round(preferred_point.y, 4)),
                end_wall_connection=Point2D(x=round(adjacent_point.x, 4), y=round(adjacent_point.y, 4)),
                topology_family="hybrid_diagonal",
                placement_reason="parallel_corner_brace_repair",
            )
            if any(_proper_layout_intersection(candidate, blocker) is not None for blocker in blockers if blocker.role != "ring_strut"):
                continue
            candidates.append((abs(offset - desired), float(target_corner_distance), span, candidate))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]

def repair_wale_support_bays(project, config: SupportLayoutConfig | None = None, *, _iteration: int = 0) -> dict[str, object]:
    """Add missing parallel corner-brace families before calculation when wale bays are excessive.

    Existing supports are preserved.  The repair adds only independent parallel diagonal members
    touching faces that fail the direct wale-bay audit, then rebuilds tributary
    widths, temporary columns and support-wale nodes.
    """
    excavation = getattr(project, "excavation", None)
    system = getattr(project, "retaining_system", None)
    if not excavation or not system or not system.supports:
        return {"changed": False, "addedSupportCount": 0, "status": "manual_review"}
    if config is None:
        settings = getattr(project, "design_settings", None)
        config = SupportLayoutConfig(
            target_main_support_spacing_m=float(getattr(settings, "default_support_spacing", TARGET_MAIN_SUPPORT_SPACING_M)),
            column_max_unbraced_span_m=COLUMN_MAX_UNBRACED_SPAN_M,
            support_wall_clearance_m=float(getattr(settings, "support_wall_clearance_m", 1.0)),
            max_direct_strut_span_m=float(getattr(settings, "max_direct_strut_span_m", 24.0)),
            max_wale_support_bay_m=float(getattr(settings, "max_wale_support_bay_m", 7.5)),
            hard_max_wale_support_bay_m=float(getattr(settings, "hard_max_wale_support_bay_m", 9.0)),
            diagonal_brace_min_wall_length_m=float(getattr(settings, "diagonal_brace_min_wall_length_m", 18.0)),
            corner_diagonal_min_offset_m=float(getattr(settings, "corner_diagonal_min_offset_m", 3.5)),
            corner_diagonal_max_offset_m=float(getattr(settings, "corner_diagonal_max_offset_m", 8.0)),
            corner_diagonal_max_wall_fraction=float(getattr(settings, "corner_diagonal_max_wall_fraction", 0.40)),
            corner_diagonal_family_count=int(getattr(settings, "corner_diagonal_family_count", 2)),
            corner_diagonal_family_spacing_m=float(getattr(settings, "corner_diagonal_family_spacing_m", 3.0)),
            corner_diagonal_parallel_tolerance_deg=float(getattr(settings, "corner_diagonal_parallel_tolerance_deg", 5.0)),
            prefer_diagonal_braces=bool(getattr(settings, "prefer_diagonal_braces", True)),
            allow_wale_repair_t_y_nodes=bool(getattr(settings, "allow_wale_repair_t_y_nodes", False)),
            topology_strategy="hybrid_diagonal",
        )
    config = config.normalized()
    removed_legacy_t_y = 0
    if not config.allow_wale_repair_t_y_nodes:
        retained_supports = []
        for support in system.supports:
            note = str(getattr(support, "layout_note", "") or "")
            one_wall_endpoint = bool(support.start_face_code) ^ bool(support.end_face_code)
            legacy_repair = (
                support.support_role == "secondary_strut"
                and one_wall_endpoint
                and ("围檩超限跨" in note or "墙面法向短撑" in note)
                and ("T/Y" in note or "止于主对撑" in note)
            )
            if legacy_repair:
                removed_legacy_t_y += 1
                continue
            retained_supports.append(support)
        if removed_legacy_t_y:
            system.supports = retained_supports
            _assign_tributary_widths(system.supports, excavation)
            system.columns = make_column_elements(
                excavation, system.supports, max_unbraced_span_m=config.column_max_unbraced_span_m
            )
            system.support_nodes = make_support_wale_nodes(system.supports, system.wale_beams)
    before = wale_support_bay_audit(
        excavation,
        list(system.supports),
        target_bay_m=config.max_wale_support_bay_m,
        hard_max_bay_m=config.hard_max_wale_support_bay_m,
    )
    failing_faces = {
        str(row["faceCode"])
        for row in before.get("rows", [])
        if row.get("status") in {"warning", "fail"}
    }
    if not failing_faces:
        return {
            "changed": bool(removed_legacy_t_y),
            "addedSupportCount": 0,
            "removedLegacyTYSupportCount": removed_legacy_t_y,
            "status": before.get("status"),
            "auditBefore": before,
            "auditAfter": before,
        }

    points = _dedup_points(list(excavation.outline.points))
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    targeted_lines = _targeted_wale_bay_repair_lines(excavation, before, obstacles, config)
    _attach_faces(targeted_lines, excavation)
    covered_faces = {
        face
        for line in targeted_lines
        for face in (str(line.start_face_code or ""), str(line.end_face_code or ""))
        if face in failing_faces
    }
    # Corner fans are a fallback only for a failing face that cannot obtain a
    # valid local tie.  Adding the full fan before the targeted repair created
    # duplicate braces and very short pseudo-members at return-wall corners.
    fallback_lines: list[SupportLayoutLine] = []
    uncovered_faces = failing_faces - covered_faces
    if uncovered_faces:
        fallback_lines = _corner_diagonal_layout(points, obstacles, config)
        _attach_faces(fallback_lines, excavation)
        fallback_lines = [
            line
            for line in fallback_lines
            if uncovered_faces.intersection({str(line.start_face_code or ""), str(line.end_face_code or "")})
        ]

    # Repair members must follow the same no-crossing rule as the initial
    # layout.  A targeted line is retained from the wall that failed the audit
    # to the first existing support node.  Generic corner fans may retain both
    # exterior stubs.  This prevents a repair from keeping only the opposite
    # wall piece and leaving the original wale bay unchanged.
    blockers: list[SupportLayoutLine] = []
    blocker_keys: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for item in system.supports:
        if item.support_role == "ring_strut":
            continue
        endpoints = sorted((
            (round(float(item.start.x), 4), round(float(item.start.y), 4)),
            (round(float(item.end.x), 4), round(float(item.end.y), 4)),
        ))
        key = (endpoints[0], endpoints[1])
        if key in blocker_keys:
            continue
        blocker_keys.add(key)
        blockers.append(SupportLayoutLine(
            role=item.support_role,
            start=item.start,
            end=item.end,
            span_length=float(item.span_length or _distance(item.start, item.end)),
            bay_spacing=item.bay_spacing,
            layout_note=item.layout_note or "existing support",
            start_face_code=item.start_face_code,
            end_face_code=item.end_face_code,
            start_wall_connection=item.start_wall_connection,
            end_wall_connection=item.end_wall_connection,
            centerline_offset_m=item.centerline_offset_m,
            start_wall_clearance_m=item.start_wall_clearance_m,
            end_wall_clearance_m=item.end_wall_clearance_m,
            topology_family=item.topology_family,
        ))
    lines: list[SupportLayoutLine] = []
    # Concave plans can require an explicit bidirectional grid/return-wall node
    # where a direct wall-to-wall brace cannot remain inside the polygon.  This
    # fallback is limited to non-convex geometry; convex end bays keep the
    # direct wall-to-wall contract by default.
    shape_info = plan_shape_diagnostics(points)
    aspect_ratio = float(shape_info.get("aspectRatio") or 999.0)
    concave_grid_fallback = int(shape_info.get("concaveVertexCount") or 0) > 0
    near_square_grid_fallback = aspect_ratio <= 1.35
    converted_repair_lines = 0
    omitted_repair_segments = 0
    for line in targeted_lines:
        preferred_face = str(line.start_face_code or line.end_face_code or "")
        retained: list[SupportLayoutLine] = []
        converted = 0
        omitted = 0

        # A targeted repair that already connects two wall/waIe faces is the
        # clearest load path. Keep it when it remains inside the pit and does
        # not cross existing members. Long direct struts are permitted here;
        # their effective unbraced length is subsequently controlled by
        # temporary columns rather than by replacing the member with a T/Y tie.
        original_direct = (
            bool(line.start_face_code) and bool(line.end_face_code)
            and _line_segment_samples_inside(line.start, line.end, points)
            and _line_avoids_obstacles(line.start, line.end, obstacles)
            and not any(_proper_layout_intersection(line, blocker) is not None for blocker in blockers if blocker.role != "ring_strut")
        )
        if original_direct:
            retained = [line]

        # If the direct cross-pit line conflicts with the local topology, create
        # an independent member of a parallel wall-to-wall corner family.
        # This avoids applying a transverse point load to a main strut that is
        # otherwise designed as an axial compression member.
        if not retained:
            parallel_brace = _direct_adjacent_wall_parallel_repair(
                line,
                preferred_face_code=preferred_face,
                excavation=excavation,
                obstacles=obstacles,
                blockers=blockers,
                config=config,
            )
            if parallel_brace is not None:
                retained = [parallel_brace]

        # Support-to-support T/Y nodes are an explicit frame/grid option only.
        # They are never introduced silently by the generic wale-bay repair.
        if not retained and (config.allow_wale_repair_t_y_nodes or concave_grid_fallback or near_square_grid_fallback):
            retained, converted, omitted = _terminate_from_preferred_wall(
                line, blockers, preferred_face,
                minimum_stub_length_m=MIN_CORNER_BRACE_LEG_M + 0.7 * config.support_wall_clearance_m,
                excavation=excavation,
            )
        if not retained:
            omitted += 1

        # Resolve repairs sequentially.  Every accepted wall-to-wall brace is a
        # blocker for subsequent candidates, preserving the non-crossing rule.
        for retained_line in retained:
            lines.append(retained_line)
            blockers.append(retained_line)
        converted_repair_lines += converted
        omitted_repair_segments += omitted
    # Fallback corner braces remain direct wall-to-wall members.  Existing
    # conflicting struts are not silently used as their bearing point; only
    # crossing-free corner braces are accepted by the repair pass.
    retained_fallback: list[SupportLayoutLine] = []
    for fallback in fallback_lines:
        if any(_proper_layout_intersection(fallback, blocker) is not None for blocker in blockers):
            omitted_repair_segments += 1
            continue
        retained_fallback.append(fallback)
    lines.extend(retained_fallback)
    lines, residual_crossing_warnings = _remove_crossing_lines(lines)
    _attach_faces(lines, excavation)
    normalization_warnings = _orthogonalize_wall_to_node_secondary(lines, excavation, reference_lines=blockers)
    _apply_support_wall_clearance(lines, excavation, config)
    lines, trim_warnings = _filter_unconstructible_trimmed_lines(lines, excavation, config)
    trim_warnings.extend(normalization_warnings)
    trim_warnings.extend(_finalize_retained_support_endpoints(lines, excavation, config))
    trim_warnings.extend(residual_crossing_warnings)
    if converted_repair_lines:
        trim_warnings.append(f"围檩跨修复中有 {converted_repair_lines} 条支撑采用经显式许可的 T/Y 节点；默认方案不会自动生成该类节点。")
    if omitted_repair_segments:
        trim_warnings.append(f"围檩跨修复中删除 {omitted_repair_segments} 个过短交叉残段。")
    if not lines:
        return {
            "changed": False,
            "addedSupportCount": 0,
            "status": str(before.get("status") or "manual_review"),
            "failingFaces": sorted(failing_faces),
            "auditBefore": before,
            "auditAfter": before,
            "warnings": trim_warnings,
            "iterationCount": _iteration + 1,
            "action": "围檩跨仍需专业复核；当前几何约束下未生成新的可施工非交叉支撑。",
        }

    level_elevations = {int(item.level_index): float(item.elevation) for item in system.supports}
    existing_keys = {
        (int(item.level_index), round(item.start.x, 2), round(item.start.y, 2), round(item.end.x, 2), round(item.end.y, 2))
        for item in system.supports
    }
    existing_codes = {str(item.code) for item in system.supports}

    def _connection_signature(item) -> tuple[tuple[str, float, float], tuple[str, float, float]]:
        rows = [
            (str(item.start_face_code or ""), float((item.start_wall_connection or item.start).x), float((item.start_wall_connection or item.start).y)),
            (str(item.end_face_code or ""), float((item.end_wall_connection or item.end).x), float((item.end_wall_connection or item.end).y)),
        ]
        rows.sort(key=lambda row: (row[0], row[1], row[2]))
        return rows[0], rows[1]

    existing_connection_signatures: dict[int, list[tuple[tuple[str, float, float], tuple[str, float, float]]]] = {}
    for item in system.supports:
        if item.support_role not in {"corner_diagonal", "secondary_strut"}:
            continue
        existing_connection_signatures.setdefault(int(item.level_index), []).append(_connection_signature(item))

    def _same_connection(a, b, tol: float = 0.75) -> bool:
        return (
            a[0][0] == b[0][0] and a[1][0] == b[1][0]
            and math.hypot(a[0][1] - b[0][1], a[0][2] - b[0][2]) <= tol
            and math.hypot(a[1][1] - b[1][1], a[1][2] - b[1][2]) <= tol
        )

    added: list[SupportElement] = []
    for level_index, elevation in sorted(level_elevations.items()):
        for line_index, line in enumerate(lines, start=1):
            line_signature = _connection_signature(line)
            if any(_same_connection(line_signature, existing) for existing in existing_connection_signatures.get(level_index, [])):
                continue
            key = (level_index, round(line.start.x, 2), round(line.start.y, 2), round(line.end.x, 2), round(line.end.y, 2))
            reverse = (level_index, key[3], key[4], key[1], key[2])
            if key in existing_keys or reverse in existing_keys:
                continue
            code_index = line_index
            prefix = "SB" if line.role == "secondary_strut" else "DB"
            code = f"{prefix}-L{level_index}-F{code_index}"
            while code in existing_codes:
                code_index += 1
                code = f"{prefix}-L{level_index}-F{code_index}"
            existing_codes.add(code)
            existing_keys.add(key)
            added.append(SupportElement(
                code=code,
                level_index=level_index,
                elevation=elevation,
                start=line.start,
                end=line.end,
                support_role="secondary_strut" if line.role == "secondary_strut" else "corner_diagonal",
                layout_note=(line.layout_note or "") + " 由计算前围檩支点间距诊断增补。",
                span_length=line.span_length,
                bay_spacing=line.bay_spacing,
                start_face_code=line.start_face_code,
                end_face_code=line.end_face_code,
                start_wall_connection=line.start_wall_connection,
                end_wall_connection=line.end_wall_connection,
                centerline_offset_m=line.centerline_offset_m,
                start_wall_clearance_m=line.start_wall_clearance_m,
                end_wall_clearance_m=line.end_wall_clearance_m,
                topology_family=(
                    "hybrid_diagonal"
                    if line.start_face_code and line.end_face_code
                    else "bidirectional_grid" if line.role == "secondary_strut"
                    else "hybrid_diagonal"
                ),
                force_distribution_note=(
                    "角部墙—墙斜撑按围檩连续梁节点反力与全局联立矩阵共同设计。"
                    if line.role == "corner_diagonal"
                    else (
                        "端部平行角撑两端分别支承于围护墙/围檩的独立节点，按轴压构件及全局联立矩阵共同设计。"
                        if line.start_face_code and line.end_face_code
                        else "墙面法向短撑止于显式 T/Y 节点；按围檩节点反力与全局联立矩阵共同设计。"
                    )
                ),
                section_type="rc_rectangular",
                section=SectionDefinition(width=1.6, height=1.6, name="1600x1600 RC parallel corner brace"),
                material=MaterialDefinition(name="Concrete", grade="C40"),
                reinforcement=_support_reinforcement(level_index, "rc_rectangular"),
            ))
            existing_connection_signatures.setdefault(level_index, []).append(line_signature)
    if added:
        system.supports.extend(added)
        _assign_tributary_widths(system.supports, excavation)
        system.columns = make_column_elements(excavation, system.supports, max_unbraced_span_m=config.column_max_unbraced_span_m)
        system.support_nodes = make_support_wale_nodes(system.supports, system.wale_beams)
    after = wale_support_bay_audit(
        excavation,
        list(system.supports),
        target_bay_m=config.max_wale_support_bay_m,
        hard_max_bay_m=config.hard_max_wale_support_bay_m,
    )
    action = (
        f"围檩支点间距诊断在 {len(failing_faces)} 个墙面发现超限，移除 {removed_legacy_t_y} 根旧版墙—支撑短撑，"
        f"自动增补 {len(added)} 根独立平行墙—墙角撑；最大支点间距由 "
        f"{before.get('maxBayM', 0)}m 降至 {after.get('maxBayM', 0)}m。"
    )
    system.layout_summary = dict(system.layout_summary or {})
    system.layout_summary.setdefault("designNotes", []).append(action)
    system.layout_summary.setdefault("designNotes", []).extend(trim_warnings)
    system.layout_summary["waleSupportBayAudit"] = after

    result = {
        "changed": bool(added or removed_legacy_t_y),
        "addedSupportCount": len(added),
        "removedLegacyTYSupportCount": removed_legacy_t_y,
        "addedSupportIds": [item.id for item in added],
        "failingFaces": sorted(failing_faces),
        "status": after.get("status"),
        "auditBefore": before,
        "auditAfter": after,
        "action": action,
        "iterationCount": _iteration + 1,
    }
    # Crossing-free T/Y repair can move the controlling bay to a neighbouring
    # interval.  Re-audit and run at most three additional local passes so the
    # final topology satisfies the configured hard bay limit, rather than
    # stopping after one nearly-complete pass (for example 9.51 m vs 9.0 m).
    if after.get("status") in {"fail", "warning"} and added and _iteration < 3:
        follow_up = repair_wale_support_bays(project, config, _iteration=_iteration + 1)
        result["changed"] = bool(result["changed"] or follow_up.get("changed"))
        result["addedSupportCount"] = int(result["addedSupportCount"]) + int(follow_up.get("addedSupportCount", 0))
        result["removedLegacyTYSupportCount"] = int(result.get("removedLegacyTYSupportCount", 0)) + int(follow_up.get("removedLegacyTYSupportCount", 0))
        result["addedSupportIds"] = [*result["addedSupportIds"], *list(follow_up.get("addedSupportIds", []))]
        result["status"] = follow_up.get("status", result["status"])
        result["auditAfter"] = follow_up.get("auditAfter", result["auditAfter"])
        result["failingFaces"] = sorted(set(result["failingFaces"]) | set(follow_up.get("failingFaces", [])))
        result["iterationCount"] = int(follow_up.get("iterationCount", _iteration + 2))
        result["action"] = f"{action} 后续局部复核：{follow_up.get('action', '')}".strip()
    return result


def unrestrained_concave_face_codes(excavation, supports: list[SupportElement]) -> list[str]:
    """Return re-entrant-adjacent wall faces without any direct support end."""
    points = _dedup_points(list(excavation.outline.points))
    concave_vertices = _concave_vertex_indices(points)
    if not concave_vertices:
        return []
    candidate_indices = {
        (vertex_index - 1) % len(points) for vertex_index in concave_vertices
    } | {
        vertex_index % len(points) for vertex_index in concave_vertices
    }
    counts = {str(segment.name): 0 for segment in excavation.segments}
    for support in supports:
        for point, stored_code in ((support.start, support.start_face_code), (support.end, support.end_face_code)):
            face_code = stored_code
            if not face_code:
                hit = _nearest_face_hit(point, excavation)
                face_code = hit.face_code if hit else None
            if face_code in counts:
                counts[str(face_code)] += 1
    missing: list[str] = []
    for index in sorted(candidate_indices):
        if index >= len(excavation.segments):
            continue
        segment = excavation.segments[index]
        if float(segment.length) >= MIN_MAIN_STRUT_SPAN_M and counts.get(str(segment.name), 0) == 0:
            missing.append(str(segment.name))
    return sorted(set(missing))


def repair_concave_return_supports(project, config: SupportLayoutConfig | None = None) -> dict[str, object]:
    """Add only the missing local return-wall struts to an existing project.

    This repair is deliberately additive: existing manual/optimized members are
    preserved.  It is therefore safe to run as a calculation preflight for old
    V3.4 projects whose L/T-shaped pit was generated before return-wall support
    detection was introduced.
    """
    excavation = getattr(project, "excavation", None)
    system = getattr(project, "retaining_system", None)
    if not excavation or not system:
        return {"changed": False, "addedSupportCount": 0, "missingFaces": []}
    missing_before = unrestrained_concave_face_codes(excavation, list(system.supports or []))
    if not missing_before:
        return {"changed": False, "addedSupportCount": 0, "missingFaces": []}
    if config is None:
        settings = getattr(project, "design_settings", None)
        config = SupportLayoutConfig(
            support_wall_clearance_m=float(getattr(settings, "support_wall_clearance_m", 1.0)),
            max_direct_strut_span_m=float(getattr(settings, "max_direct_strut_span_m", 24.0)),
            diagonal_brace_min_wall_length_m=float(getattr(settings, "diagonal_brace_min_wall_length_m", 18.0)),
            corner_diagonal_min_offset_m=float(getattr(settings, "corner_diagonal_min_offset_m", 3.5)),
            corner_diagonal_max_offset_m=float(getattr(settings, "corner_diagonal_max_offset_m", 8.0)),
            corner_diagonal_max_wall_fraction=float(getattr(settings, "corner_diagonal_max_wall_fraction", 0.40)),
            corner_diagonal_family_count=int(getattr(settings, "corner_diagonal_family_count", 2)),
            corner_diagonal_family_spacing_m=float(getattr(settings, "corner_diagonal_family_spacing_m", 3.0)),
            corner_diagonal_parallel_tolerance_deg=float(getattr(settings, "corner_diagonal_parallel_tolerance_deg", 5.0)),
            prefer_diagonal_braces=bool(getattr(settings, "prefer_diagonal_braces", True)),
            target_main_support_spacing_m=float(getattr(settings, "default_support_spacing", TARGET_MAIN_SUPPORT_SPACING_M)),
            column_max_unbraced_span_m=COLUMN_MAX_UNBRACED_SPAN_M,
            topology_strategy="hybrid_diagonal",
        )
    config = config.normalized()
    points = _dedup_points(list(excavation.outline.points))
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    # One representative level is sufficient to describe existing plan lines.
    levels = sorted({int(item.level_index) for item in system.supports})
    representative_level = levels[0] if levels else 1
    existing_lines = [
        SupportLayoutLine(
            item.support_role,
            item.start,
            item.end,
            float(item.span_length or _distance(item.start, item.end)),
            item.bay_spacing,
            item.layout_note or "existing support",
            item.start_face_code,
            item.end_face_code,
            item.start_wall_connection,
            item.end_wall_connection,
            item.centerline_offset_m,
            item.start_wall_clearance_m,
            item.end_wall_clearance_m,
            item.topology_family,
        )
        for item in system.supports
        if int(item.level_index) == representative_level
    ]
    generated_lines, warnings = _concave_return_wall_layout(
        points,
        obstacles,
        excavation,
        existing_lines,
        config.target_main_support_spacing_m,
    )
    _attach_faces(generated_lines, excavation)
    retained_returns: list[SupportLayoutLine] = []
    converted_returns = 0
    omitted_returns = 0
    for line in generated_lines:
        preferred_face = str(line.start_face_code or line.end_face_code or "")
        retained, converted, omitted = _terminate_from_preferred_wall(
            line, existing_lines, preferred_face,
            minimum_stub_length_m=MIN_CORNER_BRACE_LEG_M + 0.7 * config.support_wall_clearance_m,
            excavation=excavation,
        )
        retained_returns.extend(retained)
        converted_returns += converted
        omitted_returns += omitted
    generated_lines, residual_crossing_warnings = _remove_crossing_lines(retained_returns)
    if converted_returns:
        warnings.append(f"凹形回墙修复中有 {converted_returns} 条支撑改为止于既有支撑节点的非交叉短撑。")
    if omitted_returns:
        warnings.append(f"凹形回墙修复中删除 {omitted_returns} 个过短交叉残段。")
    warnings.extend(residual_crossing_warnings)
    _attach_faces(generated_lines, excavation)
    warnings.extend(_orthogonalize_wall_to_node_secondary(generated_lines, excavation, reference_lines=existing_lines))
    clearance_warnings = _apply_support_wall_clearance(generated_lines, excavation, config)
    warnings.extend(clearance_warnings)
    generated_lines, trim_warnings = _filter_unconstructible_trimmed_lines(generated_lines, excavation, config)
    warnings.extend(trim_warnings)
    warnings.extend(_finalize_retained_support_endpoints(generated_lines, excavation, config))
    if not generated_lines:
        return {"changed": False, "addedSupportCount": 0, "missingFaces": missing_before, "warnings": warnings}
    level_elevations: dict[int, float] = {}
    for item in system.supports:
        level_elevations.setdefault(int(item.level_index), float(item.elevation))
    if not level_elevations:
        return {"changed": False, "addedSupportCount": 0, "missingFaces": missing_before, "warnings": warnings}
    added: list[SupportElement] = []
    existing_keys = {
        (int(item.level_index), round(item.start.x, 3), round(item.start.y, 3), round(item.end.x, 3), round(item.end.y, 3))
        for item in system.supports
    }
    existing_codes = {str(item.code) for item in system.supports}
    for level_index, elevation in sorted(level_elevations.items()):
        for line_index, line in enumerate(generated_lines, start=1):
            key = (level_index, round(line.start.x, 3), round(line.start.y, 3), round(line.end.x, 3), round(line.end.y, 3))
            reverse_key = (level_index, key[3], key[4], key[1], key[2])
            if key in existing_keys or reverse_key in existing_keys:
                continue
            code_index = line_index
            code = f"GS-L{level_index}-R{code_index}"
            while code in existing_codes:
                code_index += 1
                code = f"GS-L{level_index}-R{code_index}"
            existing_codes.add(code)
            added.append(
                SupportElement(
                    code=code,
                    level_index=level_index,
                    elevation=elevation,
                    start=line.start,
                    end=line.end,
                    support_role="secondary_strut",
                    layout_note=line.layout_note + " 由计算前拓扑诊断增补。",
                    span_length=line.span_length,
                    bay_spacing=line.bay_spacing,
                    start_face_code=line.start_face_code,
                    end_face_code=line.end_face_code,
                    start_wall_connection=line.start_wall_connection,
                    end_wall_connection=line.end_wall_connection,
                    centerline_offset_m=line.centerline_offset_m,
                    start_wall_clearance_m=line.start_wall_clearance_m,
                    end_wall_clearance_m=line.end_wall_clearance_m,
                    topology_family="bidirectional_grid",
                    force_distribution_note="凹形回墙局部法向短撑止于显式 T/Y 节点；按围檩连续梁节点反力和全局矩阵复核。",
                    section_type="rc_rectangular",
                    section=SectionDefinition(width=1.8, height=1.8, name="1800x1800 RC"),
                    material=MaterialDefinition(name="Concrete", grade="C40"),
                    reinforcement=_support_reinforcement(level_index, "rc_rectangular"),
                )
            )
    if not added:
        return {"changed": False, "addedSupportCount": 0, "missingFaces": missing_before, "warnings": warnings}
    system.supports.extend(added)
    _assign_tributary_widths(system.supports, excavation)
    system.columns = make_column_elements(excavation, system.supports, max_unbraced_span_m=config.column_max_unbraced_span_m)
    system.support_nodes = make_support_wale_nodes(system.supports, system.wale_beams)
    evidence, unresolved = _partition_repair_messages(warnings)
    system.warnings = list(dict.fromkeys([*(system.warnings or []), *unresolved]))
    system.layout_summary = support_layout_summary(system.supports, system.columns, system.ring_beams, system.warnings, config=config)
    system.layout_summary.setdefault("designNotes", []).extend(evidence)
    system.layout_summary.setdefault("designNotes", []).append(f"计算前自动增补 {len(added)} 根凹形回墙局部对撑。")
    missing_after = unrestrained_concave_face_codes(excavation, system.supports)
    return {
        "changed": True,
        "addedSupportCount": len(added),
        "addedSupportIds": [item.id for item in added],
        "missingFacesBefore": missing_before,
        "missingFacesAfter": missing_after,
        "warnings": warnings,
    }


def make_ring_beams(excavation, elevations: list[float]) -> list[BeamElement]:
    points = _dedup_points(list(excavation.outline.points))
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    if not _should_use_ring(points, obstacles):
        return []
    rx0, ry0, rx1, ry1 = _ring_rectangle(points, obstacles)
    corners = [Point2D(x=rx0, y=ry0), Point2D(x=rx1, y=ry0), Point2D(x=rx1, y=ry1), Point2D(x=rx0, y=ry1)]
    beams: list[BeamElement] = []
    for level_idx, elevation in enumerate(elevations, start=1):
        for idx, (a, b) in enumerate(zip(corners, corners[1:] + corners[:1]), start=1):
            beams.append(BeamElement(
                code=f"RB-L{level_idx}-{idx}",
                axis=Polyline2D(points=[a, b], closed=False),
                elevation=elevation,
                section=SectionDefinition(width=1.2, height=1.0, name="1200x1000 RC ring beam"),
                material=MaterialDefinition(name="Concrete", grade="C40"),
                beam_role="ring_beam",
                support_level=level_idx,
            ))
    return beams


def _column_key(p: Point2D) -> tuple[int, int]:
    return (round(p.x / COLUMN_DEDUP_GRID_M), round(p.y / COLUMN_DEDUP_GRID_M))


def _segment_intersection_point(a: Point2D, b: Point2D, c: Point2D, d: Point2D) -> Point2D | None:
    """Return a proper segment intersection for grid-column placement."""
    x1, y1, x2, y2 = a.x, a.y, b.x, b.y
    x3, y3, x4, y4 = c.x, c.y, d.x, d.y
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) <= EPS:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    raw_point = Point2D(x=px, y=py)
    # Check segment membership before coordinate rounding.  Rounding a valid
    # oblique intersection to millimetres can introduce a cross-product error
    # larger than the geometric tolerance and incorrectly drop the shared node.
    # Support endpoints are persisted at millimetre precision.  A T/Y node can
    # therefore be a few tenths of a millimetre off the analytical line after
    # repeated clipping and wall-clearance offsets.  Use the same 2.5 mm design
    # tolerance as node association instead of dropping the required column.
    if _point_on_segment(raw_point, a, b, tol=2.5e-3) and _point_on_segment(raw_point, c, d, tol=2.5e-3):
        return Point2D(x=round(px, 3), y=round(py, 3))
    return None


def make_column_elements(excavation, supports: list[SupportElement], max_unbraced_span_m: float = COLUMN_MAX_UNBRACED_SPAN_M) -> list[ColumnElement]:
    if not supports:
        return []
    max_unbraced_span_m = max(6.0, min(30.0, float(max_unbraced_span_m)))
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    column_points: dict[tuple[int, int], ColumnPlanPoint] = {}

    # Non-ring support families may meet only as explicit T/Y/end nodes.  A
    # secondary or diagonal member is shortened at the first main-strut node by
    # the layout generator, so the intersection is commonly an endpoint of one
    # member and an interior point of the other.  Such nodes still require a
    # temporary column and must not be discarded as a generic shared endpoint.
    grid_roles = {"main_strut", "secondary_strut", "corner_diagonal"}
    for index, first in enumerate(supports):
        if first.support_role not in grid_roles:
            continue
        for second in supports[index + 1:]:
            if second.level_index != first.level_index or second.support_role not in grid_roles:
                continue
            point = _segment_intersection_point(first.start, first.end, second.start, second.end)
            if point is None:
                continue
            first_at_end = min(_distance(point, first.start), _distance(point, first.end)) <= 0.05
            second_at_end = min(_distance(point, second.start), _distance(point, second.end)) <= 0.05
            wall_hit = _nearest_face_hit(point, excavation, tolerance=0.35)
            # A common endpoint on the retaining wall is a support-to-wale node.
            # Internal endpoint/interior or endpoint/endpoint meetings are real
            # grid nodes and therefore receive a column.
            if first_at_end and second_at_end and wall_hit is not None:
                continue
            if not _point_avoids_obstacles(point, obstacles):
                continue
            key = _column_key(point)
            if key not in column_points:
                column_points[key] = ColumnPlanPoint(location=point)
            column_points[key].support_codes.update({first.code, second.code})

    # Associate every member that terminates at or passes through a generated
    # node.  This keeps split secondary/diagonal stubs and the continuous main
    # strut on the same column service record.
    for item in column_points.values():
        for support in supports:
            if support.support_role not in grid_roles:
                continue
            if _point_on_segment(item.location, support.start, support.end, tol=2.5e-3):
                item.support_codes.add(support.code)

    for support in supports:
        if support.support_role not in {"main_strut", "secondary_strut", "corner_diagonal", "ring_strut"}:
            continue
        length = _distance(support.start, support.end)
        n_cols = max(0, int(math.ceil(length / max_unbraced_span_m)) - 1)
        for idx in range(n_cols):
            t = (idx + 1) / (n_cols + 1)
            point = Point2D(x=round(support.start.x + (support.end.x - support.start.x) * t, 3), y=round(support.start.y + (support.end.y - support.start.y) * t, 3))
            if not _point_avoids_obstacles(point, obstacles):
                continue
            key = _column_key(point)
            if key not in column_points:
                column_points[key] = ColumnPlanPoint(location=point)
            column_points[key].support_codes.add(support.code)
    if not column_points:
        level1 = [s for s in supports if s.level_index == 1 and s.support_role in {"main_strut", "secondary_strut", "ring_strut"}]
        if level1:
            p = Point2D(x=round(mean([(s.start.x + s.end.x) / 2.0 for s in level1]), 3), y=round(mean([(s.start.y + s.end.y) / 2.0 for s in level1]), 3))
            if _point_avoids_obstacles(p, obstacles):
                column_points[_column_key(p)] = ColumnPlanPoint(location=p, support_codes={s.code for s in level1})
    columns: list[ColumnElement] = []
    for idx, item in enumerate(sorted(column_points.values(), key=lambda c: (c.location.x, c.location.y)), start=1):
        columns.append(ColumnElement(
            code=f"STC-{idx:03d}",
            location=item.location,
            top_elevation=excavation.top_elevation,
            bottom_elevation=excavation.bottom_elevation - 8.0,
            section=SectionDefinition(diameter=0.8, width=0.8, height=0.8, name="D800 steel lattice column with bored pile"),
            material=MaterialDefinition(name="Steel", grade="Q355"),
            support_codes=sorted(item.support_codes),
            service_area_note="立柱位置由非交叉 T/Y 支撑节点及跨长控制点生成，自动避让坡道、出土口、中心岛和保护区。",
        ))
    return columns


def _node_reinforcement() -> list[ReinforcementGroup]:
    return [
        ReinforcementGroup(name="支撑端部附加竖向筋", bar_type="additional", diameter=20, count=4, grade="HRB400", location_description="support-to-wale node vertical additional bars", check_status="preliminary"),
        ReinforcementGroup(name="节点区加密箍筋", bar_type="stirrup", diameter=12, spacing=100, grade="HRB400", location_description="confined stirrups in wale/support node core", check_status="preliminary"),
    ]


def make_support_wale_nodes(supports: list[SupportElement], wale_beams: list[BeamElement]) -> list[SupportWaleNode]:
    wale_by_key = {(beam.support_level, beam.code.split("-")[-1]): beam for beam in wale_beams if beam.beam_role == "wale_beam"}
    nodes: list[SupportWaleNode] = []
    for support in supports:
        endpoint_rows = (
            ("A", support.start_wall_connection or support.start, support.start_face_code),
            ("B", support.end_wall_connection or support.end, support.end_face_code),
        )
        for side, point, face_code in endpoint_rows:
            if support.support_role == "ring_strut" and not face_code:
                node_type = "ring_strut_to_ring"
            elif support.support_role == "corner_diagonal":
                node_type = "diagonal_to_wale"
            else:
                node_type = "strut_to_wale"
            wale = wale_by_key.get((support.level_index, face_code or ""))
            plate_w = max(0.60, (support.section.width or 1.2) * 0.75)
            plate_h = max(0.60, (support.section.height or 1.2) * 0.75)
            node = SupportWaleNode(
                code=f"ND-{support.code}-{side}",
                support_id=support.id,
                support_code=support.code,
                level_index=support.level_index,
                elevation=support.elevation,
                location=point,
                face_code=face_code,
                wale_beam_code=wale.code if wale else None,
                node_type=node_type,  # type: ignore[arg-type]
                bearing_plate=BearingPlateDesign(plate_width=round(plate_w, 3), plate_height=round(plate_h, 3), plate_thickness=0.04, bearing_area=round(plate_w * plate_h, 3), design_note="节点承压板按支撑截面比例初选；计算阶段按支撑轴力包络更新承压应力。"),
                reinforcement=_node_reinforcement(),
                check_status="manual_review",
                design_note="围檩-支撑节点已建模；端部承压、附加筋和加密箍筋为子集校核结果，正式节点详图需复核锚固、局压和施工焊接/连接构造。",
            )
            nodes.append(node)
    return nodes


def support_layout_summary(supports: list[SupportElement], columns: list[ColumnElement], ring_beams: list[BeamElement], warnings: list[str], config: SupportLayoutConfig | None = None) -> dict:
    config = (config or SupportLayoutConfig()).normalized()
    by_role: dict[str, int] = {}
    for support in supports:
        by_role[support.support_role] = by_role.get(support.support_role, 0) + 1
    main_spans = [s.span_length for s in supports if s.support_role in {"main_strut", "secondary_strut"} and s.span_length]
    return {
        "supportCount": len(supports),
        "supportCountByRole": by_role,
        "columnCount": len(columns),
        "ringBeamCount": len(ring_beams),
        "maxMainSpan": round(max(main_spans), 3) if main_spans else None,
        "targetMainSupportSpacing_m": config.target_main_support_spacing_m,
        "columnMaxUnbracedSpan_m": config.column_max_unbraced_span_m,
        "supportWallClearance_m": config.support_wall_clearance_m,
        "maxDirectStrutSpan_m": config.max_direct_strut_span_m,
        "topologyStrategy": config.topology_strategy,
        "practicalSupportSpacingRange_m": [MIN_PRACTICAL_MAIN_SUPPORT_SPACING_M, MAX_PRACTICAL_MAIN_SUPPORT_SPACING_M],
        "tributaryWidthMethod": "V1.6 continuous wale-beam reactions; tributary width retained as explanatory fallback/reference",
        "supportForceDistribution": "wall pressure band -> continuous wale beam -> elastic strut node reactions",
        "obstacleAvoidance": "active rectangular/polygon obstacles are treated as no-crossing/no-column zones",
        "warnings": warnings,
    }
