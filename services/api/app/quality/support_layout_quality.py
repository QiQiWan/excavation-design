from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from app.schemas.domain import Project, QualityGateIssue, SupportElement, SupportLayoutQualitySummary, Point2D, Polyline2D
from app.services.support_layout import (
    _local_coordinates,
    _plan_axes,
    _point_in_polygon,
    plan_shape_diagnostics,
    wale_support_bay_audit,
)

PRACTICAL_MIN_SPACING_M = 3.0
PRACTICAL_MAX_SPACING_M = 6.0
WARNING_MAX_SPAN_M = 30.0
FAIL_MAX_SPAN_M = 45.0
COLUMN_SPAN_TRIGGER_M = 18.0
INTERSECTION_TOL = 1e-7


def _mid(s: SupportElement) -> tuple[float, float]:
    return ((s.start.x + s.end.x) / 2.0, (s.start.y + s.end.y) / 2.0)


def _span(s: SupportElement) -> float:
    return math.hypot(s.end.x - s.start.x, s.end.y - s.start.y)


def _bbox(points: Iterable[Point2D]) -> tuple[float, float, float, float] | None:
    pts = list(points)
    if not pts:
        return None
    return min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts)


def _main_support_station(project: Project, support: SupportElement) -> float:
    """Return the support midpoint station in the excavation principal-axis frame."""
    if not project.excavation or len(project.excavation.outline.points) < 2:
        return _mid(support)[0]
    axes = _plan_axes(list(project.excavation.outline.points))
    midpoint = Point2D(x=_mid(support)[0], y=_mid(support)[1])
    return float(_local_coordinates(midpoint, axes).x)


def _pt_eq(a: Point2D, b: Point2D, tol: float = 1e-6) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) <= tol


def _shares_endpoint(a: SupportElement, b: SupportElement) -> bool:
    return any(_pt_eq(p, q) for p in (a.start, a.end) for q in (b.start, b.end))



def _distance_to_segment(point: Point2D, a: Point2D, b: Point2D) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    denom = dx * dx + dy * dy
    if denom <= 1e-16:
        return math.hypot(point.x - a.x, point.y - a.y)
    t = max(0.0, min(1.0, ((point.x - a.x) * dx + (point.y - a.y) * dy) / denom))
    x, y = a.x + t * dx, a.y + t * dy
    return math.hypot(point.x - x, point.y - y)


def _endpoint_on_other_member(a: SupportElement, b: SupportElement, tol: float = 1.0e-3) -> bool:
    return (
        any(_distance_to_segment(p, b.start, b.end) <= tol for p in (a.start, a.end))
        or any(_distance_to_segment(p, a.start, a.end) <= tol for p in (b.start, b.end))
    )

def _orientation(p: Point2D, q: Point2D, r: Point2D) -> float:
    return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)


def _on_segment(p: Point2D, q: Point2D, r: Point2D, tol: float = 1e-8) -> bool:
    return min(p.x, r.x) - tol <= q.x <= max(p.x, r.x) + tol and min(p.y, r.y) - tol <= q.y <= max(p.y, r.y) + tol


def _segment_intersection_point(a: Point2D, b: Point2D, c: Point2D, d: Point2D) -> tuple[bool, Point2D | None, bool]:
    """Return (intersects, point, is_collinear_or_touching)."""
    o1, o2, o3, o4 = _orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b)
    touching = False
    if abs(o1) < INTERSECTION_TOL and _on_segment(a, c, b):
        return True, c, True
    if abs(o2) < INTERSECTION_TOL and _on_segment(a, d, b):
        return True, d, True
    if abs(o3) < INTERSECTION_TOL and _on_segment(c, a, d):
        return True, a, True
    if abs(o4) < INTERSECTION_TOL and _on_segment(c, b, d):
        return True, b, True
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        x1, y1, x2, y2 = a.x, a.y, b.x, b.y
        x3, y3, x4, y4 = c.x, c.y, d.x, d.y
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < INTERSECTION_TOL:
            return True, None, True
        px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
        py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
        return True, Point2D(x=round(px, 4), y=round(py, 4)), touching
    return False, None, False


def _supports_cross(a: SupportElement, b: SupportElement) -> tuple[bool, Point2D | None]:
    if a.level_index != b.level_index:
        return False, None
    if _shares_endpoint(a, b) or _endpoint_on_other_member(a, b):
        return False, None
    ok, point, touching = _segment_intersection_point(a.start, a.end, b.start, b.end)
    if not ok or touching:
        return False, None
    return True, point


