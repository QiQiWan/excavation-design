from __future__ import annotations

import math

from app.schemas.domain import PressureProfile, SupportElement, WallInternalForcePoint, WallInternalForceResult


def pressure_at_depth(profile: PressureProfile, depth: float) -> float:
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


def _integrate(profile: PressureProfile, a: float, b: float, moment_about: float | None = None) -> tuple[float, float]:
    if b <= a:
        return 0.0, 0.0
    inner = [p.depth for p in profile.points if a < p.depth < b]
    samples = sorted({a, b, *inner})
    force = 0.0
    moment = 0.0
    pivot = moment_about if moment_about is not None else a
    for z1, z2 in zip(samples, samples[1:]):
        p1 = pressure_at_depth(profile, z1)
        p2 = pressure_at_depth(profile, z2)
        dz = z2 - z1
        w = 0.5 * (p1 + p2) * dz
        if abs(p1 + p2) > 1e-9:
            centroid = z1 + dz * (p1 + 2.0 * p2) / (3.0 * (p1 + p2))
        else:
            centroid = (z1 + z2) / 2.0
        force += w
        moment += w * abs(centroid - pivot)
    return force, moment


def _simply_supported_span_moment(profile: PressureProfile, a: float, b: float) -> tuple[float, float]:
    """Return max shear and max moment of a simply supported span under lateral load."""
    if b <= a:
        return 0.0, 0.0
    length = b - a
    total, first_moment = _integrate(profile, a, b, moment_about=a)
    rb = first_moment / max(length, 1e-9)
    ra = total - rb
    max_shear = max(abs(ra), abs(rb))
    max_moment = 0.0
    samples = [a + length * i / 40.0 for i in range(41)]
    for z in samples:
        left_force, left_moment_about_a = _integrate(profile, a, z, moment_about=a)
        # Moment at z: Ra*(z-a) - integral_a^z p(s)*(z-s)ds.
        _, left_moment_about_z = _integrate(profile, a, z, moment_about=z)
        m = ra * (z - a) - left_moment_about_z
        max_moment = max(max_moment, abs(m))
    return max_shear, max_moment


def calculate_wall_internal_forces_equivalent_beam(
    segment_id: str,
    stage_id: str,
    pressure_profile: PressureProfile,
    supports: list[SupportElement],
    top_elevation: float,
    excavation_bottom_elevation: float,
    wall_bottom_elevation: float,
    importance_factor: float = 1.0,
    load_factor: float = 1.25,
) -> WallInternalForceResult:
    """Approximate wall shear/moment envelope per metre wall width.

    The solver is deliberately transparent: unsupported top and embedded bottom zones
    are treated as cantilevers from the adjacent support/excavation boundary, and spans
    between supports are treated as simply supported. It provides design-assist values
    until a staged beam-on-elastic-foundation solver is connected.
    """
    wall_depth = max(0.0, top_elevation - wall_bottom_elevation)
    excavation_depth = max(0.0, top_elevation - excavation_bottom_elevation)
    support_depths = sorted({round(top_elevation - s.elevation, 6) for s in supports if 0.0 < top_elevation - s.elevation < wall_depth})
    warnings: list[str] = [
        "墙体内力采用等效竖向梁近似；正式设计应采用施工阶段弹性地基梁/数值模型形成包络。"
    ]
    max_shear = 0.0
    max_moment = 0.0
    force_points: list[WallInternalForcePoint] = []

    if not support_depths:
        # Cantilever wall fixed at toe: moment and shear at toe.
        shear, moment = _integrate(pressure_profile, 0.0, wall_depth, moment_about=wall_depth)
        max_shear, max_moment = abs(shear), abs(moment)
    else:
        first = support_depths[0]
        if first > 0.0:
            shear, moment = _integrate(pressure_profile, 0.0, first, moment_about=first)
            max_shear = max(max_shear, abs(shear))
            max_moment = max(max_moment, abs(moment))
        for a, b in zip(support_depths, support_depths[1:]):
            shear, moment = _simply_supported_span_moment(pressure_profile, a, b)
            max_shear = max(max_shear, abs(shear))
            max_moment = max(max_moment, abs(moment))
        last = support_depths[-1]
        lower_bound = max(wall_depth, excavation_depth)
        if wall_depth > last:
            shear, moment = _integrate(pressure_profile, last, wall_depth, moment_about=last)
            max_shear = max(max_shear, abs(shear))
            max_moment = max(max_moment, abs(moment))

    # Diagnostic cumulative cantilever curve about top for plotting only.
    for p in pressure_profile.points:
        if p.depth <= wall_depth + 1e-9:
            shear, moment = _integrate(pressure_profile, 0.0, p.depth, moment_about=p.depth)
            force_points.append(
                WallInternalForcePoint(
                    depth=p.depth,
                    elevation=p.elevation,
                    shear=round(shear, 3),
                    moment=round(moment, 3),
                    displacement=None,
                )
            )
    return WallInternalForceResult(
        segment_id=segment_id,
        stage_id=stage_id,
        points=force_points,
        max_moment=round(max_moment, 3),
        max_shear=round(max_shear, 3),
        max_moment_design=round(max_moment * importance_factor * load_factor, 3),
        max_shear_design=round(max_shear * importance_factor * load_factor, 3),
        importance_factor=importance_factor,
        load_combination_factor=load_factor,
        warnings=warnings,
    )
