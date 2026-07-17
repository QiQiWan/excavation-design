from __future__ import annotations

from typing import Any, Callable

from app.schemas.domain import Project
from app.services.collision_service import evaluate_model_collisions
from app.services.coordination_optimizer import build_coordination_optimization
from app.services.deepening_readiness import group_deepening_checks
from app.services.enterprise_library import resolve_enterprise_library, select_node_template, validate_enterprise_library
from app.services.node_local_analysis import evaluate_node_local_response
from app.services.node_submodel import build_calculix_input_deck, build_node_submodels
from app.services.rebar_constructability import build_rebar_constructability
from app.services.rebar_detailing import build_rebar_detailing
from app.version import SOFTWARE_VERSION

Progress = Callable[[int, str], None]


def _status_rank(value: str) -> int:
    return {"pass": 0, "preliminary": 1, "manual_review": 2, "warning": 3, "fail": 4}.get(str(value), 2)


def _compact_checks(rows: list[dict[str, Any]], maximum: int = 240) -> list[dict[str, Any]]:
    ordered = sorted(
        [dict(row) for row in rows],
        key=lambda row: (-_status_rank(str(row.get("status") or "manual_review")), str(row.get("checkId") or row.get("ruleId") or "")),
    )
    output: list[dict[str, Any]] = []
    for row in ordered[:maximum]:
        output.append({
            "id": row.get("checkId") or row.get("ruleId") or row.get("id"),
            "category": row.get("category") or row.get("type"),
            "status": row.get("status"),
            "hostId": row.get("hostId") or row.get("objectId") or row.get("objectA"),
            "hostCode": row.get("hostCode") or row.get("objectCode") or row.get("objectA"),
            "message": row.get("message"),
            "recommendedAction": row.get("recommendedAction"),
            "calculatedValue": row.get("calculatedValue"),
            "limitValue": row.get("limitValue"),
            "unit": row.get("unit"),
        })
    return output


def _node_template_assignments(project: Project, local: dict[str, Any]) -> list[dict[str, Any]]:
    support_by_code = {
        str(row.code): row
        for row in (project.retaining_system.supports if project.retaining_system else [])
    }
    rows: list[dict[str, Any]] = []
    for node in list(local.get("nodes") or []):
        support = support_by_code.get(str(node.get("supportCode") or ""))
        section_type = str(getattr(support, "section_type", "rc_rectangular") or "rc_rectangular")
        force = abs(float(node.get("designForceKn") or 0.0))
        template = select_node_template(project, section_type=section_type, axial_force_kn=force)
        rows.append({
            "nodeId": node.get("nodeId"),
            "nodeCode": node.get("nodeCode"),
            "supportCode": node.get("supportCode"),
            "sectionType": section_type,
            "designForceKn": force,
            "templateId": template.get("id") if template else None,
            "templateName": template.get("name") if template else None,
            "drawingRef": template.get("drawingRef") if template else None,
            "status": "pass" if template else "manual_review",
            "message": "已匹配企业节点模板。" if template else "企业节点库未覆盖该截面或轴力区间，需专项节点设计。",
        })
    return rows


