from __future__ import annotations

import json
import math
import heapq
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from app.schemas.domain import Project
from app.services.rebar_detailing import build_rebar_detailing


_LIBRARY = Path(__file__).resolve().parents[4] / "packages" / "crane-library" / "default-cranes.json"


def _load_cranes(project: Project | None = None) -> list[dict[str, Any]]:
    base = json.loads(_LIBRARY.read_text(encoding="utf-8"))["cranes"]
    custom = ((project.advanced_engineering or {}).get("craneLibrary") if project else None) or []
    by_id = {str(item.get("id")): dict(item) for item in base}
    for item in custom:
        if isinstance(item, dict) and item.get("id") and item.get("capacityCurve"):
            by_id[str(item["id"])] = dict(item)
    return list(by_id.values())


def _capacity_at_radius(crane: dict[str, Any], radius: float) -> float:
    curve = sorted(crane["capacityCurve"], key=lambda item: item[0])
    if radius <= curve[0][0]:
        return float(curve[0][1])
    for (r0, c0), (r1, c1) in zip(curve, curve[1:]):
        if r0 <= radius <= r1:
            t = (radius - r0) / max(r1 - r0, 1e-9)
            return float(c0 + t * (c1 - c0))
    return 0.0


def _wall_midpoints(project: Project) -> dict[str, tuple[float, float]]:
    ret = project.retaining_system
    result: dict[str, tuple[float, float]] = {}
    for wall in (ret.diaphragm_walls if ret else []):
        pts = list(wall.axis.points or [])
        if len(pts) >= 2:
            result[wall.panel_code] = ((pts[0].x + pts[-1].x) / 2.0, (pts[0].y + pts[-1].y) / 2.0)
    return result


def _site_plan(project: Project) -> dict[str, Any]:
    raw = (project.advanced_engineering or {}).get("craneSitePlan") or {}
    return raw if isinstance(raw, dict) else {}


def _stand_points(project: Project, polygon: Polygon, offset: float = 8.0) -> list[dict[str, Any]]:
    configured = _site_plan(project).get("standPoints") or []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(configured, start=1):
        if not isinstance(item, dict):
            continue
        try:
            rows.append({
                "id": str(item.get("id") or f"SP-C{index:02d}"), "x": float(item["x"]), "y": float(item["y"]),
                "groundCapacityKpa": float(item.get("groundCapacityKpa") or 150.0),
                "accessWidthM": float(item.get("accessWidthM") or 6.0), "source": "project",
            })
        except (KeyError, TypeError, ValueError):
            continue
    if rows:
        return rows
    minx, miny, maxx, maxy = polygon.bounds
    cx, cy = polygon.centroid.x, polygon.centroid.y
    coords = [
        (minx - offset, miny - offset), (cx, miny - offset), (maxx + offset, miny - offset),
        (maxx + offset, cy), (maxx + offset, maxy + offset), (cx, maxy + offset),
        (minx - offset, maxy + offset), (minx - offset, cy),
    ]
    return [{"id": f"SP-{i:02d}", "x": x, "y": y, "groundCapacityKpa": 150.0, "accessWidthM": 6.0, "source": "generated"} for i,(x,y) in enumerate(coords, start=1)]


def _outline_polygon(value: Any) -> Polygon | None:
    if isinstance(value, dict):
        value = value.get("outline") or value.get("points")
    if not isinstance(value, list):
        return None
    pts = []
    for p in value:
        if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None:
            pts.append((float(p["x"]), float(p["y"])))
    return Polygon(pts) if len(pts) >= 3 else None


