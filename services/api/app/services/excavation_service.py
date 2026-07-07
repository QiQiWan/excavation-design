from __future__ import annotations

import math
from dataclasses import dataclass

from fastapi import HTTPException

from app.schemas.domain import ExcavationModel, ExcavationSegment, Point2D, Polyline2D, new_id

EPS = 1e-9


@dataclass(frozen=True)
class OutlineMetrics:
    area: float
    signed_area: float
    perimeter: float


def _unique_polygon_points(polyline: Polyline2D) -> list[Point2D]:
    points = list(polyline.points)
    if len(points) > 1 and abs(points[0].x - points[-1].x) < EPS and abs(points[0].y - points[-1].y) < EPS:
        points = points[:-1]
    return points


def close_polyline(polyline: Polyline2D) -> Polyline2D:
    points = _unique_polygon_points(polyline)
    return Polyline2D(points=points, closed=True)


def polygon_metrics(polyline: Polyline2D) -> OutlineMetrics:
    points = _unique_polygon_points(polyline)
    if len(points) < 3:
        return OutlineMetrics(area=0.0, signed_area=0.0, perimeter=0.0)
    twice_area = 0.0
    perimeter = 0.0
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        twice_area += a.x * b.y - b.x * a.y
        perimeter += math.hypot(b.x - a.x, b.y - a.y)
    signed_area = twice_area / 2.0
    return OutlineMetrics(area=abs(signed_area), signed_area=signed_area, perimeter=perimeter)


def _orientation(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y)


def _on_segment(a: Point2D, b: Point2D, c: Point2D) -> bool:
    return min(a.x, c.x) - EPS <= b.x <= max(a.x, c.x) + EPS and min(a.y, c.y) - EPS <= b.y <= max(a.y, c.y) + EPS


def _segments_intersect(p1: Point2D, q1: Point2D, p2: Point2D, q2: Point2D) -> bool:
    def cross(a: Point2D, b: Point2D, c: Point2D) -> float:
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

    o1 = cross(p1, q1, p2)
    o2 = cross(p1, q1, q2)
    o3 = cross(p2, q2, p1)
    o4 = cross(p2, q2, q1)

    if o1 * o2 < -EPS and o3 * o4 < -EPS:
        return True
    if abs(o1) <= EPS and _on_segment(p1, p2, q1):
        return True
    if abs(o2) <= EPS and _on_segment(p1, q2, q1):
        return True
    if abs(o3) <= EPS and _on_segment(p2, p1, q2):
        return True
    if abs(o4) <= EPS and _on_segment(p2, q1, q2):
        return True
    return False


def is_self_intersecting(polyline: Polyline2D) -> bool:
    points = _unique_polygon_points(polyline)
    n = len(points)
    if n < 4:
        return False
    for i in range(n):
        a1 = points[i]
        a2 = points[(i + 1) % n]
        for j in range(i + 1, n):
            if abs(i - j) <= 1 or {i, j} == {0, n - 1}:
                continue
            b1 = points[j]
            b2 = points[(j + 1) % n]
            if _segments_intersect(a1, a2, b1, b2):
                return True
    return False


def validate_outline(polyline: Polyline2D, top_elevation: float, bottom_elevation: float, minimum_segment_length: float = 0.5) -> list[str]:
    errors: list[str] = []
    points = _unique_polygon_points(polyline)
    if len(points) < 3:
        errors.append("基坑轮廓点数量不能少于 3 个。")
    if not polyline.closed:
        errors.append("基坑轮廓必须闭合。")
    if bottom_elevation >= top_elevation:
        errors.append("坑底标高必须低于坑顶标高。")
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)] if points else a
        if math.hypot(b.x - a.x, b.y - a.y) < minimum_segment_length:
            errors.append(f"边段 {i + 1} 长度小于最小阈值 {minimum_segment_length}m。")
    if is_self_intersecting(polyline):
        errors.append("基坑轮廓不能自交。")
    if polygon_metrics(polyline).area <= EPS:
        errors.append("基坑轮廓面积必须大于 0。")
    return errors


