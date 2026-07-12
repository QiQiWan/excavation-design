from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.domain import Project
from app.version import SOFTWARE_VERSION


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


def _flatten_latest_checks(latest) -> list[dict[str, Any]]:
    if latest is None:
        return []
    checks: list[dict[str, Any]] = list(latest.checks or [])
    for stage in latest.stage_results or []:
        checks.extend(stage.checks or [])
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in checks:
        key = (
            str(item.get("ruleId") or item.get("rule_id") or ""),
            str(item.get("objectId") or item.get("object_id") or ""),
            str(item.get("status") or ""),
            str(item.get("calculatedValue") or item.get("calculated_value") or ""),
            str(item.get("message") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


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


def _module_completion_review(project: Project, latest) -> list[dict[str, Any]]:
    ret = project.retaining_system
    advanced = project.advanced_engineering or {}
    suite = advanced.get("latestSuite") if isinstance(advanced.get("latestSuite"), dict) else {}
    review = project.review_workflow.model_dump(mode="json", by_alias=True) if project.review_workflow else {}
    review_actors = [str(item.get("actor", "")).strip() for item in review.get("actions", []) if str(item.get("actor", "")).strip()]
    separation_valid = len(review_actors) == len(set(review_actors)) if review_actors else False
    latest_checks = _flatten_latest_checks(latest)
    fail_count = sum(str(item.get("status")) == "fail" for item in latest_checks)

    def row(module_id: str, name: str, owner: str, checks: list[tuple[str, bool, str]], *, blocking: bool = False) -> dict[str, Any]:
        complete = sum(1 for _, ok, _ in checks if ok)
        total = max(len(checks), 1)
        percent = round(100.0 * complete / total, 1)
        gaps = [{"item": label, "recommendation": recommendation} for label, ok, recommendation in checks if not ok]
        status = "pass" if percent >= 100.0 and not blocking else "fail" if blocking else "warning" if percent >= 50.0 else "gap"
        return {
            "id": module_id, "name": name, "ownerRole": owner, "completion": percent, "status": status,
            "completedItemCount": complete, "totalItemCount": total, "blocking": blocking,
            "gaps": gaps, "evidence": [label for label, ok, _ in checks if ok],
            "nextAction": gaps[0]["recommendation"] if gaps else "模块数据、计算与交付证据已形成。",
        }

    support_repair = ret.support_layout_repair if ret else None
    candidate_full = list(support_repair.candidate_full_calculations or []) if support_repair else []
    geometry_consistency = (latest.report_diagram_data or {}).get("geometryConsistency") if latest else None
    geometry_status = str((geometry_consistency or {}).get("status", "")) if isinstance(geometry_consistency, dict) else ""
    has_global_coupling = bool(latest and any(stage.global_coupled_result for stage in latest.stage_results))
    formal_gate = latest.formal_report_gate if latest else None
    return [
        row("M01", "项目与地勘输入", "designer", [
            ("项目控制参数", bool(project.design_settings), "完善项目控制参数和单位体系"),
            ("钻孔资料", bool(project.boreholes), "导入钻孔CSV/XLSX"),
            ("地层与物理力学参数", bool(project.strata), "补齐地层和关键土参数"),
        ]),
        row("M02", "三维地质模型", "designer", [
            ("地层面", bool(project.geological_model and project.geological_model.surfaces), "生成IDW地层面"),
            ("代表性剖面能力", bool(project.geological_model and project.geological_model.surfaces and project.excavation), "生成基坑轮廓后提取代表性剖面"),
            ("地质范围覆盖", bool(project.geological_model and not project.geological_model.warnings), "处理地质范围和低置信度警告"),
        ]),
        row("M03", "基坑几何与一致性", "designer", [
            ("闭合开挖轮廓", bool(project.excavation and project.excavation.segments), "绘制闭合基坑轮廓并生成边段"),
            ("坑顶/坑底标高", bool(project.excavation and project.excavation.depth > 0), "设置有效坑顶和坑底标高"),
            ("计算/三维/出图几何一致", bool(geometry_consistency and geometry_status == "pass"), "重新生成几何并执行几何哈希对账"),
        ], blocking=bool(geometry_consistency and geometry_status == "fail")),
        row("M04", "围护与支撑设计", "designer", [
            ("地下连续墙", bool(ret and ret.diaphragm_walls), "自动生成或导入围护墙"),
            ("围檩/冠梁", bool(ret and (ret.wale_beams or ret.crown_beams)), "生成围檩和冠梁"),
            ("支撑与临时立柱", bool(ret and ret.supports and ret.columns), "生成支撑、节点和临时立柱"),
            ("支撑净距和拓扑质量", bool(latest and latest.support_layout_quality and latest.support_layout_quality.status != "fail"), "修复支撑贴墙、交叉、长跨和传力路径问题"),
        ], blocking=bool(latest and latest.support_layout_quality and latest.support_layout_quality.status == "fail")),
        row("M05", "A/B/C方案优化", "designer", [
            ("三个整体候选", bool(support_repair and len(support_repair.candidates or []) >= 3), "生成A/B/C整体候选方案"),
            ("三个候选完整计算", len(candidate_full) >= 3, "并行运行A/B/C完整计算"),
            ("已选择整体方案", bool(support_repair and support_repair.selected_candidate_id), "采用推荐或人工选定的整体方案"),
            ("候选结果缓存", any(bool(row.get("inputHash")) for row in candidate_full), "重新执行并行比选以生成输入哈希缓存"),
        ]),
        row("M06", "施工阶段与结构计算", "calculator", [
            ("施工阶段", bool(project.calculation_cases and project.calculation_cases[-1].stages), "生成施工阶段和支撑激活路径"),
            ("最新计算结果", bool(latest), "运行完整计算校核"),
            ("墙-围檩-支撑联立", has_global_coupling, "运行全局联立计算"),
            ("无硬性工程失败", bool(latest and fail_count == 0), "处理最新计算中的硬性失败"),
        ], blocking=fail_count > 0),
        row("M07", "配筋与加工深化", "detailer", [
            ("墙体分区配筋", bool(ret and all(w.reinforcement and w.design_results for w in ret.diaphragm_walls)), "生成并应用墙体分区配筋"),
            ("支撑/围檩/节点配筋", bool(ret and any(s.reinforcement for s in ret.supports)), "生成支撑、围檩和节点配筋"),
            ("逐根钢筋和BBS数据基础", bool(ret and ret.rebar_design_scheme and (ret.rebar_design_scheme.get("summary") or {}).get("wallZoneCount", 0)), "先应用分区配筋方案，再运行钢筋深化包生成逐根钢筋和BBS"),
            ("构造协调几何写回", bool(advanced.get("detailGeometryPatches")) or not bool(suite.get("collisions", {}).get("summary", {}).get("warningCount")), "应用构造协调候选并重新执行碰撞检查"),
        ]),
        row("M08", "节点、碰撞与吊装深化", "detailer", [
            ("节点局部筛查", bool(suite.get("nodeLocal")), "运行节点局部复核"),
            ("碰撞与净距", bool(suite.get("collisions")), "运行碰撞、净距和钢筋拥挤检查"),
            ("吊机与场地路线", bool(advanced.get("craneSitePlan")) or bool(advanced.get("latestCraneLogistics")), "配置场地边界、道路、禁入区和吊机站位"),
            ("施工顺序与停检点", bool(ret and project.calculation_cases), "生成施工顺序和停检点台账"),
        ]),
        row("M09", "出图规则与CAD/PDF", "publisher", [
            ("出图规则集", bool(project.drawing_rule_set), "选择或优化出图规则集"),
            ("企业CAD模板", bool(project.cad_template), "配置企业图框、字体、图层和签审栏"),
            ("正式图纸计划", bool(project.drawing_rule_set and project.drawing_rule_set.get("id")), "生成图纸计划并校验必需图种"),
            ("图纸派生几何有效", not bool(project.drawing_rule_set.get("derivedGeometryStale")) if project.drawing_rule_set else False, "协调几何变化后重新生成图纸计划"),
        ]),
        row("M10", "BIM与模型交付", "publisher", [
            ("IFC模型可生成", bool(ret and ret.diaphragm_walls), "完成围护体系后导出IFC"),
            ("IFC兼容性检查", bool(latest and latest.ifc_compatibility), "运行IFC兼容性检查"),
            ("稳定对象ID和几何哈希", bool(geometry_consistency), "执行模型/图纸/IFC几何对账"),
        ]),
        row("M11", "监测、校准与审签", "reviewer", [
            ("监测数据入口", bool(project.monitoring_records) or bool(project.design_settings.monitoring_calibration_enabled), "导入监测数据或明确关闭反演"),
            ("参数反演记录", bool(project.calibration_runs) or not bool(project.monitoring_records), "监测数据存在时运行参数反演"),
            ("四级审签流程", bool(review), "启动设计—校核—审核—批准流程"),
            ("岗位分离", separation_valid, "使用不同人员完成设计、校核、审核和批准"),
        ]),
        row("M12", "正式发行与归档", "publisher", [
            ("无正式发行阻断", bool(formal_gate and not formal_gate.blocking_items), "处理正式发行阻断项"),
            ("当前快照批准", bool(review.get("status") == "approved"), "完成当前设计快照四级批准"),
            ("当前施工版修订", any(r.issue_status == "construction" for r in project.drawing_revisions), "创建绑定当前快照的施工版修订"),
            ("正式发行闸门允许", bool(formal_gate and formal_gate.allowed_for_official_issue), "完成计算、深化、审签和修订闭环"),
        ], blocking=bool(formal_gate and formal_gate.blocking_items)),
    ]


def evaluate_project_assurance(project: Project) -> dict[str, Any]:
    latest = _latest_result(project)
    checks = _flatten_latest_checks(latest)
    failures = [c for c in checks if c.get("status") == "fail"]
    manual = [c for c in checks if c.get("status") == "manual_review"]
    engineering_status = _engineering_status(checks)
    module_review = _module_completion_review(project, latest)
    module_overall = round(sum(float(item["completion"]) for item in module_review) / max(len(module_review), 1), 1)
    module_blocking = sum(bool(item.get("blocking")) for item in module_review)

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
    check_summary = _check_counts(checks)
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
        "softwareVersion": SOFTWARE_VERSION,
        "capabilityCompleteness": capability,
        "completionPercent": capability,
        "moduleOverallCompleteness": module_overall,
        "moduleBlockingCount": module_blocking,
        "moduleCompletionReview": module_review,
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
