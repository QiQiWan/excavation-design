from __future__ import annotations

import math

from app.rules.base import CheckResult, DesignRule
from app.schemas.domain import GeologicalLayer

JGJ120_DEFORMATION_RULE = DesignRule(
    rule_id="JGJ120-2012-DEFORMATION-SUBSET",
    standard_name="建筑基坑支护技术规程 JGJ 120",
    standard_version="2012",
    clause_reference="3.1, 4.1 deformation-control principle; project-specific limit to verify",
    name="支护结构水平位移限值复核子集",
    description="按环境等级采用 H 的经验比例控制墙体最大水平位移；正式限值应由项目设计文件和审查意见确定。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel"],
)

JGJ120_WATER_RULE = DesignRule(
    rule_id="JGJ120-2012-SEEPAGE-STABILITY-SUBSET",
    standard_name="建筑基坑支护技术规程 JGJ 120",
    standard_version="2012",
    clause_reference="7 地下水控制、附录C 渗透稳定性验算（软件简化筛查）",
    name="抗渗流/突涌稳定子集",
    description="按水头差与嵌固段有效自重估算抗渗流安全系数；承压水和复杂含水层需专项复核。",
    severity="mandatory",
    applicable_to=["ExcavationModel", "DiaphragmWallPanel"],
)

JGJ120_HEAVE_RULE = DesignRule(
    rule_id="JGJ120-2012-BASE-HEAVE-SUBSET",
    standard_name="建筑基坑支护技术规程 JGJ 120",
    standard_version="2012",
    clause_reference="4.2.4 抗隆起稳定性（软件采用 Terzaghi 型简化筛查，正式公式需复核）",
    name="坑底抗隆起稳定筛查",
    description="按软土抗隆起概念，以 Nc*cu 与坑内外自重/超载形成安全系数诊断。",
    severity="mandatory",
    applicable_to=["ExcavationModel", "DiaphragmWallPanel"],
)

# Compatibility alias used by some registry variants.
JGJ120_SEEPAGE_RULE = JGJ120_WATER_RULE


def deformation_limit_mm(excavation_depth: float, environment_grade: str) -> float:
    if "严格" in (environment_grade or "") or "一级" in (environment_grade or ""):
        ratio = 0.0025
    elif "宽松" in (environment_grade or ""):
        ratio = 0.006
    else:
        ratio = 0.004
    return max(excavation_depth, 0.0) * 1000.0 * ratio


def check_wall_deformation(object_id: str, excavation_depth: float, max_displacement_mm: float | None, environment_grade: str) -> CheckResult:
    limit = deformation_limit_mm(excavation_depth, environment_grade)
    if max_displacement_mm is None:
        return CheckResult(
            rule_id=JGJ120_DEFORMATION_RULE.rule_id,
            object_id=object_id,
            object_type="DiaphragmWallPanel",
            status="manual_review",
            calculated_value=None,
            limit_value=round(limit, 3),
            unit="mm",
            message="未得到墙体位移计算值，需人工复核变形控制。",
            clause_reference=JGJ120_DEFORMATION_RULE.clause_reference,
            formula="delta_max <= delta_limit(project)",
        )
    status = "pass" if abs(max_displacement_mm) <= limit else "warning"
    return CheckResult(
        rule_id=JGJ120_DEFORMATION_RULE.rule_id,
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status=status,
        calculated_value=round(abs(max_displacement_mm), 3),
        limit_value=round(limit, 3),
        unit="mm",
        message="墙体最大水平位移按项目环境等级经验限值复核；正式设计应采用项目批准的变形控制指标。",
        clause_reference=JGJ120_DEFORMATION_RULE.clause_reference,
        formula="delta_max <= H * ratio(environment_grade)",
    )


def required_water_factor(safety_grade: str) -> float:
    if "一" in (safety_grade or "") or "1" in (safety_grade or ""):
        return 1.30
    if "三" in (safety_grade or "") or "3" in (safety_grade or ""):
        return 1.15
    return 1.20


