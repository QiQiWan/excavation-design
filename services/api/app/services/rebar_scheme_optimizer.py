from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Literal

from app.rules.gb50010.rc_section_rules import (
    as_per_m_for_spacing,
    bar_area,
    check_rc_rectangular_axial_capacity,
    design_rectangular_flexure,
)
from app.rules.gb50010.reinforcement_rules import recommend_bar_spacing
from app.schemas.domain import Project, ReinforcementGroup
from app.services.rebar_constructability import build_rebar_constructability
from app.services.runtime_diagnostics import append_event

RebarMode = Literal["conservative", "balanced", "economic"]

_MODE_FACTORS: dict[str, dict[str, float]] = {
    "conservative": {"demand": 1.20, "spacing": 0.88, "congestion": 1.12},
    "balanced": {"demand": 1.10, "spacing": 1.00, "congestion": 1.00},
    "economic": {"demand": 1.02, "spacing": 1.10, "congestion": 0.92},
}


def _mode(value: str | None) -> RebarMode:
    return value if value in _MODE_FACTORS else "balanced"  # type: ignore[return-value]


def _round_spacing(spacing: float, *, minimum: int = 100, maximum: int = 250) -> int:
    candidates = [100, 120, 125, 140, 150, 160, 180, 200, 225, 250]
    target = min(max(float(spacing), minimum), maximum)
    return min(candidates, key=lambda item: abs(item - target))


def _standard_bar_diameter(value: float, *, minimum: int = 12) -> int:
    catalogue = [12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40]
    allowed = [item for item in catalogue if item >= minimum]
    target = max(float(value), float(minimum))
    return min(allowed, key=lambda item: (abs(item - target), item < target, item))


def _select_wall_bar(required_as: float, mode: RebarMode) -> tuple[int, int, float]:
    dia, spacing, provided = recommend_bar_spacing(required_as, preferred_diameters=(20, 22, 25, 28, 32, 36, 40))
    factor = _MODE_FACTORS[mode]["spacing"]
    spacing = _round_spacing(spacing * factor, minimum=100, maximum=225)
    provided = as_per_m_for_spacing(dia, spacing)
    while provided < required_as and spacing > 100:
        spacing = _round_spacing(spacing - 15, minimum=100, maximum=225)
        provided = as_per_m_for_spacing(dia, spacing)
    if provided < required_as:
        for candidate_dia in (25, 28, 32, 36, 40):
            candidate = as_per_m_for_spacing(candidate_dia, spacing)
            if candidate >= required_as:
                dia, provided = candidate_dia, candidate
                break
    return int(dia), int(spacing), round(float(provided), 2)


def _select_wall_arrangement(required_as: float, mode: RebarMode, *, max_spacing_mm: float = 225.0) -> dict[str, Any]:
    """Select one- or two-layer wall reinforcement without hiding excess demand."""
    spacing_factor = _MODE_FACTORS[mode]["spacing"]
    candidates: list[dict[str, Any]] = []
    for layers in (1, 2):
        for dia in (20, 22, 25, 28, 32, 36, 40):
            for base_spacing in (225, 200, 180, 160, 150, 140, 125, 120, 100):
                spacing = _round_spacing(base_spacing * spacing_factor, minimum=100, maximum=min(225, int(max_spacing_mm)))
                if spacing > float(max_spacing_mm) + 1e-6:
                    continue
                provided = layers * as_per_m_for_spacing(dia, spacing)
                clear = spacing - dia
                if provided + 1e-6 < required_as or clear < max(30.0, float(dia)):
                    continue
                congestion = layers * dia / max(spacing, 1.0)
                score = layers * 100.0 + congestion * 10.0 + provided / max(required_as, 1.0)
                candidates.append({
                    "layerCount": layers,
                    "diameterMm": dia,
                    "spacingMm": spacing,
                    "providedAsMm2PerM": round(provided, 2),
                    "clearSpacingMm": round(clear, 1),
                    "arrangementType": "single_layer_per_face" if layers == 1 else "double_layer_per_face",
                    "mechanicalCouplerRequired": layers > 1 or dia >= 36,
                    "score": score,
                })
    if candidates:
        return min(candidates, key=lambda item: float(item["score"]))
    max_provided = 2 * as_per_m_for_spacing(40, 100)
    return {
        "layerCount": 2,
        "diameterMm": 40,
        "spacingMm": 100,
        "providedAsMm2PerM": round(max_provided, 2),
        "clearSpacingMm": 60.0,
        "arrangementType": "beyond_verified_catalogue",
        "mechanicalCouplerRequired": True,
        "score": 9999.0,
    }


def _face_matches_group(group: ReinforcementGroup, face: str) -> bool:
    text = f"{group.name} {group.location_description}".lower()
    if face == "inner":
        return any(token in text for token in ("坑内", "inner", "excavation side"))
    return any(token in text for token in ("坑外", "outer", "soil side"))


def _existing_face_rebar_floor(wall: Any, face: str) -> dict[str, float | None]:
    """Return the explicit existing cage floor for one wall face.

    Existing longitudinal cage information is treated as a design decision that
    must not be silently weakened by an optimizer.  Local/additional bars are
    excluded because they do not define the continuous base cage.
    """
    rows: list[tuple[float, float | None, float]] = []
    for group in list(getattr(wall, "reinforcement", []) or []):
        if getattr(group, "bar_type", None) != "longitudinal" or not _face_matches_group(group, face):
            continue
        if getattr(group, "check_status", None) == "fail":
            continue
        area = float(getattr(group, "area_per_meter", 0.0) or 0.0)
        spacing = float(getattr(group, "spacing", 0.0) or 0.0) or None
        diameter = float(getattr(group, "diameter", 0.0) or 0.0)
        if area <= 0.0 and spacing and diameter > 0.0:
            area = as_per_m_for_spacing(diameter, spacing)
        if area > 0.0:
            rows.append((area, spacing, diameter))
    if not rows:
        return {"areaMm2PerM": 0.0, "spacingMm": None, "diameterMm": None}
    area, spacing, diameter = max(rows, key=lambda item: item[0])
    return {"areaMm2PerM": round(area, 2), "spacingMm": spacing, "diameterMm": diameter}


def _target_wall_utilization(project: Project, mode: RebarMode) -> float:
    configured = float(getattr(project.design_settings, "wall_rebar_target_utilization", 0.88) or 0.88)
    mode_target = {"conservative": 0.80, "balanced": configured, "economic": 0.95}[mode]
    return min(max(mode_target, 0.65), 0.98)


def _latest_result(project: Project):
    return project.calculation_results[-1] if project.calculation_results else None


def _wall_envelope(project: Project) -> dict[str, list[dict[str, Any]]]:
    result = _latest_result(project)
    grouped: dict[str, dict[float, dict[str, Any]]] = defaultdict(dict)
    if not result:
        return {}
    for stage in result.stage_results:
        force = stage.wall_internal_force
        if not force:
            continue
        for point in force.points:
            key = round(float(point.elevation), 2)
            row = grouped[force.segment_id].setdefault(
                key,
                {
                    "elevation": float(point.elevation),
                    "depth": float(point.depth),
                    "maxPositiveMoment": 0.0,
                    "maxNegativeMoment": 0.0,
                    "maxAbsMoment": 0.0,
                    "maxAbsShear": 0.0,
                    "maxAbsDisplacement": 0.0,
                    "governingStageId": stage.stage_id,
                },
            )
            moment = float(point.moment or 0.0)
            shear = float(point.shear or 0.0)
            displacement = float(point.displacement or 0.0)
            row["maxPositiveMoment"] = max(float(row["maxPositiveMoment"]), moment)
            row["maxNegativeMoment"] = min(float(row["maxNegativeMoment"]), moment)
            if abs(moment) >= float(row["maxAbsMoment"]):
                row["maxAbsMoment"] = abs(moment)
                row["governingStageId"] = stage.stage_id
            row["maxAbsShear"] = max(float(row["maxAbsShear"]), abs(shear))
            row["maxAbsDisplacement"] = max(float(row["maxAbsDisplacement"]), abs(displacement))
    return {segment_id: sorted(rows.values(), key=lambda item: float(item["elevation"]), reverse=True) for segment_id, rows in grouped.items()}


def _unique_elevations(values: list[float], tolerance: float = 0.35) -> list[float]:
    out: list[float] = []
    for value in sorted(values, reverse=True):
        if not out or abs(out[-1] - value) > tolerance:
            out.append(value)
    return out


def _zone_kind(top: float, bottom: float, support_elevations: list[float], excavation_bottom: float, wall_bottom: float) -> str:
    mid = (top + bottom) / 2.0
    if any(bottom - 0.2 <= elevation <= top + 0.2 for elevation in support_elevations):
        return "support_node_zone"
    if bottom <= excavation_bottom <= top or abs(mid - excavation_bottom) <= 1.5:
        return "excavation_transition_zone"
    if abs(mid - wall_bottom) <= 2.0 or bottom <= wall_bottom + 3.0:
        return "toe_zone"
    if top >= -0.5:
        return "crown_zone"
    return "field_zone"


