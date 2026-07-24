from __future__ import annotations

import math
from typing import Any

from shapely.geometry import LineString, MultiPolygon, Point as ShapelyPoint, Polygon as ShapelyPolygon

from app.schemas.domain import Point2D

# V3.70 templates are grouped by actual structural topology.  The three legacy
# offset-only templates remain readable for project migration, while the default
# comparison can now include a closed ring, a hub frame and a ring-chord frame.
TRANSFER_TEMPLATES: dict[str, dict[str, Any]] = {
    "compact_elbow_ring": {
        "label": "紧凑型异形闭合内环梁",
        "topologyClass": "closed_ring",
        "offsetRatio": 0.13,
        "minimumOffsetM": 2.8,
        "maximumOffsetM": 4.5,
    },
    "balanced_elbow_ring": {
        "label": "均衡型异形闭合内环梁",
        "topologyClass": "closed_ring",
        "offsetRatio": 0.18,
        "minimumOffsetM": 3.6,
        "maximumOffsetM": 6.0,
    },
    "extended_elbow_ring": {
        "label": "延伸型异形闭合内环梁",
        "topologyClass": "closed_ring",
        "offsetRatio": 0.23,
        "minimumOffsetM": 4.4,
        "maximumOffsetM": 7.5,
    },
    "junction_hub_frame": {
        "label": "凹角中心枢纽转接框架",
        "topologyClass": "junction_hub_frame",
        "offsetRatio": 0.17,
        "minimumOffsetM": 3.4,
        "maximumOffsetM": 6.2,
        "maximumHubArms": 6,
    },
    "ring_chord_frame": {
        "label": "闭合环梁—内弦杆框架",
        "topologyClass": "ring_chord_frame",
        "offsetRatio": 0.20,
        "minimumOffsetM": 3.8,
        "maximumOffsetM": 7.0,
        "maximumChords": 3,
    },
}

DEFAULT_TRANSFER_TEMPLATES = [
    "compact_elbow_ring",
    "junction_hub_frame",
    "ring_chord_frame",
]


def transfer_template_ids() -> set[str]:
    return set(TRANSFER_TEMPLATES)


def transfer_topology_class(template_id: str) -> str:
    return str((TRANSFER_TEMPLATES.get(str(template_id or "")) or {}).get("topologyClass") or "none")


def _signed_area(points: list[Point2D]) -> float:
    return 0.5 * sum(
        points[index].x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * points[index].y
        for index in range(len(points))
    )


def _cross(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x)


def reflex_vertices(points: list[Point2D]) -> list[dict[str, Any]]:
    if len(points) < 4:
        return []
    orientation = 1.0 if _signed_area(points) > 0 else -1.0
    rows: list[dict[str, Any]] = []
    for index, current in enumerate(points):
        previous = points[(index - 1) % len(points)]
        following = points[(index + 1) % len(points)]
        if orientation * _cross(previous, current, following) < -1e-8:
            rows.append({"index": index, "point": current})
    return rows


def _remove_short_and_collinear_vertices(points: list[Point2D], minimum_length_m: float = 1.2) -> list[Point2D]:
    """Regularise a buffered polygon into constructible beam segments."""
    if len(points) < 4:
        return points
    cleaned = list(points)
    for _ in range(4):
        changed = False
        output: list[Point2D] = []
        for index, current in enumerate(cleaned):
            previous = cleaned[(index - 1) % len(cleaned)]
            following = cleaned[(index + 1) % len(cleaned)]
            l1 = math.hypot(current.x - previous.x, current.y - previous.y)
            l2 = math.hypot(following.x - current.x, following.y - current.y)
            cross = abs((current.x - previous.x) * (following.y - current.y) - (current.y - previous.y) * (following.x - current.x))
            scale = max(l1 * l2, 1e-9)
            near_collinear = cross / scale < 1.0e-4
            if len(cleaned) > 4 and (l1 < minimum_length_m or l2 < minimum_length_m or near_collinear):
                changed = True
                continue
            output.append(current)
        if len(output) < 4:
            break
        cleaned = output
        if not changed:
            break
    return cleaned


