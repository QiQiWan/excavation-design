from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from app.schemas.domain import Point2D, Project


GEOMETRY_TOLERANCE_M = 0.02
PANEL_AXIS_TOLERANCE_M = 0.10


@dataclass(frozen=True)
class WallPathResolution:
    points: list[Point2D]
    source: str
    repaired: bool
    reason: str | None = None


def distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))


def clean_polyline(points: Iterable[Any], tolerance: float = 1.0e-8) -> list[Point2D]:
    result: list[Point2D] = []
    for raw in points:
        try:
            point = Point2D(x=float(raw.x), y=float(raw.y))
        except AttributeError:
            point = Point2D(x=float(raw["x"]), y=float(raw["y"]))
        if not (math.isfinite(point.x) and math.isfinite(point.y)):
            continue
        if not result or distance(result[-1], point) > tolerance:
            result.append(point)
    if len(result) > 2 and distance(result[0], result[-1]) <= tolerance:
        result = result[:-1]
    return result


def polyline_length(points: Iterable[Any]) -> float:
    rows = clean_polyline(points)
    return sum(distance(a, b) for a, b in zip(rows[:-1], rows[1:]))


def cumulative_lengths(points: Iterable[Any]) -> tuple[list[Point2D], list[float]]:
    rows = clean_polyline(points)
    cumulative = [0.0]
    for a, b in zip(rows[:-1], rows[1:]):
        cumulative.append(cumulative[-1] + distance(a, b))
    return rows, cumulative


def point_tangent_at_chainage(points: Iterable[Any], chainage: float) -> tuple[Point2D, tuple[float, float], int]:
    rows, cumulative = cumulative_lengths(points)
    if len(rows) < 2:
        point = rows[0] if rows else Point2D(x=0.0, y=0.0)
        return point, (1.0, 0.0), 0
    total = cumulative[-1]
    target = min(max(float(chainage), 0.0), total)
    index = len(rows) - 2
    for i in range(len(rows) - 1):
        if target <= cumulative[i + 1] + 1.0e-9:
            index = i
            break
    a, b = rows[index], rows[index + 1]
    seg_len = max(distance(a, b), 1.0e-12)
    local = min(max((target - cumulative[index]) / seg_len, 0.0), 1.0)
    point = Point2D(x=a.x + (b.x - a.x) * local, y=a.y + (b.y - a.y) * local)
    return point, ((b.x - a.x) / seg_len, (b.y - a.y) / seg_len), index


def subpath_between_chainages(points: Iterable[Any], start_m: float, end_m: float) -> list[Point2D]:
    rows, cumulative = cumulative_lengths(points)
    if len(rows) < 2:
        return rows
    total = cumulative[-1]
    lo = min(max(float(start_m), 0.0), total)
    hi = min(max(float(end_m), lo), total)
    start, _, _ = point_tangent_at_chainage(rows, lo)
    end, _, _ = point_tangent_at_chainage(rows, hi)
    result = [start]
    for point, chainage in zip(rows[1:-1], cumulative[1:-1]):
        if lo + 1.0e-9 < chainage < hi - 1.0e-9:
            result.append(point)
    if not result or distance(result[-1], end) > 1.0e-8:
        result.append(end)
    return clean_polyline(result)


def project_point_to_polyline(point: Point2D, points: Iterable[Any]) -> tuple[float, float, Point2D]:
    rows, cumulative = cumulative_lengths(points)
    if len(rows) < 2:
        ref = rows[0] if rows else Point2D(x=0.0, y=0.0)
        return 0.0, distance(point, ref), ref
    best = (0.0, float("inf"), rows[0])
    for index, (a, b) in enumerate(zip(rows[:-1], rows[1:])):
        dx, dy = b.x - a.x, b.y - a.y
        denom = dx * dx + dy * dy
        t = 0.0 if denom <= 1.0e-15 else ((point.x - a.x) * dx + (point.y - a.y) * dy) / denom
        t = min(max(t, 0.0), 1.0)
        projected = Point2D(x=a.x + dx * t, y=a.y + dy * t)
        deviation = distance(point, projected)
        chainage = cumulative[index] + distance(a, projected)
        if deviation < best[1]:
            best = (chainage, deviation, projected)
    return best


