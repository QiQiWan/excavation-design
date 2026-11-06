from __future__ import annotations

import gc
import hashlib
import json
from typing import Any, Callable

from app.schemas.domain import Project


ScenarioProgress = Callable[[int, str], None]


SCENARIO_CATALOG: list[dict[str, Any]] = [
    {
        "code": "DEWATERING_FAILURE",
        "label": "降水失效 / 坑内水位回升",
        "family": "groundwater",
        "formalMethod": "重建坑内水位并重新执行施工阶段计算",
    },
    {
        "code": "OVEREXCAVATION",
        "label": "超挖不利工况",
        "family": "geometry",
        "formalMethod": "修改最终开挖标高并重新生成施工工况",
    },
    {
        "code": "LOCAL_SEEPAGE",
        "label": "局部渗流通道放大",
        "family": "hydrogeology",
        "formalMethod": "放大高渗透层参数并重新计算渗流与稳定指标",
    },
    {
        "code": "CONFINED_HEAD_RISE",
        "label": "承压水头不利抬升",
        "family": "hydrogeology",
        "formalMethod": "抬升承压水头并重新执行突涌和整体计算",
    },
    {
        "code": "PRELOAD_TEMPERATURE_DEVIATION",
        "label": "预加轴力、温度与安装偏差组合",
        "family": "construction",
        "formalMethod": "调整支撑预加轴力、温差和初始偏差后重新计算",
    },
    {
        "code": "LONG_TERM_SERVICEABILITY",
        "label": "长期刚度、徐变和收缩",
        "family": "serviceability",
        "formalMethod": "启用长期效应参数并重新计算使用阶段",
    },
]


def scenario_catalog(project: Project | None = None) -> list[dict[str, Any]]:
    enabled = set(project.design_settings.formal_adverse_scenario_codes if project else [])
    rows = []
    for item in SCENARIO_CATALOG:
        row = dict(item)
        row["selected"] = not enabled or row["code"] in enabled
        if project and row["code"] == "LONG_TERM_SERVICEABILITY":
            row["applicable"] = project.design_settings.design_stage == "permanent_combined"
        else:
            row["applicable"] = True
        rows.append(row)
    return rows