def concave_ring_points(
    points: list[Point2D],
    template_id: str,
    *,
    scale: float = 1.0,
) -> tuple[list[Point2D], dict[str, Any]]:
    template = TRANSFER_TEMPLATES.get(str(template_id or ""))
    if not template or len(points) < 4:
        return [], {"status": "missing_template", "reason": "concave_transfer_template_missing"}
    polygon = ShapelyPolygon([(float(point.x), float(point.y)) for point in points])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area <= 1e-6:
        return [], {"status": "invalid", "reason": "invalid_excavation_polygon"}
    min_x, min_y, max_x, max_y = polygon.bounds
    short_span = max(1.0, min(max_x - min_x, max_y - min_y))
    preferred = short_span * float(template["offsetRatio"]) * max(0.65, min(1.5, float(scale)))
    preferred = max(float(template["minimumOffsetM"]), min(float(template["maximumOffsetM"]), preferred))

    chosen: list[Point2D] | None = None
    chosen_offset = None
    attempts: list[dict[str, Any]] = []
    for factor in (1.0, 0.88, 0.76, 0.64, 0.54):
        offset = max(2.2, preferred * factor)
        inner = polygon.buffer(-offset, join_style=2)
        components = len(inner.geoms) if isinstance(inner, MultiPolygon) else (1 if not inner.is_empty else 0)
        attempts.append({"offsetM": round(offset, 3), "componentCount": components})
        if inner.is_empty or isinstance(inner, MultiPolygon) or not isinstance(inner, ShapelyPolygon):
            continue
        if not inner.is_valid or inner.area <= max(4.0, polygon.area * 0.025):
            continue
        coords = list(inner.exterior.coords)[:-1]
        if len(coords) < 4:
            continue
        raw = [Point2D(x=round(float(x), 4), y=round(float(y), 4)) for x, y in coords]
        regularised = _remove_short_and_collinear_vertices(raw)
        if len(regularised) < 4:
            continue
        chosen = regularised
        chosen_offset = offset
        break
    if not chosen:
        return [], {
            "status": "blocked",
            "reason": "inner_ring_split_or_insufficient_clearance",
            "attempts": attempts,
        }
    segment_lengths = [
        math.hypot(chosen[(i + 1) % len(chosen)].x - chosen[i].x, chosen[(i + 1) % len(chosen)].y - chosen[i].y)
        for i in range(len(chosen))
    ]
    return chosen, {
        "status": "generated",
        "templateId": template_id,
        "templateLabel": template["label"],
        "topologyClass": template["topologyClass"],
        "offsetM": round(float(chosen_offset or 0.0), 3),
        "vertexCount": len(chosen),
        "minimumSegmentLengthM": round(min(segment_lengths), 3),
        "maximumSegmentLengthM": round(max(segment_lengths), 3),
        "attempts": attempts,
    }


def _visible_segment(a: Point2D, b: Point2D, polygon: ShapelyPolygon, tolerance: float = 0.03) -> bool:
    line = LineString([(a.x, a.y), (b.x, b.y)])
    return line.length > 1.0 and polygon.buffer(tolerance).covers(line)


def transfer_beam_segments(ring_points: list[Point2D], template_id: str) -> list[dict[str, Any]]:
    """Return perimeter and optional internal frame segments for one support level."""
    if len(ring_points) < 4:
        return []
    template = TRANSFER_TEMPLATES.get(str(template_id or "")) or {}
    topology = str(template.get("topologyClass") or "closed_ring")
    polygon = ShapelyPolygon([(p.x, p.y) for p in ring_points])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    rows: list[dict[str, Any]] = []
    for index, (a, b) in enumerate(zip(ring_points, ring_points[1:] + ring_points[:1]), start=1):
        rows.append({
            "segmentIndex": index,
            "start": a,
            "end": b,
            "role": "transfer_ring_beam",
            "memberClass": "perimeter_ring",
        })

    if topology == "junction_hub_frame":
        representative = polygon.representative_point()
        hub = Point2D(x=round(float(representative.x), 4), y=round(float(representative.y), 4))
        # Prefer long, angularly distributed arms.  Visibility is checked inside
        # the actual inner polygon so no chord crosses a re-entrant void.
        candidates = sorted(
            [p for p in ring_points if _visible_segment(p, hub, polygon)],
            key=lambda p: math.atan2(p.y - hub.y, p.x - hub.x),
        )
        maximum = max(3, int(template.get("maximumHubArms") or 6))
        if len(candidates) > maximum:
            step = len(candidates) / maximum
            candidates = [candidates[min(len(candidates) - 1, int(round(i * step)))] for i in range(maximum)]
        seen: set[tuple[float, float]] = set()
        for point in candidates:
            key = (point.x, point.y)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "segmentIndex": len(rows) + 1,
                "start": point,
                "end": hub,
                "role": "transfer_frame_beam",
                "memberClass": "hub_arm",
            })
    elif topology == "ring_chord_frame":
        candidates: list[tuple[float, int, int]] = []
        for i, a in enumerate(ring_points):
            for j in range(i + 2, len(ring_points)):
                if i == 0 and j == len(ring_points) - 1:
                    continue
                b = ring_points[j]
                length = math.hypot(b.x - a.x, b.y - a.y)
                if _visible_segment(a, b, polygon) and length > 0.35 * max(polygon.bounds[2] - polygon.bounds[0], polygon.bounds[3] - polygon.bounds[1]):
                    candidates.append((length, i, j))
        used_vertices: set[int] = set()
        for _length, i, j in sorted(candidates, reverse=True):
            if i in used_vertices or j in used_vertices:
                continue
            rows.append({
                "segmentIndex": len(rows) + 1,
                "start": ring_points[i],
                "end": ring_points[j],
                "role": "transfer_brace",
                "memberClass": "internal_chord",
            })
            used_vertices.update({i, j})
            if len([row for row in rows if row["role"] == "transfer_brace"]) >= int(template.get("maximumChords") or 3):
                break
    return rows


