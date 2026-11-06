from __future__ import annotations

from itertools import product
from statistics import mean
from typing import Any

from app.schemas.domain import Project
from app.services.support_layout_optimizer import optimize_support_layout_candidates
from app.services.support_layout_repair import adopt_support_layout_candidate
from app.services.wall_length_optimizer import analyze_wall_length_redundancy, apply_wall_length_candidate
from app.services.wall_vertical_length_optimizer import analyze_wall_vertical_length, apply_wall_vertical_length_candidate
from app.services.calculation_state import invalidate_calculation_state


def _plan_length_schemes(project: Project, mode: str) -> list[dict[str, Any]]:
    """Build discrete plan-length/panelization alternatives for joint optimization.

    The excavation boundary remains a geometric hard constraint.  The variables
    are the calculation-control segment length, construction-panel width/count
    and local strengthening range on each retaining-wall face.
    """
    analysis = analyze_wall_length_redundancy(project, mode=mode)
    candidates = [item for item in analysis.get("candidates", []) if item.get("status") == "candidate"]

    def section(item: dict[str, Any]) -> dict[str, Any]:
        before = item.get("before") or {}
        after = item.get("after") or {}
        return {
            "faceCode": item.get("faceCode"),
            "candidateId": item.get("candidateId"),
            "beforeDesignLengthM": before.get("designLength"),
            "afterDesignSectionLengthM": after.get("designSectionLength"),
            "panelLengthM": after.get("panelLength"),
            "panelCount": after.get("panelCount"),
            "localStrengtheningLengthM": after.get("localStrengtheningLength"),
            "estimatedRMax": after.get("estimatedRMax"),
        }

    def scheme(scheme_id: str, label: str, rows: list[dict[str, Any]], score: float | None = None) -> dict[str, Any]:
        sections = [section(item) for item in rows]
        panel_change = sum(
            abs(int((item.get("after") or {}).get("panelCount", 0) or 0) - int((item.get("before") or {}).get("panelCount", 0) or 0))
            for item in rows
        )
        return {
            "schemeId": scheme_id,
            "label": label,
            "candidateIds": [str(item["candidateId"]) for item in rows],
            "faceCount": int(analysis.get("summary", {}).get("faceCount", 0) or 0),
            "modifiedFaceCount": len(rows),
            "score": round(score if score is not None else mean(float(item.get("score", 0.0) or 0.0) for item in rows), 2),
            "estimatedPanelCountChange": panel_change,
            "designSections": sections,
            "variableDefinition": {
                "excavationBoundaryFixed": True,
                "calculationSectionLengthVariable": True,
                "constructionPanelLengthVariable": True,
                "localStrengtheningRangeVariable": True,
            },
        }

    schemes = [{
        "schemeId": "WLP-KEEP",
        "label": "保持当前计算控制段与施工槽段分幅",
        "candidateIds": [],
        "faceCount": int(analysis.get("summary", {}).get("faceCount", 0) or 0),
        "modifiedFaceCount": 0,
        "score": 88.0,
        "estimatedPanelCountChange": 0,
        "designSections": [],
        "variableDefinition": {
            "excavationBoundaryFixed": True,
            "calculationSectionLengthVariable": True,
            "constructionPanelLengthVariable": True,
            "localStrengtheningRangeVariable": True,
        },
    }]
    ordered = sorted(candidates, key=lambda item: (-float(item.get("score", 0.0) or 0.0), str(item.get("faceCode") or "")))
    # Single-face alternatives make wall length a genuine per-face variable, not
    # a binary keep/all switch.
    for item in ordered[:4]:
        face = str(item.get("faceCode") or "FACE")
        schemes.append(scheme(f"WLP-{mode.upper()}-{face}", f"仅优化墙面 {face} 的控制段与槽段分幅", [item]))

    # For four-sided pits, offer paired opposite faces.  This preserves symmetry
    # while still allowing long and short sides to use different design lengths.
    by_face = {str(item.get("faceCode") or ""): item for item in ordered}
    for pair_index, pair in enumerate((("F1", "F3"), ("F2", "F4")), start=1):
        rows = [by_face[face] for face in pair if face in by_face]
        if len(rows) == 2:
            schemes.append(scheme(f"WLP-{mode.upper()}-PAIR-{pair_index}", f"对称优化墙面 {pair[0]} 与 {pair[1]}", rows))

    if ordered:
        schemes.append(scheme(f"WLP-{mode.upper()}-ALL", "联合优化全部墙面的控制段、槽段分幅与局部加强范围", ordered))
    return schemes


