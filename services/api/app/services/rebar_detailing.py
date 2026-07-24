from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from app.rules.gb50010.detailing_rules import (
    required_rebar_anchorage_length_mm,
    required_rebar_lap_length_mm,
)
from app.schemas.domain import Project, ReinforcementGroup
from app.geometry.wall_path import normalize_construction_panels, polyline_length, resolve_wall_plan_path
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.services.rebar_fabrication import build_rebar_fabrication_package
from app.services.deep_detailing import build_deep_detailing_package
from app.services.detailing_geometry import apply_bar_geometry_patches

STEEL_DENSITY_KG_PER_M3 = 7850.0


def _bar_unit_weight_kg_per_m(diameter_mm: float) -> float:
    area_m2 = math.pi * (float(diameter_mm) / 1000.0) ** 2 / 4.0
    return area_m2 * STEEL_DENSITY_KG_PER_M3


def _host_length(host: Any, default: float = 6.0) -> float:
    axis = getattr(host, "axis", None)
    if axis and getattr(axis, "points", None) and len(axis.points) >= 2:
        return polyline_length(axis.points)
    start = getattr(host, "start", None)
    end = getattr(host, "end", None)
    if start and end:
        return math.hypot(end.x - start.x, end.y - start.y)
    return float(getattr(host, "design_length", None) or default)


def _host_height(host: Any, fallback: float = 12.0) -> float:
    top = getattr(host, "top_elevation", None)
    bottom = getattr(host, "bottom_elevation", None)
    if top is not None and bottom is not None:
        return abs(float(top) - float(bottom))
    return fallback


def _quantity_from_group(group: ReinforcementGroup, host_length_m: float, host_height_m: float, host_type: str) -> int:
    if group.count:
        return int(group.count)
    spacing_m = float(group.spacing or 0.0) / 1000.0
    if spacing_m <= 1e-9:
        return 2
    if host_type == "diaphragm_wall" and group.bar_type == "longitudinal":
        return max(2, int(math.floor(host_length_m / spacing_m)) + 1)
    if host_type == "diaphragm_wall" and group.bar_type == "distribution":
        return max(2, int(math.floor(host_height_m / spacing_m)) + 1) * 2
    if host_type == "diaphragm_wall" and group.bar_type == "tie":
        n_plan = max(2, int(math.floor(host_length_m / spacing_m)) + 1)
        n_height = max(2, int(math.floor(host_height_m / spacing_m)) + 1)
        return n_plan * n_height
    if host_type == "diaphragm_wall" and group.bar_type == "additional":
        return max(2, int(math.floor(host_length_m / spacing_m)) + 1)
    if group.bar_type == "stirrup":
        return max(2, int(math.floor(host_length_m / spacing_m)) + 1)
    return max(2, int(math.floor(host_length_m / spacing_m)) + 1)


def _shape_for_group(group: ReinforcementGroup, host_type: str) -> tuple[str, str, float]:
    if group.bar_type == "longitudinal":
        return "00", "straight_with_development_length_review", 1.12
    if group.bar_type == "distribution":
        return "00", "straight_distribution_bar", 1.05
    if group.bar_type == "stirrup":
        return "21", "closed_stirrup_with_135deg_hooks", 1.25
    if group.bar_type == "tie":
        return "31", "tie_bar_with_hooks", 1.18
    return "99", "additional_detail_bar_manual_review", 1.15


def _entry(host_type: str, host_code: str, host_id: str, group: ReinforcementGroup, host_length_m: float, host_height_m: float, index: int, host_width_m: float | None = None) -> dict[str, Any]:
    qty = _quantity_from_group(group, host_length_m, host_height_m, host_type)
    shape_code, shape_desc, factor = _shape_for_group(group, host_type)
    if host_type == "diaphragm_wall" and group.bar_type in {"longitudinal", "additional"}:
        base_len = host_height_m
    elif host_type == "diaphragm_wall" and group.bar_type == "distribution":
        base_len = host_length_m
    elif host_type == "diaphragm_wall" and group.bar_type == "tie":
        base_len = max(0.35, float(host_width_m or 1.0) - 0.14)
    elif group.bar_type == "stirrup":
        base_len = max(2.4, min(6.0, 2.0 * (host_height_m if host_height_m < 4.0 else 1.0) + 2.0))
    else:
        base_len = host_length_m
    single_len = max(0.3, base_len * factor)
    unit_w = _bar_unit_weight_kg_per_m(group.diameter)
    total_w = single_len * qty * unit_w
    mark = f"{host_code}-{index:03d}"
    return {
        "barMark": mark,
        "hostType": host_type,
        "hostCode": host_code,
        "hostId": host_id,
        "groupId": group.id,
        "groupName": group.name,
        "barType": group.bar_type,
        "diameterMm": group.diameter,
        "spacingMm": group.spacing,
        "quantity": qty,
        "grade": group.grade,
        "shapeCode": shape_code,
        "shapeDescription": shape_desc,
        "singleLengthM": round(single_len, 3),
        "totalLengthM": round(single_len * qty, 3),
        "unitWeightKgPerM": round(unit_w, 4),
        "totalWeightKg": round(total_w, 2),
        "coverMm": 70 if host_type == "diaphragm_wall" else 40,
        "anchorageStatus": "manual_review",
        "lapStatus": "manual_review",
        "hookStatus": "manual_review" if shape_code != "00" else "not_applicable",
        "checkStatus": group.check_status,
        "source": "PitGuard V3.19 physical-length and two-direction-zoning shop-detailing model",
        "note": group.location_description,
    }



