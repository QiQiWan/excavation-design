from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from app.rules.registry import list_rules
from app.schemas.domain import (
    DesignReviewRequest,
    DesignSnapshotManifest,
    ExternalCollaborationRecord,
    ParameterProvenanceRecord,
    Project,
)
from app.services.calculation_assurance import verify_current_calculation_contract
from app.services.design_pipeline import evaluate_design_pipeline
from app.services.review_workflow import review_status
from app.services.standards_matrix import build_standards_process_matrix
from app.services.support_topology_contract import support_topology_hash
from app.version import (
    ALGORITHM_VERSION,
    RESULT_PIPELINE_VERSION,
    RULE_SET_VERSION,
    SOFTWARE_VERSION,
    STRUCTURAL_KERNEL_VERSION,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _status_from_counts(*, fail: int = 0, warning: int = 0, missing: int = 0, ready: bool = False) -> str:
    if fail or missing:
        return "blocked"
    if warning:
        return "warning"
    return "ready" if ready else "not_started"


def _latest_result(project: Project):
    return project.calculation_results[-1] if project.calculation_results else None


def _latest_checks(project: Project) -> list[dict[str, Any]]:
    latest = _latest_result(project)
    return list(getattr(latest, "checks", []) or []) if latest else []


def _candidate_rows(project: Project) -> list[dict[str, Any]]:
    ret = project.retaining_system
    if not ret:
        return []
    rows = list((ret.layout_summary or {}).get("candidateSchemes", []) or [])
    repair = getattr(ret, "support_layout_repair", None)
    if not rows and repair:
        rows = [row.model_dump(mode="json", by_alias=True) for row in (repair.candidates or [])]
        full_by_id = {str(item.get("candidateId") or item.get("id")): item for item in (repair.candidate_full_calculations or [])}
        for row in rows:
            summary = full_by_id.get(str(row.get("id")))
            if summary and not row.get("fullCalculation"):
                row["fullCalculation"] = summary
    return rows



FORMAL_PARAMETER_SOURCE_TYPES = {
    "survey_report", "owner_provided", "standard_value", "project_approved",
    "enterprise_standard", "derived",
}
NONFORMAL_PARAMETER_SOURCE_TYPES = {"software_suggestion", "manual_estimate", "unknown", "default"}


def _parameter_source_eligibility(record: ParameterProvenanceRecord) -> tuple[bool, str]:
    if record.value is None:
        return False, "参数值缺失"
    source_type = str(record.source_type or "unknown")
    if source_type not in FORMAL_PARAMETER_SOURCE_TYPES:
        if source_type in NONFORMAL_PARAMETER_SOURCE_TYPES:
            return False, "软件建议值、默认值或人工估算值只能用于方案草案"
        return False, f"参数来源 {source_type} 尚未纳入正式设计来源白名单"
    if not str(record.source_reference or "").strip():
        return False, "缺少可追溯的报告、条文、批准单或推导依据"
    if source_type == "derived" and str(record.confidence or "unknown") not in {"high", "medium"}:
        return False, "推导参数缺少足够置信度或依赖证据"
    return True, "来源类型和引用证据满足正式设计要求"


def _parameter_formal_eligibility(record: ParameterProvenanceRecord) -> tuple[bool, str]:
    source_eligible, source_reason = _parameter_source_eligibility(record)
    if not source_eligible:
        return False, source_reason
    if str(record.confirmation_status or "unconfirmed") != "confirmed":
        return False, "参数尚未由责任人员确认"
    if not bool(record.formal_design_allowed):
        return False, "参数尚未批准用于正式设计"
    return True, "已满足正式设计参数来源与确认要求"


def _parameter_template(project: Project) -> list[dict[str, Any]]:
    settings = project.design_settings
    excavation = project.excavation
    rows: list[dict[str, Any]] = [
        {
            "parameterKey": "design.project_grade", "displayName": "工程等级", "value": settings.project_grade,
            "unit": None, "defaultSourceType": "project_approved", "affects": ["规则适用性", "安全储备", "审签"], "critical": True,
        },
        {
            "parameterKey": "design.excavation_safety_level", "displayName": "基坑安全等级", "value": settings.excavation_safety_level,
            "unit": None, "defaultSourceType": "project_approved", "affects": ["荷载组合", "变形限值", "稳定限值"], "critical": True,
        },
        {
            "parameterKey": "design.importance_factor", "displayName": "结构重要性系数", "value": settings.importance_factor,
            "unit": None, "defaultSourceType": "standard_value", "affects": ["墙体内力", "支撑轴力", "构件设计"], "critical": True,
        },
        {
            "parameterKey": "design.surcharge", "displayName": "坑边附加荷载", "value": settings.surcharge,
            "unit": "kPa", "defaultSourceType": "software_suggestion", "affects": ["土压力", "墙体位移", "支撑轴力"], "critical": True,
        },
        {
            "parameterKey": "design.groundwater_level", "displayName": "设计地下水位", "value": settings.groundwater_level,
            "unit": "m", "defaultSourceType": "software_suggestion", "affects": ["水压力", "渗流", "突涌", "墙体内力"], "critical": True,
        },
        {
            "parameterKey": "design.groundwater_level_inside", "displayName": "坑内控制水位", "value": settings.groundwater_level_inside,
            "unit": "m", "defaultSourceType": "project_approved", "affects": ["水压力", "降水", "稳定"], "critical": True,
        },
        {
            "parameterKey": "design.default_support_spacing", "displayName": "支撑初始间距", "value": settings.default_support_spacing,
            "unit": "m", "defaultSourceType": "enterprise_standard", "affects": ["方案搜索", "围檩跨度", "支撑数量"], "critical": False,
        },
        {
            "parameterKey": "design.max_direct_strut_span_m", "displayName": "直撑最大建议跨度", "value": settings.max_direct_strut_span_m,
            "unit": "m", "defaultSourceType": "enterprise_standard", "affects": ["体系选择", "立柱设置", "构件稳定"], "critical": False,
        },
        {
            "parameterKey": "design.max_wale_support_bay_m", "displayName": "围檩支点间距控制值", "value": settings.max_wale_support_bay_m,
            "unit": "m", "defaultSourceType": "project_approved", "affects": ["围檩内力", "支撑布置"], "critical": True,
        },
        {
            "parameterKey": "design.required_formal_analysis_level", "displayName": "正式设计最低分析等级", "value": settings.required_formal_analysis_level,
            "unit": None, "defaultSourceType": "enterprise_standard", "affects": ["计算门禁", "交付资格"], "critical": True,
        },
    ]
    if excavation:
        rows.extend([
            {
                "parameterKey": "geometry.excavation_depth", "displayName": "基坑开挖深度", "value": excavation.depth,
                "unit": "m", "defaultSourceType": "derived", "affects": ["所有设计阶段"], "critical": True,
            },
            {
                "parameterKey": "geometry.top_elevation", "displayName": "坑顶标高", "value": excavation.top_elevation,
                "unit": "m", "defaultSourceType": "owner_provided", "affects": ["土压力", "施工工况", "图纸"], "critical": True,
            },
            {
                "parameterKey": "geometry.bottom_elevation", "displayName": "坑底标高", "value": excavation.bottom_elevation,
                "unit": "m", "defaultSourceType": "owner_provided", "affects": ["墙趾", "稳定", "施工工况"], "critical": True,
            },
        ])
    for stratum in project.strata:
        for field, label, unit in (
            ("unit_weight", "重度", "kN/m³"),
            ("cohesion", "黏聚力", "kPa"),
            ("friction_angle", "内摩擦角", "°"),
            ("elastic_modulus", "弹性模量", "MPa"),
            ("horizontal_subgrade_modulus", "水平基床系数", "kN/m³"),
            ("permeability_x", "水平渗透系数", "m/s"),
            ("permeability_z", "竖向渗透系数", "m/s"),
        ):
            value = getattr(stratum.parameters, field, None)
            source = "survey_report" if stratum.parameter_source in {"test", "imported"} else "manual_estimate"
            rows.append({
                "parameterKey": f"strata.{stratum.code}.{field}",
                "displayName": f"{stratum.code} {stratum.name}—{label}",
                "value": value,
                "unit": unit,
                "defaultSourceType": source,
                "affects": ["土压力", "土弹簧", "稳定", "地下水"] if "permeability" not in field else ["渗流", "降水", "突涌"],
                "critical": True,
                "sourceReference": f"地层参数来源：{stratum.parameter_source}",
                "confidence": stratum.confidence,
            })
    return rows


def ensure_parameter_provenance(project: Project) -> dict[str, Any]:
    existing = {row.parameter_key: row for row in project.parameter_provenance}
    changed = False
    for spec in _parameter_template(project):
        key = spec["parameterKey"]
        if key in existing:
            record = existing[key]
            if record.value != spec.get("value"):
                record.value = spec.get("value")
                record.confirmation_status = "unconfirmed"
                record.formal_design_allowed = False
                record.updated_at = _now()
                changed = True
            continue
        source_type = str(spec.get("defaultSourceType") or "software_suggestion")
        confidence = str(spec.get("confidence") or ("high" if source_type in {"survey_report", "standard_value", "project_approved", "derived"} else "unknown"))
        formal_allowed = source_type in {"survey_report", "owner_provided", "standard_value", "project_approved", "enterprise_standard", "derived"} and spec.get("value") is not None
        status = "confirmed" if source_type in {"survey_report", "standard_value", "derived"} and spec.get("value") is not None else "unconfirmed"
        project.parameter_provenance.append(ParameterProvenanceRecord(
            parameter_key=key,
            display_name=str(spec["displayName"]),
            value=spec.get("value"),
            unit=spec.get("unit"),
            source_type=source_type,
            source_reference=spec.get("sourceReference"),
            confidence=confidence,
            confirmation_status=status,
            formal_design_allowed=formal_allowed and status == "confirmed",
            affects=list(spec.get("affects") or []),
        ))
        changed = True
    return {"changed": changed, "count": len(project.parameter_provenance)}


def confirm_parameter_records(project: Project, updates: list[dict[str, Any]], *, actor: str | None = None) -> dict[str, Any]:
    ensure_parameter_provenance(project)
    by_key = {row.parameter_key: row for row in project.parameter_provenance}
    changed: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in updates:
        key = str(item.get("parameterKey") or item.get("parameter_key") or "")
        record = by_key.get(key)
        if not record:
            rejected.append({"parameterKey": key, "reason": "参数不存在"})
            continue
        if "sourceType" in item or "source_type" in item:
            record.source_type = str(item.get("sourceType") or item.get("source_type") or "unknown")
        if "sourceReference" in item or "source_reference" in item:
            record.source_reference = item.get("sourceReference") or item.get("source_reference")
        if "confidence" in item:
            record.confidence = str(item["confidence"])
        status = str(item.get("confirmationStatus") or item.get("confirmation_status") or "confirmed")
        requested_formal = bool(item.get("formalDesignAllowed", item.get("formal_design_allowed", status == "confirmed")))
        source_eligible, _ = _parameter_source_eligibility(record)
        record.confirmation_status = status
        record.formal_design_allowed = bool(requested_formal and status == "confirmed" and source_eligible)
        record.confirmed_by = actor or str(item.get("confirmedBy") or "designer")
        record.confirmed_at = _now() if status == "confirmed" else None
        record.updated_at = _now()
        changed.append(key)
        if requested_formal and not source_eligible:
            _, reason = _parameter_formal_eligibility(record)
            rejected.append({"parameterKey": key, "reason": reason})
    return {"updated": changed, "count": len(changed), "rejected": rejected, "rejectedCount": len(rejected)}


def build_parameter_confirmation(project: Project) -> dict[str, Any]:
    ensure_parameter_provenance(project)
    rows = []
    formal_blockers = []
    critical_keys = {spec["parameterKey"] for spec in _parameter_template(project) if spec.get("critical")}
    for record in project.parameter_provenance:
        critical = record.parameter_key in critical_keys
        source_eligible, _ = _parameter_source_eligibility(record)
        usable, eligibility_reason = _parameter_formal_eligibility(record)
        if critical and not usable:
            formal_blockers.append(record.parameter_key)
        rows.append({
            **record.model_dump(mode="json", by_alias=True),
            "critical": critical,
            "sourceEligibleForFormalDesign": source_eligible,
            "usableForFormalDesign": usable,
            "formalEligibilityReason": eligibility_reason,
            "impactCount": len(record.affects),
        })
    source_counts = Counter(row.source_type for row in project.parameter_provenance)
    return {
        "schema": "pitguard-parameter-governance-v387",
        "status": "blocked" if formal_blockers else "ready",
        "total": len(rows),
        "confirmed": sum(row["confirmationStatus"] == "confirmed" for row in rows),
        "formalAllowed": sum(row["usableForFormalDesign"] for row in rows),
        "formalBlockerCount": len(formal_blockers),
        "formalBlockerKeys": formal_blockers,
        "sourceCounts": dict(source_counts),
        "records": rows,
        "impactPolicy": "参数修改后按 affects 域选择性失效；软件建议值和人工估算值不能直接控制正式成果。",
    }


def build_rule_evidence(project: Project) -> dict[str, Any]:
    catalogue = list_rules()
    checks = _latest_checks(project)
    checks_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for check in checks:
        checks_by_rule[str(check.get("ruleId") or check.get("rule_id") or "unmapped")].append(check)
    rows: list[dict[str, Any]] = []
    covered_rule_ids: set[str] = set()
    for rule in catalogue:
        rule_id = str(rule.get("ruleId") or rule.get("rule_id") or "")
        matched = checks_by_rule.get(rule_id, [])
        statuses = Counter(str(item.get("status") or "manual_review") for item in matched)
        implementation = "calculated" if matched else "implemented_not_run"
        if "subset" in json.dumps(rule, ensure_ascii=False).lower() or "screen" in rule_id.lower():
            implementation = "screening_subset" if matched else "screening_not_run"
        if matched:
            covered_rule_ids.add(rule_id)
        rows.append({
            "ruleId": rule_id,
            "name": rule.get("name"),
            "clauseReference": rule.get("clauseReference"),
            "applicableTo": rule.get("applicableTo", []),
            "implementationStatus": implementation,
            "executionCount": len(matched),
            "statusCounts": dict(statuses),
            "resultStatus": "fail" if statuses.get("fail") else "warning" if statuses.get("warning") else "manual_review" if statuses.get("manual_review") else "pass" if matched else "not_calculated",
            "manualEvidenceRequired": implementation.startswith("screening") or not matched,
        })
    unmapped = [item for key, values in checks_by_rule.items() if key not in {str(r.get("ruleId") or "") for r in catalogue} for item in values]
    matrix = build_standards_process_matrix(project)
    return {
        "schema": "pitguard-clause-evidence-v387",
        "ruleCount": len(catalogue),
        "executedRuleCount": len(covered_rule_ids),
        "coverageRatio": round(len(covered_rule_ids) / max(len(catalogue), 1), 4),
        "unmappedCheckCount": len(unmapped),
        "rows": rows,
        "processSteps": matrix.get("steps", []),
        "boundary": "规则目录覆盖率只反映软件规则执行证据，不代表标准全文自动审查完成。",
    }


def _candidate_family(row: dict[str, Any]) -> str:
    variables = dict(row.get("variableSummary") or row.get("variable_summary") or {})
    return str(
        row.get("schemeFamily") or row.get("topologyClass") or row.get("transferTopologyClass")
        or row.get("topologyFamily") or variables.get("schemeFamily") or variables.get("transferTopologyClass")
        or variables.get("topologyFamily") or row.get("label") or row.get("name") or "unknown"
    )


def build_scheme_search_assurance(project: Project) -> dict[str, Any]:
    candidates = _candidate_rows(project)
    families = [_candidate_family(row) for row in candidates]
    full_rows = [row for row in candidates if row.get("fullCalculation") or row.get("calculationSummary")]
    selected_id = None
    if project.retaining_system:
        selected_id = (project.retaining_system.layout_summary or {}).get("selectedCandidateId")
        repair = getattr(project.retaining_system, "support_layout_repair", None)
        if not selected_id and repair:
            selected_id = repair.selected_candidate_id or repair.best_candidate_id
    full_selected = next((row for row in full_rows if str(row.get("id")) == str(selected_id)), None)
    diversity = len(set(families)) / max(len(candidates), 1) if candidates else 0.0
    level_status = [
        {"level": 1, "name": "围护体系", "status": "ready" if project.retaining_system and project.retaining_system.diaphragm_walls else "blocked"},
        {"level": 2, "name": "支撑体系族", "status": "ready" if candidates and len(set(families)) >= min(2, len(candidates)) else "warning" if candidates else "blocked"},
        {"level": 3, "name": "空间拓扑", "status": "ready" if candidates and all(row.get("quality") or row.get("topologyAudit") or row.get("hardConstraints") for row in candidates) else "warning" if candidates else "blocked"},
        {"level": 4, "name": "构件尺寸", "status": "ready" if project.retaining_system and all(s.section_optimization_status != "not_run" for s in project.retaining_system.supports) else "warning" if project.retaining_system else "blocked"},
        {"level": 5, "name": "完整计算排序", "status": "ready" if len(full_rows) >= min(3, len(candidates)) and full_selected else "warning" if full_rows else "blocked"},
    ]
    blockers: list[str] = []
    if not candidates:
        blockers.append("尚未生成候选方案。")
    if candidates and diversity < 0.5:
        blockers.append("候选体系差异不足，A/B/C可能仅是间距或偏移变化。")
    if candidates and len(full_rows) < min(3, len(candidates)):
        blockers.append("前3个候选尚未全部执行独立完整计算。")
    if candidates and not selected_id:
        blockers.append("尚未明确采用方案。")
    if selected_id and not full_selected:
        blockers.append("采用方案缺少当前完整计算证据。")
    return {
        "schema": "pitguard-five-level-scheme-search-v387",
        "status": "blocked" if blockers else "ready",
        "candidateCount": len(candidates),
        "familyCount": len(set(families)),
        "familyDiversityRatio": round(diversity, 4),
        "fullyCalculatedCount": len(full_rows),
        "selectedCandidateId": selected_id,
        "selectedCandidateFullyCalculated": bool(full_selected),
        "levels": level_status,
        "blockers": blockers,
        "candidateSummary": [
            {
                "id": row.get("id"), "label": row.get("label") or row.get("name"),
                "family": _candidate_family(row),
                "fullCalculation": bool(row.get("fullCalculation") or row.get("calculationSummary")),
                "rank": row.get("rank"), "score": row.get("score"),
                "supportCount": row.get("supportCount") or row.get("metrics", {}).get("supportCount"),
                "columnCount": row.get("columnCount") or row.get("metrics", {}).get("columnCount"),
            }
            for row in candidates
        ],
        "formalSelectionPolicy": "正式采用方案必须具有当前完整计算、体系差异证据和明确采用记录；代理评分只用于初筛。",
    }


def build_member_envelopes(project: Project) -> dict[str, Any]:
    latest = _latest_result(project)
    if not latest:
        return {"schema": "pitguard-member-envelope-v387", "status": "missing", "records": [], "recordCount": 0}
    values: dict[tuple[str, str], dict[str, Any]] = {}

    def update(object_id: str, response: str, value: float | None, unit: str, stage_id: str | None, source: str) -> None:
        if value is None:
            return
        key = (object_id, response)
        magnitude = abs(float(value))
        current = values.get(key)
        if current is None or magnitude > abs(float(current["maximumAbsolute"])):
            values[key] = {
                "objectId": object_id,
                "responseType": response,
                "unit": unit,
                "minimum": float(value),
                "maximum": float(value),
                "maximumAbsolute": magnitude,
                "controllingStageId": stage_id,
                "source": source,
            }
        else:
            current["minimum"] = min(float(current["minimum"]), float(value))
            current["maximum"] = max(float(current["maximum"]), float(value))

    for stage in latest.stage_results:
        stage_id = stage.stage_id
        wall = stage.wall_internal_force
        if wall:
            object_id = wall.segment_id
            update(object_id, "wall_moment", wall.max_moment_design or wall.max_moment, "kN·m/m", stage_id, "wall_internal_force")
            update(object_id, "wall_shear", wall.max_shear_design or wall.max_shear, "kN/m", stage_id, "wall_internal_force")
            update(object_id, "wall_displacement", wall.max_displacement, "m", stage_id, "wall_internal_force")
            for point in wall.points:
                update(object_id, "wall_moment_point", point.moment, "kN·m/m", stage_id, f"depth={point.depth}")
        for force in stage.support_forces:
            object_id = force.support_id or f"support-level-{force.level_index}"
            update(object_id, "support_axial_force", force.axial_force_design or force.effective_axial_force or force.axial_force, "kN", stage_id, "support_force")
        for wale in stage.wale_beam_results:
            object_id = wale.wale_beam_code
            update(object_id, "wale_moment", wale.max_moment_design or wale.max_moment, "kN·m", stage_id, "wale_continuous_beam")
            update(object_id, "wale_shear", wale.max_shear_design or wale.max_shear, "kN", stage_id, "wale_continuous_beam")
            update(object_id, "wale_deflection", wale.max_deflection, "m", stage_id, "wale_continuous_beam")
    records = sorted(values.values(), key=lambda row: (row["responseType"], row["objectId"]))
    return {
        "schema": "pitguard-member-envelope-v387",
        "status": "ready" if records else "warning",
        "recordCount": len(records),
        "objectCount": len({row["objectId"] for row in records}),
        "responseCounts": dict(Counter(row["responseType"] for row in records)),
        "records": records,
        "resultHash": getattr(latest, "result_hash", None),
        "unitPolicy": "每条包络记录显式携带单位；不同单位的结果禁止合并。",
    }


def _reinforcement_component_rows(project: Project) -> Iterable[dict[str, Any]]:
    ret = project.retaining_system
    if not ret:
        return []
    rows: list[dict[str, Any]] = []
    for wall in ret.diaphragm_walls:
        rows.append({"objectId": wall.id, "code": wall.panel_code, "kind": "wall", "groups": wall.reinforcement, "designStatus": getattr(wall.design_results, "check_status", None)})
    for support in ret.supports:
        rows.append({"objectId": support.id, "code": support.code, "kind": "support", "groups": support.reinforcement, "designStatus": support.section_optimization_status, "sectionType": support.section_type})
    for beam in [*ret.crown_beams, *ret.wale_beams, *ret.ring_beams]:
        rows.append({"objectId": beam.id, "code": beam.code, "kind": "ring_beam" if "ring" in beam.beam_role else "beam", "groups": beam.reinforcement, "designStatus": getattr(beam.design_result, "check_status", None), "analysisStatus": beam.analysis_status})
    for node in ret.support_nodes:
        rows.append({"objectId": node.id, "code": node.code, "kind": "node", "groups": node.reinforcement, "designStatus": node.check_status, "bearingPlate": node.bearing_plate.model_dump(mode="json", by_alias=True) if node.bearing_plate else None})
    return rows


def build_reinforcement_closure(project: Project) -> dict[str, Any]:
    required = {
        "wall": {"longitudinal", "distribution", "tie"},
        "support": {"longitudinal", "stirrup", "distribution", "tie", "additional"},
        "beam": {"longitudinal", "stirrup"},
        "ring_beam": {"longitudinal", "stirrup", "additional"},
        "node": {"additional"},
    }
    records = []
    fail_count = warning_count = feedback_required = 0
    for row in _reinforcement_component_rows(project):
        groups = list(row.get("groups") or [])
        present = {group.bar_type for group in groups}
        needed = set(required.get(row["kind"], set()))
        if row["kind"] == "support" and row.get("sectionType") != "rc_rectangular":
            needed = set()
        missing = sorted(needed - present)
        group_fail = [group.name for group in groups if group.check_status == "fail"]
        insufficient = [
            group.name for group in groups
            if group.required_area_per_meter is not None and group.area_per_meter is not None
            and float(group.area_per_meter) + 1e-9 < float(group.required_area_per_meter)
        ]
        needs_feedback = any(group.required_area_per_meter is not None for group in groups) and row.get("designStatus") in {None, "not_run", "preliminary", "manual_review"}
        node_bearing_missing = row["kind"] == "node" and not row.get("bearingPlate")
        status = "fail" if group_fail or insufficient else "warning" if missing or needs_feedback or node_bearing_missing else "pass"
        fail_count += status == "fail"
        warning_count += status == "warning"
        feedback_required += bool(needs_feedback)
        records.append({
            "objectId": row["objectId"], "code": row["code"], "componentKind": row["kind"],
            "status": status, "presentBarTypes": sorted(present), "requiredBarTypes": sorted(needed),
            "missingBarTypes": missing, "failedGroups": group_fail, "insufficientGroups": insufficient,
            "sectionFeedbackRequired": bool(needs_feedback), "nodeBearingPlateMissing": node_bearing_missing,
            "designStatus": row.get("designStatus"),
        })
    return {
        "schema": "pitguard-reinforcement-feedback-closure-v387",
        "status": "blocked" if fail_count else "warning" if warning_count else "ready" if records else "missing",
        "componentCount": len(records), "failCount": fail_count, "warningCount": warning_count,
        "sectionFeedbackRequiredCount": feedback_required, "records": records,
        "closureLoop": ["选择实际钢筋", "更新有效高度与构件刚度", "重新验算", "检查净距、锚固和拥挤", "必要时扩大截面并再次计算"],
    }


def build_delivery_quality(project: Project) -> dict[str, Any]:
    latest = _latest_result(project)
    contract = verify_current_calculation_contract(project, latest) if latest else {"current": False, "reason": "missing calculation"}
    drawings = list(getattr(latest, "drawing_sheets", []) or []) if latest else []
    present_types = {str(getattr(row, "sheet_type", None) or row.get("sheetType") or row.get("sheet_type") or "") if isinstance(row, dict) else str(getattr(row, "sheet_type", "")) for row in drawings}
    required = {
        "general_note", "location_plan", "wall_plan", "section", "support_plan", "member_section",
        "column_detail", "replacement_principle", "rebar_cage", "wale_rebar", "support_rebar",
        "node_detail", "member_schedule", "rebar_schedule", "monitoring_control",
    }
    missing = sorted(required - present_types)
    rebar = build_reinforcement_closure(project)
    envelope = build_member_envelopes(project)
    report_sections = set((project.advanced_engineering or {}).get("calculationReportSections", []) or [])
    required_report = {
        "project_overview", "design_basis", "geology_groundwater", "surroundings", "parameters",
        "scheme", "analysis_model", "loads", "design_stages", "wall_results", "support_results",
        "stability", "reinforcement", "adverse_scenarios", "conclusions", "manual_review",
    }
    report_missing = sorted(required_report - report_sections)
    blockers = []
    calculation_fail_count = 0
    formal_gate_allowed = False
    if not latest:
        blockers.append("缺少当前计算结果。")
    else:
        calculation_fail_count = sum(str(item.get("status") or "") == "fail" for item in (_latest_checks(project) or []))
        formal_gate = getattr(latest, "formal_report_gate", None)
        formal_gate_allowed = bool(formal_gate and formal_gate.allowed_for_official_issue)
        if not contract.get("current"):
            blockers.append("计算结果与当前输入、拓扑或规则集不一致。")
        if calculation_fail_count:
            blockers.append(f"存在 {calculation_fail_count} 项计算硬失败。")
        if not formal_gate_allowed:
            blockers.append("正式发行门禁尚未通过。")
    if missing:
        blockers.append(f"缺少 {len(missing)} 类施工图。")
    if report_missing:
        blockers.append(f"计算书章节证据缺少 {len(report_missing)} 项。")
    if rebar["status"] == "blocked":
        blockers.append("配筋闭环存在硬失败。")
    if envelope["recordCount"] == 0:
        blockers.append("缺少逐构件结果包络。")
    drawing_status = "blocked" if missing else "ready"
    report_status = "blocked" if report_missing or not contract.get("current") else "ready"
    artifact_completeness = not missing and not report_missing and envelope["recordCount"] > 0
    official_issue_eligible = artifact_completeness and rebar["status"] != "blocked" and calculation_fail_count == 0 and formal_gate_allowed
    return {
        "schema": "pitguard-design-institute-delivery-qc-v387",
        "status": "ready" if official_issue_eligible else "blocked",
        "drawingStatus": drawing_status,
        "reportStatus": report_status,
        "artifactCompletenessStatus": "ready" if artifact_completeness else "blocked",
        "officialIssueEligible": official_issue_eligible,
        "calculationFailCount": calculation_fail_count,
        "formalGateAllowed": formal_gate_allowed,
        "calculationCurrent": bool(contract.get("current")),
        "calculationContract": contract,
        "requiredDrawingTypes": sorted(required), "presentDrawingTypes": sorted(present_types), "missingDrawingTypes": missing,
        "requiredReportSections": sorted(required_report), "presentReportSections": sorted(report_sections), "missingReportSections": report_missing,
        "reinforcementStatus": rebar["status"], "memberEnvelopeCount": envelope["recordCount"],
        "blockers": blockers,
        "qualityChecks": ["图框与比例", "图层与线宽", "字高", "尺寸闭合", "标高一致", "构件编号", "剖切索引", "详图引用", "图纸目录", "模型—图纸—钢筋表一致性"],
    }


def prepare_design_snapshot(project: Project, *, purpose: str = "internal_review", actor: str | None = None, persist: bool = True) -> dict[str, Any]:
    parameters = build_parameter_confirmation(project)
    latest = _latest_result(project)
    ret = project.retaining_system
    design_basis_payload = project.design_settings.model_dump(mode="json", by_alias=True)
    param_payload = [row.model_dump(mode="json", by_alias=True) for row in project.parameter_provenance]
    rebar_payload = []
    if ret:
        rebar_payload = [
            {"objectId": row["objectId"], "code": row["code"], "kind": row["kind"], "groups": [g.model_dump(mode="json", by_alias=True) for g in row.get("groups", [])]}
            for row in _reinforcement_component_rows(project)
        ]
    drawing_payload = [row.model_dump(mode="json", by_alias=True) if hasattr(row, "model_dump") else row for row in (getattr(latest, "drawing_sheets", []) or [])] if latest else []
    report_meta = (project.advanced_engineering or {}).get("calculationReportManifest") or {}
    ifc_meta = (project.advanced_engineering or {}).get("ifcExportManifest") or {}
    hashes = {
        "designBasisHash": _hash(design_basis_payload),
        "parameterHash": _hash(param_payload),
        "topologyHash": support_topology_hash(project) if ret else None,
        "calculationResultHash": getattr(latest, "result_hash", None) if latest else None,
        "reinforcementHash": _hash(rebar_payload),
        "drawingHash": _hash(drawing_payload),
        "reportHash": _hash(report_meta),
        "ifcHash": _hash(ifc_meta),
    }
    quality = build_delivery_quality(project)
    blockers = list(quality["blockers"])
    if parameters["formalBlockerCount"]:
        blockers.append(f"有 {parameters['formalBlockerCount']} 项关键参数未确认或不允许用于正式设计。")
    consistency_hash = _hash(hashes)
    manifest = DesignSnapshotManifest(
        purpose=purpose,
        status="blocked" if blockers else "qualified",
        design_basis_hash=hashes["designBasisHash"],
        parameter_hash=hashes["parameterHash"],
        topology_hash=hashes["topologyHash"],
        calculation_result_hash=hashes["calculationResultHash"],
        reinforcement_hash=hashes["reinforcementHash"],
        drawing_hash=hashes["drawingHash"],
        report_hash=hashes["reportHash"],
        ifc_hash=hashes["ifcHash"],
        consistency_hash=consistency_hash,
        blockers=blockers,
        warnings=[],
        created_by=actor,
    )
    if persist:
        for old in project.design_snapshots:
            if old.status in {"qualified", "issued"} and old.purpose == purpose:
                old.status = "superseded"
        project.design_snapshots.append(manifest)
    return {"manifest": manifest.model_dump(mode="json", by_alias=True), "quality": quality, "hashes": hashes}


def add_external_collaboration(project: Project, payload: dict[str, Any]) -> dict[str, Any]:
    record = ExternalCollaborationRecord.model_validate(payload)
    project.external_collaboration_records.append(record)
    review_request = None
    if record.design_review_required:
        review_request = DesignReviewRequest(
            title=f"设计复核：{record.title}", source_record_id=record.id, source_party=record.source_party,
            description=record.summary, affected_object_ids=list(record.affected_object_ids),
        )
        project.design_review_requests.append(review_request)
    return {
        "record": record.model_dump(mode="json", by_alias=True),
        "reviewRequest": review_request.model_dump(mode="json", by_alias=True) if review_request else None,
    }


def update_design_review_request(project: Project, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = next((row for row in project.design_review_requests if row.id == request_id), None)
    if not request:
        raise ValueError("design review request not found")
    for attr, key in (
        ("status", "status"), ("design_response", "designResponse"),
        ("exceeds_design_boundary", "exceedsDesignBoundary"),
        ("recalculation_required", "recalculationRequired"),
        ("design_change_required", "designChangeRequired"),
    ):
        if key in payload:
            setattr(request, attr, payload[key])
        elif attr in payload:
            setattr(request, attr, payload[attr])
    if request.design_response:
        request.response_revision += 1
    request.updated_at = _now()
    return request.model_dump(mode="json", by_alias=True)


def build_release_qualification(project: Project) -> dict[str, Any]:
    stored = dict((project.advanced_engineering or {}).get("releaseQualification") or {})
    checks = {
        "backendRegression": stored.get("backendRegression", "not_run"),
        "frontendTests": stored.get("frontendTests", "not_run"),
        "frontendProductionBuild": stored.get("frontendProductionBuild", "not_run"),
        "migrationTest": stored.get("migrationTest", "not_run"),
        "endurance100Runs": stored.get("endurance100Runs", "not_run"),
        "concurrency10Projects": stored.get("concurrency10Projects", "not_run"),
        "externalBenchmark": stored.get("externalBenchmark", "not_run"),
        "realProjectPilot": stored.get("realProjectPilot", "not_run"),
    }
    required = list(checks)
    failed = [key for key, value in checks.items() if value == "fail"]
    incomplete = [key for key, value in checks.items() if value not in {"pass", "not_applicable"}]
    return {
        "schema": "pitguard-production-release-qualification-v387",
        "status": "blocked" if failed or incomplete else "qualified",
        "releaseLabel": "engineering_preview" if failed or incomplete else "production_verified",
        "checks": checks, "failed": failed, "incomplete": incomplete,
        "runtimeVersions": {
            "software": SOFTWARE_VERSION, "algorithm": ALGORITHM_VERSION, "ruleSet": RULE_SET_VERSION,
            "structuralKernel": STRUCTURAL_KERNEL_VERSION, "resultPipeline": RESULT_PIPELINE_VERSION,
        },
    }


def _stage(stage_id: str, title: str, status: str, *, readiness: float, blockers: list[str], warnings: list[str], metrics: dict[str, Any], next_actions: list[str]) -> dict[str, Any]:
    return {
        "stageId": stage_id, "title": title, "status": status, "readiness": round(readiness, 1),
        "blockers": blockers, "warnings": warnings, "metrics": metrics, "nextActions": next_actions,
    }


def build_design_core_workflow(project: Project) -> dict[str, Any]:
    parameters = build_parameter_confirmation(project)
    rules = build_rule_evidence(project)
    schemes = build_scheme_search_assurance(project)
    pipeline = evaluate_design_pipeline(project)
    envelopes = build_member_envelopes(project)
    rebar = build_reinforcement_closure(project)
    delivery = build_delivery_quality(project)
    release = build_release_qualification(project)
    latest = _latest_result(project)
    checks = _latest_checks(project)
    status_counts = Counter(str(row.get("status") or "manual_review") for row in checks)
    review = review_status(project)

    data_blockers = []
    if not project.excavation:
        data_blockers.append("缺少基坑轮廓和开挖标高。")
    if not project.boreholes or not project.strata or not project.geological_model:
        data_blockers.append("缺少可用于设计的勘察、地层或地质模型。")
    if parameters["formalBlockerCount"]:
        data_blockers.append(f"{parameters['formalBlockerCount']}项关键参数待确认。")

    retaining = project.retaining_system
    retaining_blockers = [] if retaining and retaining.diaphragm_walls and retaining.supports else ["围护墙—围檩—支撑—立柱体系尚未完整生成。"]
    calc_blockers = []
    if not latest:
        calc_blockers.append("尚未完成当前快照计算。")
    if status_counts.get("fail"):
        calc_blockers.append(f"存在{status_counts['fail']}项计算硬失败。")
    if latest and not verify_current_calculation_contract(project, latest).get("current"):
        calc_blockers.append("计算结果已失效或与当前模型不一致。")

    stages = [
        _stage("D1_BASIS", "规范与参数确认", _status_from_counts(missing=len(data_blockers), ready=not data_blockers), readiness=100 if not data_blockers else max(0, 100 - 20 * len(data_blockers)), blockers=data_blockers, warnings=[], metrics={"parameterCount": parameters["total"], "formalBlockerCount": parameters["formalBlockerCount"], "ruleCoverageRatio": rules["coverageRatio"]}, next_actions=["确认关键参数来源、适用规范和项目控制值。"]),
        _stage("D2_INPUT", "工程输入与设计域", _status_from_counts(missing=len(data_blockers), ready=not data_blockers), readiness=100 if not data_blockers else 45, blockers=data_blockers, warnings=[], metrics={"boreholeCount": len(project.boreholes), "stratumCount": len(project.strata), "hasGeologicalModel": bool(project.geological_model), "hasExcavation": bool(project.excavation)}, next_actions=["补齐并核验勘察、水位、周边环境和基坑几何。"]),
        _stage("D3_SCHEME_SEARCH", "方案搜索与比选", schemes["status"], readiness=100 if schemes["status"] == "ready" else 65 if schemes["candidateCount"] else 0, blockers=schemes["blockers"], warnings=[], metrics={"candidateCount": schemes["candidateCount"], "familyCount": schemes["familyCount"], "fullyCalculatedCount": schemes["fullyCalculatedCount"]}, next_actions=["生成体系差异明确的A/B/C并完成前3名完整计算。"]),
        _stage("D4_RETAINING_DESIGN", "围护结构联合设计", _status_from_counts(missing=len(retaining_blockers), ready=not retaining_blockers), readiness=100 if not retaining_blockers else 20, blockers=retaining_blockers, warnings=[], metrics={"wallCount": len(retaining.diaphragm_walls) if retaining else 0, "supportCount": len(retaining.supports) if retaining else 0, "waleCount": len(retaining.wale_beams) if retaining else 0, "columnCount": len(retaining.columns) if retaining else 0}, next_actions=["联合优化墙厚、嵌固、支撑层、支撑间距和构件截面。"]),
        _stage("D5_CALCULATION", "计算核验与结果包络", _status_from_counts(fail=int(bool(calc_blockers)), warning=status_counts.get("warning", 0), ready=not calc_blockers), readiness=100 if not calc_blockers and not status_counts.get("warning") else 70 if latest else 0, blockers=calc_blockers, warnings=[f"{status_counts.get('warning', 0)}项预警，{status_counts.get('manual_review', 0)}项人工复核。"] if latest else [], metrics={"calculationResultId": getattr(latest, "id", None), "failCount": status_counts.get("fail", 0), "warningCount": status_counts.get("warning", 0), "memberEnvelopeCount": envelopes["recordCount"]}, next_actions=["关闭硬失败，核对数值健康、模型等级和逐构件控制工况。"]),
        _stage("D6_REINFORCEMENT", "配筋与节点深化", rebar["status"], readiness=100 if rebar["status"] == "ready" else 65 if rebar["componentCount"] else 0, blockers=[f"配筋闭环有{rebar['failCount']}项硬失败。"] if rebar["failCount"] else [], warnings=[f"{rebar['warningCount']}个构件仍需构造或回代复核。"] if rebar["warningCount"] else [], metrics={"componentCount": rebar["componentCount"], "failCount": rebar["failCount"], "warningCount": rebar["warningCount"], "feedbackRequiredCount": rebar["sectionFeedbackRequiredCount"]}, next_actions=["完成配筋回代、净距、锚固、节点承压和钢筋拥挤检查。"]),
        _stage("D7_DRAWINGS", "施工图生成与图面质检", delivery["drawingStatus"], readiness=100 if not delivery["missingDrawingTypes"] else max(0, 100 - 5 * len(delivery["missingDrawingTypes"])), blockers=[f"缺少{len(delivery['missingDrawingTypes'])}类施工图。"] if delivery["missingDrawingTypes"] else [], warnings=["图种齐全只表示成果生成完整，正式发行仍受计算、配筋和校审门禁控制。"] if not delivery["missingDrawingTypes"] and not delivery["officialIssueEligible"] else [], metrics={"presentDrawingTypeCount": len(delivery["presentDrawingTypes"]), "missingDrawingTypeCount": len(delivery["missingDrawingTypes"])}, next_actions=["补齐核心图种并执行图框、标注、索引和模型一致性质检。"]),
        _stage("D8_REPORT", "计算书与成果一致性", delivery["reportStatus"], readiness=100 if delivery["reportStatus"] == "ready" else max(0, 100 - 5 * len(delivery["missingReportSections"])), blockers=([f"缺少{len(delivery['missingReportSections'])}项计算书章节证据。"] if delivery["missingReportSections"] else []) + (["计算结果与当前模型不一致。"] if not delivery["calculationCurrent"] else []), warnings=["计算书章节齐全不代表工程验算和正式发行门禁通过。"] if delivery["reportStatus"] == "ready" and not delivery["officialIssueEligible"] else [], metrics={"presentReportSectionCount": len(delivery["presentReportSections"]), "missingReportSectionCount": len(delivery["missingReportSections"]), "calculationCurrent": delivery["calculationCurrent"]}, next_actions=["确保每个结论包含设计值、限值、控制工况、构件、条文和模型等级。"]),
        _stage("D9_REVIEW_ISSUE", "校审、快照与发行", "ready" if delivery["status"] == "ready" and str(review.get("status")) == "approved" else "blocked", readiness=100 if delivery["status"] == "ready" and str(review.get("status")) == "approved" else 50 if delivery["status"] == "ready" else 0, blockers=[] if delivery["status"] == "ready" and str(review.get("status")) == "approved" else ["成果质量门禁或四级校审尚未完成。"], warnings=[f"生产发布标识：{release['releaseLabel']}"] if release["releaseLabel"] != "production_verified" else [], metrics={"reviewStatus": review.get("status"), "snapshotCount": len(project.design_snapshots), "releaseLabel": release["releaseLabel"]}, next_actions=["生成统一DesignSnapshotId，完成设计、校核、审核、批准后发行。"]),
    ]
    weights = [12, 10, 14, 12, 18, 14, 8, 6, 6]
    overall = sum(stage["readiness"] * weight for stage, weight in zip(stages, weights)) / sum(weights)
    return {
        "schema": "pitguard-design-core-workflow-v387",
        "version": SOFTWARE_VERSION,
        "presentationRole": "quality_assurance",
        "primaryWorkflowStageCount": 6,
        "primaryWorkflow": ["basis", "input", "scheme", "calculation", "reinforcement", "deliverables"],
        "evidenceDomainCount": len(stages),
        "evidenceGrouping": {
            "basis": ["D1_BASIS"],
            "input": ["D2_INPUT"],
            "scheme": ["D3_SCHEME_SEARCH", "D4_RETAINING_DESIGN"],
            "calculation": ["D5_CALCULATION"],
            "reinforcement": ["D6_REINFORCEMENT"],
            "deliverables": ["D7_DRAWINGS", "D8_REPORT", "D9_REVIEW_ISSUE"],
        },
        "productPositioning": "六阶段单一设计主流程，九个内部证据域仅用于质量检查与追溯",
        "overallReadiness": round(overall, 1),
        "status": "blocked" if any(stage["status"] == "blocked" for stage in stages) else "warning" if any(stage["status"] == "warning" for stage in stages) else "ready",
        "stages": stages,
        "evidenceDomains": stages,
        "externalCollaboration": {
            "recordCount": len(project.external_collaboration_records),
            "openDesignReviewRequestCount": sum(row.status != "closed" for row in project.design_review_requests),
            "boundary": "外部施工或现场信息仅以资料和设计复核请求接入，不作为首次设计发行的必填项。",
        },
        "legacyConstructionFieldModules": {
            "constructionPlanStageCount": len(project.construction_plan_stages),
            "fieldSnapshotCount": len(project.field_execution_snapshots),
            "deviationEventCount": len(project.deviation_events),
            "primaryWorkflowUsage": False,
            "compatibility": "read_only_legacy",
        },
        "productionQualification": release,
        "legacyPipeline": pipeline,
    }
