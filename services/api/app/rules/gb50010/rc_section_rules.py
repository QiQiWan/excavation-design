from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.rules.base import CheckResult, DesignRule

# Material design values are kept in one place so downstream calculation modules do not hard-code them.
# Values are common GB 50010/GB 55008 design-table values used for preliminary screening.  Final projects
# must confirm grade, seismic detailing, durability class and partial factors from the current official code text.
CONCRETE_GRADES: dict[str, dict[str, float]] = {
    "C25": {"fc": 11.9, "ft": 1.27, "ec": 28000.0},
    "C30": {"fc": 14.3, "ft": 1.43, "ec": 30000.0},
    "C35": {"fc": 16.7, "ft": 1.57, "ec": 31500.0},
    "C40": {"fc": 19.1, "ft": 1.71, "ec": 32500.0},
    "C45": {"fc": 21.1, "ft": 1.80, "ec": 33500.0},
    "C50": {"fc": 23.1, "ft": 1.89, "ec": 34500.0},
}
REBAR_FY_MPA: dict[str, float] = {
    "HPB300": 270.0,
    "HRB335": 300.0,
    "HRB400": 360.0,
    "HRB500": 435.0,
}

RC_FLEXURE_RULE = DesignRule(
    rule_id="GBT50010-2024-RC-FLEX-RECT-001",
    standard_name="GB/T 50010-2010（2024年局部修订）混凝土结构设计标准 / GB 55008-2021",
    standard_version="2010-2024",
    clause_reference="矩形截面受弯承载力基本公式（软件实现为常规单筋截面诊断）",
    name="矩形截面受弯配筋诊断",
    description="按等效矩形应力图和受拉钢筋屈服假定估算受弯所需钢筋面积。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel", "SupportElement"],
)

RC_SHEAR_RULE = DesignRule(
    rule_id="GBT50010-2024-RC-SHEAR-RECT-001",
    standard_name="GB/T 50010-2010（2024年局部修订）混凝土结构设计标准 / GB 55008-2021",
    standard_version="2010-2024",
    clause_reference="斜截面受剪承载力基本诊断（软件简化）",
    name="矩形截面受剪承载力诊断",
    description="按混凝土项 0.7*ft*b*h0 进行快速受剪诊断；箍筋详细设计需人工复核。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel", "SupportElement"],
)

GB50010_AXIAL_RULE = DesignRule(
    rule_id="GBT50010-2024-RC-AXIAL-RECT-001",
    standard_name="GB/T 50010-2010（2024年局部修订）混凝土结构设计标准 / GB 55008-2021",
    standard_version="2010-2024",
    clause_reference="轴压构件承载力基本诊断（软件简化）",
    name="矩形截面轴压承载力诊断",
    description="按混凝土和纵筋强度叠加并考虑折减系数进行轴压筛查。",
    severity="warning",
    applicable_to=["SupportElement"],
)

GB50010_FLEXURE_RULE = RC_FLEXURE_RULE
GB50010_SHEAR_RULE = RC_SHEAR_RULE


@dataclass(frozen=True)
class RectangularFlexureDesign:
    required_as: float
    minimum_as: float
    governing_as: float
    compression_block_depth: float
    neutral_axis_ratio: float
    provided_as: float | None
    utilization: float | None
    status: str
    message: str
    design_regime: str = "single_reinforced"
    compression_rebar_required: float = 0.0
    limiting_moment_knm_per_m: float | None = None
    section_capacity_exceeded: bool = False


def _grade_key(grade: str) -> str:
    return grade.upper().replace(" ", "")


def concrete_fc(grade: str) -> float:
    return CONCRETE_GRADES.get(_grade_key(grade), CONCRETE_GRADES["C35"])["fc"]


def concrete_ft(grade: str) -> float:
    return CONCRETE_GRADES.get(_grade_key(grade), CONCRETE_GRADES["C35"])["ft"]


def concrete_ec(grade: str) -> float:
    return CONCRETE_GRADES.get(_grade_key(grade), CONCRETE_GRADES["C35"])["ec"]


