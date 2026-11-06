from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.rules.gb50010.rc_section_rules import CONCRETE_GRADES
from app.schemas.domain import PressureProfile, SupportElement


@dataclass(frozen=True)
class WallAnalysisResult:
    node_depths: list[float]
    displacements_mm: list[float]
    rotations_rad: list[float]
    moments_knm_per_m: list[float]
    shears_kn_per_m: list[float]
    support_reactions_kn_per_m: dict[str, float]
    max_moment_knm_per_m: float
    max_shear_kn_per_m: float
    max_displacement_mm: float
    method: str
    warnings: list[str]


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


def _element_stiffness(ei: float, length: float) -> np.ndarray:
    l = length
    factor = ei / (l ** 3)
    return factor * np.array(
        [
            [12, 6 * l, -12, 6 * l],
            [6 * l, 4 * l * l, -6 * l, 2 * l * l],
            [-12, -6 * l, 12, -6 * l],
            [6 * l, 2 * l * l, -6 * l, 4 * l * l],
        ],
        dtype=float,
    )


def _shape_functions(xi: float, length: float) -> np.ndarray:
    # xi in [0,1], Euler-Bernoulli Hermite functions for transverse displacement.
    n1 = 1 - 3 * xi**2 + 2 * xi**3
    n2 = length * (xi - 2 * xi**2 + xi**3)
    n3 = 3 * xi**2 - 2 * xi**3
    n4 = length * (-xi**2 + xi**3)
    return np.array([n1, n2, n3, n4], dtype=float)


def _element_load(q1: float, q2: float, length: float) -> np.ndarray:
    # Integrate N^T q dx using 3-point Gauss quadrature. q is positive toward excavation.
    gauss = [(-math.sqrt(3 / 5), 5 / 9), (0.0, 8 / 9), (math.sqrt(3 / 5), 5 / 9)]
    fe = np.zeros(4)
    for pt, weight in gauss:
        xi = 0.5 * (pt + 1.0)
        q = q1 + (q2 - q1) * xi
        n = _shape_functions(xi, length)
        fe += n * q * length * 0.5 * weight
    return fe


def _concrete_ec_kpa(concrete_grade: str) -> float:
    key = concrete_grade.upper().replace(" ", "")
    ec_mpa = CONCRETE_GRADES.get(key, CONCRETE_GRADES["C35"])["ec"]
    return ec_mpa * 1000.0


def _build_nodes(profile: PressureProfile, excavation_depth: float, wall_depth: float, supports: list[SupportElement], top_elevation: float) -> list[float]:
    depths = {0.0, excavation_depth, wall_depth}
    for p in profile.points:
        if 0.0 <= p.depth <= wall_depth:
            depths.add(round(p.depth, 6))
    for support in supports:
        d = top_elevation - support.elevation
        if 0.0 < d < wall_depth:
            depths.add(round(d, 6))
    # Add embedment points at 1m spacing below excavation to stabilize the simple beam model.
    d = math.ceil(excavation_depth)
    while d < wall_depth:
        depths.add(round(float(d), 6))
        d += 1
    return sorted(depths)