def generate_excavation_segments(outline: Polyline2D) -> list[ExcavationSegment]:
    points = _unique_polygon_points(outline)
    metrics = polygon_metrics(outline)
    if len(points) < 3 or metrics.area <= EPS:
        raise HTTPException(status_code=422, detail="Invalid excavation outline")
    is_ccw = metrics.signed_area > 0
    segments: list[ExcavationSegment] = []
    chainage = 0.0
    for idx, start in enumerate(points):
        end = points[(idx + 1) % len(points)]
        dx = end.x - start.x
        dy = end.y - start.y
        length = math.hypot(dx, dy)
        if length <= EPS:
            continue
        if is_ccw:
            normal = Point2D(x=dy / length, y=-dx / length)
        else:
            normal = Point2D(x=-dy / length, y=dx / length)
        midpoint = Point2D(x=(start.x + end.x) / 2.0, y=(start.y + end.y) / 2.0)
        segments.append(
            ExcavationSegment(
                id=f"S{idx + 1}",
                name=f"S{idx + 1}",
                start=start,
                end=end,
                length=round(length, 6),
                outward_normal=normal,
                midpoint=midpoint,
                chainage=round(chainage, 6),
            )
        )
        chainage += length
    return segments


def make_excavation_model(name: str, outline: Polyline2D, top_elevation: float, bottom_elevation: float, minimum_segment_length: float = 0.5) -> ExcavationModel:
    closed = close_polyline(outline)
    errors = validate_outline(closed, top_elevation, bottom_elevation, minimum_segment_length)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    metrics = polygon_metrics(closed)
    return ExcavationModel(
        id=new_id("exc"),
        name=name or "Main excavation",
        outline=closed,
        top_elevation=top_elevation,
        bottom_elevation=bottom_elevation,
        depth=round(top_elevation - bottom_elevation, 6),
        segments=generate_excavation_segments(closed),
        area=round(metrics.area, 6),
        perimeter=round(metrics.perimeter, 6),
        warnings=[],
    )


def _geological_xy_bounds(geological_model) -> tuple[float, float, float, float] | None:
    if not geological_model:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for surface in getattr(geological_model, "surfaces", []) or []:
        grid = getattr(surface, "grid", None)
        if grid:
            xs.extend([float(x) for x in getattr(grid, "x_values", []) or []])
            ys.extend([float(y) for y in getattr(grid, "y_values", []) or []])
    mesh = getattr(geological_model, "vtu_mesh", None) or {}
    if isinstance(mesh, dict):
        for pt in mesh.get("points") or []:
            if len(pt) >= 2:
                xs.append(float(pt[0])); ys.append(float(pt[1]))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def center_excavation_on_geology(excavation: ExcavationModel, geological_model, enabled: bool = True) -> ExcavationModel:
    """Center an unplaced excavation outline on the geological model XY bounds.

    This implements the product rule: if the user has not explicitly locked the
    pit location, the drawn pit outline is treated as a local shape and is moved
    to the geological model center before wall/support generation.
    """
    if not enabled or getattr(excavation, "explicit_placement", False):
        return excavation
    bounds = _geological_xy_bounds(geological_model)
    if not bounds:
        return excavation
    points = _unique_polygon_points(excavation.outline)
    if not points:
        return excavation
    ex = [p.x for p in points]; ey = [p.y for p in points]
    pit_cx = (min(ex) + max(ex)) / 2.0
    pit_cy = (min(ey) + max(ey)) / 2.0
    gx0, gy0, gx1, gy1 = bounds
    geo_cx = (gx0 + gx1) / 2.0
    geo_cy = (gy0 + gy1) / 2.0
    dx = geo_cx - pit_cx
    dy = geo_cy - pit_cy
    if abs(dx) <= 1e-8 and abs(dy) <= 1e-8:
        excavation.centered_on_geology = True
        excavation.placement_note = "基坑轮廓已与地质模型中心对齐。"
        return excavation
    shifted = [Point2D(x=round(p.x + dx, 6), y=round(p.y + dy, 6)) for p in points]
    excavation.outline = Polyline2D(points=shifted, closed=True)
    excavation.segments = generate_excavation_segments(excavation.outline)
    metrics = polygon_metrics(excavation.outline)
    excavation.area = round(metrics.area, 6)
    excavation.perimeter = round(metrics.perimeter, 6)
    excavation.centered_on_geology = True
    excavation.placement_note = f"未锁定绝对坐标，已将基坑轮廓中心平移到地质模型中心；dx={dx:.3f}m, dy={dy:.3f}m。"
    if excavation.placement_note not in excavation.warnings:
        excavation.warnings.append(excavation.placement_note)
    return excavation