def rebar_fy(grade: str) -> float:
    return REBAR_FY_MPA.get(_grade_key(grade), REBAR_FY_MPA["HRB400"])


def bar_area(diameter_mm: float) -> float:
    return math.pi * diameter_mm**2 / 4.0


def as_per_m_for_spacing(diameter_mm: float, spacing_mm: float) -> float:
    return bar_area(diameter_mm) * 1000.0 / max(spacing_mm, 1e-9)


# Compatibility alias used by the reinforcement service.
provided_area_per_meter = as_per_m_for_spacing


def design_rectangular_flexure(
    *,
    moment_design_knm_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    cover_mm: float = 70.0,
    provided_as_mm2_per_m: float | None = None,
) -> RectangularFlexureDesign:
    """Rectangular wall-strip flexural design with an explicit high-moment branch.

    The former implementation capped the compression block when the quadratic
    discriminant became negative, which made required steel plateau and obscured
    the true reason for failure.  The revised method reports the limiting
    singly-reinforced moment and estimates the additional tension/compression
    steel couple required beyond that limit.
    """
    b = 1000.0
    h = max(thickness_m, 0.1) * 1000.0
    h0 = max(h - cover_mm, 0.65 * h)
    fc = concrete_fc(concrete_grade)
    fy = rebar_fy(rebar_grade)
    m_nmm = abs(moment_design_knm_per_m) * 1e6
    alpha1 = 1.0
    xi_b = 0.55
    xb = xi_b * h0
    m_lim_nmm = alpha1 * fc * b * xb * (h0 - xb / 2.0)
    compression_required = 0.0
    capacity_exceeded = False
    if m_nmm <= m_lim_nmm + 1e-6:
        disc = max(h0**2 - 2.0 * m_nmm / max(alpha1 * fc * b, 1e-9), 0.0)
        x = h0 - math.sqrt(disc)
        required = alpha1 * fc * b * x / fy
        regime = "single_reinforced"
        status = "pass"
        message = "受弯配筋快速计算完成。"
    else:
        x = xb
        base_tension = alpha1 * fc * b * xb / fy
        compression_bar_depth = min(max(cover_mm + 20.0, 50.0), 0.25 * h0)
        lever = max(h0 - compression_bar_depth, 0.55 * h0)
        extra = (m_nmm - m_lim_nmm) / max(fy * lever, 1e-9)
        required = base_tension + extra
        compression_required = extra
        regime = "double_reinforced_required"
        status = "manual_review"
        capacity_exceeded = True
        message = "弯矩超过单筋截面界限，已估算双筋附加钢筋；需复核截面、受压钢筋和钢筋笼施工性。"
    minimum = max(0.0020 * b * h, 0.45 * concrete_ft("C30") / max(fy, 1e-9) * b * h)
    governing = max(required, minimum)
    utilization: float | None = None
    if provided_as_mm2_per_m is not None and provided_as_mm2_per_m > 0:
        utilization = governing / provided_as_mm2_per_m
        if utilization > 1.0:
            status = "fail"
            message = "实配钢筋面积小于计算控制面积。"
        elif capacity_exceeded:
            status = "manual_review"
    xi = x / max(h0, 1e-9)
    return RectangularFlexureDesign(
        required_as=round(required, 2),
        minimum_as=round(minimum, 2),
        governing_as=round(governing, 2),
        compression_block_depth=round(x, 2),
        neutral_axis_ratio=round(xi, 4),
        provided_as=round(provided_as_mm2_per_m, 2) if provided_as_mm2_per_m else None,
        utilization=round(utilization, 3) if utilization is not None else None,
        status=status,
        message=message,
        design_regime=regime,
        compression_rebar_required=round(compression_required, 2),
        limiting_moment_knm_per_m=round(m_lim_nmm / 1e6, 3),
        section_capacity_exceeded=capacity_exceeded,
    )