def _point_key(point: Any, digits: int = 4) -> tuple[float, float]:
    return (round(float(point.x), digits), round(float(point.y), digits))


def _is_perimeter_beam(beam: Any) -> bool:
    role = str(getattr(beam, "beam_role", "") or "")
    return role in {"ring_beam", "transfer_ring_beam"} or (
        str(getattr(beam, "code", "")).startswith("TR-") and "-F" not in str(getattr(beam, "code", "")) and "-B" not in str(getattr(beam, "code", ""))
    )


def _ring_closure_audit(beams: list[Any], level_index: int) -> dict[str, Any]:
    level_beams = [
        beam for beam in beams
        if int(getattr(beam, "support_level", 0) or 0) == level_index
        and _is_perimeter_beam(beam)
        and len(getattr(getattr(beam, "axis", None), "points", None) or []) >= 2
    ]
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = {}
    for beam in level_beams:
        points = list(beam.axis.points or [])
        start = _point_key(points[0])
        end = _point_key(points[-1])
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
    if not adjacency:
        return {
            "levelIndex": level_index,
            "closed": False,
            "beamCount": len(level_beams),
            "nodeCount": 0,
            "componentCount": 0,
            "invalidDegreeNodeCount": 0,
        }
    unseen = set(adjacency)
    component_count = 0
    while unseen:
        component_count += 1
        stack = [unseen.pop()]
        while stack:
            node = stack.pop()
            for neighbour in adjacency.get(node, set()):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)
    invalid_degree = [node for node, neighbours in adjacency.items() if len(neighbours) != 2]
    closed = len(level_beams) >= 3 and component_count == 1 and not invalid_degree and len(level_beams) == len(adjacency)
    return {
        "levelIndex": level_index,
        "closed": closed,
        "beamCount": len(level_beams),
        "nodeCount": len(adjacency),
        "componentCount": component_count,
        "invalidDegreeNodeCount": len(invalid_degree),
        "invalidDegreeNodes": [{"x": node[0], "y": node[1]} for node in invalid_degree[:12]],
    }