def _support_intersects_outline(s: SupportElement, outline: Polyline2D) -> bool:
    pts = outline.points
    if not outline.closed or len(pts) < 3:
        return False
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        ok, pt, touching = _segment_intersection_point(s.start, s.end, a, b)
        # Intersections at support endpoints on wall are valid; midspan exits are not.
        if ok and pt and not (_pt_eq(pt, s.start, 1e-4) or _pt_eq(pt, s.end, 1e-4)):
            return True
    return False




def _support_outside_excavation(s: SupportElement, outline: Polyline2D) -> tuple[bool, list[dict]]:
    """Detect a support centreline that leaves a concave/general excavation polygon.

    End points are allowed on the wall boundary.  Interior samples are deliberately
    denser than the layout generator samples so this function acts as a hard,
    independent containment gate before analysis and drawing export.
    """
    points = list(outline.points)
    if not outline.closed or len(points) < 3:
        return False, []
    outside: list[dict] = []
    for index in range(1, 20):
        t = index / 20.0
        point = Point2D(
            x=s.start.x + (s.end.x - s.start.x) * t,
            y=s.start.y + (s.end.y - s.start.y) * t,
        )
        if not _point_in_polygon(point, points):
            outside.append({"t": round(t, 3), "x": round(point.x, 4), "y": round(point.y, 4)})
    return bool(outside), outside


def _support_intersects_obstacle(s: SupportElement, outline: Polyline2D | None) -> bool:
    if not outline or len(outline.points) < 3:
        return False
    pts = outline.points
    for i in range(len(pts)):
        ok, _, touching = _segment_intersection_point(s.start, s.end, pts[i], pts[(i + 1) % len(pts)])
        if ok:
            return True
    return False


def _support_geometry(s: SupportElement) -> dict:
    return {"kind": "segment", "start": {"x": s.start.x, "y": s.start.y}, "end": {"x": s.end.x, "y": s.end.y}, "levelIndex": s.level_index, "supportCode": s.code}


def _point_segment_distance(point: Point2D, a: Point2D, b: Point2D) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return math.hypot(point.x - a.x, point.y - a.y)
    t = max(0.0, min(1.0, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length2))
    px, py = a.x + t * dx, a.y + t * dy
    return math.hypot(point.x - px, point.y - py)


def _support_wall_clearances(project: Project, support: SupportElement) -> list[float]:
    if not project.excavation:
        return []
    by_code = {str(seg.name): seg for seg in project.excavation.segments}
    rows: list[float] = []
    for point, face_code, stored in (
        (support.start, support.start_face_code, support.start_wall_clearance_m),
        (support.end, support.end_face_code, support.end_wall_clearance_m),
    ):
        if stored is not None:
            rows.append(float(stored))
            continue
        segment = by_code.get(str(face_code or ""))
        if segment:
            rows.append(_point_segment_distance(point, segment.start, segment.end))
    return rows


def _issue(category: str, severity: str, message: str, object_id: str | None = None, object_type: str | None = None, recommendation: str | None = None, *, geometry: dict | None = None, related: list[str] | None = None, hint: str | None = None) -> QualityGateIssue:
    return QualityGateIssue(category=category, severity=severity, object_id=object_id, object_type=object_type, message=message, recommendation=recommendation, highlight_geometry=geometry or {}, related_object_ids=related or [], display_hint=hint)


def _effective_unbraced_span(project: Project, support: SupportElement) -> float:
    length = float(support.span_length or _span(support))
    ret = project.retaining_system
    if not ret or not ret.columns or length <= 1e-9:
        return length
    dx = support.end.x - support.start.x
    dy = support.end.y - support.start.y
    stations = [0.0, length]
    for column in ret.columns:
        if support.code not in getattr(column, "support_codes", []):
            continue
        px = column.location.x - support.start.x
        py = column.location.y - support.start.y
        station = (px * dx + py * dy) / length
        if -0.25 <= station <= length + 0.25:
            stations.append(max(0.0, min(length, station)))
    stations = sorted(set(round(value, 3) for value in stations))
    return max((b - a for a, b in zip(stations[:-1], stations[1:])), default=length)


