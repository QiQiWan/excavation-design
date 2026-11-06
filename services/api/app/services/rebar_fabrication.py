from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.rules.gb50010.detailing_rules import required_rebar_lap_length_mm
from app.schemas.domain import Project


STEEL_DENSITY_KG_M3 = 7850.0
DEFAULT_STOCK_LENGTH_M = 12.0
DEFAULT_TRANSPORT_LENGTH_M = 12.0


def _unit_weight(diameter_mm: float) -> float:
    return math.pi * (diameter_mm / 1000.0) ** 2 / 4.0 * STEEL_DENSITY_KG_M3


def _splice_type(bar: dict[str, Any]) -> str:
    diameter = float(bar.get("diameterMm") or 0.0)
    host = str(bar.get("hostType") or "")
    bar_type = str(bar.get("barType") or "")
    if diameter >= 25.0 or (host == "diaphragm_wall" and bar_type == "longitudinal"):
        return "mechanical_coupler"
    return "lap_splice"


def _lap_length_m(diameter_mm: float, grade: str = "HRB400", *, seismic: bool = False) -> float:
    return required_rebar_lap_length_mm(diameter_mm, grade, seismic=seismic) / 1000.0


def _split_lengths(
    total_m: float,
    splice_type: str,
    diameter_mm: float,
    max_len_m: float,
    *,
    grade: str = "HRB400",
    seismic: bool = False,
) -> list[float]:
    if total_m <= max_len_m + 1e-9:
        return [max(total_m, 0.05)]
    lap = _lap_length_m(diameter_mm, grade, seismic=seismic) if splice_type == "lap_splice" else 0.08
    count = max(2, int(math.ceil(total_m / max(max_len_m - lap, 0.5))))
    while True:
        segment = (total_m + (count - 1) * lap) / count
        if segment <= max_len_m + 1e-9:
            break
        count += 1
    lengths = [round(segment, 3) for _ in range(count)]
    # Preserve exact total accounting after overlap/coupler allowance.
    correction = total_m + (count - 1) * lap - sum(lengths)
    lengths[-1] = round(lengths[-1] + correction, 3)
    return lengths


def _bar_midpoint(bar: dict[str, Any]) -> tuple[float, float, float]:
    points = bar.get("points") or []
    if not points:
        return 0.0, 0.0, 0.0
    return (
        sum(float(p.get("x", 0.0)) for p in points) / len(points),
        sum(float(p.get("y", 0.0)) for p in points) / len(points),
        sum(float(p.get("z", 0.0)) for p in points) / len(points),
    )


def _bar_direction(bar: dict[str, Any]) -> tuple[float, float, float]:
    points = bar.get("points") or []
    if len(points) < 2:
        return 0.0, 0.0, 1.0
    a, b = points[0], points[-1]
    dx = float(b.get("x", 0.0)) - float(a.get("x", 0.0))
    dy = float(b.get("y", 0.0)) - float(a.get("y", 0.0))
    dz = float(b.get("z", 0.0)) - float(a.get("z", 0.0))
    length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    return dx / length, dy / length, dz / length


def _spacing_axis(bar: dict[str, Any]) -> tuple[int, float]:
    direction = _bar_direction(bar)
    mid = _bar_midpoint(bar)
    dominant = max(range(3), key=lambda i: abs(direction[i]))
    candidates = [i for i in range(3) if i != dominant]
    axis = max(candidates, key=lambda i: abs(mid[i])) if candidates else 0
    return axis, mid[axis]


