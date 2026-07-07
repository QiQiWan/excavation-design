from __future__ import annotations

import math
from typing import Literal

from app.rules.jgj120_2012.earth_pressure_rules import (
    GAMMA_WATER,
    at_rest_coefficient,
    calculate_jgj120_pressure_profile,
    rankine_active_coefficient,
    rankine_passive_coefficient,
)
from app.schemas.domain import GeologicalLayer, PressureProfile

GAMMA_W = GAMMA_WATER


def earth_pressure_coefficients(phi_deg: float, k0: float | None = None) -> tuple[float, float, float]:
    """Return Rankine active/passive and Jaky/explicit at-rest coefficients."""
    return (
        rankine_active_coefficient(phi_deg),
        rankine_passive_coefficient(phi_deg),
        at_rest_coefficient(phi_deg, k0),
    )


def calculate_lateral_pressure_profile(
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    groundwater_level: float,
    surcharge: float,
    top_elevation: float = 0.0,
    step: float = 0.5,
    mode: Literal["active", "passive", "at_rest"] | str = "active",
    water_soil_method: Literal["separate", "combined"] | str = "separate",
    groundwater_level_inside: float | None = None,
    calculation_depth: float | None = None,
) -> PressureProfile:
    """Calculate lateral pressure using the central JGJ 120-oriented rules helper.

    Depth/elevation are in metres and pressure is in kPa.  The implementation
    follows a transparent engineering workflow: layered total/effective vertical
    stress integration, Rankine active/passive earth pressure, optional at-rest
    coefficient and hydrostatic water pressure.  It is deliberately kept in the
    calculation layer so UI code never embeds professional formulae.
    """
    if excavation_depth <= 0:
        raise ValueError("excavation_depth must be positive")
    if step <= 0:
        raise ValueError("step must be positive")
    use_at_rest = mode == "at_rest"
    profile = calculate_jgj120_pressure_profile(
        soil_profile=soil_profile,
        excavation_depth=excavation_depth,
        groundwater_level_outside=groundwater_level,
        groundwater_level_inside=groundwater_level_inside,
        surcharge=surcharge,
        top_elevation=top_elevation,
        step=step,
        calculation_depth=calculation_depth,
        use_at_rest=use_at_rest,
    )
    if water_soil_method == "combined":
        # Keep compatibility with earlier API setting: combined means total stress
        # treatment, so do not add a separate displayed water component.  The
        # profile remains conservative because earth pressure already contains
        # the total stress contribution in the underlying helper.
        profile = profile.model_copy(
            update={
                "points": [
                    p.model_copy(update={"water_pressure": 0.0, "total_pressure": p.earth_pressure})
                    for p in profile.points
                ],
                "warnings": profile.warnings + ["已按水土合算显示压力；正式设计需确认适用土类和地下水条件。"],
            }
        )
    if mode == "passive":
        # A passive-only profile is mainly used by isolated checks.  Rebuild from
        # coefficient fields without subtracting inside resistance.
        passive_points = []
        for p in profile.points:
            kp = p.kp or rankine_passive_coefficient(p.friction_angle or 30.0)
            c = p.cohesion or 0.0
            sig = p.vertical_stress_effective or 0.0
            passive = max(0.0, kp * sig + 2.0 * c * math.sqrt(max(kp, 0.0)))
            passive_points.append(
                p.model_copy(
                    update={
                        "earth_pressure": round(passive, 6),
                        "active_earth_pressure": None,
                        "passive_earth_pressure": round(passive, 6),
                        "total_pressure": round(passive + (p.water_pressure or 0.0), 6),
                        "method": "JGJ120-2012 Rankine passive pressure profile",
                    }
                )
            )
        profile = profile.model_copy(update={"points": passive_points})
    return profile
