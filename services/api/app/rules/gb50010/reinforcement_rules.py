from __future__ import annotations

from app.rules.base import CheckResult, DesignRule
from app.rules.gb50010.rc_section_rules import as_per_m_for_spacing

MIN_REINFORCEMENT_RULE = DesignRule(
    rule_id="GBT50010-2024-RC-MINREBAR-001",
    standard_name="GB/T 50010-2010（2024年局部修订）混凝土结构设计标准 / GB 55008-2021",
    standard_version="2010-2024",
    clause_reference="构造配筋与最小配筋率要求（软件简化诊断）",
    name="最小配筋率快速诊断",
    description="按软件默认最小配筋率检查实配钢筋面积。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel", "SupportElement"],
)


def recommend_bar_spacing(required_as_mm2_per_m: float, preferred_diameters: tuple[int, ...] = (22, 25, 28, 32)) -> tuple[int, int, float]:
    for dia in preferred_diameters:
        for spacing in (250, 225, 200, 180, 160, 150, 140, 125, 120, 100):
            if spacing - dia < 75:
                continue
            provided = as_per_m_for_spacing(dia, spacing)
            if provided >= required_as_mm2_per_m:
                return dia, spacing, round(provided, 2)
    dia = preferred_diameters[-1]
    spacing = next((item for item in (120, 125, 140, 150, 160, 180, 200, 225, 250) if item - dia >= 75), 250)
    return dia, spacing, round(as_per_m_for_spacing(dia, spacing), 2)


def check_minimum_reinforcement_gbt50010(object_id: str, object_type: str, provided_as: float, minimum_as: float) -> CheckResult:
    status = "pass" if provided_as >= minimum_as else "fail"
    return CheckResult(
        rule_id=MIN_REINFORCEMENT_RULE.rule_id,
        object_id=object_id,
        object_type=object_type,
        status=status,
        calculated_value=round(provided_as, 2),
        limit_value=round(minimum_as, 2),
        unit="mm2/m",
        message=("实配面积满足软件默认最小配筋诊断。" if status == "pass" else "实配面积小于软件默认最小配筋诊断值。"),
        clause_reference=MIN_REINFORCEMENT_RULE.clause_reference,
    )

# Backward-compatible name used by previous MVP imports/tests.
def check_minimum_reinforcement_gb50010(*args, **kwargs):
    return check_minimum_reinforcement_gbt50010(*args, **kwargs)


def check_minimum_wall_reinforcement(object_id: str, thickness_m: float, diameter_mm: float, spacing_mm: float):
    provided = as_per_m_for_spacing(diameter_mm, spacing_mm)
    minimum = 0.0020 * 1000.0 * thickness_m * 1000.0
    return check_minimum_reinforcement_gbt50010(object_id, "DiaphragmWallPanel", provided, minimum)

GB50010_MIN_REINFORCEMENT_RULE = MIN_REINFORCEMENT_RULE

# Compatibility alias used by early registry versions and tests.
GB50010_MIN_REINFORCEMENT_RULE = MIN_REINFORCEMENT_RULE