def _vertical_schemes(project: Project, mode: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analysis = analyze_wall_vertical_length(project, mode=mode)
    candidates = [item for item in analysis.get("candidates", []) if item.get("status") == "candidate"]
    # Keep current toe is always retained as a comparison baseline.
    candidates.sort(key=lambda item: (item.get("candidateId") != "WVL-KEEP", -float(item.get("score", 0.0) or 0.0)))
    return analysis, candidates[:3]


def _joint_score(support: Any, plan: dict[str, Any], vertical: dict[str, Any]) -> float:
    metrics = support.metrics or {}
    support_score = float(support.score or 0.0)
    plan_score = float(plan.get("score", 0.0) or 0.0)
    vertical_score = float(vertical.get("score", 0.0) or 0.0)
    # Extra construction penalties keep aggressive segmentation from winning on
    # concrete quantity alone.
    segmentation_penalty = 1.8 * max(0, int(plan.get("modifiedFaceCount", 0) or 0) - 1)
    segmentation_penalty += 0.6 * int(plan.get("estimatedPanelCountChange", 0) or 0)
    toe_penalty = 4.0 * max(0, int(vertical.get("zoneCount", 1) or 1) - 1)
    congestion_penalty = 3.0 * int(metrics.get("wallJunctionCount", 0) or 0)
    congestion_penalty += 5.0 * int(metrics.get("highDegreeWallJunctionCount", 0) or 0)
    congestion_penalty += 12.0 * int(metrics.get("cornerBraceParallelismIssueCount", 0) or 0)
    congestion_penalty += 10.0 * int(metrics.get("cornerBraceEndpointCongestionCount", 0) or 0)
    score = 0.58 * support_score + 0.18 * plan_score + 0.24 * vertical_score
    return round(max(0.0, min(100.0, score - segmentation_penalty - toe_penalty - congestion_penalty)), 2)


def build_integrated_retaining_candidates(project: Project, mode: str = "balanced", max_candidates: int = 8) -> dict[str, Any]:
    _, support_candidates = optimize_support_layout_candidates(
        project,
        max_candidates=max(3, min(6, max_candidates)),
        preset="clean_support_layout",
    )
    plan_schemes = _plan_length_schemes(project, mode)
    vertical_analysis, vertical_schemes = _vertical_schemes(project, mode)
    rows: list[dict[str, Any]] = []
    for support, plan, vertical in product(support_candidates[:4], plan_schemes[:8], vertical_schemes[:3]):
        metrics = support.metrics or {}
        candidate_id = f"IRD::{support.id}::{plan['schemeId']}::{vertical.get('candidateId')}"
        rows.append({
            "candidateId": candidate_id,
            "supportCandidateId": support.id,
            "wallPlanSchemeId": plan["schemeId"],
            "wallPlanCandidateIds": plan["candidateIds"],
            "wallVerticalCandidateId": vertical.get("candidateId"),
            "status": "candidate" if support.hard_constraints.get("passed", False) else "blocked",
            "score": _joint_score(support, plan, vertical),
            "hardConstraints": support.hard_constraints,
            "primaryCleanlinessMetrics": {
                "illegalCrossingCount": int(metrics.get("supportCrossingCount", support.crossing_count) or 0),
                "wallJunctionCount": int(metrics.get("wallJunctionCount", 0) or 0),
                "highDegreeWallJunctionCount": int(metrics.get("highDegreeWallJunctionCount", 0) or 0),
                "internalJunctionCount": int(metrics.get("internalJunctionCount", 0) or 0),
                "cornerBraceParallelismIssueCount": int(metrics.get("cornerBraceParallelismIssueCount", 0) or 0),
                "cornerBraceEndpointCongestionCount": int(metrics.get("cornerBraceEndpointCongestionCount", 0) or 0),
                "totalJunctionCount": int(metrics.get("totalJunctionCount", 0) or 0),
                "planIntersectionComplexity": float(metrics.get("planIntersectionComplexity", 0.0) or 0.0),
            },
            "designVariables": {
                "supportTopologyFamily": (support.variable_summary or {}).get("topologyFamily"),
                "supportPositionPattern": (support.variable_summary or {}).get("positionPattern"),
                "supportTargetSpacingM": support.target_spacing,
                "columnMaximumSpanM": support.column_max_span,
                "wallPlanDesignLengthVariable": True,
                "wallPlanCalculationSectionLengthVariable": True,
                "wallConstructionPanelLengthVariable": True,
                "excavationBoundaryFixed": True,
                "wallPlanVariableDefinition": plan.get("variableDefinition", {}),
                "wallPlanDesignSections": plan["designSections"],
                "wallVerticalLengthVariable": True,
                "wallToeZones": vertical.get("zones", []),
            },
            "quantities": {
                "supportCount": support.support_count,
                "columnCount": support.column_count,
                "wallPlanModifiedFaceCount": plan.get("modifiedFaceCount", 0),
                "wallToeZoneCount": vertical.get("zoneCount", 1),
                "estimatedConcreteSavingM3": vertical.get("estimatedConcreteSavingM3", 0.0),
                "estimatedConcreteSavingRatio": vertical.get("estimatedSavingRatio", 0.0),
            },
            "supportCandidate": support.model_dump(mode="json", by_alias=True),
            "wallPlanScheme": plan,
            "wallVerticalScheme": vertical,
            "engineeringBoundary": "先满足安全与几何硬约束，再最小化非法穿越、角撑扇形/节点拥挤、墙上汇交和内部汇交；墙体平面控制设计段与竖向墙趾均作为变量，但槽段连续性、防水、吊装和规范复核保持硬边界。",
        })
    def cleanliness_key(item: dict[str, Any]) -> tuple:
        metrics = item["primaryCleanlinessMetrics"]
        return (
            item["status"] != "candidate",
            metrics["illegalCrossingCount"],
            metrics["cornerBraceParallelismIssueCount"],
            metrics["cornerBraceEndpointCongestionCount"],
            metrics["highDegreeWallJunctionCount"],
            metrics["wallJunctionCount"],
            metrics["totalJunctionCount"],
            metrics["planIntersectionComplexity"],
        )

    rows.sort(key=lambda item: (*cleanliness_key(item), -float(item["score"])))
    # Preserve the strict cleanliness tier, then expose genuine wall-length and
    # support-topology alternatives inside that tier.  This prevents the top list
    # from being filled by the same wall scheme under small toe/support variants.
    selected_rows: list[dict[str, Any]] = []
    if rows:
        best_cleanliness = cleanliness_key(rows[0])
        clean_pool = [row for row in rows if cleanliness_key(row) == best_cleanliness]
        seen_plan: set[str] = set()
        seen_support: set[str] = set()
        for row in clean_pool:
            plan_id = str(row.get("wallPlanSchemeId") or "")
            support_id = str(row.get("supportCandidateId") or "")
            if plan_id not in seen_plan or support_id not in seen_support:
                selected_rows.append(row)
                seen_plan.add(plan_id)
                seen_support.add(support_id)
                if len(selected_rows) >= max_candidates:
                    break
        for row in rows:
            if len(selected_rows) >= max_candidates:
                break
            if row not in selected_rows:
                selected_rows.append(row)
    for rank, item in enumerate(selected_rows, start=1):
        item["rank"] = rank
    selected = selected_rows[0] if selected_rows else None
    return {
        "projectId": project.id,
        "mode": mode,
        "status": "pass" if selected and selected["status"] == "candidate" else "manual_review",
        "method": "joint support-topology, wall plan design-length and vertical wall-length optimization",
        "primaryObjectiveOrder": [
            "safety_and_geometry_hard_constraints",
            "minimum_illegal_support_crossings",
            "minimum_high_degree_wall_junctions",
            "minimum_wall_junctions",
            "minimum_total_plan_junctions",
            "minimum_plan_intersection_complexity",
            "balanced_structural_performance_material_and_constructability",
        ],
        "wallPlanAnalysisAvailable": bool(plan_schemes),
        "wallVerticalAnalysis": vertical_analysis,
        "candidates": selected_rows,
        "recommendedCandidateId": selected.get("candidateId") if selected else None,
        "summary": {
            "candidateCount": min(len(rows), max_candidates),
            "supportVariableCount": len(support_candidates),
            "wallPlanSchemeCount": len(plan_schemes),
            "wallVerticalSchemeCount": len(vertical_schemes),
            "wallDesignLengthIncludedAsVariable": True,
            "wallPlanCalculationSectionLengthIncludedAsVariable": True,
            "constructionPanelLengthIncludedAsVariable": True,
            "verticalWallToeLengthIncludedAsVariable": True,
            "excavationBoundaryRemainsFixed": True,
            "wallEndpointJunctionIncludedInObjective": True,
        },
        "professionalReviewRequired": True,
    }


def apply_integrated_retaining_candidate(project: Project, candidate_id: str, mode: str = "balanced") -> dict[str, Any]:
    analysis = build_integrated_retaining_candidates(project, mode=mode, max_candidates=12)
    candidate = next((item for item in analysis.get("candidates", []) if item.get("candidateId") == candidate_id), None)
    if candidate is None:
        raise ValueError(f"Integrated candidate not found: {candidate_id}")
    if candidate.get("status") != "candidate":
        raise ValueError("Integrated candidate does not satisfy the support hard constraints")

    support_result = adopt_support_layout_candidate(project, str(candidate["supportCandidateId"]))
    if support_result.status == "fail":
        raise ValueError(support_result.summary)

    plan_results = []
    for plan_candidate_id in candidate.get("wallPlanCandidateIds", []):
        plan_results.append(apply_wall_length_candidate(project, str(plan_candidate_id), mode=mode))

    vertical_id = str(candidate.get("wallVerticalCandidateId") or "")
    vertical_result = None
    if vertical_id and vertical_id != "WVL-KEEP":
        vertical_result = apply_wall_vertical_length_candidate(project, vertical_id, mode=mode)

    if project.retaining_system:
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary["integratedRetainingOptimization"] = {
            "candidateId": candidate_id,
            "mode": mode,
            "score": candidate.get("score"),
            "primaryCleanlinessMetrics": candidate.get("primaryCleanlinessMetrics"),
            "designVariables": candidate.get("designVariables"),
            "recomputeRequired": True,
            "professionalReviewRequired": True,
        }
    invalidate_calculation_state(project, reason=f"integrated retaining-system candidate {candidate_id} applied")
    return {
        "projectId": project.id,
        "candidateId": candidate_id,
        "supportResult": support_result.model_dump(mode="json", by_alias=True),
        "wallPlanResults": plan_results,
        "wallVerticalResult": vertical_result,
        "recomputeRequired": True,
        "message": "已应用支撑拓扑、墙体平面设计段与竖向墙长联合候选；必须重新运行分阶段计算、配筋、IFC和施工图导出。",
    }
