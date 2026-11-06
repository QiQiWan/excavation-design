from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from app.schemas.domain import Project
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.services.wall_vertical_length_optimizer import analyze_wall_vertical_length


def _length(a: Any, b: Any) -> float:
    return math.hypot(float(b.x - a.x), float(b.y - a.y))


def _effective_support_length(support: Any, columns: list[Any]) -> float:
    total = max(_length(support.start, support.end), 1.0e-9)
    dx = float(support.end.x - support.start.x)
    dy = float(support.end.y - support.start.y)
    stations = [0.0, total]
    for column in columns:
        if str(support.code) not in {str(code) for code in (column.support_codes or [])}:
            continue
        px = float(column.location.x - support.start.x)
        py = float(column.location.y - support.start.y)
        t = max(0.0, min(1.0, (px * dx + py * dy) / max(total * total, 1.0e-12)))
        qx = float(support.start.x) + t * dx
        qy = float(support.start.y) + t * dy
        if math.hypot(float(column.location.x) - qx, float(column.location.y) - qy) <= 1.5:
            stations.append(t * total)
    stations = sorted({round(value, 4) for value in stations})
    return max((b - a for a, b in zip(stations[:-1], stations[1:])), default=total)


def _support_review(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    if ret is None:
        return {"status": "manual_review", "message": "尚未生成围护体系。"}
    roles = Counter(str(item.support_role) for item in ret.supports)
    families = Counter(str(item.topology_family) for item in ret.supports)
    spans = [float(item.span_length or _length(item.start, item.end)) for item in ret.supports]
    effective_spans = [_effective_support_length(item, list(ret.columns or [])) for item in ret.supports]
    support_to_support = 0
    corner_wall_to_wall = 0
    questionable_corner = 0
    for item in ret.supports:
        if item.support_role == "corner_diagonal":
            if item.start_face_code and item.end_face_code and item.start_face_code != item.end_face_code:
                corner_wall_to_wall += 1
            else:
                questionable_corner += 1
        if not item.start_face_code or not item.end_face_code:
            if item.support_role in {"secondary_strut", "corner_diagonal"}:
                support_to_support += 1
    shape = dict((ret.layout_summary or {}).get("shapeDiagnostics") or {})
    concave = bool(shape.get("concave") or shape.get("isConcave"))
    aspect = float(shape.get("aspectRatio") or 0.0)
    preferred = "direct_grid"
    rationale = "狭长或近矩形基坑优先采用沿短跨方向的均匀直对撑，并在转角影响区设置墙—墙角撑。"
    if concave:
        preferred = "zoned_direct_grid"
        rationale = "凹形基坑应按几何分区建立直接对撑体系，凹角回墙单独形成可追溯传力路径。"
    elif aspect and aspect < 1.35:
        preferred = "bidirectional_ty_grid"
        rationale = "近方形宽大基坑宜采用主支撑连续、次支撑在专用 T/Y 节点终止的正交体系，避免普通杆件贯穿交叉。"
    issues: list[dict[str, Any]] = []
    if questionable_corner:
        issues.append({"severity": "fail", "code": "EXPERT-CORNER-NOT-WALL-TO-WALL", "count": questionable_corner, "message": "存在未直接连接两条相邻墙面/围檩的角撑。"})
    if support_to_support and preferred != "bidirectional_ty_grid":
        issues.append({"severity": "warning", "code": "EXPERT-SUPPORT-TO-SUPPORT-END", "count": support_to_support, "message": "普通补撑存在支撑—支撑终点；只有明确的边桁架或 T/Y 网格体系才允许。"})
    effective_limit = float(project.design_settings.max_direct_strut_span_m)
    if effective_spans and max(effective_spans) > effective_limit + 1e-9:
        issues.append({"severity": "warning", "code": "EXPERT-LONG-UNBRACED-STRUT", "count": sum(v > effective_limit for v in effective_spans), "message": "存在超过项目建议值的有效无侧向支承长度，应增设临时立柱或调整支撑体系。"})
    status = "fail" if any(item["severity"] == "fail" for item in issues) else "warning" if issues else "pass"
    return {
        "status": status,
        "preferredTopology": preferred,
        "rationale": rationale,
        "supportCount": len(ret.supports),
        "columnCount": len(ret.columns),
        "roleCounts": dict(roles),
        "topologyFamilyCounts": dict(families),
        "maximumTotalSpanM": round(max(spans), 3) if spans else 0.0,
        "maximumEffectiveUnbracedLengthM": round(max(effective_spans), 3) if effective_spans else 0.0,
        "effectiveUnbracedLengthLimitM": float(project.design_settings.max_direct_strut_span_m),
        "wallToWallCornerBraceCount": corner_wall_to_wall,
        "supportToSupportTerminationCount": support_to_support,
        "issues": issues,
        "decisionRules": [
            "普通对撑两端均应落在墙/围檩；角撑两端连接相邻墙面并位于转角影响区。",
            "宽大近方形基坑采用正交体系时，次支撑应在专用 T/Y 节点终止并设置竖向承托。",
            "环撑—径向撑只在明确的闭合环梁体系中启用，并使用二维/三维整体模型。",
            "支撑方案须同时满足土方通道、主体结构避让、围檩支点间距、压杆稳定和节点可施工性。",
        ],
    }


def _wall_rebar_review(project: Project, scheme: dict[str, Any]) -> dict[str, Any]:
    ret = project.retaining_system
    if ret is None:
        return {"status": "manual_review", "message": "尚未生成围护墙。"}
    zone_by_host: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for zone in scheme.get("wallZones", []):
        zone_by_host[str(zone.get("hostId"))].append(zone)
    wall_rows: list[dict[str, Any]] = []
    sparse: list[dict[str, Any]] = []
    for wall in ret.diaphragm_walls:
        pts = list(wall.axis.points or [])
        plan_length = sum(_length(a, b) for a, b in zip(pts[:-1], pts[1:])) if len(pts) > 1 else float(wall.design_length or 0.0)
        zones = zone_by_host.get(wall.id, [])
        face_rows = [face for zone in zones for face in zone.get("faces", [])]
        min_spacing = min((float(face.get("barSpacingMm") or 9999.0) for face in face_rows), default=9999.0)
        max_utilization = max((float(face.get("utilization") or 0.0) for face in face_rows), default=0.0)
        min_reserve = min((float(face.get("designReserveRatio") or 0.0) for face in face_rows), default=0.0)
        maximum_spacing = max((float(face.get("barSpacingMm") or 0.0) for face in face_rows), default=0.0)
        governing_face_by_side: dict[str, dict[str, Any]] = {}
        for face in face_rows:
            side = str(face.get("face") or "unknown")
            previous = governing_face_by_side.get(side)
            if previous is None or float(face.get("barSpacingMm") or 9999.0) < float(previous.get("barSpacingMm") or 9999.0):
                governing_face_by_side[side] = face
        estimated_vertical = sum(
            max(2, int(math.floor(plan_length / max(float(face.get("barSpacingMm") or 200.0) / 1000.0, 0.05))) + 1)
            for face in governing_face_by_side.values()
        ) if governing_face_by_side else 0
        current_groups = list(wall.reinforcement or [])
        current_vertical = 0
        for group in current_groups:
            if group.bar_type != "longitudinal":
                continue
            spacing_m = max(float(group.spacing or 200.0) / 1000.0, 0.05)
            current_vertical += max(2, int(math.floor(plan_length / spacing_m)) + 1)
        ratio = current_vertical / max(estimated_vertical, 1)
        row = {
            "wallId": wall.id,
            "wallCode": wall.panel_code,
            "planLengthM": round(plan_length, 3),
            "zoneCount": len(zones),
            "minimumMainBarSpacingMm": None if min_spacing >= 9999 else min_spacing,
            "maximumMainBarSpacingMm": maximum_spacing or None,
            "maximumSectionUtilization": round(max_utilization, 3),
            "minimumDesignReserveRatio": round(min_reserve, 3),
            "estimatedVerticalBarCount": estimated_vertical,
            "currentParametricVerticalBarCount": current_vertical,
            "densityCoverageRatio": round(ratio, 3),
            "browserDisplayIsSampled": True,
        }
        wall_rows.append(row)
        if plan_length >= 30.0 and (ratio < 0.95 or not zones):
            sparse.append(row)
    fail_count = int(scheme.get("summary", {}).get("failCount", 0) or 0)
    status = "fail" if fail_count else "warning" if sparse else "pass"
    return {
        "status": status,
        "wallCount": len(wall_rows),
        "longWallCount": sum(float(item["planLengthM"]) >= 30.0 for item in wall_rows),
        "sparseLongWallCount": len(sparse),
        "rows": wall_rows,
        "sparseWalls": sparse,
        "designPrinciples": [
            "墙体配筋按施工阶段正负弯矩包络分别设计坑内侧和坑外侧竖向主筋，并按目标利用率保留设计储备。",
            "优化器不得无提示削弱既有连续钢筋笼；长墙基础笼同时执行面积下限和最大间距下限。",
            "沿深度划分冠梁区、支撑节点区、坑底转换区、普通区和墙趾区；沿平面划分转角区、支撑节点区和长墙普通区。",
            "长墙钢筋数量按物理墙长和实际间距计算，不能使用缩短后的控制设计段长度替代。",
            "三维浏览器属于采样显示；钢筋清单、加工包和施工图必须保存完整估算数量、局部附加筋和截断状态。",
        ],
    }


def build_expert_design_review(project: Project, mode: str = "balanced") -> dict[str, Any]:
    rebar_scheme = build_rebar_design_scheme(project, mode=mode)
    support = _support_review(project)
    wall_rebar = _wall_rebar_review(project, rebar_scheme)
    vertical = analyze_wall_vertical_length(project, mode=mode)
    statuses = [support.get("status"), wall_rebar.get("status"), vertical.get("status")]
    status = "fail" if "fail" in statuses else "warning" if any(value in {"warning", "manual_review"} for value in statuses) else "pass"
    return {
        "projectId": project.id,
        "mode": mode,
        "status": status,
        "method": "design-institute expert workflow: topology family selection -> staged analysis -> member sizing -> two-direction wall reinforcement zoning -> vertical wall-length optimization -> constructability and issue gate",
        "supportSystem": support,
        "wallReinforcement": wall_rebar,
        "wallVerticalLength": vertical,
        "rebarSchemeSummary": rebar_scheme.get("summary", {}),
        "requiredSequence": [
            "锁定勘察、水位、周边荷载和施工阶段",
            "选择支撑体系族并建立明确传力路径",
            "运行墙—围檩—支撑分阶段计算",
            "按强度、稳定、变形和节点反力迭代截面",
            "按墙体竖向与平面双向分区设计配筋",
            "优化统一或分区墙趾标高并重新计算",
            "完成碰撞、吊装、施工缝、换撑和监测复核",
            "通过专业校核和发行门禁",
        ],
        "professionalReviewRequired": True,
    }
