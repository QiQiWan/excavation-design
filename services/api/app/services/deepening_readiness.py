from __future__ import annotations

from typing import Any, Iterable

from app.schemas.domain import Project
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.support_topology_contract import support_topology_hash
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION


_CATEGORY_GUIDANCE: dict[str, tuple[str, str, str]] = {
    "wall_reinforcement": ("墙体配筋或截面承载力", "增大墙厚、提高材料/配筋等级或调整分区配筋后重新计算。", "配筋深化"),
    "wall_plan_reinforcement": ("墙体平面局部配筋", "调整转角/支撑节点区附加筋和局部构造。", "配筋深化"),
    "support_reinforcement": ("支撑截面或配筋承载力", "应用建议截面并重新计算支撑刚度、轴力和节点承压。", "围护方案"),
    "beam_reinforcement": ("冠梁/围檩配筋", "增大梁截面或调整主筋、箍筋和节点加密区。", "配筋深化"),
    "node_congestion": ("节点承压或钢筋拥挤", "增大节点核心区、调整承压板/锚固或执行局部节点分析。", "配筋深化"),
    "rebar_congestion": ("墙体钢筋净距与可穿入性", "调整钢筋层数、直径、间距或墙厚，并重新生成逐根钢筋。", "配筋深化"),
    "support_rebar_congestion": ("支撑钢筋净距与可穿入性", "增大支撑截面或调整纵筋根数/直径。", "配筋深化"),
    "anchorage": ("锚固长度", "调整锚固路径、节点刚域或附加锚固构造。", "配筋深化"),
    "lap_splice": ("搭接长度与接头位置", "将接头移出节点刚域并补齐错开与接头等级。", "配筋深化"),
    "embedded_collision": ("预埋件与钢筋空间碰撞", "应用构造协调方案，移动钢筋/预埋件或增加开孔加劲大样。", "P3 深化"),
    "collision": ("构件或钢筋空间碰撞", "按碰撞对象执行几何协调并重新运行 P3。", "P3 深化"),
    "cage_hoisting": ("钢筋笼吊装", "补齐吊机工况，调整分节、吊点或临时加强。", "P3 深化"),
    "node_hardware": ("节点硬件", "调整承压板、加劲板、焊缝或锚筋并重新复核。", "P3 深化"),
}

# These failures are produced *by* P3 spatial/detailing work.  Treating them as
# prerequisites for entering P3 creates a circular gate: the engineer is told
# to run P3 but the run button is disabled.  They remain release blockers until
# resolved, while the P3 entry itself stays available.
_P3_RESOLVABLE_CATEGORIES = {
    "anchorage", "lap_splice", "node_congestion", "rebar_congestion",
    "support_rebar_congestion", "embedded_collision", "collision",
    "cage_hoisting", "node_hardware",
}


def _status_rank(value: Any) -> int:
    return {"pass": 0, "preliminary": 1, "manual_review": 2, "warning": 3, "fail": 4}.get(str(value), 2)


def _unique(values: Iterable[Any], maximum: int = 12) -> list[str]:
    rows: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= maximum:
            break
    return rows


