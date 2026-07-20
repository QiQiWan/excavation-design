from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.domain import Project
from app.services.calculation_state import invalidate_calculation_state
from app.services.runtime_diagnostics import append_event


GOAL_LABELS = {
    "quick_scheme": "快速方案",
    "standard_design": "标准设计",
    "formal_issue": "正式出图",
}

OBJECTIVE_LABELS = {
    "balanced": "安全与经济均衡",
    "safety_first": "安全储备优先",
    "economy_first": "经济性优先",
}


def _item(
    key: str,
    title: str,
    status: str,
    description: str,
    action: str,
    *,
    source: str,
    value: Any = None,
    impact: str = "medium",
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "description": description,
        "action": action,
        "source": source,
        "value": value,
        "impact": impact,
    }


def _macro_stage(key: str, title: str, status: str, summary: str, action: str) -> dict[str, Any]:
    return {"key": key, "title": title, "status": status, "summary": summary, "action": action}


def build_design_intake(
    project: Project,
    *,
    calculation_current: bool = False,
    detailing_ready: bool = False,
    deliverable_ready: bool = False,
) -> dict[str, Any]:
    """Return the intent-driven, progressively disclosed design contract.

    This contract deliberately separates the data needed to *start a scheme*
    from the evidence required for calculation and formal issue.  Missing
    specialist evidence therefore remains visible without falsely blocking an
    early engineering option study.
    """

    settings = project.design_settings
    excavation = project.excavation
    outline = list(excavation.outline.points or []) if excavation and excavation.outline else []
    excavation_ready = bool(
        excavation
        and excavation.outline
        and excavation.outline.closed
        and len(outline) >= 3
        and float(excavation.depth or 0.0) > 0.0
    )
    geology_source_ready = bool(project.boreholes or project.strata)
    geology_model_ready = bool(project.geological_model and project.geological_model.surfaces)
    intent_confirmed = bool(settings.design_intent_confirmed and settings.design_basis_confirmed)
    system = project.retaining_system
    scheme_ready = bool(system and system.diaphragm_walls and system.supports)

    # The quick scheme needs only a confirmed brief and excavation geometry.
    # Formal calculation intentionally remains stricter.
    can_generate_scheme = bool(intent_confirmed and excavation_ready)
    can_prepare_calculation = bool(scheme_ready and geology_source_ready)
    can_run_calculation = bool(can_prepare_calculation and geology_model_ready)

    facts = [
        _item(
            "design_intent",
            "设计目标",
            "ready" if intent_confirmed else "required",
            (
                f"当前目标为{GOAL_LABELS.get(settings.design_intent_goal, '快速方案')}，"
                f"取向为{OBJECTIVE_LABELS.get(settings.design_objective, '安全与经济均衡')}。"
                if intent_confirmed else "只需选择成果深度、周边敏感程度、设计取向和结构使用阶段。"
            ),
            "采用系统推荐值" if not intent_confirmed else "需要时修改设计任务书",
            source="设计人员确认",
            value=GOAL_LABELS.get(settings.design_intent_goal, settings.design_intent_goal),
            impact="high",
        ),
        _item(
            "excavation_geometry",
            "基坑轮廓与最终开挖深度",
            "ready" if excavation_ready else "required",
            (
                f"闭合轮廓 {len(outline)} 个点，最终开挖深度 {float(excavation.depth):.2f} m。"
                if excavation_ready else "这是生成围护墙、支撑层数和施工阶段建议的唯一工程几何前置项。"
            ),
            "编辑轮廓、坑顶标高和坑底标高" if not excavation_ready else "已具备，无需重复填写",
            source="工程输入",
            value=float(excavation.depth) if excavation_ready else None,
            impact="high",
        ),
        _item(
            "geology_source",
            "地勘与土层参数",
            "ready" if geology_source_ready else "deferred",
            (
                f"已读取钻孔 {len(project.boreholes)} 个、统一地层 {len(project.strata)} 层。"
                if geology_source_ready else "不阻断概念方案；进入内力、位移和稳定计算前必须补充。"
            ),
            "导入钻孔或统一地层数据" if not geology_source_ready else "计算前复核土层参数",
            source="勘察资料",
            impact="high",
        ),
        _item(
            "geology_model",
            "计算用地质设计域",
            "ready" if geology_model_ready else "deferred",
            (
                "地质面已覆盖设计域。" if geology_model_ready else
                "有钻孔后由系统自动建立；不要求工程师手工配置三维网格。"
            ),
            "由系统根据钻孔自动建立" if not geology_model_ready else "已具备",
            source="系统生成，勘察资料约束",
            impact="high",
        ),
        _item(
            "groundwater",
            "地下水位",
            "assumed" if not project.boreholes else "review",
            (
                f"方案阶段暂按标高 {float(settings.groundwater_level):.2f} m；"
                "正式计算应以勘察或降水设计为准。"
            ),
            "计算前核对勘察水位；无实测值时保留为显式假定",
            source="系统建议 / 勘察复核",
            value=float(settings.groundwater_level),
            impact="high",
        ),
        _item(
            "bearing_capacity",
            "立柱基础地基承载力",
            "ready" if settings.bearing_capacity_kpa is not None else "deferred",
            (
                f"已录入 {float(settings.bearing_capacity_kpa):.0f} kPa。"
                if settings.bearing_capacity_kpa is not None else
                "只在方案采用立柱且进入立柱基础验算时要求，不阻断前面的墙与支撑方案。"
            ),
            "采用立柱方案后从勘察报告补录",
            source="勘察报告 / 专项设计",
            value=settings.bearing_capacity_kpa,
            impact="medium",
        ),
    ]

    required_now = [row for row in facts if row["status"] == "required"]
    before_calculation = [row for row in facts if row["key"] in {"geology_source", "geology_model", "groundwater"}]
    before_issue = [row for row in facts if row["key"] == "bearing_capacity"]

    recommended = [
        {
            "key": "standards_and_loads",
            "title": "规范与荷载组合",
            "value": "国家标准核心体系 · 标准组合" if settings.load_combination_policy == "standard" else "国家标准核心体系 · 保守组合",
            "source": "按成果目标与周边敏感程度推荐",
            "editableAt": "专业设置",
        },
        {
            "key": "materials",
            "title": "首轮材料",
            "value": f"{settings.default_concrete_grade} · {settings.default_rebar_grade} · 保护层 {float(settings.default_cover_mm):.0f} mm",
            "source": "企业默认设计模板",
            "editableAt": "配筋深化前",
        },
        {
            "key": "analysis_model",
            "title": "分析模型",
            "value": "工程空间模型（含节点与开裂刚度）" if settings.structural_analysis_model == "engineering_spatial" else "紧凑空间模型",
            "source": "按成果目标自动选择",
            "editableAt": "计算前",
        },
        {
            "key": "construction_stages",
            "title": "施工阶段",
            "value": "按最终开挖深度和支撑标高自动生成，再由设计人员确认",
            "source": "系统生成，不要求在设计任务书中逐阶段填写",
            "editableAt": "计算前",
        },
    ]

    if not intent_confirmed:
        primary_action = {"key": "confirm_intent", "label": "采用推荐值并开始", "target": "basis", "reason": "先确认四项设计意图。"}
    elif not excavation_ready:
        primary_action = {"key": "edit_excavation", "label": "录入轮廓与开挖深度", "target": "input", "reason": "方案生成只缺工程几何。"}
    elif not scheme_ready:
        primary_action = {"key": "generate_scheme", "label": "生成 A/B/C 方案", "target": "scheme", "reason": "最小方案输入已经齐全。"}
    # Once an authoritative calculation contract already exists, do not send
    # the engineer backwards for a deferred geology-model task.  This used to
    # put “自动建立地质模型” above the reinforcement gate even though the only
    # remaining work was to complete beam design records and apply rebar.
    elif calculation_current and not detailing_ready:
        primary_action = {"key": "close_rebar_entry", "label": "自动关闭配筋入口", "target": "reinforcement", "reason": "当前施工阶段内力包络有效；系统将补齐梁设计记录并应用配筋。"}
    elif calculation_current and not deliverable_ready:
        primary_action = {"key": "complete_detailing", "label": "完成深化与校审", "target": "reinforcement", "reason": "正式成果仍需深化证据。"}
    elif calculation_current:
        primary_action = {"key": "deliver", "label": "生成成果", "target": "deliverables", "reason": "当前设计闭环已完成。"}
    elif not geology_source_ready:
        primary_action = {"key": "import_geology", "label": "补充地勘后计算", "target": "input", "reason": "方案已形成，下一步只补计算敏感资料。"}
    elif not geology_model_ready:
        primary_action = {"key": "build_geology", "label": "自动建立地质模型", "target": "input", "reason": "无需手工配置三维网格。"}
    else:
        primary_action = {"key": "run_calculation", "label": "确认施工阶段并计算", "target": "calculation", "reason": "正式计算资料已具备。"}

    quick_status = "done" if scheme_ready else "active"
    verification_status = "done" if calculation_current else "active" if scheme_ready else "pending"
    detailing_status = "done" if deliverable_ready else "active" if calculation_current else "pending"
    macro_stages = [
        _macro_stage(
            "quick_scheme", "快速方案", quick_status,
            "已形成可比选围护方案。" if scheme_ready else "确认设计意图与轮廓深度，先得到方案。",
            "查看/生成方案",
        ),
        _macro_stage(
            "verification", "计算与优化", verification_status,
            "施工阶段计算与设计反馈已闭合。" if calculation_current else "补齐高影响资料，自动生成施工阶段并校核优化。",
            "进入计算校核",
        ),
        _macro_stage(
            "detailing", "配筋与交付", detailing_status,
            "配筋与交付证据已闭合。" if deliverable_ready else "基于有效内力包络配筋、深化并输出成果。",
            "进入配筋深化",
        ),
    ]

    return {
        "schemaVersion": "1.0",
        "confirmed": intent_confirmed,
        "goal": settings.design_intent_goal,
        "goalLabel": GOAL_LABELS.get(settings.design_intent_goal, settings.design_intent_goal),
        "objective": settings.design_objective,
        "objectiveLabel": OBJECTIVE_LABELS.get(settings.design_objective, settings.design_objective),
        "facts": facts,
        "inputTiers": {
            "requiredNow": required_now,
            "systemRecommended": recommended,
            "beforeCalculation": before_calculation,
            "beforeFormalIssue": before_issue,
        },
        "readiness": {
            "excavationReady": excavation_ready,
            "geologySourceReady": geology_source_ready,
            "geologyModelReady": geology_model_ready,
            "schemeReady": scheme_ready,
            "canGenerateConceptScheme": can_generate_scheme,
            "canPrepareCalculation": can_prepare_calculation,
            "canRunCalculation": can_run_calculation,
            "calculationCurrent": bool(calculation_current),
            "detailingReady": bool(detailing_ready),
            "deliverableReady": bool(deliverable_ready),
        },
        "primaryAction": primary_action,
        "macroStages": macro_stages,
        "principle": "先形成方案，再按控制结果补资料；低影响资料不阻断前序工作。",
    }


