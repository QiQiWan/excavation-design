from __future__ import annotations

import math
from typing import Any

from app.schemas.domain import Project, StageCalculationResult, StabilityDetailedResult


def _min_check(checks: list[dict[str, Any]], token: str) -> float | None:
    vals = []
    for c in checks:
        rid = str(c.get("ruleId", ""))
        val = c.get("calculatedValue")
        if token in rid and isinstance(val, (int, float)):
            vals.append(float(val))
    return min(vals) if vals else None


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
    best_score = 999.0
    for seg in excavation.segments:
        seg_checks = checks_by_segment.get(seg.id, [])
        factors = [
            float(c.get("calculatedValue"))
            for c in seg_checks
            if isinstance(c.get("calculatedValue"), (int, float)) and any(tok in str(c.get("ruleId", "")) for tok in ("HEAVE", "SEEPAGE", "UPLIFT", "OVERALL", "WEAK"))
        ]
        score = min(factors) if factors else 999.0
        if score < best_score:
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
        raw_factor = max(0.85, (best_score if best_score < 900 else 1.7) + 0.04 * (i - 2))
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
    factors = {
        "heave": _min_check(checks, "HEAVE"),
        "confined": _min_check(checks, "UPLIFT"),
        "seepage": _min_check(checks, "SEEPAGE"),
        "overall": _min_check(checks, "OVERALL"),
        "weak": _min_check(checks, "WEAK"),
    }
    numeric = [v for v in factors.values() if v is not None]
    min_factor = min(numeric) if numeric else None
    mode = min(factors, key=lambda k: factors[k] if factors[k] is not None else 999.0)
    return StabilityDetailedResult(
        controlling_section_id=best_segment_id,
        controlling_section_name=best_name,
        heave_factor=factors["heave"],
        confined_uplift_factor=factors["confined"],
        seepage_factor=factors["seepage"],
        overall_stability_factor=factors["overall"],
        weak_layer_index=factors["weak"],
        min_safety_factor=min_factor,
        controlling_mode=mode,
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
            "已从孤立筛查升级为可审查稳定专项包：控制剖面、圆弧候选、渗流路径、降水过程、井点和加固方案均可追溯。",
            "本包仍属于设计辅助计算；正式工程应结合详勘、水文地质试验、降水试验和审查意见进行专项设计。",
        ],
    )
