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
    diagonal_brace_min_wall_length_m: float = 18.0
    prefer_diagonal_braces: bool = True
    topology_strategy: str = "balanced_grid"

    def normalized(self) -> "SupportLayoutConfig":
        strategy = str(self.topology_strategy or "balanced_grid")
        if strategy not in {"direct_grid", "hybrid_diagonal", "bidirectional_grid", "balanced_grid"}:
            strategy = "balanced_grid"
        return SupportLayoutConfig(
            target_main_support_spacing_m=max(MIN_PRACTICAL_MAIN_SUPPORT_SPACING_M, min(MAX_PRACTICAL_MAIN_SUPPORT_SPACING_M, float(self.target_main_support_spacing_m))),
            column_max_unbraced_span_m=max(6.0, min(30.0, float(self.column_max_unbraced_span_m))),
            support_wall_clearance_m=max(0.35, min(3.0, float(self.support_wall_clearance_m))),
            max_direct_strut_span_m=max(12.0, min(45.0, float(self.max_direct_strut_span_m))),
            diagonal_brace_min_wall_length_m=max(8.0, min(40.0, float(self.diagonal_brace_min_wall_length_m))),
            prefer_diagonal_braces=bool(self.prefer_diagonal_braces),
            topology_strategy=strategy,
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


@dataclass
class ColumnPlanPoint:
    location: Point2D
    support_codes: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SegmentFaceHit:
    face_code: str
    t: float
    length: float


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
        if ((pi.y > p.y) != (pj.y > p.y)) and (p.x < (pj.x - pi.x) * (p.y - pi.y) / max(pj.y - pi.y, EPS) + pi.x):
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


def _nearest_face_hit(point: Point2D, excavation) -> SegmentFaceHit | None:
    best: SegmentFaceHit | None = None
    best_dist = float("inf")
    for segment in getattr(excavation, "segments", []):
        t, dist = _point_segment_projection(point, segment.start, segment.end)
        if dist < best_dist:
            best_dist = dist
            best = SegmentFaceHit(face_code=segment.name, t=t, length=float(segment.length))
    return best if best and best_dist <= 0.75 else best


def _attach_faces(lines: list[SupportLayoutLine], excavation) -> None:
    for line in lines:
        s_hit = _nearest_face_hit(line.start, excavation)
        e_hit = _nearest_face_hit(line.end, excavation)
        if s_hit:
            line.start_face_code = s_hit.face_code
        if e_hit:
            line.end_face_code = e_hit.face_code


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
        line.start_wall_connection = original_start
        line.end_wall_connection = original_end
        start_segment = by_code.get(str(line.start_face_code or ""))
        end_segment = by_code.get(str(line.end_face_code or ""))
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


def _hybridize_long_struts(lines: list[SupportLayoutLine], points: list[Point2D], config: SupportLayoutConfig) -> tuple[list[SupportLayoutLine], list[str]]:
    if config.topology_strategy != "hybrid_diagonal":
        return lines, []
    diagonal_count = sum(line.role == "corner_diagonal" for line in lines)
    if diagonal_count == 0:
        return lines, ["混合斜撑策略未找到满足墙长和角度条件的斜撑，保留原对撑体系。"]
    min_x, min_y, max_x, max_y, span_x, span_y = _bounds(points)
    long_is_x = span_x >= span_y
    removed = 0
    output: list[SupportLayoutLine] = []
    for line in lines:
        if line.role != "main_strut" or line.span_length <= config.max_direct_strut_span_m:
            output.append(line)
            continue
        mid = ((line.start.x + line.end.x) * 0.5, (line.start.y + line.end.y) * 0.5)
        ratio = ((mid[0] - min_x) / max(span_x, EPS)) if long_is_x else ((mid[1] - min_y) / max(span_y, EPS))
        if ratio <= 0.18 or ratio >= 0.82:
            removed += 1
            continue
        output.append(line)
    notes = []
    if removed:
        notes.append(f"混合斜撑策略已用角部短斜撑替代 {removed} 条靠近转角的超长对撑，降低长细比和施工占用。")
    return output, notes


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


def _main_strut_layout(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]], target_spacing: float = TARGET_MAIN_SUPPORT_SPACING_M) -> tuple[list[SupportLayoutLine], list[str]]:
    min_x, min_y, max_x, max_y, span_x, span_y = _bounds(points)
    warnings: list[str] = []
    long_is_x = span_x >= span_y
    long_span = span_x if long_is_x else span_y
    count = _main_support_count(long_span, target_spacing)
    if count <= 0:
        return [], ["基坑平面尺寸过小，未自动生成主对撑。"]
    bay_spacing = long_span / (count + 1)
    lines: list[SupportLayoutLine] = []
    skipped_for_obstacle = 0
    shifted_count = 0
    if long_is_x:
        vertex_coords = [p.x for p in points]
        for i in range(count):
            x = _scan_coordinate_away_from_vertices(min_x + (i + 1) * bay_spacing, vertex_coords, span_x)
            segments, x_used, shifted = _find_viable_main_line(points=points, obstacles=obstacles, long_is_x=True, base_coord=x, min_coord=min_x, max_coord=max_x, span=span_x)
            if not segments:
                skipped_for_obstacle += 1
                continue
            if shifted:
                shifted_count += 1
            for start, end in segments:
                note = "主对撑沿短跨方向布置，沿长向按 3-6m 工程常用分仓间距布置；端点吸附到围檩墙面。"
                if shifted:
                    note += f" 已由自动修复器从原扫描线移动至 x={x_used:.2f}m 以避让障碍/出土口。"
                lines.append(SupportLayoutLine("main_strut", start, end, round(_distance(start, end), 3), round(bay_spacing, 3), note))
    else:
        vertex_coords = [p.y for p in points]
        for i in range(count):
            y = _scan_coordinate_away_from_vertices(min_y + (i + 1) * bay_spacing, vertex_coords, span_y)
            segments, y_used, shifted = _find_viable_main_line(points=points, obstacles=obstacles, long_is_x=False, base_coord=y, min_coord=min_y, max_coord=max_y, span=span_y)
            if not segments:
                skipped_for_obstacle += 1
                continue
            if shifted:
                shifted_count += 1
            for start, end in segments:
                note = "主对撑沿短跨方向布置，沿长向按 3-6m 工程常用分仓间距布置；端点吸附到围檩墙面。"
                if shifted:
                    note += f" 已由自动修复器从原扫描线移动至 y={y_used:.2f}m 以避让障碍/出土口。"
                lines.append(SupportLayoutLine("main_strut", start, end, round(_distance(start, end), 3), round(bay_spacing, 3), note))
    if not lines:
        warnings.append("未能从基坑轮廓自动生成有效主对撑；请检查凹多边形、坡道/出土口避让或手动布置支撑。")
    if shifted_count:
        warnings.append(f"支撑布置自动修复器已移动 {shifted_count} 条主支撑扫描线，以避让地下室柱网/坡道/出土口等障碍。")
    if skipped_for_obstacle:
        warnings.append(f"已因地下室柱网/坡道/出土口等避让区跳过 {skipped_for_obstacle} 条候选主支撑。")
    if len(lines) > count:
        warnings.append("检测到凹形或分叉基坑，部分扫描线被拆分为多个独立对撑，已避免跨越坑外空区。")
    return lines, warnings