def offset_polyline(points: Iterable[Any], offset_m: float, miter_limit: float = 3.0) -> list[Point2D]:
    rows = clean_polyline(points)
    if len(rows) < 2 or abs(offset_m) <= 1.0e-12:
        return rows
    tangents: list[tuple[float, float]] = []
    normals: list[tuple[float, float]] = []
    for a, b in zip(rows[:-1], rows[1:]):
        length = max(distance(a, b), 1.0e-12)
        tx, ty = (b.x - a.x) / length, (b.y - a.y) / length
        tangents.append((tx, ty))
        normals.append((-ty, tx))
    result: list[Point2D] = []
    for index, point in enumerate(rows):
        if index == 0:
            nx, ny = normals[0]
            scale = offset_m
        elif index == len(rows) - 1:
            nx, ny = normals[-1]
            scale = offset_m
        else:
            n0, n1 = normals[index - 1], normals[index]
            mx, my = n0[0] + n1[0], n0[1] + n1[1]
            mag = math.hypot(mx, my)
            if mag <= 1.0e-9:
                nx, ny = n1
                scale = offset_m
            else:
                nx, ny = mx / mag, my / mag
                denom = nx * n1[0] + ny * n1[1]
                if abs(denom) <= 1.0e-3:
                    scale = offset_m
                else:
                    scale = max(-abs(offset_m) * miter_limit, min(abs(offset_m) * miter_limit, offset_m / denom))
        result.append(Point2D(x=point.x + nx * scale, y=point.y + ny * scale))
    return result


def _orient_like_reference(points: list[Point2D], reference: list[Point2D]) -> list[Point2D]:
    if len(points) < 2 or len(reference) < 2:
        return points
    direct = distance(points[0], reference[0]) + distance(points[-1], reference[-1])
    reversed_score = distance(points[-1], reference[0]) + distance(points[0], reference[-1])
    return list(reversed(points)) if reversed_score + 1.0e-8 < direct else points


def _connected_segment_path(segments: list[Any], reference: list[Point2D]) -> list[Point2D]:
    if not segments:
        return []
    remaining = list(segments)
    first = remaining.pop(0)
    path = [Point2D(x=float(first.start.x), y=float(first.start.y)), Point2D(x=float(first.end.x), y=float(first.end.y))]
    while remaining:
        best_index = -1
        best_reverse = False
        best_distance = float("inf")
        for index, segment in enumerate(remaining):
            start = Point2D(x=float(segment.start.x), y=float(segment.start.y))
            end = Point2D(x=float(segment.end.x), y=float(segment.end.y))
            for reverse, candidate in ((False, start), (True, end)):
                d = distance(path[-1], candidate)
                if d < best_distance:
                    best_distance, best_index, best_reverse = d, index, reverse
        segment = remaining.pop(best_index)
        start = Point2D(x=float(segment.start.x), y=float(segment.start.y))
        end = Point2D(x=float(segment.end.x), y=float(segment.end.y))
        if best_reverse:
            start, end = end, start
        if distance(path[-1], start) > GEOMETRY_TOLERANCE_M:
            break
        if distance(path[-1], end) > 1.0e-8:
            path.append(end)
    return _orient_like_reference(clean_polyline(path), reference)


def resolve_wall_plan_path(project: Project, wall: Any, wall_index: int | None = None) -> WallPathResolution:
    reference = clean_polyline(list(getattr(getattr(wall, "axis", None), "points", []) or []))
    excavation = project.excavation
    if excavation is not None:
        exact = next((segment for segment in excavation.segments if str(segment.id) == str(getattr(wall, "segment_id", ""))), None)
        if exact is not None:
            points = [Point2D(x=float(exact.start.x), y=float(exact.start.y)), Point2D(x=float(exact.end.x), y=float(exact.end.y))]
            points = _orient_like_reference(points, reference)
            repaired = len(reference) < 2 or max(distance(points[0], reference[0]), distance(points[-1], reference[-1])) > GEOMETRY_TOLERANCE_M
            return WallPathResolution(points=points, source="excavation_segment", repaired=repaired, reason="wall_axis_rebased_to_excavation_segment" if repaired else None)
        ids = [str(item) for item in list(getattr(wall, "face_segment_ids", []) or [])]
        face_segments = [segment for segment in excavation.segments if str(segment.id) in ids]
        if face_segments:
            points = _connected_segment_path(face_segments, reference)
            if len(points) >= 2:
                repaired = len(reference) < 2 or polyline_length(points) > polyline_length(reference) + GEOMETRY_TOLERANCE_M
                return WallPathResolution(points=points, source="excavation_face_segments", repaired=repaired, reason="wall_axis_rebased_to_face_segments" if repaired else None)
    if len(reference) >= 2:
        return WallPathResolution(points=reference, source="wall_axis", repaired=False)
    if excavation is not None:
        outline = clean_polyline(list(excavation.outline.points or []))
        if len(outline) >= 2:
            index = int(wall_index or 0) % len(outline)
            points = [outline[index], outline[(index + 1) % len(outline)]]
            return WallPathResolution(points=points, source="excavation_outline_edge", repaired=True, reason="missing_wall_axis")
    return WallPathResolution(points=reference, source="unresolved", repaired=False, reason="missing_canonical_wall_path")


def _raw_xy(value: Any) -> Point2D | None:
    if not value:
        return None
    try:
        return Point2D(x=float(value.get("x")), y=float(value.get("y")))
    except (AttributeError, TypeError, ValueError):
        return None


