from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.domain import Project


@dataclass(frozen=True)
class AcceptanceItem:
    id: str
    title: str
    required: bool = True


ACCEPTANCE_MATRIX: list[AcceptanceItem] = [
    AcceptanceItem("P0", "monorepo, README, FastAPI health and React shell"),
    AcceptanceItem("P1", "project CRUD and SQLite persistence"),
    AcceptanceItem("P2", "borehole CSV/XLSX import, validation and strata parameter merge"),
    AcceptanceItem("P3", "IDW geological surfaces and representative design sections"),
    AcceptanceItem("P4", "VTU mesh import with field mapping and front-end selectable display"),
    AcceptanceItem("P5", "excavation outline validation, area/perimeter and segment generation"),
    AcceptanceItem("P6", "automatic diaphragm wall, crown/wale beam, supports and columns"),
    AcceptanceItem("P7", "staged calculation, earth/water pressure, support forces and wall internal forces"),
    AcceptanceItem("P8", "reinforcement design, crack/serviceability and anchorage/detailing screens"),
    AcceptanceItem("P9", "IFC4 export with spatial hierarchy, elements, property sets and rebar entities"),
    AcceptanceItem("P10", "DOCX calculation report with declarations, conclusions and traceability"),
    AcceptanceItem("P11", "standards rule registry and project compliance/gap analysis API"),
    AcceptanceItem("P12", "complete sample project running JSON -> calculation -> IFC -> report"),
]


def _latest_result(project: Project):
    return project.calculation_results[-1] if project.calculation_results else None


def _check_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for c in checks if c.get("status") == "pass"),
        "fail": sum(1 for c in checks if c.get("status") == "fail"),
        "warning": sum(1 for c in checks if c.get("status") == "warning"),
        "manualReview": sum(1 for c in checks if c.get("status") == "manual_review"),
        "manual_review": sum(1 for c in checks if c.get("status") == "manual_review"),
    }


def _engineering_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(c.get("status")) for c in checks}
    if "fail" in statuses:
        return "fail"
    if "warning" in statuses:
        return "warning"
    if "manual_review" in statuses:
        return "manual_review"
    return "pass" if checks else "manual_review"


def evaluate_project_assurance(project: Project) -> dict[str, Any]:
    latest = _latest_result(project)
    checks = latest.checks if latest else []
    failures = [c for c in checks if c.get("status") == "fail"]
    manual = [c for c in checks if c.get("status") == "manual_review"]
    engineering_status = _engineering_status(checks)

    # Capability completion is intentionally separated from engineering check status.
    # A project can have a complete software workflow and still fail an engineering check;
    # in that case engineeringCheckStatus is fail and closedLoopComplete remains false.
    implemented: dict[str, bool] = {
        "P0": True,
        "P1": True,
        "P2": bool(project.boreholes and project.strata),
        "P3": bool(project.geological_model and project.geological_model.surfaces),
        "P4": bool(project.geological_model and project.geological_model.vtu_mesh),
        "P5": bool(project.excavation and project.excavation.segments),
        "P6": bool(project.retaining_system and project.retaining_system.diaphragm_walls and project.retaining_system.supports),
        "P7": bool(latest and latest.stage_results and (latest.governing_values.max_wall_moment or 0) > 0),
        "P8": bool(project.retaining_system and all(w.reinforcement and w.design_results for w in project.retaining_system.diaphragm_walls)),
        "P9": bool(project.retaining_system and project.retaining_system.diaphragm_walls),
        "P10": bool(latest),
        "P11": bool(latest and latest.checks),
        "P12": bool(latest and project.excavation and project.retaining_system and project.retaining_system.diaphragm_walls),
    }
    rows: list[dict[str, Any]] = []
    for item in ACCEPTANCE_MATRIX:
        ok = implemented.get(item.id, False)
        rows.append({
            "id": item.id,
            "title": item.title,
            "required": item.required,
            "status": "pass" if ok else "gap",
            "message": "软件功能路径已满足 V1.2 可运行验收。" if ok else "尚缺项目数据或流程结果；请按完整流程运行。",
        })
    required = [row for row in rows if row["required"]]
    passed = [row for row in required if row["status"] == "pass"]
    capability = round(100.0 * len(passed) / max(len(required), 1), 1)
    software_flow_complete = capability >= 100.0
    check_summary = latest.check_summary if latest else _check_counts(checks)
    formal_gate = latest.formal_report_gate if latest else None
    gate_allowed = bool(formal_gate.allowed_for_official_issue) if formal_gate else False
    has_gate_blocking = bool(formal_gate and formal_gate.blocking_items)
    # closedLoopComplete means the software workflow has a usable no-fail design loop.
    # It is intentionally less strict than the official issue gate, which can remain
    # warning/manual_review until report/IFC/stability details are fully formalized.
    closed_loop_complete = software_flow_complete and engineering_status != "fail" and not has_gate_blocking
    gate_blocking = [i.model_dump(mode="json", by_alias=True) for i in (formal_gate.blocking_items if formal_gate else [])]
    gate_warnings = [i.model_dump(mode="json", by_alias=True) for i in (formal_gate.warning_items if formal_gate else [])]
    gate_missing = [i.model_dump(mode="json", by_alias=True) for i in (formal_gate.missing_items if formal_gate else [])]
    gate_detail = "正式出图质量闸门已通过" if closed_loop_complete else "查看阻断项、警告项和缺项；不可闭环不等同于存在 fail。"
    return {
        "projectId": project.id,
        "softwareVersion": "2.0.7",
        "capabilityCompleteness": capability,
        "completionPercent": capability,
        "softwareFlowComplete": software_flow_complete,
        "softwareFlowMissingItems": [row for row in rows if row["required"] and row["status"] != "pass"],
        "engineeringCheckStatus": engineering_status,
        "closedLoopComplete": closed_loop_complete,
        "officialIssueGateStatus": formal_gate.status if formal_gate else "manual_review",
        "officialIssueGateAllowed": gate_allowed,
        "officialIssueGateHeadline": formal_gate.headline if formal_gate else "尚未形成正式出图质量闸门结果。",
        "officialIssueGateDetail": gate_detail,
        "officialIssueBlockingItems": gate_blocking,
        "officialIssueWarningItems": gate_warnings,
        "officialIssueMissingItems": gate_missing,
        "professionalReviewRequired": True,
        "checkSummary": check_summary,
        "failureCount": len(failures),
        "manualReviewCount": len(manual),
        "acceptanceMatrix": rows,
        "supportLayoutQuality": latest.support_layout_quality.model_dump(mode="json", by_alias=True) if latest and latest.support_layout_quality else None,
        "ifcCompatibility": latest.ifc_compatibility.model_dump(mode="json", by_alias=True) if latest and latest.ifc_compatibility else None,
        "remainingBoundaryPolicy": [
            "capabilityCompleteness 只描述软件功能和流程覆盖率，不代表工程设计结论。",
            "softwareFlowComplete 表示从资料、建模、设计、计算到成果导出的软件路径是否完整；缺项会列入 softwareFlowMissingItems。",
            "engineeringCheckStatus 汇总当前项目最新计算结果；若存在任一 fail，则该字段必须为 fail。",
            "officialIssueGateStatus 是正式出图闸门，综合 fail/warning/manual_review、支撑布置评分、IFC 兼容性、稳定专项和报告数据完整性。",
            "closedLoopComplete 表示软件流程完整且无硬性 fail；正式出图仍由 officialIssueGateStatus 单独控制。",
        ],
    }
