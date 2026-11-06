from __future__ import annotations

from typing import Any

from app.schemas.domain import Project, StabilityDetailedResult


def _safe(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 < number < 100.0 else None


def _status(factor: float | None, target: float = 1.0) -> str:
    if factor is None:
        return "manual_review"
    if factor < 1.0:
        return "fail"
    if factor < target:
        return "warning"
    return "pass"


def build_adverse_scenario_screening(
    project: Project,
    stability: StabilityDetailedResult,
    *,
    max_displacement_mm: float | None = None,
    max_support_force_kn: float | None = None,
) -> dict[str, Any]:
    """Build bounded adverse-scenario screening from the calculated baseline.

    This service intentionally does not claim a second nonlinear solve.  It
    preserves the baseline calculation and applies transparent amplification or
    reduction factors so the project team can identify which scenarios require a
    dedicated rerun or hydrogeological analysis.
    """
    settings = project.design_settings
    if not settings.enable_adverse_scenarios:
        return {"enabled": False, "scenarios": [], "message": "项目已关闭不利情景筛查。"}
    depth = max(float(project.excavation.depth if project.excavation else 0.0), 1.0)
    reserve_target = 1.0 + float(settings.stability_reserve_ratio)
    base = {
        "heave": _safe(stability.heave_factor),
        "uplift": _safe(stability.confined_uplift_factor),
        "seepage": _safe(stability.seepage_factor),
        "overall": _safe(stability.overall_stability_factor),
    }
    rise = float(settings.dewatering_failure_rise_m)
    over = float(settings.overexcavation_depth_m)
    seepage_amp = float(settings.local_seepage_amplification)
    head_offset = float(settings.confined_head_adverse_offset_m)
    scenarios: list[dict[str, Any]] = []

    def add(
        code: str,
        label: str,
        factor_key: str,
        factor: float | None,
        displacement_amp: float,
        support_amp: float,
        assumptions: list[str],
        action: str,
    ) -> None:
        scenarios.append({
            "code": code,
            "label": label,
            "governingFamily": factor_key,
            "screenedSafetyFactor": round(factor, 4) if factor is not None else None,
            "targetSafetyFactor": round(reserve_target, 4),
            "status": _status(factor, reserve_target),
            "estimatedMaxDisplacementMm": round(float(max_displacement_mm) * displacement_amp, 3) if max_displacement_mm is not None else None,
            "estimatedMaxSupportForceKn": round(float(max_support_force_kn) * support_amp, 3) if max_support_force_kn is not None else None,
            "displacementAmplification": round(displacement_amp, 4),
            "supportForceAmplification": round(support_amp, 4),
            "assumptions": assumptions,
            "recommendedAction": action,
            "evidenceLevel": "screening_from_calculated_baseline",
        })

    water_ratio = rise / depth
    add(
        "DEWATERING_FAILURE",
        "降水失效 / 坑内水位回升",
        "seepage",
        base["seepage"] / (1.0 + 1.5 * water_ratio) if base["seepage"] is not None else None,
        1.0 + 0.8 * water_ratio,
        1.0 + 0.5 * water_ratio,
        [f"坑内水位回升 {rise:.2f}m", "按基线渗流安全系数和水头比进行透明折减"],
        "若筛查接近控制值，按回升水位重建水压力、渗流和施工阶段计算。",
    )
    over_ratio = over / depth
    add(
        "OVEREXCAVATION",
        "超挖不利工况",
        "heave",
        base["heave"] / (1.0 + 2.0 * over_ratio) if base["heave"] is not None else None,
        1.0 + 1.2 * over_ratio,
        1.0 + 0.8 * over_ratio,
        [f"超挖深度 {over:.2f}m", "被动区和坑底抗力按超挖比例折减"],
        "将超挖标高纳入施工工况，并复核坑底抗隆起、嵌固和墙体位移。",
    )
    add(
        "LOCAL_SEEPAGE",
        "局部渗流通道放大",
        "seepage",
        base["seepage"] / seepage_amp if base["seepage"] is not None else None,
        1.0 + 0.15 * (seepage_amp - 1.0),
        1.0 + 0.10 * (seepage_amp - 1.0),
        [f"局部水力梯度放大系数 {seepage_amp:.2f}", "用于识别止水帷幕缺陷和局部砂层通道风险"],
        "开展局部渗流专项分析，补充止水帷幕、井点和坑底加固设计。",
    )
    head_ratio = head_offset / depth
    add(
        "CONFINED_HEAD_RISE",
        "承压水头不利抬升",
        "uplift",
        base["uplift"] / (1.0 + 1.8 * head_ratio) if base["uplift"] is not None else None,
        1.0 + 0.25 * head_ratio,
        1.0 + 0.15 * head_ratio,
        [f"承压水头抬升 {head_offset:.2f}m", "按基线突涌安全系数与水头增量折减"],
        "按不利承压水头重算突涌稳定，并核定减压井控制水位。",
    )
    if settings.design_stage == "permanent_combined" and settings.enable_long_term_effects:
        creep = float(settings.creep_coefficient)
        shrinkage = float(settings.shrinkage_strain)
        amp = 1.0 + min(0.60, creep * float(settings.sustained_load_ratio) * 0.25 + shrinkage * 300.0)
        add(
            "LONG_TERM_SERVICEABILITY",
            "长期刚度、徐变和收缩",
            "overall",
            base["overall"],
            amp,
            1.0 + min(0.15, (amp - 1.0) * 0.25),
            [f"徐变系数 {creep:.2f}", f"收缩应变 {shrinkage:.6f}", "位移采用长期放大筛查"],
            "永久阶段应采用准永久组合、开裂刚度和长期效应进行专项复核。",
        )
    controlling = min(
        (row for row in scenarios if row.get("screenedSafetyFactor") is not None),
        key=lambda row: float(row["screenedSafetyFactor"]),
        default=None,
    )
    return {
        "enabled": True,
        "method": "transparent adverse-scenario amplification based on the current staged calculation",
        "scenarios": scenarios,
        "controllingScenario": controlling,
        "summary": {
            "count": len(scenarios),
            "failCount": sum(1 for row in scenarios if row["status"] == "fail"),
            "warningCount": sum(1 for row in scenarios if row["status"] == "warning"),
            "manualReviewCount": sum(1 for row in scenarios if row["status"] == "manual_review"),
            "minimumScreenedSafetyFactor": controlling.get("screenedSafetyFactor") if controlling else None,
        },
        "boundary": "筛查结果用于识别专项复算需求；正式设计需按不利水位、超挖和渗流工况重新组装作用并计算。",
    }