def apply_guided_design_intake(
    project: Project,
    *,
    goal: str,
    environment_level: str,
    objective: str,
    design_stage: str,
) -> dict[str, Any]:
    if goal not in GOAL_LABELS:
        raise ValueError("成果目标无效。")
    if environment_level not in {"一般", "较高", "高"}:
        raise ValueError("周边敏感程度无效。")
    if objective not in OBJECTIVE_LABELS:
        raise ValueError("设计取向无效。")
    if design_stage not in {"temporary", "permanent_combined"}:
        raise ValueError("结构使用阶段无效。")

    settings = project.design_settings
    settings.design_intent_goal = goal
    settings.design_objective = objective
    settings.design_stage = design_stage
    settings.surrounding_environment_level = environment_level
    settings.environment_grade = environment_level
    settings.design_intent_confirmed = True
    settings.design_basis_confirmed = True
    settings.design_intent_source = "guided_recommendation"

    # Only parameters controlled by these four intent choices are set here.
    # Geological, groundwater and bearing-capacity evidence are never invented.
    if environment_level == "高":
        settings.project_grade = "一级"
        settings.excavation_safety_level = "一级"
        settings.safety_grade = "一级"
        settings.site_complexity = "复杂"
        settings.importance_factor = max(float(settings.importance_factor), 1.10)
        settings.load_combination_policy = "conservative"
        settings.stability_reserve_ratio = max(float(settings.stability_reserve_ratio), 0.15)
    elif environment_level == "较高":
        settings.project_grade = "二级"
        settings.excavation_safety_level = "二级"
        settings.safety_grade = "二级"
        settings.site_complexity = "中等"
        settings.importance_factor = max(float(settings.importance_factor), 1.00)
        settings.stability_reserve_ratio = max(float(settings.stability_reserve_ratio), 0.12)
    else:
        settings.project_grade = "二级"
        settings.excavation_safety_level = "二级"
        settings.safety_grade = "二级"
        settings.site_complexity = "中等"

    if objective == "safety_first":
        settings.load_combination_policy = "conservative"
        settings.stability_reserve_ratio = max(float(settings.stability_reserve_ratio), 0.15)
        settings.intelligent_closure_strategy = "stiffness_first"
        settings.support_target_utilization = min(float(settings.support_target_utilization), 0.78)
    elif objective == "economy_first":
        settings.intelligent_closure_strategy = "economic_zoned"
        settings.support_target_utilization = max(float(settings.support_target_utilization), 0.88)
    else:
        settings.intelligent_closure_strategy = "balanced"

    if goal == "quick_scheme":
        settings.calculation_assurance_level = "screening"
        settings.require_independent_calculation_check = False
        settings.require_formal_scenario_rerun_for_issue = False
        settings.require_formal_approval_for_construction = False
    elif goal == "standard_design":
        settings.calculation_assurance_level = "engineering"
        settings.require_independent_calculation_check = True
        settings.require_formal_scenario_rerun_for_issue = False
    else:
        settings.calculation_assurance_level = "official_issue"
        settings.require_independent_calculation_check = True
        settings.require_formal_scenario_rerun_for_issue = True
        settings.require_formal_approval_for_construction = True

    now = datetime.now(timezone.utc).isoformat()
    project.advanced_engineering = dict(project.advanced_engineering or {})
    project.advanced_engineering["designIntake"] = {
        "schemaVersion": "1.0",
        "confirmedAt": now,
        "goal": goal,
        "environmentLevel": environment_level,
        "objective": objective,
        "designStage": design_stage,
        "source": "guided_recommendation",
        "assumptionBoundary": (
            "系统推荐值可用于方案生成；地勘、地下水、施工阶段和专项资料仍按对应计算/发行阶段复核。"
        ),
    }
    if project.calculation_results or project.retaining_system:
        invalidate_calculation_state(
            project,
            reason="guided design intent changed",
            rebuild_cases=bool(project.retaining_system),
        )
    message = "最小设计任务书已确认；系统推荐参数已应用，可先生成方案，专业资料将在影响对应结论前提示补充。"
    if not project.messages or project.messages[-1] != message:
        project.messages.append(message)
    append_event(
        "design-intake",
        "guided_intake_applied",
        projectId=project.id,
        goal=goal,
        environmentLevel=environment_level,
        objective=objective,
        designStage=design_stage,
    )
    return dict(project.advanced_engineering["designIntake"])
