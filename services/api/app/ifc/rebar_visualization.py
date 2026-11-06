from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from app.schemas.domain import BeamElement, Point2D, Polyline2D, Project, ReinforcementGroup, SupportElement
from app.geometry.wall_path import (
    normalize_construction_panels,
    offset_polyline,
    point_tangent_at_chainage,
    polyline_length,
    resolve_wall_plan_path,
)
from app.services.runtime_diagnostics import append_event


def _dist(a: Point2D, b: Point2D) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def _unit(a: Point2D, b: Point2D) -> tuple[float, float, float]:
    length = _dist(a, b)
    if length <= 1e-9:
        return 1.0, 0.0, 0.0
    return (b.x - a.x) / length, (b.y - a.y) / length, length


def _pt(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": round(float(x), 4), "y": round(float(y), 4), "z": round(float(z), 4)}


def _bar_length(start: dict[str, float], end: dict[str, float]) -> float:
    return math.sqrt((end["x"] - start["x"]) ** 2 + (end["y"] - start["y"]) ** 2 + (end["z"] - start["z"]) ** 2)


def _count_from_spacing(length_m: float, spacing_mm: float | None, fallback: int = 6, cap: int = 32) -> tuple[int, int]:
    if spacing_mm and spacing_mm > 1e-6:
        estimated = max(2, int(math.floor(length_m / (spacing_mm / 1000.0))) + 1)
    else:
        estimated = max(2, fallback)
    sampled = min(cap, estimated)
    return sampled, estimated


def _stations(length_m: float, count: int, margin: float = 0.25) -> list[float]:
    if count <= 1:
        return [length_m / 2.0]
    lo = min(max(margin, 0.0), max(length_m / 4.0, 0.0))
    hi = max(length_m - lo, lo)
    return [lo + (hi - lo) * i / (count - 1) for i in range(count)]


def _group_token(group: ReinforcementGroup) -> str:
    token = f"{group.name} D{group.diameter:g}"
    if group.spacing:
        token += f"@{group.spacing:g}"
    if group.count:
        token += f"x{group.count}"
    return token


def _polyline_length(points: list[dict[str, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(_bar_length(a, b) for a, b in zip(points[:-1], points[1:]))


def _make_bar(
    *,
    host_type: str,
    host_code: str,
    host_id: str,
    group: ReinforcementGroup,
    start: dict[str, float],
    end: dict[str, float],
    index: int,
    representation: str,
    estimated_count: int | None = None,
    sampled_from_count: int | None = None,
    ifc_class: str = "IfcReinforcingBar",
    points: list[dict[str, float]] | None = None,
    shape_kind: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    poly = points or [start, end]
    length = _polyline_length(poly)
    row = {
        "id": f"rbviz-{host_code}-{group.id}-{index}",
        "ifcClass": ifc_class,
        "hostType": host_type,
        "hostCode": host_code,
        "hostId": host_id,
        "groupId": group.id,
        "groupName": group.name,
        "barType": group.bar_type,
        "diameterMm": group.diameter,
        "spacingMm": group.spacing,
        "count": group.count,
        "grade": group.grade,
        "locationDescription": group.location_description,
        "checkStatus": group.check_status,
        "start": poly[0],
        "end": poly[-1],
        "points": poly,
        "lengthM": round(length, 3),
        "representation": representation,
        "shapeKind": shape_kind or ("closed_stirrup" if group.bar_type == "stirrup" else "polyline"),
        "estimatedFullCount": estimated_count,
        "sampledFromCount": sampled_from_count,
    }
    if extra:
        row.update(extra)
    return row


def _add_wall_zone_rebars(bars: list[dict[str, Any]], wall, zones: list[dict[str, Any]], max_per_wall: int = 120) -> tuple[int, int]:
    path = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    length = polyline_length(path)
    if len(path) < 2 or length <= 1.0e-9 or not zones:
        return 0, 0
    cover = min(max(wall.thickness * 0.38, 0.08), max(wall.thickness / 2.2, 0.08))
    sampled_total = 0
    estimated_total = 0
    ordered = sorted(zones, key=lambda item: float(item.get("topElevation") or wall.top_elevation), reverse=True)
    for zone_index, zone in enumerate(ordered, start=1):
        if sampled_total >= max_per_wall:
            break
        top = min(float(zone.get("topElevation") or wall.top_elevation), float(wall.top_elevation))
        bottom = max(float(zone.get("bottomElevation") or wall.bottom_elevation), float(wall.bottom_elevation))
        zone_height = max(top - bottom, 0.25)
        zone_id = str(zone.get("zoneId") or f"zone-{zone_index}")
        zone_type = str(zone.get("zoneType") or "field_zone")
        drawing_refs = list(zone.get("drawingRefs") or [])
        envelope_source = str(zone.get("envelopeSource") or "calculated_moment")
        overlap = min(0.35, zone_height * 0.12)
        for face_row in zone.get("faces", []):
            face = str(face_row.get("face") or "inner")
            face_sign = -1 if face == "inner" else 1
            dia = float(face_row.get("barDiameterMm") or 25.0)
            spacing = float(face_row.get("barSpacingMm") or 200.0)
            group = ReinforcementGroup(
                id=f"rebar-zone-{wall.id}-{zone_index}-{face}",
                name=f"{zone_id} {'坑内侧' if face == 'inner' else '坑外侧'}竖向主筋",
                bar_type="longitudinal",
                diameter=dia,
                spacing=spacing,
                grade=wall.rebar_grade,
                location_description=f"{zone_id} {zone_type}; EL {top:.3f}~{bottom:.3f}; linked to {','.join(drawing_refs)}",
                area_per_meter=float(face_row.get("providedAsMm2PerM") or 0.0),
                required_area_per_meter=float(face_row.get("requiredAsMm2PerM") or 0.0),
                check_status=str(face_row.get("status") or "manual_review"),
            )
            visual_pitch = 4.0
            dynamic_cap = max(8, min(32, int(math.ceil(length / visual_pitch)) + 1))
            sampled, estimated = _count_from_spacing(length, spacing, fallback=8, cap=min(dynamic_cap, max_per_wall - sampled_total))
            estimated_total += estimated
            for idx, station in enumerate(_stations(length, sampled), start=1):
                center, tangent, _ = point_tangent_at_chainage(path, station)
                nx, ny = -tangent[1], tangent[0]
                x = center.x + nx * cover * face_sign
                y = center.y + ny * cover * face_sign
                z0 = max(float(wall.bottom_elevation) + 0.12, bottom - (overlap if zone_index < len(ordered) else 0.0))
                z1 = min(float(wall.top_elevation) - 0.12, top + (overlap if zone_index > 1 else 0.0))
                bars.append(_make_bar(
                    host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=group,
                    start=_pt(x, y, z0), end=_pt(x, y, z1), index=zone_index * 100 + idx,
                    representation="wall_zone_vertical_bar_on_canonical_path", shape_kind="zone_vertical_segment",
                    estimated_count=estimated, sampled_from_count=sampled,
                    extra={"zoneId": zone_id, "zoneType": zone_type, "face": face, "drawingRefs": drawing_refs, "envelopeSource": envelope_source, "zoneTopElevation": top, "zoneBottomElevation": bottom},
                ))
            sampled_total += sampled
        if sampled_total >= max_per_wall:
            break
        horizontal = zone.get("horizontalDistribution") or {}
        h_dia = float(horizontal.get("diameterMm") or 16.0)
        h_spacing = float(horizontal.get("spacingMm") or 200.0)
        h_group = ReinforcementGroup(
            id=f"rebar-zone-{wall.id}-{zone_index}-horizontal", name=f"{zone_id} 水平分布筋", bar_type="distribution",
            diameter=h_dia, spacing=h_spacing, grade=wall.rebar_grade,
            location_description=f"{zone_id} horizontal distribution; EL {top:.3f}~{bottom:.3f}", check_status=str(zone.get("status") or "manual_review"),
        )
        dynamic_vertical_cap = max(4, min(16, int(math.ceil(zone_height / 2.0)) + 1))
        sample_z, estimate_z = _count_from_spacing(zone_height, h_spacing, fallback=4, cap=min(dynamic_vertical_cap, max(1, (max_per_wall - sampled_total) // 2)))
        estimated_total += estimate_z * 2
        z_values = [bottom + min(0.2, zone_height * 0.15) + max(zone_height - min(0.4, zone_height * 0.3), 0.0) * i / max(sample_z - 1, 1) for i in range(sample_z)]
        for face_index, face_sign in enumerate((-1, 1), start=1):
            offset_path = offset_polyline(path, cover * face_sign)
            for zidx, z in enumerate(z_values, start=1):
                if sampled_total >= max_per_wall:
                    break
                pts = [_pt(point.x, point.y, z) for point in offset_path]
                bars.append(_make_bar(
                    host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=h_group,
                    start=pts[0], end=pts[-1], index=zone_index * 1000 + face_index * 100 + zidx,
                    representation="wall_zone_horizontal_distribution_bar_on_canonical_path", shape_kind="zone_horizontal_polyline",
                    points=pts, estimated_count=estimate_z * 2, sampled_from_count=sample_z * 2,
                    extra={"zoneId": zone_id, "zoneType": zone_type, "face": "inner" if face_sign < 0 else "outer", "drawingRefs": drawing_refs, "envelopeSource": envelope_source, "zoneTopElevation": top, "zoneBottomElevation": bottom},
                ))
                sampled_total += 1
        if sampled_total < max_per_wall:
            tie = zone.get("tieBars") or {}
            tie_group = ReinforcementGroup(
                id=f"rebar-zone-{wall.id}-{zone_index}-tie", name=f"{zone_id} 拉结筋", bar_type="tie",
                diameter=float(tie.get("diameterMm") or 12.0), spacing=float(tie.get("spacingMm") or 450.0), grade=wall.rebar_grade,
                location_description=f"{zone_id} cage tie bar", check_status="manual_review",
            )
            center, tangent, _ = point_tangent_at_chainage(path, length * 0.5)
            nx, ny = -tangent[1], tangent[0]
            z = (top + bottom) / 2.0
            bars.append(_make_bar(
                host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=tie_group,
                start=_pt(center.x - nx * cover, center.y - ny * cover, z), end=_pt(center.x + nx * cover, center.y + ny * cover, z), index=zone_index,
                representation="wall_zone_sampled_tie_bar_on_canonical_path", estimated_count=max(1, int(length / max(float(tie.get("spacingMm") or 450.0) / 1000.0, 0.1))), sampled_from_count=1,
                extra={"zoneId": zone_id, "zoneType": zone_type, "drawingRefs": drawing_refs, "envelopeSource": envelope_source, "zoneTopElevation": top, "zoneBottomElevation": bottom},
            ))
            sampled_total += 1
    return sampled_total, estimated_total

def _add_wall_rebars(bars: list[dict[str, Any]], wall, max_per_wall: int = 110) -> tuple[int, int]:
    path = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    length = polyline_length(path)
    if len(path) < 2 or length <= 1.0e-9:
        return 0, 0
    height = max(wall.top_elevation - wall.bottom_elevation, 0.5)
    cover = min(max(wall.thickness * 0.38, 0.08), max(wall.thickness / 2.2, 0.08))
    sampled_total = 0
    estimated_total = 0
    for group in wall.reinforcement:
        if sampled_total >= max_per_wall:
            break
        if group.bar_type == "longitudinal":
            face_sign = -1 if ("内" in group.name or "inner" in group.location_description.lower()) else 1
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=8, cap=min(28, max_per_wall - sampled_total))
            estimated_total += estimated
            for idx, station in enumerate(_stations(length, sampled), start=1):
                center, tangent, _ = point_tangent_at_chainage(path, station)
                nx, ny = -tangent[1], tangent[0]
                x = center.x + nx * cover * face_sign
                y = center.y + ny * cover * face_sign
                bars.append(_make_bar(
                    host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=group,
                    start=_pt(x, y, wall.bottom_elevation + 0.15), end=_pt(x, y, wall.top_elevation - 0.15), index=idx,
                    representation="wall_vertical_bar_on_canonical_path_with_lap_offset",
                    points=[_pt(x, y, wall.bottom_elevation + 0.15), _pt(x, y, wall.bottom_elevation + height * 0.48), _pt(x + nx * cover * 0.65 * face_sign, y + ny * cover * 0.65 * face_sign, wall.bottom_elevation + height * 0.52), _pt(x + nx * cover * 0.65 * face_sign, y + ny * cover * 0.65 * face_sign, wall.top_elevation - 0.15)],
                    shape_kind="vertical_lap_polyline", estimated_count=estimated, sampled_from_count=sampled,
                ))
            sampled_total += sampled
        elif group.bar_type == "distribution":
            sampled_z, estimated_z = _count_from_spacing(height, group.spacing, fallback=8, cap=min(18, max_per_wall - sampled_total))
            estimated_total += estimated_z * 2
            z_values = [wall.bottom_elevation + 0.3 + (height - 0.6) * i / max(sampled_z - 1, 1) for i in range(sampled_z)]
            for face_index, face_sign in enumerate((-1, 1), start=1):
                offset_path = offset_polyline(path, cover * face_sign)
                for zidx, z in enumerate(z_values, start=1):
                    pts = [_pt(point.x, point.y, z) for point in offset_path]
                    bars.append(_make_bar(
                        host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=group,
                        start=pts[0], end=pts[-1], index=face_index * 1000 + zidx,
                        representation="wall_distribution_bar_on_canonical_path", points=pts,
                        shape_kind="horizontal_path_polyline", estimated_count=estimated_z * 2, sampled_from_count=sampled_z * 2,
                    ))
                    sampled_total += 1
                    if sampled_total >= max_per_wall:
                        break
                if sampled_total >= max_per_wall:
                    break
        elif group.bar_type in {"tie", "additional"}:
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=6, cap=min(10, max_per_wall - sampled_total))
            estimated_total += estimated
            z = wall.bottom_elevation + height * 0.45
            for idx, station in enumerate(_stations(length, sampled), start=1):
                center, tangent, _ = point_tangent_at_chainage(path, station)
                nx, ny = -tangent[1], tangent[0]
                bars.append(_make_bar(
                    host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=group,
                    start=_pt(center.x - nx * cover, center.y - ny * cover, z), end=_pt(center.x + nx * cover, center.y + ny * cover, z), index=idx,
                    representation="sampled_wall_tie_bars_across_canonical_path", estimated_count=estimated, sampled_from_count=sampled,
                ))
            sampled_total += sampled
    return sampled_total, estimated_total


_SUPPORT_EXPECTED_BAR_TYPES = ("longitudinal", "distribution", "stirrup", "tie", "additional")

def _support_reinforcement_contract(support: SupportElement, scheme_row: dict[str, Any] | None) -> tuple[list[ReinforcementGroup], dict[str, Any]]:
    """Resolve five bar families and physical stirrup zones without false completion."""
    source_groups = list(support.reinforcement or [])
    source_present = {str(item.bar_type) for item in source_groups}
    groups = list(source_groups)
    synthesized: list[str] = []
    row = dict(scheme_row or {})
    longitudinal = dict(row.get("longitudinal") or {})
    end_zone = dict(row.get("endZones") or {})
    middle = dict(row.get("middleZone") or {})
    distribution = dict(row.get("distributionBars") or {})
    ties = dict(row.get("tieBars") or {})
    lap_additional = dict(row.get("lapAdditionalBars") or {})
    transverse_design = dict(row.get("transverseDesign") or {})
    status = "pass" if str(row.get("status") or "") == "pass" else "manual_review"
    grade = str(longitudinal.get("grade") or "HRB400")
    _, _, span_m = _unit(support.start, support.end)
    if row:
        end_length_m = min(float(end_zone.get("lengthM") or 0.0), span_m / 2.0)
        middle_length_m = max(float(middle.get("lengthM") or 0.0), 0.0)
        middle_start_m = end_length_m
        middle_end_limit_m = max(span_m - end_length_m, middle_start_m)
        middle_end_m = min(middle_start_m + middle_length_m, middle_end_limit_m)
        if middle_length_m < 0.05:
            middle_end_m = middle_start_m
        design_status = str(transverse_design.get("status") or "manual_review")
        groups = [
            ReinforcementGroup(id=f"rebar-{support.id}-longitudinal", name="支撑纵筋", bar_type="longitudinal", diameter=float(longitudinal.get("diameterMm") or 25), count=int(longitudinal.get("count") or 8), grade=grade, check_status=status, location_description="来自已应用配筋方案的轴压纵筋", zone_type="full_length", zone_start_m=0.0, zone_end_m=span_m, zone_length_m=span_m, design_source="applied_rebar_design_scheme"),
            ReinforcementGroup(id=f"rebar-{support.id}-distribution", name="支撑侧面构造分布筋", bar_type="distribution", diameter=float(distribution.get("diameterMm") or 14), spacing=float(distribution.get("spacingMm") or 200), grade=grade, check_status="manual_review", location_description="来自已应用配筋方案的四侧面构造分布筋", zone_type="full_length", zone_start_m=0.0, zone_end_m=span_m, zone_length_m=span_m, design_source="applied_rebar_design_scheme"),
            ReinforcementGroup(id=f"rebar-{support.id}-stirrup-end", name="支撑端部加密箍筋", bar_type="stirrup", diameter=float(end_zone.get("stirrupDiameterMm") or 12), spacing=float(end_zone.get("stirrupSpacingMm") or 100), grade=grade, check_status=design_status, location_description=f"A、B 两端各 {end_length_m:.2f}m 加密区", zone_type="end_zones", zone_start_m=0.0, zone_end_m=span_m, zone_length_m=end_length_m, stirrup_legs=int(end_zone.get("geometricLegCount") or transverse_design.get("geometricLegCount") or 4), design_source="support_transverse_design"),
            ReinforcementGroup(id=f"rebar-{support.id}-stirrup-middle", name="支撑跨中箍筋", bar_type="stirrup", diameter=float(middle.get("stirrupDiameterMm") or 12), spacing=float(middle.get("stirrupSpacingMm") or 180), grade=grade, check_status=design_status, location_description="跨中普通区箍筋" if middle_end_m > middle_start_m else "短支撑由两端加密区控制，不另设跨中普通区", zone_type="middle_zone", zone_start_m=middle_start_m, zone_end_m=middle_end_m, zone_length_m=max(middle_end_m - middle_start_m, 0.0), stirrup_legs=int(middle.get("geometricLegCount") or transverse_design.get("geometricLegCount") or 4), design_source="support_transverse_design"),
            ReinforcementGroup(id=f"rebar-{support.id}-tie", name="支撑拉结/架立筋", bar_type="tie", diameter=float(ties.get("diameterMm") or 12), spacing=float(ties.get("spacingMm") or 400), grade=grade, check_status="manual_review", location_description="来自已应用配筋方案的骨架拉结与架立筋", zone_type="full_length", zone_start_m=0.0, zone_end_m=span_m, zone_length_m=span_m, design_source="applied_rebar_design_scheme"),
            ReinforcementGroup(id=f"rebar-{support.id}-additional", name="搭接与锚固区附加筋", bar_type="additional", diameter=float(lap_additional.get("diameterMm") or max(16.0, float(longitudinal.get("diameterMm") or 25) - 6.0)), count=int(lap_additional.get("count") or 4), grade=grade, check_status="manual_review", location_description="跨中错开搭接与锚固区附加筋", zone_type="lap_zone", zone_start_m=span_m * 0.40, zone_end_m=span_m * 0.60, zone_length_m=span_m * 0.20, design_source="applied_rebar_design_scheme"),
        ]
        synthesized = sorted({str(group.bar_type) for group in groups if group.bar_type not in source_present})

    final_present = sorted({str(item.bar_type) for item in groups})
    missing = [name for name in _SUPPORT_EXPECTED_BAR_TYPES if name not in final_present]
    end_zone_groups = [item for item in groups if item.bar_type == "stirrup" and (item.zone_type == "end_zones" or "端部" in item.name or "加密" in item.name)]
    middle_zone_groups = [item for item in groups if item.bar_type == "stirrup" and (item.zone_type == "middle_zone" or "跨中" in item.name)]
    missing_stirrup_zones: list[str] = []
    if not end_zone_groups:
        missing_stirrup_zones.extend(["end_left", "end_right"])
    if not middle_zone_groups and span_m > 1.0:
        missing_stirrup_zones.append("middle")
    stirrup_zone_status = "complete" if not missing_stirrup_zones else "generic_or_incomplete"
    return groups, {
        "hostId": support.id, "hostCode": support.code,
        "expectedBarTypes": list(_SUPPORT_EXPECTED_BAR_TYPES),
        "sourceBarTypes": sorted(source_present),
        "resolvedBarTypes": final_present, "synthesizedBarTypes": sorted(set(synthesized)),
        "missingBarTypes": missing, "schemeRowFound": bool(row),
        "stirrupZoneStatus": stirrup_zone_status,
        "missingStirrupZones": missing_stirrup_zones,
        "stirrupZones": {
            "end": [{"groupId": item.id, "diameterMm": item.diameter, "spacingMm": item.spacing, "lengthM": item.zone_length_m} for item in end_zone_groups],
            "middle": [{"groupId": item.id, "diameterMm": item.diameter, "spacingMm": item.spacing, "startM": item.zone_start_m, "endM": item.zone_end_m} for item in middle_zone_groups],
        },
        "transverseDesign": transverse_design or None,
        "status": "complete" if not missing and not missing_stirrup_zones else "incomplete",
    }


def _support_longitudinal_offsets(count: int, width: float, height: float) -> list[tuple[float, float]]:
    n = max(2, min(count, 16))
    # Perimeter-like distribution in local section: lateral offset in plan, vertical offset in elevation.
    perimeter: list[tuple[float, float]] = []
    rows = max(2, min(4, int(math.ceil(n / 4))))
    cols = max(2, int(math.ceil(n / rows)))
    for r in range(rows):
        for c in range(cols):
            if len(perimeter) >= n:
                break
            lateral = (-0.5 + c / max(cols - 1, 1)) * max(width * 0.72, 0.2)
            vertical = (-0.5 + r / max(rows - 1, 1)) * max(height * 0.62, 0.2)
            perimeter.append((lateral, vertical))
    return perimeter


def _stations_between(start_m: float, end_m: float, count: int) -> list[float]:
    lo = max(float(start_m), 0.0)
    hi = max(float(end_m), lo)
    length = hi - lo
    if count <= 1 or length <= 1e-9:
        return [(lo + hi) / 2.0]
    inset = min(0.12, length * 0.08)
    return [lo + inset + max(length - 2.0 * inset, 0.0) * i / (count - 1) for i in range(count)]


def _support_stirrup_regions(group: ReinforcementGroup, length_m: float, height_m: float) -> list[tuple[str, str, float, float]]:
    zone_type = str(group.zone_type or "")
    name = str(group.name or "")
    if not zone_type:
        zone_type = "end_zones" if ("端部" in name or "加密" in name) else "middle_zone" if "跨中" in name else "full_length"
    if zone_type == "end_zones":
        zone_length = min(max(float(group.zone_length_m or max(1.5 * height_m, 1.5)), 0.25), length_m / 2.0)
        return [("end_left", "A端加密区", 0.0, zone_length), ("end_right", "B端加密区", max(length_m - zone_length, zone_length), length_m)]
    if zone_type == "middle_zone":
        start = min(max(float(group.zone_start_m or 0.0), 0.0), length_m)
        end = min(max(float(group.zone_end_m if group.zone_end_m is not None else length_m), start), length_m)
        # Treat rounding remnants as a closed zone.  A 1–2 mm pseudo middle
        # region otherwise produces overlapping stirrups at the support centre.
        return [] if end - start < 0.05 else [("middle", "跨中普通区", start, end)]
    return [("full_length", "全长通用区", 0.0, length_m)]


def _add_support_rebars(bars: list[dict[str, Any]], support: SupportElement, groups: list[ReinforcementGroup] | None = None, max_per_support: int = 58) -> tuple[int, int]:
    width = max((support.section.width if support.section else None) or 0.8, 0.35)
    height = max((support.section.height if support.section else None) or 0.8, 0.35)
    ux, uy, length = _unit(support.start, support.end)
    nx, ny = -uy, ux
    sampled_total = 0
    estimated_total = 0
    # Reserve a visible quota for every reinforcement family.  The former
    # sequential sampler allowed longitudinal bars and the first stirrup group
    # to exhaust the complete preview budget, hiding ties and local bars.
    type_caps = {"longitudinal": 12, "distribution": 6, "stirrup": 22, "tie": 5, "additional": 5}
    used_by_type: dict[str, int] = {}
    priority = {"longitudinal": 0, "distribution": 1, "stirrup": 2, "tie": 3, "additional": 4}
    resolved_groups = sorted(list(groups if groups is not None else (support.reinforcement or [])), key=lambda item: priority.get(item.bar_type, 99))
    for group in resolved_groups:
        if sampled_total >= max_per_support:
            break
        type_remaining = max(0, type_caps.get(group.bar_type, 4) - used_by_type.get(group.bar_type, 0))
        remaining = min(max_per_support - sampled_total, type_remaining)
        if remaining <= 0:
            continue
        before = sampled_total
        if group.bar_type == "longitudinal":
            count = min(group.count or 8, remaining)
            estimated_total += group.count or count
            for idx, (lat, dz) in enumerate(_support_longitudinal_offsets(count, width, height), start=1):
                sx = support.start.x + nx * lat + ux * 0.25
                sy = support.start.y + ny * lat + uy * 0.25
                ex = support.end.x + nx * lat - ux * 0.25
                ey = support.end.y + ny * lat - uy * 0.25
                z = support.elevation + dz
                lap_shift = (0.18 if idx % 2 else -0.18)
                lap_x1 = support.start.x + (support.end.x - support.start.x) * 0.47 + nx * (lat + lap_shift)
                lap_y1 = support.start.y + (support.end.y - support.start.y) * 0.47 + ny * (lat + lap_shift)
                lap_x2 = support.start.x + (support.end.x - support.start.x) * 0.53 + nx * (lat + lap_shift)
                lap_y2 = support.start.y + (support.end.y - support.start.y) * 0.53 + ny * (lat + lap_shift)
                pts = [_pt(sx, sy, z), _pt(lap_x1, lap_y1, z), _pt(lap_x2, lap_y2, z + (0.08 if idx % 3 == 0 else -0.08)), _pt(ex, ey, z)]
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=pts[0],
                    end=pts[-1],
                    index=idx,
                    representation="sampled_support_longitudinal_bars_with_staggered_lap",
                    points=pts,
                    shape_kind="support_lap_polyline",
                    estimated_count=group.count or count,
                    sampled_from_count=count,
                ))
            sampled_total += count
        elif group.bar_type == "stirrup":
            regions = _support_stirrup_regions(group, length, height)
            if not regions:
                continue
            group_added = 0
            region_count = len(regions)
            for region_index, (zone_key, zone_label, zone_start, zone_end) in enumerate(regions, start=1):
                remaining_for_group = max(0, remaining - group_added)
                if remaining_for_group <= 0:
                    break
                future_regions = max(region_count - region_index, 0)
                region_cap = max(1, (remaining_for_group - future_regions) // max(region_count - region_index + 1, 1))
                region_cap = min(region_cap, 6 if zone_key.startswith("end_") else 10)
                region_length = max(zone_end - zone_start, 0.05)
                sampled, estimated = _count_from_spacing(region_length, group.spacing, fallback=5 if zone_key.startswith("end_") else 8, cap=region_cap)
                estimated_total += estimated
                for local_index, station in enumerate(_stations_between(zone_start, zone_end, sampled), start=1):
                    cx = support.start.x + ux * station
                    cy = support.start.y + uy * station
                    z = support.elevation
                    bars.append(_make_bar(
                        host_type="internal_support", host_code=support.code, host_id=support.id, group=group,
                        start=_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42),
                        end=_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42),
                        index=region_index * 100 + local_index,
                        representation=f"sampled_support_closed_stirrups_{zone_key}",
                        points=[_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42), _pt(cx + nx * width * 0.42, cy + ny * width * 0.42, z - height * 0.42), _pt(cx + nx * width * 0.42, cy + ny * width * 0.42, z + height * 0.42), _pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z + height * 0.42), _pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42)],
                        shape_kind="closed_stirrup_rectangle", estimated_count=estimated, sampled_from_count=sampled,
                        extra={"stirrupZoneType": zone_key, "stirrupZoneLabel": zone_label, "zoneStartM": round(zone_start, 3), "zoneEndM": round(zone_end, 3), "previewStationM": round(station, 3), "geometricLegCount": int(group.stirrup_legs or 4), "designSource": group.design_source or "legacy_support_reinforcement"},
                    ))
                group_added += sampled
            sampled_total += group_added
        elif group.bar_type == "distribution":
            pair_cap = max(1, remaining // 2)
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=5, cap=min(3, pair_cap))
            estimated_total += estimated
            added = 0
            for idx, station in enumerate(_stations(length, sampled, margin=0.45), start=1):
                cx = support.start.x + ux * station
                cy = support.start.y + uy * station
                z_top = support.elevation + height * 0.28
                z_bot = support.elevation - height * 0.28
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=_pt(cx - nx * width * 0.38, cy - ny * width * 0.38, z_top),
                    end=_pt(cx + nx * width * 0.38, cy + ny * width * 0.38, z_top),
                    index=idx,
                    representation="sampled_support_distribution_bars_top_face",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
                added += 1
                if added >= remaining:
                    break
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=_pt(cx - nx * width * 0.38, cy - ny * width * 0.38, z_bot),
                    end=_pt(cx + nx * width * 0.38, cy + ny * width * 0.38, z_bot),
                    index=idx + sampled,
                    representation="sampled_support_distribution_bars_bottom_face",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
                added += 1
            sampled_total += added
        elif group.bar_type == "tie":
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=4, cap=min(4, remaining))
            estimated_total += estimated
            for idx, station in enumerate(_stations(length, sampled, margin=0.6), start=1):
                cx = support.start.x + ux * station
                cy = support.start.y + uy * station
                pts = [
                    _pt(cx - nx * width * 0.2, cy - ny * width * 0.2, support.elevation + height * 0.28),
                    _pt(cx - nx * width * 0.2, cy - ny * width * 0.2, support.elevation - height * 0.28),
                    _pt(cx + nx * width * 0.2, cy + ny * width * 0.2, support.elevation - height * 0.28),
                    _pt(cx + nx * width * 0.2, cy + ny * width * 0.2, support.elevation + height * 0.28),
                ]
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=pts[0],
                    end=pts[-1],
                    index=idx,
                    representation="sampled_support_tie_bars_u_shape",
                    points=pts,
                    shape_kind="support_tie_u_shape",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
        elif group.bar_type == "additional":
            count = min(group.count or 4, remaining)
            estimated_total += group.count or count
            lap_center = 0.5 * length
            for idx in range(count):
                side = -1 if idx % 2 == 0 else 1
                z = support.elevation + (0.22 if idx < count / 2 else -0.22) * height
                s = max(lap_center - 0.9 + idx * 0.05, 0.5)
                e = min(lap_center + 0.9 - idx * 0.05, length - 0.5)
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=_pt(support.start.x + ux * s + nx * side * width * 0.22, support.start.y + uy * s + ny * side * width * 0.22, z),
                    end=_pt(support.start.x + ux * e + nx * side * width * 0.22, support.start.y + uy * e + ny * side * width * 0.22, z),
                    index=idx + 1,
                    representation="sampled_support_additional_lap_zone_bars",
                    estimated_count=group.count or count,
                    sampled_from_count=count,
                ))
            sampled_total += count
        used_by_type[group.bar_type] = used_by_type.get(group.bar_type, 0) + max(0, sampled_total - before)
    return sampled_total, estimated_total


