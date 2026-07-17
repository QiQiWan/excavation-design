from __future__ import annotations

from statistics import fmean
from typing import Any

from app.schemas.domain import Project
from app.services.standards_matrix import PROCESS_MATRIX, STANDARD_CATALOG
from app.services.engineering_templates import safety_targets
from app.services.verification_coverage import (
    VERIFICATION_CATALOG,
    coverage_summary,
    input_availability,
    input_requirement_details,
    missing_evidence_record,
)


CORE_STAGE_TO_PROCESS = {
    "basis": ("retaining", "calculation"),
    "input": ("boreholes", "geology", "excavation"),
    "scheme": ("retaining",),
    "calculation": ("calculation",),
    "reinforcement": ("retaining", "calculation"),
    "deliverables": ("export",),
}


def _catalog_by_id() -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in STANDARD_CATALOG}


def build_core_standard_guidance() -> dict[str, list[dict[str, Any]]]:
    """Return concise standards guidance for the five-screen core workflow.

    The full standards matrix remains available in documentation.  The core UI
    receives only decision-relevant references so it can explain why a step is
    required without returning to the pre-V3.40 documentation-heavy layout.
    """
    catalog = _catalog_by_id()
    process_by_key = {str(item.get("workflowStep")): item for item in PROCESS_MATRIX}
    result: dict[str, list[dict[str, Any]]] = {}
    for core_stage, process_keys in CORE_STAGE_TO_PROCESS.items():
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for process_key in process_keys:
            process = process_by_key.get(process_key) or {}
            clause_focus = list(process.get("clauseFocus") or [])
            for standard_id in list(process.get("standardIds") or []):
                if standard_id in seen:
                    continue
                seen.add(standard_id)
                item = catalog.get(str(standard_id)) or {}
                refs.append({
                    "id": standard_id,
                    "code": item.get("code") or standard_id,
                    "name": item.get("name") or standard_id,
                    "level": item.get("level"),
                    "levelLabel": item.get("levelLabel"),
                    "focus": clause_focus[0] if clause_focus else item.get("implementedScope"),
                    "implementedScope": item.get("implementedScope"),
                    "boundary": item.get("boundary"),
                    "sourceUrl": item.get("sourceUrl"),
                })
        # Keep the default panel compact. Mandatory and primary standards first.
        refs.sort(key=lambda row: (0 if row.get("level") == "mandatory_all" else 1 if row.get("level") == "primary_design" else 2, str(row.get("code"))))
        result[core_stage] = refs[:5]
    return result


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "calculatedValue", "factor", "safetyFactor", "result", "ratio"):
            if key in value:
                return _number(value.get(key))
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_limit(check: dict[str, Any]) -> float | None:
    for key in ("limitValue", "requiredValue", "minimumValue", "threshold", "limit"):
        value = _number(check.get(key))
        if value is not None:
            return value
    return None


def _factor_record(
    code: str,
    label: str,
    value: Any,
    limit: Any,
    standard: str,
    clause_focus: str,
    source: str,
) -> dict[str, Any] | None:
    factor = _number(value)
    minimum = _number(limit)
    if factor is None or factor <= 0.0 or abs(factor) >= 100.0:
        # Safety factors must be positive.  Several legacy checks use 0 or 999
        # as sentinels for "not controlling / not evaluated"; neither belongs
        # in a real safety-factor distribution.
        return None
    margin = factor / minimum if minimum not in (None, 0.0) else None
    status = "manual_review" if minimum is None else "pass"
    if margin is not None:
        status = "fail" if margin < 1.0 else "warning" if margin < 1.10 else "pass"
    return {
        "code": code,
        "label": label,
        "value": round(factor, 4),
        "limit": round(minimum, 4) if minimum is not None else None,
        "marginRatio": round(margin, 4) if margin is not None else None,
        "status": status,
        "standard": standard,
        "clauseFocus": clause_focus,
        "source": source,
    }


_STABILITY_FACTOR_CODES = {
    "EMBEDMENT_STABILITY": "embedment",
    "BOTTOM_HEAVE": "heave",
    "GLOBAL_STABILITY": "overall",
    "SUPPORT_STABILITY": "support_stability",
    "COLUMN_STABILITY": "column_stability",
    "LOCAL_WEAK_LAYER": "weak_layer",
    "WALL_ROTATIONAL_STABILITY": "wall_rotation",
    "BEARING_CAPACITY": "bearing_capacity",
    "CONSTRUCTION_STAGE_STABILITY": "construction_stage",
    "REPLACEMENT_PATH_STABILITY": "replacement_path",
    "SEEPAGE": "seepage",
    "PIPING": "uplift",
    "WATERPROOF_CUTOFF": "cutoff",
    "DEWATERING_CAPACITY": "dewatering_capacity",
    "DEWATERING_FAILURE": "dewatering_failure",
    "DRAWDOWN_INFLUENCE": "drawdown_influence",
}