def audit_concave_transfer_system(
    excavation: Any,
    elevations: list[float],
    *,
    template_id: str,
    ring_beams: list[Any],
    supports: list[Any],
    ring_generation: dict[str, Any] | None = None,
    frame_analysis: dict[str, Any] | None = None,
    construction_stage_closed: bool = False,
) -> dict[str, Any]:
    points = list(getattr(getattr(excavation, "outline", None), "points", None) or [])
    reflex = reflex_vertices(points)
    required = bool(reflex)
    template = TRANSFER_TEMPLATES.get(str(template_id or ""))
    transfer_beams = [beam for beam in ring_beams or [] if str(getattr(beam, "code", "")).startswith("TR-") or str(getattr(beam, "beam_role", "")).startswith("transfer_")]
    perimeter_beams = [beam for beam in transfer_beams if _is_perimeter_beam(beam)]
    frame_beams = [beam for beam in transfer_beams if not _is_perimeter_beam(beam)]
    radial = [support for support in supports or [] if getattr(support, "support_role", None) == "ring_strut"]
    segment_codes = {str(getattr(segment, "name", "")) for segment in (getattr(excavation, "segments", None) or [])}
    covered_faces_by_level: dict[int, set[str]] = {}
    for support in radial:
        level = int(getattr(support, "level_index", 0) or 0)
        covered_faces_by_level.setdefault(level, set())
        for face in (getattr(support, "start_face_code", None), getattr(support, "end_face_code", None)):
            if face:
                covered_faces_by_level[level].add(str(face))
    face_coverage_complete = bool(elevations) and all(
        segment_codes.issubset(covered_faces_by_level.get(level_index, set()))
        for level_index in range(1, len(elevations) + 1)
    )
    ring_closure_by_level = [_ring_closure_audit(perimeter_beams, level_index) for level_index in range(1, len(elevations) + 1)]
    ring_closed = bool(ring_closure_by_level) and all(row["closed"] for row in ring_closure_by_level)
    geometry_closed = bool(template) and ring_closed
    load_path_closed = geometry_closed and face_coverage_complete and bool(radial)
    analysis = dict(frame_analysis or {})
    analysis_status = str(analysis.get("status") or "missing")
    structural_model_closed = load_path_closed and analysis_status in {"pass", "warning"} and bool(analysis.get("solvedLevelCount"))
    proxy_calculation_ready = required and structural_model_closed
    formal_calculation_ready = proxy_calculation_ready and bool(construction_stage_closed)
    calculation_ready = proxy_calculation_ready if required else True

    ring_outline: list[dict[str, float]] = []
    if perimeter_beams:
        first_level = min(int(getattr(beam, "support_level", 0) or 0) for beam in perimeter_beams)
        level_beams = [beam for beam in perimeter_beams if int(getattr(beam, "support_level", 0) or 0) == first_level]
        ring_outline = [
            {"x": round(float(beam.axis.points[0].x), 4), "y": round(float(beam.axis.points[0].y), 4)}
            for beam in level_beams if getattr(beam.axis, "points", None)
        ]
    blocking: list[str] = []
    if required and not template:
        blocking.append("concave_transfer_template_missing")
    if required and not geometry_closed:
        blocking.append("closed_inner_ring_not_generated")
    if required and not face_coverage_complete:
        blocking.append("radial_wall_face_coverage_incomplete")
    if required and load_path_closed and not structural_model_closed:
        blocking.append("planar_transfer_frame_analysis_missing_or_failed")
    if required and structural_model_closed and not construction_stage_closed:
        blocking.append("construction_stage_transfer_envelope_not_closed")

    topology_class = str((template or {}).get("topologyClass") or "none")
    readiness = {
        "geometryClosed": geometry_closed if required else True,
        "loadPathClosed": load_path_closed if required else True,
        "structuralModelClosed": structural_model_closed if required else True,
        "constructionStageClosed": bool(construction_stage_closed) if required else True,
        "proxyCalculationReady": proxy_calculation_ready if required else True,
        "formalCalculationReady": formal_calculation_ready if required else True,
    }
    return {
        "required": required,
        "templateId": template_id,
        "templateLabel": template.get("label") if template else None,
        "topologyClass": topology_class,
        "modelClass": "closed_concave_planar_frame_with_radial_struts",
        "status": "warning" if calculation_ready else ("pass" if not required else "fail"),
        "calculationReady": calculation_ready,
        "proxyCalculationReady": readiness["proxyCalculationReady"],
        "formalCalculationReady": readiness["formalCalculationReady"],
        "officialIssueReady": False if required else True,
        "readiness": readiness,
        "junctionCount": len(reflex),
        "coveredJunctionCount": len(reflex) if load_path_closed else 0,
        "levelCount": len(elevations),
        "beamCount": len(transfer_beams),
        "perimeterBeamCount": len(perimeter_beams),
        "frameBeamCount": len(frame_beams),
        "radialSupportCount": len(radial),
        "coveredFaceCountByLevel": {str(level): len(faces) for level, faces in covered_faces_by_level.items()},
        "coveredFacesByLevel": {str(level): sorted(faces) for level, faces in covered_faces_by_level.items()},
        "requiredFaceCount": len(segment_codes),
        "faceCoverageComplete": face_coverage_complete,
        "ringClosed": ring_closed,
        "ringClosureByLevel": ring_closure_by_level,
        "frameAnalysis": analysis,
        "transferZones": [{
            "id": "TZ-1",
            "type": topology_class,
            "status": "closed" if structural_model_closed else "blocked",
            "outline": ring_outline,
            "reflexVertices": [
                {"x": round(float(row["point"].x), 4), "y": round(float(row["point"].y), 4)}
                for row in reflex
            ],
        }],
        "zoneGraph": {
            "schema": "support-zone-graph-v2",
            "nodes": [
                {"id": "outer_wale", "type": "retaining_boundary"},
                {"id": "transfer_frame", "type": topology_class, "status": "closed" if structural_model_closed else "blocked"},
                *[{"id": f"face:{code}", "type": "wall_face", "faceCode": code} for code in sorted(segment_codes)],
                *[
                    {
                        "id": f"junction:{index + 1}",
                        "type": "reflex_junction",
                        "x": round(float(row["point"].x), 4),
                        "y": round(float(row["point"].y), 4),
                        "status": "covered" if load_path_closed else "blocked",
                    }
                    for index, row in enumerate(reflex)
                ],
            ],
            "edges": [
                {"from": "outer_wale", "to": "transfer_frame", "type": "radial_compression_members", "count": len(radial)},
                *[
                    {
                        "from": f"face:{code}",
                        "to": "transfer_frame",
                        "type": "wall_face_to_transfer_frame",
                        "levelCoverage": [level for level, faces in sorted(covered_faces_by_level.items()) if code in faces],
                    }
                    for code in sorted(segment_codes)
                ],
                *[
                    {"from": f"junction:{index + 1}", "to": "transfer_frame", "type": "junction_transfer_control"}
                    for index, _row in enumerate(reflex)
                ],
            ],
        },
        "ringGeneration": dict(ring_generation or {}),
        "blockingReasons": blocking,
        "requiredReviews": [
            "复核闭合转接梁系的轴力—弯矩—剪力耦合和节点半刚性。",
            "复核径向支撑—转接梁—围檩节点锚固、局部承压和附加钢筋。",
            "按施工阶段核定梁系安装、出土通道、换撑和拆除顺序。",
        ] if required else [],
        "assumptions": [
            "候选筛查和完整计算均采用同一二维平面框架—压杆刚度模型。",
            "正式出图仍需注册工程师复核节点边界、空间偏心、立柱基础和施工组织。",
        ] if required else [],
    }


