from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from app.schemas.domain import GeologicalLayer, SoilParameters

WATER_UNIT_WEIGHT = 9.81  # kN/m3


@dataclass(frozen=True)
class EarthPressureCoefficients:
    active: float
    passive: float
    at_rest: float
    method: str


def rankine_coefficients(phi_deg: float, displacement_condition: str = "active") -> EarthPressureCoefficients:
    """Return Rankine earth-pressure coefficients for a horizontal backfill assumption.

    The implementation is intentionally isolated from UI/API code so that a future JGJ 120
    rules engine can replace or extend the coefficient model without changing callers.
    """
    phi = max(0.0, min(float(phi_deg), 45.0))
    sin_phi = math.sin(math.radians(phi))
    ka = (1.0 - sin_phi) / (1.0 + sin_phi) if abs(1.0 + sin_phi) > 1e-12 else 1.0
    kp = (1.0 + sin_phi) / (1.0 - sin_phi) if abs(1.0 - sin_phi) > 1e-12 else 999.0
    k0 = max(0.0, 1.0 - sin_phi)
    if displacement_condition == "at_rest":
        # Both active/passive are still returned for checks; callers may use k0 explicitly.
        method = "Rankine/Jaky at-rest coefficient for strict deformation control"
    else:
        method = "Rankine active/passive coefficient with horizontal backfill"
    return EarthPressureCoefficients(active=ka, passive=kp, at_rest=k0, method=method)


def layer_at_elevation(layers: list[GeologicalLayer], elevation: float) -> GeologicalLayer | None:
    for layer in layers:
        if layer.top_elevation + 1e-9 >= elevation >= layer.bottom_elevation - 1e-9:
            return layer
    if not layers:
        return None
    # Below last known layer: conservatively reuse the deepest known layer and record warning at caller.
    return min(layers, key=lambda item: item.bottom_elevation)


def layer_at_depth(layers: list[GeologicalLayer], top_elevation: float, depth: float) -> GeologicalLayer | None:
    return layer_at_elevation(layers, top_elevation - depth)


def default_unit_weight(params: SoilParameters, saturated: bool = False) -> float:
    if saturated:
        return params.saturated_unit_weight or params.unit_weight or 19.0
    return params.unit_weight or 18.0


def default_effective_unit_weight(params: SoilParameters) -> float:
    if params.effective_unit_weight is not None:
        return params.effective_unit_weight
    if params.saturated_unit_weight is not None:
        return max(params.saturated_unit_weight - WATER_UNIT_WEIGHT, 0.1)
    if params.unit_weight is not None:
        return max(params.unit_weight - WATER_UNIT_WEIGHT, 0.1)
    return 9.0


def effective_unit_weight_at(layer: GeologicalLayer | None, elevation: float, groundwater_level: float) -> float:
    if layer is None:
        return 9.0 if elevation <= groundwater_level else 18.0
    if elevation <= groundwater_level:
        return default_effective_unit_weight(layer.parameters)
    return default_unit_weight(layer.parameters, saturated=False)


def total_unit_weight_at(layer: GeologicalLayer | None, elevation: float, groundwater_level: float) -> float:
    if layer is None:
        return 19.0 if elevation <= groundwater_level else 18.0
    if elevation <= groundwater_level:
        return default_unit_weight(layer.parameters, saturated=True)
    return default_unit_weight(layer.parameters, saturated=False)


def integrate_vertical_stress(
    layers: list[GeologicalLayer],
    top_elevation: float,
    target_elevation: float,
    groundwater_level: float,
    *,
    effective: bool = True,
    step: float = 0.25,
) -> float:
    """Integrate vertical total/effective stress from top_elevation to target_elevation.

    Units: elevation/depth in m, unit weight in kN/m3, returned stress in kPa.
    """
    if target_elevation >= top_elevation:
        return 0.0
    depth = top_elevation - target_elevation
    n = max(1, int(math.ceil(depth / step)))
    dz = depth / n
    stress = 0.0
    for i in range(n):
        z_mid = top_elevation - (i + 0.5) * dz
        layer = layer_at_elevation(layers, z_mid)
        gamma = effective_unit_weight_at(layer, z_mid, groundwater_level) if effective else total_unit_weight_at(layer, z_mid, groundwater_level)
        stress += gamma * dz
    return stress


def pore_water_pressure(elevation: float, groundwater_level: float, unit_weight: float = WATER_UNIT_WEIGHT) -> float:
    return max(0.0, (groundwater_level - elevation) * unit_weight)


def soil_strength(layer: GeologicalLayer | None) -> tuple[float, float]:
    if layer is None:
        return 0.0, 30.0
    c = layer.parameters.cohesion if layer.parameters.cohesion is not None else 0.0
    phi = layer.parameters.friction_angle if layer.parameters.friction_angle is not None else 30.0
    return max(c, 0.0), max(min(phi, 45.0), 0.0)


def active_earth_pressure_effective(
    sigma_v_eff: float,
    surcharge: float,
    cohesion: float,
    ka: float,
) -> float:
    # Rankine c-phi active pressure in effective stress, truncated for tensile cracking.
    return max(0.0, ka * (sigma_v_eff + max(surcharge, 0.0)) - 2.0 * cohesion * math.sqrt(max(ka, 0.0)))


def passive_earth_pressure_effective(
    sigma_v_eff: float,
    cohesion: float,
    kp: float,
) -> float:
    return max(0.0, kp * sigma_v_eff + 2.0 * cohesion * math.sqrt(max(kp, 0.0)))


def integrate_pressure_area(points: Iterable[tuple[float, float]]) -> float:
    """Integrate pressure-vs-depth points. Returns kN/m for kPa over m."""
    pts = sorted(points)
    if len(pts) < 2:
        return 0.0
    area = 0.0
    for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
        area += 0.5 * (p0 + p1) * (d1 - d0)
    return area


def integrate_pressure_moment_about(points: Iterable[tuple[float, float]], pivot_depth: float) -> float:
    """Integrate pressure moment about a horizontal pivot depth.

    Positive pressure with lever arm abs(depth - pivot_depth); returns kN*m/m.
    """
    pts = sorted(points)
    if len(pts) < 2:
        return 0.0
    moment = 0.0
    for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
        dz = d1 - d0
        if dz <= 0:
            continue
        # two-point Gauss is overkill; trapezoid at endpoints with exact lever at endpoints is adequate here.
        lever0 = abs(d0 - pivot_depth)
        lever1 = abs(d1 - pivot_depth)
        moment += 0.5 * (p0 * lever0 + p1 * lever1) * dz
    return moment