def _geometric_spacing_checks(bars: list[dict[str, Any]], limit: int = 5000) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        grouped[(str(bar.get("hostId")), str(bar.get("groupId")), str(bar.get("barType")))].append(bar)
    checks: list[dict[str, Any]] = []
    for (host_id, group_id, bar_type), group in grouped.items():
        if len(checks) >= limit or len(group) < 2:
            continue
        directions = [_bar_direction(bar) for bar in group[: min(len(group), 20)]]
        avg_dir = tuple(sum(abs(d[i]) for d in directions) / len(directions) for i in range(3))
        dominant = max(range(3), key=lambda i: avg_dir[i])
        candidate_axes = [i for i in range(3) if i != dominant]
        mids = [_bar_midpoint(bar) for bar in group]
        def variance(axis: int) -> float:
            values = [m[axis] for m in mids]
            mean = sum(values) / len(values)
            return sum((v - mean) ** 2 for v in values) / max(len(values), 1)
        axis = max(candidate_axes, key=variance)
        ordered = sorted(((mid[axis], bar) for mid, bar in zip(mids, group)), key=lambda item: (item[0], str(item[1].get("barId") or "")))
        for (v1, b1), (v2, b2) in zip(ordered[:-1], ordered[1:]):
            center_mm = abs(v2 - v1) * 1000.0
            d1 = float(b1.get("diameterMm") or 0.0)
            d2 = float(b2.get("diameterMm") or 0.0)
            clear_mm = center_mm - (d1 + d2) / 2.0
            host_type = str(b1.get("hostType") or "")
            required = max(50.0 if host_type == "diaphragm_wall" else 25.0, d1, d2)
            status = "pass" if clear_mm + 1e-6 >= required else "fail"
            checks.append({
                "checkId": f"CLR-{b1.get('barId')}-{b2.get('barId')}",
                "hostId": host_id,
                "groupId": group_id,
                "barType": bar_type,
                "spacingAxis": axis,
                "barA": b1.get("barId"),
                "barB": b2.get("barId"),
                "centerSpacingMm": round(center_mm, 2),
                "clearSpacingMm": round(clear_mm, 2),
                "requiredClearSpacingMm": round(required, 2),
                "status": status,
                "message": "钢筋净距满足加工与浇筑要求" if status == "pass" else "钢筋净距不足，需调整直径、间距或层数",
            })
            if len(checks) >= limit:
                break
    return checks


