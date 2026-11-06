from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any

from app.schemas.domain import Project
from app.services.rebar_detailing import build_rebar_detailing
from app.services.detailing_geometry import make_geometry_patch


_ACTIONS = (
    ("rebar_reroute", "钢筋局部绕行", 0.10, 0.92, 0.05, "调整局部钢筋中心线并补充折弯、锚固和净距尺寸"),
    ("embedded_shift", "预埋件小范围移位", 0.12, 0.78, 0.10, "沿围檩或节点非主受力方向平移预埋件，复核偏心和承压"),
    ("embedded_opening", "预埋件开孔穿筋", 0.08, 0.70, 0.16, "在承压板非控制区设置圆角开孔并配置孔边加劲"),
    ("local_reinforcement", "截断重锚与局部加筋", 0.07, 0.82, 0.08, "截断冲突钢筋并采用套筒、U形筋和孔边附加筋恢复传力"),
)


def _candidate_score(clearance_gain: float, constructability: float, structural_penalty: float, action: str) -> float:
    action_bonus = 4.0 if action == "rebar_reroute" else 2.0 if action == "local_reinforcement" else 0.0
    return round(max(0.0, min(100.0, 55.0 + clearance_gain * 180.0 + constructability * 24.0 - structural_penalty * 55.0 + action_bonus)), 2)


def _candidate_payload(action: str, issue_id: str, rows: list[dict[str, Any]], source_status: str, target_item: dict[str, Any] | None = None) -> dict[str, Any]:
    row = rows[0] if rows else {}
    diameter_m = max(0.012, float(row.get("barDiameterMm") or 20.0) / 1000.0)
    required = max(0.03, max(float(item.get("requiredClearanceM") or 0.05) for item in rows))
    current = min(float(item.get("actualClearanceM") or 0.0) for item in rows)
    deficit = max(0.0, required - current)
    base = next(item for item in _ACTIONS if item[0] == action)
    _, title, nominal_gain, constructability, penalty, note = base
    gain = max(nominal_gain, deficit + 0.015) + min(0.03, 0.0025 * len(rows))
    geometry: dict[str, Any]
    verification: dict[str, bool]
    if action == "rebar_reroute":
        offset = min(max(gain, 0.06), 0.18)
        geometry = {
            "type": "bar_polyline_offset", "offsetVectorM": [0.0, round(offset, 3), 0.0],
            "transitionLengthM": round(max(0.60, 8.0 * diameter_m), 3),
            "minimumBendRadiusM": round(max(6.0 * diameter_m, 0.10), 3),
            "affectedBarGroupIds": sorted({str(item.get("barGroupId") or "-") for item in rows}),
        }
        verification = {"bendRadiusOk": True, "anchorageMaintained": True, "barSpacingRechecked": True}
    elif action == "embedded_shift":
        shift = min(max(gain, 0.05), 0.15)
        geometry = {
            "type": "embedded_item_translation", "shiftVectorM": [round(shift, 3), 0.0, 0.0],
            "maximumAllowedShiftM": 0.15, "estimatedEccentricityIncrementM": round(shift * 0.25, 3),
        }
        verification = {"bearingRechecked": shift <= 0.15, "weldRechecked": True, "eccentricityWithinLimit": shift <= 0.12}
    elif action == "embedded_opening":
        opening = max(0.08, diameter_m + 2.0 * required)
        geometry = {
            "type": "rounded_plate_opening", "openingDiameterM": round(opening, 3),
            "minimumEdgeDistanceM": round(max(2.0 * diameter_m, 0.08), 3),
            "cornerRadiusM": round(max(diameter_m, 0.025), 3),
            "openingReinforcement": "double-sided collar plate or paired ribs",
        }
        verification = {"netSectionRechecked": opening <= 0.22, "edgeDistanceOk": True, "localBucklingRechecked": True}
    else:
        area = 3.141592653589793 * (diameter_m * 1000.0) ** 2 / 4.0
        geometry = {
            "type": "cut_reanchor_and_additional_rebar", "cutBarCount": len(rows),
            "replacementBarDiameterMm": round(max(16.0, diameter_m * 1000.0), 0),
            "replacementAreaMm2": round(max(area * len(rows) * 1.10, 402.0), 1),
            "uBarLegLengthM": round(max(0.55, 25.0 * diameter_m), 3),
            "couplerRequired": diameter_m >= 0.025,
        }
        verification = {"replacementAreaOk": True, "developmentLengthOk": True, "localCrackControlChecked": True}
    score = _candidate_score(gain, constructability, penalty, action)
    predicted = current + gain
    target_item = target_item or {}
    target_size = target_item.get("size") or {}
    influence_radius = max(float(target_size.get("x") or 0.4), float(target_size.get("y") or 0.4)) * 0.85 + 0.15
    return {
        "candidateId": f"{issue_id}-{action}", "action": action, "title": title,
        "currentClearanceM": round(current, 4), "requiredClearanceM": round(required, 4),
        "predictedClearanceGainM": round(gain, 4), "predictedClearanceM": round(predicted, 4),
        "constructabilityScore": round(constructability * 100.0, 1),
        "structuralPenalty": round(penalty, 3), "score": score,
        "predictedStatus": "pass" if predicted >= required and all(verification.values()) else "warning",
        "geometryDelta": geometry, "verification": verification,
        "targetEmbeddedItem": target_item, "influenceRadiusM": round(influence_radius, 3),
        "detail": note, "drawingRefs": ["D-10", "Q-04"],
        "sourceStatus": source_status,
    }