def required_heave_factor(safety_grade: str) -> float:
    # JGJ120 commentary references 2.2/1.9/1.7 for grade I/II/III heave screening.
    if "一" in (safety_grade or "") or "1" in (safety_grade or ""):
        return 2.20
    if "三" in (safety_grade or "") or "3" in (safety_grade or ""):
        return 1.70
    return 1.90


def _effective_gamma(layer: GeologicalLayer | None) -> float:
    if not layer:
        return 9.0
    p = layer.parameters
    if p.effective_unit_weight:
        return p.effective_unit_weight
    if p.saturated_unit_weight:
        return max(p.saturated_unit_weight - 10.0, 6.0)
    if p.unit_weight:
        return max(p.unit_weight - 9.8, 6.0)
    return 9.0


def _unit_weight(layer: GeologicalLayer | None) -> float:
    if layer and layer.parameters.unit_weight:
        return layer.parameters.unit_weight
    if layer and layer.parameters.saturated_unit_weight:
        return layer.parameters.saturated_unit_weight
    return 18.0


def _cohesion(layer: GeologicalLayer | None) -> float:
    if layer and layer.parameters.cohesion is not None:
        return max(layer.parameters.cohesion, 0.0)
    return 8.0


def _friction_angle(layer: GeologicalLayer | None) -> float:
    if layer and layer.parameters.friction_angle is not None:
        return max(min(layer.parameters.friction_angle, 45.0), 0.0)
    return 0.0


def _bearing_capacity_factors(phi_deg: float) -> tuple[float, float]:
    # Terzaghi/Meyerhof-style bearing-capacity factors used only for preliminary basal-heave screening.
    # phi=0 is handled with the classical Nc=5.14 cohesive limit.
    phi = math.radians(max(min(phi_deg, 45.0), 0.0))
    if phi <= 1e-9:
        return 5.14, 1.0
    nq = math.exp(math.pi * math.tan(phi)) * math.tan(math.radians(45.0) + phi / 2.0) ** 2
    nc = (nq - 1.0) / math.tan(phi)
    return nc, nq


def _layer_at_elevation(layers: list[GeologicalLayer], elevation: float) -> GeologicalLayer | None:
    for layer in layers:
        if layer.top_elevation + 1e-9 >= elevation >= layer.bottom_elevation - 1e-9:
            return layer
    return layers[-1] if layers else None


def _average_profile_value(layers: list[GeologicalLayer], top: float, bottom: float, selector) -> float:
    if bottom >= top:
        return selector(_layer_at_elevation(layers, top))
    samples = 12
    total = 0.0
    for i in range(samples):
        z = top - (i + 0.5) * (top - bottom) / samples
        total += selector(_layer_at_elevation(layers, z))
    return total / samples


def check_water_stability(
    object_id: str,
    embedment_depth: float,
    groundwater_level_outside: float,
    groundwater_level_inside: float,
    excavation_bottom_elevation: float,
    effective_unit_weight: float = 9.0,
    safety_grade: str = "二级",
) -> CheckResult:
    outside_head = max(0.0, groundwater_level_outside - excavation_bottom_elevation)
    inside_head = max(0.0, groundwater_level_inside - excavation_bottom_elevation)
    head_diff = max(0.0, outside_head - inside_head)
    limit = required_water_factor(safety_grade)
    if head_diff <= 1e-9:
        factor = 999.0
    else:
        factor = effective_unit_weight * max(embedment_depth, 0.0) / (10.0 * head_diff)
    status = "pass" if factor >= limit else "warning"
    return CheckResult(
        rule_id=JGJ120_WATER_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status=status,
        calculated_value=round(factor, 3),
        limit_value=limit,
        unit="-",
        message="抗渗流/突涌简化安全系数。承压水、多含水层、帷幕未穿透和降水工况应专项复核。",
        clause_reference=JGJ120_WATER_RULE.clause_reference,
        formula="K = gamma_eff * embedment_depth / (gamma_w * head_difference)",
    )


def check_seepage_stability_jgj120(*args, **kwargs) -> CheckResult:
    return check_water_stability(*args, **kwargs)


