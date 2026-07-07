from __future__ import annotations

from typing import Any

from app.rules.base import CheckResult, DesignRule
from app.rules.jgj120_2012.earth_pressure_rules import calculate_jgj120_lateral_pressure_profile
from app.schemas.domain import GeologicalLayer

JGJ120_HORIZONTAL_LOAD_RULE = DesignRule(
    rule_id="JGJ120-2012-3.4-LATERAL-PRESSURE-SUBSET",
    standard_name="建筑基坑支护技术规程",
    standard_version="JGJ 120-2012",
    clause_reference="3.4 土压力与水压力计算（软件实现朗肯主动/被动土压力子集）",
    name="水平荷载标准值计算子集",
    description="按朗肯土压力系数、水土分算/合算思想生成分层侧向压力剖面。",
    severity="mandatory",
    applicable_to=["CalculationCase", "DiaphragmWallPanel"],
)

JGJ120_IMPORTANCE_RULE = DesignRule(
    rule_id="JGJ120-2012-3.1-IMPORTANCE-FACTOR-SUBSET",
    standard_name="建筑基坑支护技术规程",
    standard_version="JGJ 120-2012",
    clause_reference="支护结构安全等级与重要性系数（软件简化映射）",
    name="安全等级重要性系数子集",
    description="按安全等级采用软件内部重要性系数映射，用于设计组合说明和追踪。",
    severity="warning",
    applicable_to=["Project", "CalculationCase"],
)

JGJ120_EMBEDMENT_RULE = DesignRule(
    rule_id="JGJ120-2012-4.2-EMBEDMENT-STABILITY-SCREEN",
    standard_name="建筑基坑支护技术规程",
    standard_version="JGJ 120-2012",
    clause_reference="4.2 稳定性验算（软件采用简化抗力矩/作用矩筛查，正式公式需按项目条件核对）",
    name="地连墙嵌固稳定筛查",
    description="按主动压力作用矩和嵌固段净被动抗力矩估算嵌固稳定安全系数。",
    severity="mandatory",
    applicable_to=["DiaphragmWallPanel"],
)

JGJ120_DW_DETAILING_RULE = JGJ120_DIAPHRAGM_CONSTRUCTION_RULE = DesignRule(
    rule_id="JGJ120-2012-4.5-DIAPHRAGM-CONSTRUCTION-CHECK",
    standard_name="建筑基坑支护技术规程",
    standard_version="JGJ 120-2012",
    clause_reference="4.5.2, 4.5.5, 4.5.6, 4.5.7",
    name="地下连续墙厚度、材料与构造筛查",
    description="校核墙厚常用规格、混凝土等级、纵向钢筋直径/净距、水平筋直径/间距和保护层构造。",
    severity="warning",
    applicable_to=["DiaphragmWallPanel"],
)


def required_embedment_factor(safety_grade: str) -> float:
    if "一" in safety_grade or "1" in safety_grade:
        return 1.25
    if "三" in safety_grade or "3" in safety_grade:
        return 1.15
    return 1.20


def _pressure_at(profile, depth: float) -> float:
    points = sorted(profile.points, key=lambda p: p.depth)
    if not points:
        return 0.0
    if depth <= points[0].depth:
        return points[0].total_pressure
    if depth >= points[-1].depth:
        return points[-1].total_pressure
    for a, b in zip(points, points[1:]):
        if a.depth <= depth <= b.depth:
            t = (depth - a.depth) / max(b.depth - a.depth, 1e-9)
            return a.total_pressure + t * (b.total_pressure - a.total_pressure)
    return points[-1].total_pressure


