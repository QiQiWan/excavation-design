from __future__ import annotations

import math
from dataclasses import dataclass

from app.rules.base import CheckResult

# Design values used in the bundled rule table. Units: MPa for strengths, GPa for elastic modulus.
CONCRETE = {
    "C30": {"fc": 14.3, "ft": 1.43, "Ec": 30.0},
    "C35": {"fc": 16.7, "ft": 1.57, "Ec": 31.5},
    "C40": {"fc": 19.1, "ft": 1.71, "Ec": 32.5},
    "C45": {"fc": 21.1, "ft": 1.80, "Ec": 33.5},
    "C50": {"fc": 23.1, "ft": 1.89, "Ec": 34.5},
}

STEEL = {
    "HPB300": {"fy": 270.0, "fyv": 270.0},
    "HRB335": {"fy": 300.0, "fyv": 300.0},
    "HRB400": {"fy": 360.0, "fyv": 360.0},
    "HRB500": {"fy": 435.0, "fyv": 435.0},
}

BAR_AREAS = {d: math.pi * d * d / 4.0 for d in [8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40]}


@dataclass(frozen=True)
class FlexuralDesign:
    required_as_mm2_per_m: float
    minimum_as_mm2_per_m: float
    selected_diameter_mm: int
    selected_spacing_mm: int
    provided_as_mm2_per_m: float
    compression_zone_x_mm: float
    utilization: float
    status: str
    message: str


@dataclass(frozen=True)
class ShearDesign:
    concrete_capacity_kn: float
    stirrup_capacity_kn: float
    total_capacity_kn: float
    utilization: float
    status: str
    selected_diameter_mm: int
    selected_spacing_mm: int
    message: str


def concrete_strength(grade: str) -> dict[str, float]:
    return CONCRETE.get(grade.upper(), CONCRETE["C35"])


def steel_strength(grade: str) -> dict[str, float]:
    return STEEL.get(grade.upper(), STEEL["HRB400"])


def provided_as_mm2_per_m(diameter_mm: int, spacing_mm: int) -> float:
    return BAR_AREAS[diameter_mm] * (1000.0 / spacing_mm)


def _select_bars(required_as: float) -> tuple[int, int, float]:
    candidates: list[tuple[int, int, float]] = []
    for dia in [16, 18, 20, 22, 25, 28, 32, 36]:
        for spacing in [250, 220, 200, 180, 160, 150, 140, 125, 120, 100]:
            asv = provided_as_mm2_per_m(dia, spacing)
            if asv >= required_as:
                candidates.append((dia, spacing, asv))
    if not candidates:
        dia, spacing = 40, 100
        return dia, spacing, provided_as_mm2_per_m(dia, spacing)
    return sorted(candidates, key=lambda item: (item[2], item[0]))[0]


def design_rectangular_flexural_reinforcement(
    moment_design_knm_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    cover_mm: float = 70.0,
    trial_bar_diameter_mm: float = 25.0,
    width_m: float = 1.0,
) -> FlexuralDesign:
    """Single-reinforced rectangular section design for a diaphragm-wall strip.

    b = 1m wall strip; h = wall thickness. Moment is kN*m per metre wall width.
    """
    if thickness_m <= 0:
        raise ValueError("thickness_m must be positive")
    fc = concrete_strength(concrete_grade)["fc"]
    fy = steel_strength(rebar_grade)["fy"]
    alpha1 = 1.0  # for ordinary concrete strength classes in this MVP range
    xi_b = 0.518 if rebar_grade.upper().startswith("HRB400") else 0.55
    b = width_m * 1000.0
    h = thickness_m * 1000.0
    h0 = max(0.1 * h, h - cover_mm - 0.5 * trial_bar_diameter_mm)
    md = abs(moment_design_knm_per_m) * 1e6  # kN*m -> N*mm, MPa=N/mm2
    if md <= 1e-9:
        min_as = 0.0020 * b * h
        dia, spacing, prov = _select_bars(min_as)
        return FlexuralDesign(min_as, min_as, dia, spacing, prov, 0.0, 0.0, "pass", "弯矩接近零，按最小配筋控制。")
    discriminant = h0 * h0 - 2.0 * md / max(alpha1 * fc * b, 1e-9)
    if discriminant < 0:
        x = xi_b * h0
        max_m = alpha1 * fc * b * x * (h0 - 0.5 * x)
        required_as = alpha1 * fc * b * x / fy
        min_as = 0.0020 * b * h
        dia, spacing, prov = _select_bars(max(required_as, min_as))
        return FlexuralDesign(required_as, min_as, dia, spacing, prov, x, md / max(max_m, 1e-9), "fail", "单筋矩形截面受弯承载力不足，需增大墙厚、提高材料或采用双筋/专项设计。")
    x = h0 - math.sqrt(discriminant)
    x_lim = xi_b * h0
    status = "pass" if x <= x_lim else "fail"
    if x > x_lim:
        x = x_lim
    required_as = alpha1 * fc * b * x / fy
    min_as = 0.0020 * b * h
    dia, spacing, prov = _select_bars(max(required_as, min_as))
    capacity = alpha1 * fc * b * x * (h0 - 0.5 * x)
    utilization = md / max(capacity, 1e-9)
    msg = "正截面受弯承载力满足内置矩形截面公式。" if status == "pass" else "受压区高度超过限值，需双筋或增大截面复核。"
    return FlexuralDesign(required_as, min_as, dia, spacing, prov, x, utilization, status, msg)