def _pending_stability_factors(
    project: Project,
    existing_codes: set[str],
    availability: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in VERIFICATION_CATALOG:
        if str(spec.get("category")) not in {"stability", "hydraulic"}:
            continue
        code = _STABILITY_FACTOR_CODES.get(str(spec.get("ruleId")), str(spec.get("ruleId") or "").lower())
        if code in existing_codes:
            continue
        evidence = missing_evidence_record(project, spec, availability=availability)
        state = str(evidence.get("evidenceState") or "manual_review")
        rows.append({
            "code": code,
            "ruleId": spec.get("ruleId"),
            "label": spec.get("label"),
            "value": None,
            "limit": None,
            "marginRatio": None,
            "status": "not_applicable" if state == "not_applicable" else "manual_review",
            "standard": spec.get("standard"),
            "clauseFocus": spec.get("note") or "完整验算目录保留项",
            "source": "required_check_matrix",
            **evidence,
        })
    return rows


def build_stability_distribution(project: Project) -> dict[str, Any]:
    availability = input_availability(project)
    latest = project.calculation_results[-1] if project.calculation_results else None
    if latest is None:
        factors = _pending_stability_factors(project, set(), availability)
        return {
            "factors": factors,
            "summary": {
                "count": len(factors), "calculatedCount": 0, "pendingCount": len(factors),
                "passCount": 0, "warningCount": len(factors), "failCount": 0,
                "controllingCode": None, "controllingLabel": None,
            },
            "message": "尚未生成稳定性数值；完整目录仍列出所需资料和后续动作。",
        }

    checks: list[dict[str, Any]] = []
    checks.extend([item if isinstance(item, dict) else item.model_dump(mode="json", by_alias=True) for item in (latest.checks or [])])
    for stage in latest.stage_results or []:
        checks.extend([item if isinstance(item, dict) else item.model_dump(mode="json", by_alias=True) for item in (stage.stability_checks or [])])
        checks.extend([item if isinstance(item, dict) else item.model_dump(mode="json", by_alias=True) for item in (stage.checks or [])])

    def limit_from_tokens(*tokens: str) -> float | None:
        lowered = tuple(token.lower() for token in tokens)
        for check in checks:
            haystack = " ".join(str(check.get(key) or "") for key in ("ruleId", "category", "message", "name")).lower()
            if all(token in haystack for token in lowered):
                value = _check_limit(check)
                if value is not None:
                    return value
        return None

    gv = latest.governing_values
    detailed = latest.stability_detailed_result
    factors: list[dict[str, Any]] = []
    candidates = [
        _factor_record(
            "embedment", "嵌固稳定", getattr(gv, "embedment_safety_factor_min", None),
            limit_from_tokens("embed") or limit_from_tokens("嵌固"),
            "JGJ 120-2012 / GB 55003-2021", "支护结构嵌固与整体传力稳定", "governingValues",
        ),
        _factor_record(
            "heave", "坑底抗隆起", getattr(gv, "heave_safety_factor_min", None) or getattr(detailed, "heave_factor", None),
            limit_from_tokens("heave") or limit_from_tokens("隆起"),
            "JGJ 120-2012 / GB 55003-2021", "坑底隆起和软弱层控制", "stabilityDetailedResult",
        ),
        _factor_record(
            "seepage", "渗流稳定", getattr(gv, "seepage_safety_factor_min", None) or getattr(detailed, "seepage_factor", None),
            limit_from_tokens("seepage") or limit_from_tokens("渗流"),
            "JGJ 120-2012 / GB 55003-2021", "地下水渗流与管涌风险", "stabilityDetailedResult",
        ),
        _factor_record(
            "uplift", "承压水突涌", getattr(detailed, "confined_uplift_factor", None),
            limit_from_tokens("uplift") or limit_from_tokens("突涌"),
            "JGJ 120-2012 / GB 55003-2021", "承压水作用和坑底突涌", "stabilityDetailedResult",
        ),
        _factor_record(
            "overall", "整体稳定", getattr(detailed, "overall_stability_factor", None),
            limit_from_tokens("overall", "stability") or limit_from_tokens("整体", "稳定"),
            "JGJ 120-2012 / GB 55003-2021", "圆弧滑动或控制剖面整体稳定", "stabilityDetailedResult",
        ),
    ]
    factors.extend([item for item in candidates if item is not None])

    # Include stage-specific stability checks when they expose numeric factor and limit.
    seen = {item["code"] for item in factors}
    for index, check in enumerate(checks):
        rule_id = str(check.get("ruleId") or check.get("category") or "").strip()
        haystack = f"{rule_id} {check.get('message') or ''}".lower()
        is_factor_check = any(token in haystack for token in ("safety factor", "stability factor", "factor", "安全系数"))
        is_known_factor_rule = any(token in haystack for token in (
            "overall-stability-circular", "overall stability", "整体稳定",
            "heave", "抗隆起", "uplift", "突涌", "seepage factor", "渗流安全",
            "embedment", "嵌固稳定",
        ))
        if not (is_factor_check or is_known_factor_rule):
            continue
        value = _number(check.get("calculatedValue"))
        limit = _check_limit(check)
        if value is None or limit is None:
            continue
        code = rule_id or f"stage-{index + 1}"
        family = None
        if "uplift" in haystack or "突涌" in haystack:
            family = "uplift"
        elif "heave" in haystack or "隆起" in haystack:
            family = "heave"
        elif "seepage" in haystack or "渗流" in haystack:
            family = "seepage"
        elif "embed" in haystack or "嵌固" in haystack:
            family = "embedment"
        elif "overall" in haystack or "整体稳定" in haystack:
            family = "overall"
        if code in seen or (family and family in seen):
            continue
        seen.add(code)
        family_labels = {
            "uplift": "承压水突涌", "heave": "坑底抗隆起", "seepage": "渗流稳定",
            "embedment": "嵌固稳定", "overall": "整体稳定",
        }
        item = _factor_record(
            code, str(check.get("name") or check.get("category") or family_labels.get(family) or rule_id or "稳定性检查"),
            value, limit, str(check.get("standard") or check.get("standardRef") or "JGJ 120-2012"),
            str(check.get("clause") or check.get("message") or "稳定性条文检查"), "stageCheck",
        )
        if item:
            factors.append(item)

    factors.extend(_pending_stability_factors(project, {str(item.get("code")) for item in factors}, availability))
    margins = [float(item["marginRatio"]) for item in factors if item.get("marginRatio") is not None]
    values = [float(item["value"]) for item in factors if item.get("value") is not None]
    status_counts = {status: sum(1 for item in factors if item.get("status") == status) for status in ("pass", "warning", "fail")}
    pending_count = sum(item.get("value") is None for item in factors)
    numeric_factors = [item for item in factors if item.get("value") is not None]
    controlling = min(numeric_factors, key=lambda item: item.get("marginRatio") if item.get("marginRatio") is not None else float("inf"), default=None)
    return {
        "factors": factors,
        "summary": {
            "count": len(factors),
            "calculatedCount": len(numeric_factors),
            "pendingCount": pending_count,
            "minimumFactor": round(min(values), 4) if values else None,
            "maximumFactor": round(max(values), 4) if values else None,
            "averageFactor": round(fmean(values), 4) if values else None,
            "minimumMarginRatio": round(min(margins), 4) if margins else None,
            "averageMarginRatio": round(fmean(margins), 4) if margins else None,
            "passCount": status_counts["pass"],
            "warningCount": status_counts["warning"] + sum(item.get("status") == "manual_review" for item in factors),
            "failCount": status_counts["fail"],
            "controllingCode": controlling.get("code") if controlling else None,
            "controllingLabel": controlling.get("label") if controlling else None,
        },
        "message": "稳定与水控制目录同时展示已计算系数和待补证据；接近 1.0 的数值项目优先复核。",
    }


def build_scheme_comparison(project: Project) -> dict[str, Any]:
    system = project.retaining_system
    repair = system.support_layout_repair if system else None
    candidates = list(repair.candidates or [])[:3] if repair else []
    full_rows = list(repair.candidate_full_calculations or []) if repair else []
    if not full_rows and project.calculation_results:
        full_rows = list(project.calculation_results[-1].report_diagram_data.get("candidateFullCalculationComparison") or [])
    full_by_id = {str(row.get("candidateId") or ""): row for row in full_rows}
    rows: list[dict[str, Any]] = []
    latest = project.calculation_results[-1] if project.calculation_results else None
    for index, candidate in enumerate(candidates):
        full = full_by_id.get(str(candidate.id)) or dict(candidate.full_calculation or {})
        if not full and latest and repair and str(candidate.id) == str(repair.selected_candidate_id):
            gv = latest.governing_values
            stability = latest.stability_detailed_result
            full = {
                "maxSupportAxialForce": gv.max_support_axial_force,
                "maxDisplacement": gv.max_displacement,
                "maxWaleMoment": gv.max_wall_moment,
                "minStabilitySafetyFactor": (
                    getattr(stability, "min_safety_factor", None)
                    or getattr(latest.design_review_summary, "min_stability_safety_factor", None)
                    or gv.embedment_safety_factor_min
                ),
                "decisionRank": candidate.rank,
                "recommendedByFullCalculation": True,
                "currentAdoptedCalculation": True,
            }
        rows.append({
            "schemeLabel": chr(65 + index),
            "candidateId": candidate.id,
            "rank": candidate.rank,
            "score": candidate.score,
            "status": candidate.status,
            "topologyFamily": candidate.variable_summary.get("topologyFamily"),
            "schemeName": candidate.variable_summary.get("schemeLabel") or candidate.variable_summary.get("topologyFamily") or "支撑候选",
            "supportCount": candidate.support_count,
            "columnCount": candidate.column_count,
            "maxSpanLength": candidate.max_span_length,
            "maxBaySpacing": candidate.max_bay_spacing,
            "crossingCount": candidate.crossing_count,
            "hardPassed": bool(candidate.hard_constraints.get("passed")),
            "constructabilityNote": candidate.constructability_note,
            "fullCalculationReady": bool(full),
            "maxSupportAxialForce": full.get("maxSupportAxialForce"),
            "maxDisplacement": full.get("maxDisplacement"),
            "maxWaleMoment": full.get("maxWaleMoment"),
            "minStabilitySafetyFactor": full.get("minStabilitySafetyFactor"),
            "decisionScore": full.get("decisionScore"),
            "decisionRank": full.get("decisionRank"),
            "paretoRank": full.get("paretoRank"),
            "paretoFront": bool(full.get("paretoFront")),
            "paretoObjectives": dict(full.get("paretoObjectives") or {}),
            "materialIndex": full.get("materialIndex"),
            "constructabilityRisk": full.get("constructabilityRisk"),
            "recommended": bool(full.get("recommendedByFullCalculation")),
        })
    return {
        "candidateCount": len(rows),
        "fullCalculationCount": sum(1 for row in rows if row.get("fullCalculationReady")),
        "selectedCandidateId": repair.selected_candidate_id if repair else None,
        "rows": rows,
        "comparisonAvailable": len(rows) >= 2,
        "paretoFrontCount": sum(1 for row in rows if row.get("paretoFront")),
        "rankingMethod": "fail gate -> Pareto front -> weighted engineering score",
    }


def _all_calculation_checks(project: Project) -> list[dict[str, Any]]:
    latest = project.calculation_results[-1] if project.calculation_results else None
    if latest is None:
        return []
    rows: list[dict[str, Any]] = []
    rows.extend([item if isinstance(item, dict) else item.model_dump(mode="json", by_alias=True) for item in (latest.checks or [])])
    for stage in latest.stage_results or []:
        stage_label = str(getattr(stage, "stage_id", "") or getattr(stage, "segment_id", ""))
        segment_id = str(getattr(stage, "segment_id", "") or "")
        for source_name, source_rows in (
            ("stageStability", stage.stability_checks or []),
            ("stageRC", stage.rc_checks or []),
            ("stageCheck", stage.checks or []),
        ):
            for raw in source_rows:
                item = raw if isinstance(raw, dict) else raw.model_dump(mode="json", by_alias=True)
                item = dict(item)
                item.setdefault("source", source_name)
                item.setdefault("stageLabel", stage_label)
                item.setdefault("segmentId", segment_id)
                rows.append(item)
    return rows


def _object_index(project: Project) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    objects: dict[str, dict[str, Any]] = {
        project.id: {"objectType": "project", "objectCode": project.name, "objectScope": "system"},
    }
    segment_walls: dict[str, dict[str, Any]] = {}
    ret = project.retaining_system
    if ret is None:
        return objects, segment_walls
    objects[ret.id] = {"objectType": "retaining_system", "objectCode": "支护体系", "objectScope": "system"}
    for wall in ret.diaphragm_walls:
        row = {
            "objectType": "diaphragm_wall",
            "objectCode": wall.panel_code,
            "objectScope": "wall",
            "wallId": wall.id,
            "wallCode": wall.panel_code,
            "segmentId": wall.segment_id,
        }
        objects[wall.id] = row
        segment_walls[str(wall.segment_id)] = row
    for collection, object_type, scope in (
        (ret.crown_beams, "crown_beam", "crown_beam"),
        (ret.wale_beams, "wale_beam", "wale"),
        (ret.ring_beams, "ring_beam", "wale"),
        (ret.supports, "support", "support"),
        (ret.support_nodes, "support_wale_node", "node"),
        (ret.columns, "temporary_column", "column"),
    ):
        for item in collection or []:
            code = getattr(item, "code", None) or getattr(item, "panel_code", None) or item.id
            objects[item.id] = {"objectType": object_type, "objectCode": code, "objectScope": scope}
            foundation = getattr(item, "foundation_design", None)
            if foundation is not None:
                objects[foundation.id] = {"objectType": "column_foundation", "objectCode": foundation.code, "objectScope": "foundation"}
    return objects, segment_walls


def _check_context(
    check: dict[str, Any],
    objects: dict[str, dict[str, Any]],
    segment_walls: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    object_id = str(check.get("objectId") or check.get("hostId") or "")
    segment_id = str(check.get("segmentId") or "")
    context = dict(objects.get(object_id) or segment_walls.get(object_id) or segment_walls.get(segment_id) or {})
    context.setdefault("objectType", str(check.get("objectType") or "calculation_object"))
    context.setdefault("objectCode", str(check.get("objectCode") or check.get("hostCode") or object_id or "整体"))
    context.setdefault("objectScope", "system")
    return context


def _verification_category(check: dict[str, Any]) -> str:
    text = " ".join(str(check.get(key) or "") for key in ("ruleId", "category", "name", "message", "clauseReference")).lower()
    if any(token in text for token in ("seepage", "uplift", "piping", "water", "渗流", "突涌", "水位", "降水")):
        return "hydraulic"
    if any(token in text for token in (
        "heave", "overall stability", "整体稳定", "抗隆起", "嵌固", "embedment", "sliding", "overturn",
        "buckling", "stability", "稳定", "长细比", "slenderness",
    )):
        return "stability"
    if any(token in text for token in (
        "displacement", "deflection", "stiffness", "deformation", "crack", "位移", "挠度", "刚度", "变形", "裂缝",
        "condition number", "residual", "settlement",
    )):
        return "stiffness"
    if any(token in text for token in (
        "construction", "cover", "anchorage", "lap", "coupler", "congestion", "施工", "保护层", "锚固", "搭接", "拥挤",
    )):
        return "constructability"
    if any(token in text for token in (
        "moment", "shear", "axial", "bearing", "capacity", "reinforcement", "flexure", "stress", "抗弯", "抗剪",
        "轴压", "承载力", "承压", "配筋", "强度", "foundation",
    )):
        return "strength"
    return "other"


def _verification_direction(check: dict[str, Any], category: str, canonical_rule_id: str | None = None) -> str:
    # Determine the inequality from the check identity and unit, not from long
    # explanatory prose.  The former text scan saw words such as ``保护层`` in
    # a crack-width explanation and inverted w <= w_lim into a minimum check;
    # it also treated support utilization as a stability factor.  Both errors
    # created false safety factors below 1.0 and an impossible design loop.
    identity = " ".join([str(canonical_rule_id or ""), *(str(check.get(key) or "") for key in ("ruleId", "category", "name"))]).lower()
    unit = str(check.get("unit") or "").lower()
    # The weak-layer index is c/20 + phi/25 + gamma'/10: a larger value is
    # safer.  Treating it as a maximum/risk index inverted a passing 2.51/1.35
    # screen into a false 0.54 safety factor and kept the design loop open.
    if str(canonical_rule_id or "").upper() == "LOCAL_WEAK_LAYER":
        return "minimum"
    if any(token in unit for token in ("utilization", "ratio", "利用率")) or any(
        token in identity for token in ("utilization", "interaction", "crack", "deflection", "displacement", "slenderness")
    ):
        return "maximum"
    # The wall construction catalogue groups minimum steel area, minimum main
    # bar clear spacing and *maximum* horizontal-bar spacing under one label.
    # Preserve the raw horizontal-spacing inequality before applying the
    # canonical minimum-reinforcement direction.
    if "construction-check-horizontal" in identity:
        return "maximum"
    if "construction-check-conc" in identity or "clearance" in identity or "净空" in identity:
        return "minimum"
    if any(token in identity for token in (
        "minimum reinforcement", "minimum_as", "minrebar", "min_rebar", "anchorage", "lap", "cover",
        "最小配筋", "锚固", "搭接", "保护层", "minimum grade",
    )):
        return "minimum"
    if category in {"stability", "hydraulic"} and any(token in identity for token in ("factor", "stability", "heave", "uplift", "embedment", "seepage", "piping", "稳定", "隆起", "突涌", "嵌固")):
        return "minimum"
    return "maximum"


def _catalog_spec_for_check(check: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    specs = {str(item["ruleId"]): item for item in VERIFICATION_CATALOG}
    text = " ".join(str(check.get(key) or "") for key in ("ruleId", "category", "name", "message", "clauseReference")).upper()
    scope = str(context.get("objectScope") or "system")

    rule_id: str | None = None
    if "UPLIFT" in text or "PIPING" in text or "突涌" in text:
        rule_id = "PIPING"
    elif "LAYERED-SEEPAGE" in text or "SEEPAGE" in text or "渗流" in text:
        rule_id = "SEEPAGE"
    elif "HEAVE" in text or "隆起" in text:
        rule_id = "BOTTOM_HEAVE"
    elif "OVERALL-STABILITY" in text or "OVERALL STABILITY" in text or "整体稳定" in text:
        rule_id = "GLOBAL_STABILITY"
    elif "WEAK" in text and ("LAYER" in text or "下卧" in text):
        rule_id = "LOCAL_WEAK_LAYER"
    elif "EMBEDMENT" in text or "嵌固" in text:
        rule_id = "EMBEDMENT_STABILITY"
    elif "DEWATERING-STAGE" in text or "DEWATERING FAILURE" in text or "停泵" in text:
        rule_id = "DEWATERING_FAILURE"
    elif "SUPPORT-LIFECYCLE-PATH" in text or "REPLACEMENT-STIFFNESS" in text:
        rule_id = "REPLACEMENT_PATH_STABILITY"
    elif "SUPPORT-CONSTRUCTION-EFFECT" in text:
        rule_id = "SUPPORT_PRELOAD"
    elif "SUPPORT-DEEP-DESIGN-STABILITY" in text or (scope == "support" and "STEEL-COMPRESSION" in text):
        rule_id = "SUPPORT_STABILITY"
    elif "LOCAL-NODE-ANALYTICAL-SUBMODEL" in text:
        rule_id = "LOCAL_NODE_SUBMODEL"
    elif "SUPPORT-NODE-DETAILING-READINESS" in text or "NODE-BEARING" in text:
        rule_id = "SUPPORT_NODE"
    elif "COLUMN-PILE-CAPACITY" in text:
        rule_id = "COLUMN_FOUNDATION"
    elif "BEARING-SUBSET" in text or scope == "foundation":
        rule_id = "BEARING_CAPACITY"
    elif scope == "wall" and "DIAPHRAGM-CONSTRUCTION" in text and any(token in text for token in ("-CONC", "-COVER")):
        rule_id = "CONCRETE_DURABILITY"
    elif scope == "wall" and "DIAPHRAGM-CONSTRUCTION" in text and any(token in text for token in ("-MAINBAR", "-HORIZONTAL")):
        rule_id = "WALL_MIN_REBAR"
    elif scope == "wall" and "DIAPHRAGM-CONSTRUCTION" in text:
        rule_id = "DIAPHRAGM_WALL_THICKNESS"
    elif scope == "wall" and ("DEFORMATION" in text or "DISPLACEMENT" in text or "位移" in text):
        rule_id = "WALL_DISPLACEMENT"
    elif scope == "wall" and ("FLEXURE" in text or "抗弯" in text):
        rule_id = "WALL_FLEXURE"
    elif scope == "wall" and ("SHEAR" in text or "抗剪" in text):
        rule_id = "WALL_SHEAR"
    elif scope == "wall" and ("MINREBAR" in text or "MINIMUM REINFORCEMENT" in text or "最小配筋" in text):
        rule_id = "WALL_MIN_REBAR"
    elif scope == "wall" and "CRACK" in text:
        rule_id = "WALL_CRACK_CONTROL"
    elif scope in {"wale", "crown_beam"} and "DEFLECTION" in text:
        rule_id = "WALE_DEFLECTION"
    elif scope == "crown_beam" and "FLEXURE" in text:
        rule_id = "CROWN_BEAM_FLEXURE"
    elif scope == "crown_beam" and "SHEAR" in text:
        rule_id = "CROWN_BEAM_SHEAR"
    elif scope == "wale" and "FLEXURE" in text:
        rule_id = "WALE_FLEXURE"
    elif scope == "wale" and "SHEAR" in text:
        rule_id = "WALE_SHEAR"
    elif scope == "node" and ("BEARING" in text or "承压" in text or "WALE-NODE-REBAR-COORDINATION" in text):
        rule_id = "SUPPORT_NODE"
    elif scope == "support" and ("STABILITY" in text or "SLENDER" in text or "BUCKLING" in text):
        rule_id = "SUPPORT_STABILITY"
    elif scope == "support" and ("CONSTRUCTION-EFFECT" in text or "ECCENTRIC" in text):
        rule_id = "SUPPORT_COMBINED"
    elif scope == "support" and ("AXIAL" in text or "COMPRESSION" in text):
        rule_id = "SUPPORT_AXIAL"
    elif scope == "column" and ("STABILITY" in text or "SLENDER" in text or "BUCKLING" in text):
        rule_id = "COLUMN_STABILITY"
    elif scope == "column" and ("AXIAL" in text or "COMPRESSION" in text):
        rule_id = "COLUMN_AXIAL"
    elif scope == "foundation" or "FOUNDATION" in text:
        rule_id = "BEARING_CAPACITY"
    elif "ANCHORAGE" in text or "LAP" in text:
        rule_id = "REBAR_CONGESTION" if "CONGEST" in text else None
    return specs.get(str(rule_id)) if rule_id else None


def _verification_label(check: dict[str, Any], context: dict[str, Any]) -> str:
    rule = str(check.get("ruleId") or check.get("category") or check.get("name") or "规范校核")
    prefix = str(context.get("objectCode") or "")
    clean = rule.replace("_", "-")
    return f"{prefix}{' · ' if prefix else ''}{clean}"


def _status_rank(value: Any) -> int:
    return {"pass": 0, "not_applicable": 0, "preliminary": 1, "manual_review": 2, "warning": 3, "fail": 4}.get(str(value), 2)


def _controlling(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def key(item: dict[str, Any]) -> tuple[int, float]:
        factor = item.get("safetyFactor")
        return (_status_rank(item.get("status")), -(float(factor) if factor is not None else 1.0e9))
    return dict(max(rows, key=key))


def _target_key(item: dict[str, Any]) -> str:
    category = str(item.get("category") or "")
    rule_id = str(item.get("ruleId") or "").lower()
    if "support" in rule_id and category == "stability":
        return "support_stability"
    if "column" in rule_id and category == "stability":
        return "column_stability"
    if "embedment" in rule_id:
        return "embedment"
    if "heave" in rule_id:
        return "base_heave"
    if "seepage" in rule_id:
        return "seepage"
    if "piping" in rule_id or "uplift" in rule_id:
        return "confined_uplift"
    if "global" in rule_id:
        return "overall_stability"
    return category


def _target_safety_factor(item: dict[str, Any], targets: dict[str, float], reserve_threshold: float) -> float:
    """Return the project reserve target only for demand/capacity checks.

    Prescriptive minimum reinforcement, construction detailing and review-only
    lifecycle records have a compliance threshold of 1.0.  Applying the
    project structural reserve to those rows made a code-compliant D@spacing
    minimum look perpetually under-designed even though member capacity had
    ample reserve.
    """
    rule_id = str(item.get("ruleId") or "").upper()
    category = str(item.get("category") or "")
    if category in {"constructability", "other"} or rule_id in {
        "WALL_MIN_REBAR", "CONCRETE_DURABILITY", "REBAR_CONGESTION",
        "REPLACEMENT_PATH_STABILITY", "SUPPORT_PRELOAD", "SUPPORT_NODE",
        "LOCAL_NODE_SUBMODEL",
    }:
        return 1.0
    return float(targets.get(_target_key(item), reserve_threshold))


def build_verification_distribution(project: Project) -> dict[str, Any]:
    """Build a complete design-stage programme plus traceable object results."""
    availability = input_availability(project)
    rows = _all_calculation_checks(project)
    reserve_ratio = max(0.0, float(getattr(project.design_settings, "stability_reserve_ratio", 0.10) or 0.0))
    reserve_threshold = 1.0 + reserve_ratio
    objects, segment_walls = _object_index(project)
    normalized: list[dict[str, Any]] = []
    for index, check in enumerate(rows):
        context = _check_context(check, objects, segment_walls)
        spec = _catalog_spec_for_check(check, context)
        category = str(spec.get("category")) if spec else _verification_category(check)
        direction = _verification_direction(check, category, str(spec.get("ruleId")) if spec else None)
        value = _number(check.get("calculatedValue") if "calculatedValue" in check else check.get("calculated_value"))
        limit = _number(check.get("limitValue") if "limitValue" in check else check.get("limit_value"))
        utilization = _number(check.get("utilization"))
        safety_factor: float | None = None
        if utilization is not None and utilization > 0:
            safety_factor = 1.0 / utilization
        elif value is not None and limit is not None and value > 0 and limit > 0:
            safety_factor = value / limit if direction == "minimum" else limit / value
        if safety_factor is not None and (safety_factor <= 0 or safety_factor > 1.0e6):
            safety_factor = None
        canonical_rule = str(spec.get("ruleId") or "") if spec else ""
        if canonical_rule in {"SUPPORT_PRELOAD", "REPLACEMENT_PATH_STABILITY"}:
            # These rows document the magnitude of construction effects and the
            # confirmed install/remove sequence.  Their two numbers are not a
            # demand/capacity pair, so presenting their quotient as a safety
            # factor produces a false failure.
            safety_factor = None
        original_status = str(check.get("status") or "manual_review")
        status = original_status
        if safety_factor is not None:
            status = "fail" if safety_factor < 1.0 else "warning" if safety_factor < reserve_threshold else "pass"
            if original_status == "fail":
                status = "fail"
            elif original_status in {"warning", "manual_review", "preliminary"}:
                # A source rule deliberately classified as screening/review is
                # not promoted to a hard failure by presentation arithmetic.
                # Its numeric index remains visible, while the authoritative
                # engineering severity and required professional action are
                # preserved.
                status = original_status
        raw_rule_id = str(check.get("ruleId") or check.get("category") or f"check-{index + 1}")
        canonical_id = str(spec.get("ruleId")) if spec else raw_rule_id
        object_id = str(check.get("objectId") or check.get("hostId") or context.get("wallId") or "")
        normalized.append({
            "id": f"{canonical_id}:{object_id or index}:{check.get('stageLabel') or ''}",
            "ruleId": canonical_id,
            "rawRuleId": raw_rule_id,
            "label": str(spec.get("label")) if spec else _verification_label(check, context),
            "category": category,
            "scope": str(spec.get("scope")) if spec else str(context.get("objectScope") or "system"),
            "catalogued": bool(spec),
            "designValue": round(value, 6) if value is not None else None,
            "limitValue": round(limit, 6) if limit is not None else None,
            "unit": check.get("unit") or "-",
            "direction": direction,
            "safetyFactor": round(safety_factor, 4) if safety_factor is not None else None,
            "utilization": round(1.0 / safety_factor, 4) if safety_factor not in (None, 0.0) else utilization,
            "status": status,
            "originalStatus": original_status,
            "standard": (spec.get("standard") if spec else None) or check.get("standard") or check.get("standardRef") or check.get("standardName"),
            "clause": check.get("clauseReference") or check.get("clause"),
            "message": check.get("message"),
            "objectId": object_id or None,
            "objectCode": context.get("objectCode"),
            "objectType": context.get("objectType"),
            "wallId": context.get("wallId"),
            "wallCode": context.get("wallCode"),
            "stageLabel": check.get("stageLabel"),
            "source": check.get("source") or "calculationResult",
            "evidenceState": "calculated",
            "implementationState": str(spec.get("implementation") or "implemented") if spec else "implemented",
            "missingInputs": [],
            "missingInputDetails": [],
            "nextAction": check.get("recommendedAction") or check.get("recommendation"),
        })

    by_rule: dict[str, list[dict[str, Any]]] = {}
    for item in normalized:
        by_rule.setdefault(str(item["ruleId"]), []).append(item)

    records: list[dict[str, Any]] = []
    for spec in VERIFICATION_CATALOG:
        rule_id = str(spec["ruleId"])
        items = by_rule.pop(rule_id, [])
        if items:
            control = _controlling(items)
            by_object: dict[str, list[dict[str, Any]]] = {}
            for item in items:
                key = str(item.get("objectId") or item.get("wallId") or item.get("stageLabel") or "整体")
                by_object.setdefault(key, []).append(item)
            object_results = [_controlling(group) for group in by_object.values()]
            object_results.sort(key=lambda item: (-_status_rank(item.get("status")), float(item.get("safetyFactor") or 1.0e9), str(item.get("objectCode") or "")))
            control.update({
                "id": f"catalog:{rule_id}",
                "objectCount": len(by_object),
                "resultRecordCount": len(items),
                "objectResults": object_results,
                "catalogued": True,
                "scope": spec.get("scope"),
                "implementationState": spec.get("implementation"),
            })
            records.append(control)
        else:
            evidence = missing_evidence_record(project, spec, availability=availability)
            state = str(evidence.get("evidenceState"))
            records.append({
                "id": f"required:{rule_id}", "ruleId": rule_id, "rawRuleId": None,
                "label": spec["label"], "category": spec["category"], "scope": spec["scope"],
                "catalogued": True, "designValue": None, "limitValue": None, "unit": "-",
                "direction": "minimum" if spec["category"] in {"stability", "hydraulic"} else "maximum",
                "safetyFactor": None, "utilization": None,
                "status": "not_applicable" if state == "not_applicable" else "manual_review",
                "originalStatus": "manual_review", "standard": spec["standard"], "clause": None,
                "message": evidence["message"], "objectId": None, "objectCode": None, "objectType": None,
                "wallId": None, "wallCode": None, "stageLabel": None,
                "source": "required_check_matrix", "objectCount": 0, "resultRecordCount": 0,
                "objectResults": [], **evidence,
            })

    # Preserve solver checks that do not yet map to the formal programme.  They
    # remain supplemental and never suppress a required catalogue row.
    for raw_rule_id, items in by_rule.items():
        control = _controlling(items)
        control.update({
            "id": f"supplemental:{raw_rule_id}",
            "objectCount": len({str(item.get("objectId") or item.get("stageLabel") or "整体") for item in items}),
            "resultRecordCount": len(items),
            "objectResults": [_controlling(group) for group in ({key: [row for row in items if str(row.get("objectId") or row.get("stageLabel") or "整体") == key] for key in {str(row.get("objectId") or row.get("stageLabel") or "整体") for row in items}}).values()],
            "catalogued": False,
        })
        records.append(control)

    targets = safety_targets(project)
    for item in records:
        item["targetSafetyFactor"] = round(_target_safety_factor(item, targets, reserve_threshold), 4)
        sf = item.get("safetyFactor")
        if (
            sf is not None
            and item.get("status") != "fail"
            and str(item.get("originalStatus") or "") not in {"warning", "manual_review", "preliminary"}
        ):
            item["status"] = "warning" if float(sf) < float(item["targetSafetyFactor"]) else "pass"

    # Build the per-wall programme without losing the stage/object evidence that
    # the former category collapse discarded.
    wall_rule_ids = {
        str(spec["ruleId"])
        for spec in VERIFICATION_CATALOG
        if str(spec.get("scope")) == "wall"
    }
    wall_objects: list[dict[str, Any]] = []
    ret = project.retaining_system
    if ret:
        specs_by_id = {str(spec["ruleId"]): spec for spec in VERIFICATION_CATALOG}
        for wall in ret.diaphragm_walls:
            wall_rows = [row for row in normalized if str(row.get("wallId") or "") == wall.id and str(row.get("ruleId")) in wall_rule_ids]
            rows_by_rule: dict[str, list[dict[str, Any]]] = {}
            for row in wall_rows:
                rows_by_rule.setdefault(str(row["ruleId"]), []).append(row)
            checks: list[dict[str, Any]] = []
            for rule_id in sorted(wall_rule_ids):
                spec = specs_by_id[rule_id]
                candidates = rows_by_rule.get(rule_id, [])
                if candidates:
                    item = _controlling(candidates)
                    item["stageResults"] = sorted(candidates, key=lambda row: str(row.get("stageLabel") or ""))
                else:
                    evidence = missing_evidence_record(project, spec, availability=availability)
                    state = str(evidence.get("evidenceState"))
                    item = {
                        "id": f"wall:{wall.id}:{rule_id}", "ruleId": rule_id, "label": spec["label"],
                        "category": spec["category"], "scope": "wall", "catalogued": True,
                        "designValue": None, "limitValue": None, "unit": "-", "safetyFactor": None,
                        "utilization": None, "status": "not_applicable" if state == "not_applicable" else "manual_review",
                        "standard": spec["standard"], "message": evidence["message"],
                        "objectId": wall.id, "objectCode": wall.panel_code, "objectType": "diaphragm_wall",
                        "wallId": wall.id, "wallCode": wall.panel_code, "stageResults": [], **evidence,
                    }
                item["targetSafetyFactor"] = round(_target_safety_factor(item, targets, reserve_threshold), 4)
                checks.append(item)
            fail_count = sum(item.get("status") == "fail" for item in checks)
            unresolved_count = sum(item.get("status") in {"warning", "manual_review"} for item in checks)
            wall_objects.append({
                "wallId": wall.id,
                "wallCode": wall.panel_code,
                "wallType": "diaphragm_wall",
                "wallTypeLabel": "地下连续墙",
                "segmentId": wall.segment_id,
                "designFaceCode": wall.design_face_code,
                "lengthM": wall.design_length,
                "thicknessM": wall.thickness,
                "topElevationM": wall.top_elevation,
                "bottomElevationM": wall.bottom_elevation,
                "status": "fail" if fail_count else "warning" if unresolved_count else "pass",
                "summary": {
                    "checkCount": len(checks), "calculatedCount": sum(item.get("evidenceState") == "calculated" for item in checks),
                    "failCount": fail_count, "reviewCount": unresolved_count,
                },
                "checks": checks,
            })

    category_order = {"strength": 0, "stiffness": 1, "stability": 2, "hydraulic": 3, "constructability": 4, "other": 5}
    records.sort(key=lambda item: (category_order.get(str(item.get("category")), 9), 0 if item.get("catalogued") else 1, -_status_rank(item.get("status")), float(item.get("safetyFactor") or 1.0e9), str(item.get("label"))))
    summary: dict[str, Any] = {}
    for category in ("strength", "stiffness", "stability", "hydraulic", "constructability", "other"):
        items = [item for item in records if item.get("category") == category]
        factors = [float(item["safetyFactor"]) for item in items if item.get("safetyFactor") is not None]
        summary[category] = {
            "count": len(items),
            "passCount": sum(item.get("status") == "pass" for item in items),
            "warningCount": sum(item.get("status") in {"warning", "manual_review"} for item in items),
            "failCount": sum(item.get("status") == "fail" for item in items),
            "notApplicableCount": sum(item.get("status") == "not_applicable" for item in items),
            "minimumSafetyFactor": round(min(factors), 4) if factors else None,
            "averageSafetyFactor": round(fmean(factors), 4) if factors else None,
        }
    numeric = [item for item in records if item.get("safetyFactor") is not None]
    controlling = min(numeric, key=lambda item: float(item["safetyFactor"]), default=None)
    summary["overall"] = {
        "count": len(records),
        "catalogCount": len(VERIFICATION_CATALOG),
        "supplementalCount": sum(not bool(item.get("catalogued")) for item in records),
        "wallObjectCount": len(wall_objects),
        "minimumSafetyFactor": controlling.get("safetyFactor") if controlling else None,
        "reserveRatio": round(reserve_ratio, 4),
        "reserveThreshold": round(reserve_threshold, 4),
        "controllingLabel": controlling.get("label") if controlling else None,
        "failCount": sum(item.get("status") == "fail" for item in records),
        "warningCount": sum(item.get("status") in {"warning", "manual_review"} for item in records),
    }
    summary["evidenceCoverage"] = coverage_summary([item for item in records if item.get("catalogued")])

    missing_codes: dict[str, dict[str, Any]] = {}
    for item in records:
        for detail in item.get("missingInputDetails") or []:
            code = str(detail.get("code"))
            row = missing_codes.setdefault(code, {**detail, "affectedCheckCount": 0, "affectedChecks": []})
            row["affectedCheckCount"] += 1
            if len(row["affectedChecks"]) < 8:
                row["affectedChecks"].append(item.get("label"))
    missing_input_summary = sorted(missing_codes.values(), key=lambda item: (not bool(item.get("designStageAvailable")), str(item.get("stage")), str(item.get("label"))))
    return {
        "catalogVersion": "3.52-design-verification-programme-v1",
        "records": records,
        "wallObjects": wall_objects,
        "inputRequirements": input_requirement_details(project, availability=availability),
        "missingInputSummary": missing_input_summary,
        "summary": summary,
        "evidenceCoverage": summary["evidenceCoverage"],
        "message": "完整目录区分已计算、缺资料、待重算、专项复核和不适用；安全系数按抗力/作用效应或允许值/设计值表达。",
    }