def build_p3_detailing_closure(
    project: Project,
    *,
    mode: str = "balanced",
    progress: Progress | None = None,
    top_node_count: int = 8,
) -> dict[str, Any]:
    """Build the P3-3 reinforcement and node-detailing closure.

    Full individual-bar geometry, embedded hardware, node submodels and
    coordination candidates are returned for artifact storage.  The project
    snapshot should retain only ``compact`` to keep the workspace bounded.
    """
    if project.retaining_system is None:
        raise ValueError("缺少围护结构，无法执行节点与钢筋深化。")
    if not project.calculation_results:
        raise ValueError("缺少当前施工阶段计算结果，无法执行节点与钢筋深化。")
    if not getattr(project.retaining_system, "rebar_design_scheme", None):
        raise ValueError("请先生成并应用配筋方案。")

    if progress:
        progress(8, "读取企业标准、节点和钢筋组合库")
    enterprise_validation = validate_enterprise_library(project)
    enterprise = resolve_enterprise_library(project)

    if progress:
        progress(18, "生成逐根钢筋、套筒、锚固和预埋件深化数据")
    detailing = build_rebar_detailing(project, mode=mode)
    scheme = dict(detailing.get("designScheme") or project.retaining_system.rebar_design_scheme or {})

    if progress:
        progress(46, "执行锚固、搭接、拥挤度和机械连接校核")
    constructability = build_rebar_constructability(project, scheme)

    if progress:
        progress(58, "执行节点局部受力与企业节点模板匹配")
    local_nodes = evaluate_node_local_response(project)
    node_templates = _node_template_assignments(project, local_nodes)

    if progress:
        progress(68, "生成高风险节点局部子模型和求解器输入")
    node_submodels = build_node_submodels(project, top_n=max(1, min(int(top_node_count), 20)), local_result=local_nodes)
    solver_decks = {
        str(row.get("solverDeckFilename") or f"node_submodels/{index + 1}.inp"): build_calculix_input_deck(row)
        for index, row in enumerate(node_submodels.get("submodels") or [])
    }

    if progress:
        progress(80, "执行预埋件、构件与钢筋空间碰撞检查")
    collisions = evaluate_model_collisions(project, mode=mode)
    coordination = build_coordination_optimization(project, mode=mode, detailing=detailing)

    deep = dict(detailing.get("deepDetailing") or {})
    deep_checks = list((deep.get("nodeHardware") or {}).get("checks") or [])
    embedded_checks = list(deep.get("embeddedItemCollisionChecks") or [])
    collision_checks = list(collisions.get("collisions") or [])
    for row in collision_checks:
        row.setdefault("category", "collision")
        row.setdefault("recommendedAction", "按碰撞对象调整构件、钢筋或预埋件几何后重新运行 P3。")
    all_checks = list(constructability.get("checks") or []) + deep_checks + embedded_checks + collision_checks
    fail_count = sum(str(row.get("status")) == "fail" for row in all_checks)
    warning_count = sum(str(row.get("status")) in {"warning", "manual_review"} for row in all_checks)
    unmatched_nodes = sum(row.get("templateId") is None for row in node_templates)
    status = "fail" if fail_count else "warning" if warning_count or unmatched_nodes else "pass"
    blocking_groups = group_deepening_checks(all_checks, statuses={"fail"}, source="p3_detailing")
    warning_groups = group_deepening_checks(all_checks, statuses={"warning", "manual_review"}, source="p3_detailing")
    resolution_guide = [
        {
            "priority": index + 1,
            "reasonCode": row.get("reasonCode"),
            "title": row.get("title"),
            "affectedCount": row.get("count"),
            "objects": row.get("objects"),
            "action": row.get("requiredAction"),
            "targetStage": row.get("targetStage"),
        }
        for index, row in enumerate([*blocking_groups, *warning_groups][:16])
    ]

    compact = {
        "version": SOFTWARE_VERSION,
        "status": status,
        "mode": mode,
        "enterpriseLibrary": {
            "libraryId": (enterprise.get("library") or {}).get("libraryId"),
            "libraryVersion": (enterprise.get("library") or {}).get("libraryVersion"),
            "standardTemplateId": (enterprise.get("selection") or {}).get("localStandardTemplateId"),
            "validationStatus": enterprise_validation.get("status"),
        },
        "summary": {
            "barMarkCount": int((detailing.get("summary") or {}).get("barMarkCount") or 0),
            "individualBarCount": int((detailing.get("summary") or {}).get("individualBarCount") or 0),
            "couplerCount": int((deep.get("summary") or {}).get("couplerCount") or 0),
            "embeddedItemCount": len((deep.get("nodeHardware") or {}).get("embeddedItems") or []),
            "embeddedCollisionCheckCount": len(embedded_checks),
            "hardCollisionCount": int((collisions.get("summary") or {}).get("hardCollisionCount") or 0),
            "coordinationIssueCount": len(coordination.get("issues") or []),
            "nodeCount": int((local_nodes.get("summary") or {}).get("nodeCount") or 0),
            "nodeSubmodelCount": int((node_submodels.get("summary") or {}).get("submodelCount") or 0),
            "nonlinearFERequiredCount": int((local_nodes.get("summary") or {}).get("nonlinearFERequiredCount") or 0),
            "unmatchedEnterpriseNodeCount": unmatched_nodes,
            "failCount": fail_count,
            "warningCount": warning_count,
            "controllingCheckCount": min(len(all_checks), 240),
            "omittedCheckCount": max(0, len(all_checks) - 240),
            "blockingGroupCount": len(blocking_groups),
            "warningGroupCount": len(warning_groups),
        },
        "controllingChecks": _compact_checks(all_checks),
        "blockingGroups": blocking_groups,
        "warningGroups": warning_groups,
        "resolutionGuide": resolution_guide,
        "nodeTemplateAssignments": node_templates[:80],
        "boundary": "自动深化结果用于工程审查和施工图草案。复杂节点、焊接工艺、重型吊装、套筒产品、钢筋可穿入性和高利用率局部模型仍需项目专项校审。",
    }
    return {
        "compact": compact,
        "full": {
            "version": SOFTWARE_VERSION,
            "enterpriseLibrary": enterprise,
            "enterpriseValidation": enterprise_validation,
            "rebarDetailing": detailing,
            "constructability": constructability,
            "localNodeResponse": local_nodes,
            "nodeTemplateAssignments": node_templates,
            "nodeSubmodels": node_submodels,
            "solverDecks": solver_decks,
            "collisions": collisions,
            "coordinationOptimization": coordination,
        },
    }
