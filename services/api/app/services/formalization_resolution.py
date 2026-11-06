from __future__ import annotations

from typing import Any, Iterable

from app.schemas.domain import FormalReportGate, Project, QualityGateIssue
from app.services.calculation_assurance import verify_current_calculation_contract


_CATEGORY_LABELS = {
    "calculation_contract": "计算合同与当前设计快照不一致",
    "calculation_assurance": "工业计算质量证据不完整",
    "calculation_baseline": "不可变计算基线缺失",
    "numerical_validity": "控制结果存在无效数值",
    "analysis_fidelity": "分析等级或参数来源未达到正式发行要求",
    "geotechnical_model": "岩土与地下水参数保证未闭合",
    "spatial_verification": "空间验证未闭合",
    "verification_matrix": "验证矩阵或外部基准未闭合",
    "statutory_workflow": "设计责任范围内法定证据未闭合",
    "support_layout_quality": "支撑布置合理性未闭合",
    "ifc_compatibility": "IFC兼容性证据未闭合",
    "stability_special": "地下水与稳定专项结果缺失",
    "report_data": "计算书图表数据不完整",
    "drawing_output": "施工图详图清单不完整",
    "calculation_check": "结构校核仍有失败或复核项",
    "shape_transfer_system": "异形转接传力体系未闭合",
    "shape_transfer_stage_analysis": "异形转接体系缺少逐阶段分析",
    "shape_transfer_detailing": "异形转接节点深化与审批未闭合",
    "formal_report": "尚未形成当前方案正式化计算结果",
}


def _issue_dict(issue: QualityGateIssue | dict[str, Any]) -> dict[str, Any]:
    if isinstance(issue, QualityGateIssue):
        return issue.model_dump(mode="json", by_alias=True)
    return dict(issue or {})


def _automatic_action(category: str, *, project: Project, latest: Any | None, issue: dict[str, Any]) -> dict[str, Any] | None:
    if category in {"calculation_contract", "calculation_assurance", "calculation_baseline", "numerical_validity", "stability_special", "report_data", "formal_report"}:
        return {
            "actionCode": "recalculate_current_design",
            "label": "按当前设计快照重建工况并完整复算",
            "description": "重新生成施工阶段、计算合同、结果哈希、质量保证包和结果目录。",
            "automatic": True,
        }
    if category == "calculation_check" and str(issue.get("severity") or "") == "fail":
        return {
            "actionCode": "auto_close_structural_checks",
            "label": "运行验算—优化—再验算闭环",
            "description": "在允许的体系与截面边界内自动搜索，仍不闭合时保留真实控制项。",
            "automatic": True,
        }
    if category == "support_layout_quality" and str(issue.get("severity") or "") == "manual_review":
        return {
            "actionCode": "refresh_support_quality",
            "label": "重新评估支撑布置质量",
            "description": "按当前支撑拓扑重新执行交叉、跨度、端点和传力连续性检查。",
            "automatic": True,
        }
    if category == "ifc_compatibility" and str(issue.get("severity") or "") == "manual_review":
        return {
            "actionCode": "refresh_ifc_compatibility",
            "label": "重新执行IFC兼容性自检",
            "description": "重建当前模型的实体、尺寸、材料、空间归属和查看器风险证据。",
            "automatic": True,
        }
    if category == "analysis_fidelity":
        assurance = dict(getattr(latest, "analysis_assurance", {}) or {}) if latest else {}
        if not assurance:
            return {
                "actionCode": "recalculate_current_design",
                "label": "重建分析等级与参数来源保证包",
                "description": "按当前设计快照复算，并生成分析域等级、参数来源和回退模型清单。",
                "automatic": True,
            }
        strict_blocks = list(assurance.get("strictBlocks") or [])
        if not strict_blocks:
            return {
                "actionCode": "refresh_analysis_assurance",
                "label": "刷新分析保证包",
                "description": "使用当前结果重新生成分析等级和条文追溯证据。",
                "automatic": True,
            }
    if category == "verification_matrix" and not bool(project.design_settings.require_external_benchmark_for_issue):
        return {
            "actionCode": "refresh_verification_matrix",
            "label": "生成内置验证矩阵",
            "description": "重新汇总单元测试、参考算例和运行时验证证据；不伪造外部软件证书。",
            "automatic": True,
        }
    return None


def _manual_action(category: str, issue: dict[str, Any]) -> dict[str, Any]:
    route = {
        "analysis_fidelity": "工程输入/参数来源",
        "geotechnical_model": "工程输入/地质与地下水",
        "spatial_verification": "计算验算/空间验证",
        "verification_matrix": "质量与追溯/验证矩阵",
        "statutory_workflow": "质量与追溯/设计审签",
        "support_layout_quality": "围护方案/支撑拓扑",
        "shape_transfer_system": "围护方案/转接体系",
        "shape_transfer_stage_analysis": "计算验算/施工阶段",
        "shape_transfer_detailing": "配筋深化/节点深化",
        "calculation_check": "计算验算/控制项",
        "drawing_output": "成果交付/图纸目录",
    }.get(category, "质量与追溯")
    return {
        "actionCode": f"manual:{category}",
        "label": issue.get("recommendation") or "由相应专业工程师确认并形成可追溯证据",
        "description": "系统不会自动填写勘察来源、外部基准证书、专业签署或专项设计结论。",
        "automatic": False,
        "targetPanel": route,
    }


def build_formalization_resolution_plan(
    project: Project,
    gate: FormalReportGate | None,
    *,
    latest: Any | None = None,
) -> dict[str, Any]:
    gate = gate or FormalReportGate(status="manual_review", headline="尚未评估正式化闸门。")
    groups: list[tuple[str, Iterable[QualityGateIssue]]] = [
        ("blocking", gate.blocking_items),
        ("missing", gate.missing_items),
        ("warning", gate.warning_items),
    ]
    rows: list[dict[str, Any]] = []
    automatic_by_code: dict[str, dict[str, Any]] = {}
    manual: list[dict[str, Any]] = []
    for source, issues in groups:
        for issue in issues:
            data = _issue_dict(issue)
            category = str(data.get("category") or "formal_report")
            action = _automatic_action(category, project=project, latest=latest, issue=data)
            if action is None:
                action = _manual_action(category, data)
                manual.append({**action, "category": category, "source": source, "issueId": data.get("id")})
            else:
                automatic_by_code.setdefault(str(action["actionCode"]), action)
            rows.append({
                "issueId": data.get("id"),
                "category": category,
                "title": _CATEGORY_LABELS.get(category, category.replace("_", " ")),
                "severity": data.get("severity"),
                "source": source,
                "message": data.get("message"),
                "recommendation": data.get("recommendation"),
                "automatic": bool(action.get("automatic")),
                "actionCode": action.get("actionCode"),
                "actionLabel": action.get("label"),
                "targetPanel": action.get("targetPanel"),
            })
    current_contract = bool(latest and verify_current_calculation_contract(project, latest).get("current"))
    return {
        "schema": "pitguard-formalization-resolution-plan-v1",
        "status": "closed" if gate.allowed_for_official_issue else "action_required",
        "currentCalculationContract": current_contract,
        "automaticActions": list(automatic_by_code.values()),
        "manualActions": manual,
        "items": rows,
        "automaticCount": len(automatic_by_code),
        "manualCount": len(manual),
        "blockingCount": len(gate.blocking_items),
        "warningCount": len(gate.warning_items),
        "missingCount": len(gate.missing_items),
        "boundary": "自动闭合只重建可审计计算与软件证据；勘察来源、外部基准、专业签署和专项工程判断必须由责任人确认。",
    }