def design_rectangular_flexure_single_rebar(
    moment_design_knm: float,
    width_mm: float,
    height_mm: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    cover_mm: float = 70.0,
) -> RectangularFlexureDesign:
    design = design_rectangular_flexure(
        moment_design_knm_per_m=moment_design_knm,
        thickness_m=max(height_mm / 1000.0, 0.1),
        concrete_grade=concrete_grade,
        rebar_grade=rebar_grade,
        cover_mm=cover_mm,
    )
    scale = max(width_mm, 1.0) / 1000.0
    return RectangularFlexureDesign(
        required_as=round(design.required_as * scale, 2),
        minimum_as=round(design.minimum_as * scale, 2),
        governing_as=round(design.governing_as * scale, 2),
        compression_block_depth=design.compression_block_depth,
        neutral_axis_ratio=design.neutral_axis_ratio,
        provided_as=round(design.provided_as * scale, 2) if design.provided_as else None,
        utilization=design.utilization,
        status=design.status,
        message=design.message,
        design_regime=design.design_regime,
        compression_rebar_required=round(design.compression_rebar_required * scale, 2),
        limiting_moment_knm_per_m=round((design.limiting_moment_knm_per_m or 0.0) * scale, 3),
        section_capacity_exceeded=design.section_capacity_exceeded,
    )


def rectangular_flexural_capacity_knm_per_m(
    provided_as_mm2_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    cover_mm: float = 70.0,
) -> float:
    b = 1000.0
    h = max(thickness_m, 0.1) * 1000.0
    h0 = max(h - cover_mm, 0.65 * h)
    fc = concrete_fc(concrete_grade)
    fy = rebar_fy(rebar_grade)
    x = fy * max(provided_as_mm2_per_m, 0.0) / max(fc * b, 1e-9)
    x = min(max(x, 0.0), 0.55 * h0)
    m_nmm = fc * b * x * (h0 - x / 2.0)
    return round(m_nmm / 1e6, 3)


def _select_bar_arrangement(required_as: float) -> tuple[int, int, float]:
    # Keep the automatically proposed cage constructible.  The former D40@100
    # fallback provided a large steel area but only 60 mm clear spacing, which
    # then failed the software's own JGJ120 detailing check.  High demand must
    # surface as a flexural/section upgrade issue, not as a contradictory bar
    # spacing recommendation.
    selected = (40, 120, as_per_m_for_spacing(40, 120))
    for dia in (20, 22, 25, 28, 32, 36, 40):
        for spacing in (250, 225, 200, 180, 160, 150, 140, 125, 120, 100):
            if spacing - dia < 75:
                continue
            provided = as_per_m_for_spacing(dia, spacing)
            if provided >= required_as:
                return int(dia), int(spacing), round(provided, 2)
    return selected


def design_rectangular_flexural_reinforcement(
    moment_design_knm_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    cover_mm: float = 70.0,
) -> dict[str, Any]:
    design = design_rectangular_flexure(
        moment_design_knm_per_m=moment_design_knm_per_m,
        thickness_m=thickness_m,
        concrete_grade=concrete_grade,
        rebar_grade=rebar_grade,
        cover_mm=cover_mm,
    )
    dia, spacing, provided = _select_bar_arrangement(design.governing_as)
    arrangement_status = design.status
    if provided + 1.0e-6 < design.governing_as:
        arrangement_status = "fail"
    return {
        "status": arrangement_status,
        "momentDesign": round(abs(moment_design_knm_per_m), 3),
        "asRequired": design.governing_as,
        "asByFlexure": design.required_as,
        "asMinimum": design.minimum_as,
        "compressionBlockDepth": design.compression_block_depth,
        "neutralAxisRatio": design.neutral_axis_ratio,
        "utilization": round(design.governing_as / max(provided, 1e-9), 3),
        "barArrangement": {
            "diameter": dia,
            "spacing": spacing,
            "providedAs": provided,
            "description": f"D{dia}@{spacing}",
        },
        "message": design.message,
        "formula": "M <= alpha1*fc*b*x*(h0-x/2); alpha1*fc*b*x = fy*As",
        "reviewRequired": True,
    }


