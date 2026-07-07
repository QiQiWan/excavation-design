from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.calculation.earth_pressure import calculate_lateral_pressure_profile
from app.rules.gb50010.materials import concrete_elastic_modulus_mpa
from app.schemas.domain import GeologicalLayer, PressureProfile, SupportElement


def _pressure_at_depth(profile: PressureProfile, depth: float) -> float:
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


def _layer_at_depth(layers: list[GeologicalLayer], top_elevation: float, depth: float) -> GeologicalLayer | None:
    elevation = top_elevation - depth
    for layer in layers:
        if layer.top_elevation + 1e-9 >= elevation >= layer.bottom_elevation - 1e-9:
            return layer
    return layers[-1] if layers else None


def _m_value(layer: GeologicalLayer | None) -> float:
    if layer and layer.parameters.horizontal_subgrade_modulus:
        return layer.parameters.horizontal_subgrade_modulus
    if layer and layer.parameters.elastic_modulus:
        # Convert MPa to a conservative empirical m coefficient range for preliminary analysis.
        return max(3000.0, min(30000.0, layer.parameters.elastic_modulus * 500.0))
    if layer and layer.parameters.compression_modulus:
        return max(3000.0, min(30000.0, layer.parameters.compression_modulus * 600.0))
    return 8000.0


def _simplified_span_envelope(profile: PressureProfile, wall_depth: float, supports_depth: list[float]) -> dict[str, Any]:
    boundaries = [0.0] + sorted(d for d in supports_depth if 0.0 < d < wall_depth) + [wall_depth]
    max_m = 0.0
    max_v = 0.0
    points: list[dict[str, float]] = []
    for a, b in zip(boundaries, boundaries[1:]):
        span = b - a
        if span <= 0:
            continue
        samples = np.linspace(a, b, 21)
        q_values = np.array([_pressure_at_depth(profile, float(z)) for z in samples])
        q_mean = float(np.mean(np.abs(q_values)))
        max_m = max(max_m, q_mean * span**2 / 8.0)
        max_v = max(max_v, q_mean * span / 2.0)
        for z, q in zip(samples, q_values):
            local = z - a
            moment = q_mean * local * (span - local) / 2.0
            shear = q_mean * (span / 2.0 - local)
            points.append({"depth": round(float(z), 3), "pressure": round(float(q), 3), "moment": round(float(moment), 3), "shear": round(float(shear), 3), "displacementMm": 0.0})
    return {
        "method": "simplified-continuous-span-envelope-fallback",
        "points": points,
        "maxMoment": round(max_m, 3),
        "maxShear": round(max_v, 3),
        "maxDisplacement": None,
        "warnings": ["有限差分弹性地基梁求解失败时采用的简化跨中包络估算。"],
    }


