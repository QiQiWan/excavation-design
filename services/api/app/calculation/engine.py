from __future__ import annotations

import hashlib
import json
import math
import os
import gc
from typing import Any

from app.calculation.earth_pressure import calculate_lateral_pressure_profile
from app.calculation.global_coupled import solve_global_wall_wale_support_system
from app.calculation.stability_detailed import build_reviewable_stability_package
from app.drawings.detail_sheets import generate_construction_detail_sheets
from app.calculation.support_forces import estimate_support_axial_forces
from app.calculation.support_nodes import update_support_node_design
from app.calculation.wale_beam import build_wale_beam_envelope, support_axial_area, support_elastic_modulus
from app.calculation.wall_internal_force import analyze_wall_on_elastic_foundation
from app.geometry.consistency import geometry_consistency_summary
from app.version import SOFTWARE_VERSION, ALGORITHM_VERSION, RULE_SET_VERSION, EXPORT_SCHEMA_VERSION
from app.geology.section import extract_representative_section
from app.geology.model_builder import ensure_geological_model_covers_excavation, geological_coverage_audit
from app.rules.gb50007.foundation_rules import check_foundation_bearing_pressure
from app.rules.gb50009.load_combination_rules import design_effect_standard_to_uls
from app.rules.gb50009.load_combinations import check_combination_documented, combination_record
from app.rules.gb50010.rc_section_rules import check_rc_rectangular_axial_capacity, check_rectangular_shear_capacity, design_rectangular_flexural_reinforcement, rectangular_flexural_capacity_knm_per_m
from app.rules.gb50010.detailing_rules import check_crack_width, check_rebar_anchorage_and_lap
from app.rules.gb50010.reinforcement_rules import check_minimum_wall_reinforcement
from app.rules.gb50017.steel_support_rules import check_steel_pipe_support_axial_capacity
from app.rules.jgj120_2012.retaining_wall_rules import check_diaphragm_wall_construction, check_embedment_stability, importance_factor
from app.rules.jgj120_2012.stability_rules import check_base_heave_stability, check_confined_water_uplift_stability, check_dewatering_stage_stability, check_layered_seepage_gradient, check_overall_stability_circular_search, check_wall_deformation, check_water_stability, check_weak_underlying_layer
from app.rules.jgj120_2012.support_rules import check_internal_support_layout
from app.schemas.domain import (
    CalculationCase,
    CalculationResult,
    ConstructionStage,
    DesignReviewSummary,
    GlobalCoupledSystemResult,
    FoundationDesign,
    GoverningValues,
    Project,
    StageCalculationResult,
    WallDesignResult,
    WallInternalForcePoint,
    WallInternalForceResult,
    WaleBeamDesignResult,
    ReinforcementGroup,
    SupportLayoutRepairSummary,
)
from app.services.reinforcement_service import diaphragm_wall_reinforcement, support_reinforcement
from app.quality.support_layout_quality import evaluate_support_layout_quality
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility
from app.quality.formal_gate import build_formal_report_gate
from app.services.support_layout_repair import auto_repair_support_layout
from app.services.support_layout import repair_concave_return_supports, repair_wale_support_bays
from app.services.calculation_diagnostics import build_calculation_diagnostics
from app.services.calculation_assurance import audit_calculation_inputs, build_calculation_contract, apply_calculation_assurance
from app.services.wall_restraint import build_effective_wall_restraints
from app.services.candidate_result_cache import candidate_input_hash, get_cached_candidate_result, put_cached_candidate_result
from app.services.wall_embedment_design import auto_design_wall_embedment
from app.services.support_deep_design import evaluate_support_deep_design

LOAD_FACTOR_RETAINING = 1.25


_CHECK_KEY_ALIASES = {
    "rule_id": "ruleId",
    "object_id": "objectId",
    "object_type": "objectType",
    "calculated_value": "calculatedValue",
    "limit_value": "limitValue",
    "clause_reference": "clauseReference",
    "standard_name": "standardName",
    "standard_version": "standardVersion",
    "review_required": "reviewRequired",
}


def _normalize_check_dict(check: dict[str, Any]) -> dict[str, Any]:
    data = dict(check)
    for snake, camel in _CHECK_KEY_ALIASES.items():
        if snake in data and camel not in data:
            data[camel] = data.pop(snake)
    return data


def _check_to_dict(check) -> dict[str, Any]:
    if isinstance(check, tuple):
        check = check[0]
    if hasattr(check, "model_dump"):
        return _normalize_check_dict(check.model_dump(mode="json"))
    return _normalize_check_dict(dict(check))


def _support_length(support) -> float:
    return ((support.end.x - support.start.x) ** 2 + (support.end.y - support.start.y) ** 2) ** 0.5


FOUNDATION_FA_DEFAULT_KPA = 220.0
FOUNDATION_THICKNESS_M = 1.2
FOUNDATION_CONCRETE_UNIT_WEIGHT_KN_M3 = 25.0
FOUNDATION_ECCENTRICITY_FACTOR = 1.05
FOUNDATION_MIN_SIDE_M = 3.0
FOUNDATION_MAX_SIDE_M = 8.0
FOUNDATION_SIDE_INCREMENT_M = 0.25


def _ceil_to_increment(value: float, increment: float) -> float:
    if increment <= 0:
        return value
    import math as _math
    return _math.ceil(value / increment - 1e-9) * increment


def design_column_foundation(
    object_code: str,
    vertical_force_kN: float,
    fa_kpa: float = FOUNDATION_FA_DEFAULT_KPA,
    thickness_m: float = FOUNDATION_THICKNESS_M,
    concrete_unit_weight_kN_m3: float = FOUNDATION_CONCRETE_UNIT_WEIGHT_KN_M3,
    eccentricity_factor: float = FOUNDATION_ECCENTRICITY_FACTOR,
    min_side_m: float = FOUNDATION_MIN_SIDE_M,
    max_side_m: float = FOUNDATION_MAX_SIDE_M,
    increment_m: float = FOUNDATION_SIDE_INCREMENT_M,
) -> FoundationDesign:
    """Design a preliminary square footing for temporary support columns.

    The previous V1.1 workflow used a fixed 3.0 m x 3.0 m footing.  That could
    produce an avoidable GB 50007 bearing-pressure fail in the sample project.
    This helper increases the footing side length until both average pressure
    and the eccentricity-amplified pressure subset satisfy the available fa.
    It does not remove the GB 50007 check; it only produces a more coherent
    preliminary footing size for that check.
    """
    unit_weight_pressure = thickness_m * concrete_unit_weight_kN_m3
    allowable_average = min(fa_kpa, 1.2 * fa_kpa / max(eccentricity_factor, 1e-9))
    denominator = allowable_average - unit_weight_pressure
    if vertical_force_kN <= 0 or fa_kpa <= 0 or denominator <= 0:
        side = min_side_m
        area = side * side
        self_weight = area * unit_weight_pressure
        avg_pressure = (vertical_force_kN + self_weight) / area if area > 0 else 0.0
        max_pressure = avg_pressure * eccentricity_factor
        return FoundationDesign(
            code=f"FDN-{object_code}",
            foundation_type="manual_review",
            width=round(side, 3),
            length=round(side, 3),
            thickness=thickness_m,
            area=round(area, 3),
            concrete_unit_weight=concrete_unit_weight_kN_m3,
            foundation_self_weight=round(self_weight, 3),
            vertical_force=round(vertical_force_kN, 3),
            fa=fa_kpa,
            eccentricity_factor=eccentricity_factor,
            average_pressure=round(avg_pressure, 3),
            max_pressure=round(max_pressure, 3),
            check_status="manual_review",
            design_note="缺少有效竖向荷载或承载力参数，不能自动完成立柱基础初选。",
        )

    required_area = max(min_side_m * min_side_m, vertical_force_kN / denominator)
    side = _ceil_to_increment(required_area ** 0.5, increment_m)
    side = max(min_side_m, side)
    if side > max_side_m:
        side = max_side_m
    area = side * side
    self_weight = area * unit_weight_pressure
    avg_pressure = (vertical_force_kN + self_weight) / area
    max_pressure = avg_pressure * eccentricity_factor
    status = "pass" if avg_pressure <= fa_kpa and max_pressure <= 1.2 * fa_kpa else "fail"
    note = (
        "按 GB 50007 承载力子集自动扩大临时立柱基础尺寸；正式工程仍需复核偏心、沉降、软弱下卧层、抗浮和施工构造。"
        if status == "pass"
        else "达到当前最大自动扩基尺寸后仍不能满足承载力子集，需改用更大基础、立柱桩或人工专项设计。"
    )
    return FoundationDesign(
        code=f"FDN-{object_code}",
        foundation_type="temporary_spread_footing" if status == "pass" else "manual_review",
        width=round(side, 3),
        length=round(side, 3),
        thickness=thickness_m,
        area=round(area, 3),
        concrete_unit_weight=concrete_unit_weight_kN_m3,
        foundation_self_weight=round(self_weight, 3),
        vertical_force=round(vertical_force_kN, 3),
        fa=fa_kpa,
        eccentricity_factor=eccentricity_factor,
        average_pressure=round(avg_pressure, 3),
        max_pressure=round(max_pressure, 3),
        check_status=status,
        design_note=note,
    )



PILE_DEFAULT_DIAMETER_M = 0.8
PILE_DEFAULT_LENGTH_M = 18.0
PILE_SIDE_RESISTANCE_KPA = 55.0
PILE_END_RESISTANCE_KPA = 1800.0
PILE_CAP_SAFETY_FACTOR = 1.65


def design_column_pile(
    object_code: str,
    vertical_force_kN: float,
    excavation_bottom_elevation: float = -12.0,
    diameter_m: float = PILE_DEFAULT_DIAMETER_M,
    min_length_m: float = 10.0,
    max_length_m: float = 30.0,
    increment_m: float = 1.0,
) -> FoundationDesign:
    """Design a preliminary bored pile for a temporary support column.

    This is a screening model.  It sizes one bored pile by side friction + end
    resistance with a global safety factor.  The output remains traceable and is
    exported through the same FoundationDesign object used by earlier footing
    checks, but its foundation_type is column_pile.
    """
    import math as _math

    pile_area = _math.pi * diameter_m * diameter_m / 4.0
    perimeter = _math.pi * diameter_m
    length = min_length_m
    capacity = 0.0
    while length <= max_length_m + 1e-9:
        side_capacity = perimeter * length * PILE_SIDE_RESISTANCE_KPA
        end_capacity = pile_area * PILE_END_RESISTANCE_KPA
        capacity = (side_capacity + end_capacity) / PILE_CAP_SAFETY_FACTOR
        if capacity >= vertical_force_kN:
            break
        length += increment_m
    status = "pass" if capacity >= vertical_force_kN and vertical_force_kN > 0 else "fail"
    utilization = vertical_force_kN / capacity if capacity > 0 else 999.0
    cap_width = max(1.6, diameter_m + 0.8)
    cap_length = cap_width
    cap_thickness = 1.2
    cap_area = cap_width * cap_length
    cap_self_weight = cap_area * cap_thickness * FOUNDATION_CONCRETE_UNIT_WEIGHT_KN_M3
    return FoundationDesign(
        code=f"PFDN-{object_code}",
        foundation_type="column_pile" if status == "pass" else "manual_review",
        width=round(cap_width, 3),
        length=round(cap_length, 3),
        thickness=cap_thickness,
        area=round(cap_area, 3),
        concrete_unit_weight=FOUNDATION_CONCRETE_UNIT_WEIGHT_KN_M3,
        foundation_self_weight=round(cap_self_weight, 3),
        vertical_force=round(vertical_force_kN, 3),
        fa=FOUNDATION_FA_DEFAULT_KPA,
        eccentricity_factor=1.0,
        average_pressure=round((vertical_force_kN + cap_self_weight) / cap_area, 3),
        max_pressure=round((vertical_force_kN + cap_self_weight) / cap_area, 3),
        pile_diameter=round(diameter_m, 3),
        pile_length=round(length, 3),
        pile_count=1,
        pile_capacity=round(capacity, 3),
        pile_utilization=round(utilization, 3),
        pile_tip_elevation=round(excavation_bottom_elevation - length, 3),
        check_status=status,
        design_note=(
            "临时立柱基础采用单桩承载力子集初选：R = (u*l*qs + Ap*qp)/gamma。正式工程需复核桩身强度、沉降、负摩阻、抗拔、格构柱插入和施工偏位。"
            if status == "pass"
            else "达到最大自动桩长后仍不满足承载力子集，需增大桩径、增加桩数或专项设计。"
        ),
    )


def check_column_pile_capacity(column_id: str, foundation: FoundationDesign) -> dict[str, Any]:
    status = "pass" if foundation.pile_capacity and foundation.pile_capacity >= foundation.vertical_force else "fail"
    return {
        "ruleId": "GB50007-2011-COLUMN-PILE-CAPACITY-SUBSET",
        "objectId": column_id,
        "objectType": "ColumnElement",
        "status": status,
        "calculatedValue": foundation.vertical_force,
        "limitValue": foundation.pile_capacity,
        "unit": "kN",
        "message": "临时立柱桩竖向承载力子集筛查；正式工程需补充桩身强度、沉降、负摩阻、格构柱插入和施工偏位复核。",
        "clauseReference": "GB 50007 pile vertical bearing capacity subset; final clause applicability to verify",
        "formula": "N <= (u*l*qs + Ap*qp)/gamma",
        "foundationCode": foundation.code,
        "foundationType": foundation.foundation_type,
        "pileDiameter": foundation.pile_diameter,
        "pileLength": foundation.pile_length,
        "pileCapacity": foundation.pile_capacity,
        "pileUtilization": foundation.pile_utilization,
        "pileTipElevation": foundation.pile_tip_elevation,
        "designNote": foundation.design_note,
    }

def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for c in checks if c.get("status") == "pass"),
        "fail": sum(1 for c in checks if c.get("status") == "fail"),
        "warning": sum(1 for c in checks if c.get("status") == "warning"),
        "manualReview": sum(1 for c in checks if c.get("status") == "manual_review"),
    }


_ADVISORY_GROUP_RULES = {
    "JGJ120-SUPPORT-CONSTRUCTION-EFFECTS-SUBSET",
    "JGJ120-SUPPORT-LIFECYCLE-PATH-SUBSET",
    "GB50010-WALE-FLEXURE-SUBSET",
    "GB50010-WALE-SHEAR-SUBSET",
    "GB50010-WALE-NODE-REBAR-COORDINATION-SUBSET",
    "WALE-DEFLECTION-ENVELOPE-SUBSET",
    "JGJ120-2012-4.7-INTERNAL-SUPPORT-LAYOUT-SCREEN-SPAN",
    "QUALITY-LONG_DIRECT_STRUT",
}


def _check_governing_score(check: dict[str, Any]) -> tuple[int, float]:
    """Return a stable severity/governing score for duplicate check records."""
    status_rank = {"pass": 0, "manual_review": 1, "warning": 2, "fail": 3}
    status = str(check.get("status") or "manual_review")
    calculated = check.get("calculatedValue")
    limit = check.get("limitValue")
    numeric_score = 0.0
    if isinstance(calculated, (int, float)):
        numeric_score = abs(float(calculated))
        if isinstance(limit, (int, float)) and abs(float(limit)) > 1.0e-9:
            ratio = float(calculated) / float(limit)
            rule_id = str(check.get("ruleId") or "").upper()
            # Stability/safety factors are worse when the ratio is smaller;
            # demand/capacity and deformation checks are worse when larger.
            if any(token in rule_id for token in ("STABILITY", "EMBEDMENT", "HEAVE", "SEEPAGE", "UPLIFT")):
                numeric_score = -ratio
            else:
                numeric_score = abs(ratio)
    return status_rank.get(status, 1), numeric_score


