from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean
from types import SimpleNamespace

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
from app.services.support_layout import (
    SupportLayoutConfig,
    make_column_elements,
    make_ring_beams,
    make_support_elements,
    make_support_wale_nodes,
    plan_shape_diagnostics,
    repair_concave_return_supports,
    repair_wale_support_bays,
    support_layout_summary,
)

ANGLE_TOL_RAD = math.radians(2.0)
COLLINEAR_TOL = 0.10
DIAGONAL_BRACE_ASPECT_RATIO = 1.8


def _partition_layout_messages(messages: list[str]) -> tuple[list[str], list[str]]:
    """Separate successful algorithm actions from unresolved engineering risks.

    Layout generation emits both audit notes (what the algorithm successfully
    changed) and genuine unresolved warnings. Keeping both in one warning list
    made a healthy auto-layout look defective and obscured the actionable items.
    """
    evidence_prefixes = (
        "已将", "已根据", "已增加", "已因", "检测到", "其中",
        "支撑布置自动修复器已移动", "支撑布置为", "主对撑按", "已跳过",
    )
    evidence: list[str] = []
    unresolved: list[str] = []
    for message in messages:
        text = str(message).strip()
        if not text:
            continue
        is_unresolved = any(token in text for token in ("未能", "不足", "小于", "大于", "需人工复核", "需调整", "无法"))
        if text.startswith(evidence_prefixes) and not is_unresolved:
            evidence.append(text)
        elif text.startswith("已跳过") and "候选支撑" in text:
            # Candidate deletion is an auditable topology action. Final coverage,
            # redundancy and load-path checks remain responsible for warning/fail.
            evidence.append(text)
        else:
            unresolved.append(text)
    return list(dict.fromkeys(evidence)), list(dict.fromkeys(unresolved))


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


def _construction_panelization(segment, *, target_length_m: float = 6.0, minimum_length_m: float = 3.0, maximum_length_m: float = 7.0) -> list[dict[str, object]]:
    """Split a calculation wall face into constructible diaphragm-wall panels.

    The structural analysis keeps one wall strip per excavation segment for
    traceability.  Construction, reinforcement cages and IFC/CAD detailing use
    this panel schedule so a 100 m wall is never represented as one impossible
    cage.  The last two panels are balanced to avoid a very short remainder.
    """
    length = max(float(segment.length), 0.0)
    if length <= 1.0e-9:
        return []
    target = max(minimum_length_m, min(maximum_length_m, float(target_length_m)))
    count = max(1, int(math.ceil(length / target)))
    panel_length = length / count
    while count > 1 and panel_length < minimum_length_m:
        count -= 1
        panel_length = length / count
    while panel_length > maximum_length_m and count < 200:
        count += 1
        panel_length = length / count
    dx = float(segment.end.x - segment.start.x)
    dy = float(segment.end.y - segment.start.y)
    rows: list[dict[str, object]] = []
    for idx in range(count):
        c0 = length * idx / count
        c1 = length * (idx + 1) / count
        t0 = c0 / length
        t1 = c1 / length
        rows.append({
            "panelIndex": idx + 1,
            "panelCode": f"{segment.name}-P{idx + 1:02d}",
            "startChainageM": round(c0, 3),
            "endChainageM": round(c1, 3),
            "lengthM": round(c1 - c0, 3),
            "start": {"x": round(float(segment.start.x) + dx * t0, 4), "y": round(float(segment.start.y) + dy * t0, 4)},
            "end": {"x": round(float(segment.start.x) + dx * t1, 4), "y": round(float(segment.start.y) + dy * t1, 4)},
            "cageCount": 1,
            "jointType": "project_specific",
            "liftingReviewRequired": True,
        })
    return rows


