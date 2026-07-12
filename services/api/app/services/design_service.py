from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean

from app.rules.enterprise.preliminary_design_rules import select_embedment_depth, select_wall_thickness, support_elevations
from app.schemas.domain import (
    BeamElement,
    ColumnElement,
    DiaphragmWallPanel,
    MaterialDefinition,
    Point2D,
    Polyline2D,
    RetainingSystem,
    SectionDefinition,
    SupportElement,
    WallDesignResult,
)
from app.services.reinforcement_service import diaphragm_wall_reinforcement, support_reinforcement
from app.services.excavation_service import _unique_polygon_points
from app.services.support_layout import SupportLayoutConfig, make_column_elements, make_ring_beams, make_support_elements, make_support_wale_nodes, support_layout_summary

ANGLE_TOL_RAD = math.radians(2.0)
COLLINEAR_TOL = 0.10
DIAGONAL_BRACE_ASPECT_RATIO = 1.8


@dataclass(frozen=True)
class DesignFace:
    code: str
    segment_ids: list[str]
    length: float


def _beam_axis(start: Point2D, end: Point2D) -> Polyline2D:
    return Polyline2D(points=[start, end], closed=False)


def _make_crown_beams(excavation) -> list[BeamElement]:
    beams: list[BeamElement] = []
    for segment in excavation.segments:
        beams.append(
            BeamElement(
                code=f"CB-{segment.name}",
                axis=_beam_axis(segment.start, segment.end),
                elevation=excavation.top_elevation,
                section=SectionDefinition(width=1.0, height=0.8, name="1000x800 RC crown beam"),
                material=MaterialDefinition(name="Concrete", grade="C35"),
                beam_role="crown_beam",
            )
        )
    return beams


def _make_wale_beams(excavation, elevations: list[float]) -> list[BeamElement]:
    beams: list[BeamElement] = []
    for level_idx, elevation in enumerate(elevations, start=1):
        for segment in excavation.segments:
            beams.append(
                BeamElement(
                    code=f"WB-L{level_idx}-{segment.name}",
                    axis=_beam_axis(segment.start, segment.end),
                    elevation=elevation,
                    section=SectionDefinition(width=0.9, height=0.7, name="900x700 RC wale beam"),
                    material=MaterialDefinition(name="Concrete", grade="C35"),
                    beam_role="wale_beam",
                    support_level=level_idx,
                )
            )
    return beams


def _segment_angle(segment) -> float:
    return math.atan2(segment.end.y - segment.start.y, segment.end.x - segment.start.x)


def _angle_close(a: float, b: float) -> bool:
    # Compare modulo pi, because collinear wall segments can share a design face.
    diff = abs((a - b + math.pi / 2.0) % math.pi - math.pi / 2.0)
    return diff <= ANGLE_TOL_RAD


def _point_line_distance(p: Point2D, a: Point2D, b: Point2D) -> float:
    dx = b.x - a.x
    dy = b.y - a.y
    den = math.hypot(dx, dy)
    if den <= 1e-9:
        return math.hypot(p.x - a.x, p.y - a.y)
    return abs(dy * p.x - dx * p.y + b.x * a.y - b.y * a.x) / den


def _design_faces_by_segment(excavation) -> dict[str, DesignFace]:
    """Group consecutive collinear outline segments into one design face.

    The physical model still keeps each segment/panel for calculation traceability,
    but each segment on the same straight wall receives the same face code and
    unified design length.  This matches common foundation-pit design practice:
    one straight side wall should normally share a governing section/length rather
    than being re-designed independently because of an intermediate drafting node.
    """
    segments = list(excavation.segments)
    if not segments:
        return {}
    groups: list[list] = []
    current = [segments[0]]
    base_start = segments[0].start
    base_end = segments[0].end
    base_angle = _segment_angle(segments[0])
    for segment in segments[1:]:
        if _angle_close(base_angle, _segment_angle(segment)) and _point_line_distance(segment.end, base_start, base_end) <= COLLINEAR_TOL:
            current.append(segment)
            base_end = segment.end
        else:
            groups.append(current)
            current = [segment]
            base_start = segment.start
            base_end = segment.end
            base_angle = _segment_angle(segment)
    # Join first and last groups when a closed polygon starts mid-face.
    if groups and _angle_close(_segment_angle(groups[0][0]), _segment_angle(current[0])):
        if _point_line_distance(groups[0][0].start, current[0].start, current[-1].end) <= COLLINEAR_TOL:
            groups[0] = current + groups[0]
        else:
            groups.append(current)
    else:
        groups.append(current)

    result: dict[str, DesignFace] = {}
    for idx, group in enumerate(groups, start=1):
        code = f"F{idx}"
        length = round(sum(float(s.length) for s in group), 3)
        ids = [s.id for s in group]
        face = DesignFace(code=code, segment_ids=ids, length=length)
        for segment in group:
            result[segment.id] = face
    return result