def _exclusion_zones(project: Project) -> list[tuple[str, Polygon]]:
    zones: list[tuple[str, Polygon]] = []
    excavation = project.excavation
    for obstacle in (excavation.obstacles if excavation else []):
        if not obstacle.active:
            continue
        # Only explicit protection/crane-control zones are hard lifting exclusions.
        # Basement grids, future ramps and muck-out openings are stage-dependent and
        # must not automatically invalidate diaphragm-wall cage erection.
        descriptor = f"{obstacle.name} {obstacle.note or ''}".lower()
        if obstacle.obstacle_type != "protected_zone" and "crane" not in descriptor and "吊装" not in descriptor:
            continue
        poly = None
        if obstacle.outline and len(obstacle.outline.points or []) >= 3:
            poly = Polygon([(p.x, p.y) for p in obstacle.outline.points])
        elif obstacle.center and obstacle.width and obstacle.length:
            poly = Point(obstacle.center.x, obstacle.center.y).buffer(max(obstacle.width, obstacle.length) / 2.0, cap_style=3)
        if poly and not poly.is_empty:
            zones.append((obstacle.id, poly.buffer(float(obstacle.clearance or 0.0))))
    for index, item in enumerate(_site_plan(project).get("exclusionZones") or [], start=1):
        poly = _outline_polygon(item)
        if poly and not poly.is_empty:
            zones.append((str(item.get("id") if isinstance(item, dict) else f"ZONE-{index}"), poly))
    return zones



def _site_boundary(project: Project, pit: Polygon) -> Polygon:
    raw = _site_plan(project).get("siteBoundary")
    polygon = _outline_polygon(raw)
    return polygon if polygon and not polygon.is_empty else pit.buffer(30.0, join_style=2)


def _road_corridors(project: Project) -> list[Polygon]:
    corridors: list[Polygon] = []
    for item in _site_plan(project).get("roads") or []:
        if not isinstance(item, dict):
            continue
        points = item.get("centerline") or item.get("points") or []
        coords = [(float(p["x"]), float(p["y"])) for p in points if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None]
        if len(coords) >= 2:
            corridors.append(LineString(coords).buffer(max(2.5, float(item.get("widthM") or 6.0) / 2.0), cap_style=2, join_style=2))
    return corridors


def _nearest_grid_point(point: tuple[float, float], origin: tuple[float, float], step: float) -> tuple[int, int]:
    return (int(round((point[0] - origin[0]) / step)), int(round((point[1] - origin[1]) / step)))


