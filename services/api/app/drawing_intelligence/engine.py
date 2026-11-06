from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.domain import Project


def _package_root(name: str) -> Path:
    return Path(__file__).resolve().parents[4] / "packages" / name


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _polygon_points(project: Project) -> list[tuple[float, float]]:
    if not project.excavation:
        return []
    points = [(float(p.x), float(p.y)) for p in project.excavation.outline.points]
    if len(points) > 2 and points[0] == points[-1]:
        points.pop()
    return points


def _concave_vertex_indices(points: list[tuple[float, float]]) -> list[int]:
    if len(points) < 4:
        return []
    area2 = sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))
    orientation = 1.0 if area2 >= 0.0 else -1.0
    result: list[int] = []
    for i, current in enumerate(points):
        previous = points[(i - 1) % len(points)]
        following = points[(i + 1) % len(points)]
        cross = (current[0] - previous[0]) * (following[1] - current[1]) - (current[1] - previous[1]) * (following[0] - current[0])
        if cross * orientation < -1e-8:
            result.append(i)
    return result


def _recommendation(rec_id: str, title: str, reason: str, *, priority: str, action: str, sheet_ids: list[str] | None = None, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "id": rec_id,
        "title": title,
        "reason": reason,
        "priority": priority,
        "action": action,
        "sheetRuleIds": sheet_ids or [],
        "confidence": round(confidence, 3),
    }


def _quality_score(context: dict[str, Any], sheets: list[dict[str, Any]], decisions: list[dict[str, Any]], recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    facts = context.get("facts") or {}
    renderers = {str(item.get("renderer") or "") for item in sheets}
    required = {"general_notes", "master_plan", "excavation_section"}
    if facts.get("wallCount"):
        required.update({"wall_rebar_general", "wall_rebar_elevation"})
    if facts.get("supportCount"):
        required.update({"support_level_plan", "detail_compilation"})
    missing = sorted(required - renderers)
    coverage = max(0.0, 100.0 - 14.0 * len(missing))
    overflow = sum(1 for item in sheets if bool((item.get("scaleDecision") or {}).get("overflow")))
    readability = max(0.0, 100.0 - overflow * 12.0)
    traceability = 100.0 if all((item.get("ruleId") or item.get("id")) and item.get("file") for item in sheets) else 75.0
    excluded_required = sum(1 for item in decisions if item.get("included") is False and item.get("required"))
    consistency = max(0.0, 100.0 - excluded_required * 20.0)
    unresolved_high = sum(1 for item in recommendations if item.get("priority") == "high" and not set(item.get("sheetRuleIds") or []) & {str(sheet.get("ruleId") or sheet.get("id")) for sheet in sheets})
    constructability = max(0.0, 100.0 - unresolved_high * 18.0)
    score = 0.30 * coverage + 0.24 * readability + 0.18 * traceability + 0.16 * constructability + 0.12 * consistency
    return {
        "overall": round(score, 2),
        "coverage": round(coverage, 2),
        "readability": round(readability, 2),
        "traceability": round(traceability, 2),
        "constructability": round(constructability, 2),
        "consistency": round(consistency, 2),
        "missingCapabilities": missing,
        "overflowCount": overflow,
        "grade": "A" if score >= 90 else ("B" if score >= 80 else ("C" if score >= 70 else "D")),
    }


def build_drawing_intelligence(project: Project, context: dict[str, Any], sheets: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    facts = dict(context.get("facts") or {})
    points = _polygon_points(project)
    concave_indices = _concave_vertex_indices(points)
    facts["concaveVertexCount"] = len(concave_indices)
    latest = project.calculation_results[-1] if project.calculation_results else None
    diagnostics = dict((latest.design_iteration_summary or {}).get("calculationDiagnostics") or {}) if latest else {}
    root_codes = {str(item.get("code")) for item in diagnostics.get("rootCauses") or []}
    rules = _load_json(_package_root("drawing-knowledge") / "inference-rules.json", {})
    recommendations: list[dict[str, Any]] = []

    if concave_indices:
        recommendations.append(_recommendation(
            "CONCAVE_RETURN_SUPPORT_DETAIL",
            "增加凹角回墙局部支撑大样",
            f"基坑轮廓包含 {len(concave_indices)} 个凹角，回墙法向传力、围檩转折和局部次对撑需要独立表达。",
            priority="high",
            action="启用 D-09 异形凹角回墙局部支撑大样，并绑定回墙、次对撑和临时立柱。",
            sheet_ids=["D09"],
            confidence=0.98,
        ))
    if facts.get("secondarySupportCount", 0):
        recommendations.append(_recommendation(
            "GRID_NODE_DETAIL",
            "保留主次支撑交叉节点大样",
            f"项目包含 {facts.get('secondarySupportCount')} 根次对撑，交叉节点需要表达立柱、承压板、钢筋净距和施工顺序。",
            priority="high",
            action="保留 D-08，并在节点表中列出控制轴力与净距复核状态。",
            sheet_ids=["D08"],
        ))
    if root_codes or (latest and int((latest.check_summary or {}).get("warning") or 0) > 0):
        recommendations.append(_recommendation(
            "CALCULATION_DIAGNOSTIC_SHEET",
            "发行计算诊断与修复台账",
            "计算结果包含自动拓扑修复、工况同步或工程警告，需要把修复动作和控制墙段纳入图纸交付追溯。",
            priority="medium",
            action="在 90_schedules 中输出计算诊断 JSON/CSV，并在总说明中引用结果 ID。",
            sheet_ids=["Q02", "Q03", "Q04"],
            confidence=0.94,
        ))
    if facts.get("wallCount", 0) >= 6 and facts.get("projectWidthM", 0) > 40:
        recommendations.append(_recommendation(
            "WALL_ELEVATION_GROUPING",
            "按几何分区组合墙体立面",
            "墙面数量和轮廓复杂度较高，固定逐墙出图会增加跨图查询和重复说明。",
            priority="medium",
            action="优先按连续墙面与配筋相似度合图，凹角相邻墙面保持在同一张图。",
            sheet_ids=["R02W"],
            confidence=0.86,
        ))

    included_ids = {str(item.get("ruleId") or item.get("id")) for item in sheets}
    for item in recommendations:
        item["satisfied"] = not item.get("sheetRuleIds") or bool(set(item["sheetRuleIds"]) & included_ids)
    quality = _quality_score({**context, "facts": facts}, sheets, decisions, recommendations)
    return {
        "engineVersion": "1.0",
        "knowledgePackage": rules.get("packageId", "pitguard-drawing-knowledge"),
        "facts": {
            "concaveVertexCount": len(concave_indices),
            "concaveVertexIndices": concave_indices,
            "secondarySupportCount": facts.get("secondarySupportCount", 0),
            "wallCount": facts.get("wallCount", 0),
            "supportLevelCount": facts.get("supportLevelCount", 0),
            "calculationRootCauseCodes": sorted(root_codes),
        },
        "recommendations": recommendations,
        "quality": quality,
        "explanation": "推荐由几何复杂度、支撑拓扑、计算诊断和当前规则覆盖共同生成；不替代工程审签。",
    }
