from __future__ import annotations

from collections import Counter
from typing import Any

from app.schemas.domain import CalculationResult, Project
from app.version import ALGORITHM_VERSION, RULE_SET_VERSION, SOFTWARE_VERSION

_LEVEL_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


def _status_rank(status: str) -> int:
    return {"pass": 0, "warning": 1, "manual_review": 2, "fail": 3}.get(str(status), 2)


def _worst(statuses: list[str]) -> str:
    return max(statuses or ["pass"], key=_status_rank)


def _essential_parameter_inventory(project: Project) -> dict[str, Any]:
    required = (
        "unit_weight", "cohesion", "friction_angle", "elastic_modulus",
        "permeability_z", "horizontal_subgrade_modulus",
    )
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    empirical: list[str] = []
    low_confidence: list[str] = []
    for stratum in project.strata:
        values = stratum.parameters.model_dump(mode="json", by_alias=False)
        source = str(stratum.parameter_source or "imported")
        confidence = str(stratum.confidence or "medium")
        provided = [name for name in required if values.get(name) is not None]
        absent = [name for name in required if values.get(name) is None]
        if source in {"empirical", "manual"}:
            empirical.append(stratum.code)
        if confidence != "high":
            low_confidence.append(stratum.code)
        for name in absent:
            missing.append({"stratumCode": stratum.code, "parameter": name})
        rows.append({
            "stratumCode": stratum.code,
            "stratumName": stratum.name,
            "parameterSource": source,
            "confidence": confidence,
            "providedParameters": provided,
            "missingParameters": absent,
        })
    verified_boreholes = [item.code for item in project.boreholes if item.source_verified]
    water_records = sum(len(item.water_levels or []) for item in project.boreholes)
    return {
        "strata": rows,
        "stratumCount": len(rows),
        "missingParameterCount": len(missing),
        "missingParameters": missing[:100],
        "empiricalStrata": sorted(set(empirical)),
        "lowConfidenceStrata": sorted(set(low_confidence)),
        "verifiedBoreholeCount": len(verified_boreholes),
        "verifiedBoreholes": verified_boreholes,
        "groundwaterRecordCount": water_records,
        "formalDataReady": bool(
            rows and not missing and not empirical and not low_confidence
            and verified_boreholes and water_records
        ),
    }