def check_rectangular_shear_capacity(
    shear_design_kn_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    cover_mm: float = 70.0,
) -> dict[str, Any]:
    b = 1000.0
    h = max(thickness_m, 0.1) * 1000.0
    h0 = max(h - cover_mm, 0.65 * h)
    capacity_kn_per_m = 0.7 * concrete_ft(concrete_grade) * b * h0 / 1000.0
    util = abs(shear_design_kn_per_m) / max(capacity_kn_per_m, 1e-9)
    return {
        "status": "pass" if util <= 1.0 else "fail",
        "shearDesign": round(abs(shear_design_kn_per_m), 3),
        "concreteShearCapacity": round(capacity_kn_per_m, 3),
        "utilization": round(util, 3),
        "formula": "V <= 0.7*ft*b*h0 (screening without detailed stirrup contribution)",
        "reviewRequired": True,
    }


def check_rectangular_shear(
    *,
    object_id: str,
    shear_design_kn_per_m: float,
    thickness_m: float,
    concrete_grade: str = "C35",
    cover_mm: float = 70.0,
) -> CheckResult:
    result = check_rectangular_shear_capacity(shear_design_kn_per_m, thickness_m, concrete_grade, cover_mm)
    return CheckResult(
        rule_id=RC_SHEAR_RULE.rule_id,
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status=result["status"],
        calculated_value=result["shearDesign"],
        limit_value=result["concreteShearCapacity"],
        unit="kN/m",
        message="受剪快速诊断满足混凝土项承载力。" if result["status"] == "pass" else "受剪快速诊断不满足，应配置箍筋/加厚截面并进行完整斜截面设计。",
        clause_reference=RC_SHEAR_RULE.clause_reference,
        standard_name=RC_SHEAR_RULE.standard_name,
        standard_version=RC_SHEAR_RULE.standard_version,
        name=RC_SHEAR_RULE.name,
        formula=result["formula"],
    )


def flexure_check_result(object_id: str, design: RectangularFlexureDesign) -> CheckResult:
    return CheckResult(
        rule_id=RC_FLEXURE_RULE.rule_id,
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status=design.status if design.status in {"pass", "fail"} else "manual_review",
        calculated_value=design.provided_as,
        limit_value=design.governing_as,
        unit="mm2/m",
        message=design.message + " 正式配筋仍需考虑裂缝、构造、接头、吊装和钢筋笼施工。",
        clause_reference=RC_FLEXURE_RULE.clause_reference,
        standard_name=RC_FLEXURE_RULE.standard_name,
        standard_version=RC_FLEXURE_RULE.standard_version,
        name=RC_FLEXURE_RULE.name,
        formula="M <= alpha1*fc*b*x*(h0-x/2); alpha1*fc*b*x = fy*As",
    )


def check_rc_rectangular_axial_capacity(
    axial_design_kn: float,
    width_m: float,
    height_m: float,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    longitudinal_bar_dia: float = 25.0,
    longitudinal_bar_count: int = 8,
) -> dict[str, Any]:
    fc = concrete_fc(concrete_grade)
    fy = rebar_fy(rebar_grade)
    area_mm2 = max(width_m, 0.01) * 1000.0 * max(height_m, 0.01) * 1000.0
    as_mm2 = max(0, longitudinal_bar_count) * bar_area(longitudinal_bar_dia)
    phi = 0.75
    capacity_kn = phi * (fc * max(area_mm2 - as_mm2, 0.0) + fy * as_mm2) / 1000.0
    nd = abs(axial_design_kn)
    util = nd / max(capacity_kn, 1e-9)
    return {
        "status": "pass" if util <= 1.0 else "fail",
        "axialDesign": round(nd, 3),
        "capacity": round(capacity_kn, 3),
        "utilization": round(util, 3),
        "formula": "N <= phi*(fc*Ac + fy*As), with eccentricity/slenderness requiring manual review",
        "reviewRequired": True,
    }
