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
) -> dict[str, Any]:
    length = _bar_length(start, end)
    return {
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
        "start": start,
        "end": end,
        "lengthM": round(length, 3),
        "representation": representation,
        "estimatedFullCount": estimated_count,
        "sampledFromCount": sampled_from_count,
    }


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
                    representation="sampled_wall_vertical_bars_from_spacing",
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
                        representation="sampled_wall_horizontal_distribution_bars",
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


def _add_support_rebars(bars: list[dict[str, Any]], support, max_per_support: int = 42) -> tuple[int, int]:
    ux, uy, length = _unit(support.start, support.end)
    nx, ny = -uy, ux
    width = support.section.width or 0.8
    height = support.section.height or 0.8
    sampled_total = 0
    estimated_total = 0
    for group in support.reinforcement:
        if sampled_total >= max_per_support:
            break
        if group.bar_type == "longitudinal":
            count = min(group.count or 4, max_per_support - sampled_total)
            estimated_total += group.count or count
            for idx, (lat, dz) in enumerate(_support_longitudinal_offsets(count, width, height), start=1):
                sx = support.start.x + nx * lat + ux * 0.25
                sy = support.start.y + ny * lat + uy * 0.25
                ex = support.end.x + nx * lat - ux * 0.25
                ey = support.end.y + ny * lat - uy * 0.25
                z = support.elevation + dz
                bars.append(_make_bar(
                    host_type="internal_support",
                    host_code=support.code,
                    host_id=support.id,
                    group=group,
                    start=_pt(sx, sy, z),
                    end=_pt(ex, ey, z),
                    index=idx,
                    representation="sampled_support_longitudinal_bars",
                    estimated_count=group.count or count,
                    sampled_from_count=count,
                ))
            sampled_total += count
        elif group.bar_type == "stirrup":
            sampled, estimated = _count_from_spacing(length, group.spacing, fallback=8, cap=min(18, max_per_support - sampled_total))
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
                    start=_pt(cx - nx * width * 0.42, cy - ny * width * 0.42, z),
                    end=_pt(cx + nx * width * 0.42, cy + ny * width * 0.42, z),
                    index=idx,
                    representation="sampled_support_stirrups_as_cross_ties",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
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
                    start=_pt(cx - nx * width * 0.4, cy - ny * width * 0.4, beam.elevation),
                    end=_pt(cx + nx * width * 0.4, cy + ny * width * 0.4, beam.elevation),
                    index=idx,
                    representation="sampled_beam_stirrups_as_cross_ties",
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
                    start=_pt(node.location.x - 0.45, node.location.y, z),
                    end=_pt(node.location.x + 0.45, node.location.y, z),
                    index=idx + 1,
                    representation="sampled_node_stirrups_as_local_ties",
                    estimated_count=estimated,
                    sampled_from_count=sampled,
                ))
            sampled_total += sampled
    return sampled_total, estimated_total


def build_rebar_ifc_visualization(project: Project, max_bars: int = 950) -> dict[str, Any]:
    retaining = project.retaining_system
    bars: list[dict[str, Any]] = []
    host_summaries: list[dict[str, Any]] = []
    estimated_full_count = 0
    omitted_hosts = 0
    if retaining:
        for wall in retaining.diaphragm_walls:
            before = len(bars)
            sampled, estimated = _add_wall_rebars(bars, wall)
            estimated_full_count += estimated
            host_summaries.append({
                "hostType": "diaphragm_wall",
                "hostCode": wall.panel_code,
                "groupCount": len(wall.reinforcement),
                "sampledBarCount": sampled,
                "estimatedFullBarCount": estimated,
                "tokens": [_group_token(g) for g in wall.reinforcement],
            })
            if len(bars) >= max_bars:
                omitted_hosts += len(retaining.diaphragm_walls) - len(host_summaries)
                break
        if len(bars) < max_bars:
            for beam in [*retaining.crown_beams, *retaining.wale_beams, *(retaining.ring_beams or [])]:
                sampled, estimated = _add_beam_rebars(bars, beam)
                estimated_full_count += estimated
                if sampled or estimated:
                    host_summaries.append({
                        "hostType": "wale_or_crown_beam",
                        "hostCode": beam.code,
                        "groupCount": len(_beam_groups(beam)),
                        "sampledBarCount": sampled,
                        "estimatedFullBarCount": estimated,
                        "tokens": [_group_token(g) for g in _beam_groups(beam)],
                    })
                if len(bars) >= max_bars:
                    omitted_hosts += 1
                    break
        if len(bars) < max_bars:
            for support in retaining.supports:
                sampled, estimated = _add_support_rebars(bars, support)
                estimated_full_count += estimated
                host_summaries.append({
                    "hostType": "internal_support",
                    "hostCode": support.code,
                    "groupCount": len(support.reinforcement),
                    "sampledBarCount": sampled,
                    "estimatedFullBarCount": estimated,
                    "tokens": [_group_token(g) for g in support.reinforcement],
                })
                if len(bars) >= max_bars:
                    omitted_hosts += len(retaining.supports) - len([h for h in host_summaries if h["hostType"] == "internal_support"])
                    break
        if len(bars) < max_bars:
            for node in retaining.support_nodes or []:
                sampled, estimated = _add_node_rebars(bars, node)
                estimated_full_count += estimated
                if sampled or estimated:
                    host_summaries.append({
                        "hostType": "support_wale_node",
                        "hostCode": node.code,
                        "groupCount": len(node.reinforcement),
                        "sampledBarCount": sampled,
                        "estimatedFullBarCount": estimated,
                        "tokens": [_group_token(g) for g in node.reinforcement],
                    })
                if len(bars) >= max_bars:
                    omitted_hosts += 1
                    break
    # Trim after host sampling, keeping deterministic order.
    if len(bars) > max_bars:
        bars = bars[:max_bars]
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
            "estimatedFullBarCount": estimated_full_count or len(bars),
            "hostCount": len(host_summaries),
            "omittedHostCount": omitted_hosts,
            "steelMassProxyKg": round(steel_mass_proxy_kg, 1),
            "byBarType": dict(by_type),
            "byHostType": dict(by_host),
            "byCheckStatus": dict(by_status),
            "detailLevel": "sampled_bar_level_from_parameterized_reinforcement_groups",
            "officialDetailingLimit": "not yet a full bending-shape bar schedule; anchorage, lap, hook and exact cage fabrication still require CAD/detailing review",
        },
        "bars": bars,
        "hosts": host_summaries[:200],
        "notes": [
            "The visualization is generated from the same reinforcement groups used by the IFC exporter.",
            "It intentionally samples dense spacing-based bars to keep browser rendering stable; estimatedFullBarCount records the implied full count.",
            "design_detailed.ifc keeps semantic IfcReinforcingBar entities; construction_visual.ifc uses proxy geometry for viewer reliability.",
        ],
    }
