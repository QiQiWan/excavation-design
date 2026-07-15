from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from typing import Any

from app.schemas.domain import Project


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _distance_xy(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.hypot(float(a.get("x", 0.0)) - float(b.get("x", 0.0)), float(a.get("y", 0.0)) - float(b.get("y", 0.0)))


def _point_on_segment(p: dict[str, Any], q: dict[str, Any], t: float) -> dict[str, float]:
    return {
        "x": float(p.get("x", 0.0)) + (float(q.get("x", 0.0)) - float(p.get("x", 0.0))) * t,
        "y": float(p.get("y", 0.0)) + (float(q.get("y", 0.0)) - float(p.get("y", 0.0))) * t,
        "z": float(p.get("z", 0.0)) + (float(q.get("z", 0.0)) - float(p.get("z", 0.0))) * t,
    }


def _projection_t(p: dict[str, Any], q: dict[str, Any], center: dict[str, Any]) -> tuple[float, float]:
    px, py = float(p.get("x", 0.0)), float(p.get("y", 0.0))
    qx, qy = float(q.get("x", 0.0)), float(q.get("y", 0.0))
    cx, cy = float(center.get("x", 0.0)), float(center.get("y", 0.0))
    dx, dy = qx - px, qy - py
    den = dx * dx + dy * dy
    if den <= 1e-12:
        return 0.0, math.hypot(cx - px, cy - py)
    t = max(0.0, min(1.0, ((cx - px) * dx + (cy - py) * dy) / den))
    x, y = px + t * dx, py + t * dy
    return t, math.hypot(cx - x, cy - y)


def _polyline_length(points: list[dict[str, Any]]) -> float:
    total = 0.0
    for p, q in zip(points[:-1], points[1:]):
        total += math.sqrt(
            (float(q.get("x", 0.0)) - float(p.get("x", 0.0))) ** 2
            + (float(q.get("y", 0.0)) - float(p.get("y", 0.0))) ** 2
            + (float(q.get("z", 0.0)) - float(p.get("z", 0.0))) ** 2
        )
    return total


def _segments(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (p, q) in enumerate(zip(points[:-1], points[1:]), start=1):
        rows.append({"index": index, "type": "line", "lengthM": round(_polyline_length([p, q]), 3), "start": p, "end": q})
    return rows


def _reroute_bar(bar: dict[str, Any], patch: dict[str, Any]) -> bool:
    points = [dict(p) for p in (bar.get("points") or [])]
    if len(points) < 2:
        return False
    target = patch.get("targetEmbeddedItem") or {}
    center = target.get("center") or patch.get("targetCenter") or {}
    if center.get("x") is None or center.get("y") is None:
        return False
    size = target.get("size") or {}
    influence = float(patch.get("influenceRadiusM") or max(float(size.get("x") or 0.4), float(size.get("y") or 0.4)) * 0.85 + 0.15)
    offset = (patch.get("geometryDelta") or {}).get("offsetVectorM") or [0.0, 0.08, 0.0]
    ox, oy, oz = (float(offset[0]), float(offset[1]), float(offset[2] if len(offset) > 2 else 0.0))
    target_z = float(center.get("z", 0.0))
    half_z = float(size.get("z") or 0.6) / 2.0 + float(target.get("clearanceM") or 0.05)
    best: tuple[int, float, float] | None = None
    for index, (p, q) in enumerate(zip(points[:-1], points[1:])):
        zmin, zmax = sorted((float(p.get("z", 0.0)), float(q.get("z", 0.0))))
        if target_z and (zmax < target_z - half_z or zmin > target_z + half_z):
            continue
        t, distance = _projection_t(p, q, center)
        if distance <= influence and (best is None or distance < best[2]):
            best = (index, t, distance)
    if best is None:
        return False
    index, t, _ = best
    p, q = points[index], points[index + 1]
    seg_len = max(_polyline_length([p, q]), 1e-6)
    transition = float((patch.get("geometryDelta") or {}).get("transitionLengthM") or max(0.6, influence * 1.4))
    dt = min(0.22, max(0.04, transition / seg_len / 2.0))
    t0, t1 = max(0.0, t - dt), min(1.0, t + dt)
    a = _point_on_segment(p, q, t0)
    b = _point_on_segment(p, q, t1)
    a2 = {"x": a["x"] + ox, "y": a["y"] + oy, "z": a["z"] + oz}
    b2 = {"x": b["x"] + ox, "y": b["y"] + oy, "z": b["z"] + oz}
    new_points = points[: index + 1]
    if _distance_xy(new_points[-1], a) > 1e-8 or abs(float(new_points[-1].get("z", 0.0)) - a["z"]) > 1e-8:
        new_points.append(a)
    new_points.extend([a2, b2])
    if _distance_xy(b, q) > 1e-8 or abs(b["z"] - float(q.get("z", 0.0))) > 1e-8:
        new_points.append(b)
    new_points.extend(points[index + 1 :])
    # Remove exact duplicate consecutive points.
    clean: list[dict[str, Any]] = []
    for item in new_points:
        if not clean or _polyline_length([clean[-1], item]) > 1e-8:
            clean.append(item)
    original_length = float(bar.get("centerlineLengthM") or _polyline_length(points))
    new_length = _polyline_length(clean)
    bar["points"] = clean
    bar["segments"] = _segments(clean)
    bar["centerlineLengthM"] = round(new_length, 3)
    extra = max(0.0, new_length - original_length)
    bar["cutLengthM"] = round(float(bar.get("cutLengthM") or original_length) + extra, 3)
    unit_weight = float(bar.get("unitWeightKgPerM") or 0.0)
    bar["weightKg"] = round(float(bar["cutLengthM"]) * unit_weight, 3)
    bar["coordinationPatchId"] = patch.get("patchId")
    bar["coordinationAction"] = patch.get("action")
    bar["geometryRevision"] = int(bar.get("geometryRevision") or 0) + 1
    return True


def _additional_u_bars(patch: dict[str, Any], source_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = patch.get("targetEmbeddedItem") or {}
    center = target.get("center") or {}
    size = target.get("size") or {}
    if center.get("x") is None or center.get("y") is None:
        return []
    geometry = patch.get("geometryDelta") or {}
    diameter = float(geometry.get("replacementBarDiameterMm") or 20.0)
    leg = float(geometry.get("uBarLegLengthM") or 0.6)
    width = max(float(size.get("x") or 0.35), float(size.get("y") or 0.35)) + 0.18
    z = float(center.get("z") or 0.0)
    x, y = float(center["x"]), float(center["y"])
    host = source_bars[0] if source_bars else {}
    count = max(2, min(4, int(geometry.get("cutBarCount") or 2)))
    rows: list[dict[str, Any]] = []
    for index in range(count):
        dz = (index - (count - 1) / 2.0) * max(diameter / 1000.0 * 2.5, 0.06)
        points = [
            {"x": x - width / 2.0, "y": y - leg, "z": z + dz},
            {"x": x - width / 2.0, "y": y + width / 2.0, "z": z + dz},
            {"x": x + width / 2.0, "y": y + width / 2.0, "z": z + dz},
            {"x": x + width / 2.0, "y": y - leg, "z": z + dz},
        ]
        length = _polyline_length(points)
        unit = diameter * diameter / 162.0
        rows.append({
            "barId": f"{patch.get('patchId')}-ADD-{index + 1:02d}",
            "barMark": f"COORD-{patch.get('issueId')}-{index + 1:02d}",
            "subIndex": index + 1,
            "hostType": host.get("hostType") or "support_wale_node",
            "hostCode": host.get("hostCode") or target.get("hostCode") or "COORD",
            "hostId": host.get("hostId") or target.get("hostId") or "",
            "groupId": f"{patch.get('patchId')}-additional",
            "groupName": "构造协调附加U形筋",
            "barType": "additional",
            "diameterMm": diameter,
            "grade": host.get("grade") or "HRB400",
            "shapeCode": "31",
            "points": points,
            "segments": _segments(points),
            "centerlineLengthM": round(length, 3),
            "anchorageLengthM": round(leg, 3),
            "lapLengthM": 0.0,
            "hookLengthM": 0.0,
            "cutLengthM": round(length + 2.0 * leg, 3),
            "unitWeightKgPerM": round(unit, 4),
            "weightKg": round((length + 2.0 * leg) * unit, 3),
            "anchorageStatus": "coordination_generated_review",
            "lapStatus": "not_required",
            "hookStatus": "coordination_generated_review",
            "shapeKind": "u_bar",
            "source": "PitGuard V3.10 coordination geometry write-back",
            "coordinationPatchId": patch.get("patchId"),
            "coordinationAction": patch.get("action"),
            "geometryRevision": 1,
        })
    return rows


def apply_bar_geometry_patches(project: Project, bars: list[dict[str, Any]]) -> dict[str, Any]:
    patches = (project.advanced_engineering or {}).get("detailGeometryPatches") or {}
    if not isinstance(patches, dict) or not patches:
        return {"bars": bars, "summary": {"patchCount": 0, "modifiedBarCount": 0, "addedBarCount": 0, "status": "not_applicable"}}
    output = [copy.deepcopy(bar) for bar in bars]
    modified = 0
    added: list[dict[str, Any]] = []
    patch_rows: list[dict[str, Any]] = []
    for patch in patches.values():
        if not isinstance(patch, dict) or not patch.get("applied", True):
            continue
        action = str(patch.get("action") or "")
        group_ids = {str(x) for x in ((patch.get("geometryDelta") or {}).get("affectedBarGroupIds") or patch.get("affectedBarGroupIds") or [])}
        source_bars: list[dict[str, Any]] = []
        patch_modified = 0
        if action in {"rebar_reroute", "local_reinforcement"}:
            for bar in output:
                if group_ids and str(bar.get("groupId")) not in group_ids:
                    continue
                if _reroute_bar(bar, patch):
                    modified += 1
                    patch_modified += 1
                    source_bars.append(bar)
            if action == "local_reinforcement":
                generated = _additional_u_bars(patch, source_bars)
                added.extend(generated)
        patch_rows.append({
            "patchId": patch.get("patchId"), "issueId": patch.get("issueId"), "action": action,
            "modifiedBarCount": patch_modified, "addedBarCount": len(added), "appliedAt": patch.get("appliedAt"),
        })
    output.extend(added)
    return {
        "bars": output,
        "summary": {
            "patchCount": len(patch_rows), "modifiedBarCount": modified, "addedBarCount": len(added),
            "status": "pass" if patch_rows else "not_applicable", "patches": patch_rows,
        },
    }


def apply_embedded_item_patches(project: Project, embedded_items: list[dict[str, Any]]) -> dict[str, Any]:
    patches = (project.advanced_engineering or {}).get("detailGeometryPatches") or {}
    output = [copy.deepcopy(item) for item in embedded_items]
    by_id = {str(item.get("itemId")): item for item in output}
    modified = 0
    for patch in patches.values() if isinstance(patches, dict) else []:
        if not isinstance(patch, dict) or not patch.get("applied", True):
            continue
        target_id = str(patch.get("embeddedItemId") or (patch.get("targetEmbeddedItem") or {}).get("itemId") or "")
        item = by_id.get(target_id)
        if not item:
            continue
        action = str(patch.get("action") or "")
        geometry = patch.get("geometryDelta") or {}
        if action == "embedded_shift":
            shift = geometry.get("shiftVectorM") or [0.0, 0.0, 0.0]
            center = dict(item.get("center") or {})
            center["x"] = float(center.get("x", 0.0)) + float(shift[0])
            center["y"] = float(center.get("y", 0.0)) + float(shift[1])
            center["z"] = float(center.get("z", 0.0)) + float(shift[2] if len(shift) > 2 else 0.0)
            item["center"] = center
            modified += 1
        elif action == "embedded_opening":
            diameter = float(geometry.get("openingDiameterM") or 0.10)
            center = dict(item.get("center") or {})
            item.setdefault("openings", []).append({
                "openingId": f"{patch.get('patchId')}-OPENING",
                "center": center, "diameterM": diameter,
                "minimumEdgeDistanceM": geometry.get("minimumEdgeDistanceM"),
                "reinforcement": geometry.get("openingReinforcement"),
            })
            modified += 1
        elif action == "local_reinforcement":
            item.setdefault("localReinforcementPatches", []).append({
                "patchId": patch.get("patchId"), "geometry": geometry,
            })
            modified += 1
        item.setdefault("coordinationPatchIds", []).append(patch.get("patchId"))
        item["geometryRevision"] = int(item.get("geometryRevision") or 0) + 1
    return {"embeddedItems": output, "summary": {"modifiedEmbeddedItemCount": modified, "patchCount": len(patches) if isinstance(patches, dict) else 0}}


def make_geometry_patch(issue: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "patchId": f"PATCH-{candidate.get('candidateId')}",
        "patchVersion": "3.10.0",
        "issueId": issue.get("issueId"),
        "candidateId": candidate.get("candidateId"),
        "action": candidate.get("action"),
        "title": candidate.get("title"),
        "embeddedItemId": issue.get("embeddedItemId"),
        "hostCode": issue.get("hostCode"),
        "barGroupId": issue.get("barGroupId"),
        "sourceCheckIds": list(issue.get("sourceCheckIds") or []),
        "targetEmbeddedItem": copy.deepcopy(candidate.get("targetEmbeddedItem") or {}),
        "influenceRadiusM": candidate.get("influenceRadiusM"),
        "geometryDelta": copy.deepcopy(candidate.get("geometryDelta") or {}),
        "verification": copy.deepcopy(candidate.get("verification") or {}),
        "predictedClearanceM": candidate.get("predictedClearanceM"),
        "requiredClearanceM": candidate.get("requiredClearanceM"),
        "applied": True,
        "appliedAt": _now(),
    }
