from __future__ import annotations

from typing import Any

from app.schemas.domain import Project
from app.rules.gb50009.load_combinations import combination_record
from app.services.runtime_diagnostics import append_event
from app.services.engineering_templates import (
    build_action_group_contract, ensure_design_basis_defaults, safety_targets, template_catalog,
)
from app.services.enterprise_library import list_enterprise_libraries, resolve_enterprise_library, validate_enterprise_library


def _stage_label(value: str) -> str:
    return {
        "temporary": "临时支护阶段",
        "permanent_combined": "支护兼作永久结构",
    }.get(value, value)


def _profile_label(value: str) -> str:
    return {
        "national_core": "国家标准核心体系",
        "national_plus_local": "国家标准 + 地方标准",
        "custom_review": "项目专项审查体系",
    }.get(value, value)


def _policy_label(value: str) -> str:
    return {
        "standard": "标准组合",
        "conservative": "保守组合",
        "custom": "项目自定义组合",
    }.get(value, value)


def build_design_basis(project: Project) -> dict[str, Any]:
    """Build a compact, auditable design-basis contract for UI and reports.

    The record intentionally distinguishes project-confirmed values from software
    defaults.  It does not claim that a generic coefficient replaces project
    classification or professional review.
    """
    ensure_design_basis_defaults(project)
    settings = project.design_settings
    gamma_g = float(settings.load_gamma_g)
    gamma_q = float(settings.load_gamma_q)
    psi = float(settings.load_psi)
    if settings.load_combination_policy == "conservative":
        gamma_g = max(gamma_g, 1.35)
        gamma_q = max(gamma_q, 1.40)
    permanent_sample = 1.0
    variable_sample = 1.0
    uls = combination_record(permanent_sample, variable_sample, gamma_g=gamma_g, gamma_q=gamma_q, psi=psi)
    uls["id"] = "ULS-BASIC"
    uls["name"] = "承载能力极限状态基本组合"
    uls["expression"] = "Sd = γG·Gk + γQ·ψ·Qk"
    uls["coefficientOnly"] = True
    sls = {
        "id": "SLS-STANDARD",
        "name": "正常使用极限状态标准组合",
        "gammaG": 1.0,
        "gammaQ": 1.0,
        "psi": 1.0,
        "expression": "S = Gk + Qk",
        "coefficientOnly": True,
        "note": "用于位移、裂缝和施工阶段使用性能复核；具体作用项目按工况启用。",
    }
    quasi = {
        "id": "SLS-QUASI",
        "name": "正常使用极限状态准永久组合",
        "gammaG": 1.0,
        "gammaQ": 1.0,
        "psi": psi,
        "expression": "S = Gk + ψ·Qk",
        "coefficientOnly": True,
        "note": "仅在长期效应或永久使用阶段适用时启用。",
    }
    standards = [
        {"code": "GB 55003-2021", "role": "地基基础强制性底线与支护安全"},
        {"code": "JGJ 120-2012", "role": "基坑支护体系、变形与稳定性验算"},
        {"code": "GB 50009-2012", "role": "作用分类和荷载组合"},
        {"code": "GB 50007-2011", "role": "地基承载力与基础校核"},
        {"code": "GB 55008-2021", "role": "混凝土结构强制性要求"},
        {"code": "GB/T 50010-2010（2024年局部修订）", "role": "混凝土抗弯、抗剪、配筋与构造"},
    ]
    parameters = [
        {"group": "工程分级", "name": "项目工程等级", "value": settings.project_grade, "source": "项目确认"},
        {"group": "工程分级", "name": "基坑安全等级", "value": settings.excavation_safety_level, "source": "项目确认 / JGJ 120"},
        {"group": "场地条件", "name": "场地复杂程度", "value": settings.site_complexity, "source": "勘察与项目确认"},
        {"group": "场地条件", "name": "周边环境等级", "value": settings.surrounding_environment_level, "source": "项目确认"},
        {"group": "设计条件", "name": "设计阶段", "value": _stage_label(settings.design_stage), "source": "项目确认"},
        {"group": "荷载组合", "name": "组合策略", "value": _policy_label(settings.load_combination_policy), "source": "GB 50009 / 项目确认"},
        {"group": "荷载组合", "name": "永久作用分项系数 γG", "value": gamma_g, "source": "项目设计值"},
        {"group": "荷载组合", "name": "可变作用分项系数 γQ", "value": gamma_q, "source": "项目设计值"},
        {"group": "荷载组合", "name": "组合值系数 ψ", "value": psi, "source": "项目设计值"},
        {"group": "安全储备", "name": "重要性系数", "value": settings.importance_factor, "source": "项目设计值"},
        {"group": "安全储备", "name": "安全系数附加储备", "value": f"{settings.stability_reserve_ratio * 100:.0f}%", "source": "项目控制值"},
        {"group": "地基", "name": "地基承载力特征值", "value": settings.bearing_capacity_kpa, "unit": "kPa", "source": "勘察报告 / GB 50007"},
        {"group": "混凝土", "name": "默认混凝土等级", "value": settings.default_concrete_grade, "source": "项目设计值"},
        {"group": "混凝土", "name": "默认钢筋等级", "value": settings.default_rebar_grade, "source": "项目设计值"},
        {"group": "混凝土", "name": "默认保护层", "value": settings.default_cover_mm, "unit": "mm", "source": "项目设计值 / GB/T 50010"},
        {"group": "混凝土", "name": "抗弯设计路径", "value": settings.flexure_design_method, "source": "GB/T 50010"},
        {"group": "混凝土", "name": "抗剪设计路径", "value": settings.shear_design_method, "source": "GB/T 50010"},
    ]
    action_groups = build_action_group_contract(project)
    targets = safety_targets(project)
    enterprise = resolve_enterprise_library(project)
    enterprise_validation = validate_enterprise_library(project)
    enterprise_standard = enterprise.get("standardTemplate") or {}
    enterprise_targets = dict(enterprise_standard.get("safetyTargets") or {}) if isinstance(enterprise_standard, dict) else {}
    if enterprise_targets:
        for key, value in enterprise_targets.items():
            if value is None:
                continue
            normalized_key = "base_heave" if str(key) == "heave" else str(key)
            targets[normalized_key] = max(float(targets.get(normalized_key, 1.0)), float(value))
    blockers: list[str] = []
    if not settings.design_basis_confirmed:
        blockers.append("设计基准尚未由项目设计人员确认")
    if settings.bearing_capacity_kpa is None:
        blockers.append("地基承载力特征值尚未录入；涉及立柱基础时必须补充")
    if enterprise_validation.get("status") == "fail":
        blockers.append("企业工程资源库不可用")
    enterprise_codes = list(enterprise_standard.get("standards") or []) if isinstance(enterprise_standard, dict) else []
    known_codes = {str(item.get("code")) for item in standards}
    for code in enterprise_codes:
        if str(code) not in known_codes:
            standards.append({"code": str(code), "role": "企业/地方标准模板补充，项目校审确认"})
    append_event(
        "design-migration",
        "design_basis_built",
        projectId=project.id,
        confirmed=bool(settings.design_basis_confirmed),
        templateId=settings.design_basis_template_id,
        actionGroupCount=len(action_groups),
        safetyTargetCount=len(targets),
        structuralAnalysisModel=settings.structural_analysis_model,
    )
    return {
        "confirmed": bool(settings.design_basis_confirmed),
        "projectGrade": settings.project_grade,
        "excavationSafetyLevel": settings.excavation_safety_level,
        "siteComplexity": settings.site_complexity,
        "surroundingEnvironmentLevel": settings.surrounding_environment_level,
        "designStage": settings.design_stage,
        "designStageLabel": _stage_label(settings.design_stage),
        "standardProfile": settings.standard_profile,
        "standardProfileLabel": _profile_label(settings.standard_profile),
        "loadCombinationPolicy": settings.load_combination_policy,
        "loadCombinationPolicyLabel": _policy_label(settings.load_combination_policy),
        "loadCombinations": [uls, sls, quasi],
        "actionGroups": action_groups,
        "templateCatalog": template_catalog(),
        "selectedTemplateId": settings.design_basis_template_id,
        "safetyTargets": targets,
        "enterprise": {
            "libraries": list_enterprise_libraries(),
            "selection": enterprise.get("selection"),
            "standardTemplate": enterprise_standard,
            "standardTemplates": list((enterprise.get("library") or {}).get("standardTemplates") or []),
            "nodeTemplateCount": len(enterprise.get("nodeTemplates") or []),
            "rebarCombinationCount": len(enterprise.get("rebarCombinations") or []),
            "validation": enterprise_validation,
            "boundary": enterprise.get("boundary"),
        },
        "analysisModel": {
            "model": settings.structural_analysis_model,
            "wallCrackedStiffnessFactor": settings.wall_cracked_stiffness_factor,
            "waleCrackedStiffnessFactor": settings.wale_cracked_stiffness_factor,
            "jointTranslationalStiffnessFactor": settings.joint_translational_stiffness_factor,
            "jointRotationalStiffnessFactor": settings.joint_rotational_stiffness_factor,
            "rigidZoneLengthFactor": settings.rigid_zone_length_factor,
            "initialImperfectionRatio": settings.initial_imperfection_ratio,
            "longTermEffectsEnabled": settings.enable_long_term_effects,
            "adverseScenariosEnabled": settings.enable_adverse_scenarios,
        },
        "parameters": parameters,
        "standards": standards,
        "blockers": blockers,
        "summary": {
            "gammaG": gamma_g,
            "gammaQ": gamma_q,
            "psi": psi,
            "importanceFactor": settings.importance_factor,
            "stabilityReserveRatio": settings.stability_reserve_ratio,
            "concreteGrade": settings.default_concrete_grade,
            "rebarGrade": settings.default_rebar_grade,
            "coverMm": settings.default_cover_mm,
            "enterpriseLibraryId": settings.enterprise_library_id,
            "localStandardTemplateId": settings.local_standard_template_id,
        },
    }
