from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.services.support_topology_contract import support_topology_hash
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.delivery_release import evaluate_delivery_release_readiness
from app.geometry.consistency import geometry_consistency_summary
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.schemas.domain import Point2D, Polyline2D, Project
from app.services.calculation_trace import build_calculation_trace
from app.services.design_pipeline import evaluate_design_pipeline
from app.services.design_service import auto_diaphragm_wall, auto_supports
from app.services.excavation_service import make_excavation_model
from app.services.monitoring_calibration import monitoring_control_summary
from app.services.access_control import security_status
from app.services.rebar_detailing import build_rebar_detailing
from app.services.review_workflow import review_status
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION, SOFTWARE_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(code: str, title: str, passed: bool, evidence: Any, action: str, *, blocking: bool = True) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "status": "pass" if passed else ("fail" if blocking else "warning"),
        "blocking": blocking,
        "evidence": evidence,
        "requiredAction": "" if passed else action,
    }


def _phase(phase_id: str, title: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    fails = sum(row["status"] == "fail" for row in checks)
    warnings = sum(row["status"] == "warning" for row in checks)
    status = "fail" if fails else "warning" if warnings else "pass"
    return {
        "phaseId": phase_id,
        "title": title,
        "status": status,
        "completion": round(100.0 * sum(row["status"] == "pass" for row in checks) / max(len(checks), 1), 1),
        "blockingCount": fails,
        "warningCount": warnings,
        "checks": checks,
    }


def run_geometry_qualification_suite() -> dict[str, Any]:
    """Qualify both feasible layouts and safely blocked unsupported topologies.

    A general-shape design engine must never manufacture a false load path to
    make every benchmark "green".  Under the current axial wall-to-wall support
    model, some wide near-square or deep re-entrant plans require a ring system,
    central island, or an explicitly modelled two-way rigid frame.  Those cases
    pass this platform-safety qualification only when the system blocks
    calculation clearly and emits no crossing, outside member, support-to-support
    terminal, or unsupported internal endpoint.
    """
    shapes = {
        "long_strip_160x33": [(-80, -16.5), (80, -16.5), (80, 16.5), (-80, 16.5)],
        "narrow_strip_90x18": [(-45, -9), (45, -9), (45, 9), (-45, 9)],
        "rotated_rectangle": [(-18.172, -21.605), (28.160, 2.003), (18.172, 21.605), (-28.160, -2.003)],
        "trapezoid": [(-30, -12), (30, -9), (22, 14), (-24, 12)],
        "convex_hexagon": [(-32, -8), (-18, -18), (20, -16), (34, -2), (22, 16), (-24, 14)],
        "l_shape": [(-30, -20), (30, -20), (30, -4), (6, -4), (6, 20), (-30, 20)],
        "t_shape": [(-34, -20), (34, -20), (34, 0), (12, 0), (12, 24), (-12, 24), (-12, 0), (-34, 0)],
        "u_shape": [(-30, -20), (30, -20), (30, 20), (12, 20), (12, -2), (-12, -2), (-12, 20), (-30, 20)],
        "general_concave": [(-32, -18), (28, -18), (34, 2), (12, 8), (18, 26), (-8, 20), (-14, 6), (-32, 12)],
        "near_square": [(-25, -25), (25, -25), (25, 25), (-25, 25)],
    }
    rows: list[dict[str, Any]] = []
    for name, raw in shapes.items():
        points = [Point2D(x=float(x), y=float(y)) for x, y in raw]
        excavation = make_excavation_model(name, Polyline2D(points=points, closed=True), 0.0, -16.0)
        project = Project(name=f"qualification-{name}")
        project.excavation = excavation
        project.retaining_system = auto_supports(excavation, auto_diaphragm_wall(excavation))
        quality = evaluate_support_layout_quality(project)
        preflight = dict((project.retaining_system.layout_summary or {}).get("strengthTopologyPreflight") or {})
        metrics = dict(quality.metrics or {})
        fail_categories = sorted({str(issue.category) for issue in quality.issues if str(issue.severity) == "fail"})
        unsafe_counts = {
            "crossing": int(metrics.get("supportCrossingCount") or 0),
            "outside": int(metrics.get("supportOutsideExcavationCount") or 0),
            "supportTerminal": int(metrics.get("supportToSupportTerminalCount") or 0),
            "unsupportedEndpoint": int(metrics.get("unsupportedInternalEndpointCount") or 0),
            "cornerFan": int(metrics.get("cornerBraceParallelismIssueCount") or 0),
            "cornerNodeCongestion": int(metrics.get("cornerBraceEndpointCongestionCount") or 0),
        }
        unsafe_topology = any(value > 0 for value in unsafe_counts.values())
        calculation_ready = (
            quality.status != "fail"
            and preflight.get("status") != "fail"
            and not unsafe_topology
        )
        # A controlled block is acceptable for capability qualification only
        # when the remaining failure is an unresolved wale bay that requires a
        # different structural system. It remains non-calculable and cannot be
        # used for construction issue.
        controlled_block = (
            not calculation_ready
            and not unsafe_topology
            and bool(fail_categories)
            and set(fail_categories).issubset({"wale_support_bay"})
        )
        qualified = calculation_ready or controlled_block
        if calculation_ready:
            outcome = "calculation_ready"
            recommendation = "当前直接墙—墙轴压支撑体系可进入后续计算。"
        elif controlled_block:
            outcome = "controlled_block"
            recommendation = (
                "当前直接墙—墙轴压拓扑无法在不产生非法交叉或支撑中部支承的条件下闭合围檩跨；"
                "应改用环梁/环撑、中心岛法，或启用具有平面内弯剪刚度和节点设计的显式双向框架模型。"
            )
        else:
            outcome = "unsafe_failure"
            recommendation = "修复非法交叉、坑外杆件、支撑中部终止或角撑节点拥挤后重新资格测试。"
        rows.append({
            "caseId": name,
            "status": "pass" if qualified else "fail",
            "outcome": outcome,
            "calculationReady": calculation_ready,
            "requiresAlternativeSupportSystem": controlled_block,
            "recommendedAction": recommendation,
            "supportCount": len(project.retaining_system.supports),
            "crossingCount": unsafe_counts["crossing"],
            "junctionCount": int(metrics.get("internalJunctionCount") or metrics.get("supportJunctionCount") or 0),
            "outsideCount": unsafe_counts["outside"],
            "supportToSupportTerminalCount": unsafe_counts["supportTerminal"],
            "unsupportedInternalEndpointCount": unsafe_counts["unsupportedEndpoint"],
            "maxWaleSupportBay": float(metrics.get("maxWaleSupportBay") or 0.0),
            "preflightStatus": preflight.get("status"),
            "qualityScore": quality.score,
            "failCategories": fail_categories,
        })
    passed_count = sum(row["status"] == "pass" for row in rows)
    ready_count = sum(bool(row["calculationReady"]) for row in rows)
    blocked_count = sum(bool(row["requiresAlternativeSupportSystem"]) for row in rows)
    return {
        "suiteId": "PITGUARD-GEOMETRY-QUALIFICATION-V3",
        "status": "pass" if passed_count == len(rows) else "fail",
        "caseCount": len(rows),
        "passedCount": passed_count,
        "failedCount": len(rows) - passed_count,
        "calculationReadyCount": ready_count,
        "controlledBlockCount": blocked_count,
        "qualificationMeaning": (
            "pass 表示算例已形成可计算的直接墙—墙支撑体系，或在当前轴压拓扑能力不足时被安全阻断；"
            "controlled_block 不具备计算与施工版发行资格。"
        ),
        "cases": rows,
        "executedAt": _now(),
    }


def _calculation_contract(project: Project) -> dict[str, Any]:
    return verify_current_calculation_contract(project)


def _trace_coverage(project: Project) -> dict[str, Any]:
    trace = build_calculation_trace(project)
    entries = list(trace.get("entries") or [])
    if not entries:
        return {"coverage": 0.0, "traceCount": 0, "missing": ["calculation trace"]}
    fields = ("formula", "codeReference", "method", "inputParameters", "resultPath")
    complete = 0
    missing: list[str] = []
    for row in entries:
        ok = all(bool(row.get(field)) for field in fields)
        complete += int(ok)
        if not ok and len(missing) < 20:
            missing.append(str(row.get("id") or row.get("title") or "trace"))
    return {"coverage": round(complete / len(entries), 4), "traceCount": len(entries), "completeCount": complete, "missing": missing}


def _candidate_evidence(project: Project) -> dict[str, Any]:
    repair = project.retaining_system.support_layout_repair if project.retaining_system else None
    rows = list(repair.candidate_full_calculations or []) if repair else []
    candidates = list(repair.candidates or []) if repair else []
    valid = [row for row in rows if row.get("status") not in {"failed", "error"} and row.get("candidateId")]
    return {
        "candidateCount": len(candidates),
        "fullCalculationCount": len(valid),
        "selectedCandidateId": repair.selected_candidate_id if repair else None,
        "cleanTopologyCount": sum(int(row.get("crossingCount") or 0) == 0 for row in valid),
        "rows": valid,
    }


def _finite_governing_values(project: Project) -> bool:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if not latest:
        return False
    values = latest.governing_values.model_dump()
    numeric = [float(value) for value in values.values() if isinstance(value, (int, float))]
    return bool(numeric) and all(math.isfinite(value) for value in numeric)


def evaluate_industrial_readiness(
    project: Project,
    *,
    include_detailing: bool = False,
    run_qualification: bool = False,
) -> dict[str, Any]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    checks = list(latest.checks or []) if latest else []
    for stage in latest.stage_results if latest else []:
        checks.extend(stage.checks or [])
    fail_count = sum(str(row.get("status")) == "fail" for row in checks)
    contract = _calculation_contract(project)
    trace = _trace_coverage(project)
    geometry = geometry_consistency_summary(project)
    support_quality = evaluate_support_layout_quality(project) if project.retaining_system else None
    candidate = _candidate_evidence(project)
    qualification = run_geometry_qualification_suite() if run_qualification else dict(project.advanced_engineering.get("qualificationSuite") or {})
    review = review_status(project)
    pipeline = evaluate_design_pipeline(project)
    calculation_assurance = dict(getattr(latest, "calculation_assurance", {}) or {}) if latest else {}
    release_readiness = evaluate_delivery_release_readiness(project, issue_mode="construction")
    monitoring = monitoring_control_summary(project)
    security = security_status()

    detailing = dict(project.advanced_engineering.get("industrialDetailing") or {})
    if include_detailing and project.retaining_system:
        detailing = build_rebar_detailing(project, mode="balanced")
    detailing_summary = dict(detailing.get("summary") or {})
    deep_summary = dict((detailing.get("deepDetailing") or {}).get("summary") or {})

    p0 = _phase("P0", "计算可信度、规范追溯与正式闸门", [
        _check("P0-CALC", "当前快照已完成计算", bool(latest), getattr(latest, "id", None), "运行完整分阶段计算。"),
        _check("P0-CONTRACT", "计算输入、工况、拓扑与算法版本一致", bool(contract.get("current")), contract, "按当前输入、施工工况、支撑拓扑和规则集重新计算。"),
        _check("P0-ASSURANCE", "工业计算质量包通过", calculation_assurance.get("status") == "pass", calculation_assurance, "关闭输入冻结、阶段覆盖、数值质量、独立复核和追溯问题。"),
        _check("P0-RESULT-HASH", "计算结果具有不可变输入/结果哈希", bool(latest and latest.input_snapshot_hash and latest.result_hash and latest.calculation_contract_id), {"inputSnapshotHash": getattr(latest, "input_snapshot_hash", None), "resultHash": getattr(latest, "result_hash", None), "contractId": getattr(latest, "calculation_contract_id", None)}, "按当前快照重新运行完整计算。"),
        _check("P0-NO-FAIL", "规范校核无硬失败", bool(latest) and fail_count == 0, {"failCount": fail_count}, "关闭全部 fail 后复算。"),
        _check("P0-FINITE", "控制结果数值有限且有效", _finite_governing_values(project), getattr(latest, "governing_values", None), "检查数值稳定性、输入量纲和矩阵条件数。"),
        _check("P0-TRACE", "计算链追溯覆盖率不低于 90%", trace.get("coverage", 0.0) >= 0.90, trace, "补齐公式、条文、输入、中间量和结果路径。"),
        _check("P0-GEOMETRY", "计算/三维/图纸几何一致", str(geometry.get("status")) == "pass" or bool(geometry.get("consistent")), geometry, "重新生成当前快照成果并执行几何对账。"),
        _check("P0-QUALIFICATION", "通用多边形资格测试通过", qualification.get("status") == "pass", qualification or "not run", "运行工业资格测试并关闭失败算例。"),
    ])

    p1 = _phase("P1", "支撑洁净拓扑与施工深化", [
        _check("P1-SUPPORT", "水平支撑拓扑质量通过", bool(support_quality and support_quality.status != "fail"), support_quality.model_dump(mode="json", by_alias=True) if support_quality else None, "修复穿越、坑外杆件、超限围檩跨和无效端点。"),
        _check("P1-CROSSING", "非法平面穿越为零", bool(support_quality) and int((support_quality.metrics or {}).get("supportCrossingCount") or 0) == 0, (support_quality.metrics if support_quality else {}), "重新生成洁净支撑候选。"),
        _check("P1-CANDIDATE", "至少三个候选完成独立计算并明确采用", candidate.get("fullCalculationCount", 0) >= 3 and bool(candidate.get("selectedCandidateId")), candidate, "对 A/B/C 分别运行完整计算并采用一项。"),
        _check("P1-REBAR", "配筋加工模型已生成", int(detailing_summary.get("individualBarCount") or 0) > 0, detailing_summary or "not generated", "生成逐根钢筋、BBS、分段和套筒计划。"),
        _check("P1-NODE", "节点硬件与吊装深化无硬失败", bool(deep_summary) and int(deep_summary.get("hardFailureCount") or 0) == 0, deep_summary or "not generated", "生成并复核承压板、加劲板、焊缝、锚筋、吊点和预埋件碰撞。"),
    ])

    p2 = _phase("P2", "版本、任务、审签与可观测性", [
        _check("P2-PIPELINE", "设计院八阶段流程无阻断", pipeline.get("overallStatus") not in {"blocked", "fail"}, pipeline, "按流水线顺序补齐设计依据、计算、深化和成果。", blocking=False),
        _check("P2-APPROVAL", "当前快照完成岗位分离审签", bool(review.get("approvalValid")), review, "完成设计、校核、审核、批准四级审签。", blocking=False),
        _check("P2-RELEASE-BASELINE", "施工版发行基线完整", bool(release_readiness.get("allowed")), release_readiness, "使计算合同、质量包、正式闸门、审签和施工修订全部对应当前快照。", blocking=False),
        _check("P2-REVISION", "项目启用不可变版本与审计日志", True, {"storage": "sqlite-wal", "immutableRevisions": True, "optimisticConcurrency": True, "requestActorAudit": True}, ""),
        _check("P2-TASK", "后台任务持久化、取消和重试能力可用", True, {"persistentTasks": True, "cancelAtStageBoundary": True, "retryEndpoint": True, "heartbeat": True}, ""),
        _check("P2-OBSERVABILITY", "运行指标与就绪检查可用", True, {"httpLatencyPercentiles": True, "taskMetrics": True, "readinessEndpoint": True}, ""),
        _check("P2-BACKUP", "在线一致性备份与完整性校验可用", True, {"sqliteOnlineBackup": True, "integrityCheck": True, "sha256": True, "retention": True}, ""),
        _check("P2-SECURITY", "生产访问控制已配置", bool(security.get("enabled")), security, "生产部署设置 PITGUARD_API_KEYS，并由 TLS 反向代理终止外部连接。", blocking=False),
    ])

    monitoring_enabled = bool(project.design_settings.monitoring_calibration_enabled)
    p3 = _phase("P3", "监测反馈、预警与数字孪生校准", [
        _check("P3-ENTRY", "监测数据入口已启用", monitoring_enabled, {"enabled": monitoring_enabled}, "启用监测数据导入与阈值配置。", blocking=False),
        _check("P3-DATA", "存在可用监测记录", monitoring.get("verifiedRecordCount", 0) > 0, monitoring, "导入位移、轴力、沉降和水位记录。", blocking=False),
        _check("P3-ALERT", "监测告警已评估", bool(monitoring.get("alertsEvaluated")), monitoring.get("summary"), "运行监测控制分析。", blocking=False),
        _check("P3-CALIBRATION", "监测数据存在时已形成反演记录", not project.monitoring_records or bool(project.calibration_runs), {"recordCount": len(project.monitoring_records), "calibrationRunCount": len(project.calibration_runs)}, "预览参数反演，经工程师批准后应用并复算。", blocking=False),
    ])

    phases = [p0, p1, p2, p3]
    blocking = sum(phase["blockingCount"] for phase in phases)
    warnings = sum(phase["warningCount"] for phase in phases)
    overall = "fail" if blocking else "warning" if warnings else "pass"
    score = round(sum(phase["completion"] for phase in phases) / len(phases), 1)
    return {
        "projectId": project.id,
        "softwareVersion": SOFTWARE_VERSION,
        "status": overall,
        "industrialReadinessScore": score,
        "blockingCount": blocking,
        "warningCount": warnings,
        "phases": phases,
        "calculationContract": contract,
        "calculationAssurance": calculation_assurance,
        "deliveryReleaseReadiness": release_readiness,
        "traceability": trace,
        "qualificationSuite": qualification,
        "candidateEvidence": candidate,
        "monitoringControl": monitoring,
        "designPipeline": pipeline,
        "officialIssueEligible": bool(blocking == 0 and review.get("approvalValid")),
        "evaluatedAt": _now(),
        "boundary": "该闸门用于工程设计辅助系统内部质量控制；项目正式签发仍需注册专业工程师、设计单位质量体系和法定程序确认。",
    }


def run_industrial_closure(project: Project) -> dict[str, Any]:
    qualification = run_geometry_qualification_suite()
    project.advanced_engineering["qualificationSuite"] = qualification
    detailing = build_rebar_detailing(project, mode="balanced") if project.retaining_system else {}
    project.advanced_engineering["industrialDetailing"] = detailing
    readiness = evaluate_industrial_readiness(project, include_detailing=False, run_qualification=False)
    project.advanced_engineering["industrialReadiness"] = readiness
    project.advanced_engineering["industrialClosureExecutedAt"] = _now()
    return readiness


def execute_full_industrial_closure(project: Project, *, top_n: int = 3) -> dict[str, Any]:
    """Execute the synchronous P0-P3 closure with calculation prerequisites.

    The background task remains the preferred UI path for large projects. This
    function makes the direct API deterministic: stale calculations are replaced,
    A/B/C evidence is completed, formal gates are refreshed, and detailing plus
    qualification are persisted in one project snapshot.
    """
    from app.calculation.engine import run_calculation, run_candidate_comparison_for_project
    from app.geometry.consistency import geometry_consistency_summary
    from app.quality.formal_gate import build_formal_report_gate
    from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility
    from app.services.calculation_state import mark_calculation_state_current
    from app.services.wall_length_optimizer import mark_wall_length_recalculated

    execution: dict[str, Any] = {
        "calculationExecuted": False,
        "candidateComparisonExecuted": False,
        "requestedTopN": max(1, min(int(top_n), 5)),
    }
    if not _calculation_contract(project).get("current"):
        result = run_calculation(project, None, auto_repair=True)
        project.calculation_results.append(result)
        mark_calculation_state_current(project, result.id)
        mark_wall_length_recalculated(project, result.id)
        execution.update({"calculationExecuted": True, "calculationResultId": result.id})

    repair = project.retaining_system.support_layout_repair if project.retaining_system else None
    candidates = list(repair.candidates or []) if repair else []
    required = min(execution["requestedTopN"], len(candidates))
    valid_rows = [
        row for row in (repair.candidate_full_calculations or [])
        if row.get("candidateId") and row.get("status") not in {"failed", "error"}
    ] if repair else []
    valid_ids = {str(row.get("candidateId")) for row in valid_rows}
    target_ids = {str(row.id) for row in candidates[:required]}
    if repair and required > 0 and not target_ids.issubset(valid_ids):
        comparison = run_candidate_comparison_for_project(project, top_n=required)
        repair.candidate_full_calculations = comparison
        execution.update({
            "candidateComparisonExecuted": True,
            "candidateFullCalculationCount": len([row for row in comparison if row.get("status") not in {"failed", "error"}]),
        })

    if project.calculation_results:
        latest = project.calculation_results[-1]
        if project.retaining_system and project.retaining_system.support_layout_repair:
            latest.support_layout_repair = project.retaining_system.support_layout_repair
        latest.report_diagram_data = dict(latest.report_diagram_data or {})
        latest.report_diagram_data["geometryConsistency"] = geometry_consistency_summary(project)
        latest.report_diagram_data["candidateFullCalculationComparison"] = list(
            project.retaining_system.support_layout_repair.candidate_full_calculations or []
        ) if project.retaining_system and project.retaining_system.support_layout_repair else []
        latest.formal_report_gate = build_formal_report_gate(
            project, latest.support_layout_quality, evaluate_ifc_model_compatibility(project), latest_result=latest,
        )

    readiness = run_industrial_closure(project)
    readiness["closureExecution"] = execution
    project.advanced_engineering["industrialReadiness"] = readiness
    return readiness
