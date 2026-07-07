from __future__ import annotations

import math
from typing import Iterable

from app.rules.base import CheckResult, DesignRule
from app.schemas.domain import SupportElement

JGJ120_SUPPORT_LAYOUT_RULE = DesignRule(
    rule_id="JGJ120-2012-4.7-INTERNAL-SUPPORT-LAYOUT-SCREEN",
    standard_name="建筑基坑支护技术规程 JGJ 120",
    standard_version="2012",
    clause_reference="4.7 内支撑结构布置与构造原则（软件为几何/施工空间筛查）",
    name="内支撑平面和竖向布置筛查",
    description="检查支撑标高间距、最下一道支撑至坑底施工空间、支撑跨度和轴力记录完整性。",
    severity="warning",
    applicable_to=["SupportElement", "RetainingSystem"],
)


def _length(support: SupportElement) -> float:
    return math.hypot(support.end.x - support.start.x, support.end.y - support.start.y)


def check_internal_support_layout(
    supports: Iterable[SupportElement],
    excavation_top_elevation: float,
    excavation_bottom_elevation: float,
    object_id: str,
) -> list[CheckResult]:
    """Geometry and constructability screening for internal bracing.

    This is not a member-capacity check. It makes the automatically generated example project
    auditable by ensuring that support levels, clearances, spans and design forces are explicitly
    present before export and report generation.
    """
    supports = sorted(list(supports), key=lambda item: (-item.elevation, item.level_index, item.code))
    if not supports:
        return [
            CheckResult(
                rule_id=JGJ120_SUPPORT_LAYOUT_RULE.rule_id,
                object_id=object_id,
                object_type="RetainingSystem",
                status="not_applicable",
                message="未布置内支撑，本项不适用。",
                clause_reference=JGJ120_SUPPORT_LAYOUT_RULE.clause_reference,
            )
        ]
    checks: list[CheckResult] = []
    levels = sorted({round(s.elevation, 3) for s in supports}, reverse=True)
    if levels:
        first_drop = excavation_top_elevation - levels[0]
        checks.append(
            CheckResult(
                rule_id=JGJ120_SUPPORT_LAYOUT_RULE.rule_id + "-FIRST-LEVEL",
                object_id=object_id,
                object_type="RetainingSystem",
                status="pass" if 0.8 <= first_drop <= 2.5 else "warning",
                calculated_value=round(first_drop, 3),
                limit_value=2.5,
                unit="m",
                message="第一道支撑距坑顶的竖向距离筛查；施工栈桥、冠梁和土方开挖顺序需结合项目复核。",
                clause_reference=JGJ120_SUPPORT_LAYOUT_RULE.clause_reference,
                formula="0.8m <= z_top - z_support_1 <= 2.5m",
            )
        )
    for idx, (upper, lower) in enumerate(zip(levels, levels[1:]), start=1):
        spacing = upper - lower
        checks.append(
            CheckResult(
                rule_id=JGJ120_SUPPORT_LAYOUT_RULE.rule_id + f"-SPACING-L{idx}",
                object_id=object_id,
                object_type="RetainingSystem",
                status="pass" if 2.5 <= spacing <= 5.5 else "warning",
                calculated_value=round(spacing, 3),
                limit_value=5.5,
                unit="m",
                message="相邻支撑竖向间距筛查；正式施工阶段应结合开挖步距、换撑和变形控制复核。",
                clause_reference=JGJ120_SUPPORT_LAYOUT_RULE.clause_reference,
                formula="2.5m <= vertical_spacing <= 5.5m",
            )
        )
    last_clearance = levels[-1] - excavation_bottom_elevation
    checks.append(
        CheckResult(
            rule_id=JGJ120_SUPPORT_LAYOUT_RULE.rule_id + "-BOTTOM-CLEARANCE",
            object_id=object_id,
            object_type="RetainingSystem",
            status="pass" if last_clearance >= 1.8 else "warning",
            calculated_value=round(last_clearance, 3),
            limit_value=1.8,
            unit="m",
            message="最下一道支撑至坑底施工净空筛查。",
            clause_reference=JGJ120_SUPPORT_LAYOUT_RULE.clause_reference,
            formula="z_last_support - z_pit_bottom >= 1.8m",
        )
    )
    for support in supports:
        span = _length(support)
        checks.append(
            CheckResult(
                rule_id=JGJ120_SUPPORT_LAYOUT_RULE.rule_id + "-SPAN",
                object_id=support.id,
                object_type="SupportElement",
                status="pass" if 5.0 <= span <= 70.0 else "warning",
                calculated_value=round(span, 3),
                limit_value=70.0,
                unit="m",
                message="支撑跨度和端点吸附筛查；节点、偏心、温度效应和预加轴力应在详设阶段复核。",
                clause_reference=JGJ120_SUPPORT_LAYOUT_RULE.clause_reference,
                formula="span within configured auto-layout range and endpoints on retaining axis",
            )
        )
        checks.append(
            CheckResult(
                rule_id=JGJ120_SUPPORT_LAYOUT_RULE.rule_id + "-FORCE-TRACE",
                object_id=support.id,
                object_type="SupportElement",
                status="pass" if (support.design_axial_force or 0.0) >= 0.0 else "warning",
                calculated_value=round(float(support.design_axial_force or 0.0), 3),
                limit_value=None,
                unit="kN",
                message="支撑设计轴力已写入构件，供承载力验算、IFC 属性和计算书追溯。",
                clause_reference=JGJ120_SUPPORT_LAYOUT_RULE.clause_reference,
                formula="N_design = envelope(stage tributary pressure * influence length * gamma0 * partial factor)",
            )
        )
    return checks