def _uses_fallback(result: CalculationResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage in result.stage_results or []:
        coupled = stage.global_coupled_result
        if coupled is None:
            continue
        payload = coupled.model_dump(mode="json", by_alias=True)
        if payload.get("fallback"):
            rows.append({
                "stageId": stage.stage_id,
                "segmentId": stage.segment_id,
                "kind": "global_coupled_fallback",
                "reason": payload.get("reason") or payload.get("spatialFallbackReason"),
            })
        model_dimension = str(payload.get("modelDimension") or "")
        if "proxy" in model_dimension or payload.get("solverMode") in {"condensed", "condensed_large_face"}:
            rows.append({
                "stageId": stage.stage_id,
                "segmentId": stage.segment_id,
                "kind": "proxy_or_condensed_model",
                "reason": model_dimension or payload.get("solverMode"),
            })
        source = str(payload.get("soilSpringSource") or "")
        if source in {"default_screening", "derived_empirical"}:
            rows.append({
                "stageId": stage.stage_id,
                "segmentId": stage.segment_id,
                "kind": "soil_spring_fallback",
                "reason": source,
            })
    return rows


def _domain_levels(project: Project, result: CalculationResult, *, formal_data_ready: bool) -> list[dict[str, Any]]:
    advanced = project.advanced_engineering or {}
    transfer = dict(advanced.get("concaveTransferFrameAnalysis") or {})
    reaction = dict(advanced.get("wallWaleTransferReactionIteration") or {})
    node = dict(advanced.get("concaveTransferSpatialAnalysis") or {})
    six_dof = dict(getattr(result, "spatial_verification", {}) or advanced.get("sixDofSpatialVerification") or {})
    geotech = dict(getattr(result, "geotechnical_assurance", {}) or advanced.get("nonlinearGeotechnicalAssurance") or {})
    monitoring = bool(project.calibration_runs and project.calibration_runs[-1].applied)

    rows = [
        {
            "domain": "geometry_topology",
            "label": "几何与支撑拓扑",
            "level": "L2" if project.excavation and project.retaining_system else "L0",
            "status": "pass" if project.excavation and project.retaining_system else "fail",
            "method": "closed polygon, topology graph and load-path audit",
        },
        {
            "domain": "earth_pressure",
            "label": "土水压力",
            "level": "L2" if formal_data_ready else "L1",
            "status": "pass" if formal_data_ready else "warning",
            "method": "layered Rankine/Jaky pressure with verified parameter provenance" if formal_data_ready else "layered Rankine/Jaky pressure subset",
        },
        {
            "domain": "wall_soil_interaction",
            "label": "墙土相互作用",
            "level": "L2" if geotech.get("status") == "pass" else "L1",
            "status": str(geotech.get("status") or "warning"),
            "method": geotech.get("analysisMode") or "elastic subgrade reaction screening",
        },
        {
            "domain": "planar_support_system",
            "label": "墙—围檩—转接平面体系",
            "level": "L2" if transfer.get("status") == "pass" and reaction.get("converged") else "L1",
            "status": "pass" if transfer.get("status") == "pass" and reaction.get("converged") else "warning",
            "method": "iterated wall-wale-transfer planar frame",
        },
        {
            "domain": "spatial_structure",
            "label": "空间结构与节点",
            "level": "L2" if six_dof.get("status") in {"pass", "not_applicable"} else "L1",
            "status": str(six_dof.get("status") or node.get("status") or "not_applicable"),
            "method": six_dof.get("analysisMode") or node.get("analysisMode") or "not required for current topology",
        },
        {
            "domain": "geotechnical_stability",
            "label": "岩土稳定与地下水",
            "level": "L2" if formal_data_ready and result.design_review_summary and result.design_review_summary.stability_status == "pass" else "L1",
            "status": str(result.design_review_summary.stability_status if result.design_review_summary else "manual_review"),
            "method": "verified-input stability package and adverse scenarios" if formal_data_ready else "screening stability package and adverse scenarios",
        },
        {
            "domain": "member_design",
            "label": "构件承载力与配筋",
            "level": "L2" if result.design_review_summary and result.design_review_summary.strength_status == "pass" else "L1",
            "status": str(result.design_review_summary.strength_status if result.design_review_summary else "manual_review"),
            "method": "code-oriented section checks with detailing evidence",
        },
        {
            "domain": "monitoring_feedback",
            "label": "监测反馈与参数反演",
            "level": "L2" if monitoring or not project.design_settings.require_monitoring_feedback_before_next_stage else "L0",
            "status": "pass" if monitoring else "not_applicable" if not project.design_settings.require_monitoring_feedback_before_next_stage else "manual_review",
            "method": "robust monitoring/calculation ratio calibration" if monitoring else "not required before current design issue" if not project.design_settings.require_monitoring_feedback_before_next_stage else "required but not calibrated",
        },
    ]
    return rows


def _compliance_value_sources(project: Project) -> list[dict[str, Any]]:
    settings = project.design_settings
    return [
        {
            "name": "安全系数下限",
            "sourceType": "project_or_enterprise_floor",
            "source": "design_settings.safety_factor_overrides + safety-level/enterprise floor",
            "formalUse": True,
        },
        {
            "name": "荷载分项与组合系数",
            "sourceType": "project_confirmed_standard_value" if settings.design_basis_confirmed else "template_default",
            "source": "design_settings.load_gamma_g/load_gamma_q/load_psi",
            "formalUse": bool(settings.design_basis_confirmed),
        },
        {
            "name": "支撑竖向位置建议范围",
            "sourceType": "algorithm_screening_range",
            "source": "software constructability screening, not a normative limit",
            "formalUse": False,
        },
        {
            "name": "围檩支点间距",
            "sourceType": "project_control_value",
            "source": "design_settings.max_wale_support_bay_m/hard_max_wale_support_bay_m",
            "formalUse": bool(settings.design_basis_confirmed),
        },
        {
            "name": "水平地基反力系数",
            "sourceType": "measured_or_imported" if all(
                item.parameters.horizontal_subgrade_modulus is not None for item in project.strata
            ) and project.strata else "derived_or_default",
            "source": "strata.parameters.horizontal_subgrade_modulus",
            "formalUse": bool(project.strata) and all(
                item.parameters.horizontal_subgrade_modulus is not None for item in project.strata
            ),
        },
    ]



def _clause_traceability(result: CalculationResult) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    standards: set[str] = set()
    missing_clause = 0
    for raw in result.checks or []:
        item = raw if isinstance(raw, dict) else raw.model_dump(mode="json", by_alias=True)
        rule_id = str(item.get("ruleId") or item.get("rule_id") or "")
        clause = str(item.get("clauseReference") or item.get("clause_reference") or "").strip()
        standard = str(item.get("standardName") or item.get("standard_name") or "").strip()
        if standard:
            standards.add(standard)
        software_quality = rule_id.startswith("PITGUARD-") or "software" in clause.lower()
        if not clause and not software_quality:
            missing_clause += 1
        rows.append({
            "ruleId": rule_id,
            "status": str(item.get("status") or "manual_review"),
            "standardName": standard or None,
            "standardVersion": item.get("standardVersion") or item.get("standard_version"),
            "clauseReference": clause or None,
            "valueSourceType": "software_quality_gate" if software_quality else "normative_or_project_rule",
            "objectId": item.get("objectId") or item.get("object_id"),
        })
    return {
        "schema": "pitguard-clause-traceability-v1",
        "checkCount": len(rows),
        "clauseReferencedCount": sum(bool(row["clauseReference"]) for row in rows),
        "missingClauseReferenceCount": missing_clause,
        "standardCount": len(standards),
        "standards": sorted(standards),
        "statusCounts": dict(Counter(row["status"] for row in rows)),
        "sample": rows[:200],
    }

def build_analysis_assurance(project: Project, result: CalculationResult) -> dict[str, Any]:
    parameter_inventory = _essential_parameter_inventory(project)
    fallbacks = _uses_fallback(result)
    domains = _domain_levels(project, result, formal_data_ready=bool(parameter_inventory["formalDataReady"]))
    required = str(project.design_settings.required_formal_analysis_level or "L2")
    low_domains = [row for row in domains if _LEVEL_ORDER.get(str(row["level"]), 0) < _LEVEL_ORDER.get(required, 2)]
    failed_domains = [row for row in domains if str(row.get("status")) == "fail"]
    value_sources = _compliance_value_sources(project)
    nonformal_values = [row for row in value_sources if not row.get("formalUse")]
    clause_traceability = _clause_traceability(result)
    strict = bool(project.design_settings.formal_issue_strict_mode)
    strict_blocks: list[dict[str, Any]] = []
    if strict and fallbacks:
        strict_blocks.append({"code": "FORMAL-NO-PROXY-FALLBACK", "message": "正式模式存在代理、凝聚或默认回退模型。", "count": len(fallbacks)})
    if strict and not parameter_inventory["formalDataReady"]:
        strict_blocks.append({"code": "FORMAL-DATA-PROVENANCE", "message": "岩土与地下水参数来源未达到正式设计要求。"})
    if strict and low_domains:
        strict_blocks.append({"code": "FORMAL-ANALYSIS-LEVEL", "message": f"存在低于 {required} 的控制分析域。", "domains": [row["domain"] for row in low_domains]})
    if failed_domains:
        strict_blocks.append({"code": "FORMAL-DOMAIN-FAIL", "message": "存在工程计算失败域。", "domains": [row["domain"] for row in failed_domains]})

    statuses = [str(row.get("status") or "manual_review") for row in domains]
    status = "fail" if strict_blocks else _worst(statuses)
    return {
        "schema": "pitguard-analysis-assurance-v1",
        "version": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "ruleSetVersion": RULE_SET_VERSION,
        "status": status,
        "strictFormalMode": strict,
        "requiredFormalAnalysisLevel": required,
        "formalIssueEligible": not strict_blocks,
        "domains": domains,
        "domainLevelCounts": dict(Counter(str(row["level"]) for row in domains)),
        "lowLevelDomains": low_domains,
        "failedDomains": failed_domains,
        "parameterProvenance": parameter_inventory,
        "fallbacks": fallbacks,
        "fallbackCount": len(fallbacks),
        "complianceValueSources": value_sources,
        "nonNormativeOrUnconfirmedValueCount": len(nonformal_values),
        "clauseTraceability": clause_traceability,
        "strictBlocks": strict_blocks,
        "boundary": "分析等级描述模型保真度和验证深度，不替代注册工程师对适用条件、条文和工程资料的确认。",
    }
