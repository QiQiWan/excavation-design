from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.rules.gb50010.materials import concrete_strength
from app.schemas.domain import DiaphragmWallPanel, GeologicalLayer, PressureProfile, SupportElement


@dataclass(frozen=True)
class WallBeamResult:
    max_moment_knm_per_m: float
    max_shear_kn_per_m: float
    max_displacement_mm: float
    nodes: list[dict]
    warnings: list[str]
    method: str = "JGJ120-2012 4.1 elastic-support beam simplified FEM"


def _pressure_at_depth(profile: PressureProfile, depth: float) -> float:
    pts = sorted(profile.points, key=lambda p: p.depth)
    if not pts:
        return 0.0
    if depth <= pts[0].depth:
        return pts[0].total_pressure
    if depth >= pts[-1].depth:
        return pts[-1].total_pressure
    for a, b in zip(pts, pts[1:]):
        if a.depth <= depth <= b.depth:
            t = (depth - a.depth) / max(b.depth - a.depth, 1e-9)
            return a.total_pressure + t * (b.total_pressure - a.total_pressure)
    return pts[-1].total_pressure


def _layer_at_depth(layers: list[GeologicalLayer], top_elevation: float, depth: float) -> GeologicalLayer | None:
    elev = top_elevation - depth
    for layer in layers:
        if layer.top_elevation + 1e-9 >= elev >= layer.bottom_elevation - 1e-9:
            return layer
    return layers[-1] if layers else None


def _soil_spring_modulus(layer: GeologicalLayer | None, depth_below_pit_bottom: float) -> float:
    """Return horizontal subgrade modulus k_h in kN/m3 for screening analysis."""
    if depth_below_pit_bottom <= 0:
        return 0.0
    if layer and layer.parameters.horizontal_subgrade_modulus:
        return layer.parameters.horizontal_subgrade_modulus
    c = layer.parameters.cohesion if layer and layer.parameters.cohesion is not None else 10.0
    phi = layer.parameters.friction_angle if layer and layer.parameters.friction_angle is not None else 30.0
    e = layer.parameters.elastic_modulus if layer and layer.parameters.elastic_modulus is not None else 12.0
    # Empirical screening value: stiffer for higher E and shear strength, linearly increasing with depth.
    base = max(3000.0, 800.0 * e + 120.0 * c + 50.0 * phi)
    return base * max(depth_below_pit_bottom, 0.25)


