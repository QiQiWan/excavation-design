from __future__ import annotations

import math
from typing import Any

from app.rules.base import CheckResult, DesignRule

GB50017_STEEL_COMPRESSION_RULE = DesignRule(
    rule_id="GB50017-2017-STEEL-COMPRESSION-CURVE-SUBSET",
    standard_name="钢结构设计标准 GB 50017",
    standard_version="2017",
    clause_reference="轴心受压构件强度、长细比与稳定系数曲线计算子集；截面分类、局部稳定和连接仍需复核",
    name="钢支撑轴压强度与稳定曲线子集",
    description="用于钢管支撑的面积、回转半径、长细比、欧拉临界力和稳定折减系数筛查。",
    severity="mandatory",
    applicable_to=["SupportElement"],
)

# Imperfection parameters for auditable Perry-type stability curves.  The
# selected curve must be confirmed against the member section, fabrication and
# axis in the project design basis.  Values are deliberately exposed instead of
# hiding a fixed phi=0.75 inside the capacity check.
BUCKLING_CURVES: dict[str, float] = {"a": 0.21, "b": 0.34, "c": 0.49, "d": 0.76}


def steel_pipe_buckling_curve(
    *,
    outer_diameter_m: float,
    wall_thickness_m: float,
    length_m: float,
    elastic_modulus_mpa: float = 206000.0,
    yield_strength_mpa: float = 305.0,
    effective_length_factor: float = 1.0,
    curve_class: str = "b",
) -> dict[str, Any]:
    d = float(outer_diameter_m) * 1000.0
    t = float(wall_thickness_m) * 1000.0
    if d <= 0.0 or t <= 0.0 or 2.0 * t >= d:
        return {"status": "invalid", "message": "invalid circular hollow section"}
    inner = d - 2.0 * t
    area = math.pi * (d**2 - inner**2) / 4.0
    inertia = math.pi * (d**4 - inner**4) / 64.0
    radius = math.sqrt(inertia / max(area, 1.0e-12))
    effective_length_mm = max(float(length_m) * 1000.0 * max(float(effective_length_factor), 0.2), 1.0)
    slenderness = effective_length_mm / max(radius, 1.0e-9)
    euler_stress = math.pi**2 * float(elastic_modulus_mpa) / max(slenderness**2, 1.0e-12)
    normalized_slenderness = math.sqrt(max(float(yield_strength_mpa) / max(euler_stress, 1.0e-12), 0.0))
    alpha = BUCKLING_CURVES.get(str(curve_class).lower(), BUCKLING_CURVES["b"])
    phi_term = 0.5 * (1.0 + alpha * (normalized_slenderness - 0.2) + normalized_slenderness**2)
    radical = max(phi_term**2 - normalized_slenderness**2, 0.0)
    reduction = 1.0 / max(phi_term + math.sqrt(radical), 1.0)
    reduction = min(max(reduction, 0.05), 1.0)
    local_slenderness = d / max(t, 1.0e-9)
    return {
        "status": "ok",
        "areaMm2": area,
        "inertiaMm4": inertia,
        "radiusOfGyrationMm": radius,
        "effectiveLengthMm": effective_length_mm,
        "slenderness": slenderness,
        "eulerStressMpa": euler_stress,
        "normalizedSlenderness": normalized_slenderness,
        "curveClass": str(curve_class).lower() if str(curve_class).lower() in BUCKLING_CURVES else "b",
        "imperfectionAlpha": alpha,
        "stabilityReductionFactor": reduction,
        "diameterThicknessRatio": local_slenderness,
    }


def check_steel_pipe_support_axial_capacity(
    object_id: str,
    axial_force_kN: float,
    outer_diameter_m: float,
    wall_thickness_m: float,
    length_m: float,
    steel_design_strength_mpa: float = 305.0,
    stability_factor: float | None = None,
    gamma0: float = 1.0,
    force_factor: float = 1.25,
    effective_length_factor: float = 1.0,
    buckling_curve_class: str = "b",
) -> CheckResult:
    curve = steel_pipe_buckling_curve(
        outer_diameter_m=outer_diameter_m,
        wall_thickness_m=wall_thickness_m,
        length_m=length_m,
        yield_strength_mpa=steel_design_strength_mpa,
        effective_length_factor=effective_length_factor,
        curve_class=buckling_curve_class,
    )
    if curve.get("status") != "ok":
        return CheckResult(
            rule_id=GB50017_STEEL_COMPRESSION_RULE.rule_id,
            object_id=object_id,
            object_type="SupportElement",
            status="manual_review",
            message="钢管截面尺寸无效，需人工复核。",
            clause_reference=GB50017_STEEL_COMPRESSION_RULE.clause_reference,
            standard_name=GB50017_STEEL_COMPRESSION_RULE.standard_name,
            standard_version=GB50017_STEEL_COMPRESSION_RULE.standard_version,
        )
    reduction = float(curve["stabilityReductionFactor"])
    if stability_factor is not None:
        # Legacy explicit override remains accepted but can only reduce capacity.
        reduction = min(reduction, max(float(stability_factor), 0.05))
    nd = abs(float(axial_force_kN)) * float(gamma0) * float(force_factor)
    capacity = reduction * float(curve["areaMm2"]) * float(steel_design_strength_mpa) / 1000.0
    utilization = nd / max(capacity, 1.0e-9)
    slenderness = float(curve["slenderness"])
    diameter_thickness = float(curve["diameterThicknessRatio"])
    # The limits are project-screening guards. Local plate classification must
    # still be confirmed by the formal section-class calculation.
    local_warning = diameter_thickness > 100.0
    status = "fail" if utilization > 1.0 else "warning" if utilization > 0.90 or local_warning else "pass"
    message = (
        f"钢管轴压稳定曲线验算：lambda={slenderness:.1f}, lambda_bar={curve['normalizedSlenderness']:.3f}, "
        f"phi={reduction:.3f}, D/t={diameter_thickness:.1f}, curve={curve['curveClass']}。"
        "曲线类别、局部稳定、初弯曲、残余应力、节点偏心、焊缝和连接板仍需项目复核。"
    )
    return CheckResult(
        rule_id=GB50017_STEEL_COMPRESSION_RULE.rule_id,
        object_id=object_id,
        object_type="SupportElement",
        status=status,
        calculated_value=round(utilization, 4),
        limit_value=1.0,
        unit="utilization",
        message=message,
        clause_reference=GB50017_STEEL_COMPRESSION_RULE.clause_reference,
        standard_name=GB50017_STEEL_COMPRESSION_RULE.standard_name,
        standard_version=GB50017_STEEL_COMPRESSION_RULE.standard_version,
        formula="N_d/(chi*A*f) <= 1; chi from normalized slenderness and confirmed buckling curve",
        review_required=True,
    )
