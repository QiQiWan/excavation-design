from __future__ import annotations

from typing import Any

from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.schemas.domain import Project
from app.services.wall_vertical_length_optimizer import analyze_wall_vertical_length
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.delivery_release import evaluate_delivery_release_readiness


_STATUS_RANK = {"blocked": 0, "warning": 1, "ready": 2, "pass": 3}


def _stage(stage_id: str, name: str, status: str, evidence: dict[str, Any], actions: list[str]) -> dict[str, Any]:
    return {
        "stageId": stage_id,
        "name": name,
        "status": status,
        "evidence": evidence,
        "requiredActions": actions,
    }


def evaluate_design_pipeline(project: Project) -> dict[str, Any]:
    """Return the design-institute-style gate sequence for the current snapshot.

    A stage is allowed to consume only evidence produced by the previous stage.
    This prevents geometry proxies, stale candidate results and sampled viewer
    objects from being treated as final calculation or construction evidence.
    """
    ret = project.retaining_system
    latest = project.calculation_results[-1] if project.calculation_results else None
    checks = list(getattr(latest, "checks", []) or []) if latest else []
    fail_count = sum(1 for row in checks if str(row.get("status")) == "fail")
    warning_count = sum(1 for row in checks if str(row.get("status")) == "warning")
    manual_count = sum(1 for row in checks if str(row.get("status")) == "manual_review")
    calc_assurance = dict(getattr(latest, "calculation_assurance", {}) or {}) if latest else {}
    contract_status = verify_current_calculation_contract(project, latest) if latest else {"current": False, "reason": "missing calculation result"}
    release_readiness = evaluate_delivery_release_readiness(project, issue_mode="construction")
    coverage = getattr(project.geological_model, "coverage_audit", None) if project.geological_model else None
    coverage_dict = coverage.model_dump(by_alias=True) if hasattr(coverage, "model_dump") else (coverage or {})
    design_domain_covered = bool(coverage_dict.get("designDomainCovered", False)) if coverage_dict else bool(project.geological_model)

    stages: list[dict[str, Any]] = []
    data_ready = bool(project.boreholes and project.strata and project.geological_model and project.excavation)
    stages.append(_stage(
        "P1_DATA_BASIS", "设计依据与设计域",
        "pass" if data_ready and design_domain_covered else "blocked",
        {
            "boreholeCount": len(project.boreholes), "stratumCount": len(project.strata),
            "hasGeologicalModel": bool(project.geological_model), "hasExcavation": bool(project.excavation),
            "designDomainCovered": design_domain_covered, "coverageAudit": coverage_dict,
        },
        [] if data_ready and design_domain_covered else ["补齐勘察、地下水、基坑轮廓并确保地质模型覆盖围护及施工影响区。"],
    ))

    construction_panels = sum(len(getattr(wall, "construction_panels", []) or []) for wall in (ret.diaphragm_walls if ret else []))
    scheme_ready = bool(ret and ret.diaphragm_walls and ret.supports and construction_panels)
    stages.append(_stage(
        "P2_SCHEME", "支护体系与施工分幅",
        "pass" if scheme_ready else "blocked",
        {
            "wallSegmentCount": len(ret.diaphragm_walls) if ret else 0,
            "constructionPanelCount": construction_panels,
            "supportCount": len(ret.supports) if ret else 0,
            "supportFamily": getattr(project.design_settings, "support_layout_family", "auto"),
            "wallToeProfile": (ret.layout_summary or {}).get("wallGeometryProvenance", {}).get("wallToeProfileType") if ret else None,
        },
        [] if scheme_ready else ["选择支撑体系族，完成围护墙计算段、施工槽段、支撑层和施工阶段定义。"],
    ))

    candidate_rows = list((ret.layout_summary or {}).get("candidateSchemes", []) or []) if ret else []
    calculated_candidates = [row for row in candidate_rows if row.get("fullCalculation") or row.get("calculationSummary")]
    selected_candidate_id = (ret.layout_summary or {}).get("selectedCandidateId") if ret else None
    if not candidate_rows:
        candidate_status = "warning"
    elif len(calculated_candidates) >= min(3, len(candidate_rows)) and selected_candidate_id:
        candidate_status = "pass"
    else:
        candidate_status = "warning"
    stages.append(_stage(
        "P3_SCHEME_COMPARISON", "候选方案完整计算与选型",
        candidate_status,
        {
            "candidateCount": len(candidate_rows),
            "fullyCalculatedCandidateCount": len(calculated_candidates),
            "proxyOnlyCount": max(0, len(candidate_rows) - len(calculated_candidates)),
            "selectedCandidateId": selected_candidate_id,
        },
        [] if candidate_status == "pass" else ["生成至少 3 个可比较方案，对 A/B/C 分别执行独立施工阶段计算，并明确采用方案；禁止使用几何代理轴力和空白位移进行选型。"],
    ))

    assurance_status = str(calc_assurance.get("status") or "fail")
    if latest is None or fail_count or assurance_status == "fail" or not contract_status.get("current"):
        calc_status = "blocked"
    elif warning_count or manual_count or assurance_status in {"warning", "manual_review"}:
        calc_status = "warning"
    else:
        calc_status = "pass"
    calc_actions: list[str] = []
    if latest is None:
        calc_actions.append("运行当前快照的完整分阶段计算。")
    if fail_count:
        calc_actions.append("修复规范校核硬失败并重新计算。")
    if not contract_status.get("current"):
        calc_actions.append("当前输入、施工工况或构件参数已变化，冻结新计算基线并重新计算。")
    if assurance_status == "fail":
        calc_actions.append("关闭阶段覆盖、数值收敛、有限性或规范追溯硬失败。")
    elif assurance_status in {"warning", "manual_review"}:
        calc_actions.append("完成独立计算差异、病态矩阵、回退求解和低置信度参数人工复核。")
    stages.append(_stage(
        "P4_ANALYSIS", "分阶段计算、数值质量与独立复核",
        calc_status,
        {
            "calculationResultId": getattr(latest, "id", None), "failCount": fail_count,
            "warningCount": warning_count, "manualReviewCount": manual_count,
            "strengthStatus": getattr(getattr(latest, "governing_values", None), "strength_check_status", None),
            "stiffnessStatus": getattr(getattr(latest, "governing_values", None), "stiffness_check_status", None),
            "stabilityStatus": getattr(getattr(latest, "governing_values", None), "stability_check_status", None),
            "calculationAssuranceStatus": assurance_status,
            "calculationContract": contract_status,
            "inputSnapshotHash": getattr(latest, "input_snapshot_hash", None),
            "adoptedDesignSnapshotHash": getattr(latest, "adopted_design_snapshot_hash", None),
            "resultHash": getattr(latest, "result_hash", None),
            "stageCoverage": calc_assurance.get("stageCoverage"),
            "numericalQuality": calc_assurance.get("numericalQuality"),
            "independentCheck": calc_assurance.get("independentCheck"),
            "traceability": calc_assurance.get("traceability"),
        },
        calc_actions,
    ))

    rebar_scheme = (ret.rebar_design_scheme or {}) if ret else {}
    wall_zones = list(rebar_scheme.get("wallZones", []) or []) if isinstance(rebar_scheme, dict) else []
    component_ready = bool(latest and fail_count == 0 and wall_zones)
    stages.append(_stage(
        "P5_COMPONENT_DESIGN", "构件截面、双向配筋与墙趾优化",
        "pass" if component_ready else ("warning" if latest and fail_count == 0 else "blocked"),
        {
            "wallRebarZoneCount": len(wall_zones),
            "wallLengthOptimization": analyze_wall_vertical_length(project).get("summary", {}) if ret and ret.diaphragm_walls else {},
            "hasSupportDesign": bool(ret and any(getattr(item, "design_axial_force", None) is not None and str(getattr(item, "section_optimization_status", "not_run")) != "not_run" for item in ret.supports)),
            "hasWaleDesign": bool(ret and any(getattr(item, "design_result", None) for item in ret.wale_beams)),
        },
        [] if component_ready else ["完成墙、围檩、支撑、立柱截面和墙体深度/平面分幅联合设计，再生成配筋方案。"],
    ))

    viz = build_rebar_ifc_visualization(project, max_bars=240) if ret else {"summary": {}, "cages": []}
    cage_count = len(viz.get("cages", []) or [])
    detailing_ready = bool(cage_count and construction_panels and rebar_scheme)
    stages.append(_stage(
        "P6_DETAILING", "钢筋笼、节点与施工深化",
        "pass" if detailing_ready else "warning",
        {"rebarCageCount": cage_count, "constructionPanelCount": construction_panels, "rebarDetailLevel": viz.get("summary", {}).get("detailLevel")},
        [] if detailing_ready else ["按槽段形成完整钢筋笼网格、吊点、接头、套筒、节点附加筋和碰撞检查。"],
    ))

    drawing_rows = list(getattr(latest, "drawing_sheets", []) or []) if latest else []
    drawing_count = len(drawing_rows)
    drawing_types = {str(row.get("sheetType") or "") for row in drawing_rows if isinstance(row, dict)}
    required_drawing_types = {"plan", "section", "rebar_cage", "node_detail", "pile_detail"}
    drawing_core_complete = required_drawing_types.issubset(drawing_types)
    if not (latest and fail_count == 0 and detailing_ready):
        deliverable_status = "blocked"
    elif drawing_core_complete:
        deliverable_status = "ready"
    else:
        deliverable_status = "warning"
    stages.append(_stage(
        "P7_DELIVERABLES", "施工图、IFC、计算书与加工包",
        deliverable_status,
        {
            "drawingSheetCount": drawing_count,
            "drawingTypes": sorted(drawing_types),
            "requiredDrawingTypes": sorted(required_drawing_types),
            "drawingCoreComplete": drawing_core_complete,
            "calculationCurrent": bool(contract_status.get("current")),
            "calculationAssuranceStatus": assurance_status,
            "rebarPackageReady": detailing_ready,
            "constructionReleaseReadiness": release_readiness,
        },
        [] if deliverable_status == "ready" else ["补齐总平面、控制剖面、钢筋笼、节点和立柱基础核心图纸，并仅从当前已选方案及当前计算合同生成 CAD/PDF/IFC/DOCX/XLSX。"],
    ))

    approved = project.review_workflow.status == "approved"
    issue_status = "pass" if approved and release_readiness.get("allowed") and drawing_core_complete and detailing_ready else "blocked"
    stages.append(_stage(
        "P8_REVIEW_ISSUE", "校审、批准与受控发行",
        issue_status,
        {
            "reviewStatus": project.review_workflow.status,
            "approved": approved,
            "failCount": fail_count,
            "releaseReadiness": release_readiness,
        },
        [] if issue_status == "pass" else ["完成岗位分离审签、当前施工版修订、计算质量包和交付基线校验后方可正式发行。"],
    ))

    overall = min(stages, key=lambda row: _STATUS_RANK.get(str(row["status"]), 0))["status"] if stages else "blocked"
    return {
        "projectId": project.id,
        "overallStatus": overall,
        "stageCount": len(stages),
        "stages": stages,
        "operatingSequence": [row["stageId"] for row in stages],
        "rule": "each downstream stage consumes the current approved snapshot of the previous stage; proxy results and viewer LOD are never calculation or fabrication evidence",
    }
