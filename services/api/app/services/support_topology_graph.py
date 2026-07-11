from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from typing import Any

from shapely.geometry import LineString, Point, Polygon, box

from app.schemas.domain import MaterialDefinition, Point2D, Project, SectionDefinition, SupportElement
from app.services.support_layout import make_support_wale_nodes, _nearest_face_hit, _assign_tributary_widths


def _orientation(points: list[Point2D]) -> float:
    return sum(points[i].x * points[(i + 1) % len(points)].y - points[(i + 1) % len(points)].x * points[i].y for i in range(len(points)))


def _concave_vertices(points: list[Point2D]) -> list[dict[str, float]]:
    if len(points) < 4:
        return []
    ccw = _orientation(points) > 0
    out = []
    for i, b in enumerate(points):
        a, c = points[i - 1], points[(i + 1) % len(points)]
        cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x)
        if (ccw and cross < -1e-8) or ((not ccw) and cross > 1e-8):
            out.append({"index": i, "x": b.x, "y": b.y})
    return out


def _node_key(x: float, y: float, level: int) -> str:
    return f"L{level}:{round(x, 2)}:{round(y, 2)}"


def _graph_resilience(adjacency: dict[str, set[str]]) -> tuple[int, int]:
    timer = 0
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    articulation: set[str] = set()
    bridges = 0

    def dfs(node: str) -> None:
        nonlocal timer, bridges
        timer += 1; disc[node] = low[node] = timer
        children = 0
        for nxt in adjacency[node]:
            if nxt not in disc:
                parent[nxt] = node; children += 1; dfs(nxt); low[node] = min(low[node], low[nxt])
                if parent.get(node) is None and children > 1: articulation.add(node)
                if parent.get(node) is not None and low[nxt] >= disc[node]: articulation.add(node)
                if low[nxt] > disc[node]: bridges += 1
            elif nxt != parent.get(node):
                low[node] = min(low[node], disc[nxt])

    for node in adjacency:
        if node not in disc:
            parent[node] = None; dfs(node)
    return len(articulation), bridges


def _longest_line(geometry):
    if geometry.is_empty:
        return None
    if geometry.geom_type == "LineString":
        return geometry
    parts = [item for item in getattr(geometry, "geoms", []) if item.geom_type == "LineString"]
    return max(parts, key=lambda item: item.length, default=None)


def _candidate_avoids_obstacles(project: Project, line: LineString) -> bool:
    for obstacle in project.excavation.obstacles if project.excavation else []:
        if not obstacle.active:
            continue
        clearance = max(float(obstacle.clearance or 0.0), 0.0)
        if obstacle.outline and obstacle.outline.points:
            envelope = Polygon([(p.x, p.y) for p in obstacle.outline.points]).buffer(clearance)
        elif obstacle.center:
            width = max(float(obstacle.width or 0.5), 0.5)
            length = max(float(obstacle.length or 0.5), 0.5)
            envelope = box(obstacle.center.x-width/2, obstacle.center.y-length/2, obstacle.center.x+width/2, obstacle.center.y+length/2).buffer(clearance)
        else:
            continue
        if line.intersects(envelope):
            return False
    return True