def _integrate_moment(profile, start_depth: float, end_depth: float, toe_depth: float, net_with=None) -> tuple[float, float]:
    if end_depth <= start_depth:
        return 0.0, 0.0
    base_points = [start_depth, end_depth] + [p.depth for p in profile.points if start_depth < p.depth < end_depth]
    if net_with is not None:
        base_points += [p.depth for p in net_with.points if start_depth < p.depth < end_depth]
    samples = sorted(set(round(x, 6) for x in base_points))
    force = 0.0
    moment = 0.0
    for d1, d2 in zip(samples, samples[1:]):
        p1 = _pressure_at(profile, d1)
        p2 = _pressure_at(profile, d2)
        if net_with is not None:
            p1 = max(0.0, p1 - _pressure_at(net_with, d1))
            p2 = max(0.0, p2 - _pressure_at(net_with, d2))
        f = 0.5 * (p1 + p2) * (d2 - d1)
        # centroid by midpoint is sufficient for this screening integration with short segments.
        d_mid = 0.5 * (d1 + d2)
        lever = max(0.0, toe_depth - d_mid)
        force += f
        moment += f * lever
    return force, moment


def check_embedment_stability(
    object_id: str,
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    wall_bottom_elevation: float,
    top_elevation: float,
    groundwater_level_outside: float,
    groundwater_level_inside: float | None,
    surcharge: float,
    safety_grade: str,
) -> tuple[CheckResult, dict[str, Any]]:
    wall_depth = top_elevation - wall_bottom_elevation
    if wall_depth <= excavation_depth:
        check = CheckResult(
            rule_id=JGJ120_EMBEDMENT_RULE.rule_id,
            object_id=object_id,
            object_type="DiaphragmWallPanel",
            status="fail",
            calculated_value=0.0,
            limit_value=required_embedment_factor(safety_grade),
            unit="-",
            message="墙底未低于坑底，嵌固深度不足。",
            clause_reference=JGJ120_EMBEDMENT_RULE.clause_reference,
        )
        return check, {"wallDepth": wall_depth, "excavationDepth": excavation_depth}
    gw_inside = groundwater_level_inside if groundwater_level_inside is not None else min(groundwater_level_outside, top_elevation - excavation_depth)
    active = calculate_jgj120_lateral_pressure_profile(soil_profile, wall_depth, groundwater_level_outside, surcharge, top_elevation, step=0.25, mode="active")
    passive = calculate_jgj120_lateral_pressure_profile(soil_profile, wall_depth, gw_inside, 0.0, top_elevation, step=0.25, mode="passive")
    active_force, active_moment = _integrate_moment(active, 0.0, excavation_depth, wall_depth)
    resistance_force, resistance_moment = _integrate_moment(passive, excavation_depth, wall_depth, wall_depth, net_with=active)
    factor = resistance_moment / active_moment if active_moment > 1e-9 else 999.0
    limit = required_embedment_factor(safety_grade)
    status = "pass" if factor >= limit else "fail"
    check = CheckResult(
        rule_id=JGJ120_EMBEDMENT_RULE.rule_id,
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status=status,
        calculated_value=round(factor, 3),
        limit_value=limit,
        unit="-",
        message=("嵌固稳定筛查通过。" if status == "pass" else "嵌固稳定筛查未通过，应增加嵌固深度或调整支护体系。") + " 该项为朗肯压力抗力矩/作用矩筛查，正式公式、被动土折减、承压水和施工阶段需按规范原文复核。",
        clause_reference=JGJ120_EMBEDMENT_RULE.clause_reference,
    )
    trace = {
        "ruleId": JGJ120_EMBEDMENT_RULE.rule_id,
        "wallDepthM": round(wall_depth, 3),
        "excavationDepthM": round(excavation_depth, 3),
        "embedmentDepthM": round(wall_depth - excavation_depth, 3),
        "activeForceKnPerM": round(active_force, 3),
        "activeMomentKnMPerM": round(active_moment, 3),
        "netPassiveForceKnPerM": round(resistance_force, 3),
        "netPassiveMomentKnMPerM": round(resistance_moment, 3),
        "factor": round(factor, 3),
        "limit": limit,
    }
    return check, trace