def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    l1 = math.hypot(*v1)
    l2 = math.hypot(*v2)
    if l1 <= EPS or l2 <= EPS:
        return 0.0
    dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
    return math.degrees(math.acos(dot))




def _should_use_bidirectional_grid(points: list[Point2D], excavation) -> bool:
    """Return True when a deep elongated pit needs direct restraint on all faces.

    A single family of short-span struts leaves the two return walls relying on
    corner braces and long wale cantilevers.  For deep/large rectangular pits a
    second orthogonal family is generated at practical column-grid locations.
    """
    _, _, _, _, span_x, span_y = _bounds(points)
    short_span = min(span_x, span_y)
    long_span = max(span_x, span_y)
    aspect = long_span / max(short_span, EPS)
    depth = abs(float(getattr(excavation, "top_elevation", 0.0)) - float(getattr(excavation, "bottom_elevation", 0.0)))
    return short_span >= 24.0 and depth >= 18.0 and (aspect >= 1.55 or long_span >= 65.0)


def _secondary_grid_layout(
    points: list[Point2D],
    obstacles: list[tuple[ConstructionObstacle, list[Point2D]]],
    excavation,
    force: bool = False,
) -> tuple[list[SupportLayoutLine], list[str]]:
    """Generate the orthogonal support family for deep elongated pits."""
    if not force and not _should_use_bidirectional_grid(points, excavation):
        return [], []
    min_x, min_y, max_x, max_y, span_x, span_y = _bounds(points)
    long_is_x = span_x >= span_y
    short_span = span_y if long_is_x else span_x
    # 12--18 m secondary-grid bays work with the default temporary-column grid.
    count = max(1, min(4, int(math.ceil(short_span / 15.0)) - 1))
    spacing = short_span / (count + 1)
    lines: list[SupportLayoutLine] = []
    shifted_count = 0
    skipped = 0
    for idx in range(count):
        coordinate = (min_y if long_is_x else min_x) + (idx + 1) * spacing
        segments, used, shifted = _find_viable_main_line(
            points=points,
            obstacles=obstacles,
            long_is_x=not long_is_x,
            base_coord=coordinate,
            min_coord=min_y if long_is_x else min_x,
            max_coord=max_y if long_is_x else max_x,
            span=short_span,
        )
        if not segments:
            skipped += 1
            continue
        shifted_count += int(shifted)
        for start, end in segments:
            note = (
                "深大长条形基坑双向网格次对撑：直接约束回墙，"
                "与主对撑交点设置临时立柱/刚性节点，避免角撑独担整面回墙荷载。"
            )
            if shifted:
                note += f" 已移至坐标 {used:.2f}m 以避让障碍。"
            lines.append(
                SupportLayoutLine(
                    "secondary_strut",
                    start,
                    end,
                    round(_distance(start, end), 3),
                    round(spacing, 3),
                    note,
                )
            )
    warnings = []
    if lines:
        warnings.append(f"已增加 {len(lines)} 条正交次对撑，形成双向支撑网格并缩短回墙围檩无支点跨度。")
    if shifted_count:
        warnings.append(f"其中 {shifted_count} 条次对撑已自动平移避让障碍。")
    if skipped:
        warnings.append(f"有 {skipped} 条次对撑因障碍或轮廓限制未生成，需人工复核回墙传力。")
    return lines, warnings