def auto_diaphragm_wall(project_excavation, existing_system: RetainingSystem | None = None) -> RetainingSystem:
    excavation = project_excavation
    depth = excavation.depth
    thickness, warnings = select_wall_thickness(depth)
    embedment = select_embedment_depth(depth)
    bottom = excavation.bottom_elevation - embedment
    system = existing_system or RetainingSystem()
    panels: list[DiaphragmWallPanel] = []
    faces = _design_faces_by_segment(excavation)
    for segment in excavation.segments:
        face = faces.get(segment.id)
        panel = DiaphragmWallPanel(
            segment_id=segment.id,
            panel_code=f"DW-{segment.name}-001",
            axis=Polyline2D(points=[segment.start, segment.end], closed=False),
            design_face_code=face.code if face else segment.name,
            design_length=face.length if face else segment.length,
            face_segment_ids=face.segment_ids if face else [segment.id],
            thickness=thickness,
            top_elevation=excavation.top_elevation,
            bottom_elevation=round(bottom, 3),
            concrete_grade="C35",
            rebar_grade="HRB400",
            reinforcement=diaphragm_wall_reinforcement(thickness, 0.0),
            design_results=WallDesignResult(
                check_status="manual_review",
                notes=[
                    "墙厚/墙深为企业初选值，计算完成后将由 JGJ120 土压力、弹性地基梁和稳定性子集更新内力包络。",
                    "同一直线墙面按统一设计面长度归组；中间绘图节点不单独改变设计长度。",
                    "最终墙深应通过嵌固深度、整体稳定、坑底隆起、抗渗流、变形控制等验算确定。",
                ],
            ),
        )
        panels.append(panel)
    system.diaphragm_walls = panels
    system.crown_beams = _make_crown_beams(excavation)
    system.warnings = list(dict.fromkeys(system.warnings + warnings + ["地连墙为企业初选 + 规范子集复核工作流结果，需注册岩土/结构工程师复核。"]))
    return system


def _pit_bounds(points: list[Point2D]) -> tuple[float, float, float, float, float, float]:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return min_x, min_y, max_x, max_y, max_x - min_x, max_y - min_y


def _main_short_span_support_lines(points: list[Point2D]) -> list[tuple[Point2D, Point2D]]:
    min_x, min_y, max_x, max_y, span_x, span_y = _pit_bounds(points)
    long_span = max(span_x, span_y)
    line_count = 4 if long_span >= 24 else 2
    lines: list[tuple[Point2D, Point2D]] = []
    if span_x >= span_y:
        # Long direction is X, so struts span in Y: they are shorter and stiffer.
        for i in range(line_count):
            x = min_x + (i + 1) * span_x / (line_count + 1)
            lines.append((Point2D(x=x, y=min_y), Point2D(x=x, y=max_y)))
    else:
        # Long direction is Y, so struts span in X.
        for i in range(line_count):
            y = min_y + (i + 1) * span_y / (line_count + 1)
            lines.append((Point2D(x=min_x, y=y), Point2D(x=max_x, y=y)))
    return lines


def _corner_diagonal_lines(points: list[Point2D]) -> list[tuple[Point2D, Point2D]]:
    min_x, min_y, max_x, max_y, span_x, span_y = _pit_bounds(points)
    short_span = min(span_x, span_y)
    long_span = max(span_x, span_y)
    if short_span <= 1e-9 or long_span / short_span < DIAGONAL_BRACE_ASPECT_RATIO or short_span < 12:
        return []
    offset_long = max(3.0, min(8.0, long_span * 0.12))
    offset_short = max(3.0, min(8.0, short_span * 0.28))
    return [
        (Point2D(x=min_x + offset_long, y=min_y), Point2D(x=min_x, y=min_y + offset_short)),
        (Point2D(x=max_x - offset_long, y=min_y), Point2D(x=max_x, y=min_y + offset_short)),
        (Point2D(x=max_x - offset_long, y=max_y), Point2D(x=max_x, y=max_y - offset_short)),
        (Point2D(x=min_x + offset_long, y=max_y), Point2D(x=min_x, y=max_y - offset_short)),
    ]