def analyze_diaphragm_wall_elastic_beam(
    wall: DiaphragmWallPanel,
    soil_profile: list[GeologicalLayer],
    pressure_profile: PressureProfile,
    supports: list[SupportElement],
    excavation_top_elevation: float,
    excavation_bottom_elevation: float,
    analysis_width_m: float = 1.0,
    max_node_spacing_m: float = 0.5,
) -> WallBeamResult:
    """Simplified elastic-support beam finite-element model.

    The wall is modelled per metre width with Euler-Bernoulli beam elements. External lateral pressure
    is loaded as distributed load; support levels and embedded soil below the excavation bottom are
    represented as horizontal springs. This is a traceable engineering screening model, not a substitute
    for project-specific commercial/validated FEM analysis.
    """
    wall_height = wall.top_elevation - wall.bottom_elevation
    exposed_depth = excavation_top_elevation - excavation_bottom_elevation
    if wall_height <= 0 or exposed_depth <= 0:
        return WallBeamResult(0, 0, 0, [], ["墙高或开挖深度无效，未进行弹性支点梁计算。"])

    n_elem = max(6, int(math.ceil(wall_height / max_node_spacing_m)))
    n_node = n_elem + 1
    length = wall_height
    dx = length / n_elem
    ndof = n_node * 2

    concrete = concrete_strength(wall.concrete_grade)
    e_kn_m2 = concrete.ec * 1000.0  # MPa -> kN/m2
    inertia_m4 = analysis_width_m * wall.thickness**3 / 12.0
    ei = e_kn_m2 * inertia_m4
    if ei <= 0:
        return WallBeamResult(0, 0, 0, [], ["墙体 EI 无效，未进行弹性支点梁计算。"])

    K = np.zeros((ndof, ndof), dtype=float)
    F = np.zeros(ndof, dtype=float)

    def add_element(i: int, le: float, q: float) -> None:
        # Beam local stiffness with DOF [w1, theta1, w2, theta2].
        k = ei / le**3 * np.array(
            [[12, 6 * le, -12, 6 * le], [6 * le, 4 * le**2, -6 * le, 2 * le**2], [-12, -6 * le, 12, -6 * le], [6 * le, 2 * le**2, -6 * le, 4 * le**2]],
            dtype=float,
        )
        # Consistent nodal load for uniform load q (kN/m), sign follows positive lateral pressure.
        f = q * le / 2.0 * np.array([1, le / 6.0, 1, -le / 6.0], dtype=float)
        dofs = [2 * i, 2 * i + 1, 2 * (i + 1), 2 * (i + 1) + 1]
        for a in range(4):
            F[dofs[a]] += f[a]
            for b in range(4):
                K[dofs[a], dofs[b]] += k[a, b]

    for i in range(n_elem):
        depth_mid = (i + 0.5) * dx
        q = _pressure_at_depth(pressure_profile, depth_mid) * analysis_width_m
        add_element(i, dx, q)

    support_levels = [excavation_top_elevation - s.elevation for s in supports if wall.top_elevation >= s.elevation >= wall.bottom_elevation]
    # Spring at supports. Use high but finite value so reactions can be recovered as k*w.
    support_stiffness = 2.0e6  # kN/m per m tributary, screening value
    for s_depth in support_levels:
        idx_float = min(max(s_depth / dx, 0.0), n_elem)
        left = int(math.floor(idx_float))
        right = min(left + 1, n_elem)
        t = idx_float - left
        if left == right:
            K[2 * left, 2 * left] += support_stiffness
        else:
            K[2 * left, 2 * left] += support_stiffness * (1 - t) ** 2
            K[2 * right, 2 * right] += support_stiffness * t**2
            K[2 * left, 2 * right] += support_stiffness * t * (1 - t)
            K[2 * right, 2 * left] += support_stiffness * t * (1 - t)

    # Embedded soil springs below the current excavation bottom, per node tributary length.
    for i in range(n_node):
        depth = i * dx
        below_bottom = depth - exposed_depth
        tributary = dx if 0 < i < n_node - 1 else dx / 2.0
        if below_bottom > 0:
            layer = _layer_at_depth(soil_profile, excavation_top_elevation, depth)
            kh = _soil_spring_modulus(layer, below_bottom)
            K[2 * i, 2 * i] += kh * analysis_width_m * tributary

    # Add tiny numerical stabilization springs at bottom translational and top rotational modes.
    K[0, 0] += 1e-3
    K[ndof - 2, ndof - 2] += 1e-3
    K[1, 1] += 1e-3

    warnings: list[str] = []
    try:
        U = np.linalg.solve(K, F)
    except np.linalg.LinAlgError:
        warnings.append("弹性支点梁刚度矩阵奇异，采用最小二乘求解；需检查支撑和嵌固约束。")
        U = np.linalg.lstsq(K, F, rcond=None)[0]

    max_m = 0.0
    max_v = 0.0
    nodes: list[dict] = []
    for i in range(n_node):
        depth = i * dx
        nodes.append({"depth": round(depth, 3), "elevation": round(excavation_top_elevation - depth, 3), "displacementMm": round(float(U[2 * i] * 1000.0), 4)})
    for i in range(n_elem):
        le = dx
        dofs = [2 * i, 2 * i + 1, 2 * (i + 1), 2 * (i + 1) + 1]
        u = U[dofs]
        q = _pressure_at_depth(pressure_profile, (i + 0.5) * dx) * analysis_width_m
        # Element end forces k*u - f_load. Moments at rotational DOFs, shears at translational DOFs.
        k = ei / le**3 * np.array(
            [[12, 6 * le, -12, 6 * le], [6 * le, 4 * le**2, -6 * le, 2 * le**2], [-12, -6 * le, 12, -6 * le], [6 * le, 2 * le**2, -6 * le, 4 * le**2]],
            dtype=float,
        )
        f = q * le / 2.0 * np.array([1, le / 6.0, 1, -le / 6.0], dtype=float)
        end_forces = k @ u - f
        max_v = max(max_v, abs(float(end_forces[0])), abs(float(end_forces[2])))
        max_m = max(max_m, abs(float(end_forces[1])), abs(float(end_forces[3])))

    max_disp = max(abs(float(U[2 * i] * 1000.0)) for i in range(n_node))
    warnings.extend(
        [
            "弹性支点梁为软件内置筛查模型：支撑刚度、m法/水平反力系数、施工阶段卸载路径和土-结构相互作用需用项目参数校准。",
            "墙体内力包络已从 TODO 占位升级为简化 FEM；正式设计仍应采用经验证的软件或手算复核。",
        ]
    )
    return WallBeamResult(
        max_moment_knm_per_m=round(max_m, 3),
        max_shear_kn_per_m=round(max_v, 3),
        max_displacement_mm=round(max_disp, 3),
        nodes=nodes,
        warnings=warnings,
    )
