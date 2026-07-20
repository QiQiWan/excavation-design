from __future__ import annotations

from typing import Any

from app.schemas.domain import Project


PHASE_ORDER = [
    "basis",
    "input",
    "scheme",
    "construction",
    "calculation",
    "reinforcement",
    "deliverables",
]


def _action(
    action_id: str,
    label: str,
    description: str,
    *,
    operation: str | None = None,
    target_stage: str | None = None,
    payload: dict[str, Any] | None = None,
    automatic: bool = False,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "description": description,
        "operation": operation,
        "targetStage": target_stage,
        "payload": dict(payload or {}),
        "automatic": automatic,
    }


def _phase(
    key: str,
    title: str,
    *,
    complete: bool,
    available: bool,
    message: str,
    why: str,
    completion: str,
    action: dict[str, Any],
    owner: str,
    review_required: bool = False,
    blocking_count: int = 0,
) -> dict[str, Any]:
    status = "done" if complete else "active" if available else "pending"
    if available and review_required and not complete:
        status = "review"
    return {
        "key": key,
        "title": title,
        "status": status,
        "complete": complete,
        "available": available,
        "reviewRequired": review_required,
        "blockingCount": max(0, int(blocking_count)),
        "message": message,
        "why": why,
        "completionCriteria": completion,
        "owner": owner,
        "action": action,
    }


def _latest_intelligent_closure(project: Project) -> dict[str, Any]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if latest:
        design = dict(latest.design_iteration_summary or {})
        report = dict(latest.report_diagram_data or {})
        closure = design.get("intelligentDesignClosure") or report.get("intelligentDesignClosure")
        if isinstance(closure, dict):
            return dict(closure)
    ret = project.retaining_system
    if ret:
        closure = dict(ret.layout_summary or {}).get("intelligentDesignClosure")
        if isinstance(closure, dict):
            return dict(closure)
    return {}


def _missing_beam_designs(project: Project) -> list[str]:
    ret = project.retaining_system
    if not ret:
        return []
    beams = [*list(ret.crown_beams or []), *list(ret.wale_beams or []), *list(ret.ring_beams or [])]
    return [str(beam.code) for beam in beams if beam.design_result is None]


