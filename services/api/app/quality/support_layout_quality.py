from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from app.schemas.domain import Project, QualityGateIssue, SupportElement, SupportLayoutQualitySummary, Point2D, Polyline2D

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


def _orientation_axis(project: Project) -> str:
    if not project.excavation or not project.excavation.outline.points:
        return "x"
    b = _bbox(project.excavation.outline.points)
    if not b:
        return "x"
    minx, miny, maxx, maxy = b
    return "x" if (maxx - minx) >= (maxy - miny) else "y"


def _pt_eq(a: Point2D, b: Point2D, tol: float = 1e-6) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) <= tol


def _shares_endpoint(a: SupportElement, b: SupportElement) -> bool:
    return any(_pt_eq(p, q) for p in (a.start, a.end) for q in (b.start, b.end))


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
    if _shares_endpoint(a, b):
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


def _supported_grid_crossing(project: Project, a: SupportElement, b: SupportElement, point: Point2D | None) -> bool:
    if {a.support_role, b.support_role} != {"main_strut", "secondary_strut"} or point is None:
        return False
    ret = project.retaining_system
    if not ret:
        return False
    for column in ret.columns:
        codes = set(getattr(column, "support_codes", []) or [])
        if a.code in codes and b.code in codes and math.hypot(column.location.x - point.x, column.location.y - point.y) <= 0.75:
            return True
    return False


def evaluate_support_layout_quality(project: Project) -> SupportLayoutQualitySummary:
    ret = project.retaining_system
    if not ret or not ret.supports:
        return SupportLayoutQualitySummary(score=0, status="manual_review", summary="尚未生成水平支撑体系，无法评价支撑布置合理性。", issues=[_issue("support_layout", "manual_review", "缺少支撑体系。", recommendation="先执行一键生成围护体系。")])

    supports = ret.supports
    main = [s for s in supports if s.support_role == "main_strut"]
    secondary = [s for s in supports if s.support_role == "secondary_strut"]
    corners = [s for s in supports if s.support_role == "corner_diagonal"]
    axis = _orientation_axis(project)
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

    for level, items in sorted(by_level.items()):
        items_sorted = sorted(items, key=lambda ss: _mid(ss)[0 if axis == "x" else 1])
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

    for s in supports:
        sp = float(s.span_length or _span(s))
        unbraced = _effective_unbraced_span(project, s)
        max_span = max(max_span, sp)
        max_unbraced_span = max(max_unbraced_span, unbraced)
        if unbraced > FAIL_MAX_SPAN_M:
            issues.append(_issue("support_span", "fail", f"支撑 {s.code} 有效无侧向支承长度 {unbraced:.2f}m 超过 {FAIL_MAX_SPAN_M:.1f}m。", s.id, "SupportElement", "增设临时立柱/网格节点或改变支撑体系。", geometry=_support_geometry(s), hint="span_fail"))
        elif unbraced > WARNING_MAX_SPAN_M:
            issues.append(_issue("support_span", "warning", f"支撑 {s.code} 有效无侧向支承长度 {unbraced:.2f}m 偏大。", s.id, "SupportElement", "复核长细比、挠度、立柱和施工安装。", geometry=_support_geometry(s), hint="span_warning"))

    # Crossings: any same-level crossing without shared endpoints is a layout hard issue.
    for i, a in enumerate(supports):
        for b in supports[i + 1:]:
            crossed, pt = _supports_cross(a, b)
            if not crossed:
                continue
            if _supported_grid_crossing(project, a, b, pt):
                supported_grid_nodes += 1
                continue
            pair = {"supportA": a.code, "supportB": b.code, "supportAId": a.id, "supportBId": b.id, "levelIndex": a.level_index, "point": pt.model_dump(mode="json", by_alias=True) if pt else None}
            crossing_pairs.append(pair)
            issues.append(_issue("support_crossing", "fail", f"第 {a.level_index} 道支撑 {a.code} 与 {b.code} 发生平面交叉。", a.id, "SupportElement", "重新生成支撑或调整角撑/环撑，支撑中心线不得无节点交叉。", geometry={"kind": "crossing", "supportA": _support_geometry(a), "supportB": _support_geometry(b), "point": pair["point"]}, related=[b.id], hint="support_crossing"))

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
        "supportedGridNodeCount": supported_grid_nodes,
        "supportCrossingCount": len(crossing_pairs),
        "highlightCount": len(highlights),
        "preferredSpacingRange": [PRACTICAL_MIN_SPACING_M, PRACTICAL_MAX_SPACING_M],
    }
    summary = f"支撑布置评分 {score:.1f}；主对撑 {len(main)} 根，次对撑 {len(secondary)} 根，角撑 {len(corners)} 根，立柱 {len(columns)} 根，未设节点交叉 {len(crossing_pairs)} 处，最大无支承长度 {max_unbraced_span:.2f}m。"
    return SupportLayoutQualitySummary(score=score, status=status, summary=summary, metrics=metrics, issues=issues, highlights=highlights, crossing_pairs=crossing_pairs)