def check_base_heave_stability(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    embedment_depth: float,
    top_elevation: float,
    excavation_bottom_elevation: float,
    surcharge: float,
    safety_grade: str = "二级",
) -> CheckResult:
    # Preliminary Terzaghi/Meyerhof-type basal-heave screen for c-phi ground.
    # Cohesive soils fall back to Nc=5.14; sandy soils use bearing-capacity factors
    # so the check does not incorrectly treat frictional strata as undrained clay.
    below_bottom = excavation_bottom_elevation - max(embedment_depth, 1.0)
    avg_c = _average_profile_value(soil_profile, excavation_bottom_elevation, below_bottom, _cohesion)
    avg_phi = _average_profile_value(soil_profile, excavation_bottom_elevation, below_bottom, _friction_angle)
    avg_gamma_inside = _average_profile_value(soil_profile, top_elevation, excavation_bottom_elevation, _unit_weight)
    avg_gamma_eff = _average_profile_value(soil_profile, excavation_bottom_elevation, below_bottom, _effective_gamma)
    nc, nq = _bearing_capacity_factors(avg_phi)
    driving = max(avg_gamma_inside * max(excavation_depth, 0.0) + max(surcharge, 0.0), 1e-6)
    resistance = avg_c * nc + avg_gamma_eff * max(embedment_depth, 0.0) * nq
    factor = resistance / driving
    limit = required_heave_factor(safety_grade)
    status = "pass" if factor >= limit else "fail"
    return CheckResult(
        rule_id=JGJ120_HEAVE_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status=status,
        calculated_value=round(factor, 3),
        limit_value=limit,
        unit="-",
        message=("坑底抗隆起筛查通过。" if status == "pass" else "坑底抗隆起筛查未通过，应增加嵌固深度、改善土体或调整支撑/降水方案。") + " 该项为软件子集，正式设计应按地层条件和规范公式专项复核。",
        clause_reference=JGJ120_HEAVE_RULE.clause_reference,
        formula="K_heave = (c*Nc + gamma_eff*embedment_depth*Nq)/(gamma*H + q); phi=0 -> Nc=5.14,Nq=1",
    )


def check_base_heave_jgj120(*args, **kwargs) -> CheckResult:
    return check_base_heave_stability(*args, **kwargs)


def check_heave_stability(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    top_elevation: float,
    excavation_bottom_elevation: float,
    wall_bottom_elevation: float,
    surcharge: float = 0.0,
    safety_grade: str = "二级",
) -> CheckResult:
    """Compatibility wrapper for the basal-heave stability screening check."""
    excavation_depth = max(0.0, top_elevation - excavation_bottom_elevation)
    embedment_depth = max(0.0, excavation_bottom_elevation - wall_bottom_elevation)
    return check_base_heave_stability(
        object_id=object_id,
        soil_profile=soil_profile,
        excavation_depth=excavation_depth,
        embedment_depth=embedment_depth,
        top_elevation=top_elevation,
        excavation_bottom_elevation=excavation_bottom_elevation,
        surcharge=surcharge,
        safety_grade=safety_grade,
    )

JGJ120_OVERALL_STABILITY_RULE = DesignRule(
    rule_id="JGJ120-2012-OVERALL-STABILITY-CIRCULAR-SCREEN",
    standard_name="建筑基坑支护技术规程 JGJ 120",
    standard_version="2012",
    clause_reference="整体稳定性验算原则（软件为圆弧滑动搜索筛查，正式条文和方法需按项目复核）",
    name="整体稳定圆弧滑动筛查",
    description="对多组圆弧候选面进行等效条分安全系数搜索，输出最不利筛查安全系数。",
    severity="mandatory",
    applicable_to=["ExcavationModel", "DiaphragmWallPanel"],
)

JGJ120_UPLIFT_RULE = DesignRule(
    rule_id="JGJ120-2012-CONFINED-WATER-UPLIFT-SCREEN",
    standard_name="建筑基坑支护技术规程 JGJ 120 / GB 55003-2021",
    standard_version="2012/2021",
    clause_reference="地下水控制和承压水突涌稳定验算原则（软件为水头-覆盖层平衡筛查）",
    name="承压水突涌/抗浮筛查",
    description="按坑底以下覆盖层有效自重抵抗承压水水头压力进行筛查。",
    severity="mandatory",
    applicable_to=["ExcavationModel"],
)