def _scenario_hash(project: Project, code: str, assumptions: dict[str, Any]) -> str:
    payload = {
        "projectId": project.id,
        "code": code,
        "assumptions": assumptions,
        "designSettings": project.design_settings.model_dump(mode="json", by_alias=True),
        "excavation": project.excavation.model_dump(mode="json", by_alias=True) if project.excavation else None,
        "supportTopology": [
            [row.id, row.start.x, row.start.y, row.end.x, row.end.y, row.elevation]
            for row in (project.retaining_system.supports if project.retaining_system else [])
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _multiply_permeability(project: Project, factor: float) -> int:
    count = 0
    for stratum in project.strata:
        params = stratum.parameters
        for name in ("permeability_x", "permeability_y", "permeability_z"):
            value = getattr(params, name)
            if value is not None and value > 0:
                setattr(params, name, float(value) * factor)
                count += 1
    if project.excavation:
        for segment in project.excavation.segments:
            section = segment.representative_section
            if not section:
                continue
            for layer in section.layers:
                params = layer.parameters
                for name in ("permeability_x", "permeability_y", "permeability_z"):
                    value = getattr(params, name)
                    if value is not None and value > 0:
                        setattr(params, name, float(value) * factor)
                        count += 1
    return count


def apply_formal_scenario(project: Project, code: str) -> dict[str, Any]:
    settings = project.design_settings
    excavation = project.excavation
    assumptions: dict[str, Any] = {}
    if code == "DEWATERING_FAILURE":
        rise = float(settings.dewatering_failure_rise_m)
        outside = float(settings.groundwater_level)
        base_inside = settings.groundwater_level_inside
        if base_inside is None:
            base_inside = min(outside, float(excavation.bottom_elevation if excavation else outside - 1.0) - 0.5)
        settings.groundwater_level_inside = min(outside, float(base_inside) + rise)
        assumptions = {
            "groundwaterLevelInsideBefore": base_inside,
            "groundwaterLevelInsideAfter": settings.groundwater_level_inside,
            "riseM": rise,
        }
    elif code == "OVEREXCAVATION":
        if excavation is None:
            raise ValueError("超挖工况需要基坑轮廓和开挖标高。")
        over = float(settings.overexcavation_depth_m)
        before = float(excavation.bottom_elevation)
        excavation.bottom_elevation = before - over
        excavation.depth = float(excavation.depth) + over
        assumptions = {"bottomElevationBefore": before, "bottomElevationAfter": excavation.bottom_elevation, "overexcavationM": over}
    elif code == "LOCAL_SEEPAGE":
        factor = float(settings.local_seepage_amplification)
        changed = _multiply_permeability(project, factor)
        assumptions = {"permeabilityAmplification": factor, "modifiedParameterCount": changed}
    elif code == "CONFINED_HEAD_RISE":
        offset = float(settings.confined_head_adverse_offset_m)
        base = settings.confined_water_head_elevation
        if base is None:
            base = float(settings.groundwater_level)
        settings.confined_water_head_elevation = float(base) + offset
        assumptions = {"confinedHeadBefore": base, "confinedHeadAfter": settings.confined_water_head_elevation, "offsetM": offset}
    elif code == "PRELOAD_TEMPERATURE_DEVIATION":
        ratio_before = float(settings.support_preload_ratio)
        temp_before = float(settings.temperature_range_c)
        deviation_before = float(settings.support_installation_deviation_mm)
        settings.support_preload_ratio = min(0.60, max(ratio_before, ratio_before * 1.20 + 0.02))
        settings.temperature_range_c = max(temp_before, temp_before + 10.0)
        settings.support_installation_deviation_mm = min(100.0, max(deviation_before, deviation_before + 10.0))
        if project.retaining_system:
            for support in project.retaining_system.supports:
                support.preload_ratio = settings.support_preload_ratio
                support.temperature_delta_c = settings.temperature_range_c
        assumptions = {
            "preloadRatioBefore": ratio_before,
            "preloadRatioAfter": settings.support_preload_ratio,
            "temperatureRangeBeforeC": temp_before,
            "temperatureRangeAfterC": settings.temperature_range_c,
            "installationDeviationBeforeMm": deviation_before,
            "installationDeviationAfterMm": settings.support_installation_deviation_mm,
        }
    elif code == "LONG_TERM_SERVICEABILITY":
        if settings.design_stage != "permanent_combined":
            raise ValueError("长期效应正式复算只适用于支护兼作永久结构。")
        settings.enable_long_term_effects = True
        settings.creep_coefficient = max(float(settings.creep_coefficient), 1.8)
        settings.sustained_load_ratio = max(float(settings.sustained_load_ratio), 0.70)
        assumptions = {
            "creepCoefficient": settings.creep_coefficient,
            "shrinkageStrain": settings.shrinkage_strain,
            "sustainedLoadRatio": settings.sustained_load_ratio,
        }
    else:
        raise ValueError(f"Unsupported adverse scenario: {code}")
    assumptions["scenarioInputHash"] = _scenario_hash(project, code, assumptions)
    return assumptions




def _bounded_scenario_seed(project: Project) -> Project:
    """Create a calculation-only project seed without historical candidate payloads.

    Formal adverse scenarios run sequentially.  Removing historical candidates,
    prior results, report diagrams and rebar-detailing objects prevents each
    scenario clone from multiplying a large project snapshot in memory.
    """
    seed = project.model_copy(deep=True)
    seed.calculation_cases = []
    seed.calculation_results = []
    if seed.retaining_system is not None:
        repair = seed.retaining_system.support_layout_repair
        if repair is not None:
            repair.candidates = []
            repair.candidate_full_calculations = []
        seed.retaining_system.layout_summary = {
            key: value
            for key, value in dict(seed.retaining_system.layout_summary or {}).items()
            if key not in {
                "candidateSchemes", "candidateFullCalculationComparison",
                "supportOptimizationCandidates", "autoRepair",
            }
        }
        seed.retaining_system.rebar_design_scheme = None
    keep_advanced = {}
    for key in (
        "calculationState", "designBasisMigration", "requiresRecalculation",
        "detailGeometryPatches", "detailingOverrides",
    ):
        if key in (seed.advanced_engineering or {}):
            keep_advanced[key] = seed.advanced_engineering[key]
    seed.advanced_engineering = keep_advanced
    return seed

def _max_support_force(result: Any) -> float | None:
    values: list[float] = []
    for stage in result.stage_results or []:
        for force in stage.support_forces or []:
            value = getattr(force, "design_force", None)
            if value is None:
                value = getattr(force, "axial_force", None)
            if value is not None:
                values.append(abs(float(value)))
    return max(values) if values else None


def _compact_result(code: str, result: Any, assumptions: dict[str, Any]) -> dict[str, Any]:
    stability = result.stability_detailed_result
    governing = result.governing_values
    factors = {
        "embedment": getattr(governing, "embedment_safety_factor_min", None),
        "heave": getattr(stability, "heave_factor", None) if stability else None,
        "seepage": getattr(stability, "seepage_factor", None) if stability else None,
        "confinedUplift": getattr(stability, "confined_uplift_factor", None) if stability else None,
        "overall": getattr(stability, "overall_stability_factor", None) if stability else None,
    }
    numeric = [float(v) for v in factors.values() if isinstance(v, (int, float)) and 0 < float(v) < 100]
    return {
        "scenarioCode": code,
        "scenarioLabel": next((row["label"] for row in SCENARIO_CATALOG if row["code"] == code), code),
        "status": "fail" if int((result.check_summary or {}).get("fail", 0)) else "warning" if int((result.check_summary or {}).get("warning", 0)) else "pass",
        "calculationResultId": result.id,
        "caseId": result.case_id,
        "inputHash": assumptions.get("scenarioInputHash"),
        "assumptions": assumptions,
        "maxWallDisplacementMm": getattr(governing, "max_displacement", None),
        "maxSupportForceKn": _max_support_force(result),
        "maxWallMomentKnM": getattr(governing, "max_wall_moment", None),
        "maxWallShearKn": getattr(governing, "max_wall_shear", None),
        "minimumSafetyFactor": min(numeric) if numeric else None,
        "safetyFactors": factors,
        "checkSummary": dict(result.check_summary or {}),
        "calculatedAt": result.calculated_at,
        "evidenceLevel": "formal_staged_rerun",
    }


def run_formal_adverse_scenario_suite(
    project: Project,
    codes: list[str] | None = None,
    *,
    progress: ScenarioProgress | None = None,
) -> dict[str, Any]:
    from app.calculation.engine import build_default_construction_cases, run_calculation

    requested = list(codes or project.design_settings.formal_adverse_scenario_codes)
    allowed = {row["code"] for row in SCENARIO_CATALOG}
    requested = [code for code in requested if code in allowed]
    if not requested:
        raise ValueError("未选择任何正式不利工况。")
    summaries: list[dict[str, Any]] = []
    full_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seed = _bounded_scenario_seed(project)
    total = len(requested)
    for index, code in enumerate(requested, start=1):
        if progress:
            progress(int(8 + (index - 1) / max(total, 1) * 76), f"正式复算不利工况 {index}/{total}：{code}")
        trial = seed.model_copy(deep=True)
        try:
            assumptions = apply_formal_scenario(trial, code)
            trial.calculation_cases = build_default_construction_cases(trial)
            case = trial.calculation_cases[-1] if trial.calculation_cases else None
            result = run_calculation(trial, case, auto_repair=False, include_candidate_comparison=False)
            summaries.append(_compact_result(code, result, assumptions))
            full_results.append({
                "scenarioCode": code,
                "assumptions": assumptions,
                "calculationResult": result.model_dump(mode="json", by_alias=True),
            })
        except Exception as exc:
            errors.append({
                "scenarioCode": code,
                "scenarioLabel": next((row["label"] for row in SCENARIO_CATALOG if row["code"] == code), code),
                "status": "fail",
                "error": str(exc),
                "evidenceLevel": "formal_staged_rerun_failed",
            })
        finally:
            del trial
            gc.collect()
    del seed
    gc.collect()
    controlling = min(
        (row for row in summaries if isinstance(row.get("minimumSafetyFactor"), (int, float))),
        key=lambda row: float(row["minimumSafetyFactor"]),
        default=None,
    )
    return {
        "method": "independent staged rerun for each adverse scenario",
        "requestedCodes": requested,
        "summaries": summaries,
        "errors": errors,
        "fullResults": full_results,
        "summary": {
            "scenarioCount": len(summaries),
            "failedExecutionCount": len(errors),
            "failCount": sum(1 for row in summaries if row.get("status") == "fail"),
            "warningCount": sum(1 for row in summaries if row.get("status") == "warning"),
            "minimumSafetyFactor": controlling.get("minimumSafetyFactor") if controlling else None,
            "controllingScenarioCode": controlling.get("scenarioCode") if controlling else None,
        },
        "boundary": "每个场景均重新生成施工工况并运行当前计算内核；复杂三维渗流、非线性土体和专项降水模型仍需外部专业软件复核。",
    }