def design_rectangular_shear_reinforcement(
    shear_design_kn_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    stirrup_grade: str = "HRB400",
    cover_mm: float = 70.0,
    width_m: float = 1.0,
) -> ShearDesign:
    fc_data = concrete_strength(concrete_grade)
    ft = fc_data["ft"]
    fyv = steel_strength(stirrup_grade)["fyv"]
    b = width_m * 1000.0
    h = thickness_m * 1000.0
    h0 = max(0.1 * h, h - cover_mm - 12.0)
    v_design_n = abs(shear_design_kn_per_m) * 1000.0
    v_conc = 0.7 * ft * b * h0
    # two-legged D12@150 per metre wall strip as default horizontal/links contribution
    dia = 12
    spacing = 150
    asv = 2.0 * BAR_AREAS[dia]
    v_st = fyv * asv * h0 / spacing
    total = v_conc + v_st
    utilization = v_design_n / max(total, 1e-9)
    status = "pass" if utilization <= 1.0 else "fail"
    message = "斜截面受剪承载力满足内置混凝土+箍筋公式。" if status == "pass" else "斜截面受剪承载力不足，需加密箍筋或增大截面。"
    return ShearDesign(v_conc / 1000.0, v_st / 1000.0, total / 1000.0, utilization, status, dia, spacing, message)


def check_wall_rc_capacity(
    object_id: str,
    moment_design_knm_per_m: float,
    shear_design_kn_per_m: float,
    thickness_m: float,
    concrete_grade: str,
    rebar_grade: str,
) -> list[CheckResult]:
    flex = design_rectangular_flexural_reinforcement(moment_design_knm_per_m, thickness_m, concrete_grade, rebar_grade)
    shear = design_rectangular_shear_reinforcement(shear_design_kn_per_m, thickness_m, concrete_grade, rebar_grade)
    return [
        CheckResult(
            rule_id="GBT50010-2024-FLEXURE-RECT",
            standard_name="GB 50010",
            standard_version="2010(2024)",
            clause_reference="6.2.10",
            name="矩形截面正截面受弯承载力",
            object_id=object_id,
            object_type="diaphragm_wall_strip",
            status=flex.status, calculated_value=round(flex.utilization, 3), limit_value=1.0, unit="-",
            formula="M <= alpha1*fc*b*x*(h0-x/2); alpha1*fc*b*x = fy*As",
            message=f"{flex.message} As_req={flex.required_as_mm2_per_m:.0f} mm2/m, As_prov={flex.provided_as_mm2_per_m:.0f} mm2/m, 选筋 D{flex.selected_diameter_mm}@{flex.selected_spacing_mm}。",
        ),
        CheckResult(
            rule_id="GBT50010-2024-SHEAR-RECT",
            standard_name="GB 50010",
            standard_version="2010(2024)",
            clause_reference="6.3",
            name="矩形截面斜截面受剪承载力",
            object_id=object_id,
            object_type="diaphragm_wall_strip",
            status=shear.status, calculated_value=round(shear.utilization, 3), limit_value=1.0, unit="-",
            formula="V <= 0.7*ft*b*h0 + fyv*Asv*h0/s",
            message=f"{shear.message} Vc={shear.concrete_capacity_kn:.1f} kN/m, Vs={shear.stirrup_capacity_kn:.1f} kN/m。",
        ),
    ]


def check_rectangular_support_axial_capacity(
    object_id: str,
    axial_design_kn: float,
    width_m: float,
    height_m: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    longitudinal_bar_count: int = 8,
    longitudinal_bar_dia: int = 25,
) -> CheckResult:
    fc = concrete_strength(concrete_grade)["fc"]
    fy = steel_strength(rebar_grade)["fy"]
    area = width_m * 1000.0 * height_m * 1000.0
    as_total = longitudinal_bar_count * BAR_AREAS.get(longitudinal_bar_dia, BAR_AREAS[25])
    # Simplified concentric compression design capacity with stability/eccentricity reduction.
    phi = 0.75
    capacity_n = phi * (fc * (area - as_total) + fy * as_total)
    capacity_kn = capacity_n / 1000.0
    util = abs(axial_design_kn) / max(capacity_kn, 1e-9)
    return CheckResult(
        rule_id="GBT50010-2024-RC-SUPPORT-AXIAL",
        standard_name="GB 50010",
        standard_version="2010(2024)",
        clause_reference="6.2/6.3; compression member rules simplified",
        name="钢筋混凝土支撑轴压承载力简化复核",
        object_id=object_id,
        object_type="rc_internal_support",
        status="pass" if util <= 1.0 else "fail",
        calculated_value=round(util, 3),
        limit_value=1.0,
        unit="-",
        formula="N <= phi*(fc*Ac + fy*As)",
        message=f"轴压利用率={util:.3f}，承载力约 {capacity_kn:.1f} kN；偏心、长细比、节点和施工阶段需专项复核。",
    )
