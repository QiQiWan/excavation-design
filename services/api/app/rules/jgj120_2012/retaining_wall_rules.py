from __future__ import annotations

from typing import Any

from app.rules.base import CheckResult, DesignRule
from app.rules.jgj120_2012.earth_pressure_rules import calculate_jgj120_lateral_pressure_profile
from app.schemas.domain import GeologicalLayer, PressureProfile

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
    pressure_profile: PressureProfile | None = None,
    pressure_method: str = "active",
    water_soil_method: str = "separate",
) -> tuple[CheckResult, dict[str, Any]]:
    if excavation_depth <= 0.0:
        return CheckResult(
            rule_id=JGJ120_EMBEDMENT_RULE.rule_id, object_id=object_id,
            object_type="DiaphragmWallPanel", status="manual_review", calculated_value=None,
            limit_value=required_embedment_factor(safety_grade), unit="-",
            message="当前阶段未开挖，嵌固筛查无开挖驱动力，安全系数不适用。",
            clause_reference=JGJ120_EMBEDMENT_RULE.clause_reference,
        ), {"factor": None, "applicability": "not_applicable", "methodStatus": "unverified_screening", "formalUseAllowed": False}
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
    if water_soil_method != "separate":
        raise ValueError("Embedment screening requires supported separate water/soil pressure")
    gw_inside = groundwater_level_inside if groundwater_level_inside is not None else min(groundwater_level_outside, top_elevation - excavation_depth)
    shared = pressure_profile
    if shared is None:
        shared = calculate_jgj120_lateral_pressure_profile(
            soil_profile, excavation_depth, groundwater_level_outside, surcharge, top_elevation,
            step=0.25, mode=pressure_method, groundwater_level_inside=gw_inside,
            calculation_depth=wall_depth,
        )
    pit_bottom = top_elevation - excavation_depth
    if all(point.active_earth_pressure is not None and point.passive_earth_pressure is not None for point in shared.points):
        # Component fields are soil-only. Add each water side once; the frozen
        # net total cannot be reused as the outside driving pressure.
        active = shared.model_copy(update={"points": [
            point.model_copy(update={"total_pressure": point.active_earth_pressure + (point.outside_water_pressure or 0.0)})
            for point in shared.points
        ]})
        driving = shared.model_copy(update={"points": [
            point.model_copy(update={"total_pressure": max(0.0, point.active_earth_pressure + (point.outside_water_pressure or 0.0) - (point.inside_water_pressure or 0.0))})
            for point in shared.points
        ]})
        passive = shared.model_copy(update={"points": [
            point.model_copy(update={"total_pressure": point.passive_earth_pressure + (point.inside_water_pressure or 0.0)})
            for point in shared.points
        ]})
    else:
        # Legacy caller profiles without decomposition are outside pressure.
        # Build only the missing pit-side resistance from the local soil slice.
        active = driving = shared
        embedded_layers = [
            layer.model_copy(update={"top_elevation": min(layer.top_elevation, pit_bottom),
                                     "thickness": min(layer.top_elevation, pit_bottom) - layer.bottom_elevation})
            for layer in soil_profile if layer.bottom_elevation < pit_bottom
        ]
        passive_local = calculate_jgj120_lateral_pressure_profile(
            embedded_layers, wall_depth - excavation_depth, gw_inside, 0.0,
            pit_bottom, step=0.25, mode="passive"
        )
        passive = passive_local.model_copy(update={"points": [
            point.model_copy(update={"depth": point.depth + excavation_depth})
            for point in passive_local.points
        ]})
    active_force, active_moment = _integrate_moment(driving, 0.0, excavation_depth, wall_depth)
    resistance_force, resistance_moment = _integrate_moment(passive, excavation_depth, wall_depth, wall_depth, net_with=active)
    factor = resistance_moment / active_moment if active_moment > 1e-9 else None
    limit = required_embedment_factor(safety_grade)
    status = "manual_review" if factor is None or factor >= limit else "fail"
    check = CheckResult(
        rule_id=JGJ120_EMBEDMENT_RULE.rule_id,
        object_id=object_id,
        object_type="DiaphragmWallPanel",
        status=status,
        calculated_value=round(factor, 3) if factor is not None else None,
        limit_value=limit,
        unit="-",
        message=("当前压力无驱动力，嵌固筛查安全系数不适用。" if factor is None else ("嵌固筛查数值达到阈值，但方法未经验证，不能确认规范验算通过。" if status == "manual_review" else "嵌固稳定筛查未通过，应增加嵌固深度或调整支护体系。") + " 该项为朗肯压力抗力矩/作用矩筛查，正式公式、被动土折减、承压水和施工阶段需按规范原文复核。"),
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
        "factor": round(factor, 3) if factor is not None else None,
        "applicability": "applicable" if factor is not None else "not_applicable",
        "limit": limit,
        "methodStatus": "unverified_screening",
        "formalUseAllowed": False,
        "passiveOriginDepthM": excavation_depth,
        "passiveOriginElevationM": pit_bottom,
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