def _consolidate_global_checks(project: Project, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated stage checks while retaining full trace metadata.

    Stage results keep their detailed check arrays.  The project-level list is
    a governing issue register: one record per rule/object plus a small number
    of system-level advisory groups.  This prevents the dashboard from showing
    the same wall detailing warning seven times or 45 identical support life-
    cycle notices as separate engineering problems.
    """
    normalized = [_normalize_check_dict(item) for item in checks]
    per_object: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in normalized:
        key = (
            str(item.get("ruleId") or "UNKNOWN"),
            str(item.get("objectId") or project.id),
            str(item.get("objectType") or "EngineeringObject"),
        )
        per_object.setdefault(key, []).append(item)
    governing: list[dict[str, Any]] = []
    for records in per_object.values():
        selected = max(records, key=_check_governing_score)
        merged = dict(selected)
        stage_ids = sorted({str(item.get("stageId")) for item in records if item.get("stageId")})
        stage_names = sorted({str(item.get("stageName")) for item in records if item.get("stageName")})
        if len(records) > 1:
            merged["occurrenceCount"] = len(records)
        if stage_ids:
            merged["stageIds"] = stage_ids
            merged["governingStageId"] = selected.get("stageId")
        if stage_names:
            merged["stageNames"] = stage_names
        governing.append(merged)

    grouped_advisories: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for item in governing:
        rule_id = str(item.get("ruleId") or "")
        status = str(item.get("status") or "manual_review")
        if rule_id in _ADVISORY_GROUP_RULES and status != "fail":
            grouped_advisories.setdefault((rule_id, status, str(item.get("objectType") or "EngineeringObject")), []).append(item)
        else:
            passthrough.append(item)
    for (rule_id, status, object_type), records in grouped_advisories.items():
        selected = max(records, key=_check_governing_score)
        merged = dict(selected)
        object_ids = sorted({str(item.get("objectId")) for item in records if item.get("objectId")})
        merged.update({
            "objectId": project.id,
            "objectType": f"{object_type}Group",
            "affectedObjectIds": object_ids,
            "affectedObjectCount": len(object_ids),
            "occurrenceCount": sum(int(item.get("occurrenceCount") or 1) for item in records),
            "message": f"{len(object_ids)} 个对象采用同一类工程假定/复核要求。{selected.get('message') or ''}",
        })
        passthrough.append(merged)
    passthrough.sort(key=lambda item: (-_check_governing_score(item)[0], str(item.get("ruleId")), str(item.get("objectId"))))
    return passthrough


def _governing_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(c.get("status")) for c in checks}
    if "fail" in statuses:
        return "fail"
    if "warning" in statuses:
        return "warning"
    if "manual_review" in statuses:
        return "manual_review"
    return "pass" if checks else "manual_review"


def _min_value(checks: list[dict[str, Any]], token: str) -> float | None:
    values: list[float] = []
    for check in checks:
        if token in str(check.get("ruleId", check.get("rule_id", ""))):
            value = check.get("calculatedValue")
            if isinstance(value, (int, float)) and value < 900:
                values.append(float(value))
    return round(min(values), 3) if values else None


def _max_value(checks: list[dict[str, Any]], token: str) -> float | None:
    values: list[float] = []
    for check in checks:
        if token in str(check.get("ruleId", check.get("rule_id", ""))):
            value = check.get("calculatedValue")
            if isinstance(value, (int, float)) and value < 900:
                values.append(float(value))
    return round(max(values), 3) if values else None




def _status_from_counts(fail: int, warning: int, manual: int = 0) -> str:
    if fail:
        return "fail"
    if warning:
        return "warning"
    if manual:
        return "manual_review"
    return "pass"


def _design_review_summary(checks: list[dict[str, Any]], stage_results: list[StageCalculationResult]) -> DesignReviewSummary:
    strength_tokens = ("FLEXURE", "SHEAR", "AXIAL", "BEARING", "PILE", "CAPACITY", "CRACK", "REBAR", "WALE")
    stiffness_tokens = ("DEFORMATION", "DEFLECTION", "STIFFNESS", "DISPLACEMENT")
    stability_tokens = ("STABILITY", "EMBEDMENT", "HEAVE", "SEEPAGE", "UPLIFT", "OVERALL", "WATER")
    strength_fail = strength_warning = strength_manual = 0
    stiffness_fail = stiffness_warning = stiffness_manual = 0
    stability_fail = stability_warning = stability_manual = 0
    max_strength_util = 0.0
    max_stiff_util = 0.0
    min_stab = None
    for c in checks:
        rid = str(c.get("ruleId", "")).upper()
        status = str(c.get("status", ""))
        calc = c.get("calculatedValue")
        limit = c.get("limitValue")
        util = None
        if isinstance(calc, (int, float)) and isinstance(limit, (int, float)) and abs(limit) > 1e-9:
            # For safety factors calc/limit is inverted below when applicable.
            util = abs(float(calc) / float(limit))
        if any(tok in rid for tok in strength_tokens):
            strength_fail += status == "fail"
            strength_warning += status == "warning"
            strength_manual += status == "manual_review"
            if util is not None:
                max_strength_util = max(max_strength_util, util)
        if any(tok in rid for tok in stiffness_tokens):
            stiffness_fail += status == "fail"
            stiffness_warning += status == "warning"
            stiffness_manual += status == "manual_review"
            if util is not None:
                max_stiff_util = max(max_stiff_util, util)
        if any(tok in rid for tok in stability_tokens):
            stability_fail += status == "fail"
            stability_warning += status == "warning"
            stability_manual += status == "manual_review"
            if isinstance(calc, (int, float)) and ("SAFETY" in rid or "STABILITY" in rid or "UPLIFT" in rid or "HEAVE" in rid or "SEEPAGE" in rid):
                min_stab = float(calc) if min_stab is None else min(min_stab, float(calc))
    global_max_disp = max((sr.global_coupled_result.max_wall_displacement for sr in stage_results if sr.global_coupled_result), default=0.0)
    if global_max_disp > 0.08:
        stiffness_warning += 1
    return DesignReviewSummary(
        strength_status=_status_from_counts(strength_fail, strength_warning, strength_manual),
        stiffness_status=_status_from_counts(stiffness_fail, stiffness_warning, stiffness_manual),
        stability_status=_status_from_counts(stability_fail, stability_warning, stability_manual),
        strength_fail_count=int(strength_fail),
        stiffness_fail_count=int(stiffness_fail),
        stability_fail_count=int(stability_fail),
        strength_warning_count=int(strength_warning),
        stiffness_warning_count=int(stiffness_warning),
        stability_warning_count=int(stability_warning),
        max_strength_utilization=round(max_strength_util, 3) if max_strength_util else None,
        max_stiffness_utilization=round(max_stiff_util, 3) if max_stiff_util else None,
        min_stability_safety_factor=round(min_stab, 3) if min_stab is not None else None,
        notes=[
            "强度复核汇总覆盖墙体、围檩、支撑、节点承压、立柱桩/基础和钢筋子集检查。",
            "刚度复核汇总覆盖墙体位移、围檩挠度和全局联立矩阵位移输出。",
            "稳定性复核汇总覆盖嵌固、抗隆起、抗渗/承压水、整体稳定等专项筛查。",
        ],
    )


def _wall_force_model(segment_id: str, stage_id: str, wall_force: dict[str, Any], gamma0: float) -> WallInternalForceResult:
    raw_points = wall_force.get("points", [])
    stride = max(1, len(raw_points) // 80) if raw_points else 1
    points: list[WallInternalForcePoint] = []
    for p in raw_points[::stride]:
        points.append(
            WallInternalForcePoint(
                depth=float(p.get("depth", 0.0)),
                elevation=float(p.get("elevation", -float(p.get("depth", 0.0)))),
                shear=float(p.get("shear", 0.0)),
                moment=float(p.get("moment", 0.0)),
                displacement=p.get("displacementMm"),
            )
        )
    max_m = abs(float(wall_force.get("maxMoment") or 0.0))
    max_v = abs(float(wall_force.get("maxShear") or 0.0))
    max_d = wall_force.get("maxDisplacement")
    return WallInternalForceResult(
        segment_id=segment_id,
        stage_id=stage_id,
        points=points,
        max_moment=round(max_m, 3),
        max_shear=round(max_v, 3),
        max_displacement=round(abs(float(max_d)), 3) if max_d is not None else None,
        max_moment_design=round(max_m * gamma0 * LOAD_FACTOR_RETAINING, 3),
        max_shear_design=round(max_v * gamma0 * LOAD_FACTOR_RETAINING, 3),
        importance_factor=gamma0,
        load_combination_factor=LOAD_FACTOR_RETAINING,
        method=str(wall_force.get("method") or "JGJ120 pressure + finite-difference beam-on-elastic-foundation"),
        warnings=[str(x) for x in wall_force.get("warnings", [])],
    )




def _support_construction_effects(support, standard_force: float, safety_grade: str, *, preload_override: float | None = None) -> dict[str, Any]:
    """Preliminary construction-effect model for internal supports.

    The model records the main design effects that are normally considered by
    engineers: preload, temperature, joint-gap closure and construction
    eccentricity/deviation.  It is intentionally traceable and conservative, not
    a substitute for project-specific construction monitoring and preloading
    protocols.
    """
    standard = max(float(standard_force or 0.0), 0.0)
    length = _support_length(support)
    area = support_axial_area(support)
    e = support_elastic_modulus(support)
    alpha = 1.0e-5
    temp_delta = support.temperature_delta_c if support.temperature_delta_c is not None else (12.0 if support.section_type == "steel_pipe" else 8.0)
    preload_ratio = support.preload_ratio if support.preload_ratio is not None else (0.30 if support.section_type == "steel_pipe" else 0.10)
    # Preload is recomputed from the current raw standard-force envelope. Reusing
    # a stored preload from an earlier topology/calculation can recursively inflate
    # the next design envelope. A fixed protocol preload may be passed explicitly
    # when evaluating each stage.
    preload = float(preload_override) if preload_override is not None else standard * preload_ratio
    # A small fraction of elastic thermal restraint is used because temporary
    # support nodes are not perfectly fixed in real construction.
    thermal = max(0.0, e * area * alpha * abs(temp_delta) * 0.15)
    gap = standard * (0.06 if support.section_type == "steel_pipe" else 0.03)
    deviation_mm = support.construction_deviation_mm if support.construction_deviation_mm is not None else min(30.0, max(10.0, length * 2.0))
    eccentricity = standard * deviation_mm / 1000.0
    effective_standard = standard + 0.50 * preload + thermal + gap
    design = design_effect_standard_to_uls(effective_standard, safety_grade=safety_grade, combined_partial_factor=1.25)
    return {
        "standard": round(standard, 3),
        "preload": round(preload, 3),
        "preloadRatio": round(preload_ratio, 3),
        "thermal": round(thermal, 3),
        "gap": round(gap, 3),
        "deviationMm": round(deviation_mm, 3),
        "eccentricityMoment": round(eccentricity, 3),
        "effectiveStandard": round(effective_standard, 3),
        "design": round(design, 3),
        "note": "轴力包络已叠加预加轴力折减项、温度约束效应、节点间隙闭合和施工偏心；参数为施工阶段快速筛查默认值，正式工程需按专项施工方案复核。",
    }


def _add_wale_reinforcement_groups(beam, design: WaleBeamDesignResult) -> list[ReinforcementGroup]:
    groups = [
        ReinforcementGroup(
            name="围檩上/下缘主筋",
            bar_type="longitudinal",
            diameter=design.main_bar_diameter or 25,
            spacing=design.main_bar_spacing or 150,
            grade="HRB400",
            location_description=f"{beam.code} 沿梁长连续配置；节点区与支撑端部附加筋协调",
            area_per_meter=design.provided_reinforcement_area,
            required_area_per_meter=design.required_reinforcement_area,
            check_status=design.check_status,
        ),
        ReinforcementGroup(
            name="围檩箍筋",
            bar_type="stirrup",
            diameter=design.stirrup_diameter or 12,
            spacing=design.stirrup_spacing or 150,
            grade="HRB400",
            location_description=f"{beam.code} 支撑节点两侧 1.5h 范围加密，普通区按计算和构造取值",
            check_status=design.check_status,
        ),
        ReinforcementGroup(
            name="节点区附加抗裂筋",
            bar_type="additional",
            diameter=20,
            spacing=150,
            grade="HRB400",
            location_description=design.node_additional_reinforcement_note or "支撑端部、围檩腹板和主筋锚固区附加配置；与支撑节点承压板协调。",
            check_status="manual_review" if design.check_status == "manual_review" else design.check_status,
        ),
    ]
    return groups


def _design_wale_beams(project: Project, wale_results: list, gamma0: float) -> list[dict[str, Any]]:
    if not project.retaining_system:
        return []
    checks: list[dict[str, Any]] = []
    grouped: dict[str, list] = {}
    for result in wale_results:
        grouped.setdefault(result.wale_beam_code, []).append(result)
    beams = list(project.retaining_system.wale_beams) + list(getattr(project.retaining_system, "ring_beams", []) or [])
    for beam in beams:
        results = grouped.get(beam.code, [])
        if not results:
            continue
        envelope = build_wale_beam_envelope(beam.code, results)
        max_m = max(abs(r.max_moment) for r in results)
        max_v = max(abs(r.max_shear) for r in results)
        max_d = max(abs(r.max_deflection) for r in results)
        if envelope:
            max_m = max(max_m, abs(envelope.max_positive_moment), abs(envelope.max_negative_moment))
            max_v = max(max_v, envelope.max_abs_shear)
            max_d = max(max_d, envelope.max_abs_deflection)
        m_design = max_m * gamma0 * LOAD_FACTOR_RETAINING
        v_design = max_v * gamma0 * LOAD_FACTOR_RETAINING
        initial_width = max(float(beam.section.width or 0.9), 0.2)
        initial_height = max(float(beam.section.height or 0.8), 0.2)
        width = initial_width
        height = initial_height
        flex = design_rectangular_flexural_reinforcement(m_design / width, height, beam.material.grade, "HRB400")
        shear = check_rectangular_shear_capacity(v_design / width, height, beam.material.grade)
        # Auto-size the wale section instead of reporting avoidable fails.  The
        # search uses practical large RC wale dimensions and updates the BIM
        # section when a passing subset design is found.
        candidate_widths = [initial_width, 1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 3.0]
        candidate_heights = [initial_height, 0.9, 1.2, 1.5, 1.8, 2.0, 2.2, 2.4]
        found = False
        optimization_history: list[dict[str, Any]] = []
        for cand_h in candidate_heights:
            for cand_w in candidate_widths:
                cand_w = max(cand_w, initial_width)
                cand_h = max(cand_h, initial_height)
                cand_flex = design_rectangular_flexural_reinforcement(m_design / cand_w, cand_h, beam.material.grade, "HRB400")
                cand_shear = check_rectangular_shear_capacity(v_design / cand_w, cand_h, beam.material.grade)
                optimization_history.append({
                    "width": round(cand_w, 3),
                    "height": round(cand_h, 3),
                    "flexureStatus": cand_flex["status"],
                    "shearStatus": cand_shear["status"],
                    "asRequired": round(cand_flex["asRequired"], 2),
                    "asProvided": round(cand_flex["barArrangement"]["providedAs"], 2),
                    "shearUtilization": round(cand_shear.get("utilization", 0.0), 3),
                })
                if cand_flex["status"] == "pass" and cand_shear["status"] == "pass":
                    width, height, flex, shear = cand_w, cand_h, cand_flex, cand_shear
                    found = True
                    break
            if found:
                break
        if found and (abs(width - initial_width) > 1e-9 or abs(height - initial_height) > 1e-9):
            beam.section.width = round(width, 3)
            beam.section.height = round(height, 3)
            beam.section.name = f"{int(round(width * 1000))}x{int(round(height * 1000))} RC wale beam"
        provided = flex["barArrangement"]["providedAs"]
        required = flex["asRequired"]
        capacity = rectangular_flexural_capacity_knm_per_m(provided, height, beam.material.grade, "HRB400") * width
        shear_capacity = shear.get("concreteShearCapacity", 0.0) * width
        status = "pass" if flex["status"] == "pass" and shear["status"] == "pass" else "fail"
        stirrup_spacing = 100 if shear.get("utilization", 0.0) > 0.75 else 150
        face_code = results[0].face_code
        level_index = results[0].level_index
        deflection_limit = max(float(getattr(results[0], "beam_length", 0.0) or 1.0) / 400.0, 0.01)
        deflection_ratio = max_d / deflection_limit if deflection_limit > 0 else 999.0
        deflection_status = "pass" if deflection_ratio <= 1.0 else "warning" if deflection_ratio <= 1.5 else "fail"
        local_bearing_spread_width = round(max(width, 2.0 * (getattr(results[0], "support_node_count", 1) ** 0.5) * 0.45), 3)
        local_bearing_spread_height = round(max(height, 1.5 * height), 3)
        design = WaleBeamDesignResult(
            wale_beam_code=beam.code,
            face_code=face_code,
            level_index=level_index,
            max_moment=round(max_m, 3),
            max_shear=round(max_v, 3),
            max_deflection=round(max_d, 6),
            max_moment_design=round(m_design, 3),
            max_shear_design=round(v_design, 3),
            required_reinforcement_area=round(required, 2),
            provided_reinforcement_area=round(provided, 2),
            moment_capacity=round(capacity, 3),
            shear_capacity=round(shear_capacity, 3),
            main_bar_diameter=flex["barArrangement"].get("diameter"),
            main_bar_spacing=flex["barArrangement"].get("spacing"),
            stirrup_diameter=12,
            stirrup_spacing=stirrup_spacing,
            node_additional_reinforcement_note="围檩主筋在支撑节点两侧连续通过；承压板后方设置附加竖筋、U 形筋和加密箍筋，附加筋面积不小于主筋计算控制面积的 20%。",
            deflection_limit=round(deflection_limit, 6),
            deflection_ratio=round(deflection_ratio, 3),
            deflection_check_status=deflection_status,
            optimized_width=round(width, 3),
            optimized_height=round(height, 3),
            optimization_history=optimization_history[-20:],
            local_bearing_spread_width=local_bearing_spread_width,
            local_bearing_spread_height=local_bearing_spread_height,
            wall_connection_note="围檩与地连墙按连续传力构造处理：节点后方承压扩散区、预埋件/植筋/穿墙筋和墙面局部压应力需由施工图详设复核。",
            envelope=envelope,
            check_status="fail" if status == "fail" or deflection_status == "fail" else "warning" if deflection_status == "warning" else "pass",
            notes=[
                "围檩内力来自 V1.9 全局联立刚度矩阵与多工况围檩包络的弯矩、剪力和挠度结果。",
                "正截面配筋、斜截面抗剪和节点区附加筋已形成子集设计；正式工程需复核构造锚固、裂缝、施工缝和局部承压扩散。",
            ],
        )
        beam.internal_force_results = results
        beam.design_result = design
        beam.design_moment = design.max_moment_design
        beam.design_shear = design.max_shear_design
        beam.reinforcement = _add_wale_reinforcement_groups(beam, design)
        checks.extend([
            {
                "ruleId": "GB50010-WALE-FLEXURE-SUBSET",
                "objectId": beam.id,
                "objectType": "BeamElement",
                "status": status,
                "calculatedValue": design.required_reinforcement_area,
                "limitValue": design.provided_reinforcement_area,
                "unit": "mm2/m",
                "message": f"围檩正截面受弯配筋子集：Md={design.max_moment_design} kN*m，建议 {flex['barArrangement']['description']}。",
                "clauseReference": "GB 50010 rectangular flexure subset for RC wale beam; final clause applicability to verify",
                "formula": "M <= alpha1*fc*b*x*(h0-x/2); alpha1*fc*b*x = fy*As; total beam M converted by beam width",
            },
            {
                "ruleId": "GB50010-WALE-SHEAR-SUBSET",
                "objectId": beam.id,
                "objectType": "BeamElement",
                "status": "pass" if shear["status"] == "pass" else "fail",
                "calculatedValue": round(v_design, 3),
                "limitValue": round(shear_capacity, 3),
                "unit": "kN",
                "message": f"围檩斜截面抗剪子集：建议 D12@{stirrup_spacing} 箍筋，节点两侧加密。",
                "clauseReference": "GB 50010 shear subset for RC wale beam; stirrup detailing to verify",
                "formula": "V <= 0.7*ft*b*h0 plus stirrup contribution in detailed design",
            },
            {
                "ruleId": "WALE-DEFLECTION-ENVELOPE-SUBSET",
                "objectId": beam.id,
                "objectType": "BeamElement",
                "status": deflection_status,
                "calculatedValue": round(max_d, 6),
                "limitValue": round(deflection_limit, 6),
                "unit": "m",
                "message": "围檩多工况挠度包络筛查；限值采用 L/400 快速控制，正式工程应按构件角色和施工阶段控制指标复核。",
                "clauseReference": "engineering serviceability screening; project-specific limit to verify",
                "formula": "delta_max <= L/400",
            },
            {
                "ruleId": "GB50010-WALE-NODE-REBAR-COORDINATION-SUBSET",
                "objectId": beam.id,
                "objectType": "BeamElement",
                "status": status,
                "calculatedValue": design.max_moment_design,
                "limitValue": design.moment_capacity,
                "unit": "kN*m",
                "message": "围檩节点区附加筋与围檩主筋协调：主筋连续通过，承压板后方设置附加竖筋、U 形筋和加密箍筋。",
                "clauseReference": "GB 50010 anchorage/detailing coordination subset; project detailing to verify",
                "formula": "node additional reinforcement >= 20% of controlling main reinforcement area, screening rule",
            },
        ])
    return checks

def _support_topology_hash(project: Project) -> str:
    supports = project.retaining_system.supports if project.retaining_system else []
    payload = [
        {
            "id": support.id,
            "code": support.code,
            "level": int(support.level_index),
            "elevation": round(float(support.elevation), 4),
            "startFace": support.start_face_code,
            "endFace": support.end_face_code,
            "start": [round(float(support.start.x), 4), round(float(support.start.y), 4)],
            "end": [round(float(support.end.x), 4), round(float(support.end.y), 4)],
        }
        for support in sorted(supports, key=lambda item: (int(item.level_index), item.code, item.id))
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _case_support_audit(project: Project, case: CalculationCase) -> dict[str, Any]:
    supports = project.retaining_system.supports if project.retaining_system else []
    valid_ids = {support.id for support in supports}
    referenced_ids = {support_id for stage in case.stages for support_id in [*stage.active_support_ids, *stage.deactivated_support_ids]}
    stale_ids = sorted(referenced_ids - valid_ids)
    current_hash = _support_topology_hash(project)
    stage_count_with_no_valid_support = 0
    for stage in case.stages:
        if stage.active_support_ids and not any(support_id in valid_ids for support_id in stage.active_support_ids):
            stage_count_with_no_valid_support += 1
    return {
        "currentTopologyHash": current_hash,
        "caseTopologyHash": case.support_topology_hash,
        "hashMatches": bool(case.support_topology_hash and case.support_topology_hash == current_hash),
        "referencedSupportCount": len(referenced_ids),
        "validReferencedSupportCount": len(referenced_ids & valid_ids),
        "staleSupportCount": len(stale_ids),
        "staleSupportIds": stale_ids[:30],
        "stageCountWithNoValidSupport": stage_count_with_no_valid_support,
        "requiresSynchronization": bool(stale_ids or stage_count_with_no_valid_support or (case.support_topology_hash and case.support_topology_hash != current_hash)),
    }


def _copy_stage_operational_settings(source: ConstructionStage, target: ConstructionStage) -> None:
    target.name = source.name or target.name
    target.groundwater_level_inside = source.groundwater_level_inside
    target.groundwater_level_outside = source.groundwater_level_outside
    target.surcharge = source.surcharge
    target.zone = source.zone or target.zone
    target.replacement_action = source.replacement_action or target.replacement_action


def _stage_match_cost(source: ConstructionStage, target: ConstructionStage) -> tuple[int, float, int]:
    """Semantic stage matching cost used when topology regeneration changes stage count."""
    type_penalty = 0 if source.stage_type == target.stage_type else 1
    elevation_delta = abs(float(source.excavation_elevation) - float(target.excavation_elevation))
    zone_penalty = 0 if (source.zone or "") == (target.zone or "") else 1
    return type_penalty, elevation_delta, zone_penalty


def _copy_operational_settings_semantically(source_stages: list[ConstructionStage], target_stages: list[ConstructionStage]) -> list[dict[str, Any]]:
    unused = set(range(len(source_stages)))
    mapping: list[dict[str, Any]] = []
    for target_index, target in enumerate(target_stages):
        if not unused:
            break
        source_index = min(unused, key=lambda index: _stage_match_cost(source_stages[index], target))
        source = source_stages[source_index]
        cost = _stage_match_cost(source, target)
        # Do not copy an unrelated construction operation merely because it has
        # the same ordinal position.  Type-compatible stages are preferred; a
        # cross-type copy is allowed only for generic excavation/final aliases.
        compatible = cost[0] == 0 or {source.stage_type, target.stage_type} <= {"excavation", "final"}
        if compatible:
            _copy_stage_operational_settings(source, target)
            unused.remove(source_index)
            mapping.append({
                "sourceStageId": source.id,
                "sourceStageType": source.stage_type,
                "sourceElevationM": source.excavation_elevation,
                "targetStageId": target.id,
                "targetStageType": target.stage_type,
                "targetElevationM": target.excavation_elevation,
                "elevationDeltaM": round(cost[1], 4),
            })
    return mapping


def synchronize_calculation_case_supports(project: Project, case: CalculationCase | None) -> tuple[CalculationCase, dict[str, Any]]:
    """Synchronize staged support IDs after topology changes using semantic stages.

    Historical candidate/adopted support ids are never allowed to participate in
    a current calculation.  Water level, surcharge and zone settings are copied
    by stage type and nearest excavation elevation, not by list order.
    """
    default_case = build_default_construction_cases(project)[0]
    if case is None:
        after = _case_support_audit(project, default_case)
        return default_case, {
            "synchronized": True,
            "reason": "no_case_supplied",
            "before": None,
            "after": after,
            "afterTopologyHash": default_case.support_topology_hash,
            "operationalSettingMapping": [],
        }
    audit = _case_support_audit(project, case)
    if not audit["requiresSynchronization"]:
        return case, {
            "synchronized": False,
            "reason": "topology_current",
            "before": audit,
            "after": audit,
            "afterTopologyHash": case.support_topology_hash,
            "operationalSettingMapping": [],
        }
    mapping = _copy_operational_settings_semantically(list(case.stages), list(default_case.stages))
    default_case.name = case.name
    default_case.synchronization_note = (
        f"Support topology synchronized automatically: {audit['staleSupportCount']} stale support IDs and "
        f"{audit['stageCountWithNoValidSupport']} stages without valid active supports were replaced; "
        f"{len(mapping)} operational stage settings were mapped semantically."
    )
    after = _case_support_audit(project, default_case)
    if after["requiresSynchronization"]:
        raise ValueError("Construction-stage support synchronization did not produce a current topology")
    return default_case, {
        "synchronized": True,
        "reason": "stale_support_topology",
        "before": audit,
        "after": after,
        "afterTopologyHash": default_case.support_topology_hash,
        "operationalSettingMapping": mapping,
    }

def build_default_construction_cases(project: Project) -> list[CalculationCase]:
    if not project.excavation:
        raise ValueError("Project has no excavation")
    supports = project.retaining_system.supports if project.retaining_system else []
    stages: list[ConstructionStage] = []
    top = project.excavation.top_elevation
    bottom = project.excavation.bottom_elevation
    topology_hash = _support_topology_hash(project)
    if supports:
        level_groups: dict[float, list[Any]] = {}
        for support in sorted(supports, key=lambda s: s.elevation, reverse=True):
            level_groups.setdefault(round(support.elevation, 3), []).append(support)
        active: list[str] = []
        active_levels: list[int] = []
        for idx, (elevation, level_supports) in enumerate(level_groups.items(), start=1):
            support_ids = [support.id for support in level_supports]
            level_index = int(level_supports[0].level_index) if level_supports else idx
            excavation_elev = elevation - 0.5
            active.extend(support_ids)
            active_levels.append(level_index)
            stages.append(
                ConstructionStage(
                    name=f"Stage {idx}: excavate to {excavation_elev:.2f}m and activate support level {idx}",
                    excavation_elevation=max(excavation_elev, bottom),
                    active_support_ids=list(active),
                    active_support_levels=sorted(set(active_levels)),
                    support_topology_hash=topology_hash,
                    stage_type="support_installation",
                    zone=f"Z{idx}",
                    groundwater_level_inside=project.design_settings.groundwater_level,
                    groundwater_level_outside=project.design_settings.groundwater_level,
                    surcharge=project.design_settings.surcharge,
                )
            )
    stages.append(
        ConstructionStage(
            name="Final excavation and service verification",
            excavation_elevation=bottom,
            active_support_ids=[s.id for s in supports],
            active_support_levels=sorted({int(s.level_index) for s in supports}),
            support_topology_hash=topology_hash,
            stage_type="final",
            zone="Z-final",
            groundwater_level_inside=project.design_settings.groundwater_level,
            groundwater_level_outside=project.design_settings.groundwater_level,
            surcharge=project.design_settings.surcharge,
        )
    )
    if supports:
        level_groups_desc = sorted({s.level_index for s in supports}, reverse=True)
        remaining_support_ids = [s.id for s in supports]
        remaining_levels = sorted({int(s.level_index) for s in supports})
        transferred_levels: list[int] = []
        for level in level_groups_desc:
            remove_ids = [s.id for s in supports if s.level_index == level]
            remaining_support_ids = [support_id for support_id in remaining_support_ids if support_id not in set(remove_ids)]
            remaining_levels = [item for item in remaining_levels if item != int(level)]
            transferred_levels.append(int(level))
            stages.append(
                ConstructionStage(
                    name=f"Replacement path: remove support level {level} after basement slab/waler transfer",
                    excavation_elevation=bottom,
                    active_support_ids=list(remaining_support_ids),
                    deactivated_support_ids=remove_ids,
                    active_support_levels=list(remaining_levels),
                    transferred_support_levels=sorted(transferred_levels),
                    support_topology_hash=topology_hash,
                    stage_type="replacement",
                    zone=f"replace-L{level}",
                    replacement_action="bottom-up support removal after slab strength reaches design requirement",
                    groundwater_level_inside=project.design_settings.groundwater_level_inside if project.design_settings.groundwater_level_inside is not None else project.design_settings.groundwater_level,
                    groundwater_level_outside=project.design_settings.groundwater_level,
                    surcharge=project.design_settings.surcharge,
                )
            )
    return [CalculationCase(name="Default staged excavation and replacement path case", stages=stages, support_topology_hash=topology_hash)]


def _candidate_label(index: int) -> str:
    return chr(ord("A") + index)


def _wale_envelope_metrics(result: CalculationResult) -> dict[str, float]:
    max_moment = 0.0
    max_shear = 0.0
    max_deflection = 0.0
    for stage in result.stage_results:
        for wale in stage.wale_beam_results or []:
            max_moment = max(max_moment, abs(float(wale.max_moment or 0.0)))
            max_shear = max(max_shear, abs(float(wale.max_shear or 0.0)))
            max_deflection = max(max_deflection, abs(float(wale.max_deflection or 0.0)))
    return {"maxWaleMoment": round(max_moment, 3), "maxWaleShear": round(max_shear, 3), "maxWaleDeflection": round(max_deflection, 6)}


def _ifc_risk_level(result: CalculationResult) -> str:
    q = result.ifc_compatibility
    if not q:
        return "unknown"
    levels = {"high": 3, "medium": 2, "low": 1}
    best = "low"
    for profile in q.viewer_profiles or []:
        if levels.get(profile.risk_level, 0) > levels.get(best, 0):
            best = profile.risk_level
    if q.status == "fail":
        return "high"
    return best


def _stability_min(result: CalculationResult) -> float | None:
    vals = [
        result.governing_values.embedment_safety_factor_min,
        result.governing_values.heave_safety_factor_min,
    ]
    if result.stability_detailed_result and result.stability_detailed_result.min_safety_factor is not None:
        vals.append(result.stability_detailed_result.min_safety_factor)
    finite = [float(v) for v in vals if v is not None]
    return round(min(finite), 3) if finite else None


def _summarize_candidate_calculation(label: str, candidate, result: CalculationResult, trial_project: Project) -> dict[str, Any]:
    wale = _wale_envelope_metrics(result)
    formal_gate = result.formal_report_gate
    ifc_quality = result.ifc_compatibility
    embedment = dict((result.design_iteration_summary or {}).get("wallEmbedmentPreflight") or {})
    return {
        "schemeLabel": label,
        "candidateId": candidate.id,
        "rank": candidate.rank,
        "score": candidate.score,
        "targetSpacing": candidate.target_spacing,
        "columnMaxSpan": candidate.column_max_span,
        "positionPattern": (candidate.variable_summary or {}).get("positionPattern"),
        "supportCount": len(trial_project.retaining_system.supports) if trial_project.retaining_system else candidate.support_count,
        "columnCount": len(trial_project.retaining_system.columns) if trial_project.retaining_system else candidate.column_count,
        "maxSpanLength": candidate.max_span_length,
        "excessiveDirectStrutCount": int((candidate.metrics or {}).get("excessiveDirectStrutCount", 0) or 0),
        "minSupportWallClearance": (candidate.metrics or {}).get("minSupportWallClearance"),
        "maxSupportAxialForce": result.governing_values.max_support_axial_force,
        "maxDisplacement": result.governing_values.max_displacement,
        "maxWallMoment": result.governing_values.max_wall_moment,
        "maxWallShear": result.governing_values.max_wall_shear,
        "maxWaleMoment": wale["maxWaleMoment"],
        "maxWaleShear": wale["maxWaleShear"],
        "maxWaleDeflection": wale["maxWaleDeflection"],
        "minStabilitySafetyFactor": _stability_min(result),
        "wallBottomElevation": embedment.get("afterBottomElevationM"),
        "wallEmbedmentAddedM": embedment.get("addedEmbedmentM"),
        "wallEmbedmentMinimumFactor": embedment.get("afterMinimumFactor"),
        "wallEmbedmentDesignStatus": embedment.get("status"),
        "strengthStatus": result.design_review_summary.strength_status if result.design_review_summary else "manual_review",
        "stiffnessStatus": result.design_review_summary.stiffness_status if result.design_review_summary else "manual_review",
        "stabilityStatus": result.design_review_summary.stability_status if result.design_review_summary else "manual_review",
        "ifcStatus": ifc_quality.status if ifc_quality else "manual_review",
        "ifcRisk": _ifc_risk_level(result),
        "formalGateStatus": formal_gate.status if formal_gate else "manual_review",
        "formalGateAllowed": bool(formal_gate.allowed_for_official_issue) if formal_gate else False,
        "checkSummary": result.check_summary,
        "failCount": int((result.check_summary or {}).get("fail", 0) or 0),
        "warningCount": int((result.check_summary or {}).get("warning", 0) or 0),
        "manualReviewCount": int((result.check_summary or {}).get("manualReview", (result.check_summary or {}).get("manual_review", 0)) or 0),
        "governingCheckStatus": result.governing_values.governing_check_status,
        "calculationResultId": result.id,
        "calculatedTopologyHash": _support_topology_hash(trial_project),
        "geologyCoverage": geological_coverage_audit(trial_project),
        "calculationDiagnostics": dict((result.design_iteration_summary or {}).get("calculationDiagnostics") or {}),
        "note": "该候选已使用完整计算链路复算：施工工况、支撑轴力、墙体位移/内力、围檩内力、稳定性、IFC 兼容性和正式化闸门。",
    }


def _rank_full_candidate_calculations(outputs: list[dict[str, Any]]) -> None:
    """Add an auditable decision score after all A/B/C schemes complete calculation.

    The pre-screen score remains available for geometry search.  Final decision
    scoring uses the actual calculated response together with member count and
    long-direct-strut exposure.  It only ranks the compared schemes; it never
    bypasses engineering failures or the formal issue gate.
    """
    valid = [item for item in outputs if not item.get("error")]
    if not valid:
        return

    lower_is_better: dict[str, float] = {
        "maxSupportAxialForce": 0.12,
        "maxDisplacement": 0.12,
        "maxWallMoment": 0.08,
        "maxWallShear": 0.03,
        "maxWaleMoment": 0.06,
        "maxWaleDeflection": 0.09,
        "supportCount": 0.08,
        "columnCount": 0.07,
        "maxSpanLength": 0.12,
        "excessiveDirectStrutCount": 0.08,
    }
    # The original constrained-optimizer score contributes 15%; this preserves
    # obstacle, spacing, muck-path and symmetry knowledge not repeated below.
    higher_is_better = {"score": 0.15}

    values: dict[str, list[float]] = {}
    for key in [*lower_is_better, *higher_is_better]:
        vals: list[float] = []
        for item in valid:
            raw = item.get(key)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0.0
            if not math.isfinite(value):
                value = 0.0
            vals.append(value)
        values[key] = vals

    def normalized(key: str, index: int, lower: bool) -> float:
        vals = values[key]
        low, high = min(vals), max(vals)
        if abs(high - low) <= 1e-12:
            return 1.0
        value = vals[index]
        return (high - value) / (high - low) if lower else (value - low) / (high - low)

    for index, item in enumerate(valid):
        component_scores: dict[str, float] = {}
        total = 0.0
        for key, weight in lower_is_better.items():
            score = normalized(key, index, True)
            component_scores[key] = round(score * 100.0, 2)
            total += weight * score
        for key, weight in higher_is_better.items():
            score = normalized(key, index, False)
            component_scores[key] = round(score * 100.0, 2)
            total += weight * score

        fail_count = int(item.get("failCount", 0) or 0)
        warning_count = int(item.get("warningCount", 0) or 0)
        manual_count = int(item.get("manualReviewCount", 0) or 0)
        penalty = min(80.0, fail_count * 25.0 + warning_count * 0.35 + manual_count * 0.5)
        decision_score = max(0.0, total * 100.0 - penalty)
        item["decisionScore"] = round(decision_score, 2)
        item["decisionComponents"] = component_scores
        item["decisionPenalty"] = round(penalty, 2)

    ranked = sorted(
        valid,
        key=lambda item: (
            int(item.get("failCount", 0) or 0) > 0,
            -float(item.get("decisionScore", 0.0) or 0.0),
            int(item.get("rank", 999) or 999),
        ),
    )
    for rank, item in enumerate(ranked, start=1):
        item["decisionRank"] = rank
        item["recommendedByFullCalculation"] = rank == 1 and int(item.get("failCount", 0) or 0) == 0
        strengths = sorted(
            ((key, value) for key, value in (item.get("decisionComponents") or {}).items()),
            key=lambda pair: -float(pair[1]),
        )[:3]
        label_map = {
            "maxSupportAxialForce": "支撑轴力",
            "maxDisplacement": "墙体位移",
            "maxWallMoment": "墙体弯矩",
            "maxWallShear": "墙体剪力",
            "maxWaleMoment": "围檩弯矩",
            "maxWaleDeflection": "围檩挠度",
            "supportCount": "支撑数量",
            "columnCount": "立柱数量",
            "maxSpanLength": "最大跨度",
            "excessiveDirectStrutCount": "超长直对撑",
            "score": "几何与施工代理评分",
        }
        strengths_text = "、".join(label_map.get(key, key) for key, _value in strengths)
        item["decisionReason"] = (
            f"完整计算综合排名第 {rank}；优势指标：{strengths_text}。"
            f"Fail={int(item.get('failCount', 0) or 0)}，Warning={int(item.get('warningCount', 0) or 0)}；"
            "采用后仍须重新计算并通过正式发行闸门。"
        )

    for item in outputs:
        if item.get("error"):
            item["decisionScore"] = 0.0
            item["decisionRank"] = None
            item["recommendedByFullCalculation"] = False
            item["decisionReason"] = f"完整计算失败：{item.get('error')}"


def run_single_candidate_calculation(
    project: Project,
    candidate,
    *,
    index: int = 0,
    use_cache: bool = True,
) -> dict[str, Any]:
    from app.services.support_layout_optimizer import build_support_system_from_candidate

    label = _candidate_label(index)
    input_hash = candidate_input_hash(project, candidate)
    if use_cache:
        cached = get_cached_candidate_result(input_hash)
        if cached is not None:
            result = dict(cached)
            result["cacheHit"] = True
            result["inputHash"] = input_hash
            return result
    pattern = str((candidate.variable_summary or {}).get("positionPattern", "as_generated"))
    amplitude = float((candidate.variable_summary or {}).get("lineOffsetAmplitude", 0.0) or 0.0)
    topology_strategy = str((candidate.variable_summary or {}).get("topologyFamily", "balanced_grid"))
    lightweight_project = project.model_copy(deep=False)
    lightweight_project.calculation_results = []
    lightweight_project.calculation_cases = []
    trial_project = lightweight_project.model_copy(deep=True)
    system, adjustments = build_support_system_from_candidate(
        lightweight_project, candidate.target_spacing, candidate.column_max_span, pattern, amplitude, topology_strategy
    )
    if system is None:
        return {
            "schemeLabel": label, "candidateId": candidate.id, "rank": candidate.rank,
            "error": "候选支撑体系重建失败。", "cacheHit": False, "inputHash": input_hash,
        }
    trial_project.retaining_system = system
    trial_project.calculation_results = []
    # Candidate comparison must evaluate the constructible, strength-gated
    # topology.  V3.14 calculated raw candidates with auto_repair=False, so a
    # scheme could be reported with unsupported return walls or excessive wale
    # bays even though the adopted project was repaired before its next run.
    # Apply only additive topology gates here; do not launch another candidate
    # optimization that could silently replace the scheme being compared.
    geology_extended = ensure_geological_model_covers_excavation(trial_project)
    concave_preflight = repair_concave_return_supports(trial_project)
    wale_preflight = repair_wale_support_bays(trial_project)
    trial_project.calculation_cases = build_default_construction_cases(trial_project)
    candidate_result = run_calculation(
        trial_project, trial_project.calculation_cases[0], auto_repair=False, include_candidate_comparison=False
    )
    summary = _summarize_candidate_calculation(label, candidate, candidate_result, trial_project)
    summary["changedSupportCount"] = len(adjustments)
    summary["topologyFamily"] = topology_strategy
    summary["schemeName"] = str((candidate.variable_summary or {}).get("schemeLabel", topology_strategy))
    summary["strengthTopologyPreflight"] = {
        "concaveReturnRepair": concave_preflight,
        "waleSupportBayRepair": wale_preflight,
        "geologyExtended": geology_extended,
        "addedSupportCount": int(concave_preflight.get("addedSupportCount", 0) or 0) + int(wale_preflight.get("addedSupportCount", 0) or 0),
    }
    summary["supportCount"] = len(trial_project.retaining_system.supports)
    summary["columnCount"] = len(trial_project.retaining_system.columns)
    summary["maxSpanLength"] = max((float(item.span_length or 0.0) for item in trial_project.retaining_system.supports), default=0.0)
    summary["cacheHit"] = False
    summary["inputHash"] = input_hash
    if use_cache and not summary.get("error"):
        put_cached_candidate_result(input_hash, summary)
    return summary


def _compare_top_support_candidates(project: Project, support_repair, top_n: int = 3) -> list[dict[str, Any]]:
    if not project.excavation or not project.retaining_system or not support_repair:
        return []
    candidates = list((support_repair.candidates or [])[:top_n])
    if not candidates:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def worker(index_and_candidate):
        index, candidate = index_and_candidate
        return run_single_candidate_calculation(project, candidate, index=index, use_cache=True)

    outputs: list[dict[str, Any]] = []
    # Dense NumPy solves already use threaded BLAS. Running three large candidate
    # matrices in Python threads can oversubscribe CPU/memory and take far longer
    # than three serial solves. Keep true parallelism for compact projects and use
    # deterministic serial evaluation for large/irregular retaining systems.
    complexity = len(project.retaining_system.supports) + 8 * len(project.retaining_system.diaphragm_walls)
    configured_workers = max(1, min(3, int(os.getenv("PITGUARD_CANDIDATE_WORKERS", "1"))))
    max_workers = max(1, min(configured_workers, top_n, len(candidates))) if complexity <= 160 else 1
    if max_workers == 1:
        for item in enumerate(candidates):
            try:
                outputs.append(worker(item))
            except Exception as exc:
                outputs.append({"schemeLabel": _candidate_label(item[0]), "error": str(exc)})
            finally:
                # Each candidate builds a deep project copy and dense NumPy
                # matrices. Release the unreachable graph before starting the
                # next scheme on small-memory production instances.
                gc.collect()
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(worker, item): item[0] for item in enumerate(candidates)}
            for future in as_completed(future_map):
                try:
                    outputs.append(future.result())
                except Exception as exc:  # keep the main calculation usable if one candidate fails
                    outputs.append({"schemeLabel": _candidate_label(future_map[future]), "error": str(exc)})
    for item in outputs:
        item["comparisonExecutionMode"] = "serial_large_model" if max_workers == 1 else "parallel_compact_model"
    outputs.sort(key=lambda item: item.get("schemeLabel", "Z"))
    _rank_full_candidate_calculations(outputs)
    by_id = {item.get("candidateId"): item for item in outputs if item.get("candidateId")}
    for candidate in support_repair.candidates or []:
        if candidate.id in by_id:
            candidate.full_calculation = by_id[candidate.id]
    support_repair.candidate_full_calculations = outputs
    return outputs


def run_candidate_comparison_for_project(project: Project, top_n: int = 3) -> list[dict[str, Any]]:
    if not project.excavation:
        raise ValueError("Project has no excavation")
    if not project.retaining_system:
        raise ValueError("Project has no retaining system")
    support_repair = project.retaining_system.support_layout_repair or auto_repair_support_layout(project)
    comparison = _compare_top_support_candidates(project, support_repair, top_n=top_n)
    project.retaining_system.support_layout_repair = support_repair
    project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
    project.retaining_system.layout_summary["candidateFullCalculationComparison"] = comparison
    return comparison


def run_calculation(project: Project, calculation_case: CalculationCase | None = None, auto_repair: bool = True, include_candidate_comparison: bool = False) -> CalculationResult:
    if not project.excavation:
        raise ValueError("Project has no excavation")
    if not project.retaining_system:
        raise ValueError("Project has no retaining system")
    geology_extended = ensure_geological_model_covers_excavation(project)
    geology_audit = geological_coverage_audit(project)
    geology_screening_fallback = False
    if not geology_audit.get("designDomainCovered", False):
        has_surface_model = bool(project.geological_model and project.geological_model.surfaces)
        if not project.boreholes and not has_surface_model:
            geology_screening_fallback = True
            geology_audit = dict(geology_audit)
            geology_audit.update({
                "status": "fail",
                "coverageStatus": "fail",
                "extrapolationStatus": "manual_review",
                "designDomainCovered": False,
                "screeningFallbackUsed": True,
                "message": "缺少钻孔和可覆盖设计域的地质模型；本次仅使用未验证单层土参数进行初步筛查，正式发行被阻断。",
            })
        else:
            raise ValueError("Geological model does not cover the retaining-system design domain")
    strength_auto_enabled = bool(getattr(project.design_settings, "auto_strength_design_enabled", True))
    requested_case = calculation_case or (project.calculation_cases[-1] if project.calculation_cases else None)
    wall_embedment_preflight = auto_design_wall_embedment(
        project,
        requested_case,
        enabled=bool(getattr(project.design_settings, "auto_wall_embedment_design_enabled", True)),
    )
    concave_topology_preflight = repair_concave_return_supports(project) if auto_repair else {"changed": False}
    wale_topology_preflight = repair_wale_support_bays(project) if auto_repair and strength_auto_enabled else {"changed": False, "status": "not_run"}
    topology_preflight = {
        "changed": bool(concave_topology_preflight.get("changed") or wale_topology_preflight.get("changed")),
        "addedSupportCount": int(concave_topology_preflight.get("addedSupportCount", 0) or 0) + int(wale_topology_preflight.get("addedSupportCount", 0) or 0),
        "missingFacesBefore": list(concave_topology_preflight.get("missingFacesBefore") or concave_topology_preflight.get("missingFaces") or []),
        "concaveReturnRepair": concave_topology_preflight,
        "waleSupportBayRepair": wale_topology_preflight,
    }
    if auto_repair:
        current_quality = evaluate_support_layout_quality(project)
        hard_geometry_failure = any(
            issue.severity == "fail"
            and issue.category in {
                "support_spacing", "support_span", "wale_support_bay", "support_crossing",
                "support_outside_excavation", "obstacle_clearance", "temporary_column",
                "replacement_path", "support_to_support_terminal",
                "unsupported_internal_endpoint", "corner_brace_fan_geometry",
                "corner_brace_wall_node_congestion", "support_station_cluster",
            }
            for issue in current_quality.issues
        )
        if hard_geometry_failure or not project.retaining_system.supports:
            support_repair = auto_repair_support_layout(project)
            # The constrained optimizer may replace the preflight topology.
            # Re-apply additive wall-restraint and wale-bay hard gates to the
            # selected candidate before construction stages are synchronized.
            post_concave = repair_concave_return_supports(project)
            post_wale = repair_wale_support_bays(project) if strength_auto_enabled else {"changed": False, "status": "not_run"}
            post_added = int(post_concave.get("addedSupportCount", 0) or 0) + int(post_wale.get("addedSupportCount", 0) or 0)
            if post_added:
                topology_preflight["changed"] = True
                topology_preflight["addedSupportCount"] = int(topology_preflight.get("addedSupportCount", 0) or 0) + post_added
                topology_preflight["postOptimizationRepair"] = {
                    "changed": True,
                    "addedSupportCount": post_added,
                    "concaveReturnRepair": post_concave,
                    "waleSupportBayRepair": post_wale,
                }
                topology_preflight["waleSupportBayRepair"] = post_wale if post_wale.get("changed") else topology_preflight.get("waleSupportBayRepair", {})
                topology_preflight["concaveReturnRepair"] = post_concave if post_concave.get("changed") else topology_preflight.get("concaveReturnRepair", {})
                support_repair.actions.append({
                    "action": "post_optimization_strength_gate_repair",
                    "description": f"候选拓扑采用后再次执行回墙与围檩支点硬门禁，增补 {post_added} 根构件。",
                })
                support_repair.summary += f" 候选采用后按强度前置门禁增补 {post_added} 根局部构件。"
        else:
            # Calculation is deterministic and fast by default. Candidate
            # enumeration remains an explicit design action, rather than being
            # silently repeated every time the user recalculates.
            support_repair = project.retaining_system.support_layout_repair or SupportLayoutRepairSummary(
                status=current_quality.status,
                score_before=current_quality.score,
                score_after=current_quality.score,
                summary="当前支撑拓扑无硬性几何失败；本次计算保持现有方案。需要方案比选时请显式运行支撑优化。",
                unresolved_issues=[issue for issue in current_quality.issues if issue.severity != "pass"][:30],
                actions=[{"action": "calculation_preserved_current_support_topology", "description": "避免计算时隐式重建支撑和使施工阶段 ID 失效。"}],
            )
            project.retaining_system.support_layout_repair = support_repair
    else:
        support_repair = project.retaining_system.support_layout_repair if project.retaining_system else None

    # The current global solver treats ordinary struts as axial wall-to-wall
    # members. A branch ending at another strut midspan has no compatible
    # in-plane transverse stiffness or reaction in that model, even when a
    # temporary column exists at the plan node. Refuse to calculate such a
    # topology instead of assigning it a fictitious axial support.
    load_path_quality = evaluate_support_layout_quality(project)
    invalid_load_path = [
        issue for issue in load_path_quality.issues
        if issue.severity == "fail"
        and issue.category in {
            "support_to_support_terminal",
            "unsupported_internal_endpoint",
            "support_crossing",
            "support_outside_excavation",
        }
    ]
    if invalid_load_path:
        categories = sorted({issue.category for issue in invalid_load_path})
        raise ValueError(
            "水平支撑传力路径不成立，已阻断计算：" + "、".join(categories)
            + "。普通轴压支撑必须两端落在围护墙/围檩/环梁节点；端墙应采用直接对撑或墙—墙长斜撑。"
        )

    case, support_case_sync = synchronize_calculation_case_supports(project, requested_case)
    if support_case_sync.get("synchronized"):
        replaced = False
        if requested_case is not None:
            for index, existing_case in enumerate(project.calculation_cases):
                if existing_case.id == requested_case.id:
                    case.id = requested_case.id
                    case.created_at = requested_case.created_at
                    project.calculation_cases[index] = case
                    replaced = True
                    break
        if not replaced:
            project.calculation_cases.append(case)
    calculation_contract = build_calculation_contract(project, case)
    calculation_input_audit = audit_calculation_inputs(project, case)
    stage_results: list[StageCalculationResult] = []
    global_checks: list[dict[str, Any]] = list(calculation_input_audit.get("checks") or [])
    calibration = dict(project.advanced_engineering.get("calibrationFactors") or {})
    wall_stiffness_factor = float(calibration.get("wallStiffnessFactor") or 1.0)
    soil_modulus_factor = float(calibration.get("soilModulusFactor") or 1.0)
    support_stiffness_factor = float(calibration.get("supportStiffnessFactor") or 1.0)
    groundwater_offset_m = float(calibration.get("groundwaterOffsetM") or 0.0)

    max_pressure = 0.0
    max_support_force = 0.0
    max_wall_moment = 0.0
    max_wall_shear = 0.0
    max_displacement = 0.0
    warnings = [
        "计算结果用于工程设计辅助；正式施工图和专家论证仍需注册岩土/结构工程师签审。",
    ]
    if geology_extended:
        warnings.append("计算前已自动外扩地质模型，使围护结构及施工影响区处于地质设计域内；外推区域按低置信度处理。")
    if geology_screening_fallback:
        warnings.append("当前计算采用未验证的单层土初步筛查参数；地质设计域硬闸门保持失败，成果不得正式发行。")
    if wall_embedment_preflight.get("changed"):
        warnings.append(str(wall_embedment_preflight.get("message") or "计算前已按嵌固稳定筛查自动加深围护墙墙趾。"))
    elif wall_embedment_preflight.get("status") == "fail":
        warnings.append(str(wall_embedment_preflight.get("message") or "墙趾嵌固稳定筛查仍未闭合。"))
    if concave_topology_preflight.get("changed"):
        warnings.append(
            "计算前拓扑诊断发现凹形回墙缺少直接支点，已增补 "
            f"{concave_topology_preflight.get('addedSupportCount', 0)} 根局部法向次对撑并重建立柱/节点。"
        )
    if wale_topology_preflight.get("changed"):
        warnings.append(str(wale_topology_preflight.get("action") or "围檩支点间距超限已通过角部扇形斜撑自动修复。"))
    if support_case_sync.get("synchronized"):
        before = support_case_sync.get("before") or {}
        warnings.append(
            "支撑体系拓扑已在计算前自动同步："
            f"修复陈旧支撑引用 {before.get('staleSupportCount', 0)} 个、"
            f"无有效支撑阶段 {before.get('stageCountWithNoValidSupport', 0)} 个。"
        )

    supports_by_id = {s.id: s for s in project.retaining_system.supports}
    walls_by_segment = {w.segment_id: w for w in project.retaining_system.diaphragm_walls}
    top = project.excavation.top_elevation
    bottom = project.excavation.bottom_elevation
    final_depth = top - bottom
    gamma0 = importance_factor(project.design_settings.safety_grade)
    segment_wall_envelopes: dict[str, dict[str, float]] = {}
    segment_wall_design: dict[str, dict[str, float]] = {}

    for segment in project.excavation.segments:
        section = extract_representative_section(project, segment.id)
        wall = walls_by_segment.get(segment.id)
        wall_bottom = wall.bottom_elevation if wall else bottom - max(4.0, 0.35 * final_depth)
        wall_thickness = wall.thickness if wall else 1.0
        concrete_grade = wall.concrete_grade if wall else "C35"
        rebar_grade = wall.rebar_grade if wall else "HRB400"
        segment_max = {"moment": 0.0, "shear": 0.0, "displacement": 0.0}
        segment_design = {"moment": 0.0, "shear": 0.0}

        for stage in case.stages:
            stage_depth = min(final_depth, max(0.0, top - stage.excavation_elevation)) or final_depth
            gw_out = (stage.groundwater_level_outside if stage.groundwater_level_outside is not None else project.design_settings.groundwater_level) + groundwater_offset_m
            gw_in = stage.groundwater_level_inside if stage.groundwater_level_inside is not None else project.design_settings.groundwater_level
            pressure = calculate_lateral_pressure_profile(
                soil_profile=section.layers,
                excavation_depth=stage_depth,
                groundwater_level=gw_out,
                groundwater_level_inside=gw_in,
                surcharge=stage.surcharge,
                top_elevation=top,
                calculation_depth=max(stage_depth, top - wall_bottom),
                mode="at_rest" if "严格" in project.design_settings.environment_grade else "active",
            )
            deactivated_ids = set(stage.deactivated_support_ids or [])
            active_supports = [supports_by_id[sid] for sid in stage.active_support_ids if sid in supports_by_id and sid not in deactivated_ids]
            transferred_levels = {int(level) for level in (stage.transferred_support_levels or [])}
            transferred_supports = [
                support for support in project.retaining_system.supports
                if int(support.level_index) in transferred_levels and support.id not in {item.id for item in active_supports}
            ]
            segment_supports = [s for s in active_supports if segment.name in {s.start_face_code, s.end_face_code}]
            segment_transferred_supports = [s for s in transferred_supports if segment.name in {s.start_face_code, s.end_face_code}]
            load_path_supports = [*active_supports, *transferred_supports]
            corner_transfer_supports, wall_restraint_audit = build_effective_wall_restraints(
                project.excavation,
                segment,
                load_path_supports,
                target_spacing_m=float(project.design_settings.default_support_spacing or 5.0),
            )
            wall_restraint_supports = [*segment_supports, *segment_transferred_supports, *corner_transfer_supports]
            # Direct supports remain the source of member axial forces. Short stepped/return
            # walls may receive reduced-stiffness analytical restraints from the two adjacent
            # supported faces through continuous wales; these proxies never enter quantities.
            wale_stage_results = []
            # During replacement/removal stages the basement slab or replacement
            # waler remains part of the lateral load path at the transferred
            # elevation.  Using only the still-active struts makes the uppermost
            # support inherit the full excavation pressure band and creates
            # fictitious wale moments.  Retain transferred levels while forming
            # vertical tributary bands, then keep member forces/envelopes only for
            # physical supports and wales that remain active in this stage.
            force_distribution_supports = [*segment_supports, *segment_transferred_supports]
            forces_all = estimate_support_axial_forces(
                pressure,
                force_distribution_supports,
                segment.length,
                top,
                top - stage_depth,
                safety_grade=project.design_settings.safety_grade,
                segment_name=segment.name,
                segment=segment,
                wale_beams=project.retaining_system.wale_beams,
                stage_id=stage.id,
                wale_result_collector=wale_stage_results,
            )
            active_segment_support_ids = {item.id for item in segment_supports}
            active_segment_levels = {int(item.level_index) for item in segment_supports}
            forces = [item for item in forces_all if item.support_id in active_segment_support_ids]
            wale_stage_results = [
                item for item in wale_stage_results
                if int(item.level_index) in active_segment_levels
            ]
            wall_force_raw = analyze_wall_on_elastic_foundation(
                soil_profile=section.layers,
                supports=segment_supports,
                excavation_depth=stage_depth,
                groundwater_level_outside=gw_out,
                groundwater_level_inside=gw_in,
                surcharge=stage.surcharge,
                top_elevation=top,
                wall_bottom_elevation=wall_bottom,
                wall_thickness=wall_thickness,
                concrete_grade=concrete_grade,
                segment=segment,
                transferred_supports=[*segment_transferred_supports, *corner_transfer_supports],
                transfer_stiffness_factor=0.55 if corner_transfer_supports and not segment_transferred_supports else 1.0,
                wall_stiffness_factor=wall_stiffness_factor,
                soil_modulus_factor=soil_modulus_factor,
                support_stiffness_factor=support_stiffness_factor,
            )
            wall_force = _wall_force_model(segment.id, stage.id, wall_force_raw, gamma0)
            global_coupled_raw = solve_global_wall_wale_support_system(
                pressure_profile=pressure,
                segment=segment,
                face_code=segment.name,
                active_supports=wall_restraint_supports,
                top_elevation=top,
                excavation_elevation=top - stage_depth,
                wall_bottom_elevation=wall_bottom,
                wall_thickness=wall_thickness,
                concrete_grade=concrete_grade,
                soil_profile=section.layers,
                stage_id=stage.id,
                stage_type=stage.stage_type,
                wall_stiffness_factor=wall_stiffness_factor,
                soil_modulus_factor=soil_modulus_factor,
                support_stiffness_factor=support_stiffness_factor,
                replacement_slab_properties={
                    "effectiveWidthM": project.design_settings.replacement_slab_effective_width_m,
                    "thicknessM": project.design_settings.replacement_slab_thickness_m,
                    "elasticModulusMpa": project.design_settings.replacement_slab_elastic_modulus_mpa,
                    "connectionReduction": project.design_settings.replacement_connection_reduction,
                    "transferLengthM": float(segment.length),
                },
            )
            global_coupled = GlobalCoupledSystemResult(**global_coupled_raw)
            # Upgrade support force entries with global matrix reactions when the
            # same support endpoint is solved in the coupled model.  This keeps
            # the V1.6/V1.9 continuous-wale fields while promoting the governing
            # standard/design values to the wall-wale-support global stiffness result.
            reaction_map = {(r.support_id, r.endpoint): r for r in global_coupled.support_reactions}
            for force in forces:
                key = (force.support_id, force.support_endpoint)
                reaction = reaction_map.get(key)
                reference_force = max(float(force.axial_force or 0.0), 0.0)
                force.reference_axial_force = round(reference_force, 3)
                if reaction and reaction.axial_force > 0:
                    global_force = max(float(reaction.axial_force), 0.0)
                    ratio = global_force / max(reference_force, 1e-9) if reference_force > 1e-6 else None
                    force.global_axial_force = round(global_force, 3)
                    force.force_reconciliation_ratio = round(ratio, 3) if ratio is not None else None
                    if global_coupled.fallback or (global_coupled.condition_number is not None and global_coupled.condition_number > 1.0e12):
                        reconciliation_status = "manual_review"
                    elif ratio is not None and (ratio > 3.0 or ratio < 0.20):
                        reconciliation_status = "warning"
                    else:
                        reconciliation_status = "pass"
                    force.force_reconciliation_status = reconciliation_status
                    # The global matrix remains governing; the continuous-wale
                    # result is retained as an independent reference and ratio
                    # diagnostic rather than silently discarded.
                    force.axial_force = round(global_force, 3)
                    force.axial_force_design = round(design_effect_standard_to_uls(global_force, safety_grade=project.design_settings.safety_grade, combined_partial_factor=LOAD_FACTOR_RETAINING), 3)
                    force.continuous_beam_reaction = reaction.node_reaction
                    force.elastic_support_stiffness = reaction.spring_stiffness
                    force.normal_projection_factor = reaction.normal_projection_factor
                    force.distribution_method = "global_wall_wale_support_matrix; continuous_wale_reference"
                    force.distribution_note = f"全局矩阵轴力/连续围檩参考轴力比={ratio:.3f}。" if ratio is not None else "全局矩阵轴力已采用；连续围檩结果保留为参考。"
                    force.method = "global wall-wale-support stiffness matrix; independent continuous-wale reference retained for reconciliation"
            max_pressure = max(max_pressure, *(abs(p.total_pressure) for p in pressure.points))
            max_support_force = max(max_support_force, *(f.axial_force_design or f.axial_force for f in forces), 0.0)
            m = abs(wall_force.max_moment)
            v = abs(wall_force.max_shear)
            d = abs(wall_force.max_displacement or 0.0)
            m_design = abs(wall_force.max_moment_design or m)
            v_design = abs(wall_force.max_shear_design or v)
            max_wall_moment = max(max_wall_moment, m)
            max_wall_shear = max(max_wall_shear, v)
            max_displacement = max(max_displacement, d)
            segment_max["moment"] = max(segment_max["moment"], m)
            segment_max["shear"] = max(segment_max["shear"], v)
            segment_max["displacement"] = max(segment_max["displacement"], d)
            segment_design["moment"] = max(segment_design["moment"], m_design)
            segment_design["shear"] = max(segment_design["shear"], v_design)
            stage_checks: list[dict[str, Any]] = []
            restraint_status = str(wall_restraint_audit.get("status") or "manual_review")
            stage_checks.append({
                "ruleId": "PITGUARD-WALL-RESTRAINT-LOAD-PATH",
                "objectId": wall.id if wall else segment.id,
                "objectType": "DiaphragmWallPanel",
                "status": restraint_status,
                "calculatedValue": len(wall_restraint_audit.get("analyticalTransferLevels") or []),
                "limitValue": len(wall_restraint_audit.get("activeLevels") or []),
                "unit": "support levels",
                "message": (
                    "墙面已形成直接支撑或短回墙两端围檩传力约束。"
                    if restraint_status == "pass"
                    else "墙面存在未闭合支撑层，当前内力不得直接用于构件设计。"
                ),
                "clauseReference": "JGJ 120 支撑体系传力明确性与构造连续性原则；短回墙等效约束为软件分析模型，需节点详图复核",
                "stageId": stage.id,
                "stageName": stage.name,
                "segmentId": segment.id,
                "diagnostics": wall_restraint_audit,
            })
            numerical = dict(global_coupled.equilibrium_diagnostics or {})
            residual_limit = float(getattr(project.design_settings, "maximum_equilibrium_relative_residual", 1.0e-8) or 1.0e-8)
            residual_value = numerical.get("relativeResidual")
            if not isinstance(residual_value, (int, float)):
                numerical_status = "manual_review"
            elif float(residual_value) > residual_limit * 100.0:
                numerical_status = "fail"
            elif float(residual_value) > residual_limit:
                numerical_status = "warning"
            else:
                numerical_status = "pass"
            stage_checks.append({
                "ruleId": "PITGUARD-NUMERICAL-EQUILIBRIUM",
                "objectId": segment.id,
                "objectType": "GlobalCoupledSystem",
                "status": numerical_status,
                "calculatedValue": numerical.get("relativeResidual"),
                "limitValue": residual_limit,
                "unit": "relative residual",
                "message": numerical.get("message") or "全局刚度方程数值质量需要复核。",
                "clauseReference": "PitGuard numerical quality gate; engineering-code checks remain independent",
                "stageId": stage.id,
                "stageName": stage.name,
                "diagnostics": numerical,
            })
            condition_number = global_coupled.condition_number
            condition_review_limit = float(getattr(project.design_settings, "maximum_matrix_condition_number", 1.0e12) or 1.0e12)
            condition_fail_limit = condition_review_limit * 100.0
            condition_warning_limit = condition_review_limit / 100.0
            if condition_number is None:
                condition_status = "manual_review"
                condition_message = "未获得全局矩阵条件数，需复核矩阵组装与边界约束。"
            elif condition_number > condition_fail_limit:
                condition_status = "fail"
                condition_message = "全局矩阵严重病态，当前内力与位移结果不得作为设计依据。"
            elif condition_number > condition_review_limit:
                condition_status = "manual_review"
                condition_message = "全局矩阵条件数超过项目复核阈值，需复核刚度尺度、约束和构件连接。"
            elif condition_number > condition_warning_limit:
                condition_status = "warning"
                condition_message = "全局矩阵条件数接近项目复核阈值，建议开展参数尺度与边界条件复核。"
            else:
                condition_status = "pass"
                condition_message = "全局矩阵条件数处于项目数值质量门禁允许范围。"
            stage_checks.append({
                "ruleId": "PITGUARD-MATRIX-CONDITION",
                "objectId": segment.id,
                "objectType": "GlobalCoupledSystem",
                "status": condition_status,
                "calculatedValue": condition_number,
                "limitValue": condition_review_limit,
                "unit": "dimensionless",
                "message": condition_message,
                "clauseReference": "PitGuard numerical conditioning gate; no fabricated code clause",
                "stageId": stage.id,
                "stageName": stage.name,
            })
            replacement_status = str(global_coupled.slab_replacement_status or "not_active")
            if global_coupled.slab_replacement_required and replacement_status in {"missing", "invalid"}:
                stage_checks.append({
                    "ruleId": "REPLACEMENT-STIFFNESS-MISSING",
                    "objectId": project.retaining_system.id,
                    "objectType": "ReplacementSlabSystem",
                    "status": "fail",
                    "calculatedValue": None,
                    "limitValue": 1.0,
                    "unit": "kN/m",
                    "message": "当前施工阶段要求楼板/换撑参与，但等效刚度参数缺失或无效。请补充有效宽度、板厚、弹性模量和连接折减后重新计算。",
                    "clauseReference": "project replacement-stage load-path requirement",
                    "stageId": stage.id,
                    "stageName": stage.name,
                })
            if wall:
                embedment_check = check_embedment_stability(
                    object_id=wall.id,
                    soil_profile=section.layers,
                    excavation_depth=stage_depth,
                    wall_bottom_elevation=wall_bottom,
                    top_elevation=top,
                    groundwater_level_outside=gw_out,
                    groundwater_level_inside=gw_in,
                    surcharge=stage.surcharge,
                    safety_grade=project.design_settings.safety_grade,
                )
                stage_checks.append(_check_to_dict(embedment_check))
                stage_checks.append(_check_to_dict(check_wall_deformation(wall.id, stage_depth, d, project.design_settings.environment_grade)))
                stage_checks.append(_check_to_dict(check_water_stability(project.excavation.id, max(0.0, bottom - wall_bottom), gw_out, gw_in, top - stage_depth, safety_grade=project.design_settings.safety_grade)))
                stage_checks.append(
                    _check_to_dict(
                        check_base_heave_stability(
                            object_id=project.excavation.id,
                            soil_profile=section.layers,
                            excavation_depth=stage_depth,
                            embedment_depth=max(0.0, bottom - wall_bottom),
                            top_elevation=top,
                            excavation_bottom_elevation=top - stage_depth,
                            surcharge=stage.surcharge,
                            safety_grade=project.design_settings.safety_grade,
                        )
                    )
                )
                stage_checks.append(_check_to_dict(check_overall_stability_circular_search(
                    object_id=project.excavation.id,
                    soil_profile=section.layers,
                    excavation_depth=stage_depth,
                    embedment_depth=max(0.0, bottom - wall_bottom),
                    top_elevation=top,
                    excavation_bottom_elevation=top - stage_depth,
                    surcharge=stage.surcharge,
                    safety_grade=project.design_settings.safety_grade,
                    pit_width=max(project.excavation.area / max(project.excavation.perimeter, 1.0), segment.length * 0.5) if project.excavation.area and project.excavation.perimeter else None,
                )))
                stage_checks.append(_check_to_dict(check_confined_water_uplift_stability(
                    object_id=project.excavation.id,
                    soil_profile=section.layers,
                    excavation_bottom_elevation=top - stage_depth,
                    aquifer_head_elevation=project.design_settings.confined_water_head_elevation if project.design_settings.confined_water_head_elevation is not None else (top - stage_depth),
                    aquitard_bottom_elevation=wall_bottom,
                    safety_grade=project.design_settings.safety_grade,
                )))
                stage_checks.append(_check_to_dict(check_dewatering_stage_stability(
                    object_id=project.excavation.id,
                    groundwater_level_outside=gw_out,
                    groundwater_level_inside=gw_in,
                    excavation_bottom_elevation=top - stage_depth,
                    wall_bottom_elevation=wall_bottom,
                    safety_grade=project.design_settings.safety_grade,
                )))
                stage_checks.append(_check_to_dict(check_layered_seepage_gradient(
                    object_id=project.excavation.id,
                    soil_profile=section.layers,
                    excavation_bottom_elevation=top - stage_depth,
                    wall_bottom_elevation=wall_bottom,
                    groundwater_level_outside=gw_out,
                    groundwater_level_inside=gw_in,
                    safety_grade=project.design_settings.safety_grade,
                )))
                stage_checks.append(_check_to_dict(check_weak_underlying_layer(
                    object_id=project.excavation.id,
                    soil_profile=section.layers,
                    excavation_bottom_elevation=top - stage_depth,
                    safety_grade=project.design_settings.safety_grade,
                )))
                flex = design_rectangular_flexural_reinforcement(m_design, wall_thickness, concrete_grade, rebar_grade)
                shear = check_rectangular_shear_capacity(v_design, wall_thickness, concrete_grade)
                stage_checks.append({
                    "ruleId": "GB50010-FLEXURE-SUBSET",
                    "objectId": wall.id,
                    "objectType": "DiaphragmWallPanel",
                    "status": flex["status"],
                    "calculatedValue": flex["asRequired"],
                    "limitValue": flex["barArrangement"]["providedAs"],
                    "unit": "mm2/m",
                    "message": f"正截面受弯配筋子集：Md={flex['momentDesign']} kN*m/m，建议 {flex['barArrangement']['description']}。",
                    "clauseReference": "GB 50010 6.2.10 subset; final clause applicability to verify",
                    "formula": "M <= alpha1*fc*b*x*(h0-x/2); alpha1*fc*b*x = fy*As",
                })
                stage_checks.append({
                    "ruleId": "GB50010-SHEAR-SUBSET",
                    "objectId": wall.id,
                    "objectType": "DiaphragmWallPanel",
                    "status": shear["status"],
                    "calculatedValue": shear["shearDesign"],
                    "limitValue": shear["concreteShearCapacity"],
                    "unit": "kN/m",
                    "message": "斜截面抗剪承载力子集筛查；箍筋、构造和截面尺寸需复核。",
                    "clauseReference": "GB 50010 shear subset; final clause applicability to verify",
                    "formula": "V <= 0.7*ft*b*h0 plus stirrup contribution if detailed",
                })
                stage_checks.append(_check_to_dict(check_minimum_wall_reinforcement(wall.id, wall_thickness, flex["barArrangement"]["diameter"], flex["barArrangement"]["spacing"])))
                stage_checks.append(_check_to_dict(check_combination_documented(wall.id, combination_record(permanent=m, variable=stage.surcharge))))
                moment_capacity_for_crack = rectangular_flexural_capacity_knm_per_m(flex["barArrangement"]["providedAs"], wall_thickness, concrete_grade, rebar_grade)
                stage_checks.append(_check_to_dict(check_crack_width(
                    wall.id,
                    m_design,
                    moment_capacity_for_crack,
                    flex["barArrangement"]["spacing"],
                    flex["barArrangement"]["diameter"],
                    project.design_settings.environment_grade,
                    rebar_grade,
                )))
                for detail_check in check_rebar_anchorage_and_lap(
                    object_id=wall.id,
                    bar_diameter_mm=flex["barArrangement"]["diameter"],
                    rebar_grade=rebar_grade,
                    available_anchor_length_mm=max(1200.0, 0.12 * max(wall.top_elevation - wall.bottom_elevation, 1.0) * 1000.0),
                    available_lap_length_mm=max(1400.0, 0.14 * max(wall.top_elevation - wall.bottom_elevation, 1.0) * 1000.0),
                    seismic=False,
                ):
                    stage_checks.append(_check_to_dict(detail_check))
                for detail_check in check_diaphragm_wall_construction(
                    object_id=wall.id,
                    thickness_m=wall_thickness,
                    concrete_grade=concrete_grade,
                    main_bar_diameter_mm=flex["barArrangement"]["diameter"],
                    main_bar_spacing_mm=flex["barArrangement"]["spacing"],
                    horizontal_bar_diameter_mm=16,
                    horizontal_bar_spacing_mm=200,
                ):
                    stage_checks.append(_check_to_dict(detail_check))
            for check in stage_checks:
                check.setdefault("stageId", stage.id)
                check.setdefault("stageName", stage.name)
                check.setdefault("segmentId", segment.id)
                check.setdefault("segmentName", segment.name)
            global_checks.extend(stage_checks)
            coupled_system_result = {
                "method": "V2.0 spatial wall-wale-support-column-slab stiffness matrix summary",
                "activeSupportCount": len(active_supports),
                "segmentSupportCount": len(segment_supports),
                "wallMaxMoment": wall_force.max_moment,
                "wallMaxDisplacement": wall_force.max_displacement,
                "waleResultCount": len(wale_stage_results),
                "maxWaleMoment": max((abs(w.max_moment) for w in wale_stage_results), default=0.0),
                "maxWaleShear": max((abs(w.max_shear) for w in wale_stage_results), default=0.0),
                "globalMatrixSize": global_coupled.matrix_size,
                "globalDofSummary": global_coupled.dof_summary,
                "globalMaxWallDisplacement": global_coupled.max_wall_displacement,
                "globalMaxSupportAxialForce": global_coupled.max_support_axial_force,
                "globalEquilibriumDiagnostics": global_coupled.equilibrium_diagnostics,
                "fallback": global_coupled.fallback,
                "globalSpatialMatrixSize": global_coupled.spatial_matrix_size,
                "globalSpatialDofSummary": global_coupled.spatial_dof_summary,
                "wallRotationNodeCount": len(global_coupled.wall_rotation_profile),
                "waleRotationNodeCount": len(global_coupled.wale_node_profile),
                "columnVerticalDofCount": len(global_coupled.column_vertical_dofs),
                "slabReplacementStiffness": global_coupled.slab_replacement_stiffness,
                "slabReplacementStatus": global_coupled.slab_replacement_status,
                "slabReplacementSource": global_coupled.slab_replacement_source,
                "slabReplacementRequired": global_coupled.slab_replacement_required,
                "slabReplacementComponents": global_coupled.slab_replacement_components,
                "wallRestraintAudit": wall_restraint_audit,
                "cornerTransferProxyCount": len(corner_transfer_supports),
                "note": "墙体/围檩转角、支撑空间方向、立柱竖向、节点刚域和楼板换撑均进入空间杆系代理矩阵；短回墙可由两端连续围檩形成折减分析约束，代理不计入工程量。",
            }
            stage_results.append(
                StageCalculationResult(
                    stage_id=stage.id,
                    segment_id=segment.id,
                    pressure_profile=pressure,
                    support_forces=forces,
                    wale_beam_results=wale_stage_results,
                    coupled_system_result=coupled_system_result,
                    global_coupled_result=global_coupled,
                    wall_internal_force=wall_force,
                    wall_internal_force_placeholder={
                        "status": "calculated",
                        "algorithm": wall_force.method,
                        "maxMoment": wall_force.max_moment,
                        "maxShear": wall_force.max_shear,
                        "maxDisplacement": wall_force.max_displacement,
                        "maxMomentDesign": wall_force.max_moment_design,
                        "maxShearDesign": wall_force.max_shear_design,
                        "supportReactions": wall_force_raw.get("supportReactions", []),
                        "points": wall_force_raw.get("points", [])[:: max(1, len(wall_force_raw.get("points", [])) // 20 or 1)],
                        "warnings": wall_force_raw.get("warnings", []),
                    },
                    stability_checks=[c for c in stage_checks if str(c.get("ruleId", "")).startswith("JGJ120")],
                    rc_checks=[c for c in stage_checks if str(c.get("ruleId", "")).startswith("GB50010") or str(c.get("ruleId", "")).startswith("GBT50010")],
                    checks=stage_checks,
                )
            )
        segment_wall_envelopes[segment.id] = segment_max
        segment_wall_design[segment.id] = segment_design

    support_checks: list[dict[str, Any]] = []
    for support in project.retaining_system.supports:
        standard_forces = [
            force.axial_force
            for result in stage_results
            for force in result.support_forces
            if force.support_id == support.id
        ]
        level_forces = [
            (force.axial_force_design or force.axial_force)
            for result in stage_results
            for force in result.support_forces
            if force.support_id == support.id
        ]
        if standard_forces or level_forces:
            base_standard = max(standard_forces) if standard_forces else max(level_forces) / max(gamma0 * LOAD_FACTOR_RETAINING, 1e-9)
            effects = _support_construction_effects(support, base_standard, project.design_settings.safety_grade)
            support.raw_axial_force_standard_envelope = round(base_standard, 3)
            related_forces = [
                force
                for result in stage_results
                for force in result.support_forces
                if force.support_id == support.id
            ]
            reconciliation_statuses = {force.force_reconciliation_status for force in related_forces if force.force_reconciliation_status}
            support.force_reconciliation_status = "manual_review" if "manual_review" in reconciliation_statuses else "warning" if "warning" in reconciliation_statuses else "pass"
            max_ratio = max((float(force.force_reconciliation_ratio) for force in related_forces if force.force_reconciliation_ratio is not None), default=None)
            support.force_reconciliation_note = (
                f"支撑轴力以全局矩阵为控制值，连续围檩为独立参考；最大比值={max_ratio:.3f}。"
                if max_ratio is not None
                else "支撑轴力缺少独立参考比对，需复核。"
            )
            support.preload = effects["preload"]
            support.preload_ratio = effects["preloadRatio"]
            support.temperature_delta_c = support.temperature_delta_c if support.temperature_delta_c is not None else (12.0 if support.section_type == "steel_pipe" else 8.0)
            support.thermal_axial_force = effects["thermal"]
            support.gap_closure_force = effects["gap"]
            support.construction_deviation_mm = effects["deviationMm"]
            support.eccentricity_moment = effects["eccentricityMoment"]
            support.effective_axial_force_standard = effects["effectiveStandard"]
            support.construction_effect_note = effects["note"]
            # Stored on the support as the envelope design axial force used by RC/steel checks and IFC export.
            support.design_axial_force = max(max(level_forces) if level_forces else 0.0, effects["design"])
            for result in stage_results:
                for force in result.support_forces:
                    if force.support_id == support.id:
                        stage_effects = _support_construction_effects(
                            support,
                            float(force.axial_force or 0.0),
                            project.design_settings.safety_grade,
                            preload_override=float(effects["preload"]),
                        )
                        force.preload_effect = stage_effects["preload"]
                        force.thermal_effect = stage_effects["thermal"]
                        force.gap_effect = stage_effects["gap"]
                        force.eccentricity_effect = stage_effects["eccentricityMoment"]
                        force.effective_axial_force = stage_effects["effectiveStandard"]
                        force.construction_effect_note = stage_effects["note"]
            support.preload_stage_id = support.preload_stage_id or support.installation_stage_id or "auto-preload-after-installation"
            support.removal_stage_id = support.removal_stage_id or "auto-remove-after-basement-slab-strength"
            support.lifecycle_note = (
                f"{support.code} 采用安装后预加轴力、开挖阶段保持、底板/楼板形成后按换撑路径拆除的生命周期模型；"
                "当前为方案级时序，需施工组织和监测反馈复核。"
            )
            support.preload_protocol_status = "warning"
            support_checks.append({
                "ruleId": "JGJ120-SUPPORT-CONSTRUCTION-EFFECTS-SUBSET",
                "objectId": support.id,
                "objectType": "SupportElement",
                "status": "warning",
                "calculatedValue": support.design_axial_force,
                "limitValue": max(level_forces) if level_forces else support.design_axial_force,
                "unit": "kN",
                "message": "支撑设计轴力已考虑预加轴力、温度、节点间隙闭合和施工偏心的快速筛查效应；正式工程需按施工方案和监测数据复核。",
                "clauseReference": "JGJ120 internal support construction-stage effects screening; final protocol to verify",
                "formula": "N_eff = N_wale + 0.5*N_preload + N_temperature + N_gap; M_e = N*e0",
            })
            support_checks.append({
                "ruleId": "JGJ120-SUPPORT-LIFECYCLE-PATH-SUBSET",
                "objectId": support.id,
                "objectType": "SupportElement",
                "status": "warning",
                "calculatedValue": support.design_axial_force,
                "limitValue": support.design_axial_force,
                "unit": "kN",
                "message": support.lifecycle_note,
                "clauseReference": "internal support installation/preload/removal sequence screening; final construction method statement to verify",
                "formula": "install -> preload -> staged excavation -> replacement slab -> remove support",
            })
        support.reinforcement = support_reinforcement(support.section.width, support.section.height, support.design_axial_force, support.material.grade if support.material.name == "Concrete" else "C35")
        if support.section_type == "rc_rectangular":
            rebar = next((r for r in support.reinforcement if r.bar_type == "longitudinal"), None)
            rc_check = check_rc_rectangular_axial_capacity(
                (support.design_axial_force or 0.0),
                support.section.width or 0.8,
                support.section.height or 0.8,
                support.material.grade,
                rebar.grade if rebar else "HRB400",
                rebar.diameter if rebar else 25,
                rebar.count if rebar and rebar.count else 8,
            )
            support_checks.append({
                "ruleId": "GB50010-RC-SUPPORT-AXIAL-SUBSET",
                "objectId": support.id,
                "objectType": "SupportElement",
                "status": rc_check["status"],
                "calculatedValue": rc_check["axialDesign"],
                "limitValue": rc_check["capacity"],
                "unit": "kN",
                "message": "混凝土支撑轴压承载力子集筛查；长细比、节点、偏心和施工阶段需复核。",
                "clauseReference": "GB 50010 axial compression subset; final clause applicability to verify",
                "formula": "N <= phi*(fc*Ac + fy*As)",
            })
        elif support.section_type == "steel_pipe":
            support_checks.append(_check_to_dict(check_steel_pipe_support_axial_capacity(support.id, support.design_axial_force or 0.0, support.section.diameter or 0.609, support.section.wall_thickness or 0.016, _support_length(support), gamma0=1.0, force_factor=1.0)))

    max_support_force = max(max_support_force, *(s.design_axial_force or 0.0 for s in project.retaining_system.supports), 0.0)

    if project.retaining_system.supports:
        support_checks.extend([_check_to_dict(c) for c in check_internal_support_layout(
            project.retaining_system.supports,
            excavation_top_elevation=top,
            excavation_bottom_elevation=bottom,
            object_id=project.retaining_system.id,
        )])
    supports_by_code = {s.code: s for s in project.retaining_system.supports}
    total_support_design_force = sum((s.design_axial_force or 0.0) for s in project.retaining_system.supports)
    column_count = max(len(project.retaining_system.columns), 1)
    for col in project.retaining_system.columns:
        linked_support_force = sum((supports_by_code[code].design_axial_force or 0.0) for code in getattr(col, "support_codes", []) if code in supports_by_code)
        if linked_support_force > 0:
            carried_force = linked_support_force * 0.03
        else:
            carried_force = total_support_design_force * 0.03 / column_count
        foundation = design_column_pile(col.code, carried_force, excavation_bottom_elevation=bottom)
        col.foundation_design = foundation
        pile_check = check_column_pile_capacity(col.id, foundation)
        support_checks.append(pile_check)
        # Keep the old spread-footing helper available for small shallow pits, but the normal temporary column path is now pile-based.
        if foundation.foundation_type != "column_pile":
            spread = design_column_foundation(col.code, carried_force)
            bearing_check = _check_to_dict(
                check_foundation_bearing_pressure(
                    object_id=col.id,
                    vertical_force_kN=spread.vertical_force,
                    foundation_self_weight_kN=spread.foundation_self_weight,
                    area_m2=spread.area,
                    fa_kpa=spread.fa,
                    pkmax_kpa=spread.max_pressure,
                )
            )
            bearing_check["foundationCode"] = spread.code
            bearing_check["foundationWidth"] = spread.width
            bearing_check["foundationLength"] = spread.length
            bearing_check["foundationThickness"] = spread.thickness
            bearing_check["foundationArea"] = spread.area
            bearing_check["foundationSelfWeight"] = spread.foundation_self_weight
            bearing_check["maxPressure"] = spread.max_pressure
            bearing_check["designNote"] = spread.design_note
            support_checks.append(bearing_check)
    if getattr(project.retaining_system, "support_nodes", None):
        node_checks = update_support_node_design(project.retaining_system.support_nodes, project.retaining_system.supports)
        support_checks.extend(node_checks)
    support_deep_design = evaluate_support_deep_design(project, project.retaining_system, include_members=False)
    deep_metrics = dict(support_deep_design.get("metrics") or {})
    support_checks.append({
        "ruleId": "PITGUARD-SUPPORT-DEEP-DESIGN-STABILITY",
        "objectId": project.retaining_system.id,
        "objectType": "RetainingSystem",
        "status": "pass" if support_deep_design.get("hardPass") else "fail",
        "calculatedValue": deep_metrics.get("maximumInteractionUtilization"),
        "limitValue": 1.0,
        "unit": "utilization",
        "message": support_deep_design.get("summary"),
        "clauseReference": "JGJ120 internal-support load path and construction-stage design; GB 50017/GB 50010 member stability subset; project-specific applicability to verify",
        "formula": "N_eff=N+0.5N_pre+N_T+N_gap; eta=N_eff/N_b,Rd+M_e/M_Rd",
    })
    if int(deep_metrics.get("supportNodeUncheckedCount", 0) or 0):
        support_checks.append({
            "ruleId": "PITGUARD-SUPPORT-NODE-DETAILING-READINESS",
            "objectId": project.retaining_system.id,
            "objectType": "RetainingSystem",
            "status": "warning",
            "calculatedValue": deep_metrics.get("supportNodeUncheckedCount"),
            "limitValue": 0,
            "unit": "node",
            "message": "支撑—围檩节点仍有未闭环项；正式成果需完成承压板、节点区、加劲肋、锚固和局部配筋设计。",
            "clauseReference": "temporary bracing connection detailing and local load-transfer review",
        })
    wale_results_all = [wale for sr in stage_results for wale in getattr(sr, "wale_beam_results", [])]
    support_checks.extend(_design_wale_beams(project, wale_results_all, gamma0))
    if support_checks and stage_results:
        stage_results[-1].checks.extend(support_checks)
        global_checks.extend(support_checks)

    for wall in project.retaining_system.diaphragm_walls:
        env = segment_wall_envelopes.get(wall.segment_id, {"moment": 0.0, "shear": 0.0, "displacement": 0.0})
        design_env = segment_wall_design.get(wall.segment_id, {"moment": env["moment"] * gamma0 * LOAD_FACTOR_RETAINING, "shear": env["shear"] * gamma0 * LOAD_FACTOR_RETAINING})
        flex = design_rectangular_flexural_reinforcement(design_env["moment"], wall.thickness, wall.concrete_grade, wall.rebar_grade)
        shear = check_rectangular_shear_capacity(design_env["shear"], wall.thickness, wall.concrete_grade)
        wall.reinforcement = diaphragm_wall_reinforcement(wall.thickness, design_env["moment"], wall.concrete_grade, wall.rebar_grade)
        provided = flex["barArrangement"]["providedAs"]
        required = flex["asRequired"]
        moment_capacity = rectangular_flexural_capacity_knm_per_m(provided, wall.thickness, wall.concrete_grade, wall.rebar_grade)
        status = "pass" if flex["status"] == "pass" and shear["status"] == "pass" and provided >= required else "warning"
        wall.design_results = WallDesignResult(
            max_moment=round(env["moment"], 3),
            max_shear=round(env["shear"], 3),
            max_displacement=round(env["displacement"], 3),
            max_moment_design=round(design_env["moment"], 3),
            max_shear_design=round(design_env["shear"], 3),
            required_reinforcement_area=round(required, 2),
            provided_reinforcement_area=round(provided, 2),
            moment_capacity=moment_capacity,
            shear_capacity=shear.get("concreteShearCapacity"),
            rebar_diameter=flex["barArrangement"].get("diameter"),
            rebar_spacing=flex["barArrangement"].get("spacing"),
            governing_rule_ids=[
                "JGJ120-2012-3.4-RANKINE-PRESSURE",
                "JGJ120-2012-4.1-ELASTIC-SUPPORT-METHOD",
                "JGJ120-2012-4.5-DIAPHRAGM-WALL",
                "GB50010-FLEXURE-SUBSET",
                "GB50010-SHEAR-SUBSET",
                "GBT50010-2024-SERVICEABILITY-CRACK-SCREEN",
                "GBT50010-2024-REBAR-ANCHORAGE-LAP-SCREEN",
            ],
            formula_trace=[
                "Ka=tan^2(45deg-phi/2); Kp=tan^2(45deg+phi/2); u=gamma_w*h_w",
                "p_a=max(0, sigma_v_eff*Ka-2*c*sqrt(Ka))+u; p_p=max(0, sigma_v_eff*Kp+2*c*sqrt(Kp))+u",
                "EI*y''''+k_s*y+sum(k_support*y*delta)=q(z) finite-difference screening",
                "M_design=gamma0*1.25*M_standard; V_design=gamma0*1.25*V_standard",
                "alpha1*fc*b*x=fy*As; M<=alpha1*fc*b*x*(h0-x/2)",
            ],
            check_status=status,
            method="JGJ120 lateral pressure + staged elastic-foundation beam + GB50010 RC section subset",
            notes=[
                "已由 JGJ120 土压力、水压力和弹性地基梁子集生成内力包络，并由 GB50010 子集生成配筋建议。",
                "本版本已增加裂缝、锚固/搭接、内支撑布置、整体稳定圆弧搜索、承压水和基础承载力筛查；正式施工图仍需工程师签审。",
            ],
        )

    global_checks = _consolidate_global_checks(project, global_checks)
    for stage_result in stage_results:
        stage_result.checks = [_normalize_check_dict(c) for c in stage_result.checks]
        stage_result.stability_checks = [_normalize_check_dict(c) for c in stage_result.stability_checks]
        stage_result.rc_checks = [_normalize_check_dict(c) for c in stage_result.rc_checks]
    result_summary = _summary(global_checks)
    design_review = _design_review_summary(global_checks, stage_results)
    stability_package = build_reviewable_stability_package(project, stage_results, global_checks)
    try:
        drawing_sheets = generate_construction_detail_sheets(project, "exports/detail-sheets")
    except Exception as exc:
        drawing_sheets = []
        warnings.append(f"施工图详图 SVG 生成失败：{exc}")
    support_quality = evaluate_support_layout_quality(project)
    support_repair = project.retaining_system.support_layout_repair or support_repair
    candidate_full_calculations: list[dict[str, Any]] = []
    if include_candidate_comparison and support_repair and support_repair.candidates:
        candidate_full_calculations = _compare_top_support_candidates(project, support_repair, top_n=3)
    ifc_quality = evaluate_ifc_model_compatibility(project)
    # Also expose support-layout and IFC quality gates as traceable checks so the user can see why
    # the closed-loop gate is warning/fail instead of just a percentage.
    for issue in [*support_quality.issues, *ifc_quality.issues]:
        global_checks.append({
            "ruleId": f"QUALITY-{issue.category.upper()}",
            "objectId": issue.object_id or project.id,
            "objectType": issue.object_type or "QualityGate",
            "status": issue.severity,
            "calculatedValue": None,
            "limitValue": None,
            "unit": "-",
            "message": issue.message + ((" 建议：" + issue.recommendation) if issue.recommendation else ""),
            "clauseReference": "PitGuard V2.0.4 quality gate",
        })
    coverage_status = "pass" if geology_audit.get("designDomainCovered", False) else "fail"
    global_checks.append({
        "ruleId": "GB55017-2021-GEOLOGICAL-DESIGN-DOMAIN-COVERAGE",
        "objectId": project.id,
        "objectType": "GeologicalModel",
        "status": coverage_status,
        "calculatedValue": 1.0 if coverage_status == "pass" else 0.0,
        "limitValue": 1.0,
        "unit": "covered",
        "message": str(geology_audit.get("message") or "地质模型平面范围覆盖围护结构和施工影响区。"),
        "clauseReference": "GB 55017-2021 工程勘察通用规范：勘察成果应覆盖工程设计所需场地范围。",
    })
    extrapolation_status = str(geology_audit.get("extrapolationStatus") or "pass")
    global_checks.append({
        "ruleId": "GB55017-2021-GEOLOGICAL-EXTRAPOLATION-CONTROL",
        "objectId": project.id,
        "objectType": "GeologicalModel",
        "status": extrapolation_status,
        "calculatedValue": geology_audit.get("maximumExtrapolationDistanceM"),
        "limitValue": geology_audit.get("maximumAllowedExtrapolationDistanceM"),
        "unit": "m",
        "message": (
            "地质设计域已覆盖；外扩部分采用受控边界外推并保留低置信度标识。"
            if geology_audit.get("autoExtended")
            else "地质模型未使用平面外推。"
        ),
        "clauseReference": "GB 55017-2021 工程勘察通用规范：外推区域需明确资料依据、不确定性和补充勘察要求。",
    })
    global_checks = _consolidate_global_checks(project, global_checks)
    result_summary = _summary(global_checks)
    design_review = _design_review_summary(global_checks, stage_results)
    formal_gate_preview = type(
        "TempLatest",
        (),
        {
            "check_summary": result_summary,
            "stability_detailed_result": stability_package,
            "drawing_sheets": drawing_sheets,
            "report_diagram_data": {"checkSummary": result_summary},
            "support_topology_hash": _support_topology_hash(project),
            "design_iteration_summary": {
                "algorithmVersion": ALGORITHM_VERSION,
                "ruleSetVersion": RULE_SET_VERSION,
            },
            "governing_values": GoverningValues(
                max_total_pressure=round(max_pressure, 3),
                max_support_axial_force=round(max_support_force, 3),
                max_wall_moment=round(max_wall_moment, 3),
                max_wall_shear=round(max_wall_shear, 3),
                max_displacement=round(max_displacement, 3),
            ),
            "support_layout_repair": support_repair,
        },
    )()
    formal_gate = build_formal_report_gate(project, support_quality, ifc_quality, latest_result=formal_gate_preview)
    calculation_diagnostics = build_calculation_diagnostics(
        project,
        case,
        stage_results,
        global_checks,
        topology_preflight=topology_preflight,
        support_case_sync=support_case_sync,
        wall_embedment_preflight=wall_embedment_preflight,
        governing_values={
            "maxDisplacement": round(max_displacement, 3),
            "maxWallMoment": round(max_wall_moment, 3),
            "maxWallShear": round(max_wall_shear, 3),
            "maxSupportAxialForce": round(max_support_force, 3),
        },
    )
    result = CalculationResult(
        project_id=project.id,
        case_id=case.id,
        support_topology_hash=_support_topology_hash(project),
        stage_results=stage_results,
        governing_values=GoverningValues(
            max_total_pressure=round(max_pressure, 3),
            max_support_axial_force=round(max_support_force, 3),
            max_wall_moment=round(max_wall_moment, 3),
            max_wall_shear=round(max_wall_shear, 3),
            max_displacement=round(max_displacement, 3),
            governing_check_status=_governing_status(global_checks),
            embedment_safety_factor_min=_min_value(global_checks, "EMBEDMENT"),
            heave_safety_factor_min=_min_value(global_checks, "HEAVE"),
            seepage_safety_factor_min=None,
            seepage_risk_index_max=_max_value(global_checks, "SEEPAGE"),
            strength_check_status=design_review.strength_status,
            stiffness_check_status=design_review.stiffness_status,
            stability_check_status=design_review.stability_status,
        ),
        warnings=warnings,
        checks=global_checks,
        check_summary=result_summary,
        design_iteration_summary={
            "version": SOFTWARE_VERSION,
            "algorithmVersion": ALGORITHM_VERSION,
            "ruleSetVersion": RULE_SET_VERSION,
            "exportSchemaVersion": EXPORT_SCHEMA_VERSION,
            "p0WaleEngineering": True,
            "p1SupportLifecycle": True,
            "p2CadEngineeringDrawing": True,
            "p3ReviewViewer": True,
            "p4ReportDiagramData": True,
            "p5CoreCalculationInterfaces": True,
            "p6GlobalCoupledMatrix": True,
            "p7ReportCharts": True,
            "p8CadGeometryKernel": True,
            "p9GroundwaterStabilitySpecials": True,
            "p10DesignReviewSummary": True,
            "p11SpatialFrameKernel": True,
            "p12ReviewableStabilityPackage": True,
            "p13ConstructionDrawingOutput": True,
            "p14DetailedIfcOutput": True,
            "p15SupportLayoutQualityGate": True,
            "p16IfcCompatibilityPrecheck": True,
            "p17FormalReportGate": True,
            "p18SupportLayoutAutoRepair": True,
            "p19DualModeIfcExport": True,
            "p20SupportQualityPlanFigureInReport": True,
            "p21CandidateAbcFullCalculationComparison": bool(candidate_full_calculations),
            "p22ConcavePitTopologyRecovery": True,
            "p23CalculationRootCauseDiagnostics": True,
            "p24StrengthDrivenTopologyDesign": True,
            "p25WaleSupportBayHardGate": True,
            "p26CornerFanAutoRepair": True,
            "p27ReplacementStageLoadPathPartition": True,
            "p28ClosedPerimeterWaleEnvelope": True,
            "p29BoundedCostMatrixDiagnostics": True,
            "p30SharedGridNodeRecovery": True,
            "p31GeneralPolygonPrincipalAxisLayout": True,
            "p32CandidateStateAndStageSynchronization": True,
            "p33GeologicalDesignDomainCoverage": True,
            "p34WallEmbedmentStrengthDesign": True,
            "p35SupportDeepDesignScreening": True,
            "supportDeepDesign": support_deep_design,
            "autoStrengthDesignEnabled": strength_auto_enabled,
            "maxDesignIterations": int(getattr(project.design_settings, "max_design_iterations", 3) or 3),
            "topologyPreflight": topology_preflight,
            "wallEmbedmentPreflight": wall_embedment_preflight,
            "geometryConsistency": geometry_consistency_summary(project),
            "calculationDiagnostics": calculation_diagnostics,
            "supportTopologySynchronization": support_case_sync,
            "geologyCoverage": geology_audit,
            "supportRoleCount": {role: sum(1 for item in project.retaining_system.supports if item.support_role == role) for role in sorted({item.support_role for item in project.retaining_system.supports})},
            "remainingBoundary": "V3.14 已完成支撑拓扑强度前置、围檩支点间距硬门禁、拆换撑压力分带修正、闭合围檩多跨包络和构件强度闭环；生产级仍需经验证的三维非线性 FEM、节点专项分析、企业图纸标准及逐条规范适用性确认。",
        },
        optimization_actions=[
            {
                "target": "wall_embedment",
                "action": "common_wall_toe_stability_design",
                "count": len(project.retaining_system.diaphragm_walls) if wall_embedment_preflight.get("changed") else 0,
                "beforeBottomElevationM": wall_embedment_preflight.get("beforeBottomElevationM"),
                "afterBottomElevationM": wall_embedment_preflight.get("afterBottomElevationM"),
                "beforeMinimumFactor": wall_embedment_preflight.get("beforeMinimumFactor"),
                "afterMinimumFactor": wall_embedment_preflight.get("afterMinimumFactor"),
            },
            {"target": "support_topology", "action": "strength_first_wale_bay_and_corner_fan_repair", "count": int(topology_preflight.get("addedSupportCount") or 0)},
            {"target": "replacement_load_path", "action": "retain_transferred_slab_waler_levels_in_vertical_tributary_partition", "count": len([s for s in case.stages if s.transferred_support_levels])},
            {"target": "wale_beam_section", "action": "auto_size_width_height", "count": len([b for b in project.retaining_system.wale_beams if b.design_result])},
            {"target": "support_lifecycle", "action": "preload_temperature_gap_eccentricity_screening", "count": len(project.retaining_system.supports)},
            {"target": "support_deep_design", "action": "stability_eccentricity_node_redundancy_screening", "count": len(project.retaining_system.supports)},
            {"target": "temporary_column", "action": "pile_foundation_screening", "count": len(project.retaining_system.columns)},
        ],
        report_diagram_data={
            "globalCoupledSystems": [
                {
                    "stageId": sr.stage_id,
                    "segmentId": sr.segment_id,
                    "matrixSize": sr.global_coupled_result.matrix_size,
                    "conditionNumber": sr.global_coupled_result.condition_number,
                    "equilibriumDiagnostics": sr.global_coupled_result.equilibrium_diagnostics,
                    "maxWallDisplacement": sr.global_coupled_result.max_wall_displacement,
                    "maxSupportAxialForce": sr.global_coupled_result.max_support_axial_force,
                    "fallback": sr.global_coupled_result.fallback,
                    "modelDimension": sr.global_coupled_result.model_dimension,
                }
                for sr in stage_results
                if sr.global_coupled_result
            ][:60],
            "supportAxialSummary": [
                {"stageId": sr.stage_id, "segmentId": sr.segment_id, "supportId": f.support_id, "faceCode": f.face_code, "axialForceDesign": f.axial_force_design, "distributionMethod": f.distribution_method}
                for sr in stage_results
                for f in sr.support_forces
            ][:120],
            "checkSummary": result_summary,
            "designReviewSummary": design_review.model_dump(mode="json", by_alias=True),
            "reviewableStabilityPackage": stability_package.model_dump(mode="json", by_alias=True),
            "drawingSheets": [sheet.model_dump(mode="json", by_alias=True) for sheet in drawing_sheets],
            "supportLayoutQuality": support_quality.model_dump(mode="json", by_alias=True),
            "ifcCompatibility": ifc_quality.model_dump(mode="json", by_alias=True),
            "formalReportGate": formal_gate.model_dump(mode="json", by_alias=True),
            "supportLayoutRepair": support_repair.model_dump(mode="json", by_alias=True) if support_repair else None,
            "supportDeepDesign": support_deep_design,
            "candidateFullCalculationComparison": candidate_full_calculations,
            "waleEnvelopes": [
                b.design_result.envelope.model_dump(mode="json", by_alias=True)
                for b in project.retaining_system.wale_beams
                if b.design_result and b.design_result.envelope
            ][:20],
            # Full wall samples already live in stageResults.  Keep this key null for
            # backward-compatible clients while avoiding multi-megabyte duplication.
            "wallForceSamples": None,
            "geometryConsistency": geometry_consistency_summary(project),
            "calculationDiagnostics": calculation_diagnostics,
            "geologyCoverage": geology_audit,
        },
        design_review_summary=design_review,
        stability_detailed_result=stability_package,
        drawing_sheets=drawing_sheets,
        support_layout_quality=support_quality,
        support_layout_repair=support_repair,
        ifc_compatibility=ifc_quality,
        formal_report_gate=formal_gate,
        standards=[
            "JGJ120-2012 建筑基坑支护技术规程：水平荷载、土/水压力、弹性支点法、嵌固/抗隆起/渗透稳定、整体稳定圆弧搜索、内支撑布置筛查子集",
            "GB50010-2010(2024局部修订) 混凝土结构设计规范：矩形截面受弯、受剪、轴压、裂缝、锚固搭接和最小配筋率筛查子集",
            "GB55008-2021 混凝土结构通用规范：混凝土构件强制性约束提示和复核入口",
            "GB55003-2021 建筑与市政地基基础通用规范：地基、基坑、地下水控制通用要求提示子集",
            "GB50009-2012 建筑结构荷载规范：作用组合参数记录子集",
            "GB50007-2011 建筑地基基础设计规范：基坑工程与基础承载力复核提示子集",
            "GB50017-2017 钢结构设计标准：钢管支撑轴压强度/稳定筛查子集",
            f"V{SOFTWARE_VERSION} 质量闸门：统一几何哈希、逐墙面支撑拓扑、候选并发隔离、结果载荷去重、分区配筋和按需读取。",
        ],
        professional_review_required=True,
    )
    result = apply_calculation_assurance(
        project,
        case,
        result,
        input_audit=calculation_input_audit,
        contract=calculation_contract,
    )
    result.formal_report_gate = build_formal_report_gate(
        project, result.support_layout_quality, result.ifc_compatibility, latest_result=result
    )
    result.report_diagram_data = dict(result.report_diagram_data or {})
    result.report_diagram_data["formalReportGate"] = result.formal_report_gate.model_dump(mode="json", by_alias=True)
    return result
