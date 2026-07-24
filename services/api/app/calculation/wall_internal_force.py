from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.calculation.earth_pressure import calculate_lateral_pressure_profile
from app.rules.gb50010.materials import concrete_elastic_modulus_mpa
from app.calculation.wale_beam import support_spring_stiffness
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
    support_spring_kn_per_m: float | None = None,
    segment: Any | None = None,
    transferred_supports: list[SupportElement] | None = None,
    transfer_stiffness_factor: float = 1.15,
    wall_stiffness_factor: float = 1.0,
    soil_modulus_factor: float = 1.0,
    support_stiffness_factor: float = 1.0,
    support_stiffness_overrides_kn_per_m: dict[str, float] | None = None,
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
    transferred_supports = list(transferred_supports or [])
    support_stiffness_overrides_kn_per_m = dict(support_stiffness_overrides_kn_per_m or {})
    wall_restraints = [*supports, *transferred_supports]
    supports_depth = [top_elevation - s.elevation for s in wall_restraints if 0.0 < top_elevation - s.elevation < wall_depth]
    n = max(31, min(121, int(math.ceil(wall_depth / step)) + 1))
    z = np.linspace(0.0, wall_depth, n)
    dz = float(z[1] - z[0])
    q = np.array([_pressure_at_depth(profile, float(zi)) for zi in z], dtype=float)  # kN/m per metre wall width
    e_mpa = concrete_elastic_modulus_mpa(concrete_grade)
    e_kn_m2 = e_mpa * 1000.0  # MPa -> kN/m2
    inertia = max(wall_thickness, 0.1) ** 3 / 12.0  # per metre wall width
    wall_stiffness_factor = max(0.25, min(float(wall_stiffness_factor), 4.0))
    soil_modulus_factor = max(0.25, min(float(soil_modulus_factor), 4.0))
    support_stiffness_factor = max(0.25, min(float(support_stiffness_factor), 4.0))
    ei = e_kn_m2 * inertia * wall_stiffness_factor
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
    support_indices: dict[int, list[tuple[SupportElement, float, str]]] = {}
    transferred_ids = {support.id for support in transferred_supports}
    for support in wall_restraints:
        d = top_elevation - support.elevation
        if 0.0 <= d <= wall_depth:
            idx = int(np.argmin(np.abs(z - d)))
            is_transfer = support.id in transferred_ids
            is_corner_transfer = str(getattr(support, "code", "")).startswith("CT-")
            if is_corner_transfer:
                factor = 0.55
                source = "corner_wale_transfer_proxy"
            elif is_transfer:
                factor = float(transfer_stiffness_factor)
                source = "permanent_transfer"
            else:
                factor = 1.0
                source = "temporary_support"
            support_indices.setdefault(idx, []).append((support, factor, source))
    # A support at the excavation top is common for crown-beam/top-strut systems.
    # The previous free-head boundary equations silently ignored springs at node 0,
    # which exaggerated the first-span displacement and moment. Keep M(0)=0 and
    # replace the free-shear equation with a spring-supported shear boundary.
    if 0 in support_indices:
        top_level_stiffness = 0.0
        for support, stiffness_factor, _source in support_indices[0]:
            if support.id in support_stiffness_overrides_kn_per_m:
                support_k = float(support_stiffness_overrides_kn_per_m[support.id])
            elif support_spring_kn_per_m is not None:
                support_k = float(support_spring_kn_per_m)
            elif segment is not None:
                support_k, _projection = support_spring_stiffness(support, segment)
            else:
                length = max(float(support.span_length or 1.0), 1.0)
                width = float(support.section.width or support.section.diameter or 1.0)
                height = float(support.section.height or support.section.diameter or 1.0)
                area = max(width * height, 0.05)
                elastic_modulus = float(support.material.elastic_modulus or (32_500_000.0 if support.material.name == "Concrete" else 200_000_000.0))
                support_k = elastic_modulus * area / length
            top_level_stiffness += max(1.0e4, min(2.0e7, support_k * support_stiffness_factor)) * stiffness_factor
        distribution_length = max(float(getattr(segment, "length", 1.0) or 1.0), 1.0)
        k_top = top_level_stiffness / distribution_length
        shear_scale = ei / dz**3
        a[1, :] = 0.0
        a[1, 0] = -shear_scale + k_top
        a[1, 1] = 3.0 * shear_scale
        a[1, 2] = -3.0 * shear_scale
        a[1, 3] = shear_scale

    for i in range(2, n - 2):
        a[i, i - 2] = ei / dz**4
        a[i, i - 1] = -4.0 * ei / dz**4
        a[i, i] = 6.0 * ei / dz**4
        a[i, i + 1] = -4.0 * ei / dz**4
        a[i, i + 2] = ei / dz**4
        layer = _layer_at_depth(soil_profile, top_elevation, float(z[i]))
        ks = 0.0
        if z[i] > excavation_depth:
            ks = _m_value(layer) * soil_modulus_factor * (z[i] - excavation_depth)
        if i in support_indices:
            level_stiffness = 0.0
            for support, stiffness_factor, _source in support_indices[i]:
                if support_spring_kn_per_m is not None:
                    support_k = float(support_spring_kn_per_m)
                elif segment is not None:
                    support_k, _projection = support_spring_stiffness(support, segment)
                else:
                    length = max(float(support.span_length or 1.0), 1.0)
                    width = float(support.section.width or support.section.diameter or 1.0)
                    height = float(support.section.height or support.section.diameter or 1.0)
                    area = max(width * height, 0.05)
                    elastic_modulus = float(support.material.elastic_modulus or (32_500_000.0 if support.material.name == "Concrete" else 200_000_000.0))
                    support_k = elastic_modulus * area / length
                level_stiffness += max(1.0e4, min(2.0e7, support_k * support_stiffness_factor)) * stiffness_factor
            # The vertical wall model is for a one-metre strip. Discrete struts
            # distributed along a wall face must be converted to an equivalent
            # spring per metre of wall length; summing the full EA/L of every
            # strut directly over-stiffens the wall by roughly the support count.
            distribution_length = max(float(getattr(segment, "length", 1.0) or 1.0), 1.0)
            ks += level_stiffness / distribution_length / dz
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
    distribution_length = max(float(getattr(segment, "length", 1.0) or 1.0), 1.0)
    for idx, support_list in support_indices.items():
        for support, stiffness_factor, source in support_list:
            if support.id in support_stiffness_overrides_kn_per_m:
                support_k = float(support_stiffness_overrides_kn_per_m[support.id])
            elif support_spring_kn_per_m is not None:
                support_k = float(support_spring_kn_per_m)
            elif segment is not None:
                support_k, _projection = support_spring_stiffness(support, segment)
            else:
                length = max(float(support.span_length or 1.0), 1.0)
                width = float(support.section.width or support.section.diameter or 1.0)
                height = float(support.section.height or support.section.diameter or 1.0)
                area = max(width * height, 0.05)
                elastic_modulus = float(support.material.elastic_modulus or (32_500_000.0 if support.material.name == "Concrete" else 200_000_000.0))
                support_k = elastic_modulus * area / length
            distributed_k = support_k * support_stiffness_factor * stiffness_factor / distribution_length
            reaction = distributed_k * float(y[idx])
            support_reactions.append(
                {
                    "supportId": support.id,
                    "levelIndex": support.level_index,
                    "elevation": support.elevation,
                    "reactionPerMeter": round(reaction, 3),
                    "springStiffness": round(distributed_k, 3),
                    "unit": "kN/m",
                    "restraintSource": source,
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
        "墙体内力为一维弹性地基梁有限差分包络；离散支撑按构件 EA/L、平面投影及角色系数计算后，按墙面长度折算为单位墙宽等效弹簧。",
        "换撑阶段按已拆临时支撑所在标高设置永久楼板/围檩传力代理约束；楼板刚度、后浇带和施工时序需按专项施工方案复核。",
        "输出单位：弯矩 kN*m/m，剪力 kN/m，位移 mm。",
    ]
    return {
        "method": "JGJ120 pressure + finite-difference beam-on-elastic-foundation",
        "meshStep": round(dz, 4),
        "concreteGrade": concrete_grade,
        "wallThickness": wall_thickness,
        "EI": round(float(ei), 3),
        "calibrationFactors": {"wallStiffnessFactor": wall_stiffness_factor, "soilModulusFactor": soil_modulus_factor, "supportStiffnessFactor": support_stiffness_factor},
        "points": points,
        "supportReactions": support_reactions,
        "maxMoment": round(max_m, 3),
        "maxShear": round(max_v, 3),
        "maxDisplacement": round(max_disp_mm, 3),
        "warnings": warnings,
    }
