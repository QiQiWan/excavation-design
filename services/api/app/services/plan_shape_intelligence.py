from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon, box

from app.schemas.domain import Point2D

EPS = 1.0e-9


def _dedup(points: Iterable[Point2D]) -> list[Point2D]:
    rows: list[Point2D] = []
    for raw in points:
        point = Point2D(x=float(raw.x), y=float(raw.y))
        if rows and math.hypot(point.x - rows[-1].x, point.y - rows[-1].y) <= 1.0e-7:
            continue
        rows.append(point)
    if len(rows) > 1 and math.hypot(rows[0].x - rows[-1].x, rows[0].y - rows[-1].y) <= 1.0e-7:
        rows.pop()
    return rows


def _polygon(points: list[Point2D]) -> tuple[Polygon | None, list[str]]:
    warnings: list[str] = []
    if len(points) < 3:
        return None, ["基坑轮廓少于3个有效顶点。"]
    raw = Polygon([(point.x, point.y) for point in points])
    if raw.is_valid and raw.area > EPS:
        return raw, warnings
    repaired = raw.buffer(0)
    if isinstance(repaired, MultiPolygon):
        repaired = max(repaired.geoms, key=lambda item: item.area)
        warnings.append("轮廓自交修复后形成多个区域；诊断仅采用最大连通区域，正式设计前必须清理CAD轮廓。")
    if not isinstance(repaired, Polygon) or repaired.area <= EPS:
        return None, ["基坑轮廓无效或面积为零。"]
    warnings.append("轮廓存在自交/重复边，诊断阶段已执行几何修复；正式计算应使用清理后的闭合轮廓。")
    return repaired, warnings


def _reflex_count(points: list[Point2D]) -> int:
    if len(points) < 4:
        return 0
    signed = 0.0
    for first, second in zip(points, points[1:] + points[:1]):
        signed += first.x * second.y - second.x * first.y
    orientation = 1.0 if signed >= 0.0 else -1.0
    count = 0
    for index, current in enumerate(points):
        previous = points[(index - 1) % len(points)]
        following = points[(index + 1) % len(points)]
        ax, ay = current.x - previous.x, current.y - previous.y
        bx, by = following.x - current.x, following.y - current.y
        cross = ax * by - ay * bx
        if cross * orientation < -1.0e-7:
            count += 1
    return count