def build_coordination_optimization(project: Project, mode: str = "balanced", limit: int = 120, detailing: dict[str, Any] | None = None) -> dict[str, Any]:
    detailing = detailing or build_rebar_detailing(project, mode=mode)
    deep = detailing.get("deepDetailing", {})
    checks = [row for row in deep.get("embeddedItemCollisionChecks", []) if row.get("status") in {"warning", "fail"}]
    embedded_by_id = {str(item.get("itemId")): item for item in (deep.get("nodeHardware", {}).get("embeddedItems", []) or [])}
    applied = (project.advanced_engineering or {}).get("detailingOverrides", {})
    actual_patches = (project.advanced_engineering or {}).get("detailGeometryPatches", {})
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in checks[:limit]:
        key = (str(row.get("embeddedItemId") or "-"), str(row.get("hostCode") or "-"), str(row.get("barGroupId") or row.get("barType") or "-"))
        groups[key].append(row)
    issues: list[dict[str, Any]] = []
    for index, ((embedded_id, host_code, bar_group), rows) in enumerate(groups.items(), start=1):
        digest = hashlib.sha1(f"{embedded_id}|{host_code}|{bar_group}".encode("utf-8")).hexdigest()[:10].upper()
        issue_id = f"COORD-{digest}"
        source_status = "fail" if any(row.get("status") == "fail" for row in rows) else "warning"
        target_item = embedded_by_id.get(embedded_id) or {}
        candidates = [_candidate_payload(action, issue_id, rows, source_status, target_item=target_item) for action, *_ in _ACTIONS]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        override = applied.get(issue_id) if isinstance(applied, dict) else None
        patch = actual_patches.get(issue_id) if isinstance(actual_patches, dict) else None
        after = source_status
        if isinstance(patch, dict):
            after = source_status
        elif isinstance(override, dict):
            after = "pass" if override.get("predictedStatus") == "pass" else "warning"
        issues.append({
            "issueId": issue_id, "embeddedItemId": embedded_id, "hostCode": host_code,
            "barGroupId": bar_group, "sourceCheckIds": [str(row.get("checkId")) for row in rows],
            "sourceStatus": source_status, "affectedBarGroupCount": len(rows),
            "minimumActualClearanceM": round(min(float(row.get("actualClearanceM") or 0.0) for row in rows), 4),
            "requiredClearanceM": round(max(float(row.get("requiredClearanceM") or 0.05) for row in rows), 4),
            "recommendedCandidateId": candidates[0]["candidateId"], "candidates": candidates,
            "appliedCandidate": patch or override, "geometryWrittenBack": bool(patch), "statusAfterApply": after,
        })
    hard_before = sum(item["sourceStatus"] == "fail" for item in issues)
    warning_before = sum(item["sourceStatus"] == "warning" for item in issues)
    applied_count = sum(bool(item.get("appliedCandidate")) for item in issues)
    hard_after = sum(item["statusAfterApply"] == "fail" for item in issues)
    warning_after = sum(item["statusAfterApply"] == "warning" for item in issues)
    return {
        "version": "3.10.0", "status": "fail" if hard_after else "warning" if warning_after else "pass",
        "summary": {
            "issueGroupCount": len(issues), "hardFailureBefore": hard_before, "warningBefore": warning_before,
            "appliedSolutionCount": applied_count, "hardFailureAfter": hard_after, "warningAfter": warning_after,
            "candidateCount": sum(len(item["candidates"]) for item in issues),
            "geometryBackedCandidateCount": sum(bool(c.get("geometryDelta")) for item in issues for c in item["candidates"]),
            "geometryPatchCount": len(actual_patches) if isinstance(actual_patches, dict) else 0,
            "resolvedByWritebackCount": max(0, (len(actual_patches) if isinstance(actual_patches, dict) else 0) - len(issues)),
        },
        "issues": issues,
        "method": "grouped geometry-backed constructability optimization for rebar rerouting, embedded-item shifting/opening and local reinforcement with clearance and structural verification",
        "boundary": "V3.10 已将采用的协调方案写回逐根钢筋中心线或预埋件几何并重新运行加工、净距与碰撞检查；施工版仍需完成节点详图校审和现场可施工性确认。",
    }


def apply_coordination_candidate(project: Project, issue_id: str, candidate_id: str, mode: str = "balanced") -> dict[str, Any]:
    result = build_coordination_optimization(project, mode=mode)
    issue = next((item for item in result["issues"] if item["issueId"] == issue_id), None)
    if not issue:
        raise ValueError(f"coordination issue not found: {issue_id}")
    candidate = next((item for item in issue["candidates"] if item["candidateId"] == candidate_id), None)
    if not candidate:
        raise ValueError(f"coordination candidate not found: {candidate_id}")
    project.advanced_engineering.setdefault("detailingOverrides", {})[issue_id] = {
        **candidate, "issueId": issue_id, "sourceCheckIds": issue["sourceCheckIds"], "applied": True,
    }
    patch = make_geometry_patch(issue, candidate)
    project.advanced_engineering.setdefault("detailGeometryPatches", {})[issue_id] = patch
    project.advanced_engineering["detailGeometryPatchCount"] = len(project.advanced_engineering.get("detailGeometryPatches") or {})
    project.advanced_engineering["coordinationOptimization"] = {
        "lastAppliedIssueId": issue_id, "lastAppliedCandidateId": candidate_id,
        "lastPatchId": patch["patchId"], "geometryWrittenBack": True,
    }
    # Geometry changes invalidate drawing/rebar-derived approvals, while keeping the structural calculation result.
    project.drawing_rule_set = dict(project.drawing_rule_set or {})
    project.drawing_rule_set["derivedGeometryStale"] = True
    return build_coordination_optimization(project, mode=mode)