def _zone_points(points: list[dict[str, Any]], top: float, bottom: float) -> list[dict[str, Any]]:
    selected = [item for item in points if bottom - 1e-6 <= float(item["elevation"]) <= top + 1e-6]
    if selected:
        return selected
    if not points:
        return []
    mid = (top + bottom) / 2.0
    return [min(points, key=lambda item: abs(float(item["elevation"]) - mid))]


def _wall_zone_scheme(project: Project, mode: RebarMode) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ret = project.retaining_system
    if not ret:
        return [], []
    envelope = _wall_envelope(project)
    support_elevations = _unique_elevations([float(item.elevation) for item in ret.supports])
    excavation_bottom = float(project.excavation.bottom_elevation) if project.excavation else -12.0
    factor = _MODE_FACTORS[mode]["demand"]
    zones: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for wall in ret.diaphragm_walls:
        points = envelope.get(wall.segment_id, [])
        global_design_moment = float(wall.design_results.max_moment_design or wall.design_results.max_moment or 0.0) if wall.design_results else 0.0
        segment_max_moment = max((float(item.get("maxAbsMoment") or 0.0) for item in points), default=0.0)
        segment_max_shear = max((float(item.get("maxAbsShear") or 0.0) for item in points), default=0.0)
        segment_max_displacement = max((float(item.get("maxAbsDisplacement") or 0.0) for item in points), default=0.0)
        envelope_anomaly = bool(points and segment_max_moment <= 1e-6 and global_design_moment > 1e-6)
        if envelope_anomaly:
            checks.append(
                {
                    "checkId": f"RB-ENV-{wall.id}",
                    "category": "calculation_envelope_consistency",
                    "hostId": wall.id,
                    "hostCode": wall.panel_code,
                    "status": "manual_review",
                    "utilization": None,
                    "message": "Stage wall-moment samples are zero while the member design moment is non-zero; zoning uses a documented shear/displacement proxy and requires calculation-result review.",
                }
            )
        boundaries = [float(wall.top_elevation), float(wall.bottom_elevation), excavation_bottom]
        for elevation in support_elevations:
            if wall.bottom_elevation < elevation < wall.top_elevation:
                influence = max(1.2, 1.5 * float(wall.thickness))
                boundaries.extend([min(wall.top_elevation, elevation + influence), max(wall.bottom_elevation, elevation - influence)])
        boundaries = _unique_elevations(boundaries, tolerance=0.25)
        if boundaries[-1] > wall.bottom_elevation + 1e-6:
            boundaries.append(float(wall.bottom_elevation))
        for index, (top, bottom) in enumerate(zip(boundaries[:-1], boundaries[1:]), start=1):
            if top - bottom < 0.25:
                continue
            selected = _zone_points(points, top, bottom)
            positive = max((float(item.get("maxPositiveMoment") or 0.0) for item in selected), default=0.0)
            negative = min((float(item.get("maxNegativeMoment") or 0.0) for item in selected), default=0.0)
            local_abs_moment = max(abs(positive), abs(negative))
            zone_type = _zone_kind(top, bottom, support_elevations, excavation_bottom, float(wall.bottom_elevation))
            envelope_source = "calculated_moment"
            if local_abs_moment <= 1e-6 and global_design_moment > 1e-6:
                local_shear = max((float(item.get("maxAbsShear") or 0.0) for item in selected), default=0.0)
                local_displacement = max((float(item.get("maxAbsDisplacement") or 0.0) for item in selected), default=0.0)
                shear_ratio = local_shear / max(segment_max_shear, 1e-9)
                displacement_ratio = local_displacement / max(segment_max_displacement, 1e-9)
                zone_floor = {"support_node_zone": 0.62, "excavation_transition_zone": 0.78, "toe_zone": 0.46, "crown_zone": 0.32, "field_zone": 0.38}.get(zone_type, 0.38)
                proxy_ratio = min(1.0, max(zone_floor, 0.80 * shear_ratio + 0.20 * displacement_ratio))
                local_abs_moment = global_design_moment * proxy_ratio
                mid_elevation = (top + bottom) / 2.0
                inner_ratio = 0.72 if mid_elevation <= excavation_bottom + 1.5 else 0.58
                outer_ratio = 0.72 if mid_elevation > excavation_bottom + 1.5 else 0.58
                positive = local_abs_moment * inner_ratio
                negative = -local_abs_moment * outer_ratio
                envelope_source = "shear_displacement_proxy"
            abs_moment = max(local_abs_moment, 0.0)
            governing_stage = next((item.get("governingStageId") for item in selected if abs(float(item.get("maxAbsMoment") or 0.0)) >= max(local_abs_moment, 1e-6) - 1e-6), None)
            if governing_stage is None and selected:
                governing_stage = max(selected, key=lambda item: float(item.get("maxAbsShear") or 0.0) + float(item.get("maxAbsDisplacement") or 0.0)).get("governingStageId")
            local_factor = 1.12 if zone_type in {"support_node_zone", "excavation_transition_zone"} else 1.05 if zone_type == "toe_zone" else 1.0
            face_rows: list[dict[str, Any]] = []
            wall_plan_length = _wall_plan_length(wall)
            target_utilization = _target_wall_utilization(project, mode)
            default_max_spacing = float(getattr(project.design_settings, "wall_rebar_default_max_main_spacing_mm", 180.0) or 180.0)
            long_wall_threshold = float(getattr(project.design_settings, "wall_rebar_long_wall_threshold_m", 40.0) or 40.0)
            long_wall_max_spacing = float(getattr(project.design_settings, "wall_rebar_long_wall_max_main_spacing_mm", 150.0) or 150.0)
            configured_max_spacing = long_wall_max_spacing if wall_plan_length >= long_wall_threshold else default_max_spacing
            no_downgrade = bool(getattr(project.design_settings, "wall_rebar_no_downgrade_existing", True))
            for face, moment in (("inner", positive if positive > 0 else abs_moment * 0.55), ("outer", abs(negative) if negative < 0 else abs_moment * 0.55)):
                demand = max(moment * factor * local_factor, 0.0)
                design = design_rectangular_flexure(
                    moment_design_knm_per_m=demand,
                    thickness_m=float(wall.thickness),
                    concrete_grade=wall.concrete_grade,
                    rebar_grade=wall.rebar_grade,
                    cover_mm=70.0,
                )
                existing_floor = _existing_face_rebar_floor(wall, face)
                existing_area = float(existing_floor.get("areaMm2PerM") or 0.0) if no_downgrade else 0.0
                existing_spacing = existing_floor.get("spacingMm") if no_downgrade else None
                max_spacing = min(configured_max_spacing, float(existing_spacing)) if existing_spacing else configured_max_spacing
                reserve_target_as = float(design.governing_as) / max(target_utilization, 1e-9)
                selection_target_as = max(reserve_target_as, existing_area)
                arrangement = _select_wall_arrangement(selection_target_as, mode, max_spacing_mm=max_spacing)
                dia = int(arrangement["diameterMm"])
                spacing = int(arrangement["spacingMm"])
                provided = float(arrangement["providedAsMm2PerM"])
                utilization = float(design.governing_as) / max(provided, 1e-9)
                reserve_ratio = provided / max(float(design.governing_as), 1e-9) - 1.0
                clear_spacing = float(arrangement["clearSpacingMm"])
                minimum_clear = max(30.0, float(dia))
                status = "pass" if utilization <= 1.0 and clear_spacing >= minimum_clear else "fail"
                if design.section_capacity_exceeded and status == "pass":
                    status = "manual_review"
                recommended_thickness = float(wall.thickness)
                if utilization > 1.0 or design.section_capacity_exceeded:
                    demand_ratio = max(utilization, demand / max(float(design.limiting_moment_knm_per_m or demand), 1e-9))
                    recommended_thickness = math.ceil(float(wall.thickness) * math.sqrt(max(demand_ratio, 1.0)) * 10.0) / 10.0
                constructability_note = "单层单面配筋可实施" if arrangement["layerCount"] == 1 else "采用双层单面钢筋并配置机械连接，需复核钢筋笼净距与吊装"
                if status == "fail":
                    constructability_note = f"已超出双层 D40@100 目录能力；建议墙厚不小于 {recommended_thickness:.1f}m 或调整围护/支撑体系"
                elif design.section_capacity_exceeded:
                    constructability_note = f"单筋截面弯矩界限已超出，需双筋截面专项复核；建议墙厚不小于 {recommended_thickness:.1f}m"
                face_rows.append(
                    {
                        "face": face,
                        "momentDesignKnMPerM": round(demand, 2),
                        "requiredAsMm2PerM": round(float(design.governing_as), 2),
                        "barDiameterMm": dia,
                        "barSpacingMm": spacing,
                        "providedAsMm2PerM": provided,
                        "utilization": round(utilization, 3),
                        "targetUtilization": round(target_utilization, 3),
                        "designReserveRatio": round(reserve_ratio, 3),
                        "selectionTargetAsMm2PerM": round(selection_target_as, 2),
                        "existingContinuousCageFloorAsMm2PerM": round(existing_area, 2),
                        "existingContinuousCageSpacingMm": existing_spacing,
                        "maximumMainBarSpacingMm": round(max_spacing, 1),
                        "noDowngradeExistingCage": no_downgrade,
                        "clearSpacingMm": round(clear_spacing, 1),
                        "minimumClearSpacingMm": round(minimum_clear, 1),
                        "status": status,
                        "token": f"{wall.rebar_grade} {arrangement['layerCount']}xD{dia}@{spacing}" if arrangement["layerCount"] > 1 else f"{wall.rebar_grade} D{dia}@{spacing}",
                        "layerCount": arrangement["layerCount"],
                        "arrangementType": arrangement["arrangementType"],
                        "mechanicalCouplerRequired": arrangement["mechanicalCouplerRequired"],
                        "designRegime": design.design_regime,
                        "compressionRebarRequiredMm2PerM": design.compression_rebar_required,
                        "limitingMomentKnMPerM": design.limiting_moment_knm_per_m,
                        "failureReasonCode": "WALL_SECTION_CAPACITY" if status == "fail" else "WALL_DOUBLE_REBAR_REVIEW" if status == "manual_review" else None,
                        "recommendedMinimumWallThicknessM": round(recommended_thickness, 2),
                        "constructabilityNote": constructability_note,
                    }
                )
                checks.append(
                    {
                        "checkId": f"RB-WALL-{wall.id}-{index}-{face}",
                        "category": "wall_zone_reinforcement",
                        "hostId": wall.id,
                        "hostCode": wall.panel_code,
                        "zoneId": f"WZ-{wall.panel_code}-{index:02d}",
                        "face": face,
                        "status": status,
                        "utilization": round(utilization, 3),
                        "message": f"{wall.panel_code} {top:.2f}~{bottom:.2f}m {face} face D{dia}@{spacing}; utilization={utilization:.3f}; reserve={reserve_ratio:.1%}",
                    }
                )
            horizontal_spacing = 150 if zone_type in {"support_node_zone", "excavation_transition_zone"} else 180 if mode == "conservative" else 200
            horizontal_dia = 18 if wall.thickness >= 1.2 else 16
            tie_spacing = 350 if zone_type == "support_node_zone" else 450
            cage_clear = max(float(wall.thickness) * 1000.0 - 2 * 70.0 - 2 * max(row["barDiameterMm"] for row in face_rows), 0.0)
            congestion_ratio = sum(float(row["providedAsMm2PerM"]) for row in face_rows) / max(float(wall.thickness) * 1_000_000.0, 1.0)
            zones.append(
                {
                    "zoneId": f"WZ-{wall.panel_code}-{index:02d}",
                    "hostType": "diaphragm_wall",
                    "hostId": wall.id,
                    "hostCode": wall.panel_code,
                    "segmentId": wall.segment_id,
                    "zoneType": zone_type,
                    "topElevation": round(top, 3),
                    "bottomElevation": round(bottom, 3),
                    "heightM": round(top - bottom, 3),
                    "governingStageId": governing_stage,
                    "envelopeSource": envelope_source,
                    "calculationEnvelopeAnomaly": envelope_anomaly,
                    "positiveMomentKnMPerM": round(positive, 2),
                    "negativeMomentKnMPerM": round(negative, 2),
                    "maxAbsMomentKnMPerM": round(abs_moment, 2),
                    "maxAbsShearKnPerM": round(max((float(item.get("maxAbsShear") or 0.0) for item in selected), default=0.0), 2),
                    "maxAbsDisplacementM": round(max((float(item.get("maxAbsDisplacement") or 0.0) for item in selected), default=0.0), 6),
                    "faces": face_rows,
                    "horizontalDistribution": {"diameterMm": horizontal_dia, "spacingMm": horizontal_spacing, "token": f"D{horizontal_dia}@{horizontal_spacing}"},
                    "tieBars": {"diameterMm": 12, "spacingMm": tie_spacing, "token": f"D12@{tie_spacing}"},
                    "cageClearWidthMm": round(cage_clear, 1),
                    "longitudinalSteelRatio": round(congestion_ratio, 5),
                    "drawingRefs": ["R-01", "R-02", "D-04" if zone_type == "support_node_zone" else "R-03", "D-06"],
                    "status": "fail" if any(row["status"] == "fail" for row in face_rows) else "warning" if any(row["status"] == "warning" for row in face_rows) else "pass",
                }
            )
    return zones, checks


