from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.schemas.domain import Project

# Project templates intentionally expose their provenance. Values marked
# ``project_default`` must be confirmed by the project engineer; they are not
# represented as verbatim code clauses.
DESIGN_BASIS_TEMPLATES: dict[str, dict[str, Any]] = {
    "standard_level_2": {
        "label": "二级基坑常规场地",
        "description": "常规临时支护工程的透明项目默认值，适用于启动方案设计前的确认草案。",
        "projectGrade": "二级",
        "excavationSafetyLevel": "二级",
        "siteComplexity": "中等",
        "surroundingEnvironmentLevel": "一般",
        "loadCombinationPolicy": "standard",
        "importanceFactor": 1.0,
        "stabilityReserveRatio": 0.10,
        "wallCrackedStiffnessFactor": 0.72,
        "waleCrackedStiffnessFactor": 0.75,
        "jointRotationalStiffnessFactor": 0.65,
        "initialImperfectionRatio": 0.001,
    },
    "strict_level_1": {
        "label": "一级基坑 / 高敏感环境",
        "description": "用于高重要性或周边敏感工程的保守确认草案；项目专项审查值优先。",
        "projectGrade": "一级",
        "excavationSafetyLevel": "一级",
        "siteComplexity": "复杂",
        "surroundingEnvironmentLevel": "高",
        "loadCombinationPolicy": "conservative",
        "importanceFactor": 1.10,
        "stabilityReserveRatio": 0.15,
        "wallCrackedStiffnessFactor": 0.65,
        "waleCrackedStiffnessFactor": 0.68,
        "jointRotationalStiffnessFactor": 0.55,
        "initialImperfectionRatio": 0.0015,
    },
    "simple_level_3": {
        "label": "三级基坑 / 简单场地",
        "description": "用于低复杂度临时工程的确认草案，仍需结合周边环境和地方标准复核。",
        "projectGrade": "三级",
        "excavationSafetyLevel": "三级",
        "siteComplexity": "简单",
        "surroundingEnvironmentLevel": "一般",
        "loadCombinationPolicy": "standard",
        "importanceFactor": 0.95,
        "stabilityReserveRatio": 0.05,
        "wallCrackedStiffnessFactor": 0.78,
        "waleCrackedStiffnessFactor": 0.80,
        "jointRotationalStiffnessFactor": 0.75,
        "initialImperfectionRatio": 0.0008,
    },
}

DEFAULT_ACTION_GROUPS: list[dict[str, Any]] = [
    {
        "id": "earth_pressure",
        "label": "土压力",
        "category": "permanent",
        "enabled": True,
        "source": "地层参数与开挖工况",
        "verification": "必须由压力计算模块生成",
    },
    {
        "id": "water_pressure",
        "label": "水压力",
        "category": "permanent",
        "enabled": True,
        "source": "地下水位与承压水头",
        "verification": "水土分算或合算策略必须记录",
    },
    {
        "id": "ground_surcharge",
        "label": "坑边堆载",
        "category": "variable",
        "enabled": True,
        "source": "项目输入 surcharge",
        "verification": "项目设计人员确认",
    },
    {
        "id": "vehicle_load",
        "label": "车辆荷载",
        "category": "variable",
        "enabled": False,
        "source": "施工组织或道路条件",
        "verification": "有车辆通行时启用",
    },
    {
        "id": "adjacent_structure",
        "label": "邻近建构筑物附加作用",
        "category": "variable",
        "enabled": False,
        "source": "周边调查与专项计算",
        "verification": "高敏感环境需显式确认",
    },
    {
        "id": "construction_load",
        "label": "施工临时荷载",
        "category": "variable",
        "enabled": True,
        "source": "施工阶段与吊装组织",
        "verification": "按阶段启停",
    },
    {
        "id": "temperature",
        "label": "温度作用",
        "category": "indirect",
        "enabled": True,
        "source": "支撑温差与约束系数",
        "verification": "长条形基坑和钢支撑重点复核",
    },
    {
        "id": "preload_and_installation",
        "label": "预加轴力与安装偏差",
        "category": "construction",
        "enabled": True,
        "source": "支撑安装协议",
        "verification": "纳入支撑设计包络",
    },
]