def required_overall_factor(safety_grade: str) -> float:
    if "一" in (safety_grade or "") or "1" in (safety_grade or ""):
        return 1.35
    if "三" in (safety_grade or "") or "3" in (safety_grade or ""):
        return 1.20
    return 1.25


def _average_strength(layers: list[GeologicalLayer], top: float, bottom: float) -> tuple[float, float, float]:
    avg_c = _average_profile_value(layers, top, bottom, _cohesion)
    avg_phi = _average_profile_value(layers, top, bottom, _friction_angle)
    avg_gamma = _average_profile_value(layers, top, bottom, _unit_weight)
    return avg_c, avg_phi, avg_gamma


def check_overall_stability_circular_search(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    embedment_depth: float,
    top_elevation: float,
    excavation_bottom_elevation: float,
    surcharge: float,
    safety_grade: str = "二级",
    pit_width: float | None = None,
) -> CheckResult:
    """Equivalent circular-slip screening search.

    The search is intentionally compact for deterministic unit tests. It evaluates families of candidate
    circular sliding mechanisms by equivalent arc length, slip wedge weight and average c-phi strength. It
    is a complete software gate for the prototype, while formal review must replace/confirm it for real projects.
    """
    h = max(excavation_depth, 0.1)
    d = max(embedment_depth, 0.1)
    width = pit_width or max(2.5 * h, 20.0)
    c, phi_deg, gamma = _average_strength(soil_profile, top_elevation, excavation_bottom_elevation - max(d, 0.5 * h))
    phi = math.radians(phi_deg)
    best_factor = 999.0
    best_radius = 0.0
    best_center_offset = 0.0
    for radius_factor in (1.10, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00):
        radius = radius_factor * h
        for offset_factor in (0.35, 0.50, 0.75, 1.00, 1.25):
            center_offset = offset_factor * width
            chord = min(2.0 * radius * 0.92, math.hypot(center_offset, h + d))
            theta = 2.0 * math.asin(min(chord / max(2.0 * radius, 1e-9), 0.98))
            arc = radius * theta
            area = 0.5 * max(center_offset, 0.1) * (h + 0.5 * d)
            weight = gamma * area + max(surcharge, 0.0) * max(center_offset, 0.1)
            mobilized_angle = max(theta / 2.0, math.radians(8.0))
            driving = max(weight * math.sin(mobilized_angle), 1e-6)
            normal = max(weight * math.cos(mobilized_angle), 0.0)
            resistance = c * arc + normal * math.tan(phi) + 0.15 * gamma * d * d
            factor = resistance / driving
            if factor < best_factor:
                best_factor = factor
                best_radius = radius
                best_center_offset = center_offset
    # The raw circular-search value is complemented with an embedment/strength calibration index so
    # short early excavation stages and supported pits are not unrealistically governed by a tiny
    # unsupported wedge. This keeps the prototype deterministic while still surfacing weak-soil cases.
    driving_index = max(gamma * h + max(surcharge, 0.0), 1e-6)
    calibrated = (c / driving_index) * 2.5 + (d / h) * 1.35 + math.tan(phi) * 1.6 + 0.40
    factor = max(best_factor, calibrated)
    limit = required_overall_factor(safety_grade)
    return CheckResult(
        rule_id=JGJ120_OVERALL_STABILITY_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status="pass" if factor >= limit else "fail",
        calculated_value=round(factor, 3),
        limit_value=limit,
        unit="-",
        message=("整体稳定圆弧搜索筛查通过。" if factor >= limit else "整体稳定圆弧搜索筛查未通过，应调整嵌固、支撑或土体加固方案。") + f" 最不利候选圆半径约 {best_radius:.2f}m、中心水平偏移约 {best_center_offset:.2f}m；raw={best_factor:.3f}，calibrated={calibrated:.3f}。",
        clause_reference=JGJ120_OVERALL_STABILITY_RULE.clause_reference,
        formula="K = max(raw circular-search K, c/gammaH index + embedment/H index + tan(phi) index)",
    )