def analyze_wall_on_elastic_foundation(
    soil_profile: list[GeologicalLayer],
    supports: list[SupportElement],
    excavation_depth: float,
    groundwater_level_outside: float,
    groundwater_level_inside: float | None,
    surcharge: float,
    top_elevation: float,
    wall_bottom_elevation: float,
    wall_thickness: float,
    concrete_grade: str,
    step: float = 0.25,
    support_spring_kn_per_m: float = 500000.0,
) -> dict[str, Any]:
    """Finite-difference beam-on-elastic-foundation wall analysis.

    The formulation solves EI*y'''' + k_s*y + sum(k_support*y delta) = q(z), where q(z) is the
    net active/passive lateral pressure profile.  It is suitable for preliminary envelopes and for
    driving reinforcement design; soil-structure interaction assumptions must be reviewed for final
    design.
    """
    wall_depth = top_elevation - wall_bottom_elevation
    if wall_depth <= excavation_depth:
        wall_depth = excavation_depth
    profile = calculate_lateral_pressure_profile(
        soil_profile=soil_profile,
        excavation_depth=excavation_depth,
        groundwater_level=groundwater_level_outside,
        groundwater_level_inside=groundwater_level_inside,
        surcharge=surcharge,
        top_elevation=top_elevation,
        step=step,
        calculation_depth=wall_depth,
        mode="active",
        water_soil_method="separate",
    )
    supports_depth = [top_elevation - s.elevation for s in supports if 0.0 < top_elevation - s.elevation < wall_depth]
    n = max(31, min(121, int(math.ceil(wall_depth / step)) + 1))
    z = np.linspace(0.0, wall_depth, n)
    dz = float(z[1] - z[0])
    q = np.array([_pressure_at_depth(profile, float(zi)) for zi in z], dtype=float)  # kN/m per metre wall width
    e_mpa = concrete_elastic_modulus_mpa(concrete_grade)
    e_kn_m2 = e_mpa * 1000.0  # MPa -> kN/m2
    inertia = max(wall_thickness, 0.1) ** 3 / 12.0  # per metre wall width
    ei = e_kn_m2 * inertia
    if ei <= 0:
        return _simplified_span_envelope(profile, wall_depth, supports_depth)

    a = np.zeros((n, n), dtype=float)
    b = np.zeros(n, dtype=float)
    # Free-head boundary conditions: M=0 and V=0.
    a[0, 0] = 1.0
    a[0, 1] = -2.0
    a[0, 2] = 1.0
    a[1, 0] = -1.0
    a[1, 1] = 3.0
    a[1, 2] = -3.0
    a[1, 3] = 1.0
    # Interior finite-difference equations.
    support_indices: dict[int, list[SupportElement]] = {}
    for support in supports:
        d = top_elevation - support.elevation
        if 0.0 <= d <= wall_depth:
            idx = int(np.argmin(np.abs(z - d)))
            support_indices.setdefault(idx, []).append(support)
    for i in range(2, n - 2):
        a[i, i - 2] = ei / dz**4
        a[i, i - 1] = -4.0 * ei / dz**4
        a[i, i] = 6.0 * ei / dz**4
        a[i, i + 1] = -4.0 * ei / dz**4
        a[i, i + 2] = ei / dz**4
        layer = _layer_at_depth(soil_profile, top_elevation, float(z[i]))
        ks = 0.0
        if z[i] > excavation_depth:
            ks = _m_value(layer) * (z[i] - excavation_depth)
        if i in support_indices:
            ks += support_spring_kn_per_m / dz
        a[i, i] += ks
        b[i] = q[i]
    # Free-toe conditions; embedded soil springs provide restraint above the toe.
    a[n - 2, n - 3] = 1.0
    a[n - 2, n - 2] = -2.0
    a[n - 2, n - 1] = 1.0
    a[n - 1, n - 4] = -1.0
    a[n - 1, n - 3] = 3.0
    a[n - 1, n - 2] = -3.0
    a[n - 1, n - 1] = 1.0
    try:
        y = np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return _simplified_span_envelope(profile, wall_depth, supports_depth)

    y2 = np.gradient(np.gradient(y, dz), dz)
    y3 = np.gradient(y2, dz)
    moment = -ei * y2
    shear = -ei * y3
    support_reactions = []
    for idx, support_list in support_indices.items():
        for support in support_list:
            reaction = support_spring_kn_per_m * float(y[idx])
            support_reactions.append(
                {
                    "supportId": support.id,
                    "levelIndex": support.level_index,
                    "elevation": support.elevation,
                    "reactionPerMeter": round(reaction, 3),
                    "unit": "kN/m",
                }
            )
    max_m = float(np.max(np.abs(moment)))
    max_v = float(np.max(np.abs(shear)))
    max_disp_mm = float(np.max(np.abs(y)) * 1000.0)
    points = []
    for zi, qi, yi, mi, vi in zip(z, q, y, moment, shear):
        points.append(
            {
                "depth": round(float(zi), 3),
                "elevation": round(float(top_elevation - zi), 3),
                "netPressure": round(float(qi), 3),
                "displacementMm": round(float(yi * 1000.0), 3),
                "moment": round(float(mi), 3),
                "shear": round(float(vi), 3),
            }
        )
    warnings = profile.warnings + [
        "墙体内力为一维弹性地基梁有限差分包络；支撑刚度、土体 m 值、施工步卸载与三维效应需复核。",
        "输出单位：弯矩 kN*m/m，剪力 kN/m，位移 mm。",
    ]
    return {
        "method": "JGJ120 pressure + finite-difference beam-on-elastic-foundation",
        "meshStep": round(dz, 4),
        "concreteGrade": concrete_grade,
        "wallThickness": wall_thickness,
        "EI": round(float(ei), 3),
        "points": points,
        "supportReactions": support_reactions,
        "maxMoment": round(max_m, 3),
        "maxShear": round(max_v, 3),
        "maxDisplacement": round(max_disp_mm, 3),
        "warnings": warnings,
    }