def build_rebar_fabrication_package(
    project: Project,
    entries: list[dict[str, Any]],
    bars: list[dict[str, Any]],
    *,
    stock_length_m: float = DEFAULT_STOCK_LENGTH_M,
    transport_length_m: float = DEFAULT_TRANSPORT_LENGTH_M,
) -> dict[str, Any]:
    max_len = min(float(stock_length_m), float(transport_length_m))
    seismic = project.design_settings.seismic_grade not in {"non_seismic_temporary", "none"}
    fabrication_segments: list[dict[str, Any]] = []
    bbs: list[dict[str, Any]] = []
    splice_records: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    duplicate_ids: list[dict[str, Any]] = []

    for bar in bars:
        raw_bar_id = str(bar.get("barId") or "")
        if not raw_bar_id:
            invalid.append({"barId": raw_bar_id, "status": "fail", "message": "钢筋ID为空"})
            continue
        occurrence = seen_ids.get(raw_bar_id, 0) + 1
        seen_ids[raw_bar_id] = occurrence
        bar_id = raw_bar_id if occurrence == 1 else f"{raw_bar_id}-D{occurrence:02d}"
        if occurrence > 1:
            duplicate_ids.append({"originalBarId": raw_bar_id, "normalizedBarId": bar_id, "status": "warning", "message": "重复钢筋ID已在加工模型中确定性重编号"})
        total = float(bar.get("cutLengthM") or 0.0)
        diameter = float(bar.get("diameterMm") or 0.0)
        if total <= 0.0 or diameter <= 0.0:
            invalid.append({"barId": bar_id, "status": "fail", "message": "钢筋长度或直径无效"})
            continue
        splice_type = _splice_type(bar)
        grade = str(bar.get("grade") or "HRB400")
        lengths = _split_lengths(total, splice_type, diameter, max_len, grade=grade, seismic=seismic)
        stagger_group = int(bar.get("subIndex") or 0) % 2 + 1
        station = 0.0
        for idx, length in enumerate(lengths, start=1):
            segment_id = f"{bar_id}-F{idx:02d}"
            unit_weight = _unit_weight(diameter)
            segment = {
                "fabricationId": segment_id,
                "sourceBarId": bar_id,
                "barMark": bar.get("barMark"),
                "hostType": bar.get("hostType"),
                "hostCode": bar.get("hostCode"),
                "hostId": bar.get("hostId"),
                "groupId": bar.get("groupId"),
                "barType": bar.get("barType"),
                "diameterMm": diameter,
                "grade": bar.get("grade"),
                "shapeCode": bar.get("shapeCode"),
                "segmentIndex": idx,
                "segmentCount": len(lengths),
                "cutLengthM": round(length, 3),
                "stockLengthM": max_len,
                "weightKg": round(length * unit_weight, 3),
                "spliceTypeAtEnd": splice_type if idx < len(lengths) else "none",
                "staggerGroup": stagger_group,
                "startStationM": round(station, 3),
                "endStationM": round(station + length, 3),
                "status": "pass" if length <= max_len + 1e-6 else "fail",
            }
            fabrication_segments.append(segment)
            station += length
            if idx < len(lengths):
                splice_records.append({
                    "spliceId": f"SP-{segment_id}",
                    "sourceBarId": bar_id,
                    "barMark": bar.get("barMark"),
                    "hostCode": bar.get("hostCode"),
                    "spliceType": splice_type,
                    "diameterMm": diameter,
                    "staggerGroup": stagger_group,
                    "nominalStationRatio": 0.30 if stagger_group == 1 else 0.70,
                    "lapLengthM": _lap_length_m(diameter, grade, seismic=seismic) if splice_type == "lap_splice" else 0.0,
                    "couplerSpec": f"直螺纹套筒 D{diameter:g}" if splice_type == "mechanical_coupler" else "",
                    "status": "pass",
                })
        bbs.append({
            "barMark": bar.get("barMark"),
            "sourceBarId": bar_id,
            "hostCode": bar.get("hostCode"),
            "grade": bar.get("grade"),
            "diameterMm": diameter,
            "shapeCode": bar.get("shapeCode"),
            "fabricationPieceCount": len(lengths),
            "pieceLengthsM": ";".join(f"{x:.3f}" for x in lengths),
            "totalFabricationLengthM": round(sum(lengths), 3),
            "totalWeightKg": round(sum(lengths) * _unit_weight(diameter), 3),
            "spliceType": splice_type if len(lengths) > 1 else "none",
            "status": "pass" if max(lengths) <= max_len + 1e-6 else "fail",
        })

    spacing_checks = _geometric_spacing_checks(bars)
    hard_fail = len(invalid) + sum(x.get("status") == "fail" for x in fabrication_segments) + sum(x.get("status") == "fail" for x in spacing_checks)
    warnings: list[dict[str, Any]] = list(duplicate_ids)
    if not bars:
        warnings.append({"code": "NO_INDIVIDUAL_BARS", "status": "warning", "message": "未生成逐根钢筋几何"})
    # Embedded items are checked only when actual embedded geometry is present.
    embedded_count = sum(len(getattr(node, "reinforcement", []) or []) > 0 for node in (project.retaining_system.support_nodes if project.retaining_system else []))
    embedded_status = "not_applicable" if embedded_count == 0 else "requires_geometry_binding"

    return {
        "standardStockLengthM": stock_length_m,
        "transportLimitM": transport_length_m,
        "fabricationSegments": fabrication_segments,
        "barBendingSchedule": bbs,
        "spliceRecords": splice_records,
        "geometricSpacingChecks": spacing_checks,
        "invalidBars": invalid,
        "duplicateSourceBarIds": duplicate_ids,
        "embeddedItemCollisionStatus": embedded_status,
        "summary": {
            "sourceBarCount": len(bars),
            "fabricationPieceCount": len(fabrication_segments),
            "splitBarCount": sum(x.get("fabricationPieceCount", 0) > 1 for x in bbs),
            "maxPieceLengthM": round(max((float(x["cutLengthM"]) for x in fabrication_segments), default=0.0), 3),
            "mechanicalCouplerCount": sum(x.get("spliceType") == "mechanical_coupler" for x in splice_records),
            "lapSpliceCount": sum(x.get("spliceType") == "lap_splice" for x in splice_records),
            "spacingCheckCount": len(spacing_checks),
            "spacingFailureCount": sum(x.get("status") == "fail" for x in spacing_checks),
            "duplicateSourceBarIdCount": len(duplicate_ids),
            "hardFailureCount": hard_fail,
            "status": "fail" if hard_fail else "warning" if warnings or embedded_status == "requires_geometry_binding" else "pass",
        },
        "qualityBoundary": "逐根钢筋已按定尺、运输长度、接头类型和错开组进行加工分段；预埋件碰撞仅在具备真实预埋件几何时给出施工级结论。",
    }