def _corner_diagonal_layout(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]], config: SupportLayoutConfig | None = None) -> list[SupportLayoutLine]:
    config = (config or SupportLayoutConfig()).normalized()
    if len(points) < 4 or not config.prefer_diagonal_braces:
        return []
    _, _, _, _, span_x, span_y = _bounds(points)
    short_span, long_span = min(span_x, span_y), max(span_x, span_y)
    if short_span < 12.0:
        return []
    orientation = 1.0 if _signed_area(points) >= 0 else -1.0
    if not ((long_span / max(short_span, EPS) >= 1.35) or long_span >= 36.0):
        return []
    lines: list[SupportLayoutLine] = []
    n = len(points)
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
        # A short return wall still needs a local corner restraint.  The project
        # threshold controls whether the brace is enlarged into the preferred
        # long-wall hybrid topology; it must not suppress the basic corner brace.
        basic_min_wall = max(2.0 * MIN_CORNER_BRACE_LEG_M, 5.0)
        if len_prev < basic_min_wall or len_next < basic_min_wall:
            continue
        is_long_wall_corner = (
            len_prev >= config.diagonal_brace_min_wall_length_m
            and len_next >= config.diagonal_brace_min_wall_length_m
        )
        use_extended_brace = config.topology_strategy == "hybrid_diagonal" and is_long_wall_corner
        # Keep ordinary corner braces inside the first support bay.  At long-wall
        # corners the hybrid scheme may use a larger diagonal to replace a long
        # through-strut and shorten the principal load path.
        offset_cap = 9.0 if use_extended_brace else 5.0
        offset_ratio = 0.20 if use_extended_brace else 0.12
        offset_prev = min(max(3.0, short_span * offset_ratio), offset_cap, len_prev * 0.38)
        offset_next = min(max(3.0, short_span * offset_ratio), offset_cap, len_next * 0.38)
        p1, p2 = _point_at(curr, prev, offset_prev), _point_at(curr, nxt, offset_next)
        if _line_segment_samples_inside(p1, p2, points) and _line_avoids_obstacles(p1, p2, obstacles):
            lines.append(SupportLayoutLine("corner_diagonal", Point2D(x=round(p1.x, 3), y=round(p1.y, 3)), Point2D(x=round(p2.x, 3), y=round(p2.y, 3)), round(_distance(p1, p2), 3), None, "凸直角附近设置角撑，用于角部变形和扭转刚度控制；凹角、坡道和出土口不自动跨越。"))
    return lines