def analyze_wall_as_supported_beam(
    pressure_profile: PressureProfile,
    wall_thickness_m: float,
    concrete_grade: str,
    top_elevation: float,
    wall_bottom_elevation: float,
    excavation_bottom_elevation: float,
    supports: list[SupportElement],
) -> WallAnalysisResult:
    """Preliminary staged 2D beam analysis for one metre width of diaphragm wall.

    The beam uses Euler-Bernoulli elements. Support and wall toe lateral translations are restrained;
    rotations remain free except for a cantilever case where the wall toe is fixed. Soil springs are not
    calibrated in this version, so the result is a conservative/review-oriented design aid, not a final
    elastic-foundation result.
    """
    excavation_depth = max(top_elevation - excavation_bottom_elevation, 0.0)
    wall_depth = max(top_elevation - wall_bottom_elevation, excavation_depth)
    if wall_depth <= 0.0:
        raise ValueError("Invalid wall depth")
    node_depths = _build_nodes(pressure_profile, excavation_depth, wall_depth, supports, top_elevation)
    n = len(node_depths)
    dof_count = 2 * n
    ec = _concrete_ec_kpa(concrete_grade)
    inertia = 1.0 * wall_thickness_m**3 / 12.0  # m4 per metre wall width
    ei = ec * inertia  # kN*m2 per metre width
    k_global = np.zeros((dof_count, dof_count), dtype=float)
    f_global = np.zeros(dof_count, dtype=float)

    for idx in range(n - 1):
        z1 = node_depths[idx]
        z2 = node_depths[idx + 1]
        length = z2 - z1
        if length <= 1e-9:
            continue
        k = _element_stiffness(ei, length)
        # pressure below current excavation stage is assumed zero on the excavated side for this preliminary action model.
        q1 = _pressure_at_depth(pressure_profile, min(z1, excavation_depth)) if z1 <= excavation_depth + 1e-9 else 0.0
        q2 = _pressure_at_depth(pressure_profile, min(z2, excavation_depth)) if z2 <= excavation_depth + 1e-9 else 0.0
        fe = _element_load(q1, q2, length)
        dofs = [2 * idx, 2 * idx + 1, 2 * (idx + 1), 2 * (idx + 1) + 1]
        for a in range(4):
            f_global[dofs[a]] += fe[a]
            for b in range(4):
                k_global[dofs[a], dofs[b]] += k[a, b]

    constrained: dict[int, str] = {}
    support_reaction_dofs: dict[int, str] = {}
    for support in supports:
        support_depth = round(top_elevation - support.elevation, 6)
        nearest = min(range(n), key=lambda i: abs(node_depths[i] - support_depth))
        dof = 2 * nearest
        constrained[dof] = support.id
        support_reaction_dofs[dof] = support.id
    bottom_idx = n - 1
    constrained[2 * bottom_idx] = "wall_toe_lateral_restraint"
    if not supports:
        constrained[2 * bottom_idx + 1] = "wall_toe_rotation_restraint"

    free = [i for i in range(dof_count) if i not in constrained]
    fixed = sorted(constrained)
    displacements = np.zeros(dof_count, dtype=float)
    if free:
        k_ff = k_global[np.ix_(free, free)]
        f_f = f_global[free]
        try:
            displacements[free] = np.linalg.solve(k_ff, f_f)
        except np.linalg.LinAlgError:
            displacements[free] = np.linalg.lstsq(k_ff + np.eye(len(free)) * 1e-9, f_f, rcond=None)[0]
    reactions = k_global @ displacements - f_global
    support_reactions: dict[str, float] = {}
    for dof, support_id in support_reaction_dofs.items():
        support_reactions[support_id] = round(abs(reactions[dof]), 3)

    moments: list[float] = []
    shears: list[float] = []
    for idx in range(n - 1):
        z1 = node_depths[idx]
        z2 = node_depths[idx + 1]
        length = z2 - z1
        if length <= 1e-9:
            continue
        k = _element_stiffness(ei, length)
        q1 = _pressure_at_depth(pressure_profile, min(z1, excavation_depth)) if z1 <= excavation_depth + 1e-9 else 0.0
        q2 = _pressure_at_depth(pressure_profile, min(z2, excavation_depth)) if z2 <= excavation_depth + 1e-9 else 0.0
        fe = _element_load(q1, q2, length)
        dofs = [2 * idx, 2 * idx + 1, 2 * (idx + 1), 2 * (idx + 1) + 1]
        element_disp = displacements[dofs]
        end_forces = k @ element_disp - fe
        shears.extend([float(end_forces[0]), float(-end_forces[2])])
        moments.extend([float(end_forces[1]), float(-end_forces[3])])
    disp_mm = [round(abs(displacements[2 * i]) * 1000.0, 4) for i in range(n)]
    rotations = [round(float(displacements[2 * i + 1]), 7) for i in range(n)]
    max_m = max((abs(m) for m in moments), default=0.0)
    max_v = max((abs(v) for v in shears), default=0.0)
    max_disp = max(disp_mm, default=0.0)
    warnings = [
        "墙体内力采用二维梁单元初步分析；未以地区经验标定土弹簧和墙-土相互作用，正式设计需复核。",
        "支点位移按理想约束处理，支撑刚度、预加力、节点变形和施工偏差需另行考虑。",
    ]
    return WallAnalysisResult(
        node_depths=[round(d, 4) for d in node_depths],
        displacements_mm=disp_mm,
        rotations_rad=rotations,
        moments_knm_per_m=[round(m, 4) for m in moments],
        shears_kn_per_m=[round(v, 4) for v in shears],
        support_reactions_kn_per_m=support_reactions,
        max_moment_knm_per_m=round(max_m, 3),
        max_shear_kn_per_m=round(max_v, 3),
        max_displacement_mm=round(max_disp, 3),
        method="preliminary_2d_euler_bernoulli_supported_beam_per_m_width",
        warnings=warnings,
    )