def _beam_axis(beam: BeamElement) -> tuple[Point2D, Point2D] | None:
    if len(beam.axis.points) < 2:
        return None
    return beam.axis.points[0], beam.axis.points[-1]


def _beam_groups(beam: BeamElement) -> list[ReinforcementGroup]:
    groups = list(beam.reinforcement or [])
    design = beam.design_result
    if design:
        if design.main_bar_diameter:
            groups.append(ReinforcementGroup(
                name="围檩/冠梁主筋",
                bar_type="longitudinal",
                diameter=design.main_bar_diameter,
                spacing=design.main_bar_spacing,
                count=4,
                grade="HRB400",
                location_description="synthesized from wale beam design result; explicit cage detailing pending",
                check_status=design.check_status or "manual_review",
            ))
        if design.stirrup_diameter:
            groups.append(ReinforcementGroup(
                name="围檩/冠梁箍筋",
                bar_type="stirrup",
                diameter=design.stirrup_diameter,
                spacing=design.stirrup_spacing,
                grade="HRB400",
                location_description="synthesized from wale beam design result; stirrup closed shape shown as sampled cross ties",
                check_status=design.check_status or "manual_review",
            ))
    return groups


def _add_beam_rebars(bars: list[dict[str, Any]], beam: BeamElement, max_per_beam: int = 36) -> tuple[int, int]:
    axis = _beam_axis(beam)
    if not axis:
        return 0, 0
    a, b = axis
    ux, uy, length = _unit(a, b)
    nx, ny = -uy, ux
    width = beam.section.width or 0.8
    height = beam.section.height or 0.8
    sampled_total = 0
    estimated_total = 0
    for group in _beam_groups(beam):
        if sampled_total >= max_per_beam:
            break
        if group.bar_type == "longitudinal":
            count = min(group.count or 4, max_per_beam - sampled_total)
            estimated_total += group.count or count
            for idx, (lat, dz) in enumerate(_support_longitudinal_offsets(count, width, height), start=1):
                z = beam.elevation + dz * 0.65
                bars.append(_make_bar(
                    host_type="wale_or_crown_beam",
                    host_code=beam.code,
                    host_id=beam.id,
                    group=group,
                    start=_pt(a.x + nx * lat + ux * 0.2, a.y + ny * lat + uy * 0.2, z),
                    end=_pt(b.x + nx * lat - ux * 0.2, b.y + ny * lat - uy * 0.2, z),
                    index=idx,
                    representation="sampled_beam_longitudinal_bars",
                    estimated_count=group.count or count,
                    sampled_from_count=count,
                ))
            sampled_total += count
        elif group.bar_type == "stirrup":
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=8, cap=min(14, max_per_beam - sampled_total))
            estimated_total += estimated
            for idx, station in enumerate(_stations(length, sampled, margin=0.25), start=1):
                cx = a.x + ux * station
                cy = a.y + uy * station
                bars.append(_make_bar(
                    host_type="wale_or_crown_beam",
                    host_code=beam.code,
                    host_id=beam.id,
                    group=group,
                    start=_pt(cx - nx * width * 0.4, cy - ny * width * 0.4, beam.elevation - height * 0.4),
                    end=_pt(cx - nx * width * 0.4, cy - ny * width * 0.4, beam.elevation - height * 0.4),
                    index=idx,
                    representation="sampled_beam_closed_stirrups",
                    points=[_pt(cx - nx * width * 0.4, cy - ny * width * 0.4, beam.elevation - height * 0.4), _pt(cx + nx * width * 0.4, cy + ny * width * 0.4, beam.elevation - height * 0.4), _pt(cx + nx * width * 0.4, cy + ny * width * 0.4, beam.elevation + height * 0.4), _pt(cx - nx * width * 0.4, cy - ny * width * 0.4, beam.elevation + height * 0.4), _pt(cx - nx * width * 0.4, cy - ny * width * 0.4, beam.elevation - height * 0.4)],
                    shape_kind="closed_stirrup_rectangle",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
    return sampled_total, estimated_total


def _add_node_rebars(bars: list[dict[str, Any]], node, max_per_node: int = 10) -> tuple[int, int]:
    sampled_total = 0
    estimated_total = 0
    for group in node.reinforcement:
        if sampled_total >= max_per_node:
            break
        if group.bar_type == "additional":
            count = min(group.count or 4, max_per_node - sampled_total)
            estimated_total += group.count or count
            for idx, angle in enumerate([2 * math.pi * i / max(count, 1) for i in range(count)], start=1):
                r = 0.25
                x = node.location.x + math.cos(angle) * r
                y = node.location.y + math.sin(angle) * r
                bars.append(_make_bar(
                    host_type="support_wale_node",
                    host_code=node.code,
                    host_id=node.id,
                    group=group,
                    start=_pt(x, y, node.elevation - 0.5),
                    end=_pt(x, y, node.elevation + 0.5),
                    index=idx,
                    representation="sampled_node_additional_vertical_bars",
                    estimated_count=group.count or count,
                    sampled_from_count=count,
                ))
            sampled_total += count
        elif group.bar_type == "stirrup":
            sampled, estimated = _count_from_spacing(1.2, group.spacing, fallback=4, cap=min(4, max_per_node - sampled_total))
            estimated_total += estimated
            for idx in range(sampled):
                z = node.elevation - 0.3 + idx * 0.2
                bars.append(_make_bar(
                    host_type="support_wale_node",
                    host_code=node.code,
                    host_id=node.id,
                    group=group,
                    start=_pt(node.location.x - 0.45, node.location.y - 0.45, z),
                    end=_pt(node.location.x - 0.45, node.location.y - 0.45, z),
                    index=idx + 1,
                    representation="sampled_node_closed_stirrups",
                    points=[_pt(node.location.x - 0.45, node.location.y - 0.45, z), _pt(node.location.x + 0.45, node.location.y - 0.45, z), _pt(node.location.x + 0.45, node.location.y + 0.45, z), _pt(node.location.x - 0.45, node.location.y + 0.45, z), _pt(node.location.x - 0.45, node.location.y - 0.45, z)],
                    shape_kind="closed_node_stirrup",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
    return sampled_total, estimated_total




def _stratified_bar_type_sample(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    """Sample reinforcement across bar families and hosts.

    A pure host round-robin still favors the first reinforcement family stored
    on every host.  For internal supports that made longitudinal bars consume
    the global budget before stirrups, distribution bars, ties and local bars
    were reached.  This sampler first reserves a bounded quota per bar family,
    then distributes each family across hosts.
    """
    if quota <= 0 or not items:
        return []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    type_order = ["longitudinal", "distribution", "stirrup", "tie", "additional"]
    for item in items:
        grouped[str(item.get("barType") or "other")].append(item)
    active = [name for name in type_order if grouped.get(name)]
    active.extend(sorted(name for name in grouped if name not in active))
    if not active:
        return _round_robin_host_sample(items, quota)

    weights = {
        "longitudinal": 0.28,
        "distribution": 0.14,
        "stirrup": 0.30,
        "tie": 0.13,
        "additional": 0.15,
    }
    weight_sum = sum(weights.get(name, 0.08) for name in active) or 1.0
    quotas: dict[str, int] = {}
    remaining = quota
    for index, name in enumerate(active):
        available = len(grouped[name])
        if index == len(active) - 1:
            assigned = min(available, remaining)
        else:
            raw = int(round(quota * weights.get(name, 0.08) / weight_sum))
            assigned = max(1, min(available, raw))
            assigned = min(assigned, max(1, remaining - (len(active) - index - 1)))
        quotas[name] = assigned
        remaining -= assigned

    if remaining > 0:
        for name in sorted(active, key=lambda row: len(grouped[row]) - quotas.get(row, 0), reverse=True):
            if remaining <= 0:
                break
            extra = min(len(grouped[name]) - quotas.get(name, 0), remaining)
            if extra > 0:
                quotas[name] = quotas.get(name, 0) + extra
                remaining -= extra

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for name in active:
        rows = _stratified_stirrup_zone_sample(grouped[name], quotas.get(name, 0)) if name == "stirrup" else _round_robin_host_sample(grouped[name], quotas.get(name, 0))
        selected.extend(rows)
        selected_ids.update(str(row.get("id")) for row in rows)
    if len(selected) < quota:
        residual = [row for row in items if str(row.get("id")) not in selected_ids]
        selected.extend(_round_robin_host_sample(residual, quota - len(selected)))
    return selected[:quota]


def _stratified_stirrup_zone_sample(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    """Keep A-end, middle and B-end stirrups visible in a global sample."""
    if quota <= 0 or not items:
        return []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order = ["end_left", "middle", "end_right", "full_length"]
    for item in items:
        grouped[str(item.get("stirrupZoneType") or "full_length")].append(item)
    active = [zone for zone in order if grouped.get(zone)]
    active.extend(sorted(zone for zone in grouped if zone not in active))
    if not active:
        return _round_robin_host_sample(items, quota)
    base = quota // len(active)
    remainder = quota % len(active)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for index, zone in enumerate(active):
        zone_quota = min(len(grouped[zone]), base + (1 if index < remainder else 0))
        rows = _round_robin_host_sample(grouped[zone], zone_quota)
        selected.extend(rows)
        selected_ids.update(str(row.get("id")) for row in rows)
    if len(selected) < quota:
        residual = [row for row in items if str(row.get("id")) not in selected_ids]
        selected.extend(_round_robin_host_sample(residual, quota - len(selected)))
    return selected[:quota]


def _round_robin_host_sample(items: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    if quota <= 0 or not items:
        return []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for item in items:
        key = str(item.get("hostId") or item.get("hostCode") or "unknown")
        if key not in grouped:
            order.append(key)
        grouped[key].append(item)
    # Hosts with failing/warning reinforcement are placed first, then stable input order.
    severity = {"fail": 0, "warning": 1, "manual_review": 2, "preliminary": 3, "pass": 4}
    order.sort(key=lambda key: min((severity.get(str(row.get("checkStatus")), 5) for row in grouped[key]), default=5))
    selected: list[dict[str, Any]] = []
    index = 0
    while len(selected) < quota:
        added = False
        for key in order:
            rows = grouped[key]
            if index < len(rows):
                selected.append(rows[index])
                added = True
                if len(selected) >= quota:
                    break
        if not added:
            break
        index += 1
    return selected


def _governing_wall_cage_reinforcement(wall: Any, zones: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a constructible cage summary for one calculation wall.

    Browser bars remain selectable samples.  Cage grids are a separate LOD
    representation generated per construction panel, with real design spacing
    and estimated counts so long walls no longer look almost unreinforced.
    """
    faces: dict[str, dict[str, float]] = {
        "inner": {"diameterMm": 25.0, "spacingMm": 200.0},
        "outer": {"diameterMm": 25.0, "spacingMm": 200.0},
    }
    horizontal = {"diameterMm": 16.0, "spacingMm": 200.0}
    ties = {"diameterMm": 12.0, "spacingMm": 450.0}
    zone_ids: list[str] = []
    for zone in zones:
        zone_ids.append(str(zone.get("zoneId") or ""))
        for row in zone.get("faces", []) or []:
            face = str(row.get("face") or "inner")
            current = faces.setdefault(face, {"diameterMm": 25.0, "spacingMm": 200.0})
            current["diameterMm"] = max(float(current.get("diameterMm") or 0.0), float(row.get("barDiameterMm") or 0.0))
            spacing = float(row.get("barSpacingMm") or current.get("spacingMm") or 200.0)
            current["spacingMm"] = min(float(current.get("spacingMm") or spacing), spacing)
        h = zone.get("horizontalDistribution") or {}
        horizontal["diameterMm"] = max(float(horizontal["diameterMm"]), float(h.get("diameterMm") or 0.0))
        horizontal["spacingMm"] = min(float(horizontal["spacingMm"]), float(h.get("spacingMm") or horizontal["spacingMm"]))
        t = zone.get("tieBars") or {}
        ties["diameterMm"] = max(float(ties["diameterMm"]), float(t.get("diameterMm") or 0.0))
        ties["spacingMm"] = min(float(ties["spacingMm"]), float(t.get("spacingMm") or ties["spacingMm"]))
    if not zones:
        for group in list(getattr(wall, "reinforcement", []) or []):
            token = str(getattr(group, "location_description", "") or "").lower()
            if group.bar_type == "longitudinal":
                face = "outer" if ("outer" in token or "坑外" in token) else "inner"
                faces[face]["diameterMm"] = max(float(faces[face]["diameterMm"]), float(group.diameter or 0.0))
                if group.spacing:
                    faces[face]["spacingMm"] = min(float(faces[face]["spacingMm"]), float(group.spacing))
            elif group.bar_type == "distribution":
                horizontal["diameterMm"] = max(float(horizontal["diameterMm"]), float(group.diameter or 0.0))
                if group.spacing:
                    horizontal["spacingMm"] = min(float(horizontal["spacingMm"]), float(group.spacing))
            elif group.bar_type == "tie":
                ties["diameterMm"] = max(float(ties["diameterMm"]), float(group.diameter or 0.0))
                if group.spacing:
                    ties["spacingMm"] = min(float(ties["spacingMm"]), float(group.spacing))
    return {"faces": faces, "horizontal": horizontal, "ties": ties, "zoneIds": [z for z in zone_ids if z]}


def _wall_axis_is_valid(wall: Any) -> bool:
    points = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    if len(points) < 2:
        return False
    a, b = points[0], points[-1]
    values = (getattr(a, "x", None), getattr(a, "y", None), getattr(b, "x", None), getattr(b, "y", None))
    try:
        return all(math.isfinite(float(value)) for value in values) and _dist(a, b) > 1.0e-6
    except (TypeError, ValueError):
        return False


def _resolve_wall_for_visualization(project: Project, wall: Any, wall_index: int) -> tuple[Any, str | None]:
    """Resolve every cage against the current excavation/wall geometry contract.

    Existing wall axes and construction-panel endpoint coordinates can survive a
    later outline edit.  The viewer must therefore derive plan geometry from the
    current excavation segment first and treat saved panel coordinates as audit
    metadata, never as authoritative geometry.
    """
    resolution = resolve_wall_plan_path(project, wall, wall_index)
    if len(resolution.points) < 2:
        return wall, "unresolved"
    resolved = wall.model_copy(deep=True)
    resolved.axis = Polyline2D(points=resolution.points, closed=False)
    resolved.design_length = polyline_length(resolution.points)
    return resolved, (resolution.source if resolution.repaired else None)

def _wall_cage_descriptors(
    wall: Any,
    zones: list[dict[str, Any]],
    *,
    line_cap: int = 160,
    target_panel_length_m: float = 6.0,
    minimum_panel_length_m: float = 3.0,
    maximum_panel_length_m: float = 7.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = list(getattr(getattr(wall, "axis", None), "points", []) or [])
    total_length = polyline_length(path)
    if len(path) < 2 or total_length <= 1.0e-9:
        return [], {"status": "unresolved", "panelCount": 0, "repairedPanelCount": 0, "maximumStoredDeviationM": None}
    reinforcement = _governing_wall_cage_reinforcement(wall, zones)
    panels, panel_audit = normalize_construction_panels(
        wall,
        path,
        target_length_m=target_panel_length_m,
        minimum_length_m=minimum_panel_length_m,
        maximum_length_m=maximum_panel_length_m,
    )
    cage_rows: list[dict[str, Any]] = []
    height = max(float(wall.top_elevation) - float(wall.bottom_elevation), 0.1)
    cover = min(max(float(wall.thickness) * 0.38, 0.08), max(float(wall.thickness) / 2.2, 0.08))
    for idx, panel in enumerate(panels, start=1):
        panel_path_raw = list(panel.get("planPath") or [])
        panel_path = [Point2D(x=float(item["x"]), y=float(item["y"])) for item in panel_path_raw]
        panel_length = polyline_length(panel_path)
        if len(panel_path) < 2 or panel_length <= 1.0e-9:
            continue
        panel_code = str(panel.get("panelCode") or f"{wall.panel_code}-P{idx:02d}")
        start_point = _pt(panel_path[0].x, panel_path[0].y, float(wall.top_elevation))
        end_point = _pt(panel_path[-1].x, panel_path[-1].y, float(wall.top_elevation))
        vertical_rows = []
        for face, spec in reinforcement["faces"].items():
            spacing_m = max(float(spec["spacingMm"]) / 1000.0, 0.05)
            vertical_rows.append({
                "face": face,
                **spec,
                "estimatedVerticalBarCount": max(2, int(math.floor(panel_length / spacing_m)) + 1),
            })
        h_spacing_m = max(float(reinforcement["horizontal"]["spacingMm"]) / 1000.0, 0.05)
        segment_count = max(1, int(math.ceil(height / 12.0)))
        splice_elevations = [
            round(float(wall.bottom_elevation) + height * segment / segment_count, 3)
            for segment in range(1, segment_count)
        ]
        lifting_points = []
        for lifting_index, ratio in enumerate((0.25, 0.75), start=1):
            point, _, _ = point_tangent_at_chainage(panel_path, panel_length * ratio)
            lifting_points.append({
                "id": f"LP-{panel_code}-{lifting_index}",
                "ratio": ratio,
                "point": _pt(point.x, point.y, float(wall.top_elevation) - 0.35),
                "reviewRequired": bool(panel.get("liftingReviewRequired", True)),
            })
        cage_rows.append({
            "id": f"cage-{wall.id}-{idx}",
            "hostId": wall.id,
            "hostCode": wall.panel_code,
            "panelCode": panel_code,
            "panelIndex": int(panel.get("panelIndex") or idx),
            "start": start_point,
            "end": end_point,
            "planPath": [_pt(point.x, point.y, float(wall.top_elevation)) for point in panel_path],
            "topElevation": float(wall.top_elevation),
            "bottomElevation": float(wall.bottom_elevation),
            "heightM": round(height, 3),
            "panelLengthM": round(panel_length, 3),
            "thicknessM": float(wall.thickness),
            "coverM": round(cover, 3),
            "faces": vertical_rows,
            "horizontal": {**reinforcement["horizontal"], "estimatedBarCountPerFace": max(2, int(math.floor(height / h_spacing_m)) + 1)},
            "ties": reinforcement["ties"],
            "zoneIds": reinforcement["zoneIds"],
            "jointType": panel.get("jointType") or "project_specific",
            "jointMarkers": [
                {"end": "start", "point": start_point, "jointType": panel.get("jointType") or "project_specific"},
                {"end": "end", "point": end_point, "jointType": panel.get("jointType") or "project_specific"},
            ],
            "liftingPoints": lifting_points,
            "spliceZones": [{"elevation": value, "type": "coupler_or_staggered_lap", "reviewRequired": True} for value in splice_elevations],
            "segmentCount": segment_count,
            "cageStatus": "manual_review" if bool(panel.get("liftingReviewRequired", True)) else "preliminary",
            "liftingReviewRequired": bool(panel.get("liftingReviewRequired", True)),
            "displayLineCap": int(line_cap),
            "representation": "construction_panel_rebar_cage_grid_with_joints_lifting_and_splice_zones",
            "planGeometryRepresentation": "canonical_wall_path_chainage_polyline",
            "geometrySource": panel.get("geometrySource"),
            "geometryStatus": panel.get("geometryStatus"),
            "storedGeometryDeviationM": panel.get("storedGeometryDeviationM"),
        })
    return cage_rows, panel_audit

def build_rebar_ifc_visualization(project: Project, max_bars: int = 950, focus_host_id: str | None = None) -> dict[str, Any]:
    retaining = project.retaining_system
    host_summaries: list[dict[str, Any]] = []
    estimated_full_count = 0
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_order = ["diaphragm_wall", "wale_or_crown_beam", "internal_support", "support_wale_node"]

    def _capture(host_type: str, host_code: str, group_count: int, sampled: int, estimated: int, tokens: list[str], produced: list[dict[str, Any]]) -> None:
        nonlocal estimated_full_count
        estimated_full_count += estimated
        host_summaries.append({
            "hostType": host_type,
            "hostCode": host_code,
            "groupCount": group_count,
            "sampledBarCount": sampled,
            "estimatedFullBarCount": estimated,
            "tokens": tokens,
        })
        if produced:
            pools[host_type].extend(produced)

    zone_scheme = retaining.rebar_design_scheme if retaining and isinstance(retaining.rebar_design_scheme, dict) else {}
    wall_zones_by_host: dict[str, list[dict[str, Any]]] = defaultdict(list)
    support_scheme_by_host: dict[str, dict[str, Any]] = {}
    for item in zone_scheme.get("supportSchemes", []) if isinstance(zone_scheme, dict) else []:
        row = dict(item)
        if row.get("hostId"):
            support_scheme_by_host[str(row.get("hostId"))] = row
        if row.get("hostCode"):
            support_scheme_by_host[str(row.get("hostCode"))] = row
    regenerated_support_scheme_count = 0
    if retaining and list(zone_scheme.get("supportSchemes") or []):
        missing_current_supports = [
            support for support in retaining.supports
            if support.id not in support_scheme_by_host and support.code not in support_scheme_by_host
        ]
        if missing_current_supports:
            from app.services.rebar_scheme_optimizer import build_current_support_rebar_rows
            current_rows = build_current_support_rebar_rows(project, mode=str(zone_scheme.get("mode") or "balanced"))
            for item in current_rows:
                row = dict(item)
                if row.get("hostId"):
                    support_scheme_by_host[str(row.get("hostId"))] = row
                if row.get("hostCode"):
                    support_scheme_by_host[str(row.get("hostCode"))] = row
            regenerated_support_scheme_count = len(current_rows)
    support_contracts: list[dict[str, Any]] = []
    cages: list[dict[str, Any]] = []
    repaired_wall_axis_count = 0
    repaired_panel_geometry_count = 0
    wall_panel_geometry_mismatch_count = 0
    maximum_panel_geometry_deviation_m = 0.0
    unresolved_wall_codes: list[str] = []
    represented_wall_codes: set[str] = set()
    wall_focus_selected = bool(
        retaining and focus_host_id and any(
            focus_host_id in {wall.id, wall.panel_code}
            for wall in retaining.diaphragm_walls
        )
    )
    wall_coverage_active = not focus_host_id or wall_focus_selected
    for zone in zone_scheme.get("wallZones", []) if isinstance(zone_scheme, dict) else []:
        wall_zones_by_host[str(zone.get("hostId"))].append(zone)

    if retaining:
        for wall_index, wall in enumerate(retaining.diaphragm_walls):
            if focus_host_id and focus_host_id not in {wall.id, wall.panel_code}:
                continue
            visual_wall, axis_source = _resolve_wall_for_visualization(project, wall, wall_index)
            if axis_source and axis_source not in {"unresolved", "missing_excavation"}:
                repaired_wall_axis_count += 1
            if not _wall_axis_is_valid(visual_wall):
                unresolved_wall_codes.append(str(wall.panel_code))
            host_bars: list[dict[str, Any]] = []
            wall_zones = wall_zones_by_host.get(wall.id, [])
            wall_cages, panel_audit = _wall_cage_descriptors(
                visual_wall,
                wall_zones,
                line_cap=int(getattr(project.design_settings, "rebar_cage_grid_max_lines_per_face", 140) or 140),
                target_panel_length_m=float(getattr(project.design_settings, "wall_panel_target_length_m", 6.0) or 6.0),
                minimum_panel_length_m=float(getattr(project.design_settings, "wall_panel_min_length_m", 3.0) or 3.0),
                maximum_panel_length_m=float(getattr(project.design_settings, "wall_panel_max_length_m", 7.0) or 7.0),
            )
            cages.extend(wall_cages)
            repaired_panel_geometry_count += int(panel_audit.get("repairedPanelCount") or 0)
            if str(panel_audit.get("status") or "") in {"repaired", "rebuilt"}:
                wall_panel_geometry_mismatch_count += 1
            maximum_panel_geometry_deviation_m = max(
                maximum_panel_geometry_deviation_m,
                float(panel_audit.get("maximumStoredDeviationM") or 0.0),
            )
            if wall_zones:
                sampled, estimated = _add_wall_zone_rebars(host_bars, visual_wall, wall_zones)
                tokens = sorted({str(face.get("token")) for zone in wall_zones for face in zone.get("faces", []) if face.get("token")})
                group_count = sum(len(zone.get("faces", [])) + 2 for zone in wall_zones)
            else:
                sampled, estimated = _add_wall_rebars(host_bars, visual_wall)
                tokens = [_group_token(g) for g in wall.reinforcement]
                group_count = len(wall.reinforcement)
            if wall_cages or sampled or estimated:
                represented_wall_codes.add(str(wall.panel_code))
            _capture(
                "diaphragm_wall",
                wall.panel_code,
                group_count,
                sampled,
                estimated,
                tokens,
                host_bars,
            )
        for beam in [*retaining.crown_beams, *retaining.wale_beams, *(retaining.ring_beams or [])]:
            if focus_host_id and focus_host_id not in {beam.id, beam.code}:
                continue
            host_bars = []
            sampled, estimated = _add_beam_rebars(host_bars, beam)
            if sampled or estimated:
                _capture(
                    "wale_or_crown_beam",
                    beam.code,
                    len(_beam_groups(beam)),
                    sampled,
                    estimated,
                    [_group_token(g) for g in _beam_groups(beam)],
                    host_bars,
                )
        for support in retaining.supports:
            if focus_host_id and focus_host_id not in {support.id, support.code}:
                continue
            host_bars = []
            resolved_groups, contract = _support_reinforcement_contract(support, support_scheme_by_host.get(support.id) or support_scheme_by_host.get(support.code))
            sampled, estimated = _add_support_rebars(host_bars, support, resolved_groups)
            contract["sampledBarTypes"] = sorted({str(item.get("barType")) for item in host_bars})
            sampled_stirrup_zones = sorted({str(item.get("stirrupZoneType")) for item in host_bars if item.get("barType") == "stirrup" and item.get("stirrupZoneType")})
            required_stirrup_zones = ["end_left", "end_right"]
            if any(group.bar_type == "stirrup" and group.zone_type == "middle_zone" and float(group.zone_length_m or 0.0) > 0.0 for group in resolved_groups):
                required_stirrup_zones.append("middle")
            contract["sampledStirrupZones"] = sampled_stirrup_zones
            contract["requiredStirrupZones"] = required_stirrup_zones
            contract["missingSampledStirrupZones"] = [zone for zone in required_stirrup_zones if zone not in sampled_stirrup_zones]
            contract["stirrupPreviewStatus"] = "complete" if not contract["missingSampledStirrupZones"] else "incomplete"
            contract["status"] = "complete" if not contract.get("missingBarTypes") and not contract.get("missingStirrupZones") and not contract["missingSampledStirrupZones"] else "incomplete"
            contract["sampledBarCount"] = sampled
            support_contracts.append(contract)
            append_event("rebar-contract", "support-contract-resolved", projectId=project.id, **contract)
            _capture(
                "internal_support",
                support.code,
                len(resolved_groups),
                sampled,
                estimated,
                [_group_token(g) for g in resolved_groups],
                host_bars,
            )
        for node in retaining.support_nodes or []:
            if focus_host_id and focus_host_id not in {node.id, node.code}:
                continue
            host_bars = []
            sampled, estimated = _add_node_rebars(host_bars, node)
            if sampled or estimated:
                _capture(
                    "support_wale_node",
                    node.code,
                    len(node.reinforcement),
                    sampled,
                    estimated,
                    [_group_token(g) for g in node.reinforcement],
                    host_bars,
                )

    total_available = sum(len(v) for v in pools.values())
    if total_available <= max_bars:
        bars = [bar for category in category_order for bar in pools.get(category, [])]
        omitted_hosts = 0
    else:
        # Wall cages carry the largest number of physical bars.  Keep enough wall
        # samples in the global budget so long walls do not appear artificially
        # sparse in the 3D viewer.
        weights = {
            "diaphragm_wall": 0.46,
            "wale_or_crown_beam": 0.16,
            "internal_support": 0.28,
            "support_wale_node": 0.10,
        }
        active_categories = [category for category in category_order if pools.get(category)]
        weight_sum = sum(weights.get(category, 0.0) for category in active_categories) or 1.0
        quotas: dict[str, int] = {}
        remaining_budget = max_bars
        for idx, category in enumerate(active_categories):
            available = len(pools[category])
            if idx == len(active_categories) - 1:
                quota = min(available, remaining_budget)
            else:
                raw = int(round(max_bars * weights.get(category, 0.0) / weight_sum))
                quota = max(1, min(available, raw))
                remaining_after_this = len(active_categories) - idx - 1
                quota = min(quota, max(1, remaining_budget - remaining_after_this))
            quotas[category] = quota
            remaining_budget -= quota
        if remaining_budget > 0:
            leftovers = sorted(
                active_categories,
                key=lambda category: len(pools[category]) - quotas.get(category, 0),
                reverse=True,
            )
            for category in leftovers:
                if remaining_budget <= 0:
                    break
                addable = min(len(pools[category]) - quotas.get(category, 0), remaining_budget)
                if addable > 0:
                    quotas[category] = quotas.get(category, 0) + addable
                    remaining_budget -= addable
        bars = []
        for category in category_order:
            category_rows = pools.get(category, [])
            quota = quotas.get(category, 0)
            if category == "internal_support":
                bars.extend(_stratified_bar_type_sample(category_rows, quota))
            else:
                bars.extend(_round_robin_host_sample(category_rows, quota))
        omitted_hosts = sum(1 for summary in host_summaries if summary["sampledBarCount"] and not any(bar.get("hostCode") == summary["hostCode"] for bar in bars))

    by_type = Counter(str(bar.get("barType")) for bar in bars)
    by_host = Counter(str(bar.get("hostType")) for bar in bars)
    by_status = Counter(str(bar.get("checkStatus")) for bar in bars)
    steel_mass_proxy_kg = 0.0
    for bar in bars:
        dia_m = max(float(bar.get("diameterMm") or 0.0) / 1000.0, 0.0)
        area = math.pi * dia_m * dia_m / 4.0
        steel_mass_proxy_kg += area * float(bar.get("lengthM") or 0.0) * 7850.0
    support_types_present = sorted({str(bar.get("barType")) for bar in bars if str(bar.get("hostType")) == "internal_support"})
    support_types_missing = [name for name in _SUPPORT_EXPECTED_BAR_TYPES if name not in support_types_present]
    incomplete_contracts = [item for item in support_contracts if item.get("status") != "complete"]
    stirrup_zone_complete_contracts = [item for item in support_contracts if item.get("stirrupZoneStatus") == "complete" and item.get("stirrupPreviewStatus") == "complete"]
    expected_wall_codes = {
        str(wall.panel_code) for wall in (retaining.diaphragm_walls if retaining and wall_coverage_active else [])
    }
    missing_wall_codes = sorted(expected_wall_codes.difference(represented_wall_codes).union(unresolved_wall_codes))
    append_event(
        "rebar-visualization", "visualization-built", projectId=project.id, maxBars=max_bars, focusHostId=focus_host_id,
        sampledBarCount=len(bars), totalAvailableBarCount=total_available,
        supportBarTypesPresent=support_types_present, supportBarTypesMissing=support_types_missing,
        incompleteSupportContractCount=len(incomplete_contracts), supportCount=len(support_contracts),
        regeneratedSupportSchemeCount=regenerated_support_scheme_count,
        expectedWallHostCount=len(expected_wall_codes),
        representedWallHostCount=len(represented_wall_codes), repairedWallAxisCount=repaired_wall_axis_count,
        repairedPanelGeometryCount=repaired_panel_geometry_count,
        wallPanelGeometryMismatchCount=wall_panel_geometry_mismatch_count,
        maximumPanelGeometryDeviationM=round(maximum_panel_geometry_deviation_m, 4),
        unresolvedWallCodes=missing_wall_codes,
        byBarType=dict(by_type), byHostType=dict(by_host), omittedHostCount=omitted_hosts,
    )
    return {
        "projectId": project.id,
        "exportProfileMapping": {
            "designDetailed": "设计深化模型：代表钢筋按 IfcReinforcingBar 导出，并保留钢筋组、直径、间距、分区和宿主关系。",
            "constructionVisual": "施工可视模型：使用兼容性较好的代理实体表达同一批钢筋几何，便于通用 IFC 查看器浏览。",
            "coordinationLight": "轻量协调模型：不输出逐根实体，仅在宿主构件属性中保留钢筋参数和关联关系。",
        },
        "summary": {
            "sampledBarCount": len(bars),
            "cageCount": len(cages),
            "constructionPanelCount": len(cages),
            "estimatedFullBarCount": estimated_full_count or len(bars),
            "hostCount": len(host_summaries),
            "omittedHostCount": omitted_hosts,
            "steelMassProxyKg": round(steel_mass_proxy_kg, 1),
            "byBarType": dict(by_type),
            "supportBarTypesPresent": support_types_present,
            "supportBarTypesExpected": list(_SUPPORT_EXPECTED_BAR_TYPES),
            "supportBarTypesMissing": support_types_missing,
            "supportContractCompleteCount": sum(1 for item in support_contracts if item.get("status") == "complete"),
            "supportContractIncompleteCount": len(incomplete_contracts),
            "supportStirrupZoneCompleteCount": len(stirrup_zone_complete_contracts),
            "supportStirrupZoneIncompleteCount": len(support_contracts) - len(stirrup_zone_complete_contracts),
            "supportStirrupPreviewCount": sum(1 for bar in bars if bar.get("hostType") == "internal_support" and bar.get("barType") == "stirrup"),
            "regeneratedSupportSchemeCount": regenerated_support_scheme_count,
            "expectedWallHostCount": len(expected_wall_codes),
            "representedWallHostCount": len(represented_wall_codes),
            "repairedWallAxisCount": repaired_wall_axis_count,
            "repairedPanelGeometryCount": repaired_panel_geometry_count,
            "wallPanelGeometryMismatchCount": wall_panel_geometry_mismatch_count,
            "maximumPanelGeometryDeviationM": round(maximum_panel_geometry_deviation_m, 4),
            "wallPlanGeometryStatus": "matched" if wall_panel_geometry_mismatch_count == 0 and not missing_wall_codes else "auto_repaired" if not missing_wall_codes else "unresolved",
            "missingWallHostCodes": missing_wall_codes,
            "focusHostId": focus_host_id,
            "byHostType": dict(by_host),
            "byCheckStatus": dict(by_status),
            "detailLevel": "construction_panel_cage_grid_plus_zone_linked_sampled_bars" if cages else ("zone_linked_sampled_bar_level" if wall_zones_by_host else "sampled_bar_level_from_parameterized_reinforcement_groups"),
            "zoneLinked": bool(wall_zones_by_host),
            "officialDetailingLimit": "浏览器显示的是与设计分区和图纸编号关联的代表性钢筋；完整下料数量、套筒、精确搭接、弯钩及吊装仍以施工图钢筋表和专业复核为准。",
        },
        "bars": bars,
        "cages": cages,
        "hosts": host_summaries[:200],
        "supportContracts": support_contracts[:500],
        "notes": [
            "三维钢筋来自已应用的配筋方案，并与 IFC/CAD 导出使用相同的宿主构件编号。",
            "水平支撑箍筋分别按 A 端加密区、跨中普通区和 B 端加密区生成，通用箍筋记录不会再被误判为分区完整。",
            "地下连续墙钢筋笼网格按当前围护墙规范化平面路径和实际间距表达；历史槽段端点只作为审计信息，不再直接驱动几何。",
            "估算完整数量记录理论逐根数量，近景模式可按单根支撑重新加载完整的分区样本。",
            "设计深化 IFC 保留钢筋语义，施工可视 IFC 使用兼容代理几何。",
        ],
    }