def _center_island_polygon(obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> list[Point2D] | None:
    for obstacle, poly in obstacles:
        if obstacle.obstacle_type == "center_island":
            return poly
    return None


def _should_use_ring(points: list[Point2D], obstacles: list[tuple[ConstructionObstacle, list[Point2D]]]) -> bool:
    _, _, _, _, span_x, span_y = _bounds(points)
    short_span, long_span = min(span_x, span_y), max(span_x, span_y)
    return bool(_center_island_polygon(obstacles)) or (short_span >= RING_SUPPORT_MIN_SHORT_SPAN_M and long_span / max(short_span, EPS) <= RING_SUPPORT_MAX_ASPECT)


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
    o1 = _orientation2(a.start, a.end, b.start)
    o2 = _orientation2(a.start, a.end, b.end)
    o3 = _orientation2(b.start, b.end, a.start)
    o4 = _orientation2(b.start, b.end, a.end)
    if abs(o1) <= EPS or abs(o2) <= EPS or abs(o3) <= EPS or abs(o4) <= EPS:
        return False
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _remove_crossing_lines(lines: list[SupportLayoutLine]) -> tuple[list[SupportLayoutLine], list[str]]:
    if not lines:
        return lines, []
    priority = {"main_strut": 0, "secondary_strut": 1, "ring_strut": 2, "corner_diagonal": 3}
    kept: list[SupportLayoutLine] = []
    skipped = 0
    for line in sorted(lines, key=lambda item: (priority.get(item.role, 9), item.span_length, item.start.x, item.start.y)):
        def incompatible_crossing(other: SupportLayoutLine) -> bool:
            # Main/secondary grid crossings are intentional structural nodes and
            # receive temporary columns in make_column_elements().
            if {line.role, other.role} == {"main_strut", "secondary_strut"}:
                return False
            return _support_lines_cross(line, other)
        if any(incompatible_crossing(other) for other in kept):
            skipped += 1
            continue
        kept.append(line)
    kept.sort(key=lambda item: (priority.get(item.role, 9), item.start.x, item.start.y, item.end.x, item.end.y))
    warnings = [f"已跳过 {skipped} 条会与既有支撑无节点交叉的候选支撑；请复核角撑/环撑局部布置。"] if skipped else []
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
            )
        )
    return output, snapped

