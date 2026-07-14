from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from app.schemas.domain import BeamElement, Point2D, Project, ReinforcementGroup


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
    if len(wall.axis.points) < 2 or not zones:
        return 0, 0
    a = wall.axis.points[0]
    b = wall.axis.points[-1]
    ux, uy, length = _unit(a, b)
    nx, ny = -uy, ux
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
            # Browser visualization remains sampled, but the sample density must scale with
            # physical wall length.  The former hard cap of five bars per face made a
            # 100 m wall look almost unreinforced even when the design was D22@150.
            visual_pitch = 4.0
            dynamic_cap = max(8, min(32, int(math.ceil(length / visual_pitch)) + 1))
            sampled, estimated = _count_from_spacing(
                length, spacing, fallback=8, cap=min(dynamic_cap, max_per_wall - sampled_total)
            )
            estimated_total += estimated
            for idx, station in enumerate(_stations(length, sampled), start=1):
                x = a.x + ux * station + nx * cover * face_sign
                y = a.y + uy * station + ny * cover * face_sign
                z0 = max(float(wall.bottom_elevation) + 0.12, bottom - (overlap if zone_index < len(ordered) else 0.0))
                z1 = min(float(wall.top_elevation) - 0.12, top + (overlap if zone_index > 1 else 0.0))
                bars.append(_make_bar(
                    host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=group,
                    start=_pt(x, y, z0), end=_pt(x, y, z1), index=zone_index * 100 + idx,
                    representation="wall_zone_vertical_bar_with_overlap", shape_kind="zone_vertical_segment",
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
        sample_z, estimate_z = _count_from_spacing(
            zone_height, h_spacing, fallback=4,
            cap=min(dynamic_vertical_cap, max(1, (max_per_wall - sampled_total) // 2)),
        )
        estimated_total += estimate_z * 2
        z_values = [bottom + min(0.2, zone_height * 0.15) + max(zone_height - min(0.4, zone_height * 0.3), 0.0) * i / max(sample_z - 1, 1) for i in range(sample_z)]
        for face_index, face_sign in enumerate((-1, 1), start=1):
            for zidx, z in enumerate(z_values, start=1):
                if sampled_total >= max_per_wall:
                    break
                bars.append(_make_bar(
                    host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=h_group,
                    start=_pt(a.x + nx * cover * face_sign, a.y + ny * cover * face_sign, z),
                    end=_pt(b.x + nx * cover * face_sign, b.y + ny * cover * face_sign, z),
                    index=zone_index * 1000 + face_index * 100 + zidx,
                    representation="wall_zone_horizontal_distribution_bar", shape_kind="zone_horizontal_bar",
                    estimated_count=estimate_z * 2, sampled_from_count=sample_z * 2,
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
            station = length * 0.5
            cx, cy, z = a.x + ux * station, a.y + uy * station, (top + bottom) / 2.0
            bars.append(_make_bar(
                host_type="diaphragm_wall", host_code=wall.panel_code, host_id=wall.id, group=tie_group,
                start=_pt(cx - nx * cover, cy - ny * cover, z), end=_pt(cx + nx * cover, cy + ny * cover, z), index=zone_index,
                representation="wall_zone_sampled_tie_bar", estimated_count=max(1, int(length / max(float(tie.get("spacingMm") or 450.0) / 1000.0, 0.1))), sampled_from_count=1,
                extra={"zoneId": zone_id, "zoneType": zone_type, "drawingRefs": drawing_refs, "envelopeSource": envelope_source, "zoneTopElevation": top, "zoneBottomElevation": bottom},
            ))
            sampled_total += 1
    return sampled_total, estimated_total


def _add_wall_rebars(bars: list[dict[str, Any]], wall, max_per_wall: int = 110) -> tuple[int, int]:
    if len(wall.axis.points) < 2:
        return 0, 0
    a = wall.axis.points[0]
    b = wall.axis.points[-1]
    ux, uy, length = _unit(a, b)
    nx, ny = -uy, ux
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
                x = a.x + ux * station + nx * cover * face_sign
                y = a.y + uy * station + ny * cover * face_sign
                bars.append(_make_bar(
                    host_type="diaphragm_wall",
                    host_code=wall.panel_code,
                    host_id=wall.id,
                    group=group,
                    start=_pt(x, y, wall.bottom_elevation + 0.15),
                    end=_pt(x, y, wall.top_elevation - 0.15),
                    index=idx,
                    representation="wall_vertical_bar_with_lap_offset",
                    points=[_pt(x, y, wall.bottom_elevation + 0.15), _pt(x, y, wall.bottom_elevation + height * 0.48), _pt(x + nx * cover * 0.65 * face_sign, y + ny * cover * 0.65 * face_sign, wall.bottom_elevation + height * 0.52), _pt(x + nx * cover * 0.65 * face_sign, y + ny * cover * 0.65 * face_sign, wall.top_elevation - 0.15)],
                    shape_kind="vertical_lap_polyline",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
        elif group.bar_type == "distribution":
            sampled_z, estimated_z = _count_from_spacing(height, group.spacing, fallback=8, cap=min(18, max_per_wall - sampled_total))
            estimated_total += estimated_z * 2
            z_values = [wall.bottom_elevation + 0.3 + (height - 0.6) * i / max(sampled_z - 1, 1) for i in range(sampled_z)]
            for face_index, face_sign in enumerate((-1, 1), start=1):
                for zidx, z in enumerate(z_values, start=1):
                    bars.append(_make_bar(
                        host_type="diaphragm_wall",
                        host_code=wall.panel_code,
                        host_id=wall.id,
                        group=group,
                        start=_pt(a.x + nx * cover * face_sign, a.y + ny * cover * face_sign, z),
                        end=_pt(b.x + nx * cover * face_sign, b.y + ny * cover * face_sign, z),
                        index=face_index * 1000 + zidx,
                        representation="wall_distribution_bar_with_end_hooks",
                        points=[_pt(a.x + nx * cover * face_sign + ux * 0.18, a.y + ny * cover * face_sign + uy * 0.18, z - 0.18), _pt(a.x + nx * cover * face_sign, a.y + ny * cover * face_sign, z), _pt(b.x + nx * cover * face_sign, b.y + ny * cover * face_sign, z), _pt(b.x + nx * cover * face_sign - ux * 0.18, b.y + ny * cover * face_sign - uy * 0.18, z - 0.18)],
                        shape_kind="horizontal_hooked_polyline",
                        estimated_count=estimated_z * 2,
                        sampled_from_count=sampled_z * 2,
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
                cx = a.x + ux * station
                cy = a.y + uy * station
                bars.append(_make_bar(
                    host_type="diaphragm_wall",
                    host_code=wall.panel_code,
                    host_id=wall.id,
                    group=group,
                    start=_pt(cx - nx * cover, cy - ny * cover, z),
                    end=_pt(cx + nx * cover, cy + ny * cover, z),
                    index=idx,
                    representation="sampled_wall_tie_bars_across_cage",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
    return sampled_total, estimated_total


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


def _add_support_rebars(bars: list[dict[str, Any]], support: SupportElement, max_per_support: int = 24) -> tuple[int, int]:
    width = max((support.section.width if support.section else None) or 0.8, 0.35)
    height = max((support.section.height if support.section else None) or 0.8, 0.35)
    ux, uy, length = _unit(support.start, support.end)
    nx, ny = -uy, ux
    sampled_total = 0
    estimated_total = 0
    for group in support.reinforcement:
        if sampled_total >= max_per_support:
            break
        remaining = max_per_support - sampled_total
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
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=8, cap=min(10, remaining))
            estimated_total += estimated
            for idx, station in enumerate(_stations(length, sampled, margin=0.35), start=1):
                cx = support.start.x + ux * station
                cy = support.start.y + uy * station
                z = support.elevation
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42),
                    end=_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42),
                    index=idx,
                    representation="sampled_support_closed_stirrups",
                    points=[_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42), _pt(cx + nx * width * 0.42, cy + ny * width * 0.42, z - height * 0.42), _pt(cx + nx * width * 0.42, cy + ny * width * 0.42, z + height * 0.42), _pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z + height * 0.42), _pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z - height * 0.42)],
                    shape_kind="closed_stirrup_rectangle",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
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


def _wall_cage_descriptors(wall: Any, zones: list[dict[str, Any]], line_cap: int = 160) -> list[dict[str, Any]]:
    if len(wall.axis.points) < 2:
        return []
    a, b = wall.axis.points[0], wall.axis.points[-1]
    ux, uy, total_length = _unit(a, b)
    if total_length <= 1.0e-9:
        return []
    reinforcement = _governing_wall_cage_reinforcement(wall, zones)
    panels = list(getattr(wall, "construction_panels", []) or [])
    if not panels:
        panels = [{
            "panelIndex": 1, "panelCode": f"{wall.panel_code}-P01",
            "startChainageM": 0.0, "endChainageM": total_length, "lengthM": total_length,
            "start": {"x": a.x, "y": a.y}, "end": {"x": b.x, "y": b.y},
            "cageCount": 1, "jointType": "project_specific", "liftingReviewRequired": True,
        }]
    cage_rows: list[dict[str, Any]] = []
    height = max(float(wall.top_elevation) - float(wall.bottom_elevation), 0.1)
    cover = min(max(float(wall.thickness) * 0.08, 0.07), 0.12)
    for idx, panel in enumerate(panels, start=1):
        c0 = float(panel.get("startChainageM") or 0.0)
        c1 = float(panel.get("endChainageM") or panel.get("lengthM") or total_length)
        c0 = max(0.0, min(total_length, c0)); c1 = max(c0, min(total_length, c1))
        start = panel.get("start") or {"x": a.x + ux * c0, "y": a.y + uy * c0}
        end = panel.get("end") or {"x": a.x + ux * c1, "y": a.y + uy * c1}
        panel_length = max(c1 - c0, 0.01)
        faces = []
        for face in ("inner", "outer"):
            spec = reinforcement["faces"].get(face, reinforcement["faces"]["inner"])
            spacing_m = max(float(spec.get("spacingMm") or 200.0) / 1000.0, 0.05)
            faces.append({
                "face": face,
                "diameterMm": float(spec.get("diameterMm") or 25.0),
                "spacingMm": float(spec.get("spacingMm") or 200.0),
                "estimatedVerticalBarCount": max(2, int(math.floor(panel_length / spacing_m)) + 1),
            })
        h_spacing_m = max(float(reinforcement["horizontal"].get("spacingMm") or 200.0) / 1000.0, 0.05)
        cage_rows.append({
            "id": f"cage-{wall.id}-{idx}",
            "hostId": wall.id, "hostCode": wall.panel_code,
            "panelCode": str(panel.get("panelCode") or f"{wall.panel_code}-P{idx:02d}"),
            "panelIndex": int(panel.get("panelIndex") or idx),
            "start": _pt(float(start.get("x", a.x)), float(start.get("y", a.y)), float(wall.top_elevation)),
            "end": _pt(float(end.get("x", b.x)), float(end.get("y", b.y)), float(wall.top_elevation)),
            "topElevation": float(wall.top_elevation), "bottomElevation": float(wall.bottom_elevation),
            "heightM": round(height, 3), "panelLengthM": round(panel_length, 3),
            "thicknessM": float(wall.thickness), "coverM": round(cover, 3),
            "faces": faces,
            "horizontal": {**reinforcement["horizontal"], "estimatedBarCountPerFace": max(2, int(math.floor(height / h_spacing_m)) + 1)},
            "ties": reinforcement["ties"], "zoneIds": reinforcement["zoneIds"],
            "jointType": panel.get("jointType") or "project_specific",
            "liftingReviewRequired": bool(panel.get("liftingReviewRequired", True)),
            "displayLineCap": int(line_cap),
            "representation": "construction_panel_rebar_cage_grid_lod",
        })
    return cage_rows

def build_rebar_ifc_visualization(project: Project, max_bars: int = 950) -> dict[str, Any]:
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
    cages: list[dict[str, Any]] = []
    for zone in zone_scheme.get("wallZones", []) if isinstance(zone_scheme, dict) else []:
        wall_zones_by_host[str(zone.get("hostId"))].append(zone)

    if retaining:
        for wall in retaining.diaphragm_walls:
            host_bars: list[dict[str, Any]] = []
            wall_zones = wall_zones_by_host.get(wall.id, [])
            cages.extend(_wall_cage_descriptors(wall, wall_zones, line_cap=int(getattr(project.design_settings, "rebar_cage_grid_max_lines_per_face", 140) or 140)))
            if wall_zones:
                sampled, estimated = _add_wall_zone_rebars(host_bars, wall, wall_zones)
                tokens = sorted({str(face.get("token")) for zone in wall_zones for face in zone.get("faces", []) if face.get("token")})
                group_count = sum(len(zone.get("faces", [])) + 2 for zone in wall_zones)
            else:
                sampled, estimated = _add_wall_rebars(host_bars, wall)
                tokens = [_group_token(g) for g in wall.reinforcement]
                group_count = len(wall.reinforcement)
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
            host_bars = []
            sampled, estimated = _add_support_rebars(host_bars, support)
            _capture(
                "internal_support",
                support.code,
                len(support.reinforcement),
                sampled,
                estimated,
                [_group_token(g) for g in support.reinforcement],
                host_bars,
            )
        for node in retaining.support_nodes or []:
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
        bars = [bar for category in category_order for bar in _round_robin_host_sample(pools.get(category, []), quotas.get(category, 0))]
        omitted_hosts = sum(1 for summary in host_summaries if summary["sampledBarCount"] and not any(bar.get("hostCode") == summary["hostCode"] for bar in bars))

    by_type = Counter(str(bar.get("barType")) for bar in bars)
    by_host = Counter(str(bar.get("hostType")) for bar in bars)
    by_status = Counter(str(bar.get("checkStatus")) for bar in bars)
    steel_mass_proxy_kg = 0.0
    for bar in bars:
        dia_m = max(float(bar.get("diameterMm") or 0.0) / 1000.0, 0.0)
        area = math.pi * dia_m * dia_m / 4.0
        steel_mass_proxy_kg += area * float(bar.get("lengthM") or 0.0) * 7850.0
    return {
        "projectId": project.id,
        "exportProfileMapping": {
            "designDetailed": "representative bars are exported as IfcReinforcingBar with Pset_ReinforcementGroup",
            "constructionVisual": "same representative bars are exported as viewer-safe IfcBuildingElementProxy with Pset_ReinforcementVisualProxy",
            "coordinationLight": "physical bar geometry is omitted; reinforcement remains as parameterized property sets on host elements",
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
            "byHostType": dict(by_host),
            "byCheckStatus": dict(by_status),
            "detailLevel": "construction_panel_cage_grid_plus_zone_linked_sampled_bars" if cages else ("zone_linked_sampled_bar_level" if wall_zones_by_host else "sampled_bar_level_from_parameterized_reinforcement_groups"),
            "zoneLinked": bool(wall_zones_by_host),
            "officialDetailingLimit": "sampled browser geometry is linked to design zones and drawing references; full fabrication quantities, couplers, exact laps, hooks and cage lifting remain governed by CAD schedules and engineering review",
        },
        "bars": bars,
        "cages": cages,
        "hosts": host_summaries[:200],
        "notes": [
            "The visualization is generated from the applied reinforcement design scheme and the same host object IDs used by IFC/CAD exports.",
            "Applied wall-zone schemes display separate elevation ranges, inner/outer faces and drawing references; unapplied projects fall back to governing member groups.",
            "Each diaphragm-wall construction panel is also represented by a cage-grid LOD using the governing actual bar spacing; selectable cylinders remain a sampled inspection layer.",
            "estimatedFullBarCount records the implied full count while cage descriptors preserve panel, face, cover and spacing semantics.",
            "Sampling is balanced across walls, beams, supports and nodes so support detailing remains visible in mixed scenes.",
            "design_detailed.ifc keeps semantic IfcReinforcingBar entities; construction_visual.ifc uses proxy geometry for viewer reliability.",
        ],
    }