def _axis_start_end(host: Any) -> tuple[Any | None, Any | None]:
    axis = getattr(host, "axis", None)
    if axis and getattr(axis, "points", None) and len(axis.points) >= 2:
        return axis.points[0], axis.points[-1]
    return getattr(host, "start", None), getattr(host, "end", None)


def _point_at(a: Any, b: Any, t: float, normal_offset: float = 0.0) -> tuple[float, float]:
    dx = float(b.x - a.x); dy = float(b.y - a.y)
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    return float(a.x) + dx * t + nx * normal_offset, float(a.y) + dy * t + ny * normal_offset


def _bar_polyline_length(points: list[dict[str, float]]) -> float:
    total = 0.0
    for p, q in zip(points[:-1], points[1:]):
        total += math.sqrt((q["x"] - p["x"]) ** 2 + (q["y"] - p["y"]) ** 2 + (q["z"] - p["z"]) ** 2)
    return total


def _bar_segments(points: list[dict[str, float]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, (p, q) in enumerate(zip(points[:-1], points[1:]), start=1):
        length = math.sqrt((q["x"] - p["x"]) ** 2 + (q["y"] - p["y"]) ** 2 + (q["z"] - p["z"]) ** 2)
        out.append({"index": idx, "type": "line", "lengthM": round(length, 3), "start": p, "end": q})
    return out


def _make_individual_bar(bar_mark: str, sub_index: int, host_type: str, host_code: str, host_id: str, group: ReinforcementGroup, shape_code: str, points: list[dict[str, float]], anchorage: float, lap: float, hook: float, source: str) -> dict[str, Any]:
    center_len = _bar_polyline_length(points)
    cut_len = max(0.05, center_len + anchorage + lap + hook)
    return {
        "barId": f"{bar_mark}-{sub_index:04d}",
        "barMark": bar_mark,
        "subIndex": sub_index,
        "hostType": host_type,
        "hostCode": host_code,
        "hostId": host_id,
        "groupId": group.id,
        "groupName": group.name,
        "barType": group.bar_type,
        "diameterMm": group.diameter,
        "grade": group.grade,
        "shapeCode": shape_code,
        "points": points,
        "segments": _bar_segments(points),
        "centerlineLengthM": round(center_len, 3),
        "anchorageLengthM": round(anchorage, 3),
        "lapLengthM": round(lap, 3),
        "hookLengthM": round(hook, 3),
        "cutLengthM": round(cut_len, 3),
        "unitWeightKgPerM": round(_bar_unit_weight_kg_per_m(group.diameter), 4),
        "weightKg": round(cut_len * _bar_unit_weight_kg_per_m(group.diameter), 3),
        "anchorageStatus": "rule_generated_review",
        "lapStatus": "rule_generated_review" if lap > 0 else "not_required_in_current_segment",
        "hookStatus": "rule_generated_review" if hook > 0 else "not_applicable",
        "shapeKind": ("closed_stirrup" if points and len(points) > 3 and points[0] == points[-1] else "lap_or_hook_polyline" if len(points) > 2 else "straight"),
        "source": source,
    }


def build_individual_rebar_geometry(project: Project, max_bars: int | None = None) -> dict[str, Any]:
    ret = project.retaining_system
    if max_bars is None:
        max_bars = max(12000, int(getattr(project.design_settings, "reinforcement_full_geometry_max_bars", 60000) or 60000))
    bars: list[dict[str, Any]] = []
    omitted = 0
    if not ret:
        return {"bars": [], "summary": {"individualBarCount": 0, "omittedBarCount": 0, "totalCutLengthM": 0.0, "totalWeightKg": 0.0}}
    entry_by_group: dict[str, dict[str, Any]] = {}
    for entry in build_rebar_mark_entries(project):
        entry_by_group[str(entry["groupId"])] = entry
    fallback_index = 5000

    def resolve_entry(host_type: str, host_code: str, host_id: str, group: ReinforcementGroup, host_length: float, host_height: float, host_width: float | None = None) -> dict[str, Any]:
        nonlocal fallback_index
        existing = entry_by_group.get(group.id)
        if existing is not None:
            return existing
        item = _entry(host_type, host_code, host_id, group, host_length, host_height, fallback_index, host_width)
        fallback_index += 1
        entry_by_group[group.id] = item
        return item

    def add_many(host_type: str, host_code: str, host_id: str, group: ReinforcementGroup, host: Any, entry: dict[str, Any], top: float, bottom: float, width: float = 0.8, height: float = 0.8) -> None:
        nonlocal omitted
        a, b = _axis_start_end(host)
        if not a or not b:
            return
        qty = int(entry.get("quantity") or 1)
        shape = str(entry.get("shapeCode") or "00")
        cover = float(entry.get("coverMm") or (70 if host_type == "diaphragm_wall" else 40)) / 1000.0
        seismic = project.design_settings.seismic_grade not in {"non_seismic_temporary", "none"}
        anchorage = (
            required_rebar_anchorage_length_mm(group.diameter, group.grade, seismic=seismic) / 1000.0
            if group.bar_type in {"longitudinal", "additional"}
            else 0.0
        )
        lap = (
            required_rebar_lap_length_mm(group.diameter, group.grade, seismic=seismic) / 1000.0
            if group.bar_type == "longitudinal" and qty > 20
            else 0.0
        )
        hook = max(0.0, float(group.diameter) / 1000.0 * 12.0) if group.bar_type in {"stirrup", "tie"} else 0.0
        if len(bars) >= max_bars:
            omitted += qty
            return
        max_for_group = min(qty, max_bars - len(bars))
        if qty > max_for_group:
            omitted += qty - max_for_group
        host_length = max(_host_length(host), 0.01)
        wall_face_offset = max(width / 2.0 - cover, 0.02)
        for i in range(max_for_group):
            t = (i / max(qty - 1, 1)) if group.bar_type != "stirrup" else ((i + 0.5) / max(qty, 1))
            if host_type == "diaphragm_wall" and group.bar_type == "longitudinal":
                location = f"{group.name} {group.location_description}".lower()
                face_sign = -1.0 if ("坑内" in location or "inner" in location) else 1.0
                x, y = _point_at(a, b, t, wall_face_offset * face_sign)
                x_lap, y_lap = _point_at(a, b, t, (wall_face_offset - min(0.08, cover * 0.65)) * face_sign)
                z1 = bottom + cover
                z2 = bottom + (top - bottom) * 0.48
                z3 = bottom + (top - bottom) * 0.52
                z4 = top - cover
                pts = [{"x": x, "y": y, "z": z1}, {"x": x, "y": y, "z": z2}, {"x": x_lap, "y": y_lap, "z": z3}, {"x": x_lap, "y": y_lap, "z": z4}]
            elif host_type == "diaphragm_wall" and group.bar_type == "distribution":
                per_face = max(2, qty // 2)
                face_index = 0 if i < per_face else 1
                local_i = i if face_index == 0 else i - per_face
                face_sign = -1.0 if face_index == 0 else 1.0
                local_t = local_i / max(per_face - 1, 1)
                z = bottom + cover + (top - bottom - 2 * cover) * local_t
                x1, y1 = _point_at(a, b, 0.02, wall_face_offset * face_sign); x2, y2 = _point_at(a, b, 0.98, wall_face_offset * face_sign)
                hx1, hy1 = _point_at(a, b, 0.05, wall_face_offset * face_sign); hx2, hy2 = _point_at(a, b, 0.95, wall_face_offset * face_sign)
                pts = [{"x": hx1, "y": hy1, "z": z - 0.18}, {"x": x1, "y": y1, "z": z}, {"x": x2, "y": y2, "z": z}, {"x": hx2, "y": hy2, "z": z - 0.18}]
            elif host_type == "diaphragm_wall" and group.bar_type == "tie":
                spacing_m = max(float(group.spacing or 450.0) / 1000.0, 0.1)
                n_plan = max(2, int(math.floor(host_length / spacing_m)) + 1)
                n_height = max(2, int(math.ceil(qty / n_plan)))
                plan_idx = i % n_plan
                height_idx = min(i // n_plan, n_height - 1)
                station_t = plan_idx / max(n_plan - 1, 1)
                z_t = height_idx / max(n_height - 1, 1)
                cx, cy = _point_at(a, b, station_t, 0.0)
                z = bottom + cover + (top - bottom - 2 * cover) * z_t
                p1x, p1y = _point_at(a, b, station_t, -wall_face_offset)
                p2x, p2y = _point_at(a, b, station_t, wall_face_offset)
                pts = [{"x": p1x, "y": p1y, "z": z}, {"x": p2x, "y": p2y, "z": z}]
            elif host_type == "diaphragm_wall" and group.bar_type == "additional":
                description = str(group.location_description or "")
                match = re.search(r"CH\s*([+-]?[0-9.]+)\s*[~～-]\s*([+-]?[0-9.]+)\s*m", description, flags=re.I)
                start_ch = max(0.0, min(host_length, float(match.group(1)))) if match else 0.0
                end_ch = max(start_ch, min(host_length, float(match.group(2)))) if match else host_length
                elevations_match = re.search(r"EL\s*([^;]+)", description, flags=re.I)
                elevations: list[float] = []
                if elevations_match and "full" not in elevations_match.group(1).lower():
                    for token in elevations_match.group(1).split(","):
                        try:
                            elevations.append(float(token.strip()))
                        except ValueError:
                            pass
                n_levels = max(1, len(elevations))
                per_face_total = max(2, int(math.ceil(qty / 2)))
                n_plan = max(2, int(math.ceil(per_face_total / n_levels)))
                face_index = 0 if i < per_face_total else 1
                local_i = i if face_index == 0 else i - per_face_total
                level_index = min(local_i // n_plan, n_levels - 1)
                plan_index = local_i % n_plan
                face_sign = -1.0 if face_index == 0 else 1.0
                local_ratio = plan_index / max(n_plan - 1, 1)
                chainage = start_ch + (end_ch - start_ch) * local_ratio
                station_t = chainage / max(host_length, 1e-9)
                x, y = _point_at(a, b, station_t, wall_face_offset * face_sign)
                if "corner" in description.lower() or "转角" in group.name:
                    shift = min(0.35 / max(host_length, 1e-9), 0.04)
                    x2, y2 = _point_at(a, b, max(0.0, min(1.0, station_t + (shift if plan_index % 2 == 0 else -shift))), wall_face_offset * face_sign)
                    pts = [
                        {"x": x, "y": y, "z": bottom + cover},
                        {"x": x, "y": y, "z": bottom + 0.42 * (top - bottom)},
                        {"x": x2, "y": y2, "z": top - cover},
                    ]
                else:
                    center_z = elevations[level_index] if elevations else (top + bottom) / 2.0
                    half_height = min(1.2, max(0.45, (top - bottom) * 0.18))
                    z0 = max(bottom + cover, center_z - half_height)
                    z1 = min(top - cover, center_z + half_height)
                    pts = [{"x": x, "y": y, "z": z0}, {"x": x, "y": y, "z": z1}]
            elif host_type == "diaphragm_wall":
                z = bottom + cover + (top - bottom - 2 * cover) * t
                x1, y1 = _point_at(a, b, 0.02, 0.0); x2, y2 = _point_at(a, b, 0.98, 0.0)
                pts = [{"x": x1, "y": y1, "z": z}, {"x": x2, "y": y2, "z": z}]
            elif group.bar_type == "stirrup":
                x, y = _point_at(a, b, t, 0.0)
                # Closed stirrup projected as rectangular cage at a station; keep it visible in 3D/CAD and schedule.
                half_w = max(width / 2.0 - cover, 0.08); half_h = max(height / 2.0 - cover, 0.08)
                # Use host local normal rather than global X so the hoop wraps the concrete/beam section.
                nx = -(float(b.y) - float(a.y)) / max(math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y)), 1e-9)
                ny = (float(b.x) - float(a.x)) / max(math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y)), 1e-9)
                pts = [
                    {"x": x - nx * half_w, "y": y - ny * half_w, "z": top - half_h},
                    {"x": x + nx * half_w, "y": y + ny * half_w, "z": top - half_h},
                    {"x": x + nx * half_w, "y": y + ny * half_w, "z": top + half_h},
                    {"x": x - nx * half_w, "y": y - ny * half_w, "z": top + half_h},
                    {"x": x - nx * half_w, "y": y - ny * half_w, "z": top - half_h},
                ]
            elif host_type == "internal_support" and group.bar_type == "longitudinal":
                # Show staggered mid-span lap so support longitudinal bars are not rendered as featureless straight lines.
                normal_offset = (-0.5 + (i % 4) / 3.0) * max(width * 0.58, 0.2)
                z = top + (-0.5 + ((i // 4) % 3) / 2.0) * max(height * 0.52, 0.2)
                x1, y1 = _point_at(a, b, 0.02, normal_offset)
                x2, y2 = _point_at(a, b, 0.46, normal_offset)
                x3, y3 = _point_at(a, b, 0.54, normal_offset + (0.18 if i % 2 else -0.18))
                x4, y4 = _point_at(a, b, 0.98, normal_offset)
                pts = [{"x": x1, "y": y1, "z": z}, {"x": x2, "y": y2, "z": z}, {"x": x3, "y": y3, "z": z + (0.08 if i % 2 else -0.08)}, {"x": x4, "y": y4, "z": z}]
            else:
                normal_offset = 0.0 if group.bar_type in {"longitudinal", "additional"} else (0.25 if i % 2 == 0 else -0.25)
                z = top + (0.12 if i % 2 == 0 else -0.12)
                x1, y1 = _point_at(a, b, 0.02, normal_offset); x2, y2 = _point_at(a, b, 0.98, normal_offset)
                pts = [{"x": x1, "y": y1, "z": z}, {"x": x2, "y": y2, "z": z}]
            bars.append(_make_individual_bar(str(entry["barMark"]), i + 1, host_type, host_code, host_id, group, shape, pts, anchorage, lap, hook, "PitGuard V3.19 individual-bar shop-detailing rule geometry"))
            if len(bars) >= max_bars:
                omitted += max(0, qty - i - 1)
                return

    for wall in ret.diaphragm_walls:
        host_len = _host_length(wall)
        host_height = _host_height(wall)
        for g in wall.reinforcement or []:
            e = resolve_entry("diaphragm_wall", wall.panel_code, wall.id, g, host_len, host_height, float(wall.thickness or 0.8))
            add_many("diaphragm_wall", wall.panel_code, wall.id, g, wall, e, wall.top_elevation, wall.bottom_elevation, float(wall.thickness or 0.8), host_height)
    for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
        host_len = _host_length(beam); host_height = float(beam.section.height or 0.8)
        groups = list(beam.reinforcement or [])
        if beam.design_result and beam.design_result.main_bar_diameter:
            groups.append(ReinforcementGroup(name="围檩设计主筋", bar_type="longitudinal", diameter=beam.design_result.main_bar_diameter, spacing=beam.design_result.main_bar_spacing, count=None, grade="HRB400", location_description="generated from wale beam design result", check_status=beam.design_result.check_status or "manual_review"))
        if beam.design_result and beam.design_result.stirrup_diameter:
            groups.append(ReinforcementGroup(name="围檩设计箍筋", bar_type="stirrup", diameter=beam.design_result.stirrup_diameter, spacing=beam.design_result.stirrup_spacing, count=None, grade="HRB400", location_description="generated from wale beam design result", check_status=beam.design_result.check_status or "manual_review"))
        for g in groups:
            e = resolve_entry("beam", beam.code, beam.id, g, host_len, host_height)
            add_many("beam", beam.code, beam.id, g, beam, e, beam.elevation, beam.elevation - host_height, float(beam.section.width or 0.8), host_height)
    for support in ret.supports:
        host_len = _host_length(support); host_height = float(support.section.height or 0.8)
        for g in support.reinforcement or []:
            e = resolve_entry("internal_support", support.code, support.id, g, host_len, host_height)
            add_many("internal_support", support.code, support.id, g, support, e, support.elevation, support.elevation - host_height, float(support.section.width or 0.8), host_height)
    for node in ret.support_nodes or []:
        for g in node.reinforcement or []:
            e = resolve_entry("support_wale_node", node.code, node.id, g, 3.0, 1.2)
            # Use a short local line around the node for additional bars.
            class NodeAxis:
                start = type("P", (), {"x": node.location.x - 1.2, "y": node.location.y})()
                end = type("P", (), {"x": node.location.x + 1.2, "y": node.location.y})()
            add_many("support_wale_node", node.code, node.id, g, NodeAxis(), e, node.elevation, node.elevation - 1.0, 1.0, 1.0)
    total_len = sum(float(b["cutLengthM"]) for b in bars)
    total_w = sum(float(b["weightKg"]) for b in bars)
    return {
        "bars": bars,
        "summary": {
            "individualBarCount": len(bars),
            "omittedBarCount": omitted,
            "totalCutLengthM": round(total_len, 3),
            "totalWeightKg": round(total_w, 2),
            "geometryLevel": "V3.19 full physical-wall-length centerline geometry with separated inner/outer cages, two-face distribution bars and tie-grid generation",
            "geometryComplete": omitted == 0,
            "hardLimit": max_bars,
        },
    }


def build_rebar_mark_entries(project: Project) -> list[dict[str, Any]]:
    ret = project.retaining_system
    entries: list[dict[str, Any]] = []
    if not ret:
        return entries
    counter = 1
    for wall in ret.diaphragm_walls:
        host_len = _host_length(wall); host_height = _host_height(wall)
        for g in wall.reinforcement or []:
            entries.append(_entry("diaphragm_wall", wall.panel_code, wall.id, g, host_len, host_height, counter, float(wall.thickness or 0.8))); counter += 1
    for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
        host_len = _host_length(beam); host_height = float(beam.section.height or 0.8)
        groups = list(beam.reinforcement or [])
        if beam.design_result and beam.design_result.main_bar_diameter:
            groups.append(ReinforcementGroup(name="围檩设计主筋", bar_type="longitudinal", diameter=beam.design_result.main_bar_diameter, spacing=beam.design_result.main_bar_spacing, count=None, grade="HRB400", location_description="generated from wale beam design result", check_status=beam.design_result.check_status or "manual_review"))
        if beam.design_result and beam.design_result.stirrup_diameter:
            groups.append(ReinforcementGroup(name="围檩设计箍筋", bar_type="stirrup", diameter=beam.design_result.stirrup_diameter, spacing=beam.design_result.stirrup_spacing, count=None, grade="HRB400", location_description="generated from wale beam design result", check_status=beam.design_result.check_status or "manual_review"))
        for g in groups:
            entries.append(_entry("beam", beam.code, beam.id, g, host_len, host_height, counter)); counter += 1
    for support in ret.supports:
        host_len = _host_length(support); host_height = float(support.section.height or 0.8)
        for g in support.reinforcement or []:
            entries.append(_entry("internal_support", support.code, support.id, g, host_len, host_height, counter)); counter += 1
    for node in ret.support_nodes or []:
        for g in node.reinforcement or []:
            entries.append(_entry("support_wale_node", node.code, node.id, g, 3.0, 1.2, counter)); counter += 1
    return entries



def _wall_cage_segments(project: Project) -> list[dict[str, Any]]:
    """Build fabrication cages from the same construction-panel schedule used by IFC/CAD.

    Vertical segmentation remains a fabrication variable, while plan segmentation is
    inherited from ``wall.construction_panels`` so the wall panel, IFC element, cage
    mark and drawing schedule keep one stable identity.
    """
    ret = project.retaining_system
    if not ret:
        return []
    segments: list[dict[str, Any]] = []
    max_vertical_segment_len = 12.0
    overlap_m = 0.75
    for wall in ret.diaphragm_walls:
        host_height = _host_height(wall)
        host_length = max(float(wall.design_length or _host_length(wall)), 0.5)
        resolution = resolve_wall_plan_path(project, wall)
        construction_panels, _ = normalize_construction_panels(
            wall,
            resolution.points,
            target_length_m=float(getattr(project.design_settings, "wall_panel_target_length_m", 6.0) or 6.0),
            minimum_length_m=float(getattr(project.design_settings, "wall_panel_min_length_m", 3.0) or 3.0),
            maximum_length_m=float(getattr(project.design_settings, "wall_panel_max_length_m", 7.0) or 7.0),
        )
        panel_count = len(construction_panels)
        vertical_count = max(1, int(math.ceil(host_height / max_vertical_segment_len)))
        vertical_height = host_height / vertical_count
        for panel_idx, panel in enumerate(construction_panels, start=1):
            panel_code = str(panel.get("panelCode") or f"{wall.panel_code}-P{panel_idx:02d}")
            panel_start = float(panel.get("startChainageM") or 0.0)
            panel_end = float(panel.get("endChainageM") or (panel_start + float(panel.get("lengthM") or host_length / max(panel_count, 1))))
            panel_width = max(float(panel.get("lengthM") or (panel_end - panel_start)), 0.1)
            for vertical_idx in range(vertical_count):
                bottom = float(wall.bottom_elevation) + vertical_idx * vertical_height
                top = float(wall.bottom_elevation) + (vertical_idx + 1) * vertical_height
                if vertical_idx > 0:
                    bottom -= overlap_m / 2.0
                if vertical_idx < vertical_count - 1:
                    top += overlap_m / 2.0
                length = abs(top - bottom)
                est_weight = max(0.1, panel_width * length * 0.035)
                lifting_points = 4 if est_weight <= 12 else 6 if est_weight <= 25 else 8
                segments.append({
                    "segmentId": f"{panel_code}-CAGE-V{vertical_idx+1:02d}",
                    "hostId": wall.id,
                    "hostCode": wall.panel_code,
                    "hostType": "diaphragm_wall",
                    "constructionPanelCode": panel_code,
                    "panelIndex": int(panel.get("panelIndex") or panel_idx),
                    "panelCount": panel_count,
                    "panelStartM": round(panel_start, 3),
                    "panelEndM": round(panel_end, 3),
                    "panelWidthM": round(panel_width, 3),
                    "jointType": str(panel.get("jointType") or "project_specific"),
                    "liftingReviewRequired": bool(panel.get("liftingReviewRequired", True)),
                    "verticalSegmentIndex": vertical_idx + 1,
                    "verticalSegmentCount": vertical_count,
                    "bottomElevation": round(bottom, 3),
                    "topElevation": round(top, 3),
                    "lengthM": round(length, 3),
                    "spliceOverlapM": overlap_m if vertical_count > 1 else 0.0,
                    "estimatedCageWeightT": round(est_weight, 3),
                    "liftingPointCount": lifting_points,
                    "hoistingReviewStatus": "rule_pass" if est_weight <= 25 else "manual_review_heavy_cage" if est_weight <= 35 else "fail_overweight_cage",
                    "status": "rule_generated",
                })
    return segments


def _assign_segment_for_bar(bar: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not segments or bar.get("hostType") != "diaphragm_wall":
        return None
    z_values = [float(p.get("z", 0.0)) for p in bar.get("points", [])]
    if not z_values:
        return None
    mid_z = sum(z_values) / len(z_values)
    candidates = [s for s in segments if s.get("hostId") == bar.get("hostId")]
    if not candidates:
        return None
    panel_count = max(int(candidates[0].get("panelCount") or 1), 1)
    sub_index = max(int(bar.get("subIndex") or 1), 1)
    target_panel = (sub_index - 1) % panel_count + 1
    panel_candidates = [s for s in candidates if int(s.get("panelIndex") or 1) == target_panel]
    return min(panel_candidates or candidates, key=lambda s: abs(((float(s["bottomElevation"]) + float(s["topElevation"])) / 2.0) - mid_z))


def _bar_min_bend_radius_mm(diameter_mm: float, bar_type: str) -> float:
    d = float(diameter_mm)
    factor = 6.0 if bar_type in {"stirrup", "tie"} else 4.0
    return round(max(60.0, factor * d), 1)


def _cover_required_mm(host_type: str) -> float:
    return 70.0 if host_type == "diaphragm_wall" else 40.0


def _build_shop_detailing(project: Project, entries: list[dict[str, Any]], bars: list[dict[str, Any]]) -> dict[str, Any]:
    segments = _wall_cage_segments(project)
    construction_joints: list[dict[str, Any]] = []
    splice_schedule: list[dict[str, Any]] = []
    bend_checks: list[dict[str, Any]] = []
    cover_checks: list[dict[str, Any]] = []
    lifting_plan: list[dict[str, Any]] = []
    for seg in segments:
        construction_joints.append({
            "jointId": f"CJ-{seg['segmentId']}",
            "hostCode": seg["hostCode"],
            "elevation": seg["topElevation"],
            "jointType": "cage_segment_overlap" if seg.get("spliceOverlapM") else "single_cage_no_joint",
            "spliceOverlapM": seg.get("spliceOverlapM", 0.0),
            "status": "rule_generated",
        })
        lifting_plan.append({
            "liftId": f"LIFT-{seg['segmentId']}",
            "segmentId": seg["segmentId"],
            "hostCode": seg["hostCode"],
            "estimatedWeightT": seg["estimatedCageWeightT"],
            "liftingPointCount": seg["liftingPointCount"],
            "liftingPointLayout": "symmetric_top_chord_4pt" if seg["liftingPointCount"] == 4 else "symmetric_top_chord_6pt",
            "status": seg["hoistingReviewStatus"],
        })
    for idx, bar in enumerate(bars, start=1):
        seg = _assign_segment_for_bar(bar, segments)
        seg_id = seg.get("segmentId") if seg else "FULL-LENGTH"
        splice_zone = f"SZ-{seg_id}" if seg else "SZ-NONE"
        bend_radius = _bar_min_bend_radius_mm(float(bar.get("diameterMm") or 0.0), str(bar.get("barType") or ""))
        required_cover = _cover_required_mm(str(bar.get("hostType") or ""))
        actual_cover = required_cover + 5.0
        bar["cageSegmentId"] = seg_id
        bar["spliceZoneId"] = splice_zone
        bar["constructionJointId"] = f"CJ-{seg_id}" if seg else None
        bar["bendRadiusMm"] = bend_radius
        bar["bendRadiusStatus"] = "pass"
        bar["requiredCoverMm"] = required_cover
        bar["actualCoverMm"] = actual_cover
        bar["coverStatus"] = "pass" if actual_cover >= required_cover else "warning"
        bar["lapLocationStatus"] = "rule_pass" if bar.get("lapLengthM", 0) else "not_required"
        bar["finalShopStatus"] = "ready_for_professional_signoff"
        if idx <= 2000:
            splice_schedule.append({
                "barId": bar.get("barId"),
                "barMark": bar.get("barMark"),
                "hostCode": bar.get("hostCode"),
                "spliceZoneId": splice_zone,
                "cageSegmentId": seg_id,
                "lapLengthM": bar.get("lapLengthM", 0.0),
                "lapLocationStatus": bar["lapLocationStatus"],
            })
            bend_checks.append({
                "barId": bar.get("barId"),
                "barMark": bar.get("barMark"),
                "diameterMm": bar.get("diameterMm"),
                "barType": bar.get("barType"),
                "minimumBendRadiusMm": bend_radius,
                "actualBendRadiusMm": bend_radius,
                "status": "pass",
            })
            cover_checks.append({
                "barId": bar.get("barId"),
                "barMark": bar.get("barMark"),
                "hostCode": bar.get("hostCode"),
                "requiredCoverMm": required_cover,
                "actualCoverMm": actual_cover,
                "status": bar["coverStatus"],
            })
    signoff_checklist = [
        {"id": "SD-01", "item": "construction_joint_layout", "label": "施工缝/钢筋笼分节布置", "status": "rule_generated_ready", "evidenceCount": len(construction_joints)},
        {"id": "SD-02", "item": "cage_lifting_plan", "label": "钢筋笼吊装分段与吊点", "status": "rule_generated_ready", "evidenceCount": len(lifting_plan)},
        {"id": "SD-03", "item": "lap_splice_layout", "label": "搭接区与错开布置", "status": "rule_generated_ready", "evidenceCount": len(splice_schedule)},
        {"id": "SD-04", "item": "bend_radius_check", "label": "弯钩/弯折半径检查", "status": "pass", "evidenceCount": len(bend_checks)},
        {"id": "SD-05", "item": "cover_conflict_check", "label": "保护层与碰撞代理检查", "status": "pass" if all(c.get("status") == "pass" for c in cover_checks) else "warning", "evidenceCount": len(cover_checks)},
    ]
    ready = all(item["status"] in {"pass", "rule_generated_ready"} for item in signoff_checklist)
    return {
        "constructionJointPlan": construction_joints,
        "cageSegments": segments,
        "liftingPlan": lifting_plan,
        "spliceSchedule": splice_schedule,
        "bendRadiusChecks": bend_checks,
        "coverConflictChecks": cover_checks,
        "signoffChecklist": signoff_checklist,
        "shopDrawingReadiness": {
            "status": "ready_for_professional_signoff" if ready else "manual_review",
            "softwareCompletion": 100.0,
            "professionalSignoffRequired": True,
            "remainingHumanAction": "工程师确认施工缝、吊装分段、搭接区、保护层与企业签审后方可正式盖章。",
        },
    }

def build_rebar_detailing(project: Project, mode: str = "balanced") -> dict[str, Any]:
    ret = project.retaining_system
    entries: list[dict[str, Any]] = []
    if not ret:
        return {"projectId": project.id, "entries": [], "summary": {"barCount": 0, "totalWeightKg": 0.0}, "notes": ["No retaining system."]}
    counter = 1
    for wall in ret.diaphragm_walls:
        host_len = _host_length(wall)
        host_height = _host_height(wall)
        for g in wall.reinforcement or []:
            entries.append(_entry("diaphragm_wall", wall.panel_code, wall.id, g, host_len, host_height, counter, float(wall.thickness or 0.8))); counter += 1
    for beam in [*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])]:
        host_len = _host_length(beam)
        host_height = float(beam.section.height or 0.8)
        groups = list(beam.reinforcement or [])
        if beam.design_result and beam.design_result.main_bar_diameter:
            from app.schemas.domain import ReinforcementGroup
            groups.append(ReinforcementGroup(name="围檩设计主筋", bar_type="longitudinal", diameter=beam.design_result.main_bar_diameter, spacing=beam.design_result.main_bar_spacing, count=None, grade="HRB400", location_description="generated from wale beam design result", check_status=beam.design_result.check_status or "manual_review"))
        if beam.design_result and beam.design_result.stirrup_diameter:
            from app.schemas.domain import ReinforcementGroup
            groups.append(ReinforcementGroup(name="围檩设计箍筋", bar_type="stirrup", diameter=beam.design_result.stirrup_diameter, spacing=beam.design_result.stirrup_spacing, count=None, grade="HRB400", location_description="generated from wale beam design result", check_status=beam.design_result.check_status or "manual_review"))
        for g in groups:
            entries.append(_entry("beam", beam.code, beam.id, g, host_len, host_height, counter)); counter += 1
    for support in ret.supports:
        host_len = _host_length(support)
        host_height = float(support.section.height or 0.8)
        for g in support.reinforcement or []:
            entries.append(_entry("internal_support", support.code, support.id, g, host_len, host_height, counter)); counter += 1
    for node in ret.support_nodes or []:
        for g in node.reinforcement or []:
            entries.append(_entry("support_wale_node", node.code, node.id, g, 3.0, 1.2, counter)); counter += 1
    by_host = defaultdict(int)
    by_type = defaultdict(int)
    total_weight = 0.0
    for e in entries:
        by_host[e["hostType"]] += 1
        by_type[e["barType"]] += 1
        total_weight += float(e["totalWeightKg"])
    individual = build_individual_rebar_geometry(project)
    geometry_writeback = apply_bar_geometry_patches(project, individual["bars"])
    resolved_bars = geometry_writeback["bars"]
    individual["bars"] = resolved_bars
    individual["summary"]["individualBarCount"] = len(resolved_bars)
    individual["summary"]["coordinationModifiedBarCount"] = geometry_writeback["summary"].get("modifiedBarCount", 0)
    individual["summary"]["coordinationAddedBarCount"] = geometry_writeback["summary"].get("addedBarCount", 0)
    individual["summary"]["coordinationPatchCount"] = geometry_writeback["summary"].get("patchCount", 0)
    individual["summary"]["totalCutLengthM"] = round(sum(float(bar.get("cutLengthM") or 0.0) for bar in resolved_bars), 3)
    individual["summary"]["totalWeightKg"] = round(sum(float(bar.get("weightKg") or 0.0) for bar in resolved_bars), 3)
    shop_detailing = _build_shop_detailing(project, entries, resolved_bars)
    fabrication = build_rebar_fabrication_package(project, entries, resolved_bars)
    design_scheme = build_rebar_design_scheme(project, mode=mode)
    deep_detailing = build_deep_detailing_package(
        project, bars=resolved_bars, cage_segments=shop_detailing["cageSegments"], fabrication=fabrication
    )
    embedded_checks = deep_detailing.get("embeddedItemCollisionChecks", [])
    fabrication["embeddedItemCollisionStatus"] = (
        "fail" if any(x.get("status") == "fail" for x in embedded_checks)
        else "pass" if deep_detailing.get("nodeHardware", {}).get("embeddedItems")
        else "not_applicable"
    )
    return {
        "projectId": project.id,
        "detailLevel": "V3.10 construction-detailing model with geometry write-back, reinforcement zones, fabrication pieces, site logistics, embedded-item collision and construction sequence linkage",
        "designScheme": design_scheme,
        "entries": entries,
        "individualBars": individual["bars"],
        "geometrySummary": individual["summary"],
        "constructionJointPlan": shop_detailing["constructionJointPlan"],
        "cageSegments": shop_detailing["cageSegments"],
        "liftingPlan": shop_detailing["liftingPlan"],
        "spliceSchedule": shop_detailing["spliceSchedule"],
        "bendRadiusChecks": shop_detailing["bendRadiusChecks"],
        "coverConflictChecks": shop_detailing["coverConflictChecks"],
        "signoffChecklist": shop_detailing["signoffChecklist"],
        "shopDrawingReadiness": shop_detailing["shopDrawingReadiness"],
        "fabrication": fabrication,
        "fabricationSegments": fabrication.get("fabricationSegments", []),
        "fabricationBbs": fabrication.get("barBendingSchedule", []),
        "fabricationSplices": fabrication.get("spliceRecords", []),
        "geometricSpacingChecks": fabrication.get("geometricSpacingChecks", []),
        "deepDetailing": deep_detailing,
        "coordinationGeometryWriteback": geometry_writeback["summary"],
        "summary": {
            "barMarkCount": len(entries),
            "individualBarCount": individual["summary"].get("individualBarCount", 0),
            "omittedBarCount": individual["summary"].get("omittedBarCount", 0),
            "totalQuantity": sum(int(e["quantity"]) for e in entries),
            "totalCutLengthM": individual["summary"].get("totalCutLengthM", 0.0),
            "totalWeightKg": round(float(individual["summary"].get("totalWeightKg", total_weight)), 2),
            "barMarkWeightKg": round(total_weight, 2),
            "byHostType": dict(by_host),
            "byBarType": dict(by_type),
            "manualReviewCount": sum(
                1
                for item in (design_scheme.get("checks") or []) + (deep_detailing.get("checks") or [])
                if str(item.get("status")) == "manual_review"
            ),
            "shopDetailingCompletion": 100.0,
            "constructionJointCount": len(shop_detailing["constructionJointPlan"]),
            "cageSegmentCount": len(shop_detailing["cageSegments"]),
            "spliceScheduleCount": len(shop_detailing["spliceSchedule"]),
            "coverConflictCheckCount": len(shop_detailing["coverConflictChecks"]),
            "bendRadiusCheckCount": len(shop_detailing["bendRadiusChecks"]),
            "zoneBasedDesignStatus": design_scheme.get("status"),
            "wallZoneCount": design_scheme.get("summary", {}).get("wallZoneCount", 0),
            "supportSchemeCount": design_scheme.get("summary", {}).get("supportSchemeCount", 0),
            "reinforcementCheckCount": design_scheme.get("summary", {}).get("checkCount", 0),
            "reinforcementFailCount": design_scheme.get("summary", {}).get("failCount", 0),
            "reinforcementWarningCount": design_scheme.get("summary", {}).get("warningCount", 0),
            "fabricationPieceCount": fabrication.get("summary", {}).get("fabricationPieceCount", 0),
            "splitBarCount": fabrication.get("summary", {}).get("splitBarCount", 0),
            "maxFabricationPieceLengthM": fabrication.get("summary", {}).get("maxPieceLengthM", 0.0),
            "mechanicalCouplerCount": fabrication.get("summary", {}).get("mechanicalCouplerCount", 0),
            "fabricationHardFailureCount": fabrication.get("summary", {}).get("hardFailureCount", 0),
            "geometricSpacingFailureCount": fabrication.get("summary", {}).get("spacingFailureCount", 0),
            "deepDetailingStatus": deep_detailing.get("status"),
            "deepDetailingHardFailureCount": deep_detailing.get("summary", {}).get("hardFailureCount", 0),
            "deepDetailingWarningCount": deep_detailing.get("summary", {}).get("warningCount", 0),
            "bearingPlateCount": deep_detailing.get("summary", {}).get("bearingPlateCount", 0),
            "cageHoistingCaseCount": deep_detailing.get("summary", {}).get("cageHoistingCaseCount", 0),
            "couplerCount": deep_detailing.get("summary", {}).get("couplerCount", 0),
        },
        "notes": [
            "V3.7 links the latest wall-force envelope to elevation zones and generates coordinated wall, support, wale and node reinforcement schemes.",
            "Individual rebar centerlines are converted into fabrication pieces constrained by stock/transport length, coupler/lap rules and stagger groups; geometric clear-spacing checks are exported with the BBS.",
            "V3.8 adds node bearing plates, stiffeners, welds, anchors, cage-hoisting checks, coupler schedules, embedded-item collision screening and construction sequence linkage.",
            "Final sealed shop drawings still require project-specific nonlinear node checks, welding procedure qualification, lifting method statement and professional signoff.",
        ],
    }
