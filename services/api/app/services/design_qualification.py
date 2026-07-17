from __future__ import annotations

from typing import Any

from app.geometry.consistency import geometry_consistency_summary
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.geology.model_builder import geological_coverage_audit
from app.schemas.domain import Project
from app.services.plan_shape_intelligence import classify_excavation_plan
from app.services.project_coordinate_audit import audit_project_coordinate_alignment


_STATUS_RANK = {"pass": 0, "warning": 1, "manual_review": 2, "fail": 3, "blocked": 3}


SYSTEM_CATALOG: dict[str, dict[str, Any]] = {
    "direct_grid": {
        "title": "主轴短跨墙—墙对撑",
        "generationMode": "automatic",
        "modelClass": "axial_wall_to_wall",
        "prerequisites": ["存在连续可见墙对", "围檩支点间距可闭合", "普通支撑零非法穿越"],
        "hardBoundaries": ["支撑端点必须落墙或落明确结构节点", "不得以支撑跨中 T/Y 节点代替结构转接"],
    },
    "hybrid_diagonal": {
        "title": "短跨对撑与墙—墙角撑混合",
        "generationMode": "automatic",
        "modelClass": "axial_wall_to_wall",
        "prerequisites": ["角撑两端具有独立墙节点", "角撑作用区与普通对撑无冲突"],
        "hardBoundaries": ["角撑不得止于普通支撑中部", "不得形成共节点扇形拥挤"],
    },
    "zoned_direct": {
        "title": "分区墙—墙对撑与显式转接区",
        "generationMode": "preliminary",
        "modelClass": "zoned_axial_with_transfer",
        "prerequisites": ["平面可分解为可施工分区", "转接区采用环梁、分隔墙或显式框架"],
        "hardBoundaries": ["分区间传力路径必须计算建模", "不得仅靠几何相交声明节点成立"],
    },
    "ring_radial": {
        "title": "闭合内环梁与径向支撑",
        "generationMode": "automatic_subject_to_full_check",
        "modelClass": "ring_radial",
        "prerequisites": ["内环闭合", "径向支撑节点可施工", "环梁具备平面内弯剪刚度"],
        "hardBoundaries": ["环梁断口和临时拆换阶段必须单独验算", "径向支撑偏心节点需深化"],
    },
    "center_island": {
        "title": "中心岛法或留土分区施工",
        "generationMode": "system_selection_required",
        "modelClass": "staged_center_island",
        "prerequisites": ["施工组织允许保留中心岛或分区土体", "换撑路径和出土通道明确"],
        "hardBoundaries": ["施工阶段必须显式激活与拆除", "中心岛与永久结构关系需协调"],
    },
    "ring_truss": {
        "title": "环桁架或多环支撑体系",
        "generationMode": "system_selection_required",
        "modelClass": "ring_truss",
        "prerequisites": ["平面适合形成闭合或分段闭合环系", "节点刚度与构造可定义"],
        "hardBoundaries": ["需使用梁/框架模型", "不能按纯轴压杆替代环梁弯剪作用"],
    },
    "multi_ring": {
        "title": "多环梁分区体系",
        "generationMode": "system_selection_required",
        "modelClass": "multi_ring_transfer",
        "prerequisites": ["多臂或深凹平面具有可布置的转接核心区", "各分区施工顺序明确"],
        "hardBoundaries": ["各环之间必须有明确传力构件", "转接核心节点需局部复核"],
    },
    "explicit_two_way_frame": {
        "title": "显式双向平面框架",
        "generationMode": "system_selection_required",
        "modelClass": "two_way_frame",
        "prerequisites": ["支撑交汇节点按框架节点设计", "杆件具有轴力、弯矩和剪力模型"],
        "hardBoundaries": ["不得把框架节点退化为几何 T/Y 交点", "需考虑二阶效应和节点半刚性"],
    },
    "explicit_space_frame": {
        "title": "显式空间框架或桁架转接体系",
        "generationMode": "manual_model_required",
        "modelClass": "space_frame",
        "prerequisites": ["复杂转接区需要空间构件", "节点与施工安装顺序可定义"],
        "hardBoundaries": ["必须建立空间自由度模型", "局部节点需专项深化"],
    },
    "partitioned_excavation": {
        "title": "分仓开挖与分隔墙体系",
        "generationMode": "system_selection_required",
        "modelClass": "partitioned_excavation",
        "prerequisites": ["允许设置临时分隔墙或先后施工分区", "分区界面和换撑顺序明确"],
        "hardBoundaries": ["分隔墙稳定与拆换过程需单独验算", "不得忽略分区间差异变形"],
    },
    "double_wall_ring": {
        "title": "双层环梁或双圈支撑",
        "generationMode": "manual_model_required",
        "modelClass": "double_ring",
        "prerequisites": ["井筒或多边形坑具有双圈布置空间"],
        "hardBoundaries": ["内外环协同与节点偏心需专项计算"],
    },
    "radial_servo_struts": {
        "title": "径向伺服支撑体系",
        "generationMode": "manual_model_required",
        "modelClass": "servo_radial",
        "prerequisites": ["具备可控预加轴力设备和监测闭环"],
        "hardBoundaries": ["设备可靠性、失效工况和控制策略需专项设计"],
    },
    "engineer_selected": {
        "title": "工程师定义的专项支撑体系",
        "generationMode": "manual_model_required",
        "modelClass": "engineer_defined",
        "prerequisites": ["结构体系、节点和施工阶段均由工程师明确"],
        "hardBoundaries": ["自动算法仅提供几何与检查辅助，不声明计算资格"],
    },
}


