from __future__ import annotations

import math

from app.rules.base import CheckResult, DesignRule

GB50017_STEEL_COMPRESSION_RULE = DesignRule(
    rule_id="GB50017-2017-STEEL-COMPRESSION-SUBSET",
    standard_name="钢结构设计标准 GB 50017",
    standard_version="2017",
    clause_reference="axial compression strength/stability subset; section class and effective length to verify",
    name="钢支撑轴压强度/稳定子集",
    description="用于钢管或 H 型钢支撑的轴压强度和长细比提示。",
    severity="mandatory",
    applicable_to=["SupportElement"],
)


def check_steel_pipe_support_axial_capacity(
    object_id: str,
    axial_force_kN: float,
    outer_diameter_m: float,
    wall_thickness_m: float,
    length_m: float,
    steel_design_strength_mpa: float = 305.0,
    stability_factor: float = 0.75,
    gamma0: float = 1.0,
    force_factor: float = 1.25,
) -> CheckResult:
    if outer_diameter_m <= 0 or wall_thickness_m <= 0 or wall_thickness_m >= outer_diameter_m / 2:
        return CheckResult(
            rule_id=GB50017_STEEL_COMPRESSION_RULE.rule_id,
            object_id=object_id,
            object_type="SupportElement",
            status="manual_review",
            message="钢管截面尺寸无效，需人工复核。",
            clause_reference=GB50017_STEEL_COMPRESSION_RULE.clause_reference,
        )
    d = outer_diameter_m * 1000.0
    t = wall_thickness_m * 1000.0
    area = math.pi * (d**2 - (d - 2 * t) ** 2) / 4.0
    nd = abs(axial_force_kN) * gamma0 * force_factor
    capacity = stability_factor * area * steel_design_strength_mpa / 1000.0
    status = "pass" if nd <= capacity else "fail"
    slenderness_note = "长细比、初弯曲、节点偏心和焊缝连接未完全自动化。"
    return CheckResult(
        rule_id=GB50017_STEEL_COMPRESSION_RULE.rule_id,
        object_id=object_id,
        object_type="SupportElement",
        status=status,
        calculated_value=round(nd / capacity, 3) if capacity else None,
        limit_value=1.0,
        unit="utilization",
        message=f"钢管轴压承载力子集验算；{slenderness_note}",
        clause_reference=GB50017_STEEL_COMPRESSION_RULE.clause_reference,
    )