def _wall_plan_length(wall: Any) -> float:
    points = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    if len(points) < 2:
        return max(float(getattr(wall, "design_length", 0.0) or 0.0), 0.0)
    return sum(math.hypot(float(b.x - a.x), float(b.y - a.y)) for a, b in zip(points[:-1], points[1:]))


def _wall_chainage(wall: Any, point: Any) -> float | None:
    points = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    if len(points) < 2 or point is None:
        return None
    best: tuple[float, float] | None = None
    accumulated = 0.0
    for a, b in zip(points[:-1], points[1:]):
        dx, dy = float(b.x - a.x), float(b.y - a.y)
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        t = max(0.0, min(1.0, ((float(point.x) - float(a.x)) * dx + (float(point.y) - float(a.y)) * dy) / (length * length)))
        px, py = float(a.x) + t * dx, float(a.y) + t * dy
        distance = math.hypot(float(point.x) - px, float(point.y) - py)
        chainage = accumulated + t * length
        if best is None or distance < best[0]:
            best = (distance, chainage)
        accumulated += length
    return best[1] if best is not None else None


def _wall_plan_zones(project: Project, wall_zones: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create plan-direction wall reinforcement zones linked to actual support nodes.

    The wall strip calculation supplies reinforcement demand through depth.  Plan
    zoning adds local corner and support-introduction regions.  Repeated support
    endpoints at different levels are clustered by chainage so a 100 m wall does
    not receive hundreds of duplicate plan regions.
    """
    ret = project.retaining_system
    if not ret or not bool(getattr(project.design_settings, "reinforcement_plan_zoning_enabled", True)):
        return [], []
    depth_by_host: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for zone in wall_zones:
        depth_by_host[str(zone.get("hostId"))].append(zone)
    result: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    corner_cfg = max(1.5, float(getattr(project.design_settings, "reinforcement_corner_zone_length_m", 3.0) or 3.0))
    node_half = max(0.8, float(getattr(project.design_settings, "reinforcement_support_node_zone_half_length_m", 1.8) or 1.8))

    for wall in ret.diaphragm_walls:
        length = _wall_plan_length(wall)
        if length <= 0.5:
            continue
        identifiers = {str(value) for value in (wall.id, wall.segment_id, wall.panel_code, wall.design_face_code) if value}
        raw_nodes: list[dict[str, Any]] = []
        for support in ret.supports:
            endpoint_rows = (
                (support.start_face_code, support.start_wall_connection or support.start),
                (support.end_face_code, support.end_wall_connection or support.end),
            )
            for face_code, point in endpoint_rows:
                if face_code is None or str(face_code) not in identifiers:
                    continue
                chainage = _wall_chainage(wall, point)
                if chainage is None:
                    continue
                raw_nodes.append({
                    "chainage": max(0.0, min(length, float(chainage))),
                    "elevation": float(support.elevation),
                    "code": str(support.code),
                })
        raw_nodes.sort(key=lambda item: float(item["chainage"]))
        clusters: list[dict[str, Any]] = []
        for item in raw_nodes:
            if clusters and abs(float(clusters[-1]["chainage"]) - float(item["chainage"])) <= 0.25:
                cluster = clusters[-1]
                cluster["chainages"].append(float(item["chainage"]))
                cluster["chainage"] = sum(cluster["chainages"]) / len(cluster["chainages"])
                cluster["elevations"].add(round(float(item["elevation"]), 3))
                cluster["codes"].add(str(item["code"]))
            else:
                clusters.append({
                    "chainage": float(item["chainage"]),
                    "chainages": [float(item["chainage"])],
                    "elevations": {round(float(item["elevation"]), 3)},
                    "codes": {str(item["code"])},
                })

        corner_length = min(corner_cfg, max(length * 0.18, 1.5), max(length / 3.0, 1.5))
        breaks = {0.0, length, corner_length, max(0.0, length - corner_length)}
        for node in clusters:
            station = float(node["chainage"])
            breaks.add(max(0.0, station - node_half))
            breaks.add(min(length, station + node_half))
        ordered = sorted(value for value in breaks if -1e-9 <= value <= length + 1e-9)
        governing_faces = [face for zone in depth_by_host.get(wall.id, []) for face in zone.get("faces", [])]
        governing_dia = max((float(face.get("barDiameterMm") or 0.0) for face in governing_faces), default=20.0)
        governing_spacing = min((float(face.get("barSpacingMm") or 9999.0) for face in governing_faces), default=200.0)
        raw_zones: list[dict[str, Any]] = []
        for start_ch, end_ch in zip(ordered[:-1], ordered[1:]):
            if end_ch - start_ch < 0.20:
                continue
            mid = (start_ch + end_ch) / 2.0
            active = [node for node in clusters if abs(float(node["chainage"]) - mid) <= node_half + 1e-9]
            if active:
                zone_type = "support_node_plan_zone"
                multiplier = 1.25
                local_spacing = min(governing_spacing, 150.0)
                additional = {"type": "local_vertical_U_and_horizontal_distribution", "diameterMm": _standard_bar_diameter(governing_dia - 4.0, minimum=16), "spacingMm": 150.0, "verticalHalfHeightM": 1.2}
                refs = sorted({code for node in active for code in node["codes"]})
                elevations = sorted({elevation for node in active for elevation in node["elevations"]}, reverse=True)
            elif mid <= corner_length + 1e-9 or mid >= length - corner_length - 1e-9:
                zone_type = "corner_plan_zone"
                multiplier = 1.15
                local_spacing = min(governing_spacing, 160.0)
                additional = {"type": "corner_return_and_diagonal_bar", "diameterMm": _standard_bar_diameter(governing_dia - 4.0, minimum=16), "spacingMm": 160.0}
                refs, elevations = [], []
            else:
                zone_type = "field_plan_zone"
                multiplier = 1.0
                local_spacing = governing_spacing
                additional = None
                refs, elevations = [], []
            row = {
                "hostId": wall.id,
                "hostCode": wall.panel_code,
                "segmentId": wall.segment_id,
                "zoneType": zone_type,
                "startChainageM": round(start_ch, 3),
                "endChainageM": round(end_ch, 3),
                "lengthM": round(end_ch - start_ch, 3),
                "demandMultiplier": multiplier,
                "governingMainBarDiameterMm": governing_dia,
                "governingMainBarSpacingMm": local_spacing,
                "additionalReinforcement": additional,
                "supportRefs": refs,
                "supportElevationsM": elevations,
                "drawingRefs": ["R-01", "R-02", "D-04" if zone_type == "support_node_plan_zone" else "D-06" if zone_type == "corner_plan_zone" else "R-03"],
                "status": "pass",
                "professionalReviewRequired": zone_type != "field_plan_zone",
            }
            if raw_zones and raw_zones[-1]["zoneType"] == row["zoneType"] and raw_zones[-1]["supportRefs"] == row["supportRefs"] and raw_zones[-1]["supportElevationsM"] == row["supportElevationsM"]:
                raw_zones[-1]["endChainageM"] = row["endChainageM"]
                raw_zones[-1]["lengthM"] = round(float(raw_zones[-1]["endChainageM"]) - float(raw_zones[-1]["startChainageM"]), 3)
            else:
                raw_zones.append(row)

        for index, row in enumerate(raw_zones, start=1):
            row["planZoneId"] = f"WPZ-{wall.panel_code}-{index:02d}"
            result.append(row)
            checks.append({
                "checkId": f"RB-WALL-PLAN-{wall.id}-{index}",
                "category": "wall_plan_reinforcement_zone",
                "hostId": wall.id,
                "hostCode": wall.panel_code,
                "zoneId": row["planZoneId"],
                "status": "pass",
                "utilization": None,
                "message": f"{wall.panel_code} CH {row['startChainageM']:.2f}~{row['endChainageM']:.2f}m {row['zoneType']}; main bars not wider than D{int(governing_dia)}@{int(row['governingMainBarSpacingMm'])}.",
                "recommendedAction": "在施工图中表达局部附加筋、锚固范围及钢筋笼分段，节点区需结合节点反力复核。" if row["professionalReviewRequired"] else None,
            })
    return result, checks


_SUPPORT_TARGET_UTILIZATION: dict[str, float] = {"conservative": 0.78, "balanced": 0.88, "economic": 0.95}


def _support_section_candidates(width: float, height: float, role: str) -> list[tuple[float, float]]:
    practical_max = 2.4 if role == "secondary_strut" else 2.2 if role == "corner_diagonal" else 2.0
    values: list[tuple[float, float]] = [(round(width, 2), round(height, 2))]
    size = max(width, height)
    while size < practical_max - 1e-9:
        size = min(practical_max, math.ceil((size + 0.19) * 5.0) / 5.0)
        values.append((round(size, 2), round(size, 2)))
    return list(dict.fromkeys(values))


def _optimize_support_section(
    *,
    force: float,
    width: float,
    height: float,
    concrete_grade: str,
    rebar_grade: str,
    role: str,
    mode: RebarMode,
) -> dict[str, Any]:
    target = _SUPPORT_TARGET_UTILIZATION[mode]
    bar_patterns = [(12, 28), (16, 28), (16, 32), (20, 32), (20, 36), (24, 36), (24, 40)]
    feasible: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for candidate_width, candidate_height in _support_section_candidates(width, height, role):
        for count, dia in bar_patterns:
            check = check_rc_rectangular_axial_capacity(
                axial_design_kn=force,
                width_m=candidate_width,
                height_m=candidate_height,
                concrete_grade=concrete_grade,
                rebar_grade=rebar_grade,
                longitudinal_bar_dia=dia,
                longitudinal_bar_count=count,
            )
            bars_per_face = max(math.ceil(count / 4), 2)
            clear_width = max(candidate_width * 1000.0 - 2 * 50.0 - 2 * dia, 0.0)
            clear_spacing = (clear_width - bars_per_face * dia) / max(bars_per_face - 1, 1)
            steel_ratio = count * bar_area(dia) / max(candidate_width * candidate_height * 1_000_000.0, 1.0)
            row = {
                "widthM": candidate_width,
                "heightM": candidate_height,
                "count": count,
                "diameterMm": dia,
                "capacityKn": float(check["capacity"]),
                "utilization": float(check["utilization"]),
                "clearSpacingMm": round(clear_spacing, 1),
                "steelRatio": round(steel_ratio, 5),
                "targetUtilization": target,
            }
            if check["status"] == "pass" and clear_spacing >= max(35.0, float(dia)) and steel_ratio <= 0.025:
                fallback.append(row)
                if float(check["utilization"]) <= target:
                    feasible.append(row)
    pool = feasible or fallback
    if pool:
        selected = min(
            pool,
            key=lambda item: (
                float(item["widthM"]) * float(item["heightM"]),
                int(item["count"]) * bar_area(float(item["diameterMm"])),
            ),
        )
        selected["status"] = "pass" if selected["utilization"] <= target else "warning"
        selected["sectionChanged"] = abs(selected["widthM"] - width) > 1e-6 or abs(selected["heightM"] - height) > 1e-6
        selected["autoFixAction"] = "UPSIZE_SECTION_AND_RECALCULATE" if selected["sectionChanged"] else "UPDATE_REBAR_ONLY"
        selected["failureReasonCode"] = None
        return selected
    candidate_width, candidate_height = _support_section_candidates(width, height, role)[-1]
    count, dia = bar_patterns[-1]
    check = check_rc_rectangular_axial_capacity(force, candidate_width, candidate_height, concrete_grade, rebar_grade, dia, count)
    return {
        "widthM": candidate_width,
        "heightM": candidate_height,
        "count": count,
        "diameterMm": dia,
        "capacityKn": float(check["capacity"]),
        "utilization": float(check["utilization"]),
        "clearSpacingMm": 0.0,
        "steelRatio": round(count * bar_area(dia) / max(candidate_width * candidate_height * 1_000_000.0, 1.0), 5),
        "targetUtilization": target,
        "status": "fail",
        "sectionChanged": True,
        "autoFixAction": "ADD_OR_REARRANGE_SUPPORTS",
        "failureReasonCode": "SUPPORT_TOPOLOGY_OR_SECTION_LIMIT",
    }


def _support_scheme(project: Project, mode: RebarMode) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ret = project.retaining_system
    if not ret:
        return [], []
    rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for support in ret.supports:
        if support.section_type != "rc_rectangular":
            rows.append({
                "hostId": support.id, "hostCode": support.code, "hostType": "internal_support",
                "sectionType": support.section_type, "status": "manual_review",
                "failureReasonCode": "NON_RC_SUPPORT_REQUIRES_STEEL_MODULE",
                "note": "钢支撑由钢结构构件与连接模块校核。", "drawingRefs": ["S-02", "D-01"],
            })
            continue
        width = float(support.section.width or 0.8)
        height = float(support.section.height or 0.8)
        # support.design_axial_force is already the ULS envelope including
        # construction effects. Strategy modes change reserve targets and
        # detailing, not the demand a second time.
        force = abs(float(support.design_axial_force or support.effective_axial_force_standard or 0.0))
        existing_long = next((item for item in support.reinforcement if item.bar_type == "longitudinal"), None)
        rebar_grade = existing_long.grade if existing_long else "HRB400"
        optimized = _optimize_support_section(
            force=force, width=width, height=height, concrete_grade=support.material.grade,
            rebar_grade=rebar_grade, role=support.support_role, mode=mode,
        )
        count = int(optimized["count"]); dia = int(optimized["diameterMm"])
        selected_width = float(optimized["widthM"]); selected_height = float(optimized["heightM"])
        end_length = round(max(1.5 * selected_height, 1.5), 2)
        span = float(support.span_length or math.hypot(support.end.x - support.start.x, support.end.y - support.start.y))
        end_spacing = 100 if force >= 6500 or mode == "conservative" else 120
        mid_spacing = 150 if force >= 6500 else 180 if mode != "economic" else 200
        distribution_dia = 16 if min(selected_width, selected_height) >= 0.9 else 14
        distribution_spacing = 180 if force >= 6500 else 200
        tie_spacing = 350 if force >= 6500 else 400
        lap_dia = max(16, dia - 6)
        status = str(optimized["status"])
        row = {
            "hostType": "internal_support", "hostId": support.id, "hostCode": support.code,
            "levelIndex": support.level_index, "elevation": support.elevation, "supportRole": support.support_role,
            "spanM": round(span, 3),
            "existingSection": {"widthM": width, "heightM": height, "name": support.section.name},
            "section": {"widthM": selected_width, "heightM": selected_height, "name": f"{int(selected_width*1000)}x{int(selected_height*1000)} RC"},
            "sectionChanged": bool(optimized["sectionChanged"]),
            "axialForceDesignKn": round(force, 2),
            "rawAxialForceStandardKn": support.raw_axial_force_standard_envelope,
            "forceReconciliationStatus": support.force_reconciliation_status,
            "forceReconciliationNote": support.force_reconciliation_note,
            "longitudinal": {"count": count, "diameterMm": dia, "grade": rebar_grade, "token": f"{count}D{dia}"},
            "endZones": {"lengthM": end_length, "stirrupDiameterMm": 14 if force >= 15000 else 12, "stirrupSpacingMm": end_spacing, "token": f"D{14 if force >= 15000 else 12}@{end_spacing}"},
            "middleZone": {"lengthM": round(max(span - 2 * end_length, 0.0), 2), "stirrupDiameterMm": 12, "stirrupSpacingMm": mid_spacing, "token": f"D12@{mid_spacing}"},
            "distributionBars": {"diameterMm": distribution_dia, "spacingMm": distribution_spacing, "token": f"D{distribution_dia}@{distribution_spacing}", "label": "侧面构造分布筋"},
            "tieBars": {"diameterMm": 12, "spacingMm": tie_spacing, "token": f"D12@{tie_spacing}", "label": "拉结与架立筋"},
            "lapAdditionalBars": {"count": 4, "diameterMm": lap_dia, "token": f"4D{lap_dia}", "label": "搭接与锚固区附加筋"},
            "rebarContract": {
                "expectedBarTypes": ["纵向主筋", "箍筋", "侧面构造分布筋", "拉结与架立筋", "搭接附加筋"],
                "presentBarTypes": ["纵向主筋", "箍筋", "侧面构造分布筋", "拉结与架立筋", "搭接附加筋"],
                "missingBarTypes": [],
                "status": "complete",
                "message": "五类水平支撑钢筋均已形成参数化设计记录，箍筋分端部加密区和跨中区。",
            },
            "lapArrangement": {"type": "mechanical_or_staggered", "maximumSameSectionRatio": 0.5, "recommendedLocation": "middle_third_away_from_node_rigid_zones"},
            "axialCapacityKn": round(float(optimized["capacityKn"]), 2),
            "utilization": round(float(optimized["utilization"]), 3),
            "targetUtilization": optimized["targetUtilization"],
            "clearSpacingMm": optimized["clearSpacingMm"],
            "longitudinalSteelRatio": optimized["steelRatio"],
            "status": status,
            "failureReasonCode": optimized["failureReasonCode"],
            "autoFixAction": optimized["autoFixAction"],
            "recommendedAction": (
                "应用截面优化后重新计算内力与节点承压。" if optimized["sectionChanged"]
                else "当前截面可用，应用配筋并复核长细比与施工阶段偏心。" if status != "fail"
                else "增加次对撑/角撑或改变支撑体系后重新计算。"
            ),
            "drawingRefs": [f"S-02-L{support.level_index:02d}", "R-04", "D-01", "D-02" if support.support_role == "corner_diagonal" else "D-03", "D-07"],
        }
        rows.append(row)
        checks.append({
            "checkId": f"RB-SUPPORT-{support.id}", "category": "support_reinforcement",
            "hostId": support.id, "hostCode": support.code, "status": status,
            "utilization": row["utilization"], "failureReasonCode": row["failureReasonCode"],
            "recommendedAction": row["recommendedAction"],
            "message": f"{support.code}: {row['section']['name']}, {count}D{dia}, 轴压利用率 {row['utilization']:.3f}",
        })
    return rows, checks


def _beam_and_node_scheme(project: Project, mode: RebarMode) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ret = project.retaining_system
    if not ret:
        return [], []
    rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
        design = beam.design_result
        main_dia = int(design.main_bar_diameter or 25) if design else 25
        main_spacing = int(design.main_bar_spacing or 150) if design else 150
        stirrup_dia = int(design.stirrup_diameter or 12) if design else 12
        stirrup_spacing = int(design.stirrup_spacing or 150) if design else 150
        if mode == "conservative":
            main_spacing = _round_spacing(main_spacing * 0.9, minimum=100, maximum=200)
            stirrup_spacing = _round_spacing(stirrup_spacing * 0.85, minimum=100, maximum=200)
        elif mode == "economic":
            main_spacing = _round_spacing(main_spacing * 1.08, minimum=100, maximum=225)
        status = str(design.check_status if design else "manual_review")
        rows.append(
            {
                "hostType": "wale_or_crown_beam",
                "hostId": beam.id,
                "hostCode": beam.code,
                "beamRole": beam.beam_role,
                "levelIndex": beam.support_level,
                "elevation": beam.elevation,
                "mainBars": {"diameterMm": main_dia, "spacingMm": main_spacing, "token": f"D{main_dia}@{main_spacing}"},
                "stirrups": {"diameterMm": stirrup_dia, "spacingMm": stirrup_spacing, "nodeSpacingMm": min(stirrup_spacing, 100), "token": f"D{stirrup_dia}@{stirrup_spacing}"},
                "distributionBars": {"diameterMm": 14, "spacingMm": 200, "token": "D14@200", "label": "梁侧面构造筋"},
                "tieBars": {"diameterMm": 12, "spacingMm": 400, "token": "D12@400", "label": "截面拉结筋"},
                "nodeAdditional": design.node_additional_reinforcement_note if design else "尚缺正式施工阶段设计内力；重新计算后生成转角 U 形筋、封闭箍筋和局部承压附加筋。",
                "structuralClosure": {
                    "status": "closed" if design and design.check_status == "pass" else "needs_calculation" if not design else "needs_strengthening",
                    "message": "承载力和刚度计算已闭合；锚固、接头与预埋件进入专业构造复核。" if design and design.check_status == "pass" else "该梁尚未形成通过的正式设计内力与配筋记录。",
                },
                "status": status,
                "drawingRefs": ["R-05", "D-01", "D-02"],
            }
        )
        checks.append({"checkId": f"RB-BEAM-{beam.id}", "category": "beam_reinforcement", "hostId": beam.id, "hostCode": beam.code, "status": status, "message": f"{beam.code}: D{main_dia}@{main_spacing}, stirrup D{stirrup_dia}@{stirrup_spacing}"})
    for node in ret.support_nodes:
        bearing = node.bearing_plate
        force = next((abs(float(item.design_axial_force or 0.0)) for item in ret.supports if item.id == node.support_id), 0.0)
        u_dia = 20 if force < 5000 else 25 if force < 9000 else 28
        u_count = 4 if force < 5000 else 6 if force < 9000 else 8
        confinement_spacing = 100 if force < 6500 else 80
        status = node.check_status
        bearing_utilization = None
        if bearing and bearing.bearing_capacity and bearing.bearing_stress is not None:
            bearing_utilization = float(bearing.bearing_stress) / max(float(bearing.bearing_capacity), 1e-9)
        failure_reason = "NODE_BEARING_CAPACITY" if status == "fail" else "NODE_BEARING_HIGH_UTILIZATION" if status == "warning" else None
        recommended_action = (
            "增大支撑/围檩节点核心区和承压扩散范围，或降低支撑轴力后重新计算。" if status == "fail"
            else "承压板接近支撑截面边界，复核锚固、抗劈裂钢筋及施工净距。" if status == "warning"
            else "节点承压满足快速筛查，继续复核锚固、局部抗裂和预埋件。"
        )
        rows.append(
            {
                "hostType": "support_wale_node",
                "hostId": node.id,
                "hostCode": node.code,
                "nodeType": node.node_type,
                "supportCode": node.support_code,
                "waleBeamCode": node.wale_beam_code,
                "designAxialForceKn": round(force, 2),
                "bearingPlate": bearing.model_dump(mode="json", by_alias=True) if bearing else None,
                "bearingUtilization": round(bearing_utilization, 3) if bearing_utilization is not None else None,
                "failureReasonCode": failure_reason,
                "recommendedAction": recommended_action,
                "additionalUBars": {"count": u_count, "diameterMm": u_dia, "token": f"{u_count}D{u_dia}"},
                "confinement": {"stirrupDiameterMm": 12, "spacingMm": confinement_spacing, "zoneLengthM": 1.5},
                "antiBurstingMesh": {"diameterMm": 14, "spacingMm": 100, "layers": 2},
                "status": status,
                "drawingRefs": ["D-01", "D-02" if node.node_type == "diagonal_to_wale" else "D-03"],
            }
        )
        checks.append({"checkId": f"RB-NODE-{node.id}", "category": "node_congestion", "hostId": node.id, "hostCode": node.code, "status": status, "utilization": round(bearing_utilization, 3) if bearing_utilization is not None else None, "failureReasonCode": failure_reason, "recommendedAction": recommended_action, "message": f"{node.code}: {u_count}D{u_dia} U-bars, D12@{confinement_spacing} confinement"})
    return rows, checks


def _quantity_summary(wall_zones: list[dict[str, Any]], supports: list[dict[str, Any]], beams_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    wall_weight_index = 0.0
    for zone in wall_zones:
        height = float(zone.get("heightM") or 0.0)
        for face in zone.get("faces", []):
            dia = float(face.get("barDiameterMm") or 0.0)
            spacing = float(face.get("barSpacingMm") or 200.0)
            wall_weight_index += height * (1000.0 / max(spacing, 1.0)) * (dia**2 / 162.0)
    support_weight_index = 0.0
    for item in supports:
        longitudinal = item.get("longitudinal") or {}
        support_weight_index += float(item.get("spanM") or 0.0) * float(longitudinal.get("count") or 0.0) * (float(longitudinal.get("diameterMm") or 0.0) ** 2 / 162.0)
    return {
        "wallVerticalSteelWeightIndexKg": round(wall_weight_index, 2),
        "supportLongitudinalSteelWeightIndexKg": round(support_weight_index, 2),
        "wallZoneCount": len(wall_zones),
        "supportSchemeCount": len(supports),
        "beamNodeSchemeCount": len(beams_nodes),
    }


def _build_design_diagnostics(
    project: Project,
    checks: list[dict[str, Any]],
    support_rows: list[dict[str, Any]],
    *,
    scheme_applied_override: bool | None = None,
) -> dict[str, Any]:
    from app.services.deepening_readiness import build_deepening_readiness, calculation_readiness

    latest = _latest_result(project)
    calc_sync = ((latest.design_iteration_summary or {}).get("supportTopologySynchronization") if latest else None) or {}
    calculation_gate = calculation_readiness(project)
    calculation_valid = bool(calculation_gate.get("valid"))
    invalid_reasons: list[str] = list(calculation_gate.get("messages") or []) if not calculation_valid else []
    if calc_sync.get("after", {}).get("requiresSynchronization"):
        calculation_valid = False
        invalid_reasons.append("计算工况仍存在失效支撑引用。")
    ret = project.retaining_system
    secondary_count = sum(1 for item in (ret.supports if ret else []) if item.support_role == "secondary_strut")
    corner_max_tributary = max(
        [
            float(value)
            for item in (ret.supports if ret else [])
            if item.support_role == "corner_diagonal"
            for value in (item.start_tributary_width, item.end_tributary_width)
            if value is not None
        ]
        or [0.0]
    )
    topology_status = "pass"
    topology_message = "各墙面均由主/次对撑或角撑形成直接传力路径。"
    if corner_max_tributary > 15.0 and secondary_count == 0:
        topology_status = "fail"
        topology_message = f"角撑最大参考分担墙宽 {corner_max_tributary:.1f}m，缺少正交次对撑，易产生角撑超大轴力。"
    elif corner_max_tributary > 12.0:
        topology_status = "warning"
        topology_message = f"角撑最大参考分担墙宽 {corner_max_tributary:.1f}m，需复核围檩连续传力和节点扩散。"
    categories: dict[str, dict[str, int]] = {}
    reason_groups: dict[str, dict[str, Any]] = {}
    for item in checks:
        category = str(item.get("category") or "other")
        status = str(item.get("status") or "manual_review")
        categories.setdefault(category, {"pass": 0, "warning": 0, "manual_review": 0, "fail": 0})
        categories[category][status if status in categories[category] else "manual_review"] += 1
        reason = item.get("failureReasonCode")
        if reason:
            group = reason_groups.setdefault(str(reason), {"count": 0, "objects": [], "recommendedAction": item.get("recommendedAction")})
            group["count"] += 1
            if len(group["objects"]) < 12:
                group["objects"].append(item.get("hostCode"))
    fail_count = sum(1 for item in checks if item.get("status") == "fail")
    warning_count = sum(1 for item in checks if item.get("status") in {"warning", "manual_review", "preliminary"})
    section_change_count = sum(1 for item in support_rows if item.get("sectionChanged"))
    actions: list[dict[str, Any]] = []
    if not calculation_valid:
        actions.append({"id": "RECALCULATE", "priority": 1, "label": "重新计算", "description": "同步支撑拓扑和施工阶段后重新计算，再生成正式配筋。"})
    if topology_status == "fail":
        actions.append({"id": "OPTIMIZE_SUPPORT_TOPOLOGY", "priority": 1, "label": "优化支撑体系", "description": "增加正交次对撑或调整角撑分担宽度，降低端墙与角撑集中荷载。"})
    if section_change_count:
        actions.append({"id": "APPLY_SECTION_UPGRADES", "priority": 2, "label": "应用截面优化", "description": f"有 {section_change_count} 根支撑建议增大截面，应用后需要重新计算。"})
    if fail_count:
        actions.append({"id": "REVIEW_FAILURES", "priority": 2, "label": "查看不满足项", "description": "按原因分组处理承载力、截面上限、节点承压或净距问题。"})
    if warning_count:
        actions.append({"id": "REVIEW_WARNINGS", "priority": 3, "label": "处理复核项", "description": "复核机械连接、锚固、裂缝、吊装、节点拥挤和施工偏差。"})
    deepening_gate = build_deepening_readiness(
        project,
        checks=checks,
        section_change_count=section_change_count,
        topology_status=topology_status,
        scheme_applied=scheme_applied_override,
    )
    can_apply = bool(deepening_gate.get("canGenerateScheme"))
    can_issue = bool(deepening_gate.get("canIssueConstructionDrawings"))
    gate_actions = list(deepening_gate.get("nextActions") or [])
    if gate_actions:
        actions = gate_actions
    return {
        "calculation": {
            "status": "pass" if calculation_valid else "fail",
            "valid": calculation_valid,
            "messages": invalid_reasons or ["配筋使用最新施工阶段内力包络。"],
            "topologySynchronization": calc_sync,
            "contract": calculation_gate.get("contract"),
            "assuranceStatus": calculation_gate.get("assuranceStatus"),
        },
        "supportTopology": {
            "status": topology_status, "message": topology_message,
            "secondaryGridSupportCount": secondary_count,
            "maxCornerTributaryWidthM": round(corner_max_tributary, 3),
        },
        "categoryStatusCounts": categories,
        "failureReasons": reason_groups,
        "actions": sorted(actions, key=lambda item: int(item["priority"])),
        "deepeningGate": deepening_gate,
        "canApply": can_apply,
        "canEnterDetailing": bool(deepening_gate.get("canEnterDetailing")),
        "canRunP3": bool(deepening_gate.get("canRunP3")),
        "canIssueConstructionDrawings": can_issue,
        "exportMode": "construction" if can_issue else "review",
        "reviewWatermarkRequired": not can_issue,
        "sectionChangeCount": section_change_count,
        "headline": "配筋与节点校核通过，可进入施工图复核。" if can_issue else str(deepening_gate.get("headline") or "仍有阻断项，当前仅可输出审查版图纸。"),
    }


def build_rebar_design_scheme(
    project: Project,
    mode: str = "balanced",
    *,
    scheme_applied_override: bool | None = None,
) -> dict[str, Any]:
    selected_mode = _mode(mode)
    wall_zones, wall_checks = _wall_zone_scheme(project, selected_mode)
    wall_plan_zones, wall_plan_checks = _wall_plan_zones(project, wall_zones)
    supports, support_checks = _support_scheme(project, selected_mode)
    beams_nodes, beam_node_checks = _beam_and_node_scheme(project, selected_mode)
    checks = [*wall_checks, *wall_plan_checks, *support_checks, *beam_node_checks]
    constructability = build_rebar_constructability(project, {
        "wallZones": wall_zones,
        "wallPlanZones": wall_plan_zones,
        "supportSchemes": supports,
        "beamNodeSchemes": beams_nodes,
    })
    checks.extend(list(constructability.get("checks") or []))
    fail_count = sum(1 for item in checks if item.get("status") == "fail")
    warning_count = sum(1 for item in checks if item.get("status") in {"warning", "manual_review", "preliminary"})
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    quantities = _quantity_summary(wall_zones, supports, beams_nodes)
    diagnostics = _build_design_diagnostics(
        project,
        checks,
        supports,
        scheme_applied_override=scheme_applied_override,
    )
    append_event(
        "rebar-detailing",
        "rebar_scheme_built",
        projectId=project.id,
        mode=selected_mode,
        wallZoneCount=len(wall_zones),
        supportSchemeCount=len(supports),
        nodeSchemeCount=len(beams_nodes),
        checkCount=len(checks),
        failCount=fail_count,
        warningCount=warning_count,
        constructability=constructability.get("summary"),
        deepeningGate={
            "status": (diagnostics.get("deepeningGate") or {}).get("status"),
            "blockerCount": (diagnostics.get("deepeningGate") or {}).get("blockerCount"),
            "blockerGroups": [
                {
                    "reasonCode": row.get("reasonCode"), "title": row.get("title"),
                    "count": row.get("count"), "objects": row.get("objects"),
                    "targetStage": row.get("targetStage"),
                }
                for row in list((diagnostics.get("deepeningGate") or {}).get("blockers") or [])[:12]
            ],
        },
    )
    return {
        "projectId": project.id,
        "mode": selected_mode,
        "status": status,
        "method": "V3.55 专家配筋设计：同步施工阶段与拓扑，按墙体深度和墙面平面双向分区，完成钢筋混凝土支撑截面、节点承压与构造设计",
        "diagnostics": diagnostics,
        "wallZones": wall_zones,
        "wallPlanZones": wall_plan_zones,
        "supportSchemes": supports,
        "beamNodeSchemes": beams_nodes,
        "constructability": constructability,
        "checks": checks,
        "summary": {
            **quantities,
            "checkCount": len(checks),
            "failCount": fail_count,
            "warningCount": warning_count,
            "passCount": sum(1 for item in checks if item.get("status") == "pass"),
            "governingStatus": status,
            "zoneBasedDesign": True,
            "twoDirectionWallZoning": True,
            "wallPlanZoneCount": len(wall_plan_zones),
            "drawingLinked": True,
            "envelopeAnomalyCount": sum(1 for item in checks if item.get("category") == "calculation_envelope_consistency"),
            "sectionUpgradeRequiredCount": sum(1 for zone in wall_zones for face in zone.get("faces", []) if face.get("status") == "fail") + sum(1 for item in supports if item.get("sectionChanged")),
            "supportSectionChangeCount": diagnostics["sectionChangeCount"],
            "canIssueConstructionDrawings": diagnostics["canIssueConstructionDrawings"],
            "reviewWatermarkRequired": diagnostics["reviewWatermarkRequired"],
            "constructabilityFailCount": int((constructability.get("summary") or {}).get("failCount") or 0),
            "constructabilityReviewCount": int((constructability.get("summary") or {}).get("warningCount") or 0),
        },
        "drawingIndex": {
            "R-01": "地下连续墙配筋总图",
            "R-02": "墙体配筋立面与设计分区图",
            "R-03": "钢筋笼剖面、搭接与接头详图",
            "R-04": "水平内支撑配筋总图",
            "R-05": "冠梁、围檩与环梁配筋图",
            "D-01": "支撑—围檩典型节点详图",
            "D-02": "角撑节点详图",
            "D-03": "支撑—立柱交叉及承压节点详图",
            "D-04": "墙体支撑区局部加强详图",
            "D-06": "墙幅接头、接头箱及钢筋笼连接详图",
            "D-07": "钢筋混凝土支撑锚固与错开搭接详图",
        },
        "limitations": [
            "配筋设计基于最新施工阶段内力包络；缺少计算或支撑拓扑不同步时禁止作为正式施工图依据。",
            "裂缝宽度、抗震构造、套筒选型、机械连接工艺评定、钢筋笼吊装及注册工程师签审仍需结合项目专项复核。",
            "本方案可用于辅助设计和生成可编辑 CAD；正式施工图仍需完成专业校审并符合企业制图标准。",
        ],
    }


def _governing_wall_groups(project: Project, scheme: dict[str, Any]) -> dict[str, list[ReinforcementGroup]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    plan_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for zone in scheme.get("wallZones", []):
        grouped[str(zone.get("hostId"))].append(zone)
    for zone in scheme.get("wallPlanZones", []):
        plan_grouped[str(zone.get("hostId"))].append(zone)
    out: dict[str, list[ReinforcementGroup]] = {}
    ret = project.retaining_system
    if not ret:
        return out
    for wall in ret.diaphragm_walls:
        zones = grouped.get(wall.id, [])
        if not zones:
            continue
        groups: list[ReinforcementGroup] = []
        for face in ("inner", "outer"):
            rows = [face_row for zone in zones for face_row in zone.get("faces", []) if face_row.get("face") == face]
            if not rows:
                continue
            governing = max(rows, key=lambda item: float(item.get("requiredAsMm2PerM") or 0.0))
            groups.append(
                ReinforcementGroup(
                    name="坑内侧竖向主筋" if face == "inner" else "坑外侧竖向主筋",
                    bar_type="longitudinal",
                    diameter=float(governing["barDiameterMm"]),
                    spacing=float(governing["barSpacingMm"]),
                    grade=wall.rebar_grade,
                    area_per_meter=float(governing["providedAsMm2PerM"]),
                    required_area_per_meter=float(governing["requiredAsMm2PerM"]),
                    check_status="pass" if governing.get("status") == "pass" else "warning",
                    location_description=f"按施工阶段控制包络配置{'坑内侧' if face == 'inner' else '坑外侧'}主筋；目标利用率及不降级下限已写入正式配筋方案",
                )
            )
        distribution = min((int(zone.get("horizontalDistribution", {}).get("spacingMm") or 200) for zone in zones), default=200)
        distribution_dia = max((int(zone.get("horizontalDistribution", {}).get("diameterMm") or 16) for zone in zones), default=16)
        tie_spacing = min((int(zone.get("tieBars", {}).get("spacingMm") or 450) for zone in zones), default=450)
        groups.extend(
            [
                ReinforcementGroup(name="水平分布筋", bar_type="distribution", diameter=distribution_dia, spacing=distribution, grade=wall.rebar_grade, check_status="pass", location_description="按墙体深度分区控制，坑内外两侧连续配置水平分布筋"),
                ReinforcementGroup(name="拉结筋/架立筋", bar_type="tie", diameter=12, spacing=tie_spacing, grade=wall.rebar_grade, check_status="manual_review", location_description="连接两侧钢筋网并稳定钢筋笼；支撑节点区按平面及深度分区加密"),
            ]
        )
        local_zones = plan_grouped.get(wall.id, [])
        for local in local_zones:
            zone_type = str(local.get("zoneType") or "field_plan_zone")
            if zone_type == "field_plan_zone":
                continue
            spacing = float((local.get("additionalReinforcement") or {}).get("spacingMm") or (150 if zone_type == "support_node_plan_zone" else 160))
            diameter = float((local.get("additionalReinforcement") or {}).get("diameterMm") or max(16, distribution_dia))
            zone_length = max(float(local.get("lengthM") or 0.0), 0.2)
            # Two faces are scheduled explicitly; the plan-zone chainage is kept
            # in the description so CAD/BBS geometry remains local rather than
            # being spread over the whole physical wall.
            n_plan = int(math.floor(zone_length / max(spacing / 1000.0, 0.1))) + 1
            start = float(local.get("startChainageM") or 0.0)
            end = float(local.get("endChainageM") or start + zone_length)
            is_node = zone_type == "support_node_plan_zone"
            elevations = [float(value) for value in (local.get("supportElevationsM") or [])]
            level_count = max(1, len(elevations)) if is_node else 1
            count = max(4, 2 * n_plan * level_count)
            elevation_token = ",".join(f"{value:.3f}" for value in elevations)
            groups.append(ReinforcementGroup(
                name="支撑节点区附加筋" if is_node else "转角区附加筋",
                bar_type="additional",
                diameter=diameter,
                spacing=spacing,
                count=count,
                grade=wall.rebar_grade,
                check_status="manual_review",
                location_description=f"{'支撑节点局部竖向筋/U 形筋' if is_node else '转角回折筋/斜向附加筋'}；墙面里程 {start:.3f}～{end:.3f}m；标高 {elevation_token or '全高'}；两侧配置；关联支撑 {','.join(local.get('supportRefs') or []) or '无'}；详见 {'D-04' if is_node else 'D-06'}",
            ))
        out[wall.id] = groups
    return out


def apply_rebar_design_scheme(project: Project, mode: str = "balanced") -> dict[str, Any]:
    if not project.retaining_system:
        raise ValueError("Project has no retaining system")
    scheme = build_rebar_design_scheme(project, mode=mode, scheme_applied_override=True)
    wall_groups = _governing_wall_groups(project, scheme)
    for wall in project.retaining_system.diaphragm_walls:
        if wall.id in wall_groups:
            wall.reinforcement = wall_groups[wall.id]
    support_map = {str(item.get("hostId")): item for item in scheme.get("supportSchemes", [])}
    for support in project.retaining_system.supports:
        item = support_map.get(support.id)
        if not item or support.section_type != "rc_rectangular":
            continue
        longitudinal = item.get("longitudinal") or {}
        end_zone = item.get("endZones") or {}
        middle = item.get("middleZone") or {}
        distribution = item.get("distributionBars") or {}
        ties = item.get("tieBars") or {}
        lap_additional = item.get("lapAdditionalBars") or {}
        proposed_section = item.get("section") or {}
        if item.get("sectionChanged"):
            support.section.width = float(proposed_section.get("widthM") or support.section.width or 0.8)
            support.section.height = float(proposed_section.get("heightM") or support.section.height or 0.8)
            support.section.name = str(proposed_section.get("name") or support.section.name)
            support.section_optimization_status = "section_upgraded"
            support.section_optimization_note = "V3.2 配筋优化已增大支撑截面；必须重新计算支撑刚度、轴力和节点承压。"
        else:
            support.section_optimization_status = "pass" if item.get("status") != "fail" else "topology_upgrade_required"
            support.section_optimization_note = str(item.get("recommendedAction") or "")
        support.reinforcement = [
            ReinforcementGroup(name="支撑纵筋", bar_type="longitudinal", diameter=float(longitudinal.get("diameterMm") or 25), count=int(longitudinal.get("count") or 8), grade=str(longitudinal.get("grade") or "HRB400"), check_status="pass" if item.get("status") == "pass" else "warning", location_description="按轴压承载力配置，并与箍筋、侧面构造筋及节点净距协同校核"),
            ReinforcementGroup(name="支撑侧面构造分布筋", bar_type="distribution", diameter=float(distribution.get("diameterMm") or 14), spacing=float(distribution.get("spacingMm") or 200), grade="HRB400", check_status="manual_review", location_description="支撑四个侧面连续配置；用于裂缝分散、箍筋定位和钢筋骨架稳定"),
            ReinforcementGroup(name="支撑端部加密箍筋", bar_type="stirrup", diameter=float(end_zone.get("stirrupDiameterMm") or 12), spacing=float(end_zone.get("stirrupSpacingMm") or 100), grade="HRB400", check_status="manual_review", location_description=f"支撑两端各 {end_zone.get('lengthM')}m 范围加密配置"),
            ReinforcementGroup(name="支撑跨中箍筋", bar_type="stirrup", diameter=float(middle.get("stirrupDiameterMm") or 12), spacing=float(middle.get("stirrupSpacingMm") or 180), grade="HRB400", check_status="manual_review", location_description="支撑跨中普通区抗剪与约束箍筋"),
            ReinforcementGroup(name="支撑拉结/架立筋", bar_type="tie", diameter=float(ties.get("diameterMm") or 12), spacing=float(ties.get("spacingMm") or 400), grade="HRB400", check_status="manual_review", location_description="约束侧面纵筋并保持钢筋骨架尺寸，端部节点区按大样加密"),
            ReinforcementGroup(name="搭接与锚固区附加筋", bar_type="additional", diameter=float(lap_additional.get("diameterMm") or max(16, float(longitudinal.get("diameterMm") or 25) - 6)), count=int(lap_additional.get("count") or 4), grade="HRB400", check_status="manual_review", location_description="错开布置于跨中搭接区并避开端部刚域；具体锚固长度由施工图复核"),
        ]

    beam_map = {str(item.get("hostId")): item for item in scheme.get("beamNodeSchemes", []) if item.get("hostType") == "wale_or_crown_beam"}
    expected_beam_types = {"longitudinal", "distribution", "stirrup", "tie", "additional"}
    beam_contracts: list[dict[str, Any]] = []
    for beam in [*project.retaining_system.crown_beams, *project.retaining_system.wale_beams, *(project.retaining_system.ring_beams or [])]:
        item = beam_map.get(beam.id)
        if not item:
            continue
        main = item.get("mainBars") or {}
        stirrups = item.get("stirrups") or {}
        distribution = item.get("distributionBars") or {}
        ties = item.get("tieBars") or {}
        status = "pass" if item.get("status") == "pass" else "warning"
        design = beam.design_result
        role_label = "冠梁" if beam.beam_role == "crown_beam" else "围檩"
        beam.reinforcement = [
            ReinforcementGroup(name=f"{role_label}上/下缘主筋", bar_type="longitudinal", diameter=float(main.get("diameterMm") or 25), spacing=float(main.get("spacingMm") or 150), grade="HRB400", area_per_meter=design.provided_reinforcement_area if design else None, required_area_per_meter=design.required_reinforcement_area if design else None, check_status=status, location_description=f"{beam.code} 沿梁长连续配置，转角与施工缝处按节点大样锚固"),
            ReinforcementGroup(name=f"{role_label}箍筋", bar_type="stirrup", diameter=float(stirrups.get("diameterMm") or 12), spacing=float(stirrups.get("spacingMm") or 150), grade="HRB400", check_status="manual_review", location_description=f"普通区按计算间距，转角、墙接头和支撑节点两侧采用 {stirrups.get('nodeSpacingMm') or 100}mm 加密"),
            ReinforcementGroup(name=f"{role_label}侧面构造筋", bar_type="distribution", diameter=float(distribution.get("diameterMm") or 14), spacing=float(distribution.get("spacingMm") or 200), grade="HRB400", check_status="manual_review", location_description="侧面连续构造配置，用于裂缝分散、箍筋定位和钢筋骨架稳定"),
            ReinforcementGroup(name=f"{role_label}拉结筋", bar_type="tie", diameter=float(ties.get("diameterMm") or 12), spacing=float(ties.get("spacingMm") or 400), grade="HRB400", check_status="manual_review", location_description="拉结上、下缘及侧面钢筋；转角和预埋件区加密"),
            ReinforcementGroup(name=f"{role_label}节点附加筋", bar_type="additional", diameter=20, spacing=150, grade="HRB400", check_status="manual_review", location_description=str(item.get("nodeAdditional") or "转角、墙接头、支撑节点和预埋件区配置 U 形筋、封闭箍筋和局部抗裂筋")),
        ]
        present = sorted({str(group.bar_type) for group in beam.reinforcement})
        missing = sorted(expected_beam_types.difference(present))
        beam_contracts.append({
            "hostId": beam.id, "hostCode": beam.code, "beamRole": beam.beam_role,
            "structuralStatus": item.get("status"), "expectedBarTypes": sorted(expected_beam_types),
            "presentBarTypes": present, "missingBarTypes": missing,
            "status": "complete" if not missing and design is not None else "incomplete",
        })
    expected_support_types = {"longitudinal", "distribution", "stirrup", "tie", "additional"}
    support_contracts: list[dict[str, Any]] = []
    for support in project.retaining_system.supports:
        if support.section_type != "rc_rectangular":
            continue
        present = sorted({str(item.bar_type) for item in (support.reinforcement or [])})
        missing = sorted(expected_support_types.difference(present))
        support_contracts.append({
            "hostId": support.id, "hostCode": support.code,
            "expectedBarTypes": sorted(expected_support_types), "presentBarTypes": present,
            "missingBarTypes": missing, "status": "complete" if not missing else "incomplete",
        })
    scheme["supportRebarContracts"] = support_contracts
    scheme["supportRebarContractSummary"] = {
        "supportCount": len(support_contracts),
        "completeCount": sum(1 for item in support_contracts if not item["missingBarTypes"]),
        "incompleteCount": sum(1 for item in support_contracts if item["missingBarTypes"]),
        "expectedBarTypes": sorted(expected_support_types),
    }
    scheme["beamRebarContracts"] = beam_contracts
    scheme["beamRebarContractSummary"] = {
        "beamCount": len(beam_contracts),
        "completeCount": sum(1 for item in beam_contracts if item["status"] == "complete"),
        "incompleteCount": sum(1 for item in beam_contracts if item["status"] != "complete"),
        "expectedBarTypes": sorted(expected_beam_types),
    }
    scheme["requiresRecalculation"] = bool(scheme.get("diagnostics", {}).get("sectionChangeCount"))
    project.retaining_system.rebar_design_scheme = scheme
    # Rebuild the gate after the five-family contracts have been written.  The
    # first preview is intentionally built before mutation; reusing that gate
    # here made the immediate response carry empty contract summaries and led
    # the UI to report “structure not closed” until a full page reload.
    scheme["diagnostics"] = _build_design_diagnostics(
        project,
        list(scheme.get("checks") or []),
        list(scheme.get("supportSchemes") or []),
        scheme_applied_override=True,
    )
    scheme["summary"]["canIssueConstructionDrawings"] = bool(scheme["diagnostics"].get("canIssueConstructionDrawings"))
    scheme["summary"]["reviewWatermarkRequired"] = bool(scheme["diagnostics"].get("reviewWatermarkRequired"))
    append_event(
        "rebar-contract", "support-contract-applied", projectId=project.id, mode=mode,
        requiresRecalculation=scheme["requiresRecalculation"], **scheme["supportRebarContractSummary"],
    )
    return scheme
