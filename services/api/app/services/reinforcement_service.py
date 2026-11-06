from __future__ import annotations

from app.rules.gb50010.rc_section_rules import (
    as_per_m_for_spacing,
    bar_area,
    design_rectangular_flexure,
)
from app.rules.gb50010.reinforcement_rules import recommend_bar_spacing
from app.schemas.domain import ReinforcementGroup


def _select_bar_spacing(required_as_per_m: float, preferred_diameters: tuple[int, ...] = (22, 25, 28, 32, 36)) -> tuple[int, int, float]:
    dia, spacing, provided = recommend_bar_spacing(required_as_per_m, preferred_diameters=preferred_diameters)
    return int(dia), int(spacing), float(provided)


def diaphragm_wall_reinforcement(
    thickness: float,
    max_moment_design: float | None = None,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
    target_safety_factor: float = 1.0,
) -> list[ReinforcementGroup]:
    """Generate traceable GB 50010-oriented reinforcement suggestions per metre of wall.

    ``max_moment_design`` is kN*m/m for a 1 m wide wall strip.  The returned groups are
    design-assist recommendations, not final detailing: crack width, anchorage, splice,
    construction joint, cage lifting and node checks remain professional review items.
    """
    base_design = design_rectangular_flexure(
        moment_design_knm_per_m=max_moment_design or 0.0,
        thickness_m=thickness,
        concrete_grade=concrete_grade,
        rebar_grade=rebar_grade,
        cover_mm=70.0,
    )
    reserve_design = design_rectangular_flexure(
        moment_design_knm_per_m=(max_moment_design or 0.0) * max(float(target_safety_factor or 1.0), 1.0),
        thickness_m=thickness,
        concrete_grade=concrete_grade,
        rebar_grade=rebar_grade,
        cover_mm=70.0,
    )
    dia, spacing, provided = _select_bar_spacing(reserve_design.governing_as)
    distribution_dia = 16 if thickness < 1.2 else 18
    distribution_spacing = 200 if thickness < 1.2 else 180
    return [
        ReinforcementGroup(
            name="坑内侧竖向主筋",
            bar_type="longitudinal",
            diameter=dia,
            spacing=spacing,
            grade=rebar_grade,
            area_per_meter=round(provided, 2),
            required_area_per_meter=round(base_design.governing_as, 2),
            check_status="pass" if provided >= reserve_design.governing_as else "fail",
            location_description=(
                f"inner face vertical bars; As_provided={provided:.0f}mm2/m; "
                f"As_required={base_design.governing_as:.0f}mm2/m; "
                f"reserve target={max(float(target_safety_factor or 1.0), 1.0):.2f}; GB50010 flexure/min-rebar subset"
            ),
        ),
        ReinforcementGroup(
            name="坑外侧竖向主筋",
            bar_type="longitudinal",
            diameter=dia,
            spacing=spacing,
            grade=rebar_grade,
            area_per_meter=round(provided, 2),
            required_area_per_meter=round(base_design.governing_as, 2),
            check_status="pass" if provided >= reserve_design.governing_as else "fail",
            location_description="坑外侧竖向主筋；方案阶段按双面对称钢筋笼考虑弯矩反向与各施工阶段作用",
        ),
        ReinforcementGroup(
            name="水平分布筋",
            bar_type="distribution",
            diameter=distribution_dia,
            spacing=distribution_spacing,
            grade=rebar_grade,
            area_per_meter=round(as_per_m_for_spacing(distribution_dia, distribution_spacing), 2),
            check_status="manual_review",
            location_description="墙体水平分布筋；裂缝与节点构造由专业工程师复核",
        ),
        ReinforcementGroup(
            name="拉结筋/架立筋",
            bar_type="tie",
            diameter=12,
            spacing=450,
            grade=rebar_grade,
            check_status="manual_review",
            location_description="连接两侧钢筋网的拉结筋；间距结合钢筋笼制作和吊装复核",
        ),
    ]


def support_reinforcement(
    section_width: float | None,
    section_height: float | None,
    axial_force: float | None = None,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
) -> list[ReinforcementGroup]:
    width = (section_width or 0.8) * 1000.0
    height = (section_height or 0.8) * 1000.0
    force = axial_force or 0.0
    # Rough axial demand tiers. Final compression + bending + slenderness design is checked separately/manual.
    if force < 3500:
        count, dia = 8, 25
    elif force < 6500:
        count, dia = 12, 28
    else:
        count, dia = 16, 32
    as_total = count * bar_area(dia)
    rho = as_total / max(width * height, 1.0)
    stirrup_spacing = 150 if force < 6500 else 100
    distribution_dia = 14 if min(width, height) < 900 else 16
    distribution_spacing = 200 if force < 6500 else 180
    tie_spacing = 450 if force < 6500 else 400
    lap_dia = max(16, dia - 6)
    return [
        ReinforcementGroup(
            name="支撑纵筋",
            bar_type="longitudinal",
            diameter=dia,
            count=count,
            grade=rebar_grade,
            area_per_meter=round(as_total, 2),
            check_status="preliminary",
            location_description=(
                f"RC support longitudinal bars; As={as_total:.0f}mm2, rho={rho*100:.2f}%; "
                f"concrete={concrete_grade}; axial-flexure-slenderness requires full check"
            ),
        ),
        ReinforcementGroup(
            name="支撑箍筋",
            bar_type="stirrup",
            diameter=12,
            spacing=stirrup_spacing,
            grade=rebar_grade,
            check_status="manual_review",
            location_description="钢筋混凝土支撑箍筋；抗剪、约束及端部加密构造需专业复核",
        ),
        ReinforcementGroup(
            name="支撑分布筋",
            bar_type="distribution",
            diameter=distribution_dia,
            spacing=distribution_spacing,
            grade=rebar_grade,
            check_status="manual_review",
            location_description="支撑侧面连续构造分布筋，用于控制裂缝并稳定钢筋骨架",
        ),
        ReinforcementGroup(
            name="支撑拉结/架立筋",
            bar_type="tie",
            diameter=12,
            spacing=tie_spacing,
            grade=rebar_grade,
            check_status="manual_review",
            location_description="纵筋骨架之间的拉结筋，用于保持净距和施工阶段骨架稳定",
        ),
        ReinforcementGroup(
            name="搭接加强筋",
            bar_type="additional",
            diameter=lap_dia,
            count=4,
            grade=rebar_grade,
            check_status="manual_review",
            location_description="错开搭接及锚固区附加筋；搭接长度与弯钩形式在深化设计中复核",
        ),
    ]