def check_diaphragm_wall_construction(
    object_id: str,
    thickness_m: float,
    concrete_grade: str,
    main_bar_diameter_mm: float | None,
    main_bar_spacing_mm: float | None,
    horizontal_bar_diameter_mm: float | None,
    horizontal_bar_spacing_mm: float | None,
    inner_cover_mm: float = 50.0,
    outer_cover_mm: float = 70.0,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    allowed = [0.6, 0.8, 1.0, 1.2]
    thickness_status = "pass" if any(abs(thickness_m - item) < 1e-6 for item in allowed) else "warning"
    checks.append(CheckResult(
        rule_id=JGJ120_DIAPHRAGM_CONSTRUCTION_RULE.rule_id + "-THK",
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status=thickness_status,
        calculated_value=round(thickness_m, 3),
        limit_value=None,
        unit="m",
        message="地下连续墙厚度宜采用 600/800/1000/1200mm 常用规格；特殊设备墙厚需专项说明。",
        clause_reference="4.5.2",
    ))
    grade_num = int(concrete_grade.upper().replace("C", "")) if concrete_grade.upper().startswith("C") else 0
    checks.append(CheckResult(
        rule_id=JGJ120_DIAPHRAGM_CONSTRUCTION_RULE.rule_id + "-CONC",
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status="pass" if 30 <= grade_num <= 40 else "warning",
        calculated_value=float(grade_num),
        limit_value=30.0,
        unit="grade",
        message="地下连续墙混凝土强度等级宜取 C30~C40；用于截水时还应校核抗渗等级。",
        clause_reference="4.5.5",
    ))
    checks.append(CheckResult(
        rule_id=JGJ120_DIAPHRAGM_CONSTRUCTION_RULE.rule_id + "-MAINBAR",
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status="pass" if (main_bar_diameter_mm or 0) >= 16 and (main_bar_spacing_mm or 999) - (main_bar_diameter_mm or 0) >= 75 else "fail",
        calculated_value=main_bar_spacing_mm,
        limit_value=75.0,
        unit="mm clear spacing",
        message="纵向受力钢筋直径不宜小于16mm，净距不宜小于75mm；通长比例、锚固和分段配筋需详图复核。",
        clause_reference="4.5.6",
    ))
    checks.append(CheckResult(
        rule_id=JGJ120_DIAPHRAGM_CONSTRUCTION_RULE.rule_id + "-HORIZONTAL",
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status="pass" if (horizontal_bar_diameter_mm or 0) >= 12 and 200 <= (horizontal_bar_spacing_mm or 0) <= 400 else "warning",
        calculated_value=horizontal_bar_spacing_mm,
        limit_value=400.0,
        unit="mm",
        message="水平钢筋及构造筋直径不宜小于12mm，水平筋间距宜为200~400mm。",
        clause_reference="4.5.6",
    ))
    checks.append(CheckResult(
        rule_id=JGJ120_DIAPHRAGM_CONSTRUCTION_RULE.rule_id + "-COVER",
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status="pass" if inner_cover_mm >= 50 and outer_cover_mm >= 70 else "fail",
        calculated_value=min(inner_cover_mm, outer_cover_mm),
        limit_value=50.0,
        unit="mm",
        message="基坑内侧保护层厚度不宜小于50mm，外侧不宜小于70mm。",
        clause_reference="4.5.7",
    ))
    return checks


def wall_design_summary(max_moment: float | None, max_shear: float | None, max_displacement: float | None) -> dict[str, Any]:
    return {
        "standardSubset": [JGJ120_EMBEDMENT_RULE.rule_id, JGJ120_DIAPHRAGM_CONSTRUCTION_RULE.rule_id],
        "maxMoment": max_moment,
        "maxShear": max_shear,
        "maxDisplacement": max_displacement,
        "reviewRequired": True,
    }


def importance_factor(safety_grade: str) -> float:
    """Action amplification/importance coefficient used by preliminary checks."""
    if "一" in (safety_grade or "") or "1" in (safety_grade or ""):
        return 1.10
    if "三" in (safety_grade or "") or "3" in (safety_grade or ""):
        return 0.90
    return 1.00


def safety_grade_factor(safety_grade: str) -> tuple[float, float, float]:
    return importance_factor(safety_grade), 1.25, required_embedment_factor(safety_grade)
