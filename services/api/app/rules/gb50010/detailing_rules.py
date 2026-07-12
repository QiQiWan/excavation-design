from __future__ import annotations

from app.rules.base import CheckResult, DesignRule
from app.rules.gb50010.rc_section_rules import rebar_fy

GB50010_CRACK_RULE = DesignRule(
    rule_id="GBT50010-2024-SERVICEABILITY-CRACK-SCREEN",
    standard_name="GB 50010-2010（2024年局部修订）混凝土结构设计规范 / GB 55008-2021",
    standard_version="2010-2024",
    clause_reference="正常使用极限状态裂缝宽度验算（软件为参数化工程筛查）",
    name="裂缝宽度筛查",
    description="根据钢筋应力利用率、钢筋间距和环境等级估算裂缝宽度；正式公式和长期效应需复核。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel", "SupportElement"],
)

GB50010_ANCHORAGE_RULE = DesignRule(
    rule_id="GBT50010-2024-REBAR-ANCHORAGE-LAP-SCREEN",
    standard_name="GB 50010-2010（2024年局部修订）混凝土结构设计规范 / GB 55008-2021",
    standard_version="2010-2024",
    clause_reference="钢筋锚固、搭接和构造要求（软件为参数化筛查）",
    name="锚固搭接和构造筛查",
    description="按钢筋等级、直径、可用锚固长度和搭接长度进行构造完整性筛查。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel", "SupportElement"],
)


def crack_width_limit_mm(environment_grade: str) -> float:
    if "严格" in (environment_grade or "") or "一级" in (environment_grade or ""):
        return 0.20
    if "宽松" in (environment_grade or ""):
        return 0.35
    return 0.30


def estimate_crack_width_mm(
    moment_design_knm_per_m: float,
    moment_capacity_knm_per_m: float,
    bar_spacing_mm: float,
    bar_diameter_mm: float,
    rebar_grade: str = "HRB400",
) -> float:
    """A deterministic serviceability screening estimate.

    It intentionally stays conservative and transparent instead of claiming a full GB 50010 crack
    formula. The result is used as a gate in the prototype calculation book and IFC properties.
    """
    capacity = max(moment_capacity_knm_per_m, 1e-9)
    utilization = min(abs(moment_design_knm_per_m) / capacity, 1.35)
    fy = rebar_fy(rebar_grade)
    stress_ratio = min(0.85, 0.55 * utilization * fy / 360.0)
    spacing_factor = max(bar_spacing_mm, 75.0) / 150.0
    diameter_factor = max(bar_diameter_mm, 10.0) / 25.0
    return round(0.13 * stress_ratio * spacing_factor * diameter_factor, 3)


def check_crack_width(
    object_id: str,
    moment_design_knm_per_m: float,
    moment_capacity_knm_per_m: float,
    bar_spacing_mm: float,
    bar_diameter_mm: float,
    environment_grade: str,
    rebar_grade: str = "HRB400",
) -> CheckResult:
    width = estimate_crack_width_mm(moment_design_knm_per_m, moment_capacity_knm_per_m, bar_spacing_mm, bar_diameter_mm, rebar_grade)
    limit = crack_width_limit_mm(environment_grade)
    return CheckResult(
        rule_id=GB50010_CRACK_RULE.rule_id,
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status="pass" if width <= limit else "warning",
        calculated_value=width,
        limit_value=limit,
        unit="mm",
        message="裂缝宽度服务性筛查。正式设计应按构件受力性质、保护层、长期效应、荷载准永久组合和地下环境等级复核。",
        clause_reference=GB50010_CRACK_RULE.clause_reference,
        standard_name=GB50010_CRACK_RULE.standard_name,
        standard_version=GB50010_CRACK_RULE.standard_version,
        formula="w_screen = 0.13 * stress_ratio * (spacing/150) * (diameter/25)",
    )


def check_rebar_anchorage_and_lap(
    object_id: str,
    bar_diameter_mm: float,
    rebar_grade: str,
    available_anchor_length_mm: float,
    available_lap_length_mm: float,
    seismic: bool = False,
) -> list[CheckResult]:
    # Conservative engineering screening values. Final detailing must use current code tables,
    # concrete grade, bond condition, anchorage form, compression/tension state and seismic category.
    grade_factor = 1.10 if "500" in (rebar_grade or "") else 1.0
    seismic_factor = 1.15 if seismic else 1.0
    lb = 35.0 * max(bar_diameter_mm, 1.0) * grade_factor * seismic_factor
    lap = 1.2 * lb
    return [
        CheckResult(
            rule_id=GB50010_ANCHORAGE_RULE.rule_id + "-ANCHOR",
            object_id=object_id,
            object_type="DiaphragmWallPanel",
            status="pass" if available_anchor_length_mm >= lb else "fail",
            calculated_value=round(available_anchor_length_mm, 3),
            limit_value=round(lb, 3),
            unit="mm",
            message="受力钢筋基本锚固长度筛查；锚固形式、弯折、焊接/机械连接和抗震构造需在施工图详设中复核。",
            clause_reference=GB50010_ANCHORAGE_RULE.clause_reference,
            standard_name=GB50010_ANCHORAGE_RULE.standard_name,
            standard_version=GB50010_ANCHORAGE_RULE.standard_version,
            formula="l_available >= 35d * grade_factor * seismic_factor",
        ),
        CheckResult(
            rule_id=GB50010_ANCHORAGE_RULE.rule_id + "-LAP",
            object_id=object_id,
            object_type="DiaphragmWallPanel",
            status="pass" if available_lap_length_mm >= lap else "warning",
            calculated_value=round(available_lap_length_mm, 3),
            limit_value=round(lap, 3),
            unit="mm",
            message="纵筋搭接长度筛查；同一区段接头率、机械连接等级和钢筋笼分节吊装需复核。",
            clause_reference=GB50010_ANCHORAGE_RULE.clause_reference,
            standard_name=GB50010_ANCHORAGE_RULE.standard_name,
            standard_version=GB50010_ANCHORAGE_RULE.standard_version,
            formula="l_lap_available >= 1.2 * l_anchor_screen",
        ),
    ]