def _allowed_ring_crossing(project: Project, a: SupportElement, b: SupportElement, point: Point2D | None) -> bool:
    """Only an explicit ring/radial system may retain a proper plan crossing."""
    if point is None or {a.support_role, b.support_role} != {"ring_strut"}:
        return False
    return bool(project.retaining_system and project.retaining_system.ring_beams)


def _point_key(level_index: int, point: Point2D, tolerance: float = 0.02) -> tuple[int, int, int]:
    """Return a stable key for topology nodes that are geometrically coincident."""
    scale = 1.0 / max(tolerance, 1.0e-6)
    return int(level_index), int(round(float(point.x) * scale)), int(round(float(point.y) * scale))


def _point_is_member_endpoint(support: SupportElement, point: Point2D, tolerance: float = 0.02) -> bool:
    return _pt_eq(support.start, point, tolerance) or _pt_eq(support.end, point, tolerance)


def _point_is_wall_endpoint(support: SupportElement, point: Point2D, tolerance: float = 0.02) -> bool:
    return (
        _pt_eq(support.start, point, tolerance) and bool(support.start_face_code)
    ) or (
        _pt_eq(support.end, point, tolerance) and bool(support.end_face_code)
    )


def _topology_intersection_metrics(project: Project, supports: list[SupportElement]) -> dict:
    """Measure plan cleanliness independently from structural validity.

    ``supportCrossingCount`` remains the hard safety/geometry gate for proper
    mid-span crossings.  This companion audit also counts valid internal T/Y/X
    nodes because a scheme with many legal junctions can still be visually
    congested, difficult to fabricate, and awkward to sequence on site.
    """
    same_level_nodes: dict[tuple[int, int, int], dict] = {}
    illegal_keys: set[tuple[int, int, int]] = set()
    projected_cross_level_pairs = 0

    for index, first in enumerate(supports):
        for second in supports[index + 1:]:
            ok, point, touching = _segment_intersection_point(first.start, first.end, second.start, second.end)
            if not ok or point is None:
                continue
            if int(first.level_index) != int(second.level_index):
                # Cross-level projection overlap is not a structural crossing,
                # but it increases drawing and installation coordination burden.
                if not touching:
                    projected_cross_level_pairs += 1
                continue
            key = _point_key(int(first.level_index), point)
            proper_crossing, _ = _supports_cross(first, second)
            if proper_crossing and not _allowed_ring_crossing(project, first, second, point):
                illegal_keys.add(key)
            # Wall-end convergence belongs to the wale/support connection and is
            # not counted as an internal plan intersection.
            if _point_is_wall_endpoint(first, point) or _point_is_wall_endpoint(second, point):
                continue
            node = same_level_nodes.setdefault(
                key,
                {
                    "levelIndex": int(first.level_index),
                    "point": {"x": round(float(point.x), 4), "y": round(float(point.y), 4)},
                    "members": {},
                },
            )
            node["members"][first.code] = first
            node["members"][second.code] = second

    junction_rows: list[dict] = []
    internal_junction_count = 0
    high_degree_junction_count = 0
    max_branch_degree = 0
    for key, node in same_level_nodes.items():
        if key in illegal_keys:
            continue
        members: dict[str, SupportElement] = node["members"]
        point = Point2D(**node["point"])
        branch_degree = sum(1 if _point_is_member_endpoint(member, point) else 2 for member in members.values())
        # Two members meeting end-to-end form a simple continuous joint.  T/Y/X
        # nodes start at three geometric branches and are the main cleanliness
        # concern addressed by the optimizer.
        if branch_degree < 3:
            continue
        internal_junction_count += 1
        high_degree = branch_degree >= 4
        high_degree_junction_count += int(high_degree)
        max_branch_degree = max(max_branch_degree, branch_degree)
        junction_rows.append(
            {
                "levelIndex": node["levelIndex"],
                "point": node["point"],
                "supportCodes": sorted(members),
                "memberCount": len(members),
                "branchDegree": branch_degree,
                "highDegree": high_degree,
            }
        )

    # Illegal crossings dominate the index.  Legal internal nodes and projected
    # cross-level overlaps then distinguish otherwise feasible alternatives.
    intersection_complexity = (
        100.0 * len(illegal_keys)
        + 1.0 * internal_junction_count
        + 2.0 * high_degree_junction_count
        + 0.20 * projected_cross_level_pairs
    )
    return {
        "sameLevelPlanIntersectionPointCount": len(illegal_keys) + internal_junction_count,
        "internalJunctionCount": internal_junction_count,
        "highDegreeJunctionCount": high_degree_junction_count,
        "maxJunctionBranchDegree": max_branch_degree,
        "projectedCrossLevelIntersectionCount": projected_cross_level_pairs,
        "planIntersectionComplexity": round(intersection_complexity, 4),
        "junctionPoints": junction_rows,
    }


