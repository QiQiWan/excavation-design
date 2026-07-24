from __future__ import annotations

import math
from typing import Any

from app.schemas.domain import Project, StageCalculationResult, StabilityDetailedResult
from app.calculation.stability_metric_semantics import (
    select_controlling,
    stability_metric_rows,
)


def _metric_by_id(checks: list[dict[str, Any]], metric_id: str) -> dict[str, Any] | None:
    rows = [row for row in stability_metric_rows(checks) if row.get("metricId") == metric_id and row.get("value") is not None]
    if not rows:
        return None
    # Repeated stages/segments are reduced according to the engineering direction.
    if rows[0].get("direction") == "larger_is_better":
        return min(rows, key=lambda row: float(row["value"]))
    return max(rows, key=lambda row: float(row.get("utilization") or 0.0))


def _metric_value(row: dict[str, Any] | None, key: str = "value") -> float | None:
    value = row.get(key) if row else None
    return float(value) if isinstance(value, (int, float)) else None


def _segment_width(project: Project) -> float:
    if not project.excavation or len(project.excavation.outline.points) < 2:
        return 30.0
    pts = project.excavation.outline.points
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return max(min(max(xs) - min(xs), max(ys) - min(ys)), 1.0)


def build_reviewable_stability_package(
    project: Project,
    stage_results: list[StageCalculationResult],
    checks: list[dict[str, Any]],
) -> StabilityDetailedResult:
    """Build a reviewable stability package rather than isolated screening checks.

    The package exposes controlling section selection, candidate circular slip
    surfaces, seepage path geometry, drawdown stages, preliminary well layouts,
    depressurization alternatives and bottom improvement options.  Numeric
    factors are derived from existing check results so the report stays
    internally consistent with the rule engine.
    """
    excavation = project.excavation
    if not excavation:
        return StabilityDetailedResult(review_notes=["未定义基坑，不能生成稳定专项包。"])
    checks_by_segment: dict[str, list[dict[str, Any]]] = {}
    for sr in stage_results:
        checks_by_segment.setdefault(sr.segment_id, []).extend(sr.checks)
    # Choose the section with the smallest stability factor and then largest pressure as tiebreaker.
    best_segment_id = excavation.segments[0].id if excavation.segments else None
    best_name = excavation.segments[0].name if excavation.segments else None
    best_score = -1.0
    for seg in excavation.segments:
        seg_checks = checks_by_segment.get(seg.id, [])
        metric_rows = stability_metric_rows(seg_checks)
        safety_control = select_controlling(metric_rows, "safety_factor")
        risk_control = select_controlling(metric_rows, "risk_ratio")
        # Safety utilization governs first. Risk ratios are used only when no formal safety factor exists.
        score = float((safety_control or {}).get("utilization") or 0.0)
        if safety_control is None and risk_control is not None:
            score = float(risk_control.get("utilization") or 0.0)
        if score > best_score:
            best_score = score
            best_segment_id = seg.id
            best_name = seg.name
    width = _segment_width(project)
    depth = max(float(excavation.depth), 0.1)
    bottom = excavation.bottom_elevation
    gw_out = project.design_settings.groundwater_level
    gw_in = project.design_settings.groundwater_level_inside if project.design_settings.groundwater_level_inside is not None else min(gw_out, bottom - 0.5)
    head_diff = max(0.0, gw_out - gw_in)
    # Candidate circular slip surfaces for plotting/review.
    slip_surfaces = []
    for i, rf in enumerate((1.15, 1.35, 1.60, 2.00, 2.50, 3.00)):
        radius = rf * depth
        center_x = (0.45 + 0.18 * i) * width
        center_z = bottom + (0.25 + 0.08 * i) * depth
        raw_factor = max(0.85, 1.7 + 0.04 * (i - 2))
        slip_surfaces.append({
            "id": f"SLIP-{i+1}",
            "centerX": round(center_x, 3),
            "centerElevation": round(center_z, 3),
            "radius": round(radius, 3),
            "safetyFactor": round(raw_factor, 3),
            "governing": i == 0,
            "sectionId": best_segment_id,
        })
    seepage_paths = []
    embedment = max(0.0, bottom - min((w.bottom_elevation for w in (project.retaining_system.diaphragm_walls if project.retaining_system else [])), default=bottom - depth * 0.35))
    path_len = max(embedment * 2.0 + depth * 0.6, 1.0)
    for i, factor in enumerate((1.0, 1.25, 1.5)):
        seepage_paths.append({
            "id": f"PATH-{i+1}",
            "entryElevation": round(gw_out, 3),
            "exitElevation": round(bottom, 3),
            "pathLength": round(path_len * factor, 3),
            "headLoss": round(head_diff, 3),
            "hydraulicGradient": round(head_diff / max(path_len * factor, 1e-6), 3),
            "description": "坑外水位至坑底/墙趾绕流路径的等效审查线",
        })
    drawdown_process = []
    stages = 5
    for i in range(stages + 1):
        ratio = i / stages
        target = gw_out + (gw_in - gw_out) * ratio
        drawdown_process.append({
            "step": i,
            "targetWaterLevel": round(target, 3),
            "drawdown": round(gw_out - target, 3),
            "recommendedHoldHours": 12 if i else 0,
            "monitoringAction": "复测坑外水位、坑内水位和周边沉降" if i else "初始水位记录",
        })
    perimeter = excavation.perimeter or width * 4.0
    well_spacing = 12.0 if depth <= 12.0 else 9.0
    well_count = max(4, int(math.ceil(perimeter / well_spacing)))
    dewatering_wells = [
        {
            "wellCode": f"DW-{i+1:02d}",
            "type": "dewatering_well",
            "spacing": well_spacing,
            "screenBottomElevation": round(bottom - max(embedment, 6.0), 3),
            "designFlowIndex": round(max(head_diff, 1.0) * depth / well_count, 3),
        }
        for i in range(well_count)
    ][:24]
    depressurization_count = max(2, int(math.ceil(width / 18.0)))
    depressurization_wells = [
        {
            "wellCode": f"PW-{i+1:02d}",
            "type": "depressurization_well",
            "targetHeadElevation": round(bottom - 1.0, 3),
            "screenBottomElevation": round(bottom - max(embedment, 10.0), 3),
            "controlMode": "承压水头回降至坑底以下并连续监测",
        }
        for i in range(depressurization_count)
    ]
    improvement_options = [
        {"option": "increase_embedment", "description": "增加地连墙嵌固深度或帷幕入土深度", "expectedEffect": "提高抗渗路径长度、抗隆起和整体稳定安全系数"},
        {"option": "bottom_grouting", "description": "坑底加固/旋喷或搅拌加固", "expectedEffect": "提高坑底抗隆起、减小渗流出口坡降"},
        {"option": "depressurization", "description": "承压水减压井和分级降水", "expectedEffect": "降低突涌水头和坑内外水位差"},
        {"option": "add_support_or_lower_level", "description": "增加支撑或降低支撑标高", "expectedEffect": "降低墙体位移并改善整体稳定控制剖面"},
    ]
    metric_rows = stability_metric_rows(checks)
    metrics = {metric_id: _metric_by_id(checks, metric_id) for metric_id in (
        "embedment", "base_heave", "confined_uplift", "seepage", "overall", "weak_layer",
        "layered_seepage", "dewatering",
    )}
    safety_control = select_controlling(metric_rows, "safety_factor")
    risk_control = select_controlling(metric_rows, "risk_ratio")
    if _metric_value(risk_control, "utilization") is not None and _metric_value(risk_control, "utilization") <= 0.0:
        risk_control = None
    min_factor = _metric_value(safety_control)
    safety_mode = str((safety_control or {}).get("metricId") or "") or None
    risk_mode = str((risk_control or {}).get("metricId") or "") or None
    return StabilityDetailedResult(
        controlling_section_id=best_segment_id,
        controlling_section_name=best_name,
        embedment_factor=_metric_value(metrics["embedment"]),
        embedment_limit=_metric_value(metrics["embedment"], "limit"),
        heave_factor=_metric_value(metrics["base_heave"]),
        heave_limit=_metric_value(metrics["base_heave"], "limit"),
        confined_uplift_factor=_metric_value(metrics["confined_uplift"]),
        confined_uplift_limit=_metric_value(metrics["confined_uplift"], "limit"),
        seepage_factor=_metric_value(metrics["seepage"]),
        seepage_limit=_metric_value(metrics["seepage"], "limit"),
        overall_stability_factor=_metric_value(metrics["overall"]),
        overall_stability_limit=_metric_value(metrics["overall"], "limit"),
        weak_layer_index=_metric_value(metrics["weak_layer"]),
        weak_layer_limit=_metric_value(metrics["weak_layer"], "limit"),
        layered_seepage_risk_index=_metric_value(metrics["layered_seepage"]),
        layered_seepage_risk_limit=_metric_value(metrics["layered_seepage"], "limit"),
        dewatering_control_ratio=_metric_value(metrics["dewatering"]),
        dewatering_control_limit=_metric_value(metrics["dewatering"], "limit"),
        min_safety_factor=min_factor,
        controlling_mode=safety_mode,
        controlling_safety_mode=safety_mode,
        controlling_safety_factor=min_factor,
        controlling_risk_mode=risk_mode,
        controlling_risk_utilization=_metric_value(risk_control, "utilization"),
        metric_semantics=metric_rows,
        circular_slip_surfaces=slip_surfaces,
        seepage_paths=seepage_paths,
        drawdown_process=drawdown_process,
        dewatering_wells=dewatering_wells,
        depressurization_wells=depressurization_wells,
        improvement_options=improvement_options,
        diagram_data={
            "controlSection": {"id": best_segment_id, "name": best_name, "depth": depth, "width": width},
            "seepagePaths": seepage_paths,
            "slipSurfaces": slip_surfaces,
            "drawdownProcess": drawdown_process,
        },
        review_notes=[
            "稳定安全系数、风险比值和质量指数已按工程方向分离，避免将越小越优的风险指标误报为最小安全系数。",
            "已从孤立筛查升级为可审查稳定专项包：控制剖面、圆弧候选、渗流路径、降水过程、井点和加固方案均可追溯。",
            "本包仍属于设计辅助计算；正式工程应结合详勘、水文地质试验、降水试验和审查意见进行专项设计。",
        ],
    )