def _minimum_rectangle_frame(poly: Polygon) -> tuple[float, float, float, float, float, Polygon]:
    rectangle = poly.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)[:-1]
    edges: list[tuple[float, float, float]] = []
    for first, second in zip(coords, coords[1:] + coords[:1]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        edges.append((math.hypot(dx, dy), dx, dy))
    length, dx, dy = max(edges, key=lambda row: row[0])
    angle = math.degrees(math.atan2(dy, dx))
    local = affinity.rotate(poly, -angle, origin=poly.centroid, use_radians=False)
    min_x, min_y, max_x, max_y = local.bounds
    long_span = max_x - min_x
    short_span = max_y - min_y
    if short_span > long_span:
        angle += 90.0
        local = affinity.rotate(poly, -angle, origin=poly.centroid, use_radians=False)
        min_x, min_y, max_x, max_y = local.bounds
        long_span = max_x - min_x
        short_span = max_y - min_y
    return angle, min_x, min_y, max_x, max_y, local


def _orthogonal_ratio(poly: Polygon, angle_deg: float) -> tuple[float, int]:
    coords = list(poly.exterior.coords)[:-1]
    aligned = 0.0
    total = 0.0
    directions: list[float] = []
    for first, second in zip(coords, coords[1:] + coords[:1]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = math.hypot(dx, dy)
        if length <= EPS:
            continue
        total += length
        local_angle = (math.degrees(math.atan2(dy, dx)) - angle_deg) % 180.0
        distance_to_axis = min(abs(local_angle), abs(local_angle - 90.0), abs(local_angle - 180.0))
        if distance_to_axis <= 8.0:
            aligned += length
        directions.append(round(local_angle / 10.0) * 10.0)
    return aligned / max(total, EPS), len(set(directions))


def _geometry_parts(geometry: Any) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [item for item in geometry.geoms if isinstance(item, Polygon)]
    return []


def _void_signatures(local: Polygon) -> list[dict[str, Any]]:
    min_x, min_y, max_x, max_y = local.bounds
    span = max(max_x - min_x, max_y - min_y, 1.0)
    tolerance = max(1.0e-5 * span, 1.0e-5)
    frame = box(min_x, min_y, max_x, max_y)
    void = frame.difference(local)
    minimum_area = max(local.area * 0.003, span * span * 1.0e-6)
    rows: list[dict[str, Any]] = []
    for component in _geometry_parts(void):
        if component.area <= minimum_area:
            continue
        bounds = component.bounds
        sides: list[str] = []
        if bounds[0] <= min_x + tolerance:
            sides.append("left")
        if bounds[2] >= max_x - tolerance:
            sides.append("right")
        if bounds[1] <= min_y + tolerance:
            sides.append("bottom")
        if bounds[3] >= max_y - tolerance:
            sides.append("top")
        rows.append({
            "area": round(component.area, 4),
            "areaRatio": round(component.area / max(frame.area, EPS), 5),
            "touchSides": sides,
            "bounds": [round(value, 4) for value in bounds],
            "centroid": [round(component.centroid.x, 4), round(component.centroid.y, 4)],
        })
    return sorted(rows, key=lambda item: float(item["area"]), reverse=True)


def _single_corridor_profile(local: Polygon, sample_count: int = 41) -> dict[str, Any]:
    """Describe whether a concave plan remains one continuous strip.

    Long pits with local widenings have reflex vertices but every station along
    the principal axis still cuts one continuous excavation interval.  Treating
    them as generic L/U/T shapes disables valid terminal braces and creates
    dense transition stations.  This profile separates a stepped strip from a
    branched or re-entrant corridor.
    """
    min_x, min_y, max_x, max_y = local.bounds
    span = max(max_x - min_x, EPS)
    pad = max(max_y - min_y, 1.0)
    widths: list[float] = []
    centers: list[float] = []
    single = 0
    valid = 0
    for index in range(sample_count):
        x = min_x + (index + 0.5) * span / sample_count
        intersection = local.intersection(LineString([(x, min_y - pad), (x, max_y + pad)]))
        parts: list[LineString] = []
        if isinstance(intersection, LineString):
            parts = [intersection]
        elif isinstance(intersection, MultiLineString):
            parts = [part for part in intersection.geoms if part.length > EPS]
        elif isinstance(intersection, GeometryCollection):
            parts = [part for part in intersection.geoms if isinstance(part, LineString) and part.length > EPS]
        if not parts:
            continue
        valid += 1
        if len(parts) != 1:
            continue
        part = parts[0]
        ys = [float(coord[1]) for coord in part.coords]
        if not ys:
            continue
        y0, y1 = min(ys), max(ys)
        width = y1 - y0
        if width <= EPS:
            continue
        single += 1
        widths.append(width)
        centers.append(0.5 * (y0 + y1))
    ratio = single / max(valid, 1)
    mean_width = sum(widths) / max(len(widths), 1)
    width_variation = (max(widths) - min(widths)) / max(mean_width, EPS) if widths else 0.0
    center_drift = (max(centers) - min(centers)) / max(mean_width, EPS) if centers else 1.0
    return {
        "sampleCount": sample_count,
        "validSampleCount": valid,
        "singleIntervalRatio": round(ratio, 4),
        "meanWidthM": round(mean_width, 4),
        "minimumWidthM": round(min(widths), 4) if widths else 0.0,
        "maximumWidthM": round(max(widths), 4) if widths else 0.0,
        "widthVariationRatio": round(width_variation, 4),
        "centerlineDriftRatio": round(center_drift, 4),
        "singleCorridor": bool(ratio >= 0.92 and center_drift <= 0.22),
    }


def _orthogonal_archetype(voids: list[dict[str, Any]], reflex_count: int) -> str:
    if not voids:
        return "orthogonal_convex"
    side_sets = [set(item.get("touchSides") or []) for item in voids]
    if len(voids) == 1:
        sides = side_sets[0]
        if len(sides) >= 2:
            adjacent = any(pair <= sides for pair in (
                {"left", "top"}, {"left", "bottom"}, {"right", "top"}, {"right", "bottom"}
            ))
            if adjacent:
                return "l_shape"
        if len(sides) == 1:
            side = next(iter(sides))
            return "c_shape" if side in {"left", "right"} else "u_shape"
        return "single_notch_shape"
    if len(voids) == 2:
        first, second = side_sets
        single_sides = [next(iter(item)) for item in side_sets if len(item) == 1]
        if len(single_sides) == 2:
            if set(single_sides) in ({"top", "bottom"}, {"left", "right"}):
                return "h_shape"
        common = first.intersection(second)
        if common and all(len(item) >= 2 for item in side_sets):
            return "t_shape"
        if not common and all(len(item) >= 2 for item in side_sets):
            return "z_shape"
        return "branched_orthogonal_shape"
    if reflex_count >= 4:
        return "multi_step_or_comb_shape"
    return "stepped_orthogonal_shape"


def _parallel(first: tuple[float, float], second: tuple[float, float], tolerance_deg: float = 8.0) -> bool:
    a = math.degrees(math.atan2(first[1], first[0])) % 180.0
    b = math.degrees(math.atan2(second[1], second[0])) % 180.0
    delta = abs(a - b)
    delta = min(delta, 180.0 - delta)
    return delta <= tolerance_deg


def _quadrilateral_archetype(poly: Polygon, rectangularity: float, orthogonal_ratio: float) -> str:
    coords = list(poly.exterior.coords)[:-1]
    if len(coords) != 4:
        return "convex_polygon"
    vectors = [
        (coords[(index + 1) % 4][0] - coords[index][0], coords[(index + 1) % 4][1] - coords[index][1])
        for index in range(4)
    ]
    parallel_pairs = int(_parallel(vectors[0], vectors[2])) + int(_parallel(vectors[1], vectors[3]))
    if rectangularity >= 0.96 and orthogonal_ratio >= 0.90:
        return "rectangle"
    if parallel_pairs == 2:
        return "parallelogram"
    if parallel_pairs == 1:
        return "trapezoid"
    return "irregular_quadrilateral"


def _grid_decomposition(local: Polygon, angle_deg: float, origin: tuple[float, float]) -> list[dict[str, Any]]:
    coords = list(local.exterior.coords)[:-1]
    xs = sorted({round(float(x), 7) for x, _ in coords})
    ys = sorted({round(float(y), 7) for _, y in coords})
    if len(xs) < 2 or len(ys) < 2 or len(xs) * len(ys) > 900:
        return []
    row_runs: list[tuple[int, int, int, float, float]] = []
    for row in range(len(ys) - 1):
        occupied: list[int] = []
        y_mid = 0.5 * (ys[row] + ys[row + 1])
        for col in range(len(xs) - 1):
            x_mid = 0.5 * (xs[col] + xs[col + 1])
            if local.covers(Point(x_mid, y_mid)):
                occupied.append(col)
        if not occupied:
            continue
        start = previous = occupied[0]
        for col in occupied[1:] + [occupied[-1] + 2]:
            if col == previous + 1:
                previous = col
                continue
            row_runs.append((row, start, previous + 1, ys[row], ys[row + 1]))
            start = previous = col
    active: dict[tuple[int, int], dict[str, Any]] = {}
    rectangles: list[dict[str, Any]] = []
    for row, start, end, y0, y1 in row_runs:
        key = (start, end)
        current = active.get(key)
        if current and int(current["lastRow"]) == row - 1:
            current["y1"] = y1
            current["lastRow"] = row
        else:
            if current:
                rectangles.append(current)
            active[key] = {"start": start, "end": end, "x0": xs[start], "x1": xs[end], "y0": y0, "y1": y1, "lastRow": row}
        for other_key in list(active):
            if other_key != key and int(active[other_key]["lastRow"]) < row - 1:
                rectangles.append(active.pop(other_key))
    rectangles.extend(active.values())
    output: list[dict[str, Any]] = []
    ox, oy = origin
    for index, item in enumerate(sorted(rectangles, key=lambda row: -((row["x1"] - row["x0"]) * (row["y1"] - row["y0"])))):
        width = float(item["x1"] - item["x0"])
        height = float(item["y1"] - item["y0"])
        if width * height <= max(local.area * 0.002, 0.1):
            continue
        local_corners = [
            (item["x0"], item["y0"]), (item["x1"], item["y0"]),
            (item["x1"], item["y1"]), (item["x0"], item["y1"]),
        ]
        global_corners = []
        radians = math.radians(angle_deg)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        for x, y in local_corners:
            dx, dy = x - ox, y - oy
            global_corners.append({
                "x": round(ox + dx * cos_a - dy * sin_a, 4),
                "y": round(oy + dx * sin_a + dy * cos_a, 4),
            })
        output.append({
            "zoneId": f"Z{index + 1}",
            "localBounds": [round(float(item[key]), 4) for key in ("x0", "y0", "x1", "y1")],
            "corners": global_corners,
            "lengthM": round(max(width, height), 3),
            "widthM": round(min(width, height), 3),
            "areaM2": round(width * height, 3),
            "preferredStrutDirection": "local_short_axis" if width >= height else "local_long_axis",
            "recommendedFamily": "direct_wall_to_wall",
        })
    return output


def _strategy(archetype: str, aspect: float, short_span: float, compactness: float) -> dict[str, Any]:
    automatic = {
        "slender_rectangle": ("short_span_direct_grid_with_terminal_parallel_braces", ["direct_grid", "hybrid_diagonal"]),
        "rectangle": ("principal_axis_direct_grid", ["direct_grid", "hybrid_diagonal"]),
        "trapezoid": ("visibility_oblique_wall_pair_grid", ["direct_grid", "hybrid_diagonal"]),
        "parallelogram": ("principal_axis_oblique_direct_grid", ["direct_grid", "hybrid_diagonal"]),
        "irregular_quadrilateral": ("visibility_wall_pair_grid", ["direct_grid"]),
        "elongated_convex_polygon": ("visibility_wall_pair_grid", ["direct_grid", "hybrid_diagonal"]),
        "elongated_stepped_strip": ("adaptive_short_span_grid_with_terminal_parallel_braces", ["direct_grid", "hybrid_diagonal"]),
    }
    if archetype in automatic:
        primary, families = automatic[archetype]
        return {
            "capability": "automatic",
            "primarySystem": primary,
            "supportedTopologyFamilies": families,
            "alternativeSystems": ["center_island", "ring_truss"] if short_span > 30.0 else [],
            "junctionTreatment": "independent_wall_nodes",
        }
    if archetype in {"near_square_rectangle", "compact_convex_polygon", "convex_polygon", "triangle"}:
        return {
            "capability": "automatic_ring_subject_to_full_check",
            "primarySystem": "ring_radial",
            "supportedTopologyFamilies": ["ring_radial"],
            "alternativeSystems": ["explicit_two_way_frame", "center_island"],
            "junctionTreatment": "closed_inner_ring",
        }
    if archetype in {"circle", "ellipse", "regular_multisided_shaft"}:
        return {
            "capability": "automatic_ring_subject_to_full_check",
            "primarySystem": "ring_radial",
            "supportedTopologyFamilies": ["ring_radial"],
            "alternativeSystems": ["double_wall_ring", "radial_servo_struts"],
            "junctionTreatment": "polygonal_or_curved_ring",
        }
    concave_plans: dict[str, tuple[str, list[str], str]] = {
        "l_shape": ("two_corridor_zones_with_elbow_ring", ["zoned_direct"], "elbow_transfer_ring_or_partition_wall"),
        "u_shape": ("three_zone_direct_with_center_island", ["zoned_direct"], "central_island_or_crosshead_ring"),
        "c_shape": ("three_zone_direct_with_open_side_logistics", ["zoned_direct"], "local_ring_at_web_and_open_side_control"),
        "t_shape": ("three_arm_zoning_with_junction_ring", ["zoned_direct"], "central_transfer_ring_or_explicit_frame"),
        "z_shape": ("staged_partitioned_zones", ["zoned_direct"], "two_transition_transfer_zones"),
        "h_shape": ("multi_ring_or_two_center_islands", ["zoned_direct"], "dual_junction_rings"),
        "stepped_orthogonal_shape": ("stepped_visibility_zones", ["zoned_direct"], "transition_ring_or_partition"),
        "multi_step_or_comb_shape": ("multi_zone_partitioned_excavation", ["zoned_direct"], "multiple_transfer_zones"),
        "branched_orthogonal_shape": ("branched_partitioned_excavation", ["zoned_direct"], "branch_junction_frame"),
        "single_notch_shape": ("notch_partitioned_direct_support", ["zoned_direct"], "notch_head_transfer_member"),
        "general_concave_polygon": ("visibility_decomposition_with_ring_nodes", ["zoned_direct"], "engineer_defined_transfer_zones"),
    }
    if archetype in concave_plans:
        primary, families, junction = concave_plans[archetype]
        return {
            "capability": "zoned_preliminary_then_controlled_check",
            "primarySystem": primary,
            "supportedTopologyFamilies": families,
            "alternativeSystems": ["multi_ring", "center_island", "partitioned_excavation", "explicit_space_frame"],
            "junctionTreatment": junction,
        }
    return {
        "capability": "manual_system_selection",
        "primarySystem": "engineer_selected",
        "supportedTopologyFamilies": ["zoned_direct"],
        "alternativeSystems": ["ring_truss", "center_island", "partitioned_excavation", "explicit_space_frame"],
        "junctionTreatment": "manual",
    }



def _engineering_scheme(archetype: str) -> dict[str, Any]:
    key = archetype.replace("_with_center_island", "")
    schemes: dict[str, dict[str, Any]] = {
        "elongated_stepped_strip": {
            "name": "变宽长条形短跨对撑+端部平行角撑",
            "zoning": "沿局部长轴识别连续单走廊和宽度台阶；场区按短跨布置直撑，真实端墙采用独立墙节点的平行角撑族。",
            "layoutRules": ["支撑站位按局部宽度自适应", "台阶两侧最多各设一个过渡站位", "端部保留角撑作用区", "每根角撑两端落墙"],
            "forbidden": ["每个轮廓顶点两侧重复加密", "端部用密集短跨直撑代替角撑", "角撑共用墙节点或止于其他支撑"],
            "calculationModel": "墙—围檩—短跨轴压撑+端部平行角撑",
            "construction": "按宽度分区控制预加轴力，端部角撑先形成闭合传力路径。",
        },
        "slender_rectangle": {
            "name": "短跨直对撑+端部平行角撑",
            "zoning": "沿长轴按3-6m分仓；两端短墙按围檩允许跨反算平行角撑数量。",
            "layoutRules": ["主支撑沿短跨", "角撑两端独立落墙", "端墙中部保留独立节点净距", "长向设置温度与预加轴力分区"],
            "forbidden": ["端墙短撑接主支撑跨中", "扇形共节点角撑", "用纵向主支撑承担横向集中力"],
            "calculationModel": "墙-围檩-轴压支撑-立柱分阶段耦合",
            "construction": "按长向分区开挖与安装，保留连续出土通道。",
        },
        "rectangle": {
            "name": "主轴短跨对撑体系",
            "zoning": "按最小旋转外接矩形识别长短轴，沿长轴分仓。",
            "layoutRules": ["短跨传力", "凸角必要时设平行角撑", "支撑站位避让柱网/坡道"],
            "forbidden": ["依赖全局X/Y误判方向", "对称性优先于障碍和传力路径"],
            "calculationModel": "轴压支撑+连续围檩",
            "construction": "顺长轴流水施工。",
        },
        "near_square_rectangle": {
            "name": "闭合内环梁+径向支撑",
            "zoning": "外围围檩按目标支点间距布置径向支撑，中心保留闭合内环和出土空间。",
            "layoutRules": ["环梁闭合", "径向杆墙上节点独立", "开口处设置局部加强与传力闭合"],
            "forbidden": ["一向直撑覆盖全部墙面", "无弯剪模型的支撑中部T/Y节点"],
            "calculationModel": "外围围檩-径向杆-闭合环梁耦合",
            "construction": "环梁分段浇筑并形成闭合后进入下一开挖阶段。",
        },
        "trapezoid": {
            "name": "可见墙面对斜向直撑",
            "zoning": "以平行墙对为主，非平行端墙通过独立墙-墙斜撑或局部环梁闭合。",
            "layoutRules": ["支撑接近两端墙法向", "斜撑全线位于坑内", "端点独立落围檩"],
            "forbidden": ["多根斜撑汇聚同一端点", "切向擦墙连接"],
            "calculationModel": "斜向轴压杆+围檩节点反力",
            "construction": "先形成端部闭合单元，再施工中部对撑。",
        },
        "parallelogram": {
            "name": "局部主轴斜向对撑",
            "zoning": "沿斜交短跨布置平行对撑。",
            "layoutRules": ["使用局部轴系", "保持平行支撑族", "端点按墙面链距排序"],
            "forbidden": ["按全局坐标水平/竖直布置"],
            "calculationModel": "斜交墙-围檩-支撑耦合",
            "construction": "斜向支撑分仓应与吊装通道协调。",
        },
        "triangle": {
            "name": "内环/中心节点径向体系",
            "zoning": "三边围檩通过径向构件传力至闭合内环，避免三根杆直接汇于单点。",
            "layoutRules": ["闭合内环", "每边多个独立径向节点", "尖角局部围檩加强"],
            "forbidden": ["所有支撑汇聚单一中心点", "尖角扇形拥挤"],
            "calculationModel": "环梁轴力-弯矩+径向杆",
            "construction": "尖角区先完成围檩加厚与节点预埋。",
        },
        "circle": {
            "name": "环梁/圆环支撑",
            "zoning": "利用闭合环向受压；必要时设置少量径向撑和中心岛。",
            "layoutRules": ["环向连续", "径向对称", "开口局部闭合"],
            "forbidden": ["用矩形网格强行拟合圆坑"],
            "calculationModel": "环向轴力、弯矩与径向支撑耦合",
            "construction": "环梁闭合强度达到要求后继续开挖。",
        },
        "ellipse": {
            "name": "椭圆/多边形环梁+径向支撑",
            "zoning": "长轴端与曲率变化区加密径向支撑。",
            "layoutRules": ["按曲率和围檩跨加密", "保持内环相似形", "长轴端节点加强"],
            "forbidden": ["等角度但不等围檩跨的机械布点"],
            "calculationModel": "非均匀环梁+径向杆",
            "construction": "长短轴区域分批张拉/预加轴力。",
        },
        "regular_multisided_shaft": {
            "name": "多边形内环+径向支撑",
            "zoning": "每个边段按围檩跨布置径向支撑。",
            "layoutRules": ["多边形内环闭合", "边中径向传力", "转角节点加强"],
            "forbidden": ["跨越多边形中心的任意弦撑"],
            "calculationModel": "多边形环梁框架",
            "construction": "环梁分段连接必须形成可靠闭合。",
        },
        "l_shape": {
            "name": "双走廊分区对撑+肘部传力环",
            "zoning": "两个矩形走廊分别沿短跨布撑；凹角肘部设置局部闭合环、分隔墙或专项框架。",
            "layoutRules": ["两翼支撑方向独立", "凹角设置加强区", "肘部传力构件闭合后再开挖下一分区"],
            "forbidden": ["任意斜撑跨凹口", "两方向支撑直接在跨中相交", "凹角扇形补撑"],
            "calculationModel": "分区墙-撑模型+肘部环/框架子模型",
            "construction": "优先分区开挖，控制两翼不同步卸荷。",
        },
        "u_shape": {
            "name": "三走廊分区对撑+中心岛/横向环梁",
            "zoning": "两侧翼和底部横廊分别布置短跨对撑；开口内部采用中心岛或横向闭合环。",
            "layoutRules": ["翼部支撑镜像", "开口侧保持物流通道", "两凹角设置独立转接区"],
            "forbidden": ["跨越U形空口的直撑", "用一组斜撑同时服务两凹角"],
            "calculationModel": "多分区支撑+双转接环",
            "construction": "先施工底部横廊和转接区，再推进两翼。",
        },
        "c_shape": {
            "name": "三分区对撑+开口侧施工通道",
            "zoning": "上下翼及腹板分区，开口侧避免布置阻断出土的闭合直撑。",
            "layoutRules": ["腹板区形成主传力带", "开口上下端设置局部环/斜撑", "保持开口物流"],
            "forbidden": ["从开口一端拉斜撑穿越整个凹口"],
            "calculationModel": "分区轴压体系+腹板转接构件",
            "construction": "利用开口侧作为出土通道并后封闭。",
        },
        "t_shape": {
            "name": "三臂分区对撑+中心转接环",
            "zoning": "横翼和竖翼分别按短跨布撑，在三臂交汇处设置闭合转接环或空间框架。",
            "layoutRules": ["三臂独立分仓", "交汇区节点不依赖单根主撑", "施工阶段对称推进"],
            "forbidden": ["三方向杆件汇聚到一个支撑跨中点"],
            "calculationModel": "三分区+转接环/空间框架",
            "construction": "交汇区先形成高刚度节点，再分臂开挖。",
        },
        "z_shape": {
            "name": "错台分区对撑+两处过渡框",
            "zoning": "按错台切成连续矩形区，每处折转设置独立传力过渡区。",
            "layoutRules": ["每段沿局部短跨", "过渡区避免支撑穿越", "分阶段激活相邻区"],
            "forbidden": ["一根超长斜撑贯穿两个折转"],
            "calculationModel": "分段模型+过渡区子结构",
            "construction": "沿Z形路径分段推进，控制偏载。",
        },
        "h_shape": {
            "name": "多环/双中心岛分区体系",
            "zoning": "两侧竖廊和中部横廊分别布置，两个交汇区采用局部环或双中心岛。",
            "layoutRules": ["两个转接区独立", "中部横廊连续", "施工分区避免同时卸荷"],
            "forbidden": ["用单一中心节点承担两个交汇区"],
            "calculationModel": "多环耦合或空间框架",
            "construction": "至少划分三个施工区。",
        },
        "general_concave_polygon": {
            "name": "可见性分解+多环/分区体系",
            "zoning": "基于可见墙对和凸分解形成子区，凹角/分叉处设置明确转接构件。",
            "layoutRules": ["支撑线全程位于坑内", "两端近法向承压", "转接节点具有独立计算模型"],
            "forbidden": ["任意角度射线补齐", "跨凹口", "切向落墙"],
            "calculationModel": "子区模型+转接子结构+整体阶段耦合",
            "construction": "按几何子区和周边风险分区开挖。",
        },
    }
    fallback = schemes.get("general_concave_polygon") if key.endswith("shape") else {
        "name": "可见墙对支撑/环撑比选",
        "zoning": "按凸分解、墙面对和施工通道确定。",
        "layoutRules": ["传力路径连续", "零非法穿越", "节点可构造"],
        "forbidden": ["几何补丁代替受力体系"],
        "calculationModel": "与选定体系一致的分阶段模型",
        "construction": "根据分区和出土组织专项设计。",
    }
    return schemes.get(key, fallback)

def classify_excavation_plan(points: Iterable[Point2D], *, local_pit_count: int = 0, has_center_island: bool = False) -> dict[str, Any]:
    pts = _dedup(points)
    poly, warnings = _polygon(pts)
    if poly is None:
        return {
            "classification": "invalid_outline",
            "archetype": "invalid_outline",
            "capability": "blocked",
            "warnings": warnings,
            "supportedTopologyFamilies": [],
            "designZones": [],
        }
    angle, min_x, min_y, max_x, max_y, local = _minimum_rectangle_frame(poly)
    long_span, short_span = max_x - min_x, max_y - min_y
    aspect = long_span / max(short_span, EPS)
    area = poly.area
    perimeter = poly.length
    circularity = 4.0 * math.pi * area / max(perimeter * perimeter, EPS)
    rectangularity = area / max(poly.minimum_rotated_rectangle.area, EPS)
    convexity = area / max(poly.convex_hull.area, EPS)
    reflex = _reflex_count(pts)
    orthogonal_ratio, direction_count = _orthogonal_ratio(poly, angle)
    orthogonal = orthogonal_ratio >= 0.90
    voids = _void_signatures(local) if reflex else []
    corridor_profile = _single_corridor_profile(local) if reflex and orthogonal and aspect >= 2.20 else {}

    vertex_count = len(pts)
    if reflex and bool(corridor_profile.get("singleCorridor")):
        archetype = "elongated_stepped_strip"
    elif reflex:
        archetype = _orthogonal_archetype(voids, reflex) if orthogonal else "general_concave_polygon"
    elif vertex_count == 3:
        archetype = "triangle"
    elif circularity >= 0.82 and vertex_count >= 8 and aspect <= 1.20:
        archetype = "circle"
    elif circularity >= 0.62 and vertex_count >= 8 and aspect > 1.20:
        archetype = "ellipse"
    elif vertex_count == 4:
        quad = _quadrilateral_archetype(poly, rectangularity, orthogonal_ratio)
        if quad == "rectangle":
            if aspect >= 2.20:
                archetype = "slender_rectangle"
            elif aspect <= 1.35:
                archetype = "near_square_rectangle"
            else:
                archetype = "rectangle"
        else:
            archetype = quad
    elif circularity >= 0.72 and aspect <= 1.25 and vertex_count >= 6:
        archetype = "regular_multisided_shaft"
    elif aspect >= 2.20:
        archetype = "elongated_convex_polygon"
    elif rectangularity >= 0.72 and convexity >= 0.98:
        archetype = "compact_convex_polygon"
    else:
        archetype = "convex_polygon"

    if has_center_island:
        archetype = f"{archetype}_with_center_island"

    strategy_key = archetype.replace("_with_center_island", "")
    strategy = _strategy(strategy_key, aspect, short_span, rectangularity)
    if has_center_island:
        strategy = {
            **strategy,
            "capability": "automatic_ring_subject_to_full_check",
            "primarySystem": "center_island_ring_radial",
            "supportedTopologyFamilies": ["ring_radial"],
            "junctionTreatment": "center_island_ring",
        }

    zones = _grid_decomposition(local, angle, (poly.centroid.x, poly.centroid.y)) if orthogonal and reflex else []
    risk_flags: list[str] = []
    if local_pit_count:
        risk_flags.append("local_pits_require_stage_and_local_wall_check")
    if reflex:
        risk_flags.extend(["reentrant_corner_concentration", "multiple_support_directions", "junction_transfer_required"])
    if aspect >= 4.0:
        risk_flags.append("very_long_plan_temperature_and_preload_zoning")
    if short_span >= 40.0:
        risk_flags.append("large_clear_span_consider_ring_or_center_island")
    if rectangularity < 0.60:
        risk_flags.append("low_rectangularity_requires_visibility_decomposition")

    classification_alias = {
        "slender_rectangle": "slender_quadrilateral",
        "near_square_rectangle": "near_square_quadrilateral",
        "rectangle": "rotated_or_orthogonal_quadrilateral",
        "l_shape": "orthogonal_concave_corridor",
        "u_shape": "orthogonal_concave_corridor",
        "c_shape": "orthogonal_concave_corridor",
        "t_shape": "orthogonal_concave_corridor",
        "z_shape": "orthogonal_concave_corridor",
        "h_shape": "orthogonal_concave_corridor",
        "stepped_orthogonal_shape": "orthogonal_concave_corridor",
        "multi_step_or_comb_shape": "orthogonal_concave_corridor",
        "branched_orthogonal_shape": "orthogonal_concave_corridor",
        "single_notch_shape": "orthogonal_concave_corridor",
        "general_concave_polygon": "general_concave_polygon",
        "circle": "circular_or_multisided_shaft",
        "ellipse": "circular_or_multisided_shaft",
        "regular_multisided_shaft": "circular_or_multisided_shaft",
        "elongated_convex_polygon": "slender_convex_polygon",
        "elongated_stepped_strip": "slender_stepped_strip",
    }.get(strategy_key, "general_convex_polygon")

    if strategy_key in {"rectangle", "slender_rectangle", "near_square_rectangle"}:
        recognition_confidence = min(0.99, 0.55 + 0.25 * rectangularity + 0.20 * orthogonal_ratio)
    elif strategy_key in {"circle", "ellipse", "regular_multisided_shaft"}:
        recognition_confidence = min(0.97, 0.45 + 0.45 * circularity + 0.10 * convexity)
    elif strategy_key == "elongated_stepped_strip":
        recognition_confidence = min(0.98, 0.60 + 0.20 * orthogonal_ratio + 0.18 * float(corridor_profile.get("singleIntervalRatio") or 0.0))
    elif reflex and orthogonal:
        recognition_confidence = min(0.95, 0.50 + 0.30 * orthogonal_ratio + 0.03 * min(len(voids), 5))
    elif convexity >= 0.98:
        recognition_confidence = min(0.90, 0.50 + 0.30 * convexity + 0.10 * rectangularity)
    else:
        recognition_confidence = 0.62
    alternatives: list[str] = []
    if 1.25 <= aspect <= 1.45 and strategy_key in {"rectangle", "near_square_rectangle"}:
        alternatives = ["near_square_rectangle", "rectangle"]
    elif strategy_key in {"circle", "ellipse"} and 1.12 <= aspect <= 1.30:
        alternatives = ["circle", "ellipse", "regular_multisided_shaft"]
    elif strategy_key in {"trapezoid", "parallelogram", "irregular_quadrilateral"} and rectangularity < 0.85:
        alternatives = ["trapezoid", "parallelogram", "irregular_quadrilateral"]
    elif strategy_key in {"single_notch_shape", "stepped_orthogonal_shape", "multi_step_or_comb_shape"}:
        alternatives = ["single_notch_shape", "stepped_orthogonal_shape", "multi_step_or_comb_shape"]

    return {
        "classification": classification_alias,
        "archetype": archetype,
        "recognitionConfidence": round(recognition_confidence, 4),
        "ambiguousAlternatives": alternatives,
        "vertexCount": vertex_count,
        "concaveVertexCount": reflex,
        "principalAxisMethod": "minimum_rotated_rectangle",
        "principalAxisRotationDeg": round(angle, 3),
        "longSpanM": round(long_span, 3),
        "shortSpanM": round(short_span, 3),
        "aspectRatio": round(aspect, 4),
        "areaM2": round(area, 3),
        "perimeterM": round(perimeter, 3),
        "circularity": round(circularity, 4),
        "rectangularity": round(rectangularity, 4),
        "convexityRatio": round(convexity, 4),
        "orthogonalEdgeRatio": round(orthogonal_ratio, 4),
        "orthogonalPlan": orthogonal,
        "edgeDirectionClusterCount": direction_count,
        "corridorProfile": corridor_profile,
        "slenderPlan": aspect >= 2.20,
        "nearSquarePlan": aspect <= 1.35,
        "circularShaftLike": strategy_key in {"circle", "regular_multisided_shaft"},
        "ellipticalShaftLike": strategy_key == "ellipse",
        "voidSignatures": voids,
        "designZones": zones,
        "zoneCount": len(zones),
        "localPitCount": int(local_pit_count),
        "hasCenterIsland": bool(has_center_island),
        "riskFlags": risk_flags,
        "warnings": warnings,
        "recommendedTopology": strategy["primarySystem"],
        "engineeringScheme": _engineering_scheme(archetype),
        "designWorkflow": [
            "outline_validation_and_coordinate_normalization",
            "shape_archetype_and_design_zone_recognition",
            "support_system_family_selection",
            "wall_pair_visibility_and_load_path_generation",
            "intersection_wale_bay_and_constructability_gate",
            "stagewise_global_calculation_and_independent_check",
            "detailing_ifc_drawing_and_controlled_release",
        ],
        **strategy,
    }
