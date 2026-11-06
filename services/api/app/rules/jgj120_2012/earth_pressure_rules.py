from __future__ import annotations

import math
from dataclasses import dataclass

from app.schemas.domain import GeologicalLayer, PressurePoint, PressureProfile, SoilParameters

GAMMA_WATER = 9.81


@dataclass(frozen=True)
class SoilStateAtDepth:
    layer: GeologicalLayer | None
    total_vertical_stress: float
    outside_water_pressure: float
    inside_water_pressure: float
    effective_vertical_stress: float


def rankine_active_coefficient(phi_deg: float) -> float:
    """Rankine active coefficient: Ka = tan^2(45 deg - phi/2)."""
    phi = max(min(phi_deg, 89.0), 0.0)
    return math.tan(math.radians(45.0 - phi / 2.0)) ** 2


def rankine_passive_coefficient(phi_deg: float) -> float:
    """Rankine passive coefficient: Kp = tan^2(45 deg + phi/2)."""
    phi = max(min(phi_deg, 89.0), 0.0)
    return math.tan(math.radians(45.0 + phi / 2.0)) ** 2


def at_rest_coefficient(phi_deg: float, k0: float | None = None) -> float:
    if k0 is not None and k0 > 0:
        return k0
    phi = max(min(phi_deg, 89.0), 0.0)
    return max(0.0, 1.0 - math.sin(math.radians(phi)))


def _layer_at_depth(layers: list[GeologicalLayer], top_elevation: float, depth: float) -> GeologicalLayer | None:
    elevation = top_elevation - depth
    for layer in layers:
        if layer.top_elevation + 1e-9 >= elevation >= layer.bottom_elevation - 1e-9:
            return layer
    return layers[-1] if layers else None


def _unit_weight(params: SoilParameters | None, below_groundwater: bool) -> float:
    if params is None:
        return 18.0
    if below_groundwater and params.saturated_unit_weight:
        return params.saturated_unit_weight
    if params.unit_weight:
        return params.unit_weight
    return 18.0


def water_pressure_at(elevation: float, groundwater_level: float | None) -> float:
    if groundwater_level is None:
        return 0.0
    return max(0.0, GAMMA_WATER * (groundwater_level - elevation))


def total_vertical_stress_at_depth(
    layers: list[GeologicalLayer],
    top_elevation: float,
    depth: float,
    groundwater_level: float | None,
    surcharge: float = 0.0,
    step: float = 0.1,
) -> float:
    """Integrate total vertical stress through a stratified one-dimensional section."""
    # Integrate exactly between layer/water boundaries. Include ponded water
    # weight at the soil surface so submerged effective stress stays coherent.
    stress = surcharge + water_pressure_at(top_elevation, groundwater_level)
    if depth <= 0:
        return stress
    boundaries = {0.0, depth}
    for layer in layers:
        boundaries.update(d for d in (top_elevation-layer.top_elevation, top_elevation-layer.bottom_elevation) if 0 < d < depth)
    if groundwater_level is not None and 0 < top_elevation-groundwater_level < depth:
        boundaries.add(top_elevation-groundwater_level)
    depths = sorted(boundaries)
    for start, end in zip(depths, depths[1:]):
        mid_depth = (start + end) / 2
        elevation = top_elevation - mid_depth
        layer = _layer_at_depth(layers, top_elevation, mid_depth)
        below_gw = groundwater_level is not None and elevation < groundwater_level
        stress += _unit_weight(layer.parameters if layer else None, below_gw) * (end-start)
    return stress


def active_pressure_jgj120(
    sigma_total: float,
    water_pressure: float,
    cohesion: float,
    phi_deg: float,
    separate_water_soil: bool = True,
) -> tuple[float, float]:
    """Return active total lateral pressure and Ka.

    Soil tension is cut off before adding water: max(Ka*sigma'-2c*sqrt(Ka), 0)+u.
    Total-stress strength/drainage parameters are not represented in this schema.
    """
    ka = rankine_active_coefficient(phi_deg)
    if separate_water_soil:
        sigma_eff = max(0.0, sigma_total - water_pressure)
        pressure = max(0.0, ka * sigma_eff - 2.0 * cohesion * math.sqrt(max(ka, 0.0))) + water_pressure
    else:
        raise ValueError("Unsupported total-stress method: independently verified total-stress parameters required")
    return max(0.0, pressure), ka


def passive_pressure_jgj120(
    sigma_total: float,
    water_pressure: float,
    cohesion: float,
    phi_deg: float,
    separate_water_soil: bool = True,
) -> tuple[float, float]:
    """Return passive total lateral pressure and Kp."""
    kp = rankine_passive_coefficient(phi_deg)
    if separate_water_soil:
        sigma_eff = max(0.0, sigma_total - water_pressure)
        pressure = kp * sigma_eff + 2.0 * cohesion * math.sqrt(max(kp, 0.0)) + water_pressure
    else:
        raise ValueError("Unsupported total-stress method: independently verified total-stress parameters required")
    return max(0.0, pressure), kp