def make_transfer_frame_columns(
    excavation: Any,
    ring_beams: list[Any],
    supports: list[Any],
    *,
    maximum_vertical_unbraced_span_m: float = 12.0,
) -> list[Any]:
    """Create vertically continuous column candidates at transfer-frame nodes.

    One plan column is shared by all support levels.  Perimeter corners, hub
    nodes, chord endpoints and long transfer-beam spans are considered.  The
    resulting locations are later checked against obstacles and pile capacity by
    the normal calculation chain.
    """
    from app.schemas.domain import ColumnElement, MaterialDefinition, SectionDefinition

    first_level = min((int(getattr(beam, "support_level", 0) or 0) for beam in ring_beams or [] if int(getattr(beam, "support_level", 0) or 0) > 0), default=0)
    beams = [beam for beam in ring_beams or [] if int(getattr(beam, "support_level", 0) or 0) == first_level]
    if not beams:
        return []
    maximum_span = max(6.0, min(20.0, float(maximum_vertical_unbraced_span_m)))
    points: dict[tuple[float, float], dict[str, Any]] = {}
    for beam in beams:
        a, b = beam.axis.points[0], beam.axis.points[-1]
        length = math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))
        count = max(0, int(math.ceil(length / maximum_span)) - 1)
        local = [a, b] + [
            Point2D(
                x=round(float(a.x) + (float(b.x) - float(a.x)) * index / (count + 1), 3),
                y=round(float(a.y) + (float(b.y) - float(a.y)) * index / (count + 1), 3),
            )
            for index in range(1, count + 1)
        ]
        for point in local:
            key = _point_key(point, digits=3)
            points.setdefault(key, {"point": point, "beamCodes": set(), "supportCodes": set()})["beamCodes"].add(str(beam.code))
    for support in supports or []:
        if str(getattr(support, "support_role", "")) != "ring_strut":
            continue
        for key, row in points.items():
            point = row["point"]
            if min(
                math.hypot(float(support.start.x) - float(point.x), float(support.start.y) - float(point.y)),
                math.hypot(float(support.end.x) - float(point.x), float(support.end.y) - float(point.y)),
            ) <= 0.45:
                row["supportCodes"].add(str(support.code))
    top = float(getattr(excavation, "top_elevation", 0.0) or 0.0)
    bottom = float(getattr(excavation, "bottom_elevation", -10.0) or -10.0)
    columns = []
    for index, row in enumerate(sorted(points.values(), key=lambda item: (item["point"].x, item["point"].y)), start=1):
        columns.append(ColumnElement(
            code=f"TFC-{index:03d}",
            location=row["point"],
            top_elevation=top,
            bottom_elevation=bottom - 8.0,
            section=SectionDefinition(diameter=0.8, width=0.8, height=0.8, name="D800 transfer-frame lattice column with bored pile"),
            material=MaterialDefinition(name="Steel", grade="Q355"),
            support_codes=sorted(row["supportCodes"]),
            service_area_note=(
                "异形转接梁系立柱：设置于环梁角点、框架枢纽/弦杆节点及长梁段控制点；"
                f"关联梁 {', '.join(sorted(row['beamCodes']))}。各层位置上下对齐，正式设计需与永久柱网、坡道和出土通道协调。"
            ),
        ))
    return columns