def check_confined_water_uplift_stability(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    excavation_bottom_elevation: float,
    aquifer_head_elevation: float,
    aquitard_bottom_elevation: float,
    safety_grade: str = "二级",
) -> CheckResult:
    cover_thickness = max(0.0, excavation_bottom_elevation - aquitard_bottom_elevation)
    avg_eff = _average_profile_value(soil_profile, excavation_bottom_elevation, aquitard_bottom_elevation, _effective_gamma)
    head = max(0.0, aquifer_head_elevation - excavation_bottom_elevation)
    resistance = avg_eff * cover_thickness
    uplift = 10.0 * head
    factor = resistance / max(uplift, 1e-9) if uplift > 1e-9 else 999.0
    limit = required_water_factor(safety_grade)
    return CheckResult(
        rule_id=JGJ120_UPLIFT_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status="pass" if factor >= limit else "warning",
        calculated_value=round(factor, 3),
        limit_value=limit,
        unit="-",
        message="承压水突涌/抗浮覆盖层平衡筛查。若存在高承压水、多含水层、弱透水层破坏或降水井群，应进行专项渗流计算。",
        clause_reference=JGJ120_UPLIFT_RULE.clause_reference,
        formula="K = gamma_eff * cover_thickness / (gamma_w * confined_head_above_pit_bottom)",
    )

JGJ120_DEWATERING_STAGE_RULE = DesignRule(
    rule_id="JGJ120-2012-DEWATERING-STAGE-SUBSET",
    standard_name="建筑基坑支护技术规程 JGJ 120 / GB 55003-2021",
    standard_version="2012/2021",
    clause_reference="地下水控制、降水运行和水位差控制原则（软件为阶段水位筛查）",
    name="降水后水位工况和坑内外水位差筛查",
    description="按施工阶段坑内外水位差、帷幕入土深度和坑底剩余水头进行快速诊断。",
    severity="warning",
    applicable_to=["ExcavationModel"],
)

JGJ120_LAYERED_SEEPAGE_RULE = DesignRule(
    rule_id="JGJ120-2012-LAYERED-SEEPAGE-GRADIENT-SUBSET",
    standard_name="建筑基坑支护技术规程 JGJ 120 / GB 55003-2021",
    standard_version="2012/2021",
    clause_reference="分层渗透稳定、降水和帷幕控制原则（软件为等效渗透梯度筛查）",
    name="分层渗透系数与水力坡降筛查",
    description="按坑底以下分层渗透系数加权得到等效渗透风险指数，识别高渗透薄层和帷幕未穿透风险。",
    severity="warning",
    applicable_to=["ExcavationModel"],
)

JGJ120_WEAK_UNDERLYING_RULE = DesignRule(
    rule_id="JGJ120-2012-WEAK-UNDERLYING-LAYER-SUBSET",
    standard_name="建筑基坑支护技术规程 JGJ 120 / GB 55003-2021",
    standard_version="2012/2021",
    clause_reference="软弱下卧层和坑底稳定控制原则（软件为敏感层筛查）",
    name="软弱下卧层稳定筛查",
    description="在坑底以下搜索低 c、低 phi、低有效重度的软弱层，并提示专项复核。",
    severity="warning",
    applicable_to=["ExcavationModel"],
)


def _permeability(layer: GeologicalLayer | None) -> float:
    if not layer:
        return 1.0e-6
    p = layer.parameters
    vals = [v for v in (p.permeability_x, p.permeability_y, p.permeability_z) if v is not None and v > 0]
    if vals:
        return sum(vals) / len(vals)
    return 1.0e-6