# Verification targets are software/project control values. The applicable
# standard and clause evidence remains attached to each calculation check.
SAFETY_TARGETS_BY_LEVEL: dict[str, dict[str, float]] = {
    "一级": {
        "strength": 1.15,
        "stiffness": 1.15,
        "support_stability": 1.20,
        "column_stability": 1.20,
        "embedment": 1.15,
        "base_heave": 1.15,
        "seepage": 1.15,
        "confined_uplift": 1.15,
        "overall_stability": 1.15,
    },
    "二级": {
        "strength": 1.10,
        "stiffness": 1.10,
        "support_stability": 1.15,
        "column_stability": 1.15,
        "embedment": 1.10,
        "base_heave": 1.10,
        "seepage": 1.10,
        "confined_uplift": 1.10,
        "overall_stability": 1.10,
    },
    "三级": {
        "strength": 1.05,
        "stiffness": 1.05,
        "support_stability": 1.10,
        "column_stability": 1.10,
        "embedment": 1.05,
        "base_heave": 1.05,
        "seepage": 1.05,
        "confined_uplift": 1.05,
        "overall_stability": 1.05,
    },
}


def template_catalog() -> list[dict[str, Any]]:
    return [{"id": key, **deepcopy(value)} for key, value in DESIGN_BASIS_TEMPLATES.items()]


def recommended_template_id(project: Project) -> str:
    settings = project.design_settings
    if settings.excavation_safety_level == "一级" or settings.surrounding_environment_level == "高":
        return "strict_level_1"
    if settings.excavation_safety_level == "三级" and settings.site_complexity == "简单":
        return "simple_level_3"
    return "standard_level_2"


def ensure_design_basis_defaults(project: Project) -> dict[str, Any]:
    """Populate missing V3.46 inputs without silently confirming the basis."""
    settings = project.design_settings
    changed: list[str] = []
    if not settings.design_basis_template_id or settings.design_basis_template_id not in DESIGN_BASIS_TEMPLATES:
        settings.design_basis_template_id = recommended_template_id(project)
        changed.append("designBasisTemplateId")
    if not settings.action_group_catalog:
        settings.action_group_catalog = deepcopy(DEFAULT_ACTION_GROUPS)
        changed.append("actionGroupCatalog")
    level_targets = SAFETY_TARGETS_BY_LEVEL.get(settings.excavation_safety_level, SAFETY_TARGETS_BY_LEVEL["二级"])
    if not settings.safety_factor_overrides:
        settings.safety_factor_overrides = deepcopy(level_targets)
        changed.append("safetyFactorOverrides")
    if not settings.displacement_limit_overrides_mm:
        # Explicit project defaults; the calculation check still records its
        # actual source and any project-defined limit.
        settings.displacement_limit_overrides_mm = {
            "wall_horizontal": 30.0 if settings.excavation_safety_level == "一级" else 40.0,
            "wale_deflection": 20.0,
        }
        changed.append("displacementLimitOverridesMm")
    project.advanced_engineering = dict(project.advanced_engineering or {})
    migration = dict(project.advanced_engineering.get("designBasisMigration") or {})
    migration.update({
        "version": "3.46.0",
        "changedFields": changed,
        "requiresConfirmation": not bool(settings.design_basis_confirmed),
        "templateId": settings.design_basis_template_id,
    })
    project.advanced_engineering["designBasisMigration"] = migration
    return migration


def build_action_group_contract(project: Project) -> list[dict[str, Any]]:
    ensure_design_basis_defaults(project)
    settings = project.design_settings
    result: list[dict[str, Any]] = []
    for raw in settings.action_group_catalog:
        row = dict(raw)
        action_id = str(row.get("id") or "")
        if action_id == "ground_surcharge":
            row["value"] = float(settings.surcharge)
            row["unit"] = "kPa"
        elif action_id == "temperature":
            row["value"] = float(settings.temperature_range_c)
            row["unit"] = "°C"
        elif action_id == "preload_and_installation":
            row["preloadRatio"] = float(settings.support_preload_ratio)
            row["installationDeviationMm"] = float(settings.support_installation_deviation_mm)
        result.append(row)
    return result


def safety_targets(project: Project) -> dict[str, float]:
    ensure_design_basis_defaults(project)
    base = deepcopy(SAFETY_TARGETS_BY_LEVEL.get(project.design_settings.excavation_safety_level, SAFETY_TARGETS_BY_LEVEL["二级"]))
    base.update({str(k): float(v) for k, v in project.design_settings.safety_factor_overrides.items() if v is not None})
    return base


def action_group_enabled(project: Project, action_id: str, default: bool = True) -> bool:
    ensure_design_basis_defaults(project)
    for row in project.design_settings.action_group_catalog:
        if str(row.get("id") or "") == action_id:
            return bool(row.get("enabled", default))
    return default