def auto_diaphragm_wall(project_excavation, existing_system: RetainingSystem | None = None, settings=None) -> RetainingSystem:
    excavation = project_excavation
    depth = excavation.depth
    thickness, warnings = select_wall_thickness(depth)
    embedment = select_embedment_depth(depth)
    bottom = excavation.bottom_elevation - embedment
    system = existing_system or RetainingSystem()
    existing_by_segment = {
        wall.segment_id: wall
        for wall in (system.diaphragm_walls or [])
    }
    panels: list[DiaphragmWallPanel] = []
    faces = _design_faces_by_segment(excavation)
    for segment in excavation.segments:
        face = faces.get(segment.id)
        existing = existing_by_segment.get(segment.id)
        selected_bottom = round(bottom, 3)
        selected_source = "enterprise_initial"
        selected_locked = False
        source_bottom = None
        selected_thickness = thickness
        selected_concrete = "C35"
        selected_rebar = "HRB400"
        selected_reinforcement = diaphragm_wall_reinforcement(thickness, 0.0)
        if existing is not None:
            # Re-running one-click wall generation must not erase a deeper
            # imported/manual/previously stability-designed toe.  This was the
            # cause of the Fengshou project returning 20 identical embedment
            # failures after an otherwise valid support candidate was adopted.
            if (
                bool(getattr(existing, "bottom_elevation_locked", False))
                or float(existing.bottom_elevation) < selected_bottom - 1.0e-6
                or str(getattr(existing, "bottom_elevation_source", "unknown")) in {"imported", "manual", "auto_stability"}
            ):
                selected_bottom = float(existing.bottom_elevation)
                selected_source = str(getattr(existing, "bottom_elevation_source", "unknown") or "unknown")
                selected_locked = bool(getattr(existing, "bottom_elevation_locked", False))
                source_bottom = getattr(existing, "source_bottom_elevation", None)
            selected_thickness = max(float(thickness), float(existing.thickness))
            selected_concrete = existing.concrete_grade or "C35"
            selected_rebar = existing.rebar_grade or "HRB400"
            selected_reinforcement = list(existing.reinforcement or diaphragm_wall_reinforcement(selected_thickness, 0.0))
        target_panel = float(getattr(settings, "wall_panel_target_length_m", 6.0) if settings is not None else 6.0)
        minimum_panel = float(getattr(settings, "wall_panel_min_length_m", 3.0) if settings is not None else 3.0)
        maximum_panel = float(getattr(settings, "wall_panel_max_length_m", 7.0) if settings is not None else 7.0)
        construction_panels = _construction_panelization(
            segment,
            target_length_m=target_panel,
            minimum_length_m=minimum_panel,
            maximum_length_m=maximum_panel,
        )
        if existing is not None and getattr(existing, "construction_panels", None):
            construction_panels = list(existing.construction_panels)
        panel = DiaphragmWallPanel(
            segment_id=segment.id,
            panel_code=f"DW-{segment.name}-001",
            axis=Polyline2D(points=[segment.start, segment.end], closed=False),
            design_face_code=face.code if face else segment.name,
            design_length=face.length if face else segment.length,
            face_segment_ids=face.segment_ids if face else [segment.id],
            thickness=selected_thickness,
            top_elevation=excavation.top_elevation,
            bottom_elevation=round(selected_bottom, 3),
            bottom_elevation_source=selected_source,
            bottom_elevation_locked=selected_locked,
            source_bottom_elevation=source_bottom,
            toe_profile_status=("reference_locked" if selected_locked or selected_source in {"imported", "manual"} else "uniform"),
            construction_panels=construction_panels,
            concrete_grade=selected_concrete,
            rebar_grade=selected_rebar,
            reinforcement=selected_reinforcement,
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
    system.warnings = list(dict.fromkeys(system.warnings + warnings))
    system.layout_summary.setdefault("designNotes", []).append(
        "地连墙墙厚和墙深属于企业初选值；最终结果需经分阶段计算、稳定验算和注册专业工程师审签。"
    )
    system.layout_summary["wallGeometryProvenance"] = {
        "preservedExistingToeCount": sum(
            1
            for panel in panels
            if panel.bottom_elevation_source in {"imported", "manual", "auto_stability"}
        ),
        "lockedToeCount": sum(1 for panel in panels if panel.bottom_elevation_locked),
        "commonBottomElevationM": min((panel.bottom_elevation for panel in panels), default=None),
        "constructionPanelCount": sum(len(panel.construction_panels) for panel in panels),
        "wallToeProfileType": "uniform" if len({round(float(panel.bottom_elevation), 3) for panel in panels}) <= 1 else "zoned",
        "policy": "never shorten a deeper imported/manual/stability-designed wall toe during regeneration",
    }
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
    offsets = [max(3.5, min(5.0, short_span * 0.22)), max(6.5, min(8.0, short_span * 0.38))]
    lines: list[tuple[Point2D, Point2D]] = []
    for offset in sorted(set(round(value, 3) for value in offsets)):
        lines.extend([
            (Point2D(x=min_x + offset, y=min_y), Point2D(x=min_x, y=min_y + offset)),
            (Point2D(x=max_x - offset, y=min_y), Point2D(x=max_x, y=min_y + offset)),
            (Point2D(x=max_x - offset, y=max_y), Point2D(x=max_x, y=max_y - offset)),
            (Point2D(x=min_x + offset, y=max_y), Point2D(x=min_x, y=max_y - offset)),
        ])
    return lines


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
        max_wale_support_bay_m=float(getattr(settings, "max_wale_support_bay_m", 7.5)),
        hard_max_wale_support_bay_m=float(getattr(settings, "hard_max_wale_support_bay_m", 9.0)),
        diagonal_brace_min_wall_length_m=float(getattr(settings, "diagonal_brace_min_wall_length_m", 18.0)),
        corner_diagonal_min_offset_m=float(getattr(settings, "corner_diagonal_min_offset_m", 3.5)),
        corner_diagonal_max_offset_m=float(getattr(settings, "corner_diagonal_max_offset_m", 8.0)),
        corner_diagonal_max_wall_fraction=float(getattr(settings, "corner_diagonal_max_wall_fraction", 0.40)),
        corner_diagonal_family_count=int(getattr(settings, "corner_diagonal_family_count", 2)),
        corner_diagonal_family_spacing_m=float(getattr(settings, "corner_diagonal_family_spacing_m", 3.0)),
        corner_diagonal_parallel_tolerance_deg=float(getattr(settings, "corner_diagonal_parallel_tolerance_deg", 5.0)),
        prefer_diagonal_braces=bool(getattr(settings, "prefer_diagonal_braces", True)),
        allow_wale_repair_t_y_nodes=bool(getattr(settings, "allow_wale_repair_t_y_nodes", False)),
        topology_strategy=topology_strategy,
        transition_zone_spacing_factor=float(getattr(settings, "support_transition_zone_spacing_factor", 0.72)),
        transition_zone_influence_m=float(getattr(settings, "support_transition_zone_influence_m", 8.0)),
        support_min_station_separation_m=float(getattr(settings, "support_min_station_separation_m", 2.8)),
        support_level_depths_m=tuple(getattr(settings, "support_level_depths_m", []) or []),
    ).normalized()