def build_design_workflow(
    project: Project,
    *,
    design_basis: dict[str, Any],
    construction_workspace: dict[str, Any],
    calculation_gate: dict[str, Any],
    deepening_readiness: dict[str, Any],
) -> dict[str, Any]:
    """Build one authoritative design-check-reinforcement workflow contract.

    The legacy workspace inferred completion from object existence.  That made a
    calculation with stale stage evidence look complete and made an applied
    reinforcement draft look equivalent to a closed structural design.  This
    contract separates engineering inputs, analysis feedback and formal design
    evidence, and supplies exactly one recommended action for the current gate.
    """

    basis_complete = bool(design_basis.get("confirmed"))
    excavation_complete = bool(
        project.excavation
        and project.excavation.outline
        and len(project.excavation.outline.points) >= 3
    )
    geology_complete = bool(project.strata or project.boreholes)
    input_complete = excavation_complete and geology_complete

    ret = project.retaining_system
    wall_count = len(ret.diaphragm_walls or []) if ret else 0
    support_count = len(ret.supports or []) if ret else 0
    scheme_complete = wall_count > 0 and support_count > 0

    construction_summary = dict(construction_workspace.get("summary") or {})
    construction_validation = dict(construction_workspace.get("validation") or {})
    construction_valid = bool(construction_validation.get("valid"))
    construction_saved = bool(construction_workspace.get("saved"))
    construction_case = dict(construction_workspace.get("case") or {})
    latest = project.calculation_results[-1] if project.calculation_results else None
    case_used_by_latest = bool(
        latest
        and construction_case.get("id")
        and str(latest.case_id or "") == str(construction_case.get("id") or "")
    )
    construction_complete = bool(construction_valid and (construction_saved or case_used_by_latest))

    calculation_complete = bool(calculation_gate.get("valid"))
    closure = _latest_intelligent_closure(project)
    calculation_started = latest is not None
    calculation_fail_count = int(calculation_gate.get("failCount") or 0)
    quantitative_open = int(closure.get("quantitativeOpenCount") or 0)
    structural_closed = bool(closure.get("structuralClosed", calculation_complete))

    rebar = dict(ret.rebar_design_scheme or {}) if ret else {}
    rebar_applied = bool(rebar)
    rebar_diagnostics = dict(rebar.get("diagnostics") or {})
    section_change_count = int(rebar_diagnostics.get("sectionChangeCount") or 0)
    missing_beams = _missing_beam_designs(project)
    rebar_complete = bool(
        rebar_applied
        and calculation_complete
        and section_change_count == 0
        and not missing_beams
        and deepening_readiness.get("canRunP3")
    )
    p3 = dict(project.advanced_engineering.get("p3DetailingClosure") or {})
    deliverable_complete = bool(deepening_readiness.get("canIssueConstructionDrawings"))

    phases: list[dict[str, Any]] = []
    phases.append(_phase(
        "basis", "设计基准", complete=basis_complete, available=True,
        message="规范、材料、荷载组合和安全目标已冻结" if basis_complete else "先确认规范、材料、荷载组合和安全目标",
        why="后续方案比较、分项系数和储备目标都以此为唯一基准。",
        completion="设计基准已确认，关键参数有来源且变更可追溯。",
        action=_action("open-basis", "确认设计基准", "填写并确认设计采用值。", target_stage="basis"),
        owner="设计工程师",
    ))
    phases.append(_phase(
        "input", "工程模型", complete=input_complete, available=basis_complete,
        message=(
            f"地勘与基坑几何已就绪：钻孔 {len(project.boreholes)}、地层 {len(project.strata)}"
            if input_complete else "补齐地勘数据和最终开挖几何"
        ),
        why="施工阶段自动生成和土压力计算都依赖最终开挖深度及地层。",
        completion="存在闭合基坑轮廓、顶底标高，以及钻孔或统一地层。",
        action=_action("open-input", "完善工程模型", "补录地勘、地下水和基坑轮廓。", target_stage="input"),
        owner="岩土/基坑设计工程师",
    ))
    phases.append(_phase(
        "scheme", "方案定型", complete=scheme_complete, available=input_complete,
        message=f"当前采用方案：围护墙 {wall_count}，支撑 {support_count}" if scheme_complete else "生成、比较并采用一个可施工围护方案",
        why="只有被采用的墙、围檩和支撑拓扑才能进入施工阶段与计算合同。",
        completion="至少一个候选通过拓扑硬约束，并明确采用方案。",
        action=_action(
            "generate-scheme", "生成并比较方案", "生成差异化候选，采用前不改动当前正式方案。",
            operation="support_layout_optimization", target_stage="scheme",
            payload={"preset": "balanced", "maxCandidates": 3, "searchConfig": {"requireDiverseSchemes": True}},
            automatic=True,
        ),
        owner="基坑设计工程师",
    ))
    construction_message = (
        f"{int(construction_summary.get('stageCount') or 0)} 个施工阶段已校验并用于当前计算"
        if construction_complete
        else f"系统已按最终开挖深度和支撑标高生成 {int(construction_summary.get('stageCount') or 0)} 个推荐阶段，请确认或修改"
        if construction_valid
        else "施工阶段存在错误，需按卡片提示修正"
    )
    phases.append(_phase(
        "construction", "施工路径", complete=construction_complete, available=scheme_complete,
        review_required=bool(construction_valid and not construction_complete),
        blocking_count=int(construction_validation.get("failCount") or 0),
        message=construction_message,
        why="开挖、支撑安装、换撑和拆撑决定每个构件在何时受力；它是计算输入，不是计算结果。",
        completion="阶段到达设计坑底、构件激活顺序有效；推荐阶段已采用或用户阶段已锁定。",
        action=_action(
            "review-construction", "确认施工路径", "检查系统推荐阶段；实际顺序不同时直接修改并锁定。",
            target_stage="construction",
        ),
        owner="设计工程师确认；施工组织/结构专业协同",
    ))

    if calculation_complete:
        calculation_message = (
            f"计算合同有效；自动闭环 {int(closure.get('executedIterations') or 1)} 轮，结构数值已闭合"
            if closure else "当前设计快照、施工阶段和计算结果一致，硬失败为 0"
        )
        calculation_action = _action(
            "verify-again", "重新验证当前设计", "按相同施工路径重新校核并记录差异。",
            operation="design_workflow_closure", target_stage="calculation",
            payload={"scope": "verification", "preserveScheme": True, "preserveConstructionStages": True},
            automatic=True,
        )
    elif calculation_started:
        calculation_message = (
            f"仍有 {max(calculation_fail_count, quantitative_open)} 个定量问题；系统可继续定位控制构件并补强复算"
            if calculation_fail_count or quantitative_open
            else str((calculation_gate.get("messages") or ["计算结果已过期，需要按当前设计重新计算"])[0])
        )
        calculation_action = _action(
            "close-verification", "自动分析、补强并复算", "保留采用方案和施工顺序，只强化可安全自动修改的构件设计。",
            operation="design_workflow_closure", target_stage="calculation",
            payload={"scope": "verification", "preserveScheme": True, "preserveConstructionStages": True},
            automatic=True,
        )
    else:
        calculation_message = "尚未形成当前方案的逐施工阶段内力与验算结果"
        calculation_action = _action(
            "run-verification", "启动校核与自动优化", "计算施工阶段；低于目标时自动补强并复算，达到上限后转人工方案。",
            operation="design_workflow_closure", target_stage="calculation",
            payload={"scope": "verification", "preserveScheme": True, "preserveConstructionStages": True},
            automatic=True,
        )
    phases.append(_phase(
        "calculation", "分析校核与设计迭代", complete=calculation_complete, available=construction_valid,
        blocking_count=max(calculation_fail_count, quantitative_open),
        message=calculation_message,
        why="校核结果必须反馈给设计器；未达标项不是只读结论，而是下一轮设计输入。",
        completion="施工阶段证据完整、计算合同当前、计算质量包无硬失败、定量校核闭合。",
        action=calculation_action,
        owner="系统自动迭代；超出安全自动边界时由设计工程师接管",
    ))

    if rebar_complete:
        rebar_message = "墙、冠梁、围檩和支撑均有正式设计结果与完整配筋合同"
    elif missing_beams:
        rebar_message = f"缺少 {len(missing_beams)} 根梁的正式内力/承载力/配筋记录，可自动补算后继续"
    elif section_change_count:
        rebar_message = f"配筋建议调整 {section_change_count} 个截面，必须重新计算后再定筋"
    elif not rebar_applied:
        rebar_message = "尚未把配筋草案写入正式构件"
    else:
        rebar_message = str(deepening_readiness.get("headline") or "正式配筋仍有结构入口阻断")
    phases.append(_phase(
        "reinforcement", "正式配筋与复算", complete=rebar_complete, available=calculation_complete,
        blocking_count=int(deepening_readiness.get("blockerCount") or len(missing_beams)),
        message=rebar_message,
        why="配筋不是计算后的孤立步骤；截面或刚度变化会改变内力，必须回到相同施工阶段复算。",
        completion="所有结构构件有正式内力、承载力和五类配筋；截面变化已回算且 P3 入口成立。",
        action=_action(
            "close-reinforcement", "自动完成正式设计与配筋闭环", "自动修复缺失梁证据、生成配筋、处理截面变化并复算至稳定。",
            operation="design_workflow_closure", target_stage="reinforcement",
            payload={
                "scope": "reinforcement", "rebarMode": "balanced", "preserveScheme": True,
                "preserveConstructionStages": True, "repairMissingDesignEvidence": True,
            },
            automatic=True,
        ),
        owner="系统自动闭环；构造选择由设计工程师确认",
    ))
    phases.append(_phase(
        "deliverables", "P3 深化与成果交付", complete=deliverable_complete, available=rebar_complete,
        blocking_count=int(deepening_readiness.get("blockerCount") or 0),
        message="已具备正式出图条件" if deliverable_complete else (
            f"P3 状态：{str(p3.get('status') or '尚未运行')}；结构闭合后处理节点、锚固、碰撞和审签"
        ),
        why="结构数值闭合与构造深化分开判定，避免把可在 P3 解决的碰撞误报为结构未闭合。",
        completion="P3 空间深化完成，发行门禁通过；复核项有责任人和结论。",
        action=_action(
            "run-p3", "运行 P3 构造深化", "处理节点模板、锚固、接头、逐根钢筋和碰撞。",
            operation="p3_detailing_closure", target_stage="deliverables",
            payload={"mode": str(rebar.get("mode") or "balanced"), "topNodeCount": 8},
            automatic=True,
        ),
        owner="设计工程师/详图工程师；最终由注册工程师签审",
    ))

    current = next((row for row in phases if not row["complete"]), phases[-1])
    primary_action = dict(current["action"])
    history = list(closure.get("history") or [])
    iteration_rows = [
        {
            "iteration": int(row.get("iteration") or index + 1),
            "openBefore": int(row.get("quantitativeOpenBefore") or 0),
            "hardFailBefore": int(row.get("hardFailCount") or 0),
            "actionCount": len(row.get("actions") or []),
            "changedObjectCount": int(row.get("changedObjectCount") or 0),
            "actions": list(row.get("actions") or [])[:12],
        }
        for index, row in enumerate(history)
    ]
    return {
        "version": "3.58-design-workflow-v1",
        "state": "completed" if all(row["complete"] for row in phases) else (
            "needs_review" if current.get("reviewRequired") else "ready_to_run" if current.get("available") else "blocked"
        ),
        "currentPhase": current["key"],
        "phases": phases,
        "primaryAction": primary_action,
        "automationPolicy": {
            "preserveAdoptedScheme": True,
            "preserveLockedConstructionStages": True,
            "neverRelaxLoadsOrSoilParameters": True,
            "boundedIterations": True,
            "calculationIterationLimit": int(closure.get("maximumIterations") or project.design_settings.max_intelligent_closure_iterations or 5),
            "reinforcementRecalculationLimit": 3,
        },
        "iteration": {
            "status": str(closure.get("status") or ("closed" if calculation_complete else "not_started")),
            "executed": int(closure.get("executedIterations") or 0),
            "maximum": int(closure.get("maximumIterations") or project.design_settings.max_intelligent_closure_iterations or 5),
            "structuralClosed": structural_closed,
            "calculationClosed": bool(closure.get("calculationClosed", calculation_complete)),
            "quantitativeOpenCount": quantitative_open,
            "reviewCount": int(closure.get("reviewCount") or 0),
            "history": iteration_rows,
            "interventionOptions": list(closure.get("interventionOptions") or [])[:20],
        },
        "evidence": {
            "calculationCurrent": calculation_complete,
            "constructionStageValid": construction_valid,
            "constructionStageSource": construction_summary.get("source"),
            "constructionStageLocked": bool(construction_summary.get("locked")),
            "missingBeamDesignCount": len(missing_beams),
            "missingBeamCodes": missing_beams[:50],
            "rebarApplied": rebar_applied,
            "rebarSectionChangeCount": section_change_count,
            "canRunP3": bool(deepening_readiness.get("canRunP3")),
            "canIssueConstructionDrawings": deliverable_complete,
        },
        "humanDecisionPoints": [
            "确认设计基准和工程输入来源。",
            "确认系统推荐施工阶段与实际施工组织一致；锁定阶段不会被自动覆盖。",
            "审查自动补强对造价、净空和施工性的影响，必要时选择其他策略。",
            "确认构造复核、专项设计和最终签审结论。",
        ],
    }