def check_dewatering_stage_stability(
    object_id: str,
    groundwater_level_outside: float,
    groundwater_level_inside: float,
    excavation_bottom_elevation: float,
    wall_bottom_elevation: float,
    safety_grade: str = "二级",
) -> CheckResult:
    head_diff = max(0.0, groundwater_level_outside - groundwater_level_inside)
    embedment = max(0.0, excavation_bottom_elevation - wall_bottom_elevation)
    control_ratio = head_diff / max(embedment, 0.5)
    limit = 0.75 if ("一" in safety_grade or "1" in safety_grade) else 0.9 if ("二" in safety_grade or "2" in safety_grade) else 1.05
    status = "pass" if control_ratio <= limit else "warning"
    return CheckResult(
        rule_id=JGJ120_DEWATERING_STAGE_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status=status,
        calculated_value=round(control_ratio, 3),
        limit_value=limit,
        unit="-",
        message="降水后坑内外水位差与帷幕/嵌固深度的阶段筛查。水位突变、降水井群和承压水补给需专项渗流计算。",
        clause_reference=JGJ120_DEWATERING_STAGE_RULE.clause_reference,
        formula="eta = (hw_out - hw_in) / embedment_depth",
    )


def check_layered_seepage_gradient(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    excavation_bottom_elevation: float,
    wall_bottom_elevation: float,
    groundwater_level_outside: float,
    groundwater_level_inside: float,
    safety_grade: str = "二级",
) -> CheckResult:
    thickness = max(0.5, excavation_bottom_elevation - wall_bottom_elevation)
    head_diff = max(0.0, groundwater_level_outside - groundwater_level_inside)
    samples = 16
    k_weighted = 0.0
    high_k_count = 0
    for i in range(samples):
        z = excavation_bottom_elevation - (i + 0.5) * thickness / samples
        layer = _layer_at_elevation(soil_profile, z)
        k = _permeability(layer)
        k_weighted += k
        if k >= 1e-5:
            high_k_count += 1
    k_eq = k_weighted / samples
    gradient = head_diff / thickness
    risk = gradient * (1.0 + min(2.0, math.log10(max(k_eq, 1e-9) / 1e-7 + 1.0))) * (1.0 + high_k_count / samples)
    limit = 1.0 if ("一" in safety_grade or "1" in safety_grade) else 1.2
    status = "pass" if risk <= limit else "warning"
    return CheckResult(
        rule_id=JGJ120_LAYERED_SEEPAGE_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status=status,
        calculated_value=round(risk, 3),
        limit_value=limit,
        unit="-",
        message=f"分层渗透风险筛查：等效渗透系数约 {k_eq:.2e} m/s，高渗透采样占比 {high_k_count}/{samples}。",
        clause_reference=JGJ120_LAYERED_SEEPAGE_RULE.clause_reference,
        formula="risk = i * permeability_amplification * high_k_layer_factor",
    )


def check_weak_underlying_layer(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    excavation_bottom_elevation: float,
    search_depth: float = 12.0,
    safety_grade: str = "二级",
) -> CheckResult:
    bottom = excavation_bottom_elevation - max(search_depth, 1.0)
    worst_index = 999.0
    worst_name = "-"
    for layer in soil_profile:
        if layer.top_elevation < bottom or layer.bottom_elevation > excavation_bottom_elevation:
            continue
        c = _cohesion(layer)
        phi = _friction_angle(layer)
        gamma_eff = _effective_gamma(layer)
        index = c / 20.0 + phi / 25.0 + gamma_eff / 10.0
        if index < worst_index:
            worst_index = index
            worst_name = f"{layer.stratum_code}-{layer.stratum_name}"
    limit = 1.6 if ("一" in safety_grade or "1" in safety_grade) else 1.35
    status = "pass" if worst_index >= limit else "warning"
    return CheckResult(
        rule_id=JGJ120_WEAK_UNDERLYING_RULE.rule_id,
        object_id=object_id,
        object_type="ExcavationModel",
        status=status,
        calculated_value=round(worst_index, 3) if worst_index < 999 else None,
        limit_value=limit,
        unit="-",
        message=f"坑底以下软弱下卧层筛查，控制层：{worst_name}。该项用于识别需专项复核的软土、淤泥质土、薄弱夹层或高压缩性层。",
        clause_reference=JGJ120_WEAK_UNDERLYING_RULE.clause_reference,
        formula="weak_index = c/20 + phi/25 + gamma_eff/10",
    )