def auto_supports(project_excavation, existing_system: RetainingSystem | None = None, layout_config: SupportLayoutConfig | None = None) -> RetainingSystem:
    excavation = project_excavation
    layout_config = (layout_config or SupportLayoutConfig()).normalized()
    warnings: list[str] = []
    explicit_depths = [
        depth for depth in layout_config.support_level_depths_m
        if depth <= max(0.0, excavation.top_elevation - excavation.bottom_elevation - 0.5)
    ]
    if explicit_depths:
        elevations = [round(excavation.top_elevation - depth, 4) for depth in explicit_depths]
        omitted = len(layout_config.support_level_depths_m) - len(explicit_depths)
        if omitted:
            warnings.append(f"已忽略 {omitted} 个超出开挖深度或距离坑底不足 0.5m 的指定支撑深度。")
    else:
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

    # Run the same topology/strength preflight used by optimized candidates on
    # the primary one-click layout.  Previously the raw Step-5 scheme could be
    # saved with oversized wale bays and only repaired when Step 6 started,
    # which made A/B/C cards and the active model disagree.  The retaining
    # system now leaves Step 5 with concave-return coverage and direct wale-bay
    # constraints already resolved whenever a constructible repair exists.
    preflight_project = SimpleNamespace(excavation=excavation, retaining_system=system)
    concave_preflight = repair_concave_return_supports(preflight_project, layout_config)
    wale_preflight = repair_wale_support_bays(preflight_project, layout_config)

    # Algorithm descriptions and successfully applied design actions are evidence,
    # not warnings. Only unresolved geometry/constructability conditions remain in
    # system.warnings and flow into the issue center.
    design_notes = [
        "地连墙墙厚和墙深属于企业初选值；最终结果需经分阶段计算、稳定验算和注册专业工程师审签。",
        "主对撑按短跨方向布置，沿长向按目标间距分仓；凹形基坑通过线-多边形求交避免支撑穿越坑外空区。",
        "凸直角位置按长宽比和基坑尺度自动设置角撑；凹角、坡道、出土口和保护区作为拓扑约束。",
        "支撑轴力按围檩节点反力计算，tributary width 仅作为结果解释和节点分配证据。",
        "长跨支撑按有效无侧向支承长度自动设置临时立柱；已服务长跨不再重复产生几何长度预警。",
    ]
    if explicit_depths:
        design_notes.append(
            "已采用项目明确指定的支撑深度：" + "、".join(f"{value:.2f}m" for value in explicit_depths) + "（相对坑顶向下）。"
        )
        if explicit_depths[0] <= 0.05:
            design_notes.append("第一道支撑位于坑顶标高附近；应核实其为冠梁/顶撑还是独立支撑，并复核安装与拆撑工序。")
    if system.ring_beams:
        design_notes.append(f"已生成 {len(system.ring_beams)} 段环梁。")
    if system.support_nodes:
        design_notes.append(f"已生成 {len(system.support_nodes)} 个支撑-围檩节点。")
    if system.columns:
        design_notes.append(f"已自动生成 {len(system.columns)} 个临时立柱点，并记录每根立柱的支撑服务关系。")
    if int(concave_preflight.get("addedSupportCount", 0) or 0):
        design_notes.append(
            f"强度前置拓扑检查已增补 {int(concave_preflight.get('addedSupportCount', 0) or 0)} 根凹形回墙局部支撑。"
        )
    if int(wale_preflight.get("addedSupportCount", 0) or 0):
        design_notes.append(
            f"强度前置围檩检查已增补 {int(wale_preflight.get('addedSupportCount', 0) or 0)} 根局部短对撑/角撑；"
            f"修复后状态 {wale_preflight.get('status', 'unknown')}。"
        )
    layout_evidence, unresolved_layout_warnings = _partition_layout_messages(layout_warnings)
    design_notes.extend(layout_evidence)
    merged_warnings = list(dict.fromkeys(system.warnings + warnings + unresolved_layout_warnings))
    system.warnings = merged_warnings
    previous_layout_summary = dict(system.layout_summary or {})
    system.layout_summary = {
        **previous_layout_summary,
        **support_layout_summary(system.supports, system.columns, system.ring_beams, merged_warnings, config=layout_config),
    }
    system.layout_summary["planShapeDiagnostics"] = plan_shape_diagnostics(list(excavation.outline.points))
    system.layout_summary["designNotes"] = list(dict.fromkeys(design_notes))
    system.layout_summary["warningPolicy"] = "仅保留无法由当前算法闭环处理的工程风险；已自动修复动作进入设计证据。"
    preflight_status = (
        "fail"
        if str(wale_preflight.get("status")) == "fail" or bool(concave_preflight.get("missingFacesAfter"))
        else "warning"
        if str(wale_preflight.get("status")) == "warning"
        else "pass"
    )
    requires_alternative_system = (
        preflight_status == "fail"
        and str(wale_preflight.get("status")) == "fail"
        and not bool(concave_preflight.get("missingFacesAfter"))
    )
    system.layout_summary["strengthTopologyPreflight"] = {
        "executed": True,
        "concaveReturnSupport": concave_preflight,
        "waleSupportBay": wale_preflight,
        "status": preflight_status,
        "calculationReady": preflight_status != "fail",
        "requiresAlternativeSupportSystem": requires_alternative_system,
        "alternativeSupportSystemReason": (
            "直接墙—墙轴压支撑在当前几何中无法同时满足围檩支点间距和零非法交叉；系统已阻断以避免生成支撑中部 T/Y 伪支座。"
            if requires_alternative_system else None
        ),
        "recommendedSupportSystems": (
            ["ring_strut", "central_island", "explicit_two_way_frame"]
            if requires_alternative_system else []
        ),
    }
    if requires_alternative_system:
        system.warnings = list(dict.fromkeys([
            *system.warnings,
            "当前轴压墙—墙支撑拓扑无法闭合全部围檩跨，已安全阻断自动计算；请采用环撑/中心岛，或建立可承担平面内弯剪的显式双向框架模型。",
        ]))
    system.replacement_path = [
        {"step": 1, "name": "底板形成后保留全部内支撑", "action": "bottom_slab_cast", "activeSupportLevels": sorted({s.level_index for s in system.supports})},
        {"step": 2, "name": "地下室结构达到强度后自下而上换撑", "action": "replace_from_lowest_level", "removeOrder": sorted({s.level_index for s in system.supports}, reverse=True)},
        {"step": 3, "name": "最终拆除上部支撑并完成永久结构传力", "action": "final_support_removal", "engineeringReviewRequired": True},
    ]
    return system