def analyze_support_topology(project: Project) -> dict[str, Any]:
    if not project.excavation or not project.retaining_system:
        return {"status": "fail", "summary": {"message": "缺少基坑或支撑体系"}, "levels": [], "recommendations": []}
    points = project.excavation.outline.points
    polygon = Polygon([(p.x, p.y) for p in points])
    concave = _concave_vertices(points)
    by_level: dict[int, list[SupportElement]] = defaultdict(list)
    for support in project.retaining_system.supports:
        by_level[support.level_index].append(support)
    level_rows = []
    recommendations: list[dict[str, Any]] = []
    overall = "pass"
    for level, supports in sorted(by_level.items()):
        adjacency: dict[str, set[str]] = defaultdict(set)
        angles = []
        lines = []
        for support in supports:
            a = _node_key(support.start.x, support.start.y, level)
            b = _node_key(support.end.x, support.end.y, level)
            adjacency[a].add(b); adjacency[b].add(a)
            angle = math.degrees(math.atan2(support.end.y - support.start.y, support.end.x - support.start.x)) % 180.0
            angles.append(angle)
            lines.append((support, LineString([(support.start.x, support.start.y), (support.end.x, support.end.y)])))
        for i, (s1, l1) in enumerate(lines):
            for s2, l2 in lines[i + 1:]:
                inter = l1.intersection(l2)
                if inter.geom_type == "Point" and not inter.is_empty and polygon.buffer(-0.05).contains(inter):
                    key = _node_key(inter.x, inter.y, level)
                    for support in (s1, s2):
                        a = _node_key(support.start.x, support.start.y, level)
                        b = _node_key(support.end.x, support.end.y, level)
                        adjacency[key].update((a, b)); adjacency[a].add(key); adjacency[b].add(key)
        visited: set[str] = set(); components = 0
        for node in adjacency:
            if node in visited: continue
            components += 1
            queue = deque([node]); visited.add(node)
            while queue:
                cur = queue.popleft()
                for nxt in adjacency[cur]:
                    if nxt not in visited: visited.add(nxt); queue.append(nxt)
        horizontal = sum(1 for a in angles if min(abs(a), abs(180-a)) <= 25)
        vertical = sum(1 for a in angles if abs(a - 90) <= 25)
        directional = horizontal > 0 and vertical > 0
        redundancy = max(len(supports) - max(len(adjacency) - components, 0), 0)
        articulation_count, bridge_count = _graph_resilience(adjacency)
        face_codes = {segment.name for segment in project.excavation.segments}
        covered_faces = {code for support in supports for code in (support.start_face_code, support.end_face_code) if code}
        uncovered_faces = sorted(face_codes - covered_faces)
        face_coverage = len(covered_faces) / max(len(face_codes), 1)
        status = "pass"
        issues = []
        if components > 1:
            status = "warning"; issues.append(f"支撑图存在 {components} 个连通分量")
        if not directional and len(points) <= 6:
            status = "warning"; issues.append("缺少双向直接传力路径")
        if concave and not any(s.support_role == "corner_diagonal" for s in supports):
            status = "fail"; issues.append("凹角区域缺少局部斜撑或环梁闭合")
        if face_coverage < 0.75:
            status = "warning" if status == "pass" else status; issues.append(f"墙面直接支承覆盖率仅 {face_coverage:.0%}")
        if articulation_count and bridge_count > max(2, len(supports)//3):
            status = "warning" if status == "pass" else status; issues.append("存在较多单点/单杆失效敏感路径")
        if status == "fail": overall = "fail"
        elif status == "warning" and overall == "pass": overall = "warning"
        level_rows.append({
            "levelIndex": level, "elevation": supports[0].elevation if supports else None,
            "supportCount": len(supports), "nodeCount": len(adjacency), "connectedComponents": components,
            "horizontalPathCount": horizontal, "verticalPathCount": vertical, "directionalCoverage": directional,
            "graphRedundancy": redundancy, "articulationNodeCount": articulation_count, "bridgeMemberCount": bridge_count,
            "faceCoverageRatio": round(face_coverage, 3), "uncoveredFaceCodes": uncovered_faces, "status": status, "issues": issues,
        })
        if not directional:
            recommendations.append({"levelIndex": level, "type": "add_orthogonal_path", "priority": 1, "message": "增加与现有主对撑正交的次对撑，并在交点设置立柱。"})
        for vertex in concave:
            recommendations.append({"levelIndex": level, "type": "concave_corner_brace", "priority": 1, "vertex": vertex, "message": "凹角设置斜撑、局部环梁或板带换撑，避免应力集中。"})
    obstacle_count = len([o for o in project.excavation.obstacles if o.active])
    return {
        "status": overall,
        "summary": {"levelCount": len(level_rows), "concaveVertexCount": len(concave), "obstacleCount": obstacle_count, "message": "支撑图按层进行连通性、双向传力、冗余度和凹角覆盖检查。"},
        "levels": level_rows, "concaveVertices": concave, "recommendations": recommendations,
        "topologyHash": hashlib.sha256(json.dumps([(s.level_index, s.start.x, s.start.y, s.end.x, s.end.y) for s in project.retaining_system.supports], sort_keys=True).encode()).hexdigest()[:16],
    }


def preview_topology_enhancements(project: Project) -> dict[str, Any]:
    analysis = analyze_support_topology(project)
    additions: list[dict[str, Any]] = []
    if not project.excavation or not project.retaining_system:
        return {**analysis, "safeAdditions": additions}
    polygon = Polygon([(p.x, p.y) for p in project.excavation.outline.points])
    center = polygon.representative_point()
    existing = [LineString([(s.start.x, s.start.y), (s.end.x, s.end.y)]) for s in project.retaining_system.supports]
    minx, miny, maxx, maxy = polygon.bounds
    for row in analysis.get("recommendations", []):
        if row.get("type") != "add_orthogonal_path":
            continue
        level = int(row["levelIndex"])
        level_row = next((item for item in analysis.get("levels", []) if int(item.get("levelIndex", -1)) == level), {})
        missing_horizontal = int(level_row.get("horizontalPathCount", 0)) == 0
        raw = LineString([(minx-2, center.y), (maxx+2, center.y)]) if missing_horizontal else LineString([(center.x, miny-2), (center.x, maxy+2)])
        candidate = _longest_line(raw.intersection(polygon))
        if not candidate or candidate.length < 4.0 or not _candidate_avoids_obstacles(project, candidate):
            continue
        coords = list(candidate.coords); start, end = Point(coords[0]), Point(coords[-1])
        if any(candidate.hausdorff_distance(line) < 1.0 for line in existing):
            continue
        additions.append({"levelIndex": level, "start": {"x": start.x, "y": start.y}, "end": {"x": end.x, "y": end.y}, "role": "secondary_strut", "reason": "orthogonal_load_path"})
    for row in analysis.get("recommendations", []):
        if row.get("type") != "concave_corner_brace": continue
        v = row["vertex"]; vx, vy = float(v["x"]), float(v["y"])
        dx, dy = center.x - vx, center.y - vy
        norm = math.hypot(dx, dy) or 1.0
        start = Point(vx + dx / norm * 0.8, vy + dy / norm * 0.8)
        ray = LineString([(start.x, start.y), (vx + dx / norm * 5000, vy + dy / norm * 5000)])
        segment = ray.intersection(polygon)
        if segment.is_empty or segment.geom_type != "LineString": continue
        coords = list(segment.coords)
        end = Point(coords[-1])
        candidate = LineString([(start.x, start.y), (end.x, end.y)])
        if candidate.length < 4.0 or any(candidate.hausdorff_distance(line) < 1.0 for line in existing): continue
        additions.append({"levelIndex": row["levelIndex"], "start": {"x": start.x, "y": start.y}, "end": {"x": end.x, "y": end.y}, "role": "corner_diagonal", "reason": "concave_corner_brace"})
    return {**analysis, "safeAdditions": additions}


def apply_topology_enhancements(project: Project) -> dict[str, Any]:
    preview = preview_topology_enhancements(project)
    ret = project.retaining_system
    if not ret:
        return preview
    level_elevations = {s.level_index: s.elevation for s in ret.supports}
    created = []
    for idx, item in enumerate(preview.get("safeAdditions", []), start=1):
        level = int(item["levelIndex"])
        role = item.get("role", "corner_diagonal")
        prefix = "ORTH" if role == "secondary_strut" else "CC"
        start_point = Point2D(**item["start"]); end_point = Point2D(**item["end"])
        start_hit = _nearest_face_hit(start_point, project.excavation); end_hit = _nearest_face_hit(end_point, project.excavation)
        support = SupportElement(
            code=f"AUTO-{prefix}-L{level}-{idx:02d}", level_index=level, elevation=level_elevations.get(level, -3.0 * level),
            start=start_point, end=end_point, support_role=role,
            start_face_code=start_hit.face_code if start_hit else None, end_face_code=end_hit.face_code if end_hit else None,
            section=SectionDefinition(width=1.0, height=1.0, name="1000x1000 RC"), material=MaterialDefinition(name="Concrete", grade="C40"),
            layout_note=f"V3.3 {item.get('reason')} topology enhancement; calculation and professional review required",
        )
        support.span_length = math.hypot(support.end.x-support.start.x, support.end.y-support.start.y)
        ret.supports.append(support); created.append(support.code)
    if created:
        _assign_tributary_widths(ret.supports, project.excavation)
        ret.support_nodes = make_support_wale_nodes(ret.supports, ret.wale_beams)
        ret.warnings.append(f"V3.3 自动增加 {len(created)} 根凹角支撑，必须重新计算并复核节点。")
        project.calculation_cases = []
        project.calculation_results = []
    preview["createdSupportCodes"] = created
    preview["requiresRecalculation"] = bool(created)
    return preview