def normalize_construction_panels(
    wall: Any,
    path_points: Iterable[Any],
    *,
    target_length_m: float = 6.0,
    minimum_length_m: float = 3.0,
    maximum_length_m: float = 7.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = clean_polyline(path_points)
    total = polyline_length(path)
    if len(path) < 2 or total <= 1.0e-9:
        return [], {"status": "unresolved", "totalLengthM": total, "repairedPanelCount": 0, "maximumStoredDeviationM": None}
    raw_panels = [dict(row) for row in list(getattr(wall, "construction_panels", []) or [])]
    valid_chainages: list[tuple[float, float, dict[str, Any]]] = []
    raw_max_end = 0.0
    for row in raw_panels:
        try:
            c0 = float(row.get("startChainageM") or 0.0)
            c1 = float(row.get("endChainageM") if row.get("endChainageM") is not None else c0 + float(row.get("lengthM") or 0.0))
        except (TypeError, ValueError):
            continue
        if c1 > c0 + 1.0e-6:
            raw_max_end = max(raw_max_end, c1)
            valid_chainages.append((c0, c1, row))
    schedule_rebuilt = False
    if valid_chainages:
        valid_chainages.sort(key=lambda item: (item[0], item[1]))
        scale = total / raw_max_end if raw_max_end > 1.0e-9 and abs(raw_max_end - total) > max(0.05, total * 0.005) else 1.0
        intervals: list[tuple[float, float, dict[str, Any]]] = []
        cursor = 0.0
        for c0, c1, row in valid_chainages:
            lo = min(max(c0 * scale, 0.0), total)
            hi = min(max(c1 * scale, lo), total)
            if abs(lo - cursor) > max(0.10, total * 0.002):
                schedule_rebuilt = True
                break
            lo = cursor
            if hi - lo <= 1.0e-6:
                schedule_rebuilt = True
                break
            intervals.append((lo, hi, row))
            cursor = hi
        if not schedule_rebuilt and intervals:
            if abs(cursor - total) <= max(0.10, total * 0.002):
                lo, _, row = intervals[-1]
                intervals[-1] = (lo, total, row)
            else:
                schedule_rebuilt = True
    else:
        schedule_rebuilt = True
        intervals = []
    if schedule_rebuilt:
        target = max(minimum_length_m, min(maximum_length_m, float(target_length_m)))
        count = max(1, int(math.ceil(total / target)))
        while count > 1 and total / count < minimum_length_m:
            count -= 1
        while total / count > maximum_length_m and count < 200:
            count += 1
        intervals = []
        for index in range(count):
            row = raw_panels[index] if index < len(raw_panels) else {}
            intervals.append((total * index / count, total * (index + 1) / count, row))
    normalized: list[dict[str, Any]] = []
    repaired_count = 0
    max_deviation = 0.0
    for index, (c0, c1, raw) in enumerate(intervals, start=1):
        plan_path = subpath_between_chainages(path, c0, c1)
        if len(plan_path) < 2:
            continue
        stored_start = _raw_xy(raw.get("start"))
        stored_end = _raw_xy(raw.get("end"))
        deviations = []
        if stored_start is not None:
            deviations.append(project_point_to_polyline(stored_start, path)[1])
        if stored_end is not None:
            deviations.append(project_point_to_polyline(stored_end, path)[1])
        deviation = max(deviations, default=0.0)
        max_deviation = max(max_deviation, deviation)
        geometry_repaired = schedule_rebuilt or deviation > PANEL_AXIS_TOLERANCE_M
        if geometry_repaired:
            repaired_count += 1
        row = dict(raw)
        row.update({
            "panelIndex": int(raw.get("panelIndex") or index),
            "panelCode": str(raw.get("panelCode") or f"{getattr(wall, 'panel_code', 'DW')}-P{index:02d}"),
            "startChainageM": round(c0, 4),
            "endChainageM": round(c1, 4),
            "lengthM": round(c1 - c0, 4),
            "start": {"x": round(plan_path[0].x, 4), "y": round(plan_path[0].y, 4)},
            "end": {"x": round(plan_path[-1].x, 4), "y": round(plan_path[-1].y, 4)},
            "planPath": [{"x": round(point.x, 4), "y": round(point.y, 4)} for point in plan_path],
            "geometrySource": "canonical_wall_path_chainage",
            "geometryStatus": "repaired" if geometry_repaired else "matched",
            "storedGeometryDeviationM": round(deviation, 4),
            "cageCount": int(raw.get("cageCount") or 1),
            "jointType": str(raw.get("jointType") or "project_specific"),
            "liftingReviewRequired": bool(raw.get("liftingReviewRequired", True)),
        })
        normalized.append(row)
    return normalized, {
        "status": "rebuilt" if schedule_rebuilt else ("repaired" if repaired_count else "matched"),
        "totalLengthM": round(total, 4),
        "panelCount": len(normalized),
        "repairedPanelCount": repaired_count,
        "maximumStoredDeviationM": round(max_deviation, 4),
    }