def _shape_diagnostics(project: Project) -> dict[str, Any]:
    if project.excavation is None or not project.excavation.outline.points:
        return {
            "classification": "missing_excavation",
            "archetype": "missing_excavation",
            "capability": "manual_system_selection",
            "supportedTopologyFamilies": [],
            "alternativeSystems": [],
        }
    return classify_excavation_plan(
        list(project.excavation.outline.points),
        local_pit_count=len(project.excavation.local_pits or []),
        has_center_island=any(
            getattr(item, "obstacle_type", "") == "center_island" and getattr(item, "active", True)
            for item in (project.excavation.obstacles or [])
        ),
    )


def _normalise_family(value: str) -> str:
    aliases = {
        "explicit_two_way_frame": "explicit_two_way_frame",
        "bidirectional_frame": "explicit_two_way_frame",
        "ring_truss": "ring_truss",
        "center_island": "center_island",
        "multi_ring": "multi_ring",
        "partitioned_excavation": "partitioned_excavation",
        "explicit_space_frame": "explicit_space_frame",
        "double_wall_ring": "double_wall_ring",
        "radial_servo_struts": "radial_servo_struts",
    }
    return aliases.get(value, value if value in SYSTEM_CATALOG else "engineer_selected")


def build_support_system_options(project: Project, *, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = dict(diagnostics or _shape_diagnostics(project))
    supported = [_normalise_family(str(item)) for item in diagnostics.get("supportedTopologyFamilies", [])]
    alternatives = [_normalise_family(str(item)) for item in diagnostics.get("alternativeSystems", [])]
    primary_raw = str(diagnostics.get("primarySystem") or diagnostics.get("recommendedTopology") or "")
    capability = str(diagnostics.get("capability") or "manual_system_selection")

    # The shape classifier may return a descriptive primary-system name rather
    # than a topology-family id.  The first supported family remains the
    # executable automatic candidate; descriptive names are retained as design
    # intent in the response.
    ordered: list[str] = []
    for family in supported + alternatives:
        if family not in ordered:
            ordered.append(family)
    if not ordered:
        ordered.append("engineer_selected")

    repair = project.retaining_system.support_layout_repair if project.retaining_system else None
    controlled = False
    if repair:
        repair_candidates = list(repair.candidates or [])
        has_feasible_candidate = any(bool((candidate.hard_constraints or {}).get("passed")) for candidate in repair_candidates)
        controlled = bool(repair_candidates) and not has_feasible_candidate and any(
            str((candidate.variable_summary or {}).get("capabilityOutcome") or "") == "controlled_block"
            for candidate in repair_candidates
        )

    options: list[dict[str, Any]] = []
    for index, family in enumerate(ordered):
        catalog = dict(SYSTEM_CATALOG.get(family, SYSTEM_CATALOG["engineer_selected"]))
        executable = family in supported and catalog["generationMode"] in {
            "automatic", "automatic_subject_to_full_check", "preliminary"
        }
        if controlled and family in supported:
            readiness = "diagnostic_only"
            next_action = "切换到替代结构体系或补充显式转接构件后重新生成候选。"
        elif executable:
            readiness = "candidate_generation_ready"
            next_action = "生成该体系的几何候选，完成拓扑预检后提交独立施工阶段计算。"
        else:
            readiness = "system_definition_required"
            next_action = "先定义该体系的构件、节点、施工阶段和计算模型，再生成可计算候选。"
        options.append({
            "id": f"SYS-{index + 1:02d}-{family}",
            "family": family,
            "title": catalog["title"],
            "priority": index + 1,
            "recommended": index == 0,
            "source": "supported_topology" if family in supported else "alternative_system",
            "generationMode": catalog["generationMode"],
            "modelClass": catalog["modelClass"],
            "candidateReadiness": readiness,
            "automaticGenerationAvailable": executable and not controlled,
            "prerequisites": list(catalog["prerequisites"]),
            "hardBoundaries": list(catalog["hardBoundaries"]),
            "nextAction": next_action,
        })

    return {
        "projectId": project.id,
        "shapeClassification": diagnostics.get("classification"),
        "shapeArchetype": diagnostics.get("archetype"),
        "recognitionConfidence": diagnostics.get("recognitionConfidence"),
        "capability": capability,
        "descriptivePrimarySystem": primary_raw,
        "controlledBlock": controlled,
        "options": options,
        "recommendedOptionId": options[0]["id"] if options else None,
        "decisionBoundary": (
            "体系候选先确定受力模型与施工组织，再生成线位；自动可生成只表示几何与拓扑能力已覆盖，"
            "完整计算、节点深化和正式发行仍由后续证据门禁控制。"
        ),
    }


def _gate(code: str, title: str, status: str, message: str, *, blocks: list[str] | None = None, evidence: dict[str, Any] | None = None, action: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "status": status,
        "message": message,
        "blocks": blocks or [],
        "evidence": evidence or {},
        "recommendedAction": action,
    }


def build_design_qualification(
    project: Project,
    *,
    storage_info: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    systems: dict[str, Any] | None = None,
    topology_detail: str = "summary",
) -> dict[str, Any]:
    storage = dict(storage_info or {})
    full_allowed = bool(storage.get("fullLoadAllowed", True))
    workspace_allowed = bool(storage.get("workspaceLoadAllowed", True))
    compaction_recommended = bool(storage.get("compactionRecommended", False))
    workspace_bytes = int(storage.get("workspaceBytes") or 0)
    payload_bytes = int(storage.get("payloadBytes") or 0)
    resource_policy = dict(storage.get("resourcePolicy") or {})
    evidence = {
        "payloadBytes": payload_bytes,
        "workspaceBytes": workspace_bytes,
        "fullLoadAllowed": full_allowed,
        "workspaceLoadAllowed": workspace_allowed,
        "compactionRecommended": compaction_recommended,
        "apiFullLoadLimitBytes": storage.get("apiFullLoadLimitBytes"),
        "workspaceLimitBytes": storage.get("workspaceLimitBytes"),
        "resourcePolicy": resource_policy,
    }
    if not workspace_allowed:
        storage_gate = _gate(
            "Q-STORAGE",
            "项目数据工作集",
            "warning",
            "网页工作区投影超过当前动态预算，需要重建轻量投影；完整工程仍可由独立worker读取。",
            blocks=["interactive_full_load"],
            evidence=evidence,
            action="运行项目存储压缩任务，重建包含候选预览的轻量工作区。",
        )
    elif not full_allowed:
        storage_gate = _gate(
            "Q-STORAGE",
            "项目数据工作集",
            "pass",
            "系统已进入工作区优先模式：网页加载轻量投影，完整快照由独立worker按当前内存余量读取。",
            blocks=["interactive_full_load"],
            evidence=evidence,
            action="仅在工作区体积偏大或重型对象尚未外部化时执行压缩。" if compaction_recommended else None,
        )
    else:
        storage_gate = _gate(
            "Q-STORAGE", "项目数据工作集", "pass", "工作区投影与完整快照均处于当前动态资源预算内。",
            evidence=evidence,
            action="运行压缩以降低历史冗余。" if compaction_recommended else None,
        )

    geometry = geometry_consistency_summary(project)
    geometry_ok = bool(geometry.get("consistent"))
    geometry_gate = _gate(
        "Q-GEOMETRY", "围护几何一致性", "pass" if geometry_ok else "fail",
        "基坑轮廓闭合且围护墙段与轮廓段一致。" if geometry_ok else "基坑轮廓、墙段映射或孤立墙段存在不一致。",
        blocks=[] if geometry_ok else ["candidate_generation", "calculation", "formal_issue"],
        evidence=geometry,
        action=None if geometry_ok else "修复闭合轮廓、缺失墙段或孤立墙段后重新生成围护结构。",
    )

    coordinate = audit_project_coordinate_alignment(project)
    geology = geological_coverage_audit(project)
    coordinate_status = str(coordinate.get("status") or "manual_review")
    geology_status = str(geology.get("status") or "manual_review")
    coordinate_geology_status = max((coordinate_status, geology_status), key=lambda value: _STATUS_RANK.get(value, 2))
    coordinate_blocks: list[str] = []
    if coordinate_status == "fail" or geology_status == "fail":
        coordinate_blocks = ["calculation", "formal_issue"]
    elif coordinate_status in {"manual_review", "warning"} or geology_status in {"manual_review", "warning"}:
        coordinate_blocks = ["formal_issue"]
    coordinate_gate = _gate(
        "Q-COORD-GEO", "坐标与地质覆盖", coordinate_geology_status,
        f"坐标检查：{coordinate.get('message', '—')} 地质覆盖：{geology.get('message', '—')}",
        blocks=coordinate_blocks,
        evidence={"coordinateAlignment": coordinate, "geologyCoverage": geology},
        action=("确认坐标转换并重建地质设计域。" if coordinate_blocks else None),
    )

    diagnostics = dict(diagnostics or _shape_diagnostics(project))
    systems = dict(systems or build_support_system_options(project, diagnostics=diagnostics))
    repair = project.retaining_system.support_layout_repair if project.retaining_system else None
    candidates = list(repair.candidates or []) if repair else []
    feasible = [candidate for candidate in candidates if bool((candidate.hard_constraints or {}).get("passed"))]
    current_support_count = len(project.retaining_system.supports or []) if project.retaining_system else 0
    topology_hard_categories = {
        "support_spacing", "support_span", "wale_support_bay", "support_crossing",
        "support_outside_excavation", "obstacle_clearance", "temporary_column",
        "replacement_path", "support_to_support_terminal",
        "unsupported_internal_endpoint", "corner_brace_fan_geometry",
        "corner_brace_wall_node_congestion", "support_station_cluster",
    }
    current_quality = None
    topology_evidence_source = "workspace_summary"
    if current_support_count and str(topology_detail).lower() == "full":
        current_quality = evaluate_support_layout_quality(project)
        topology_evidence_source = "live_full_quality_evaluation"
    elif current_support_count and project.calculation_results:
        latest_quality = getattr(project.calculation_results[-1], "support_layout_quality", None)
        calculation_state = dict(project.advanced_engineering.get("calculationState") or {})
        if latest_quality is not None and not bool(calculation_state.get("requiresRecalculation")):
            current_quality = latest_quality
            topology_evidence_source = "current_calculation_quality_snapshot"
    current_hard_failures = [
        issue for issue in (current_quality.issues if current_quality else [])
        if issue.severity == "fail" and issue.category in topology_hard_categories
    ]
    candidate_blocking_categories = sorted({
        str(category)
        for candidate in candidates
        if not bool((candidate.hard_constraints or {}).get("passed"))
        for category in (
            (candidate.hard_constraints or {}).get("blockingCategories")
            or (candidate.hard_constraints or {}).get("qualityFailCategories")
            or []
        )
        if category
    })
    candidate_controls = [
        {
            "candidateId": candidate.id,
            "targetSpacingM": candidate.target_spacing,
            "columnSpanM": candidate.column_max_span,
            "blockingCategories": list(
                (candidate.hard_constraints or {}).get("blockingCategories")
                or (candidate.hard_constraints or {}).get("qualityFailCategories")
                or []
            ),
            "hardFailureKeys": list((candidate.hard_constraints or {}).get("hardFailureKeys") or []),
            "controlMetrics": dict(
                ((candidate.variable_summary or {}).get("topologyQualification") or {}).get("controlMetrics")
                or {}
            ),
        }
        for candidate in candidates[:3]
        if not bool((candidate.hard_constraints or {}).get("passed"))
    ]
    selected_candidate_id = str(getattr(repair, "selected_candidate_id", None) or "") if repair else ""
    selected_candidate = next((candidate for candidate in candidates if str(candidate.id or "") == selected_candidate_id), None)
    selected_candidate_passed = bool(selected_candidate and (selected_candidate.hard_constraints or {}).get("passed"))
    quality_snapshot_passed = bool(current_quality and current_quality.status in {"pass", "warning"} and not current_hard_failures)
    current_topology_ready = bool(current_support_count and (quality_snapshot_passed or selected_candidate_passed))
    controlled = bool(systems.get("controlledBlock")) and not current_topology_ready
    if current_topology_ready:
        topology_status = "pass"
        topology_message = f"当前采用体系包含 {current_support_count} 根支撑，传力拓扑硬约束检查通过。"
        topology_blocks: list[str] = []
    elif feasible and not controlled:
        topology_status = "pass"
        topology_message = f"已形成 {len(feasible)} 个满足当前拓扑硬约束的候选方案，可提交独立计算。"
        topology_blocks = []
    elif candidates or controlled:
        topology_status = "manual_review"
        category_text = "、".join(candidate_blocking_categories[:6])
        topology_message = (
            "当前体系只形成诊断候选或受控阻断，需要切换/补充结构体系。"
            + (f" 候选控制类别：{category_text}。" if category_text else "")
        )
        topology_blocks = ["calculation", "formal_issue"]
    else:
        topology_status = "warning"
        topology_message = "尚未形成可计算的当前支撑体系或候选方案。"
        topology_blocks = ["calculation", "formal_issue"]
    topology_gate = _gate(
        "Q-TOPOLOGY", "支撑体系与传力拓扑", topology_status, topology_message,
        blocks=topology_blocks,
        evidence={
            "candidateCount": len(candidates),
            "feasibleCandidateCount": len(feasible),
            "currentSupportCount": current_support_count,
            "currentTopologyReady": current_topology_ready,
            "currentQualityStatus": current_quality.status if current_quality else None,
            "topologyEvidenceSource": topology_evidence_source,
            "selectedCandidateId": selected_candidate_id or None,
            "selectedCandidatePassed": selected_candidate_passed,
            "currentHardFailureCount": len(current_hard_failures),
            "currentHardFailureCategories": sorted({issue.category for issue in current_hard_failures}),
            "candidateBlockingCategories": candidate_blocking_categories,
            "candidateControls": candidate_controls,
            "controlledBlock": controlled,
            "systemOptionCount": len(systems.get("options") or []),
        },
        action="生成体系级候选或选用替代支撑体系。" if topology_blocks else None,
    )

    calculation_state = dict(project.advanced_engineering.get("calculationState") or {})
    requires_recalculation = bool(
        calculation_state.get("requiresRecalculation")
        or project.advanced_engineering.get("requiresRecalculation")
    )
    latest = project.calculation_results[-1] if project.calculation_results else None
    assurance = dict(getattr(latest, "calculation_assurance", {}) or {}) if latest else {}
    calculation_ready = bool(latest and not requires_recalculation and assurance.get("eligibleForEngineeringUse", assurance.get("status") in {"pass", "warning"}))
    calculation_gate = _gate(
        "Q-CALC", "当前方案计算证据", "pass" if calculation_ready else "fail" if requires_recalculation else "warning",
        "当前方案计算合同有效，可用于后续深化。" if calculation_ready else (
            "当前拓扑或设计输入已变化，旧计算证据失效。" if requires_recalculation else "尚无当前方案的完整施工阶段计算证据。"
        ),
        blocks=[] if calculation_ready else ["detailing_release", "formal_issue"],
        evidence={
            "hasCalculationResult": bool(latest),
            "requiresRecalculation": requires_recalculation,
            "calculationState": calculation_state,
            "assuranceStatus": assurance.get("status"),
            "eligibleForEngineeringUse": assurance.get("eligibleForEngineeringUse"),
        },
        action=None if calculation_ready else "采用方案后运行完整施工阶段计算并重新验证计算合同。",
    )

    formal_gate = dict(getattr(latest, "formal_report_gate", {}) or {}) if latest else {}
    delivery = dict(getattr(latest, "delivery_readiness", {}) or {}) if latest else {}
    formal_allowed = bool(
        formal_gate.get("allowed")
        or formal_gate.get("formalIssueAllowed")
        or delivery.get("formalIssueAllowed")
        or delivery.get("allowed")
    )
    delivery_gate = _gate(
        "Q-DELIVERY", "深化与正式交付", "pass" if formal_allowed else "manual_review",
        "正式交付闸门已满足。" if formal_allowed else "节点、配筋、校审或正式发行证据尚未全部闭环。",
        blocks=[] if formal_allowed else ["formal_issue"],
        evidence={"formalReportGate": formal_gate, "deliveryReadiness": delivery},
        action=None if formal_allowed else "完成节点与配筋深化、专业校审和发行审批后再出正式施工版。",
    )

    gates = [storage_gate, geometry_gate, coordinate_gate, topology_gate, calculation_gate, delivery_gate]
    hard_fail = any(item["status"] == "fail" and any(block != "interactive_full_load" for block in item["blocks"]) for item in gates)
    controlled_mode = controlled or topology_gate["status"] == "manual_review"
    degraded = not workspace_allowed or any(item["status"] in {"warning", "manual_review"} for item in gates)
    interaction_mode = "diagnostic" if controlled_mode or hard_fail else "degraded" if degraded else "normal"

    action_queue: list[dict[str, Any]] = []
    for index, gate in enumerate(gates, start=1):
        if gate.get("recommendedAction"):
            action_queue.append({
                "priority": index,
                "gateCode": gate["code"],
                "title": gate["title"],
                "action": gate["recommendedAction"],
            })

    return {
        "projectId": project.id,
        "status": "blocked" if hard_fail or controlled_mode else "warning" if degraded else "pass",
        "interactionMode": interaction_mode,
        "workspaceProfileRequired": not full_allowed,
        "workspaceHealthy": workspace_allowed,
        "compactionRecommended": compaction_recommended,
        "candidateGenerationAllowed": geometry_ok and coordinate_status != "fail",
        "calculationAllowed": geometry_ok and "calculation" not in coordinate_blocks and topology_gate["status"] == "pass" and not controlled,
        "formalIssueAllowed": formal_allowed,
        "gates": gates,
        "systemOptions": systems,
        "nextActions": action_queue,
        "statusSemantics": {
            "normal": "可按标准流程生成候选、计算和深化。",
            "degraded": "工作区仍可操作，但存在非致命数据或证据缺口。",
            "diagnostic": "当前只允许诊断、体系选择和修复，不应把候选卡理解为可计算设计方案。",
        },
    }