def calculate_jgj120_pressure_profile(
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    groundwater_level_outside: float | None,
    groundwater_level_inside: float | None = None,
    surcharge: float = 0.0,
    top_elevation: float = 0.0,
    step: float = 0.5,
    calculation_depth: float | None = None,
    use_at_rest: bool = False,
) -> PressureProfile:
    """Calculate a traceable JGJ 120-oriented lateral-pressure profile.

    The implementation covers the engineering subset needed by the prototype: layered vertical-stress
    integration, Rankine active/passive coefficients, effective-stress water-soil split and hydrostatic
    water-pressure difference.  It does not replace finite-wedge or project-specific code calculations.
    """
    values = dict(excavation_depth=excavation_depth, step=step, surcharge=surcharge, top_elevation=top_elevation,
                  calculation_depth=calculation_depth, groundwater_level_outside=groundwater_level_outside,
                  groundwater_level_inside=groundwater_level_inside)
    for name, value in values.items():
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if excavation_depth < 0 or surcharge < 0 or step <= 0:
        raise ValueError("excavation_depth and surcharge must be nonnegative; step must be positive")
    calc_depth = excavation_depth if calculation_depth is None else calculation_depth
    if calc_depth <= 0 or calc_depth < excavation_depth:
        raise ValueError("calculation_depth must be positive and at least excavation_depth")
    for layer in soil_profile:
        if not all(math.isfinite(v) for v in (layer.top_elevation, layer.bottom_elevation, layer.thickness)):
            raise ValueError("soil layer geometry must be finite")
        if layer.top_elevation <= layer.bottom_elevation or layer.thickness <= 0:
            raise ValueError("soil layer must have positive thickness")
        for name, value in layer.parameters.model_dump().items():
            if value is not None and not math.isfinite(value):
                raise ValueError(f"soil parameter {name} must be finite")
        params = layer.parameters
        if params.cohesion is not None and params.cohesion < 0:
            raise ValueError("cohesion must be nonnegative")
        if params.friction_angle is not None and not 0 <= params.friction_angle < 89:
            raise ValueError("friction_angle must be in [0, 89) degrees")
        if params.k0 is not None and params.k0 <= 0:
            raise ValueError("k0 must be positive")
        if any(v is not None and v <= 0 for v in (params.unit_weight, params.saturated_unit_weight)):
            raise ValueError("unit weights must be positive")
    inside_gw = groundwater_level_inside
    if inside_gw is None and groundwater_level_outside is not None:
        inside_gw = min(groundwater_level_outside, top_elevation - excavation_depth)
    point_count = max(1, int(math.ceil(calc_depth / step))) + 1
    points: list[PressurePoint] = []
    for i in range(point_count):
        depth = min(i * step, calc_depth)
        elevation = top_elevation - depth
        layer = _layer_at_depth(soil_profile, top_elevation, depth)
        params = layer.parameters if layer else SoilParameters()
        cohesion = params.cohesion if params.cohesion is not None else 0.0
        phi = params.friction_angle if params.friction_angle is not None else 30.0
        sigma_out = total_vertical_stress_at_depth(soil_profile, top_elevation, depth, groundwater_level_outside, surcharge)
        u_out = water_pressure_at(elevation, groundwater_level_outside)
        if use_at_rest:
            ka = at_rest_coefficient(phi, params.k0)
            active_total = max(0.0, ka * max(0.0, sigma_out - u_out) + u_out)
        else:
            active_total, ka = active_pressure_jgj120(sigma_out, u_out, cohesion, phi, separate_water_soil=True)
        u_in = water_pressure_at(elevation, inside_gw)
        if depth > excavation_depth:
            inside_depth = depth - excavation_depth
            inside_top = top_elevation - excavation_depth
            sigma_in = total_vertical_stress_at_depth(soil_profile, inside_top, inside_depth, inside_gw, 0.0)
            passive_total, kp = passive_pressure_jgj120(sigma_in, u_in, cohesion, phi, separate_water_soil=True)
            passive_soil = passive_total - u_in
        else:
            passive_soil = 0.0
            kp = rankine_passive_coefficient(phi)
        active_soil = active_total - u_out
        net_soil = active_soil - passive_soil
        water_difference = u_out - u_in
        net_pressure = max(0.0, net_soil + water_difference)
        points.append(
            PressurePoint(
                depth=round(depth, 6),
                elevation=round(elevation, 6),
                earth_pressure=round(net_soil, 6),
                water_pressure=round(water_difference, 6),
                total_pressure=round(net_pressure, 6),
                active_earth_pressure=round(active_soil, 6),
                passive_earth_pressure=round(passive_soil, 6),
                outside_water_pressure=round(u_out, 6),
                inside_water_pressure=round(u_in, 6),
                vertical_stress_total=round(sigma_out, 6),
                vertical_stress_effective=round(max(0.0, sigma_out - u_out), 6),
                ka=round(ka, 6),
                kp=round(kp, 6),
                k0=round(at_rest_coefficient(phi, params.k0), 6),
                cohesion=cohesion,
                friction_angle=phi,
                stratum_code=layer.stratum_code if layer else None,
                method=("at-rest outside / Rankine passive inside" if use_at_rest else "Rankine active/passive") + " effective-stress soil + hydrostatic water difference",
            )
        )
    warnings: list[str] = []
    if any((layer.parameters.cohesion is None or layer.parameters.friction_angle is None) for layer in soil_profile):
        warnings.append("部分土层缺少 c 或 phi，已采用 c=0kPa、phi=30deg 的默认值；正式设计应采用勘察/试验参数。")
    if any((layer.parameters.saturated_unit_weight is None and groundwater_level_outside is not None) for layer in soil_profile):
        warnings.append("部分土层缺少饱和重度，地下水以下竖向应力使用天然重度近似。")
    if not soil_profile:
        warnings.append("No soil layers supplied; assumed gamma=18kN/m3, c=0kPa, phi=30deg.")
    warnings.append("Bounded pressure model: fully mobilized passive resistance and nonnegative net lateral load; reverse loading is not solved.")
    return PressureProfile(points=points, warnings=warnings, method=("At-rest" if use_at_rest else "Rankine active") + " effective-stress outside / Rankine passive inside + hydrostatic difference")


