from __future__ import annotations

import math
from typing import Any

from shapely.geometry import LineString, Point, box

from app.schemas.domain import Project
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme


def _distance_point_line(px: float, py: float, line: LineString) -> float:
    return float(line.distance(Point(px, py)))


def evaluate_model_collisions(project: Project, mode: str = "balanced") -> dict[str, Any]:
    ret = project.retaining_system
    if not ret:
        return {"status": "fail", "summary": {"message": "缺少围护结构"}, "collisions": []}
    scheme = build_rebar_design_scheme(project, mode=mode)
    collisions: list[dict[str, Any]] = []
    intended: list[dict[str, Any]] = []
    supports = ret.supports
    boundary = None
    if project.excavation and project.excavation.outline and project.excavation.outline.points:
        pts = [(p.x, p.y) for p in project.excavation.outline.points]
        if project.excavation.outline.closed and pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        boundary = LineString(pts) if len(pts) >= 2 else None
    vertical_clearance_checks = 0
    for i, a in enumerate(supports):
        la = LineString([(a.start.x, a.start.y), (a.end.x, a.end.y)])
        aw = float(a.section.width or a.section.diameter or 0.8)
        ah = float(a.section.height or a.section.diameter or aw)
        for b in supports[i + 1:]:
            lb = LineString([(b.start.x, b.start.y), (b.end.x, b.end.y)])
            inter = la.intersection(lb)
            if inter.is_empty:
                continue
            point = inter if inter.geom_type == "Point" else inter.representative_point()
            bw = float(b.section.width or b.section.diameter or 0.8)
            bh = float(b.section.height or b.section.diameter or bw)
            if a.level_index != b.level_index:
                vertical_clearance_checks += 1
                clear = abs(float(a.elevation) - float(b.elevation)) - (ah + bh) / 2.0
                required = 0.15
                if clear < required:
                    status = "fail" if clear < 0.0 else "warning"
                    collisions.append({
                        "id": f"COL-VERT-{a.id}-{b.id}", "objectA": a.code, "objectB": b.code,
                        "levelIndexA": a.level_index, "levelIndexB": b.level_index, "x": point.x, "y": point.y,
                        "type": "vertical_member_clearance", "status": status, "clearanceM": round(clear, 3),
                        "requiredClearanceM": required,
                        "message": "不同标高构件实体包络发生穿透" if status == "fail" else "不同标高构件竖向净距不足",
                        "recommendedAction": "调整标高、截面或平面位置，并检查施工顺序和换撑路径",
                    })
                continue
            near_endpoint = any(point.distance(Point(p.x, p.y)) < 0.25 for p in (a.start, a.end, b.start, b.end))
            connected_column = any(
                _distance_point_line(c.location.x, c.location.y, la) <= aw / 2 + 0.35
                and _distance_point_line(c.location.x, c.location.y, lb) <= bw / 2 + 0.35
                and Point(c.location.x, c.location.y).distance(point) <= 0.75
                for c in ret.columns
            )
            role_pair = {a.support_role, b.support_role}
            at_wall = bool(boundary is not None and boundary.distance(point) <= 0.35)
            ring_exception = role_pair == {"ring_strut"} and bool(ret.ring_beams)
            valid_node = (near_endpoint and at_wall) or (near_endpoint and connected_column) or ring_exception
            status = "pass" if valid_node else "fail"
            item = {
                "id": f"COL-SUP-{a.id}-{b.id}", "objectA": a.code, "objectB": b.code,
                "levelIndex": a.level_index, "x": point.x, "y": point.y,
                "type": "support_intersection", "status": status,
                "message": (
                    "支撑在墙端或带立柱的 T/Y 节点连接" if valid_node and not ring_exception
                    else "环形/径向支撑按内环节点连接" if ring_exception
                    else "非环形水平支撑发生跨中穿越或节点缺少立柱"
                ),
                "recommendedAction": "按节点大样复核" if status == "pass" else "将次对撑或角撑终止于主支撑节点，并在节点设置临时立柱；禁止跨中穿越",
            }
            (intended if item["status"] == "pass" else collisions).append(item)
    for support in supports:
        line = LineString([(support.start.x, support.start.y), (support.end.x, support.end.y)])
        half = float(support.section.width or support.section.diameter or 0.8) / 2
        for obs in project.excavation.obstacles if project.excavation else []:
            if not obs.active or not obs.center:
                continue
            clearance = float(obs.clearance or 0.0)
            width = max(float(obs.width or 0.0), 0.5)
            length = max(float(obs.length or 0.0), 0.5)
            envelope = box(obs.center.x - width/2.0, obs.center.y - length/2.0, obs.center.x + width/2.0, obs.center.y + length/2.0)
            clear_distance = float(line.distance(envelope)) - half
            if line.intersects(envelope.buffer(clearance + half)):
                actual_intrusion = line.intersects(envelope.buffer(half))
                severity = "fail" if actual_intrusion or obs.obstacle_type in {"ramp", "muck_out_opening"} else "warning"
                collisions.append({
                    "id": f"COL-OBS-{support.id}-{obs.id}", "objectA": support.code, "objectB": obs.name,
                    "type": "support_obstacle_clearance", "status": severity, "clearanceM": round(clear_distance, 3),
                    "requiredClearanceM": clearance, "message": "支撑侵入施工通道或障碍实体" if severity == "fail" else "支撑进入保护区建议净空，需结合邻近结构专项复核",
                    "recommendedAction": "移动支撑端点、调整通道边界或锁定后重新优化" if severity == "fail" else "复核墙端节点、邻近结构影响和施工净空",
                })
    congestion = []
    for row in scheme.get("wallZones", []):
        ratio = float(row.get("congestionRatio") or 0.0)
        clear = float(row.get("cageClearWidthMm") or 999.0)
        status = "fail" if clear < 100 or ratio > 0.035 else "warning" if clear < 160 or ratio > 0.025 else "pass"
        if status != "pass":
            congestion.append({
                "id": f"COL-RB-{row.get('zoneId')}", "objectA": row.get("hostCode"), "objectB": row.get("zoneId"),
                "type": "rebar_congestion", "status": status, "cageClearWidthMm": clear, "reinforcementRatio": ratio,
                "message": "钢筋笼局部净距或钢筋体积率偏紧", "recommendedAction": "优化钢筋层数、机械连接、接头错开和预埋件位置",
                "drawingRefs": row.get("drawingRefs", []),
            })
    all_issues = collisions + congestion
    status = "fail" if any(x["status"] == "fail" for x in all_issues) else "warning" if all_issues else "pass"
    return {
        "status": status,
        "summary": {"hardCollisionCount": sum(x["status"] == "fail" for x in all_issues), "warningCount": sum(x["status"] == "warning" for x in all_issues), "intendedNodeCount": len(intended), "checkedSupportCount": len(supports), "verticalClearanceCheckCount": vertical_clearance_checks},
        "collisions": all_issues, "intendedConnections": intended,
        "method": "3D engineering clearance screen using support centerlines, section envelopes, cross-level vertical clearances, obstacle protection zones and reinforcement-zone congestion indicators",
    }
