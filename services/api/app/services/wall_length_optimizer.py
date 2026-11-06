from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from app.schemas.domain import Project
from app.services.calculation_trace import build_calculation_trace
from app.version import SOFTWARE_VERSION

TARGET_PRESETS: dict[str, tuple[float, float]] = {
    "conservative": (3.0, 10.0),
    "balanced": (2.0, 8.0),
    "economic": (1.3, 5.0),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _length(points: list[Any]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(math.hypot(float(b.x - a.x), float(b.y - a.y)) for a, b in zip(points[:-1], points[1:]))


def _status_for_r(r: float | None, low: float, high: float) -> str:
    if r is None or not math.isfinite(r):
        return "manual_review"
    if r < 1.0:
        return "fail"
    if r < low:
        return "near_limit"
    if r <= high:
        return "target"
    if r <= high * 1.8:
        return "conservative"
    return "over_redundant"


def _entry_redundancy(entry: dict[str, Any]) -> float | None:
    demand = entry.get("demandValue")
    cap = entry.get("capacityValue")
    util = entry.get("utilization")
    try:
        if cap not in (None, 0) and demand not in (None, 0):
            return abs(float(cap) / float(demand))
        if util not in (None, 0):
            return abs(1.0 / float(util))
    except Exception:
        return None
    return None


def _panel_count(face_length: float, panel_len: float) -> int:
    return max(1, int(math.ceil(face_length / max(panel_len, 0.5))))


def _history(project: Project) -> list[dict[str, Any]]:
    if not project.retaining_system:
        return []
    raw = project.retaining_system.layout_summary.get("wallLengthOptimizationHistory", [])
    return [dict(item) for item in raw if isinstance(item, dict)]


def _recompute_required(project: Project) -> bool:
    if not project.retaining_system:
        return False
    return bool(project.retaining_system.layout_summary.get("wallLengthOptimizationRecomputeRequired"))


def _history_summary(project: Project) -> dict[str, Any]:
    items = _history(project)
    latest = items[-1] if items else None
    return {
        "count": len(items),
        "latest": latest,
        "recomputeRequired": _recompute_required(project),
        "latestCandidateId": latest.get("candidateId") if latest else None,
        "latestAppliedAt": latest.get("appliedAt") if latest else None,
    }


def _closed_loop_status(project: Project, outside_count: int, candidate_count: int) -> dict[str, Any]:
    hist = _history_summary(project)
    if hist["recomputeRequired"]:
        return {
            "status": "applied_pending_recalculation",
            "severity": "warning",
            "recomputeRequired": True,
            "message": "已采纳围护墙设计长度优化建议，但尚未重新运行计算；当前冗余指标仍可能对应旧方案。",
            "nextAction": "重新运行一键复核计算并刷新冗余指标。",
        }
    if outside_count > 0 and candidate_count > 0:
        return {
            "status": "candidate_ready",
            "severity": "warning",
            "recomputeRequired": False,
            "message": "发现可优化的设计面长度冗余或接近下限项，可先查看候选方案再采纳。",
            "nextAction": "选择保守、均衡或经济候选，采纳后自动复算。",
        }
    if outside_count > 0:
        return {
            "status": "manual_review_required",
            "severity": "manual_review",
            "recomputeRequired": False,
            "message": "发现冗余异常项，但未形成可自动采纳候选；需人工复核地层参数、墙体分组和规范适用性。",
            "nextAction": "查看控制条文和墙身云图，必要时人工调整设计面。",
        }
    if hist["count"]:
        return {
            "status": "closed_after_recalculation",
            "severity": "pass",
            "recomputeRequired": False,
            "message": "冗余优化历史已完成复算闭环，当前未发现严重冗余设计面。",
            "nextAction": "可导出冗余优化记录并进入成果交付。",
        }
    return {
        "status": "analysis_complete",
        "severity": "pass",
        "recomputeRequired": False,
        "message": "当前设计面长度冗余分析完成。",
        "nextAction": "继续查看计算追溯链或导出成果。",
    }


def _repair_actions(face: dict[str, Any], low: float, high: float, mode: str) -> list[dict[str, Any]]:
    status = str(face.get("status") or "manual_review")
    face_code = str(face.get("faceCode") or "")
    actions: list[dict[str, Any]] = []
    if status == "over_redundant":
        actions.extend([
            {
                "actionId": f"{face_code}-balanced-split",
                "strategy": "balanced",
                "label": "拆分控制段与普通段",
                "description": "保留项目统一墙厚，将设计面拆分为控制设计段、普通设计段和转角/节点加强段。",
                "riskLevel": "low",
                "requiresRecalculation": True,
            },
            {
                "actionId": f"{face_code}-economic-panel",
                "strategy": "economic",
                "label": "优化槽段分幅长度",
                "description": "在不改变外轮廓的前提下调整槽段均长，降低整面墙按局部控制值放大的程度。",
                "riskLevel": "medium",
                "requiresRecalculation": True,
            },
        ])
    elif status == "conservative":
        actions.append({
            "actionId": f"{face_code}-minor-shortening",
            "strategy": mode,
            "label": "缩短控制设计段",
            "description": "适度缩短设计控制段，保留转角和支撑节点附近局部加强段。",
            "riskLevel": "low",
            "requiresRecalculation": True,
        })
    elif status in {"near_limit", "fail"}:
        actions.append({
            "actionId": f"{face_code}-keep-strengthen",
            "strategy": "conservative",
            "label": "保留长度并局部加强",
            "description": "该面存在接近下限或不满足项，不建议降冗余；优先保留设计长度并复核嵌固、支撑和配筋。",
            "riskLevel": "high",
            "requiresRecalculation": True,
        })
    return actions


def _candidate_for_face(face: dict[str, Any], low: float, high: float, mode: str) -> dict[str, Any]:
    length = max(float(face["physicalLength"]), 0.1)
    r_max = float(face.get("rMax") or high)
    r_min = float(face.get("rMin") or low)
    current_panel = float(face.get("currentPanelLength") or min(6.0, max(4.0, length / max(face.get("wallCount", 1), 1))))
    if r_max > high * 1.8 and r_min >= low:
        section_len = max(12.0, min(30.0, length * 0.42))
        panel_len = 6.0 if length >= 36 else 5.0
        action = "split_face_into_design_zones"
        reason = "该设计面控制冗余偏高，建议拆分为控制段、普通段和转角/节点段，避免整面墙按局部控制值统一放大。"
    elif r_max > high and r_min >= low:
        section_len = max(15.0, min(36.0, length * 0.58))
        panel_len = min(6.0, max(5.0, current_panel))
        action = "shorten_governing_design_length"
        reason = "该设计面略偏保守，建议缩短控制设计段长度并维持项目统一墙厚。"
    elif r_min < low:
        section_len = length
        panel_len = min(5.0, current_panel)
        action = "keep_face_length_and_strengthen_locally"
        reason = "该设计面存在接近下限项，不建议降冗余；优先保留设计长度并在控制段局部加强。"
    else:
        section_len = max(18.0, min(length, length * 0.75))
        panel_len = min(6.0, max(5.0, current_panel))
        action = "keep_or_minor_adjustment"
        reason = "该设计面冗余处于目标带附近，仅建议优化分幅长度和局部加强段表达。"
    local_strength = max(6.0, min(section_len, length * 0.28))
    estimated_r_max = max(low * 1.05, min(r_max, r_max * math.sqrt(section_len / max(length, 1e-6))))
    if action.startswith("keep_face"):
        estimated_r_max = r_max
    after_panel_count = _panel_count(length, panel_len)
    before_panel_count = max(1, int(face.get("wallCount") or _panel_count(length, current_panel)))
    candidate = {
        "candidateId": f"WLO-{face['faceCode']}-{mode}",
        "faceCode": face["faceCode"],
        "intent": "统一墙厚前提下优化设计面长度、分幅长度和局部加强段长度",
        "action": action,
        "reason": reason,
        "before": {
            "projectUniformThickness": face.get("projectUniformThickness"),
            "designLength": round(length, 3),
            "panelLength": round(current_panel, 3),
            "panelCount": before_panel_count,
            "rMin": face.get("rMin"),
            "rMax": face.get("rMax"),
        },
        "after": {
            "projectUniformThickness": face.get("projectUniformThickness"),
            "designSectionLength": round(section_len, 3),
            "panelLength": round(panel_len, 3),
            "panelCount": after_panel_count,
            "localStrengtheningLength": round(local_strength, 3),
            "estimatedRMax": round(estimated_r_max, 3),
        },
        "score": round(100.0 - max(0.0, estimated_r_max - high) * 4.0 - max(0.0, low - r_min) * 18.0 - abs(after_panel_count - before_panel_count) * 0.4, 2),
        "status": "candidate" if r_min >= 1.0 else "blocked",
    }
    candidate["repairActions"] = _repair_actions({**face, "status": _status_for_r(r_max, low, high)}, low, high, mode)
    return candidate


def _issue_suggestions(faces: list[dict[str, Any]], candidates: list[dict[str, Any]], low: float, high: float) -> list[dict[str, Any]]:
    by_face_candidate = {str(c.get("faceCode")): c for c in candidates}
    suggestions: list[dict[str, Any]] = []
    for face in faces:
        status = str(face.get("status") or "manual_review")
        if status not in {"fail", "near_limit", "over_redundant", "conservative"}:
            continue
        face_code = str(face.get("faceCode") or "")
        candidate = by_face_candidate.get(face_code) or face.get("recommendation") or {}
        if status == "over_redundant":
            severity = "warning"
            title = f"{face_code} 设计面冗余偏高"
            recommendation = "优先采纳均衡候选：拆分控制段/普通段并保留局部加强段；采纳后必须复算。"
        elif status == "conservative":
            severity = "manual_review"
            title = f"{face_code} 设计面偏保守"
            recommendation = "可缩短控制设计段或仅优化槽段分幅；建议与墙身云图和控制条文联动复核。"
        elif status == "near_limit":
            severity = "warning"
            title = f"{face_code} 设计面接近下限"
            recommendation = "不建议降冗余；保留当前设计长度并复核支撑、嵌固和局部配筋。"
        else:
            severity = "fail"
            title = f"{face_code} 设计面存在不满足项"
            recommendation = "先处理 fail 后再进行降冗余优化。"
        suggestions.append({
            "id": f"wall-length-{face_code}-{status}",
            "category": "wall_length_redundancy",
            "severity": severity,
            "faceCode": face_code,
            "objectId": (face.get("wallIds") or [None])[0],
            "objectType": "diaphragm_wall_design_face",
            "title": title,
            "message": f"Rmin={face.get('rMin', '-')}，Rmax={face.get('rMax', '-')}，目标带={low:g}–{high:g}。",
            "recommendation": recommendation,
            "candidateId": candidate.get("candidateId"),
            "repairActions": candidate.get("repairActions", []),
            "locator": {
                "workflowStep": "calculation",
                "targetPanel": "WallLengthRedundancyPanel",
                "objectType": "diaphragm_wall_design_face",
                "objectId": (face.get("wallIds") or [None])[0],
                "objectCode": face_code,
                "action": "open_wall_length_redundancy_panel",
                "highlightTargets": ["result", "plan", "threeD", "cad"],
            },
        })
    return suggestions


def analyze_wall_length_redundancy(project: Project, target_low: float = 2.0, target_high: float = 8.0, mode: str = "balanced") -> dict[str, Any]:
    retaining = project.retaining_system
    if not retaining or not retaining.diaphragm_walls:
        return {
            "projectId": project.id,
            "status": "fail",
            "message": "尚未生成围护墙，无法进行设计长度冗余优化。",
            "uniformThickness": None,
            "faces": [],
            "candidates": [],
            "issueSuggestions": [],
            "repairActions": [],
            "historySummary": _history_summary(project),
            "closedLoopStatus": _closed_loop_status(project, 1, 0),
            "summary": {},
        }
    if mode in TARGET_PRESETS:
        target_low, target_high = TARGET_PRESETS[mode]
    thicknesses = [round(float(w.thickness), 3) for w in retaining.diaphragm_walls]
    uniform_thickness = round(float(median(thicknesses)), 3)
    inconsistent = sorted(set(thicknesses))
    trace = build_calculation_trace(project)
    entries = trace.get("entries", [])
    by_face: dict[str, dict[str, Any]] = {}
    id_to_face: dict[str, str] = {}
    for wall in retaining.diaphragm_walls:
        face = wall.design_face_code or wall.segment_id or wall.panel_code
        ids = [wall.id, wall.segment_id, wall.panel_code, *(wall.face_segment_ids or [])]
        for item in ids:
            if item:
                id_to_face[str(item)] = face
        row = by_face.setdefault(face, {
            "faceCode": face,
            "wallIds": [],
            "segmentIds": [],
            "panelCodes": [],
            "physicalLength": 0.0,
            "wallCount": 0,
            "currentDesignLength": float(wall.design_length or 0.0),
            "projectUniformThickness": uniform_thickness,
            "redundancyValues": [],
            "governingChecks": [],
        })
        row["wallIds"].append(wall.id)
        row["segmentIds"].append(wall.segment_id)
        row["panelCodes"].append(wall.panel_code)
        row["physicalLength"] += _length(wall.axis.points)
        row["wallCount"] += 1
        if wall.design_length:
            row["currentDesignLength"] = max(float(row["currentDesignLength"]), float(wall.design_length))
    for entry in entries:
        obj = str(entry.get("objectId") or "")
        face = id_to_face.get(obj)
        if not face:
            continue
        r = _entry_redundancy(entry)
        if r is None or not math.isfinite(r):
            continue
        row = by_face[face]
        row["redundancyValues"].append(round(r, 4))
        row["governingChecks"].append({
            "title": entry.get("title"),
            "category": entry.get("category"),
            "stageName": entry.get("stageName"),
            "redundancyIndex": round(r, 3),
            "status": _status_for_r(r, target_low, target_high),
            "demand": entry.get("demandValue"),
            "capacity": entry.get("capacityValue"),
            "unit": entry.get("unit"),
        })
    faces: list[dict[str, Any]] = []
    for row in by_face.values():
        values = [float(v) for v in row.pop("redundancyValues", [])]
        if values:
            r_min = min(values)
            r_max = max(values)
            r_avg = sum(values) / len(values)
        else:
            r_min, r_max, r_avg = None, None, None
        length = float(row["physicalLength"])
        wall_count = max(int(row["wallCount"]), 1)
        current_panel = length / wall_count if wall_count else length
        row.update({
            "physicalLength": round(length, 3),
            "currentPanelLength": round(current_panel, 3),
            "rMin": round(r_min, 3) if r_min is not None else None,
            "rMax": round(r_max, 3) if r_max is not None else None,
            "rAvg": round(r_avg, 3) if r_avg is not None else None,
            "targetLow": target_low,
            "targetHigh": target_high,
            "status": _status_for_r(r_max if r_max is not None else None, target_low, target_high),
        })
        row["recommendation"] = _candidate_for_face(row, target_low, target_high, mode)
        row["repairActions"] = row["recommendation"].get("repairActions", [])
        row["governingChecks"] = sorted(row["governingChecks"], key=lambda item: abs(float(item.get("redundancyIndex") or 0) - target_high), reverse=True)[:8]
        faces.append(row)
    candidates = sorted([f["recommendation"] for f in faces], key=lambda c: (c["status"] != "candidate", -float(c["score"])))[:6]
    outside = sum(1 for f in faces if f.get("status") in {"fail", "near_limit", "over_redundant", "conservative"})
    over = sum(1 for f in faces if f.get("status") == "over_redundant")
    near = sum(1 for f in faces if f.get("status") in {"fail", "near_limit"})
    issue_suggestions = _issue_suggestions(faces, candidates, target_low, target_high)
    repair_actions = [action for suggestion in issue_suggestions for action in suggestion.get("repairActions", [])]
    status = "warning" if outside or _recompute_required(project) else "pass"
    result = {
        "projectId": project.id,
        "status": status,
        "message": "已按项目统一墙厚分析围护墙设计面长度、槽段分幅和局部加强段冗余。",
        "mode": mode,
        "targetBand": {"low": target_low, "high": target_high},
        "uniformThickness": {
            "value": uniform_thickness,
            "source": "project_median_thickness",
            "isUniform": len(inconsistent) <= 1,
            "allThicknesses": inconsistent,
            "policy": "项目统一墙厚；本优化器不独立改变单面墙厚。",
        },
        "summary": {
            "faceCount": len(faces),
            "candidateCount": len(candidates),
            "outsideTargetFaceCount": outside,
            "overRedundantFaceCount": over,
            "nearLimitFaceCount": near,
            "repairActionCount": len(repair_actions),
            "targetBand": f"{target_low:g}–{target_high:g}",
        },
        "faces": sorted(faces, key=lambda item: item["faceCode"]),
        "candidates": candidates,
        "issueSuggestions": issue_suggestions,
        "repairActions": repair_actions,
        "historySummary": _history_summary(project),
    }
    result["closedLoopStatus"] = _closed_loop_status(project, outside, len(candidates))
    return result


def _find_candidate(project: Project, candidate_id: str, mode: str = "balanced", target_low: float = 2.0, target_high: float = 8.0) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_modes = [mode, "balanced", "conservative", "economic"]
    seen: set[str] = set()
    for item_mode in requested_modes:
        if item_mode in seen:
            continue
        seen.add(item_mode)
        analysis = analyze_wall_length_redundancy(project, target_low=target_low, target_high=target_high, mode=item_mode)
        candidate = next((c for c in analysis.get("candidates", []) if c.get("candidateId") == candidate_id), None)
        if candidate:
            return analysis, candidate
    raise ValueError(f"Candidate not found: {candidate_id}")


def apply_wall_length_candidate(project: Project, candidate_id: str, mode: str = "balanced", target_low: float = 2.0, target_high: float = 8.0) -> dict[str, Any]:
    analysis, candidate = _find_candidate(project, candidate_id, mode=mode, target_low=target_low, target_high=target_high)
    face = str(candidate["faceCode"])
    after = candidate["after"]
    changed = []
    if project.retaining_system:
        summary_before = dict(analysis.get("summary") or {})
        before_candidate = dict(candidate.get("before") or {})
        for wall in project.retaining_system.diaphragm_walls:
            if (wall.design_face_code or wall.segment_id) == face:
                before = wall.design_length
                wall.design_length = float(after["designSectionLength"])
                if wall.design_results:
                    notes = list(wall.design_results.notes or [])
                    notes.append(
                        f"围护墙设计长度优化：候选 {candidate_id}，设计段 {after.get('designSectionLength')}m，"
                        f"分幅 {after.get('panelLength')}m，局部加强段 {after.get('localStrengtheningLength')}m；"
                        "几何边界和项目统一墙厚未改变。"
                    )
                    wall.design_results.notes = list(dict.fromkeys(notes))
                changed.append({"wallId": wall.id, "panelCode": wall.panel_code, "beforeDesignLength": before, "afterDesignLength": wall.design_length})
        history_entry = {
            "candidateId": candidate_id,
            "appliedAt": _now(),
            "mode": analysis.get("mode"),
            "targetBand": analysis.get("targetBand"),
            "changedFaces": [face],
            "changedWalls": changed,
            "before": {
                "summary": summary_before,
                "candidate": before_candidate,
            },
            "estimatedAfter": {
                "designSectionLength": after.get("designSectionLength"),
                "panelLength": after.get("panelLength"),
                "localStrengtheningLength": after.get("localStrengtheningLength"),
                "estimatedRMax": after.get("estimatedRMax"),
                "panelCount": after.get("panelCount"),
            },
            "recomputeRequired": True,
            "professionalReviewRequired": True,
        }
        history = _history(project)
        history.append(history_entry)
        project.retaining_system.layout_summary["wallLengthOptimization"] = {
            "selectedCandidateId": candidate_id,
            "selectedFaceCode": face,
            "mode": analysis.get("mode"),
            "targetBand": analysis.get("targetBand"),
            "candidate": candidate,
            "changedWallCount": len(changed),
            "recomputeRequired": True,
        }
        project.retaining_system.layout_summary["wallLengthOptimizationHistory"] = history
        project.retaining_system.layout_summary["wallLengthOptimizationRecomputeRequired"] = True
        project.retaining_system.layout_summary["wallLengthOptimizationLastAppliedAt"] = history_entry["appliedAt"]
        project.retaining_system.warnings = list(dict.fromkeys([
            *(project.retaining_system.warnings or []),
            "已采纳围护墙设计长度冗余均衡候选；未改变项目统一墙厚和基坑外轮廓，需重新运行规范计算复核。",
        ]))
    return {
        "projectId": project.id,
        "status": "pass",
        "candidateId": candidate_id,
        "changedWalls": changed,
        "recomputeRequired": True,
        "message": "已写入设计长度优化建议；请重新运行计算校核以更新冗余指标。",
        "analysisBeforeApply": analysis,
    }


def mark_wall_length_recalculated(project: Project, calculation_result_id: str | None = None) -> None:
    if not project.retaining_system:
        return
    summary = project.retaining_system.layout_summary
    if not summary.get("wallLengthOptimizationHistory"):
        return
    now = _now()
    summary["wallLengthOptimizationRecomputeRequired"] = False
    summary["wallLengthOptimizationLastRecomputedAt"] = now
    summary["wallLengthOptimizationLastCalculationResultId"] = calculation_result_id
    history = _history(project)
    if history:
        history[-1]["recomputeRequired"] = False
        history[-1]["recomputedAt"] = now
        history[-1]["calculationResultId"] = calculation_result_id
        summary["wallLengthOptimizationHistory"] = history
    if isinstance(summary.get("wallLengthOptimization"), dict):
        summary["wallLengthOptimization"]["recomputeRequired"] = False
        summary["wallLengthOptimization"]["lastRecomputedAt"] = now


def build_wall_length_redundancy_report(project: Project, mode: str = "balanced") -> dict[str, Any]:
    analysis = analyze_wall_length_redundancy(project, mode=mode)
    return {
        "projectId": project.id,
        "reportVersion": SOFTWARE_VERSION,
        "generatedAt": _now(),
        "analysis": analysis,
        "history": _history(project),
        "engineeringBoundary": "围护墙厚度按项目统一值控制；本报告仅记录设计面长度、槽段分幅和局部加强段长度优化建议，正式施工图仍需专业复核。",
    }


def export_wall_length_redundancy_report(project: Project, output_dir: Path, mode: str = "balanced") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{project.id}_wall_length_redundancy_v3_0_0.json"
    path.write_text(json.dumps(build_wall_length_redundancy_report(project, mode=mode), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
