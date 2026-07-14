from __future__ import annotations

import math

from app.services.support_topology_contract import support_topology_hash
from app.schemas.domain import FormalReportGate, IfcCompatibilityCheckResult, Project, QualityGateIssue, SupportLayoutQualitySummary
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION
from app.services.calculation_assurance import verify_current_calculation_contract


def _issue(category: str, severity: str, message: str, object_id: str | None = None, object_type: str | None = None, recommendation: str | None = None) -> QualityGateIssue:
    return QualityGateIssue(category=category, severity=severity, object_id=object_id, object_type=object_type, message=message, recommendation=recommendation)


def _section(title: str, items: list[QualityGateIssue]) -> dict:
    counts = {"fail": 0, "warning": 0, "manual_review": 0, "pass": 0}
    for item in items:
        counts[item.severity] = counts.get(item.severity, 0) + 1
    status = "fail" if counts.get("fail") else "warning" if counts.get("warning") or counts.get("manual_review") else "pass"
    return {"title": title, "status": status, "counts": counts, "items": [i.model_dump(mode="json", by_alias=True) for i in items[:20]]}


def build_formal_report_gate(project: Project, support_quality: SupportLayoutQualitySummary | None, ifc_quality: IfcCompatibilityCheckResult | None, latest_result=None) -> FormalReportGate:
    latest = latest_result or (project.calculation_results[-1] if project.calculation_results else None)
    blocking: list[QualityGateIssue] = []
    warnings: list[QualityGateIssue] = []
    missing: list[QualityGateIssue] = []
    check_summary = latest.check_summary if latest else {}
    fail_count = int(check_summary.get("fail", 0) or 0)
    warn_count = int(check_summary.get("warning", 0) or 0)
    manual_count = int(check_summary.get("manualReview", check_summary.get("manual_review", 0)) or 0)
    if not latest:
        missing.append(_issue("formal_report", "manual_review", "尚未运行计算，不能形成正式计算书首页结论。", recommendation="先执行一键计算校核。"))
    if latest:
        iteration = dict(getattr(latest, "design_iteration_summary", {}) or {})
        current_hash = support_topology_hash(project) if project.retaining_system else None
        contract_verification = verify_current_calculation_contract(project, latest)
        contract_current = bool(contract_verification.get("current"))
        if not contract_current:
            blocking.append(_issue(
                "calculation_contract",
                "fail",
                "当前计算结果与支撑拓扑、算法版本或规则集不一致。",
                recommendation="按当前设计快照重新建立工况并执行完整计算。",
            ))
        assurance = dict(getattr(latest, "calculation_assurance", {}) or iteration.get("industrialCalculationAssurance") or {})
        assurance_status = str(assurance.get("status") or "missing")
        if not assurance:
            missing.append(_issue(
                "calculation_assurance",
                "manual_review",
                "缺少工业计算质量包，未证明输入冻结、阶段覆盖、数值收敛和独立复核已执行。",
                recommendation="按当前快照重新运行 V3.24 完整计算。",
            ))
        elif assurance_status == "fail":
            blocking.append(_issue(
                "calculation_assurance",
                "fail",
                "工业计算质量包存在硬失败。",
                recommendation="关闭输入、数值、阶段覆盖或独立复核失败后重新计算。",
            ))
        elif assurance_status in {"warning", "manual_review"}:
            warnings.append(_issue(
                "calculation_assurance",
                "manual_review",
                "工业计算质量包仍存在警告或人工复核项。",
                recommendation="正式发行前处理独立计算差异、低置信度参数和追溯缺项。",
            ))
        if not getattr(latest, "input_snapshot_hash", None) or not getattr(latest, "result_hash", None):
            blocking.append(_issue(
                "calculation_baseline",
                "fail",
                "计算结果缺少输入快照哈希或结果哈希，无法形成不可变计算基线。",
                recommendation="重新运行完整计算并保存计算合同。",
            ))
        governing_obj = getattr(latest, "governing_values", None)
        governing = governing_obj.model_dump() if governing_obj is not None else {}
        numeric_values = [float(value) for value in governing.values() if isinstance(value, (int, float))]
        if not numeric_values or not all(math.isfinite(value) for value in numeric_values):
            blocking.append(_issue(
                "numerical_validity",
                "fail",
                "控制结果存在空值、无穷值或非数值状态。",
                recommendation="检查输入量纲、工况激活、矩阵条件数和计算收敛性。",
            ))
        latest_repair = getattr(latest, "support_layout_repair", None)
        candidate_rows = list(((latest_repair.candidate_full_calculations if latest_repair else []) or []))
        valid_candidate_rows = [row for row in candidate_rows if row.get("status") not in {"failed", "error"}]
        if project.retaining_system and project.retaining_system.support_layout_repair and project.retaining_system.support_layout_repair.candidates and len(valid_candidate_rows) < 3:
            warnings.append(_issue(
                "candidate_calculation",
                "manual_review",
                "A/B/C 候选方案尚未全部完成独立计算。",
                recommendation="对前三个候选分别运行完整计算，比较轴力、位移、围檩内力、稳定性和施工复杂度。",
            ))
    if fail_count > 0:
        blocking.append(_issue("calculation_check", "fail", f"当前计算结果存在 {fail_count} 个 fail 项。", recommendation="修复 fail 后重新计算。"))
    if warn_count > 0:
        warnings.append(_issue("calculation_check", "warning", f"当前计算结果存在 {warn_count} 个 warning 项。", recommendation="正式提交前逐项复核 warning。"))
    if manual_count > 0:
        warnings.append(_issue("calculation_check", "manual_review", f"当前计算结果存在 {manual_count} 个人工复核项。", recommendation="由注册岩土/结构工程师补充复核。"))
    if not support_quality:
        missing.append(_issue("support_layout_quality", "manual_review", "缺少支撑布置合理性评分。", recommendation="重新运行计算或执行支撑质量检查。"))
    elif support_quality.status == "fail":
        blocking.extend([i for i in support_quality.issues if i.severity == "fail"] or [_issue("support_layout_quality", "fail", support_quality.summary)])
    elif support_quality.status in {"warning", "manual_review"}:
        warnings.extend(support_quality.issues[:12] or [_issue("support_layout_quality", support_quality.status, support_quality.summary)])
    if not ifc_quality:
        missing.append(_issue("ifc_compatibility", "manual_review", "缺少 IFC 兼容性自检结果。", recommendation="导出前运行 IFC 兼容性自检。"))
    elif ifc_quality.status == "fail":
        blocking.extend([i for i in ifc_quality.issues if i.severity == "fail"] or [_issue("ifc_compatibility", "fail", ifc_quality.summary)])
    elif ifc_quality.status in {"warning", "manual_review"}:
        warnings.extend(ifc_quality.issues[:12] or [_issue("ifc_compatibility", ifc_quality.status, ifc_quality.summary)])
    if latest:
        if not latest.stability_detailed_result:
            missing.append(_issue("stability_special", "manual_review", "缺少可审查地下水与稳定专项结果。", recommendation="补充稳定专项计算包。"))
        if not latest.drawing_sheets:
            warnings.append(_issue("drawing_output", "warning", "缺少施工图级详图输出清单。", recommendation="导出支撑平面、围檩节点、钢筋笼和立柱桩详图。"))
        if not (latest.report_diagram_data or {}).get("checkSummary"):
            warnings.append(_issue("report_data", "warning", "计算书图表数据不完整。", recommendation="重新生成计算书图表数据。"))
    support_items = list(support_quality.issues if support_quality else [])
    ifc_items = list(ifc_quality.issues if ifc_quality else [])
    calculation_items = []
    if fail_count:
        calculation_items.append(_issue("calculation_check", "fail", f"计算 fail 项 {fail_count} 个。"))
    if warn_count:
        calculation_items.append(_issue("calculation_check", "warning", f"计算 warning 项 {warn_count} 个。"))
    if manual_count:
        calculation_items.append(_issue("calculation_check", "manual_review", f"人工复核项 {manual_count} 个。"))
    checklist_sections = [
        _section("一、计算结果状态", calculation_items or [_issue("calculation_check", "pass", "未发现硬性 fail。")]),
        _section("二、支撑布置合理性", support_items or [_issue("support_layout_quality", "pass", "支撑布置评分未发现主要问题。")]),
        _section("三、IFC 兼容性", ifc_items or [_issue("ifc_compatibility", "pass", "IFC 自检未发现主要兼容性问题。")]),
        _section("四、成果完整性与专项复核", warnings + missing),
        _section("五、正式出图阻断项", blocking or [_issue("formal_gate", "pass", "没有硬性阻断项。")]),
    ]
    status = "fail" if blocking else "warning" if warnings or missing else "pass"
    allowed = status == "pass"
    headline = "正式出图闸门通过。" if allowed else ("存在阻断项，当前成果不得作为正式施工图输出。" if blocking else "未发现硬性 fail，但存在需完善/复核项，暂不建议正式出图。")
    return FormalReportGate(
        status=status,
        allowed_for_official_issue=allowed,
        headline=headline,
        blocking_items=blocking,
        warning_items=warnings,
        missing_items=missing,
        checklist_sections=checklist_sections,
        summary={
            "fail": fail_count,
            "warning": warn_count,
            "manualReview": manual_count,
            "supportLayoutStatus": support_quality.status if support_quality else "missing",
            "supportLayoutScore": support_quality.score if support_quality else None,
            "supportCrossingCount": (support_quality.metrics or {}).get("supportCrossingCount") if support_quality else None,
            "ifcCompatibilityStatus": ifc_quality.status if ifc_quality else "missing",
            "ifcCompatibilityScore": ifc_quality.score if ifc_quality else None,
            "viewerProfileCount": len(ifc_quality.viewer_profiles) if ifc_quality else 0,
            "calculationContractCurrent": bool(latest and verify_current_calculation_contract(project, latest).get("current")),
            "calculationAssuranceStatus": (getattr(latest, "calculation_assurance", {}) or {}).get("status") if latest else "missing",
            "inputSnapshotHash": getattr(latest, "input_snapshot_hash", None) if latest else None,
            "resultHash": getattr(latest, "result_hash", None) if latest else None,
            "blockingCount": len(blocking),
            "warningCount": len(warnings),
            "missingCount": len(missing),
        },
    )