def generate_support_layout_lines(excavation, config: SupportLayoutConfig | None = None) -> tuple[list[SupportLayoutLine], list[str]]:
    config = (config or SupportLayoutConfig()).normalized()
    points = _dedup_points(list(excavation.outline.points))
    if len(points) < 3:
        return [], ["基坑轮廓点数不足，无法生成水平支撑。"]
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    if _should_use_ring(points, obstacles):
        lines, warnings = _ring_layout(points, obstacles)
    else:
        main_lines, warnings = _main_strut_layout(points, obstacles, config.target_main_support_spacing_m)
        force_secondary = config.topology_strategy == "bidirectional_grid"
        secondary_lines, secondary_warnings = _secondary_grid_layout(points, obstacles, excavation, force=force_secondary)
        if config.topology_strategy == "direct_grid":
            secondary_lines = []
            secondary_warnings = []
        warnings.extend(secondary_warnings)
        return_wall_lines, return_wall_warnings = _concave_return_wall_layout(
            points,
            obstacles,
            excavation,
            [*main_lines, *secondary_lines],
            config.target_main_support_spacing_m,
        )
        warnings.extend(return_wall_warnings)
        diagonal_lines = _corner_diagonal_layout(points, obstacles, config)
        lines = main_lines + secondary_lines + return_wall_lines + diagonal_lines
        lines, hybrid_warnings = _hybridize_long_struts(lines, points, config)
        warnings.extend(hybrid_warnings)
        if diagonal_lines:
            warnings.append("已根据长宽比/平面尺寸在凸直角位置生成角撑；凹角、坡道和出土口位置不自动跨越布撑。")
            lines, snapped_count = _snap_corner_diagonals_to_main_nodes(lines)
            if snapped_count:
                warnings.append(f"已将 {snapped_count} 个角撑端部吸附至相邻主对撑节点，避免近节点伪交叉导致角撑被删除。")
    lines, crossing_warnings = _remove_crossing_lines(lines)
    warnings.extend(crossing_warnings)
    _attach_faces(lines, excavation)
    warnings.extend(_apply_support_wall_clearance(lines, excavation, config))
    return lines, warnings


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
                topology_family=line.topology_family if line.topology_family in {"direct_grid", "hybrid_diagonal", "bidirectional_grid"} else "direct_grid",
                force_distribution_note="V1.6 支撑轴力按围檩连续梁节点反力分配；墙面 tributary width 作为参考宽度保留。",
                section_type="rc_rectangular",
                section=SectionDefinition(
                    width=1.8 if line.role == "secondary_strut" else 1.6,
                    height=1.8 if line.role == "secondary_strut" else 1.6,
                    name="1800x1800 RC" if line.role == "secondary_strut" else "1600x1600 RC",
                ),
                material=MaterialDefinition(name="Concrete", grade="C40"),
                reinforcement=_support_reinforcement(level_idx, "rc_rectangular"),
            )
            supports.append(support)
    _assign_tributary_widths(supports, excavation)
    return supports, warnings


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
    clearance_warnings = _apply_support_wall_clearance(generated_lines, excavation, config)
    warnings.extend(clearance_warnings)
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
                    topology_family="hybrid_diagonal",
                    force_distribution_note="凹形回墙局部法向对撑；按围檩连续梁节点反力和全局矩阵复核。",
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
    system.warnings = list(dict.fromkeys([*(system.warnings or []), *warnings, f"计算前自动增补 {len(added)} 根凹形回墙局部对撑。"] ))
    system.layout_summary = support_layout_summary(system.supports, system.columns, system.ring_beams, system.warnings, config=config)
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
    point = Point2D(x=round(px, 3), y=round(py, 3))
    if _point_on_segment(point, a, b, tol=1e-4) and _point_on_segment(point, c, d, tol=1e-4):
        return point
    return None


def make_column_elements(excavation, supports: list[SupportElement], max_unbraced_span_m: float = COLUMN_MAX_UNBRACED_SPAN_M) -> list[ColumnElement]:
    if not supports:
        return []
    max_unbraced_span_m = max(6.0, min(30.0, float(max_unbraced_span_m)))
    obstacles = _active_obstacle_polygons(getattr(excavation, "obstacles", []))
    column_points: dict[tuple[int, int], ColumnPlanPoint] = {}
    for support in supports:
        if support.support_role not in {"main_strut", "secondary_strut", "ring_strut"}:
            continue
        # Orthogonal grid struts are vertically supported at every intentional
        # main/secondary crossing. This avoids duplicate nearby columns and
        # creates an explicit shared load-transfer node.
        if support.support_role == "secondary_strut":
            intersections = []
            for main in supports:
                if main.level_index != support.level_index or main.support_role != "main_strut":
                    continue
                point = _segment_intersection_point(support.start, support.end, main.start, main.end)
                if point and _point_avoids_obstacles(point, obstacles):
                    intersections.append((point, main.code))
            if intersections:
                for point, main_code in intersections:
                    key = _column_key(point)
                    if key not in column_points:
                        column_points[key] = ColumnPlanPoint(location=point)
                    column_points[key].support_codes.update({support.code, main_code})
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
            service_area_note="立柱位置由主/次对撑交点及跨长控制点生成，自动避让坡道、出土口、中心岛和保护区。",
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
