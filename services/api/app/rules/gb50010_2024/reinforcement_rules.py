from __future__ import annotations

from app.rules.gb50010_2024.rc_section_rules import (
    design_rectangular_flexural_reinforcement,
    design_rectangular_shear_reinforcement,
)
from app.schemas.domain import ReinforcementGroup


def design_wall_reinforcement_groups(
    thickness_m: float,
    max_moment_design_knm_per_m: float | None,
    max_shear_design_kn_per_m: float | None,
    concrete_grade: str = "C35",
    rebar_grade: str = "HRB400",
) -> tuple[list[ReinforcementGroup], dict]:
    flex = design_rectangular_flexural_reinforcement(max_moment_design_knm_per_m or 0.0, thickness_m, concrete_grade, rebar_grade)
    shear = design_rectangular_shear_reinforcement(max_shear_design_kn_per_m or 0.0, thickness_m, concrete_grade, rebar_grade)
    groups = [
        ReinforcementGroup(name="坑内侧竖向主筋", bar_type="longitudinal", diameter=flex.selected_diameter_mm, spacing=flex.selected_spacing_mm, grade=rebar_grade, location_description="inner face vertical bars designed by rectangular flexural strip"),
        ReinforcementGroup(name="坑外侧竖向主筋", bar_type="longitudinal", diameter=flex.selected_diameter_mm, spacing=flex.selected_spacing_mm, grade=rebar_grade, location_description="outer face vertical bars designed by rectangular flexural strip"),
        ReinforcementGroup(name="水平分布筋", bar_type="distribution", diameter=16, spacing=200, grade=rebar_grade, location_description="horizontal distribution bars; detailing review required"),
        ReinforcementGroup(name="拉结/构造筋", bar_type="tie", diameter=shear.selected_diameter_mm, spacing=shear.selected_spacing_mm, grade=rebar_grade, location_description="two-leg ties/stirrups equivalent for shear and cage integrity"),
    ]
    summary = {
        "requiredAsMm2PerM": round(flex.required_as_mm2_per_m, 1),
        "minimumAsMm2PerM": round(flex.minimum_as_mm2_per_m, 1),
        "providedAsMm2PerM": round(flex.provided_as_mm2_per_m, 1),
        "flexuralUtilization": round(flex.utilization, 3),
        "shearUtilization": round(shear.utilization, 3),
        "flexuralStatus": flex.status,
        "shearStatus": shear.status,
        "message": f"{flex.message} {shear.message}",
    }
    return groups, summary


def design_support_reinforcement_groups(width_m: float | None, height_m: float | None, axial_design_kn: float | None, rebar_grade: str = "HRB400") -> tuple[list[ReinforcementGroup], dict]:
    width = width_m or 0.8
    height = height_m or 0.8
    axial = axial_design_kn or 0.0
    if axial < 3000:
        count, dia, stirrup_spacing = 8, 25, 150
    elif axial < 6000:
        count, dia, stirrup_spacing = 12, 28, 120
    else:
        count, dia, stirrup_spacing = 16, 32, 100
    groups = [
        ReinforcementGroup(name="支撑纵筋", bar_type="longitudinal", diameter=dia, count=count, grade=rebar_grade, location_description=f"RC support longitudinal bars for {width:.2f}m x {height:.2f}m section"),
        ReinforcementGroup(name="支撑箍筋", bar_type="stirrup", diameter=12, spacing=stirrup_spacing, grade=rebar_grade, location_description="RC support stirrups; node anchorage review required"),
    ]
    summary = {"barCount": count, "barDiameter": dia, "stirrupSpacing": stirrup_spacing, "axialDesignKn": round(axial, 1)}
    return groups, summary