def calculate_jgj120_lateral_pressure_profile(
    soil_profile: list[GeologicalLayer],
    excavation_depth: float,
    groundwater_level_outside: float | None = None,
    surcharge: float = 0.0,
    top_elevation: float = 0.0,
    step: float = 0.5,
    mode: str = "active",
    groundwater_level: float | None = None,
    groundwater_level_inside: float | None = None,
    wall_bottom_elevation: float | None = None,
    calculation_depth: float | None = None,
    separated_water: bool = True,
    passive_reduction: float = 1.0,
    **_: object,
) -> PressureProfile:
    """Compatibility facade for modules that need active, passive or at-rest profiles."""
    if mode not in {"active", "passive", "at_rest"}:
        raise ValueError(f"Unsupported pressure mode: {mode}")
    if not separated_water:
        raise ValueError("Unsupported total-stress method: independently verified total-stress parameters required")
    if not math.isfinite(passive_reduction) or passive_reduction < 0:
        raise ValueError("passive_reduction must be finite and nonnegative")
    gw_out = groundwater_level_outside if groundwater_level_outside is not None else groundwater_level
    calc_depth = calculation_depth
    if calc_depth is None and wall_bottom_elevation is not None:
        calc_depth = max(excavation_depth, top_elevation - wall_bottom_elevation)
    profile = calculate_jgj120_pressure_profile(
        soil_profile=soil_profile,
        excavation_depth=excavation_depth,
        groundwater_level_outside=gw_out,
        groundwater_level_inside=groundwater_level_inside,
        surcharge=surcharge,
        top_elevation=top_elevation,
        step=step,
        calculation_depth=calc_depth,
        use_at_rest=(mode == "at_rest"),
    )
    if mode != "passive":
        return profile
    passive_points: list[PressurePoint] = []
    for p in profile.points:
        kp = p.kp or rankine_passive_coefficient(p.friction_angle or 30.0)
        c = p.cohesion or 0.0
        sigma_eff = p.vertical_stress_effective or max(0.0, (p.vertical_stress_total or 0.0) - (p.outside_water_pressure or 0.0))
        passive = max(0.0, passive_reduction * (kp * sigma_eff + 2.0 * c * math.sqrt(max(kp, 0.0))))
        water = p.outside_water_pressure or 0.0 if separated_water else 0.0
        passive_points.append(
            p.model_copy(
                update={
                    "earth_pressure": round(passive, 6),
                    "water_pressure": round(water, 6),
                    "total_pressure": round(passive + water, 6),
                    "active_earth_pressure": None,
                    "passive_earth_pressure": round(passive, 6),
                    "inside_water_pressure": 0.0,
                    "method": "JGJ120-2012 Rankine passive pressure profile",
                }
            )
        )
    return profile.model_copy(update={"points": passive_points, "method": "JGJ120-2012 Rankine passive pressure profile"})