def group_deepening_checks(
    checks: list[dict[str, Any]],
    *,
    statuses: set[str],
    source: str = "reinforcement_checks",
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        if str(check.get("status") or "manual_review") not in statuses:
            continue
        category = str(check.get("failureReasonCode") or check.get("category") or check.get("type") or check.get("ruleId") or "other")
        groups.setdefault(category, []).append(dict(check))
    output: list[dict[str, Any]] = []
    for category, rows in groups.items():
        sample = max(rows, key=lambda row: _status_rank(row.get("status")))
        normalized_category = str(sample.get("category") or category).lower()
        title, fallback_action, target = _CATEGORY_GUIDANCE.get(
            normalized_category,
            (str(category).replace("_", " "), "按控制对象调整设计后重新计算并运行深化检查。", "配筋深化"),
        )
        objects = _unique((row.get("hostCode") or row.get("objectCode") or row.get("hostId") or row.get("objectId") for row in rows))
        output.append({
            "id": f"{source}:{category}",
            "source": source,
            "category": normalized_category,
            "reasonCode": category,
            "title": title,
            "status": "fail" if any(str(row.get("status")) == "fail" for row in rows) else "warning",
            "count": len(rows),
            "objectCount": len(set(objects)),
            "objects": objects,
            "message": str(sample.get("message") or title),
            "requiredAction": str(sample.get("recommendedAction") or sample.get("recommendation") or fallback_action),
            "targetStage": target,
            "canResolveAtDesignStage": normalized_category not in {"cage_hoisting"},
            "evidence": {
                "calculatedValue": sample.get("calculatedValue"),
                "limitValue": sample.get("limitValue"),
                "unit": sample.get("unit"),
                "sampleCheckId": sample.get("checkId") or sample.get("ruleId") or sample.get("id"),
            },
        })
    output.sort(key=lambda row: (-_status_rank(row.get("status")), -int(row.get("count") or 0), str(row.get("title"))))
    return output


def calculation_readiness(project: Project) -> dict[str, Any]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    advanced = dict(project.advanced_engineering or {})
    state = dict(advanced.get("calculationState") or {})
    workspace_evidence = dict(advanced.get("workspaceCalculationEvidence") or {})
    has_stages = bool(latest and latest.stage_results)
    stage_summary = dict(getattr(latest, "stage_result_summary", None) or {}) if latest else {}
    declared_stage_count = int(stage_summary.get("actualCount") or workspace_evidence.get("persistedStageResultCount") or 0)
    loaded_stage_count = len(latest.stage_results) if latest else 0
    expected_stage_count = int(stage_summary.get("expectedCount") or workspace_evidence.get("expectedStageResultCount") or 0)
    evidence_state = str(workspace_evidence.get("state") or stage_summary.get("loadState") or "")
    if has_stages and not evidence_state:
        evidence_state = "loaded"
    elif latest is None:
        evidence_state = "no_result"
    elif not evidence_state and declared_stage_count > 0:
        evidence_state = "external_unloaded"
    elif not evidence_state:
        evidence_state = "not_generated"
    if evidence_state in {"inline", "loaded"} and expected_stage_count > 0 and loaded_stage_count < expected_stage_count:
        evidence_state = "partial"
    evidence_complete = bool(
        has_stages
        and evidence_state in {"inline", "loaded"}
        and (expected_stage_count <= 0 or loaded_stage_count >= expected_stage_count)
    )
    explicit_stale = bool(state.get("requiresRecalculation") or advanced.get("requiresRecalculation"))
    has_contract = bool(latest and (
        latest.calculation_contract_id
        or latest.input_snapshot_hash
        or (latest.calculation_assurance or {}).get("contract")
        or (latest.design_iteration_summary or {}).get("calculationContract")
    ))
    contract = verify_current_calculation_contract(project, latest) if latest and has_contract else {
        "current": bool(has_stages and not explicit_stale),
        "reason": "legacy calculation has no immutable contract; stage evidence is accepted for migration and must be refreshed before formal issue",
        "legacy": True,
    }
    workspace_profile = str(((advanced.get("workspaceStorage") or {}).get("profile") or "")) == "workspace"
    if latest and has_contract and workspace_profile and not contract.get("current"):
        stored = dict((latest.calculation_assurance or {}).get("contract") or (latest.design_iteration_summary or {}).get("calculationContract") or {})
        current_topology = support_topology_hash(project) if project.retaining_system else None
        stored_algorithm = str(stored.get("algorithmVersion") or (latest.design_iteration_summary or {}).get("algorithmVersion") or "")
        stored_rules = str(stored.get("ruleSetVersion") or (latest.design_iteration_summary or {}).get("ruleSetVersion") or "")
        case_exists = any(case.id == latest.case_id for case in project.calculation_cases)
        state_current = (
            str(state.get("status") or "") == "current"
            and str(state.get("resultId") or "") == str(latest.id)
            and not explicit_stale
        )
        projection_compatible = bool(
            state_current
            and case_exists
            and latest.calculation_contract_id
            and latest.support_topology_hash == current_topology
            and stored_algorithm == ALGORITHM_VERSION
            and stored_rules == RULE_SET_VERSION
        )
        if projection_compatible:
            contract = {
                **contract,
                "current": True,
                "verificationMode": "workspace_projection_with_authoritative_invalidation_state",
                "reason": "精简工作区省略了部分计算输入；依据结果ID、拓扑、工况、版本和显式失效状态确认当前性。",
            }
    current = bool(evidence_complete and not explicit_stale and contract.get("current"))
    fail_count = int((latest.check_summary or {}).get("fail", 0) or 0) if latest else 0
    assurance = dict(getattr(latest, "calculation_assurance", None) or {}) if latest else {}
    assurance_status = str(assurance.get("status") or ("legacy" if latest else "missing"))
    reasons: list[str] = []
    missing_data: list[dict[str, Any]] = []
    if latest is None:
        reasons.append("尚未生成当前方案计算结果。")
        missing_data.append({
            "code": "CALCULATION_NOT_RUN", "type": "output_not_generated", "label": "当前方案施工阶段计算结果",
            "targetStage": "计算验算", "action": "确认施工阶段后运行“计算当前方案”。", "designStageAvailable": True,
        })
    elif evidence_state in {"artifact_missing", "partial"}:
        if evidence_state == "partial" and loaded_stage_count:
            reasons.append(
                f"仅载入 {loaded_stage_count}/{expected_stage_count or declared_stage_count} 条施工阶段结果，不能据此形成完整内力包络。"
            )
        else:
            reasons.append("施工阶段结果索引存在，但外部成果缺失、损坏或仅部分可读。")
        missing_data.append({
            "code": "STAGE_ARTIFACT_MISSING", "type": "external_evidence_missing", "label": "外部施工阶段成果",
            "targetStage": "系统维护/计算验算", "action": "检查项目 artifacts 目录及校验和；无法恢复时重新运行当前方案计算。", "designStageAvailable": False,
        })
    elif not has_stages:
        if declared_stage_count > 0 or evidence_state == "external_unloaded":
            reasons.append("施工阶段成果已外部保存，但当前接口尚未载入；这不是工程输入缺失。")
            missing_data.append({
                "code": "STAGE_EVIDENCE_NOT_LOADED", "type": "external_evidence_not_loaded", "label": "外部施工阶段成果加载",
                "targetStage": "系统读取", "action": "通过最新计算证据接口按计算结果ID加载阶段分块，无需重新录入设计资料。", "designStageAvailable": False,
            })
        else:
            reasons.append("计算已建立汇总，但尚未生成逐施工阶段结果。")
            missing_data.append({
                "code": "STAGE_RESULTS_NOT_GENERATED", "type": "output_not_generated", "label": "逐施工阶段内力与变形结果",
                "targetStage": "计算验算", "action": "检查施工阶段有效性后重新运行当前方案完整计算。", "designStageAvailable": True,
            })
    if explicit_stale:
        reasons.append(str(state.get("reason") or "构件、拓扑或输入修改后尚未重新计算。"))
        missing_data.append({
            "code": "CALCULATION_STALE", "type": "output_stale", "label": "与当前设计快照一致的计算合同",
            "targetStage": "计算验算", "action": "保存当前施工阶段并重新运行完整计算。", "designStageAvailable": True,
        })
    if has_contract and not contract.get("current"):
        reasons.append("计算合同与当前设计快照不一致。")
    if fail_count:
        reasons.append(f"当前计算仍有 {fail_count} 个硬失败。")
    if assurance_status == "fail":
        reasons.append("计算质量包存在阶段覆盖、数值质量或追溯硬失败。")
    return {
        "status": "pass" if current and fail_count == 0 and assurance_status != "fail" else "fail",
        "valid": current and fail_count == 0 and assurance_status != "fail",
        "hasStageResults": has_stages,
        "stageEvidenceState": evidence_state,
        "stageResultCount": loaded_stage_count,
        "persistedStageResultCount": declared_stage_count,
        "expectedStageResultCount": expected_stage_count,
        "stageEvidenceComplete": evidence_complete,
        "stageEvidence": workspace_evidence,
        "resultId": getattr(latest, "id", None),
        "failCount": fail_count,
        "assuranceStatus": assurance_status,
        "explicitlyStale": explicit_stale,
        "contract": contract,
        "missingData": missing_data,
        "messages": reasons or ["当前施工阶段内力包络、输入快照和支撑拓扑一致。"],
    }


def _gate_issue(
    issue_id: str,
    title: str,
    message: str,
    action: str,
    target: str,
    *,
    count: int = 1,
    source: str = "workflow_gate",
    objects: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "source": source,
        "category": "workflow",
        "reasonCode": issue_id,
        "title": title,
        "status": "fail",
        "count": max(1, int(count)),
        "objectCount": len(objects or []),
        "objects": objects or [],
        "message": message,
        "requiredAction": action,
        "targetStage": target,
        "canResolveAtDesignStage": True,
        "evidence": {},
    }


def build_deepening_readiness(
    project: Project,
    *,
    checks: list[dict[str, Any]],
    section_change_count: int = 0,
    topology_status: str = "pass",
    scheme_applied: bool | None = None,
    extra_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ret = project.retaining_system
    applied = bool(ret and ret.rebar_design_scheme and ret.rebar_design_scheme.get("wallZones")) if scheme_applied is None else bool(scheme_applied)
    calculation = calculation_readiness(project)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not bool(project.design_settings.design_basis_confirmed):
        blockers.append(_gate_issue("DESIGN_BASIS_MISSING", "设计基准未确认", "材料、荷载组合和控制指标尚未冻结。", "确认设计基准后重新计算。", "设计基准"))
    if ret is None:
        blockers.append(_gate_issue("RETAINING_SYSTEM_MISSING", "围护体系缺失", "没有可配筋和深化的墙、梁、支撑与节点对象。", "先生成并采用围护体系。", "围护方案"))
    if not calculation["valid"]:
        blockers.append(_gate_issue(
            "CALCULATION_NOT_CURRENT", "计算结果缺失或已过期",
            "；".join(calculation["messages"]), "运行当前方案完整计算并关闭计算质量硬失败。", "计算验算",
            count=max(1, int(calculation.get("failCount") or 0)), source="calculation_contract",
        ))
    if topology_status == "fail":
        blockers.append(_gate_issue("SUPPORT_TOPOLOGY_FAILED", "支撑传力体系未闭合", "支撑拓扑仍有硬失败。", "优化支撑拓扑并重新计算。", "围护方案", source="support_topology"))
    if not applied:
        blockers.append(_gate_issue("REBAR_SCHEME_NOT_APPLIED", "配筋方案尚未应用", "当前查看结果仍是配筋草案，构件尚未写入正式配筋。", "点击“生成并应用配筋草案”。", "配筋深化"))
    if section_change_count:
        blockers.append(_gate_issue(
            "SECTION_CHANGE_RECALCULATION_REQUIRED", "截面调整后需要重算",
            f"配筋设计建议调整 {section_change_count} 个支撑截面，旧内力包络已失效。",
            "应用截面优化并完成自动重算，再重新生成配筋。", "计算验算", count=section_change_count,
        ))

    missing_beams = [
        beam.code for beam in ([*ret.crown_beams, *ret.wale_beams, *(ret.ring_beams or [])] if ret else [])
        if beam.design_result is None
    ]
    # Legacy results are accepted only as a migration bridge and historically
    # did not contain crown/wale design-result objects.  New immutable
    # calculation contracts must satisfy the full beam evidence contract.
    blocking_missing_beams = [] if bool((calculation.get("contract") or {}).get("legacy")) else missing_beams
    if blocking_missing_beams:
        blockers.append(_gate_issue(
            "BEAM_DESIGN_RESULT_MISSING", "冠梁/围檩缺少正式设计结果",
            "部分梁只有几何对象，尚未写入施工阶段内力、承载力与配筋记录。",
            "重新运行当前施工方案计算；系统将由墙顶剪力或围檩反力包络生成梁设计并回写配筋。",
            "计算验算", count=len(blocking_missing_beams), source="beam_design_contract", objects=blocking_missing_beams,
        ))

    raw_check_blockers = group_deepening_checks(checks, statuses={"fail"})
    p3_check_work = [row for row in raw_check_blockers if str(row.get("category")) in _P3_RESOLVABLE_CATEGORIES]
    check_blockers = [row for row in raw_check_blockers if str(row.get("category")) not in _P3_RESOLVABLE_CATEGORIES]
    for row in p3_check_work:
        row["status"] = "warning"
        row["message"] = f"该项需在 P3 内处理，不阻断进入 P3：{row.get('message') or row.get('title')}"
    blockers.extend(check_blockers)
    warnings.extend(p3_check_work)
    warnings.extend(group_deepening_checks(checks, statuses={"warning", "manual_review", "preliminary"}))
    extra = list(extra_checks or [])
    raw_extra_blockers = group_deepening_checks(extra, statuses={"fail"}, source="spatial_detailing")
    extra_blockers = [row for row in raw_extra_blockers if str(row.get("category")) not in _P3_RESOLVABLE_CATEGORIES]
    p3_extra_work = [row for row in raw_extra_blockers if str(row.get("category")) in _P3_RESOLVABLE_CATEGORIES]
    for row in p3_extra_work:
        row["status"] = "warning"
        row["message"] = f"该空间问题正是 P3 的处理对象，不阻断启动 P3：{row.get('message') or row.get('title')}"
    warnings.extend(p3_extra_work)
    extra_warnings = group_deepening_checks(extra, statuses={"warning", "manual_review"}, source="spatial_detailing")
    warnings.extend(extra_warnings)

    p3 = dict((project.advanced_engineering or {}).get("p3DetailingClosure") or {})
    p3_summary = dict(p3.get("summary") or {})
    p3_fail_count = int(p3_summary.get("failCount") or 0)
    p3_unmatched = int(p3_summary.get("unmatchedEnterpriseNodeCount") or 0)
    # ``blockers`` already contains every hard calculation blocker.  Only add
    # the P3-resolvable check work here so the release gate remains strict
    # without counting the same hard failure twice.
    release_blockers = list(blockers) + p3_check_work + raw_extra_blockers
    if str(p3.get("status") or "") == "fail" and p3_fail_count:
        release_blockers.append(_gate_issue(
            "P3_CLOSURE_FAILED", "P3 节点与空间深化未闭合",
            f"已运行的 P3 闭环仍有 {p3_fail_count} 个硬失败。",
            "按 P3 控制项处理节点、碰撞或吊装问题后重新运行 P3。", "P3 深化", count=p3_fail_count, source="p3_detailing",
        ))
    if p3_unmatched:
        unmatched_issue = {
            **_gate_issue(
                "ENTERPRISE_NODE_TEMPLATE_MISSING", "企业节点模板未覆盖",
                f"有 {p3_unmatched} 个节点未匹配企业模板。", "补充企业节点模板或完成专项节点设计。", "P3 深化",
                count=p3_unmatched, source="p3_detailing",
            ),
            "status": "warning",
        }
        warnings.append(unmatched_issue)
        release_blockers.append(unmatched_issue)
    if not p3:
        p3_not_run_issue = {
            **_gate_issue(
                "P3_NOT_RUN", "P3 深化闭环尚未运行", "配筋方案通过后仍需生成逐根钢筋、节点子模型和空间协调结果。",
                "运行 P3 深化闭环。", "P3 深化", source="p3_detailing",
            ),
            "status": "warning",
        }
        warnings.append(p3_not_run_issue)
        release_blockers.append(p3_not_run_issue)
    elif str(p3.get("status") or "") == "warning" and not p3_unmatched:
        p3_review_issue = {
            **_gate_issue(
                "P3_REVIEW_ITEMS_OPEN", "P3 深化复核项尚未关闭",
                f"P3 闭环仍有 {int(p3_summary.get('warningCount') or 0)} 个复核项。",
                "关闭 P3 控制复核项，或按项目校审流程形成有依据的接受结论。", "P3 深化",
                count=max(1, int(p3_summary.get("warningCount") or 0)), source="p3_detailing",
            ),
            "status": "warning",
        }
        warnings.append(p3_review_issue)
        release_blockers.append(p3_review_issue)

    can_generate_scheme = bool(
        project.design_settings.design_basis_confirmed
        and ret is not None
        and calculation["valid"]
        and topology_status != "fail"
    )
    # Applying a generated design scheme is itself part of the design
    # iteration.  Member-level failures remain visible and block P3/issue, but
    # must not create a dead end where the proposed reinforcement cannot be
    # written back for adjustment and recalculation.
    can_apply_scheme = can_generate_scheme
    can_enter = len(blockers) == 0
    can_run_p3 = can_enter
    can_issue = can_enter and not extra_blockers and str(p3.get("status") or "") == "pass"

    steps = [
        {"id": "calculation", "label": "当前计算合同", "status": "pass" if calculation["valid"] else "fail", "message": calculation["messages"][0]},
        {"id": "scheme", "label": "配筋方案写入", "status": "pass" if applied else "fail", "message": "已应用到构件" if applied else "仍为草案"},
        {"id": "recalculation", "label": "截面调整后复算", "status": "pass" if section_change_count == 0 else "fail", "message": "无需复算" if section_change_count == 0 else f"待复算 {section_change_count} 项"},
        {"id": "member_checks", "label": "构件与配筋硬校核", "status": "pass" if not check_blockers else "fail", "message": f"{sum(int(row['count']) for row in check_blockers)} 个阻断" if check_blockers else "硬校核通过"},
        {"id": "spatial_detailing", "label": "节点与空间深化", "status": "pass" if not extra_blockers else "fail", "message": f"{sum(int(row['count']) for row in extra_blockers)} 个阻断" if extra_blockers else "当前未发现空间硬碰撞"},
        {"id": "p3", "label": "P3 企业深化闭环", "status": str(p3.get("status") or "pending"), "message": "尚未运行" if not p3 else f"失败 {p3_fail_count}，模板未匹配 {p3_unmatched}"},
    ]
    next_actions: list[dict[str, Any]] = []
    for priority, row in enumerate(blockers + extra_blockers + warnings, start=1):
        action = str(row.get("requiredAction") or "").strip()
        if action and action not in {str(item.get("description")) for item in next_actions}:
            next_actions.append({
                "id": row.get("reasonCode") or row.get("id"),
                "priority": 1 if row.get("status") == "fail" else 3,
                "label": row.get("title"),
                "description": action,
                "targetStage": row.get("targetStage"),
            })
        if len(next_actions) >= 12:
            break
    support_contract_summary = dict((ret.rebar_design_scheme or {}).get("supportRebarContractSummary") or {}) if ret else {}
    beam_contract_summary = dict((ret.rebar_design_scheme or {}).get("beamRebarContractSummary") or {}) if ret else {}
    contract_incomplete = int(support_contract_summary.get("incompleteCount") or 0) + int(beam_contract_summary.get("incompleteCount") or 0)
    structural_closed = bool(
        calculation["valid"] and topology_status != "fail" and not check_blockers
        and not blocking_missing_beams and section_change_count == 0 and contract_incomplete == 0
    )
    if can_enter:
        headline = (
            "结构计算与构件配筋已闭合；可运行 P3。剩余项目属于锚固、接头、碰撞或审签复核，不再误报为结构未闭合。"
            if structural_closed and warnings
            else "结构计算、配筋合同与深化入口均已闭合。"
        )
    else:
        headline = f"配筋深化入口仍有 {sum(int(row.get('count') or 1) for row in blockers)} 个结构/数据阻断，请按下列顺序补齐。"
    return {
        "version": "3.55-deepening-readiness-v2",
        "status": "blocked" if blockers else "review" if warnings else "ready",
        "calculation": calculation,
        "schemeApplied": applied,
        "canGenerateScheme": can_generate_scheme,
        "canApplyScheme": can_apply_scheme,
        "canEnterDetailing": can_enter,
        "canRunP3": can_run_p3,
        "canIssueConstructionDrawings": can_issue,
        "blockerGroupCount": len(blockers),
        "blockerCount": sum(int(row.get("count") or 1) for row in blockers),
        "releaseBlockerGroupCount": len(release_blockers),
        "releaseBlockerCount": sum(int(row.get("count") or 1) for row in release_blockers),
        "warningGroupCount": len(warnings),
        "warningCount": sum(int(row.get("count") or 1) for row in warnings),
        "blockers": blockers,
        "releaseBlockers": release_blockers,
        "warnings": warnings,
        "steps": steps,
        "nextActions": next_actions,
        "p3": {"status": p3.get("status") or "not_run", "summary": p3_summary},
        "structuralClosure": {
            "status": "closed" if structural_closed else "open",
            "closed": structural_closed,
            "missingBeamDesignCount": len(blocking_missing_beams),
            "legacyBeamDesignResultCount": len(missing_beams) if not blocking_missing_beams else 0,
            "supportRebarContract": support_contract_summary,
            "beamRebarContract": beam_contract_summary,
            "incompleteRebarContractCount": contract_incomplete,
            "message": "结构数值与五类钢筋合同已闭合，构造复核继续在 P3 完成。" if structural_closed else "仍有结构计算、梁设计或配筋合同未闭合。",
        },
        "headline": headline,
    }
