from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas.domain import Project
from app.services.wall_length_optimizer import analyze_wall_length_redundancy
from app.version import SOFTWARE_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wall_length(points: list[Any]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(points[:-1], points[1:]):
        total += math.hypot(float(b.x - a.x), float(b.y - a.y))
    return total


def _latest(project: Project):
    return project.calculation_results[-1] if project.calculation_results else None


def _check_summary(project: Project) -> dict[str, int]:
    latest = _latest(project)
    raw = latest.check_summary if latest and latest.check_summary else {}
    return {
        "pass": int(raw.get("pass") or 0),
        "fail": int(raw.get("fail") or 0),
        "warning": int(raw.get("warning") or 0),
        "manualReview": int(raw.get("manualReview") or raw.get("manual_review") or 0),
    }


def _layout(project: Project) -> dict[str, Any]:
    if not project.retaining_system:
        return {}
    return dict(project.retaining_system.layout_summary or {})


def _history(project: Project) -> list[dict[str, Any]]:
    raw = _layout(project).get("wallLengthOptimizationHistory", [])
    return [dict(item) for item in raw if isinstance(item, dict)]


def _face_state(project: Project) -> list[dict[str, Any]]:
    ret = project.retaining_system
    if not ret:
        return []
    faces: dict[str, dict[str, Any]] = {}
    for wall in ret.diaphragm_walls:
        face = wall.design_face_code or wall.segment_id or wall.panel_code
        item = faces.setdefault(face, {
            "faceCode": face,
            "wallCount": 0,
            "panelCodes": [],
            "physicalLength": 0.0,
            "designLengths": [],
            "uniformThickness": wall.thickness,
            "bottomElevations": [],
        })
        item["wallCount"] += 1
        item["panelCodes"].append(wall.panel_code)
        item["physicalLength"] += _wall_length(wall.axis.points)
        if wall.design_length is not None:
            item["designLengths"].append(float(wall.design_length))
        item["bottomElevations"].append(float(wall.bottom_elevation))
    rows: list[dict[str, Any]] = []
    for item in faces.values():
        lengths = item.pop("designLengths", [])
        bottoms = item.pop("bottomElevations", [])
        item["physicalLength"] = round(float(item["physicalLength"]), 3)
        item["currentDesignLength"] = round(max(lengths), 3) if lengths else item["physicalLength"]
        item["minDesignLength"] = round(min(lengths), 3) if lengths else item["physicalLength"]
        item["averagePanelLength"] = round(item["physicalLength"] / max(int(item["wallCount"]), 1), 3)
        item["bottomElevationMin"] = round(min(bottoms), 3) if bottoms else None
        item["bottomElevationMax"] = round(max(bottoms), 3) if bottoms else None
        rows.append(item)
    return sorted(rows, key=lambda row: str(row["faceCode"]))


def _current_kpis(project: Project) -> dict[str, Any]:
    ret = project.retaining_system
    latest = _latest(project)
    faces = _face_state(project)
    physical = sum(float(row.get("physicalLength") or 0.0) for row in faces)
    design = sum(float(row.get("currentDesignLength") or 0.0) for row in faces)
    thicknesses = [float(w.thickness) for w in ret.diaphragm_walls] if ret else []
    latest_gov = latest.governing_values.model_dump(mode="json", by_alias=True) if latest else {}
    return {
        "faceCount": len(faces),
        "wallCount": len(ret.diaphragm_walls) if ret else 0,
        "supportCount": len(ret.supports) if ret else 0,
        "columnCount": len(ret.columns) if ret else 0,
        "totalPhysicalWallLength": round(physical, 3),
        "totalDesignFaceLength": round(design, 3),
        "designToPhysicalLengthRatio": round(design / physical, 3) if physical > 1e-9 else None,
        "uniformWallThickness": round(sum(thicknesses) / len(thicknesses), 3) if thicknesses else None,
        "wallThicknessIsUniform": len({round(t, 3) for t in thicknesses}) <= 1 if thicknesses else None,
        "calculationResultCount": len(project.calculation_results),
        "latestCheckSummary": _check_summary(project),
        "maxWallDisplacement": latest_gov.get("maxDisplacement"),
        "maxSupportAxialForce": latest_gov.get("maxSupportAxialForce"),
        "governingCheckStatus": latest_gov.get("governingCheckStatus"),
    }


def _delta_from_history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    changed = entry.get("changedWalls") or []
    before_total = 0.0
    after_total = 0.0
    count = 0
    for item in changed:
        if not isinstance(item, dict):
            continue
        b = item.get("beforeDesignLength")
        a = item.get("afterDesignLength")
        try:
            if b is not None and a is not None:
                before_total += float(b)
                after_total += float(a)
                count += 1
        except Exception:
            continue
    return {
        "changedWallCount": count,
        "changedDesignLengthBefore": round(before_total, 3),
        "changedDesignLengthAfter": round(after_total, 3),
        "changedDesignLengthDelta": round(after_total - before_total, 3),
        "changedDesignLengthReductionRatio": round((before_total - after_total) / before_total, 4) if before_total > 1e-9 else 0.0,
    }


def _scheme_snapshots(project: Project) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for index, entry in enumerate(_history(project), start=1):
        delta = _delta_from_history_entry(entry)
        status = "closed" if not entry.get("recomputeRequired") and entry.get("recomputedAt") else "pending_recalculation"
        snapshots.append({
            "schemeId": f"WLS-{index:03d}",
            "candidateId": entry.get("candidateId"),
            "status": status,
            "mode": entry.get("mode"),
            "appliedAt": entry.get("appliedAt"),
            "recomputedAt": entry.get("recomputedAt"),
            "calculationResultId": entry.get("calculationResultId"),
            "changedFaces": entry.get("changedFaces") or [],
            "targetBand": entry.get("targetBand"),
            "delta": delta,
            "estimatedAfter": entry.get("estimatedAfter") or {},
            "professionalReviewRequired": bool(entry.get("professionalReviewRequired", True)),
        })
    return snapshots


def _delivery_gate(project: Project) -> dict[str, Any]:
    summary = _check_summary(project)
    layout = _layout(project)
    recompute_required = bool(layout.get("wallLengthOptimizationRecomputeRequired"))
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not project.retaining_system:
        blockers.append({"category": "model", "message": "尚未生成围护结构。"})
    if not project.calculation_results:
        blockers.append({"category": "calculation", "message": "尚未运行一键计算校核。"})
    if summary["fail"] > 0:
        blockers.append({"category": "compliance", "message": f"仍有 {summary['fail']} 项规范筛查不满足。"})
    if recompute_required:
        blockers.append({"category": "optimization", "message": "已采纳围护墙设计长度优化建议，但尚未重新计算。"})
    if summary["warning"] > 0:
        warnings.append({"category": "warning", "message": f"仍有 {summary['warning']} 项预警。"})
    if summary["manualReview"] > 0:
        warnings.append({"category": "review", "message": f"仍有 {summary['manualReview']} 项需要人工复核。"})
    status = "ready" if not blockers else "blocked"
    if not blockers and warnings:
        status = "warning"
    return {
        "status": status,
        "allowedForDeliveryPackage": status in {"ready", "warning"},
        "allowedForOfficialIssue": status == "ready" and not warnings,
        "blockers": blockers,
        "warnings": warnings,
        "recomputeRequired": recompute_required,
        "headline": "可生成交付包" if status == "ready" else "交付前仍需处理关键项" if status == "blocked" else "可交付但需人工复核",
    }


def _next_actions(project: Project, analysis: dict[str, Any], gate: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not project.retaining_system:
        actions.append({"workflowStep": "retaining", "title": "生成围护体系", "recommendation": "先生成地连墙、支撑和围檩，再进行计算。", "severity": "warning"})
    if not project.calculation_results:
        actions.append({"workflowStep": "calculation", "title": "运行一键计算校核", "recommendation": "当前没有计算结果，无法判断冗余和交付状态。", "severity": "warning"})
    if gate.get("recomputeRequired"):
        actions.append({"workflowStep": "calculation", "title": "重新计算并关闭优化待复算", "recommendation": "采纳设计长度建议后必须重新计算，刷新规范校核与冗余指标。", "severity": "fail"})
    candidates = analysis.get("candidates") or []
    if not gate.get("recomputeRequired") and candidates:
        actions.append({"workflowStep": "calculation", "title": "审查围护墙设计长度候选", "recommendation": "优先处理严重冗余或接近下限设计面，采纳后自动形成方案快照。", "severity": "warning"})
    if gate.get("status") == "ready":
        actions.append({"workflowStep": "export", "title": "生成完整交付包", "recommendation": "当前无阻断项，可导出计算书、IFC、CAD 和优化记录。", "severity": "pass"})
    return actions[:6]


def build_design_scheme_ledger(project: Project, mode: str = "balanced") -> dict[str, Any]:
    analysis = analyze_wall_length_redundancy(project, mode=mode)
    gate = _delivery_gate(project)
    snapshots = _scheme_snapshots(project)
    return {
        "projectId": project.id,
        "ledgerVersion": SOFTWARE_VERSION,
        "generatedAt": _now(),
        "mode": mode,
        "currentKpis": _current_kpis(project),
        "faceState": _face_state(project),
        "wallLengthClosedLoop": {
            "summary": analysis.get("summary") or {},
            "closedLoopStatus": analysis.get("closedLoopStatus") or {},
            "historySummary": analysis.get("historySummary") or {},
            "candidateCount": len(analysis.get("candidates") or []),
            "issueSuggestionCount": len(analysis.get("issueSuggestions") or []),
        },
        "schemeSnapshots": snapshots,
        "activeScheme": snapshots[-1] if snapshots else None,
        "deliveryGate": gate,
        "nextActions": _next_actions(project, analysis, gate),
        "engineeringBoundary": "墙厚按项目统一值控制；设计长度优化只改变设计面控制段、分幅和局部加强表达，不改变基坑外轮廓、墙轴线或正式签审边界。",
    }


def build_project_dashboard(project: Project, mode: str = "balanced") -> dict[str, Any]:
    ledger = build_design_scheme_ledger(project, mode=mode)
    return {
        "projectId": project.id,
        "dashboardVersion": SOFTWARE_VERSION,
        "generatedAt": _now(),
        "headline": ledger["deliveryGate"].get("headline"),
        "currentKpis": ledger["currentKpis"],
        "wallLengthClosedLoop": ledger["wallLengthClosedLoop"],
        "activeScheme": ledger["activeScheme"],
        "deliveryGate": ledger["deliveryGate"],
        "nextActions": ledger["nextActions"],
    }


def export_design_scheme_ledger(project: Project, output_dir: Path, mode: str = "balanced") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{project.id}_design_scheme_ledger_v3_0_0.json"
    path.write_text(json.dumps(build_design_scheme_ledger(project, mode=mode), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