def _support_line_for_level(points: list[Point2D], level_idx: int, elevation: float) -> list[tuple[str, Point2D, Point2D]]:
    lines: list[tuple[str, Point2D, Point2D]] = [("main_strut", a, b) for a, b in _main_short_span_support_lines(points)]
    lines.extend(("corner_diagonal", a, b) for a, b in _corner_diagonal_lines(points))
    return lines


def _pit_centroid(points: list[Point2D]) -> Point2D:
    return Point2D(x=mean([p.x for p in points]), y=mean([p.y for p in points]))


def support_layout_config_from_settings(settings, *, topology_strategy: str = "balanced_grid", target_spacing: float | None = None, column_span: float | None = None) -> SupportLayoutConfig:
    return SupportLayoutConfig(
        target_main_support_spacing_m=float(target_spacing if target_spacing is not None else getattr(settings, "default_support_spacing", 5.0)),
        column_max_unbraced_span_m=float(column_span if column_span is not None else 18.0),
        support_wall_clearance_m=float(getattr(settings, "support_wall_clearance_m", 1.0)),
        max_direct_strut_span_m=float(getattr(settings, "max_direct_strut_span_m", 24.0)),
        diagonal_brace_min_wall_length_m=float(getattr(settings, "diagonal_brace_min_wall_length_m", 18.0)),
        prefer_diagonal_braces=bool(getattr(settings, "prefer_diagonal_braces", True)),
        topology_strategy=topology_strategy,
    ).normalized()


def auto_supports(project_excavation, existing_system: RetainingSystem | None = None, layout_config: SupportLayoutConfig | None = None) -> RetainingSystem:
    excavation = project_excavation
    layout_config = (layout_config or SupportLayoutConfig()).normalized()
    elevations, warnings = support_elevations(excavation.top_elevation, excavation.bottom_elevation)
    system = existing_system or RetainingSystem()

    supports, layout_warnings = make_support_elements(excavation, elevations, config=layout_config)
    for support in supports:
        support.reinforcement = support_reinforcement(
            support.section.width or 1.6,
            support.section.height or 1.6,
            support.design_axial_force,
            support.material.grade if support.material.name == "Concrete" else "C35",
        )

    system.supports = supports
    system.wale_beams = _make_wale_beams(excavation, elevations)
    system.ring_beams = make_ring_beams(excavation, elevations)
    system.columns = make_column_elements(excavation, supports, max_unbraced_span_m=layout_config.column_max_unbraced_span_m)
    system.support_nodes = make_support_wale_nodes(system.supports, system.wale_beams)

    warnings_out = [
        "支撑布置为拓扑化自动建议：端点吸附、围檩节点、立柱桩、换撑和施工空间需人工复核。",
        "主对撑按短跨方向布置，沿长向按目标间距分仓；凹形基坑通过线-多边形求交避免支撑穿越坑外空区。",
        "凸直角位置按长宽比和基坑尺度自动设置角撑；凹角、坡道、出土口和保护区不自动跨越布撑。",
        "支撑轴力已预留按墙面 tributary width 关联计算，不再采用同层全局均分。",
        "临时立柱按主对撑/环撑跨长自动布置，计算阶段优先按立柱桩承载力子集进行复核。",
    ]
    if system.ring_beams:
        warnings_out.append(f"已生成 {len(system.ring_beams)} 段环梁，当前采用中心岛/环撑体系原型。")
    if system.support_nodes:
        warnings_out.append(f"已生成 {len(system.support_nodes)} 个支撑-围檩节点，计算阶段将更新端部承压和节点配筋筛查。")
    if system.columns:
        warnings_out.append(f"已自动生成 {len(system.columns)} 个临时立柱候选点，位置来自主对撑/环撑跨中或分跨点。")
    merged_warnings = list(dict.fromkeys(system.warnings + warnings + layout_warnings + warnings_out))
    system.warnings = merged_warnings
    system.layout_summary = support_layout_summary(system.supports, system.columns, system.ring_beams, merged_warnings, config=layout_config)
    system.replacement_path = [
        {"step": 1, "name": "底板形成后保留全部内支撑", "action": "bottom_slab_cast", "activeSupportLevels": sorted({s.level_index for s in system.supports})},
        {"step": 2, "name": "地下室结构达到强度后自下而上换撑", "action": "replace_from_lowest_level", "removeOrder": sorted({s.level_index for s in system.supports}, reverse=True)},
        {"step": 3, "name": "最终拆除上部支撑并完成永久结构传力", "action": "final_support_removal", "engineeringReviewRequired": True},
    ]
    return system
