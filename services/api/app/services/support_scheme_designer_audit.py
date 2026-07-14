from __future__ import annotations

from typing import Any

from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Project
from app.services.calculation_resource_estimator import estimate_calculation_resources
from app.services.plan_shape_intelligence import classify_excavation_plan


def audit_support_scheme_designer(project: Project) -> dict[str, Any]:
    """Audit the complete scheme-designer decision chain.

    The audit is intentionally stricter than a visual layout score.  It checks
    whether the designer has enough information to recognise the plan, select a
    compatible structural system, generate genuinely different candidates,
    close the load path, coordinate construction stages, remain inside the
    worker resource budget and produce a current calculation/issue baseline.
    """
    if not project.excavation:
        return {
            "status": "blocked",
            "score": 0,
            "blockingItems": ["缺少闭合基坑轮廓。"],
            "warningItems": [],
            "sections": [],
            "workflow": ["几何输入", "形状识别", "体系选型", "候选生成", "完整计算", "受控交付"],
        }

    shape = classify_excavation_plan(
        list(project.excavation.outline.points),
        local_pit_count=len(project.excavation.local_pits or []),
        has_center_island=any(
            getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True)
            for item in project.excavation.obstacles or []
        ),
    )
    retaining = project.retaining_system
    quality = evaluate_support_layout_quality(project) if retaining else None
    metrics = dict(quality.metrics or {}) if quality else {}
    repair = retaining.support_layout_repair if retaining else None
    candidates = list(repair.candidates or []) if repair else []
    candidate_families = {
        str((candidate.variable_summary or {}).get("topologyFamily") or "unknown")
        for candidate in candidates
    }
    candidate_fingerprints = {
        str((candidate.variable_summary or {}).get("geometryFingerprint") or candidate.id)
        for candidate in candidates
    }
    resource = estimate_calculation_resources(project, candidate_count=min(3, len(candidates)))
    supports = list(retaining.supports or []) if retaining else []
    walls = list(retaining.diaphragm_walls or []) if retaining else []
    topology_families = {str(getattr(item, "topology_family", "") or "unknown") for item in supports}
    load_path_classes = {str(getattr(item, "load_path_class", "") or "unknown") for item in supports}
    locked_support_count = sum(1 for item in supports if bool(getattr(item, "optimization_locked", False)))
    hard_feasible_candidates = sum(1 for item in candidates if bool((item.hard_constraints or {}).get("passed")))
    recognition_confidence = float(shape.get("recognitionConfidence") or shape.get("confidence") or 0.0)
    layout_summary = dict(retaining.layout_summary or {}) if retaining else {}

    blocking: list[str] = []
    warnings: list[str] = []

    # Input and recognition confidence.
    outline_points = list(project.excavation.outline.points or [])
    if len(outline_points) < 3 or not project.excavation.outline.closed:
        blocking.append("基坑轮廓未形成有效闭合多边形。")
    if not project.boreholes or not project.strata:
        warnings.append("钻孔或地层资料不完整，当前支撑方案只能作为筛查级设计。")
    coverage = dict((project.geological_model.coverage_audit if project.geological_model else {}) or {})
    if coverage and coverage.get("designDomainCovered") is False:
        blocking.append("地质模型没有覆盖围护结构与施工影响设计域。")
    if recognition_confidence and recognition_confidence < 0.55:
        warnings.append("轮廓原型识别置信度偏低，应由设计人员确认分区、主轴和体系类型。")
    if shape.get("ambiguousAlternatives"):
        warnings.append("轮廓接近多个原型分类阈值，应对备选形状原型分别生成并比较支撑体系。")

    # Structural-system and load-path closure.
    if not retaining or not supports:
        blocking.append("尚未形成水平支撑体系。")
    if retaining and repair and candidates and hard_feasible_candidates == 0:
        blocking.append("当前候选集没有通过全部拓扑硬约束的方案。")
    if int(metrics.get("supportCrossingCount", 0) or 0) > 0:
        blocking.append("存在同层非法穿越。")
    if int(metrics.get("supportToSupportTerminalCount", 0) or 0) > 0:
        blocking.append("存在支撑终止于另一根轴压支撑的无效传力路径。")
    if int(metrics.get("unsupportedInternalEndpointCount", 0) or 0) > 0:
        blocking.append("存在无围护墙、围檩或环梁支承的内部端点。")
    if int(metrics.get("waleSupportBayFailCount", 0) or 0) > 0:
        blocking.append("围檩支点间距仍有超限区段。")
    shape_scheme = dict(shape.get("engineeringScheme") or {})
    transfer_required = bool(shape_scheme.get("transferSystemRequired") or "junction_transfer_required" in (shape.get("riskFlags") or []))
    transfer_complete = bool(layout_summary.get("shapeTransferSystemComplete"))
    if transfer_required and not transfer_complete:
        blocking.append("异形轮廓的凹角/交汇区缺少明确转接环、分隔墙、中心岛或平面框架。")
    frame_model_complete = bool(layout_summary.get("explicitFrameModelComplete"))
    if "supported_frame_node" in load_path_classes and not frame_model_complete:
        blocking.append("存在框架节点支撑，但尚未完成主撑平面内弯剪、节点刚度和立柱水平反力模型。")

    # Wall design variables and construction panelisation.
    wall_design_length_count = sum(1 for wall in walls if getattr(wall, "design_length", None) is not None)
    construction_panel_count = sum(len(getattr(wall, "construction_panels", None) or []) for wall in walls)
    wall_length_state = dict(layout_summary.get("wallLengthOptimization") or {})
    wall_recompute_required = bool(layout_summary.get("wallLengthOptimizationRecomputeRequired"))
    if walls and wall_design_length_count < len(walls):
        warnings.append("部分计算墙尚未显式记录设计控制段长度，墙长变量未完全进入方案台账。")
    if walls and construction_panel_count == 0:
        warnings.append("围护墙尚未完成施工槽段分幅，IFC、钢筋笼和施工图无法形成一一映射。")
    if wall_recompute_required:
        blocking.append("围护墙设计长度或墙趾已调整，但当前计算结果尚未复算。")
    if walls and not wall_length_state:
        warnings.append("尚未形成围护墙设计长度/施工分幅/局部加强的联合优化记录。")

    # Candidate diversity and human locks.
    if len(candidates) >= 2 and len(candidate_fingerprints) < 2:
        warnings.append("候选方案几何同质化，A/B/C没有形成实质性差异。")
    if len(candidates) >= 3 and len(candidate_families) < 2 and str(shape.get("archetype")) not in {"slender_rectangle", "rectangle"}:
        warnings.append("复杂平面候选拓扑族不足，方案比选仍偏向单一生成器。")
    if locked_support_count and candidates and len(candidate_fingerprints) < 2:
        warnings.append("人工锁定可能限制了候选多样性；应确认锁定对象仍符合当前轮廓和施工通道。")

    # Constructability and stage consistency.
    obstacle_count = len(project.excavation.obstacles or [])
    support_node_count = len(retaining.support_nodes or []) if retaining else 0
    column_count = len(retaining.columns or []) if retaining else 0
    if obstacle_count == 0:
        warnings.append("未录入出土口、坡道、地下室柱网或保护区，施工性评分置信度较低。")
    if supports and support_node_count == 0:
        warnings.append("支撑已生成但节点对象尚未深化，节点板、预埋件和围檩局部承压无法校核。")
    if supports and column_count == 0 and any(float(getattr(item, "clear_span", 0.0) or 0.0) > 18.0 for item in supports):
        warnings.append("存在较长水平支撑但未形成临时立柱/稳定体系，应复核平面外稳定和安装阶段。")
    if not project.calculation_cases:
        warnings.append("支撑方案尚未与施工阶段激活、换撑和拆撑序列核对。")
    elif retaining:
        active_ids = {str(item.id) for item in supports}
        referenced_ids: set[str] = set()
        for case in project.calculation_cases:
            for stage in case.stages or []:
                referenced_ids.update(str(value) for value in (stage.active_support_ids or []))
                referenced_ids.update(str(value) for value in (stage.deactivated_support_ids or []))
        missing_stage_references = sorted(referenced_ids - active_ids)
        if missing_stage_references:
            blocking.append(f"施工阶段引用了 {len(missing_stage_references)} 个当前支撑拓扑中不存在的构件。")

    # Runtime, calculation and issue baseline.
    if resource["status"] == "blocked":
        blocking.append("项目规模超过当前单worker计算安全预算。")
    elif resource["status"] in {"high", "elevated"}:
        warnings.append("计算资源风险较高，应采用安全模式和逐方案计算。")
    calculation_state = dict((project.advanced_engineering or {}).get("calculationState") or {})
    latest = project.calculation_results[-1] if project.calculation_results else None
    if calculation_state.get("requiresRecalculation"):
        blocking.append("当前结构或拓扑已变化，已有计算合同失效。")
    elif not latest:
        warnings.append("当前方案尚未完成独立worker完整计算。")
    formal_gate = latest.formal_report_gate if latest else None
    if latest and formal_gate and formal_gate.status == "fail":
        blocking.append("最新计算结果未通过正式成果发行闸门。")
    elif latest and (not formal_gate or not formal_gate.allowed_for_official_issue):
        warnings.append("最新计算尚未达到正式成果发行条件。")

    sections = [
        {"id": "input", "name": "输入与设计域", "status": "fail" if len(outline_points) < 3 or (coverage and coverage.get("designDomainCovered") is False) else "warning" if not project.boreholes or not project.strata else "pass", "evidence": {"outlinePointCount": len(outline_points), "closed": bool(project.excavation.outline.closed), "boreholeCount": len(project.boreholes), "stratumCount": len(project.strata), "coverage": coverage}},
        {"id": "shape", "name": "形状识别", "status": "warning" if recognition_confidence and recognition_confidence < 0.55 else "pass", "evidence": shape},
        {"id": "system", "name": "体系选型与转接", "status": "fail" if transfer_required and not transfer_complete else "pass", "evidence": shape_scheme},
        {"id": "topology", "name": "传力与拓扑", "status": quality.status if quality else "fail", "evidence": metrics},
        {"id": "wallVariables", "name": "围护墙设计变量", "status": "fail" if wall_recompute_required else "warning" if walls and (wall_design_length_count < len(walls) or construction_panel_count == 0) else "pass", "evidence": {"wallCount": len(walls), "designLengthCount": wall_design_length_count, "constructionPanelCount": construction_panel_count, "optimization": wall_length_state, "recomputeRequired": wall_recompute_required}},
        {"id": "candidates", "name": "候选多样性", "status": "warning" if candidates and (len(candidate_fingerprints) < 2 or hard_feasible_candidates < min(2, len(candidates))) else "pass", "evidence": {"candidateCount": len(candidates), "hardFeasibleCount": hard_feasible_candidates, "familyCount": len(candidate_families), "geometryCount": len(candidate_fingerprints), "lockedSupportCount": locked_support_count}},
        {"id": "constructability", "name": "施工与节点可实施性", "status": "warning" if obstacle_count == 0 or (supports and support_node_count == 0) else "pass", "evidence": {"obstacleCount": obstacle_count, "supportNodeCount": support_node_count, "columnCount": column_count, "optimizationLockCount": len(retaining.optimization_locks or []) if retaining else 0}},
        {"id": "staging", "name": "施工阶段一致性", "status": "pass" if project.calculation_cases else "warning", "evidence": {"caseCount": len(project.calculation_cases), "supportCount": len(supports)}},
        {"id": "calculation", "name": "计算合同与发行", "status": "fail" if calculation_state.get("requiresRecalculation") or (formal_gate and formal_gate.status == "fail") else "warning" if not latest or not formal_gate or not formal_gate.allowed_for_official_issue else "pass", "evidence": {"calculationState": calculation_state, "resultId": getattr(latest, "id", None), "formalGateStatus": getattr(formal_gate, "status", None), "officialIssueAllowed": bool(getattr(formal_gate, "allowed_for_official_issue", False))}},
        {"id": "runtime", "name": "计算资源预算", "status": "fail" if resource["status"] == "blocked" else "warning" if resource["status"] in {"high", "elevated"} else "pass", "evidence": resource},
    ]

    # Weight sections rather than allowing many low-severity warnings to hide a
    # failed load path.  Blocking items always cap the result below issue-ready.
    score = 100
    score -= min(72, 14 * len(blocking))
    score -= min(24, 3 * len(warnings))
    score = max(0, min(100, score))
    return {
        "status": "blocked" if blocking else "warning" if warnings else "pass",
        "score": score,
        "shapeArchetype": shape.get("archetype"),
        "recognitionConfidence": recognition_confidence,
        "blockingItems": blocking,
        "warningItems": warnings,
        "sections": sections,
        "resourceEstimate": resource,
        "candidateDiversity": {"count": len(candidates), "topologyFamilyCount": len(candidate_families), "geometryFingerprintCount": len(candidate_fingerprints), "hardFeasibleCount": hard_feasible_candidates},
        "wallDesignVariables": {"wallCount": len(walls), "designLengthCount": wall_design_length_count, "constructionPanelCount": construction_panel_count, "recomputeRequired": wall_recompute_required},
        "modelCompatibility": {"supportTopologyFamilies": sorted(topology_families), "loadPathClasses": sorted(load_path_classes), "lockedSupportCount": locked_support_count, "explicitFrameModelComplete": frame_model_complete},
        "workflow": [
            "输入冻结与设计域检查", "形状识别及歧义确认", "支撑体系与转接系统选型", "有界候选生成",
            "墙—墙传力及交叉硬约束", "围护墙长度与施工分幅联合变量", "施工通道和阶段一致性",
            "计算资源预检", "独立worker完整计算", "候选采用、节点深化与受控交付",
        ],
    }