def _astar_route(
    start: tuple[float, float],
    goal: tuple[float, float],
    boundary: Polygon,
    exclusions: list[tuple[str, Polygon]],
    roads: list[Polygon],
    step: float = 2.0,
) -> dict[str, Any]:
    minx, miny, maxx, maxy = boundary.bounds
    origin = (minx, miny)
    blocked = unary_union([poly.buffer(0.8) for _, poly in exclusions]) if exclusions else None
    road_union = unary_union(roads) if roads else None

    def xy(node: tuple[int, int]) -> tuple[float, float]:
        return origin[0] + node[0] * step, origin[1] + node[1] * step

    def walkable(node: tuple[int, int]) -> bool:
        x, y = xy(node)
        pt = Point(x, y)
        if not boundary.buffer(-0.25).contains(pt):
            return False
        if blocked is not None and blocked.contains(pt):
            return False
        return True

    def nearest_walkable(seed: tuple[int, int]) -> tuple[int, int] | None:
        if walkable(seed):
            return seed
        for radius in range(1, 9):
            for dx in range(-radius, radius + 1):
                for dy in (-radius, radius):
                    node = (seed[0] + dx, seed[1] + dy)
                    if walkable(node):
                        return node
            for dy in range(-radius + 1, radius):
                for dx in (-radius, radius):
                    node = (seed[0] + dx, seed[1] + dy)
                    if walkable(node):
                        return node
        return None

    start_node = nearest_walkable(_nearest_grid_point(start, origin, step))
    goal_node = nearest_walkable(_nearest_grid_point(goal, origin, step))
    if start_node is None or goal_node is None:
        return {"found": False, "reason": "site gate or stand point is outside the available site boundary"}
    neighbors = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]
    queue: list[tuple[float, tuple[int, int]]] = [(0.0, start_node)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g: dict[tuple[int, int], float] = {start_node: 0.0}
    visited = 0
    while queue and visited < 120000:
        _, current = heapq.heappop(queue)
        visited += 1
        if current == goal_node:
            break
        for dx, dy in neighbors:
            nxt = (current[0] + dx, current[1] + dy)
            if not walkable(nxt):
                continue
            diagonal = dx != 0 and dy != 0
            move = step * (math.sqrt(2.0) if diagonal else 1.0)
            pt = Point(*xy(nxt))
            # Prefer designated roads while still allowing controlled off-road access near stand points.
            road_penalty = 1.0 if road_union is None or road_union.contains(pt) else 2.8
            boundary_clearance = pt.distance(boundary.boundary)
            edge_penalty = 1.0 + max(0.0, (3.0 - boundary_clearance) / 3.0) * 0.8
            tentative = g[current] + move * road_penalty * edge_penalty
            if tentative >= g.get(nxt, float("inf")):
                continue
            came[nxt] = current
            g[nxt] = tentative
            hx = math.hypot(goal_node[0] - nxt[0], goal_node[1] - nxt[1]) * step
            heapq.heappush(queue, (tentative + hx, nxt))
    if goal_node not in g:
        return {"found": False, "reason": "no collision-free site route was found", "visitedNodeCount": visited}
    nodes = [goal_node]
    while nodes[-1] != start_node:
        nodes.append(came[nodes[-1]])
    nodes.reverse()
    coords = [start, *[xy(node) for node in nodes[1:-1]], goal]
    line = LineString(coords).simplify(step * 0.45, preserve_topology=False)
    route_coords = [{"x": round(x, 3), "y": round(y, 3)} for x, y in line.coords]
    clearance = min((line.distance(poly) for _, poly in exclusions), default=999.0)
    road_length = float(line.intersection(road_union).length) if road_union is not None else float(line.length)
    turns = max(0, len(route_coords) - 2)
    return {
        "found": True, "line": line, "coordinates": route_coords, "lengthM": round(line.length, 3),
        "minimumExclusionClearanceM": round(clearance, 3), "roadCoverageRatio": round(road_length / max(line.length, 1e-9), 3),
        "turnCount": turns, "visitedNodeCount": visited, "gridStepM": step, "algorithm": "A* visibility-aware site grid",
    }

def optimize_cage_crane_logistics(project: Project, mode: str = "balanced", max_cases: int = 160, detailing: dict[str, Any] | None = None) -> dict[str, Any]:
    detailing = detailing or build_rebar_detailing(project, mode=mode)
    deep = detailing.get("deepDetailing", {})
    all_cages = list(deep.get("cageHoisting", []))
    # Round-robin by host wall so a case limit does not select only the first wall.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for cage in all_cages:
        grouped.setdefault(str(cage.get("hostCode") or "-"), []).append(cage)
    cages: list[dict[str, Any]] = []
    while len(cages) < max_cases and any(grouped.values()):
        for host in sorted(grouped):
            if grouped[host] and len(cages) < max_cases:
                cages.append(grouped[host].pop(0))
    excavation = project.excavation
    outline = [(p.x, p.y) for p in (excavation.outline.points if excavation else [])]
    polygon = Polygon(outline) if len(outline) >= 3 else Polygon([(0, 0), (30, 0), (30, 20), (0, 20)])
    stands = _stand_points(project, polygon)
    wall_targets = _wall_midpoints(project)
    cranes = _load_cranes(project)
    zones = _exclusion_zones(project)
    plan = _site_plan(project)
    site_boundary = _site_boundary(project, polygon)
    road_corridors = _road_corridors(project)
    gate = plan.get("siteGate") if isinstance(plan.get("siteGate"), dict) else None
    gate_point = (float(gate.get("x")), float(gate.get("y"))) if gate and gate.get("x") is not None and gate.get("y") is not None else None
    wind_speed = float(plan.get("designWindSpeedMps") or 8.0)
    rows: list[dict[str, Any]] = []
    for cage in cages:
        target = wall_targets.get(str(cage.get("hostCode"))) or (polygon.centroid.x, polygon.centroid.y)
        weight = float(cage.get("weightT") or 0.0)
        cage_length = float(cage.get("lengthM") or 0.0)
        required = weight * 1.35 * 1.10
        sail_area = max(1.0, cage_length * float(cage.get("widthM") or 2.5) * 0.35)
        candidates = []
        for stand in stands:
            stand_xy = (float(stand["x"]), float(stand["y"]))
            radius = Point(stand_xy).distance(Point(target))
            lift_path = LineString([stand_xy, target])
            pit_interior = polygon.buffer(-0.5)
            swing_over_pit_m = float(lift_path.intersection(pit_interior).length) if not pit_interior.is_empty else 0.0
            exclusion_hits = [zone_id for zone_id, zone in zones if lift_path.intersects(zone)]
            route = _astar_route(gate_point, stand_xy, site_boundary, zones, road_corridors, step=float(plan.get("routeGridStepM") or 2.0)) if gate_point else {"found": True, "line": None, "coordinates": [], "lengthM": None, "minimumExclusionClearanceM": None, "roadCoverageRatio": None, "turnCount": 0, "algorithm": "not_required"}
            transport_path = route.get("line")
            transport_hits = [zone_id for zone_id, zone in zones if transport_path and transport_path.intersects(zone)]
            crane_footprint_radius = float(stand.get("footprintRadiusM") or 5.5)
            footprint = Point(stand_xy).buffer(crane_footprint_radius)
            footprint_hits = [zone_id for zone_id, zone in zones if footprint.intersects(zone)]
            access_ok = float(stand.get("accessWidthM") or 0.0) >= float(plan.get("minimumAccessWidthM") or 5.0) and bool(route.get("found")) and not transport_hits and not footprint_hits
            for crane in cranes:
                capacity = _capacity_at_radius(crane, radius)
                utilization = required / max(capacity, 1e-9) if capacity > 0 else 999.0
                boom_required = math.sqrt(radius ** 2 + max(cage_length + 8.0, 12.0) ** 2)
                boom_ok = boom_required <= float(crane["maxBoomLengthM"])
                ground_pressure = float(crane.get("groundPressureKpa") or 999.0)
                ground_capacity = float(stand.get("groundCapacityKpa") or 150.0)
                ground_util = ground_pressure / max(ground_capacity, 1e-9)
                ground_ok = ground_util <= 0.85
                crane_wind_limit = float(crane.get("maxWindSpeedMps") or 10.0)
                wind_util = wind_speed / max(crane_wind_limit, 1e-9) * min(1.25, 0.75 + sail_area / 120.0)
                wind_ok = wind_util <= 1.0
                path_ok = not exclusion_hits and not footprint_hits and swing_over_pit_m <= max(2.0, radius * 0.20)
                feasible = utilization <= 0.85 and boom_ok and ground_ok and wind_ok and path_ok and access_ok
                mat_area = max(0.0, weight * 9.81 * 1.35 / max(ground_capacity * 0.70, 1e-9))
                score = 100.0 - utilization * 42.0 - radius * 0.65 - max(0.0, ground_util - 0.55) * 24.0 - wind_util * 8.0
                route_length = float(route.get("lengthM") or 0.0)
                score -= min(18.0, swing_over_pit_m * 0.9) + len(exclusion_hits) * 15.0 + len(transport_hits) * 12.0 + len(footprint_hits) * 14.0 + min(10.0, route_length / 30.0)
                if feasible:
                    score += 20.0
                candidates.append({
                    "craneId": crane["id"], "craneName": crane["name"], "standId": stand["id"],
                    "standPoint": {"x": round(stand_xy[0], 3), "y": round(stand_xy[1], 3)},
                    "standSource": stand.get("source"), "targetPoint": {"x": round(target[0], 3), "y": round(target[1], 3)},
                    "workingRadiusM": round(radius, 3), "availableCapacityT": round(capacity, 3), "requiredCapacityT": round(required, 3),
                    "capacityUtilization": round(utilization, 3), "requiredBoomLengthM": round(boom_required, 3),
                    "groundPressureKpa": round(ground_pressure, 2), "groundCapacityKpa": round(ground_capacity, 2),
                    "groundUtilization": round(ground_util, 3), "recommendedMatAreaM2": round(mat_area, 2),
                    "designWindSpeedMps": round(wind_speed, 2), "windUtilization": round(wind_util, 3),
                    "sailAreaM2": round(sail_area, 2), "liftPathLengthM": round(lift_path.length, 3),
                    "swingOverPitM": round(swing_over_pit_m, 3), "exclusionZoneHits": exclusion_hits,
                    "transportRouteLengthM": route.get("lengthM"), "transportRouteCoordinates": route.get("coordinates"),
                    "transportRouteAlgorithm": route.get("algorithm"), "transportRouteFound": bool(route.get("found")),
                    "transportRouteRoadCoverageRatio": route.get("roadCoverageRatio"), "transportRouteTurnCount": route.get("turnCount"),
                    "transportMinimumClearanceM": route.get("minimumExclusionClearanceM"),
                    "transportExclusionHits": transport_hits, "craneFootprintRadiusM": round(crane_footprint_radius, 3),
                    "craneFootprintExclusionHits": footprint_hits, "accessWidthM": stand.get("accessWidthM"),
                    "boomOk": boom_ok, "groundOk": ground_ok, "windOk": wind_ok, "pathOk": path_ok, "accessOk": access_ok,
                    "feasible": feasible, "score": round(max(0.0, min(100.0, score)), 2),
                })
        candidates.sort(key=lambda item: (not item["feasible"], -item["score"], item["capacityUtilization"]))
        best = candidates[0] if candidates else None
        rows.append({
            "analysisId": f"CRANE-{cage.get('segmentId')}", "segmentId": cage.get("segmentId"), "hostCode": cage.get("hostCode"),
            "cageWeightT": weight, "cageLengthM": cage_length, "requiredCapacityT": round(required, 3),
            "status": "pass" if best and best["feasible"] else "fail", "recommended": best, "alternatives": candidates[:8],
            "recommendedAction": "按推荐吊机/站位复核履带板、风速、吊索具和现场回转禁入区" if best and best["feasible"] else "重新分节、增大吊机等级、加铺路基箱或调整站位/运输路径",
        })
    fail = sum(item["status"] == "fail" for item in rows)
    return {
        "version": "3.10.0", "status": "fail" if fail else "pass",
        "summary": {
            "caseCount": len(rows), "feasibleCount": len(rows) - fail, "failCount": fail,
            "craneLibraryCount": len(cranes), "standPointCount": len(stands), "exclusionZoneCount": len(zones),
            "projectSpecificStandPointCount": sum(item.get("source") == "project" for item in stands),
            "routePlannedCaseCount": sum(bool((item.get("recommended") or {}).get("transportRouteFound")) for item in rows),
            "roadCorridorCount": len(road_corridors),
        },
        "cases": rows, "craneLibrary": cranes, "standPoints": stands,
        "siteAssumptions": {"designWindSpeedMps": wind_speed, "siteGate": gate, "exclusionZoneIds": [x[0] for x in zones], "siteBoundary": [{"x": round(x,3), "y": round(y,3)} for x,y in site_boundary.exterior.coords], "roadCorridorCount": len(road_corridors)},
        "method": "joint cage segmentation, crane capacity-curve interpolation, stand/footprint screening, ground bearing, wind, lift swing and A* site-route planning",
        "boundary": "A*路线和自动站位用于场地方案筛选。正式吊装仍须导入实测道路宽高、转弯半径、高压线/地下管线、实际设备工况表、吊索具、履带板和经审批运输路线。",
    }