def evaluate_support_layout_quality(project: Project) -> SupportLayoutQualitySummary:
    ret = project.retaining_system
    if not ret or not ret.supports:
        return SupportLayoutQualitySummary(score=0, status="manual_review", summary="尚未生成水平支撑体系，无法评价支撑布置合理性。", issues=[_issue("support_layout", "manual_review", "缺少支撑体系。", recommendation="先执行一键生成围护体系。")])

    supports = ret.supports
    main = [s for s in supports if s.support_role == "main_strut"]
    secondary = [s for s in supports if s.support_role == "secondary_strut"]
    corners = [s for s in supports if s.support_role == "corner_diagonal"]
    shape_diagnostics = plan_shape_diagnostics(list(project.excavation.outline.points)) if project.excavation else {}
    by_level: dict[int, list[SupportElement]] = defaultdict(list)
    for s in main:
        by_level[s.level_index].append(s)

    issues: list[QualityGateIssue] = []
    highlights: list[dict] = []
    crossing_pairs: list[dict] = []
    bay_spacings: list[float] = []
    main_counts: dict[str, int] = {}
    max_span = 0.0
    max_unbraced_span = 0.0
    supported_grid_nodes = 0
    support_outside_count = 0

    for level, items in sorted(by_level.items()):
        items_sorted = sorted(items, key=lambda ss: _main_support_station(project, ss))
        main_counts[str(level)] = len(items_sorted)
        for s in items_sorted:
            if s.bay_spacing is not None:
                bay_spacings.append(float(s.bay_spacing))
                if s.bay_spacing > PRACTICAL_MAX_SPACING_M:
                    issues.append(_issue("support_spacing", "fail", f"第 {level} 道主对撑分仓间距 {s.bay_spacing:.2f}m 超过 {PRACTICAL_MAX_SPACING_M:.1f}m，平面布置过稀。", s.id, "SupportElement", "将目标主支撑分仓间距调至 3-6m，并重新生成支撑。", geometry=_support_geometry(s), hint="spacing_over_limit"))
                elif s.bay_spacing < PRACTICAL_MIN_SPACING_M:
                    issues.append(_issue("support_spacing", "warning", f"第 {level} 道主对撑分仓间距 {s.bay_spacing:.2f}m 小于 {PRACTICAL_MIN_SPACING_M:.1f}m，可能过密或影响施工。", s.id, "SupportElement", "复核支撑施工空间和出土路线。", geometry=_support_geometry(s), hint="spacing_too_dense"))
        if len(items_sorted) <= 1 and project.excavation and project.excavation.depth >= 8.0:
            issues.append(_issue("support_spacing", "warning", f"第 {level} 道只有 {len(items_sorted)} 根主对撑，深基坑布置可能偏稀。", object_type="SupportLevel", recommendation="增加主对撑或采用环撑/角撑组合。"))

    target_clearance = float(getattr(project.design_settings, "support_wall_clearance_m", 1.0) or 1.0)
    max_direct_span = float(getattr(project.design_settings, "max_direct_strut_span_m", 24.0) or 24.0)
    clearance_values: list[float] = []
    excessive_direct_count = 0
    geometric_long_direct_count = 0
    column_resolved_long_direct_count = 0
    for s in supports:
        if project.excavation:
            outside, outside_samples = _support_outside_excavation(s, project.excavation.outline)
            crosses_boundary = _support_intersects_outline(s, project.excavation.outline)
            if outside or crosses_boundary:
                support_outside_count += 1
                issues.append(_issue(
                    "support_outside_excavation",
                    "fail",
                    f"支撑 {s.code} 的中心线穿出基坑轮廓，不能作为有效坑内传力构件。",
                    s.id,
                    "SupportElement",
                    "按基坑局部主轴重新求交，并对凹角、阶梯段和回折边执行分区布置；不得以包围盒端点代替真实墙面交点。",
                    geometry={**_support_geometry(s), "outsideSamples": outside_samples},
                    hint="support_outside_excavation",
                ))
        sp = float(s.span_length or _span(s))
        unbraced = _effective_unbraced_span(project, s)
        clearances = _support_wall_clearances(project, s)
        clearance_values.extend(clearances)
        for clearance in clearances:
            if clearance < max(0.20, target_clearance * 0.65):
                issues.append(_issue("support_wall_clearance", "fail", f"支撑 {s.code} 中心线距围护墙仅 {clearance:.2f}m，小于目标净距 {target_clearance:.2f}m。", s.id, "SupportElement", "将支撑中心线向坑内偏移，并通过围檩刚臂节点连接墙体。", geometry=_support_geometry(s), hint="support_wall_overlap"))
            elif clearance < target_clearance * 0.90:
                issues.append(_issue("support_wall_clearance", "warning", f"支撑 {s.code} 中心线距围护墙 {clearance:.2f}m，接近目标净距下限。", s.id, "SupportElement", "复核围檩宽度、承压板和安装空间。", geometry=_support_geometry(s), hint="support_wall_clearance"))
        if s.support_role == "corner_diagonal":
            wall_to_wall = bool(
                s.start_face_code and s.end_face_code
                and s.start_face_code != s.end_face_code
                and s.start_wall_connection is not None
                and s.end_wall_connection is not None
            )
            if not wall_to_wall:
                issues.append(_issue(
                    "corner_brace_bearing",
                    "fail",
                    f"角撑 {s.code} 未形成相邻围檩/围护墙之间的直接墙—墙支承。",
                    s.id,
                    "SupportElement",
                    "重新生成角撑，使两端均落在转角附近的相邻围檩节点；角撑不得终止于另一水平支撑。",
                    geometry=_support_geometry(s),
                    hint="corner_brace_wall_bearing",
                ))
        if s.support_role == "main_strut" and sp > max_direct_span:
            geometric_long_direct_count += 1
            if unbraced > max_direct_span:
                excessive_direct_count += 1
                issues.append(_issue("long_direct_strut", "warning", f"主对撑 {s.code} 总长 {sp:.2f}m，且有效无侧向支承长度 {unbraced:.2f}m 超过控制值 {max_direct_span:.1f}m。", s.id, "SupportElement", "自动增设临时立柱/共享节点，或比较短对撑混合与双向网格方案。", geometry=_support_geometry(s), hint="prefer_diagonal"))
            else:
                column_resolved_long_direct_count += 1
        max_span = max(max_span, sp)
        max_unbraced_span = max(max_unbraced_span, unbraced)
        if unbraced > FAIL_MAX_SPAN_M:
            issues.append(_issue("support_span", "fail", f"支撑 {s.code} 有效无侧向支承长度 {unbraced:.2f}m 超过 {FAIL_MAX_SPAN_M:.1f}m。", s.id, "SupportElement", "增设临时立柱/网格节点或改变支撑体系。", geometry=_support_geometry(s), hint="span_fail"))
        elif unbraced > WARNING_MAX_SPAN_M:
            issues.append(_issue("support_span", "warning", f"支撑 {s.code} 有效无侧向支承长度 {unbraced:.2f}m 偏大。", s.id, "SupportElement", "复核长细比、挠度、立柱和施工安装。", geometry=_support_geometry(s), hint="span_warning"))

    # Direct wale-support bay audit.  This is a topology/strength precondition:
    # a wale with a 20--30 m unsupported bay cannot be made reliable by section
    # enlargement alone.  The layout optimizer must first provide a clear load path.
    wale_bay_audit = wale_support_bay_audit(
        project.excavation,
        supports,
        target_bay_m=float(getattr(project.design_settings, "max_wale_support_bay_m", 7.5) or 7.5),
        hard_max_bay_m=float(getattr(project.design_settings, "hard_max_wale_support_bay_m", 9.0) or 9.0),
    ) if project.excavation else {"status": "manual_review", "rows": [], "maxBayM": None, "failCount": 0, "warningCount": 0}
    for row in list(wale_bay_audit.get("rows", [])):
        row_status = str(row.get("status", "pass"))
        if row_status not in {"fail", "warning"}:
            continue
        face_code = str(row.get("faceCode", ""))
        level_index = int(row.get("levelIndex", 0) or 0)
        max_bay = float(row.get("maxBayM", 0.0) or 0.0)
        target_bay = float(row.get("targetBayM", 7.5) or 7.5)
        hard_bay = float(row.get("hardMaxBayM", 9.0) or 9.0)
        message = (
            f"第 {level_index} 道围檩墙面 {face_code} 最大直接支点间距 {max_bay:.2f}m "
            f"超过{'硬上限' if row_status == 'fail' else '目标值'} {hard_bay if row_status == 'fail' else target_bay:.2f}m。"
        )
        issues.append(_issue(
            "wale_support_bay",
            row_status,
            message,
            object_id=face_code,
            object_type="WaleSupportBay",
            recommendation="优先增设角部扇形斜撑、局部短对撑或双向网格支点，再进行围檩截面与配筋设计。",
            geometry={"kind": "wall_face", "faceCode": face_code, "levelIndex": level_index, "stationsM": row.get("stationsM", []), "bayLengthsM": row.get("bayLengthsM", [])},
            hint="wale_support_bay",
        ))

    topology_intersections = _topology_intersection_metrics(project, supports)

    # Crossings: any same-level crossing without shared endpoints is a layout hard issue.
    for i, a in enumerate(supports):
        for b in supports[i + 1:]:
            crossed, pt = _supports_cross(a, b)
            if not crossed:
                continue
            if _allowed_ring_crossing(project, a, b, pt):
                supported_grid_nodes += 1
                continue
            pair = {"supportA": a.code, "supportB": b.code, "supportAId": a.id, "supportBId": b.id, "levelIndex": a.level_index, "point": pt.model_dump(mode="json", by_alias=True) if pt else None}
            crossing_pairs.append(pair)
            issues.append(_issue("support_crossing", "fail", f"第 {a.level_index} 道非环形支撑 {a.code} 与 {b.code} 发生平面穿越。", a.id, "SupportElement", "次对撑可止于主支撑形成带立柱的 T/Y 节点；角撑必须保持墙—墙直接支承，并通过调整或删除冲突对撑消除穿越。", geometry={"kind": "crossing", "supportA": _support_geometry(a), "supportB": _support_geometry(b), "point": pair["point"]}, related=[b.id], hint="support_crossing"))

    if project.excavation and len(project.excavation.outline.points) >= 4 and project.excavation.depth >= 8.0 and len(corners) < 4:
        issues.append(_issue("corner_support", "warning", f"角撑数量 {len(corners)}，对较深矩形/多边形基坑可能不足。", object_type="SupportSystem", recommendation="检查凸直角是否布置角撑，或采用环撑。"))

    long_supports = [s for s in supports if float(s.span_length or _span(s)) >= COLUMN_SPAN_TRIGGER_M]
    columns = ret.columns or []
    if long_supports and not columns:
        issues.append(_issue("temporary_column", "fail", "存在较长支撑但未生成临时立柱。", object_type="ColumnElement", recommendation="按支撑跨长自动布置临时立柱/立柱桩。", hint="missing_column_service"))
    elif long_supports and columns:
        served = {code for col in columns for code in getattr(col, "support_codes", [])}
        unserved = [s for s in long_supports if s.code not in served]
        if len(unserved) > max(2, len(long_supports) // 2):
            issues.append(_issue("temporary_column", "warning", f"较长支撑 {len(unserved)} 根未明确纳入立柱服务范围。", object_type="ColumnElement", recommendation="在前端显示立柱服务范围并复核立柱间距。", geometry={"kind": "support_collection", "supports": [_support_geometry(s) for s in unserved[:20]]}, related=[s.id for s in unserved[:20]], hint="missing_column_service"))

    obstacle_count = len(project.excavation.obstacles) if project.excavation else 0
    if project.excavation and project.excavation.obstacles:
        for obs in project.excavation.obstacles:
            if not obs.active:
                continue
            for s in supports:
                if _support_intersects_obstacle(s, obs.outline):
                    sev = "fail" if obs.obstacle_type in {"muck_out_opening", "ramp", "protected_zone"} else "warning"
                    issues.append(_issue("obstacle_clearance", sev, f"支撑 {s.code} 与障碍/出土口 {obs.name} 的图形范围相交。", s.id, "SupportElement", "调整支撑分仓、设置洞口避让或在该区采用换撑/环撑。", geometry={"kind": "obstacle_conflict", "support": _support_geometry(s), "obstacleId": obs.id, "obstacleName": obs.name, "obstacleType": obs.obstacle_type}, related=[obs.id], hint="obstacle_conflict"))
    elif project.excavation:
        issues.append(_issue("obstacle_clearance", "warning", "未录入坡道、出土口、中心岛或保护区障碍，无法校核支撑避让。", object_type="ConstructionObstacle", recommendation="在 CAD 编辑器高级抽屉中绘制障碍物。"))

    replacement_count = len(ret.replacement_path or [])
    if replacement_count == 0:
        issues.append(_issue("replacement_path", "warning", "尚未定义换撑/拆撑路径。", object_type="RetainingSystem", recommendation="补充底板、楼板换撑和拆撑顺序。"))

    for issue in issues:
        if issue.highlight_geometry:
            highlights.append({
                "issueId": issue.id,
                "category": issue.category,
                "severity": issue.severity,
                "objectId": issue.object_id,
                "objectType": issue.object_type,
                "displayHint": issue.display_hint,
                "geometry": issue.highlight_geometry,
                "message": issue.message,
            })

    penalties = {"fail": 25.0, "warning": 8.0, "manual_review": 12.0, "pass": 0.0}
    score = 100.0 - sum(penalties.get(i.severity, 8.0) for i in issues)
    score = max(0.0, round(score, 1))
    severities = {i.severity for i in issues}
    status = "fail" if "fail" in severities else "warning" if "warning" in severities else "manual_review" if "manual_review" in severities else "pass"
    metrics = {
        "mainSupportCountByLevel": main_counts,
        "mainSupportCount": len(main),
        "secondaryGridSupportCount": len(secondary),
        "cornerDiagonalCount": len(corners),
        "supportCount": len(supports),
        "columnCount": len(columns),
        "obstacleCount": obstacle_count,
        "replacementPathCount": replacement_count,
        "minBaySpacing": round(min(bay_spacings), 3) if bay_spacings else None,
        "maxBaySpacing": round(max(bay_spacings), 3) if bay_spacings else None,
        "maxSpanLength": round(max_span, 3),
        "maxEffectiveUnbracedSpan": round(max_unbraced_span, 3),
        "supportedRingCrossingCount": supported_grid_nodes,
        "supportedGridNodeCount": supported_grid_nodes,
        "supportCrossingCount": len(crossing_pairs),
        "nonRingCrossingCount": len(crossing_pairs),
        **topology_intersections,
        "supportOutsideExcavationCount": support_outside_count,
        "planShapeDiagnostics": shape_diagnostics,
        "highlightCount": len(highlights),
        "minSupportWallClearance": round(min(clearance_values), 3) if clearance_values else None,
        "targetSupportWallClearance": round(target_clearance, 3),
        "geometricLongDirectStrutCount": geometric_long_direct_count,
        "columnResolvedLongDirectStrutCount": column_resolved_long_direct_count,
        "excessiveDirectStrutCount": excessive_direct_count,
        "maxRecommendedDirectStrutSpan": round(max_direct_span, 3),
        "maxWaleSupportBay": wale_bay_audit.get("maxBayM"),
        "waleSupportBayFailCount": wale_bay_audit.get("failCount", 0),
        "waleSupportBayWarningCount": wale_bay_audit.get("warningCount", 0),
        "waleSupportBayAudit": wale_bay_audit,
        "preferredSpacingRange": [PRACTICAL_MIN_SPACING_M, PRACTICAL_MAX_SPACING_M],
    }
    min_clearance_text = f"，最小墙边净距 {min(clearance_values):.2f}m" if clearance_values else ""
    summary = (
        f"支撑布置评分 {score:.1f}；主对撑 {len(main)} 根，次对撑 {len(secondary)} 根，角撑 {len(corners)} 根，"
        f"立柱 {len(columns)} 根，非法平面穿越 {len(crossing_pairs)} 处，内部 T/Y/X 汇交节点 "
        f"{int(topology_intersections.get('internalJunctionCount', 0))} 处，高度汇交节点 "
        f"{int(topology_intersections.get('highDegreeJunctionCount', 0))} 处，越界支撑 {support_outside_count} 根，"
        f"最大无支承长度 {max_unbraced_span:.2f}m{min_clearance_text}。"
    )
    return SupportLayoutQualitySummary(score=score, status=status, summary=summary, metrics=metrics, issues=issues, highlights=highlights, crossing_pairs=crossing_pairs)
