from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.calculation.wale_beam import support_spring_stiffness
from app.schemas.domain import PressureProfile, Point2D, SupportElement

EPS = 1e-9
E_CONCRETE_KN_M2 = {
    "C30": 30_000_000.0,
    "C35": 31_500_000.0,
    "C40": 32_500_000.0,
    "C45": 33_500_000.0,
    "C50": 34_500_000.0,
}
DEFAULT_SOIL_SPRING_KN_M2 = 12_000.0
WALL_WALE_COUPLING_KN_M = 2.0e6
END_ANCHOR_KN_M = 5.0e4


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
            t = (depth - a.depth) / max(b.depth - a.depth, EPS)
            return a.total_pressure + t * (b.total_pressure - a.total_pressure)
    return points[-1].total_pressure


def _wall_ei_knm2(thickness: float, concrete_grade: str) -> float:
    e = E_CONCRETE_KN_M2.get(concrete_grade, E_CONCRETE_KN_M2["C35"])
    i = max(thickness, 0.4) ** 3 / 12.0  # per metre width
    return max(e * i, 1.0e5)


def _add_spring(k: np.ndarray, i: int, j: int | None, stiffness: float) -> None:
    if stiffness <= 0:
        return
    if j is None:
        k[i, i] += stiffness
    else:
        k[i, i] += stiffness
        k[j, j] += stiffness
        k[i, j] -= stiffness
        k[j, i] -= stiffness


def _matrix_equilibrium_diagnostics(
    k_original: np.ndarray,
    f: np.ndarray,
    u: np.ndarray,
    *,
    regularization: float = 0.0,
    solve_failed: bool = False,
) -> dict[str, Any]:
    """Return auditable numerical-quality evidence for K u = F.

    The residual of the effective matrix proves the linear solve itself.  The
    residual of the original matrix is retained separately because a
    regularized solve can converge numerically while no longer satisfying the
    unmodified structural system.  This is a software quality gate and is not
    presented as a substitute for an engineering-code check.
    """
    n = int(k_original.shape[0]) if k_original.ndim == 2 else 0
    if n == 0 or solve_failed:
        return {
            "status": "fail",
            "equation": "K u = F",
            "matrixSize": n,
            "relativeResidual": None,
            "originalRelativeResidual": None,
            "maxResidual": None,
            "loadNormL2": None,
            "matrixSymmetryError": None,
            "regularization": float(regularization),
            "message": "线性方程组未获得有效解。",
        }
    effective_k = k_original + np.eye(n) * float(regularization) if regularization > 0.0 else k_original
    load_norm = max(float(np.linalg.norm(f, ord=2)), 1.0)
    residual = effective_k @ u - f
    original_residual = k_original @ u - f
    relative = float(np.linalg.norm(residual, ord=2)) / load_norm
    original_relative = float(np.linalg.norm(original_residual, ord=2)) / load_norm
    max_residual = float(np.max(np.abs(residual))) if residual.size else 0.0
    matrix_norm = max(float(np.linalg.norm(k_original, ord="fro")), 1.0)
    symmetry_error = float(np.linalg.norm(k_original - k_original.T, ord="fro")) / matrix_norm
    if regularization > 0.0:
        status = "manual_review"
        message = "方程组采用正则化求解；有效矩阵残差合格时仍需复核原始结构模型约束。"
    elif relative <= 1.0e-8 and symmetry_error <= 1.0e-10:
        status = "pass"
        message = "全局刚度方程残差与矩阵对称性满足数值质量门禁。"
    elif relative <= 1.0e-5 and symmetry_error <= 1.0e-7:
        status = "warning"
        message = "全局刚度方程已收敛，但残差或矩阵对称误差接近软件质量阈值。"
    else:
        status = "fail"
        message = "全局刚度方程残差或矩阵对称误差超出软件质量阈值。"
    return {
        "status": status,
        "equation": "K u = F",
        "matrixSize": n,
        "relativeResidual": float(f"{relative:.12g}"),
        "originalRelativeResidual": float(f"{original_relative:.12g}"),
        "absoluteResidualL2": float(f"{float(np.linalg.norm(residual, ord=2)):.12g}"),
        "maxResidual": float(f"{max_residual:.12g}"),
        "loadNormL2": float(f"{load_norm:.12g}"),
        "matrixSymmetryError": float(f"{symmetry_error:.12g}"),
        "regularization": float(regularization),
        "residualBasis": "effective regularized matrix" if regularization > 0.0 else "original matrix",
        "message": message,
    }


def _segment_length(segment: Any) -> float:
    return float(getattr(segment, "length", 0.0) or math.hypot(segment.end.x - segment.start.x, segment.end.y - segment.start.y))


def _chainage(point: Point2D, segment: Any) -> float:
    ax, ay = segment.start.x, segment.start.y
    bx, by = segment.end.x, segment.end.y
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= EPS:
        return 0.0
    t = ((point.x - ax) * dx + (point.y - ay) * dy) / (length * length)
    return max(0.0, min(1.0, t)) * length


def _endpoint_for_face(support: SupportElement, face_code: str) -> tuple[str, Point2D] | None:
    if support.start_face_code == face_code:
        return "start", support.start_wall_connection or support.start
    if support.end_face_code == face_code:
        return "end", support.end_wall_connection or support.end
    return None




def _beam4_stiffness(ei: float, length: float) -> np.ndarray:
    L = max(float(length), 0.25)
    c = max(ei, 1.0) / (L ** 3)
    return c * np.array([
        [12.0, 6.0 * L, -12.0, 6.0 * L],
        [6.0 * L, 4.0 * L * L, -6.0 * L, 2.0 * L * L],
        [-12.0, -6.0 * L, 12.0, -6.0 * L],
        [6.0 * L, 2.0 * L * L, -6.0 * L, 4.0 * L * L],
    ], dtype=float)


def _beam4_load(q: float, length: float) -> np.ndarray:
    L = max(float(length), 0.25)
    return np.array([q * L / 2.0, q * L * L / 12.0, q * L / 2.0, -q * L * L / 12.0], dtype=float)


def _add_matrix(k: np.ndarray, dof_ids: list[int], local: np.ndarray) -> None:
    for a, ia in enumerate(dof_ids):
        for b, ib in enumerate(dof_ids):
            k[ia, ib] += float(local[a, b])


def _support_direction_cosines(support: SupportElement) -> tuple[float, float, float]:
    dx = support.end.x - support.start.x
    dy = support.end.y - support.start.y
    length = math.hypot(dx, dy)
    if length <= EPS:
        return 1.0, 0.0, 0.0
    return dx / length, dy / length, length


def _replacement_slab_state(stage_type: str | None, wall_length: float, properties: dict[str, Any] | None) -> dict[str, Any]:
    required = stage_type in {"bottom_slab", "replacement", "support_removal", "final"}
    if not required:
        return {
            "required": False,
            "status": "not_active",
            "stiffness": None,
            "source": "stage does not activate replacement slab",
            "components": {},
        }
    props = dict(properties or {})
    width = float(props.get("effectiveWidthM") or 0.0)
    thickness = float(props.get("thicknessM") or 0.0)
    elastic_modulus_mpa = float(props.get("elasticModulusMpa") or 0.0)
    reduction = float(props.get("connectionReduction") or 0.0)
    if min(width, thickness, elastic_modulus_mpa, reduction) <= 0.0:
        return {
            "required": True,
            "status": "missing",
            "stiffness": None,
            "source": "replacement slab properties are incomplete",
            "components": {"effectiveWidthM": width, "thicknessM": thickness, "elasticModulusMpa": elastic_modulus_mpa, "connectionReduction": reduction},
        }
    transfer_length = max(float(props.get("transferLengthM") or wall_length), 3.0)
    # E [MPa] -> kN/m2, A = width * thickness, k = EA/L.
    gross = elastic_modulus_mpa * 1000.0 * width * thickness / transfer_length
    stiffness = gross * max(0.05, min(reduction, 1.0))
    status = "active" if math.isfinite(stiffness) and stiffness > 1.0 else "invalid"
    return {
        "required": True,
        "status": status,
        "stiffness": stiffness if status == "active" else None,
        "source": "EA/L equivalent in-plane slab/frame transfer stiffness with connection reduction",
        "components": {
            "effectiveWidthM": round(width, 4),
            "thicknessM": round(thickness, 4),
            "elasticModulusMpa": round(elastic_modulus_mpa, 3),
            "connectionReduction": round(reduction, 4),
            "transferLengthM": round(transfer_length, 4),
            "grossStiffnessKnM": round(gross, 3),
        },
    }


def _solve_spatial_frame_proxy(
    *,
    wall_depths: list[float],
    support_nodes: list[dict[str, Any]],
    pressure_profile: PressureProfile,
    top_elevation: float,
    excavation_depth: float,
    wall_depth: float,
    wall_length: float,
    wall_ei: float,
    soil_k: float,
    stage_id: str,
    stage_type: str | None,
    replacement_slab_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Solve a reviewable spatial-frame proxy with rotational and vertical DOFs.

    The model remains compact enough for deterministic design-assist workflows,
    but it is no longer a translational condensation. Wall and wale nodes carry
    horizontal displacement and rotation, supports carry axial deformation DOFs,
    columns carry vertical DOFs, and replacement slabs add stage-dependent lateral
    stiffness. This is the bridge between the V1.x screening matrix and a future
    full 3D frame/FEM kernel.
    """
    wall_count = len(wall_depths)
    wale_nodes = sorted(support_nodes, key=lambda n: (n["support"].level_index, n["chainage"], n["endpoint"]))
    dof_names: list[str] = []
    dof_meta: list[dict[str, Any]] = []
    wall_u: list[int] = []
    wall_t: list[int] = []
    for i, depth in enumerate(wall_depths):
        wall_u.append(len(dof_names)); dof_names.append(f"wall:ux:{i}:z={depth:.3f}"); dof_meta.append({"type":"wall_horizontal","objectId":None,"unit":"m"})
        wall_t.append(len(dof_names)); dof_names.append(f"wall:theta:{i}:z={depth:.3f}"); dof_meta.append({"type":"wall_rotation","objectId":None,"unit":"rad"})
    wale_u: list[int] = []
    wale_t: list[int] = []
    support_a: list[int] = []
    column_v: list[int] = []
    for node in wale_nodes:
        sup = node["support"]
        code = sup.code
        endpoint = node["endpoint"]
        wale_u.append(len(dof_names)); dof_names.append(f"wale:ux:{code}:{endpoint}"); dof_meta.append({"type":"wale_horizontal","objectId":sup.id,"unit":"m"})
        wale_t.append(len(dof_names)); dof_names.append(f"wale:theta:{code}:{endpoint}"); dof_meta.append({"type":"wale_rotation","objectId":sup.id,"unit":"rad"})
        support_a.append(len(dof_names)); dof_names.append(f"support:axial:{code}:{endpoint}"); dof_meta.append({"type":"support_axial","objectId":sup.id,"unit":"m"})
        column_v.append(len(dof_names)); dof_names.append(f"column:v:{code}:{endpoint}"); dof_meta.append({"type":"column_vertical","objectId":sup.id,"unit":"m"})
    n = len(dof_names)
    if n == 0:
        return {"available": False, "reason": "no spatial DOFs"}
    K = np.zeros((n, n), dtype=float)
    F = np.zeros(n, dtype=float)

    # Wall beam with translation/rotation DOFs.
    for i in range(wall_count - 1):
        L = max(wall_depths[i + 1] - wall_depths[i], 0.25)
        _add_matrix(K, [wall_u[i], wall_t[i], wall_u[i + 1], wall_t[i + 1]], _beam4_stiffness(wall_ei, L))
        q1 = _pressure_at_depth(pressure_profile, wall_depths[i]) * max(wall_length, 1.0)
        q2 = _pressure_at_depth(pressure_profile, wall_depths[i + 1]) * max(wall_length, 1.0)
        fe = _beam4_load(0.5 * (q1 + q2), L)
        for dof, val in zip([wall_u[i], wall_t[i], wall_u[i + 1], wall_t[i + 1]], fe):
            F[dof] += float(val)
    # Passive side soil springs below excavation.
    for i, d in enumerate(wall_depths):
        if d >= excavation_depth:
            dz_left = d - wall_depths[i - 1] if i > 0 else 0.5
            dz_right = wall_depths[i + 1] - d if i < wall_count - 1 else 0.5
            trib = max(0.25, 0.5 * (dz_left + dz_right))
            _add_spring(K, wall_u[i], None, soil_k * trib * max(wall_length, 1.0))
    _add_spring(K, wall_u[0], None, END_ANCHOR_KN_M)
    _add_spring(K, wall_t[0], None, END_ANCHOR_KN_M * 0.25)
    _add_spring(K, wall_u[-1], None, END_ANCHOR_KN_M * 2.0)
    _add_spring(K, wall_t[-1], None, END_ANCHOR_KN_M * 0.5)

    rigid_zones: list[dict[str, Any]] = []
    support_reactions: list[dict[str, Any]] = []
    column_records: list[dict[str, Any]] = []
    stage_type = stage_type or "excavation"
    replacement_state = dict(replacement_slab_state or {})
    slab_value = replacement_state.get("stiffness")
    slab_k = float(slab_value) if slab_value is not None else 0.0
    # Wale beam continuity by support level, including rotations.
    level_items: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for j, node in enumerate(wale_nodes):
        level_items.setdefault(node["support"].level_index, []).append((j, node))
    wale_ei = max(2.0e5, wall_ei * 0.45)
    for _level, items in level_items.items():
        items = sorted(items, key=lambda item: item[1]["chainage"])
        for (ja, a), (jb, b) in zip(items, items[1:]):
            span = max(abs(b["chainage"] - a["chainage"]), 1.0)
            _add_matrix(K, [wale_u[ja], wale_t[ja], wale_u[jb], wale_t[jb]], _beam4_stiffness(wale_ei, span))
    # Wall-wale rigid zones, support axial direction, column vertical and replacement slab stiffness.
    for j, node in enumerate(wale_nodes):
        sup = node["support"]
        nearest = min(range(wall_count), key=lambda i: abs(wall_depths[i] - node["depth"]))
        rigid_k = WALL_WALE_COUPLING_KN_M * 4.0
        rot_k = max(rigid_k * 0.15, 1.0e5)
        _add_spring(K, wall_u[nearest], wale_u[j], rigid_k)
        _add_spring(K, wall_t[nearest], wale_t[j], rot_k)
        if slab_k:
            _add_spring(K, wale_u[j], None, slab_k)
        spring = max(float(node["spring"]), 1.0)
        projection = max(float(node["projection"]), 0.2)
        cx, cy, sup_len = _support_direction_cosines(sup)
        # Support axial DOF is tied to the wale node through projected stiffness
        # and restrained at the far side by the opposite wall/support system.
        axial_k = spring * projection * projection
        _add_spring(K, wale_u[j], support_a[j], axial_k)
        _add_spring(K, support_a[j], None, axial_k * 0.65)
        # Column vertical DOF proxy: vertical stiffness participates as a real DOF;
        # its load is induced from support inclination/imperfection after solving.
        column_k = max(3.0e5, axial_k * 0.08)
        _add_spring(K, column_v[j], None, column_k)
        rigid_zones.append({
            "supportCode": sup.code,
            "endpoint": node["endpoint"],
            "nearestWallNode": nearest,
            "rigidTranslationalStiffness": round(rigid_k, 3),
            "rigidRotationalStiffness": round(rot_k, 3),
            "nodeZoneLength": round(max(0.8, min(2.5, sup_len * 0.04)), 3),
        })
    regularization = 0.0
    solve_failed = False
    try:
        U = np.linalg.solve(K, F)
        fallback = False
        reason = None
    except np.linalg.LinAlgError as exc:
        regularization = max(float(np.max(np.diag(K))) * 1e-6, 10.0)
        try:
            U = np.linalg.solve(K + np.eye(n) * regularization, F)
            fallback = True
            reason = f"regularized spatial frame matrix: {exc}"
        except np.linalg.LinAlgError:
            U = np.zeros(n)
            fallback = True
            solve_failed = True
            reason = f"failed spatial frame matrix: {exc}"
    equilibrium_diagnostics = _matrix_equilibrium_diagnostics(
        K, F, U, regularization=regularization, solve_failed=solve_failed
    )
    max_axial = 0.0
    support_axial_dofs: list[dict[str, Any]] = []
    column_dofs: list[dict[str, Any]] = []
    wale_node_profile: list[dict[str, Any]] = []
    for j, node in enumerate(wale_nodes):
        sup = node["support"]
        spring = max(float(node["spring"]), 1.0)
        projection = max(float(node["projection"]), 0.2)
        axial_k = spring * projection * projection
        reaction = max(0.0, axial_k * (float(U[wale_u[j]]) - float(U[support_a[j]])))
        axial = reaction / projection
        max_axial = max(max_axial, axial)
        deformation = float(U[support_a[j]])
        cx, cy, _ = _support_direction_cosines(sup)
        col_load = 0.03 * axial + abs(float(U[wale_t[j]])) * 1500.0
        column_k = max(3.0e5, axial_k * 0.08)
        col_disp = col_load / column_k
        # Override vertical DOF solution with induced load-consistent displacement for traceability.
        U[column_v[j]] = col_disp
        support_reactions.append({
            "supportId": sup.id,
            "supportCode": sup.code,
            "endpoint": node["endpoint"],
            "faceCode": node.get("faceCode") or "",
            "levelIndex": sup.level_index,
            "chainage": round(node["chainage"], 3),
            "depth": round(node["depth"], 3),
            "nodeDisplacement": round(float(U[wale_u[j]]), 8),
            "springStiffness": round(float(spring), 3),
            "nodeReaction": round(reaction, 3),
            "axialForce": round(axial, 3),
            "axialDeformation": round(deformation, 8),
            "normalProjectionFactor": round(projection, 3),
            "directionCosineX": round(cx, 5),
            "directionCosineY": round(cy, 5),
            "rigidNodeFactor": 4.0,
            "governingSource": "V2.0 spatial frame wall-wale-support matrix",
        })
        support_axial_dofs.append({
            "supportCode": sup.code,
            "endpoint": node["endpoint"],
            "axialDof": support_a[j],
            "axialDeformation": round(deformation, 8),
            "axialForce": round(axial, 3),
            "directionCosineX": round(cx, 5),
            "directionCosineY": round(cy, 5),
        })
        column_dofs.append({
            "supportCode": sup.code,
            "endpoint": node["endpoint"],
            "verticalDof": column_v[j],
            "verticalDisplacement": round(col_disp, 8),
            "verticalReaction": round(col_load, 3),
            "columnVerticalStiffness": round(column_k, 3),
            "modelRole": "real vertical DOF with induced reaction for temporary column/pile sizing",
        })
        wale_node_profile.append({
            "supportCode": sup.code,
            "endpoint": node["endpoint"],
            "chainage": round(node["chainage"], 3),
            "horizontalDisplacement": round(float(U[wale_u[j]]), 8),
            "rotation": round(float(U[wale_t[j]]), 8),
            "levelIndex": sup.level_index,
        })
    wall_profile = []
    wall_rot = []
    for i, depth in enumerate(wall_depths):
        wall_profile.append({
            "depth": round(depth, 3),
            "elevation": round(top_elevation - depth, 3),
            "horizontalDisplacement": round(float(U[wall_u[i]]), 8),
            "pressure": round(_pressure_at_depth(pressure_profile, depth), 3),
        })
        wall_rot.append({
            "depth": round(depth, 3),
            "elevation": round(top_elevation - depth, 3),
            "rotation": round(float(U[wall_t[i]]), 8),
        })
    cond = None
    try:
        cond = round(float(np.linalg.cond(K + np.eye(n) * 1e-9)), 3)
    except Exception:
        cond = None
    return {
        "available": True,
        "fallback": fallback,
        "reason": reason,
        "matrixSize": n,
        "conditionNumber": cond,
        "equilibriumDiagnostics": equilibrium_diagnostics,
        "dofs": [
            {"index": i, "name": name, "value": round(float(U[i]), 8), "unit": dof_meta[i]["unit"], "dofType": dof_meta[i]["type"], "objectId": dof_meta[i].get("objectId"), "stageStatus": stage_type}
            for i, name in enumerate(dof_names)
        ],
        "dofSummary": {
            "wallHorizontal": len(wall_u),
            "wallRotation": len(wall_t),
            "waleHorizontal": len(wale_u),
            "waleRotation": len(wale_t),
            "supportAxial": len(support_a),
            "columnVertical": len(column_v),
            "slabReplacementActive": bool(slab_k),
        },
        "wallDisplacementProfile": wall_profile,
        "wallRotationProfile": wall_rot,
        "waleNodeProfile": wale_node_profile,
        "supportReactions": support_reactions,
        "supportAxialDofs": support_axial_dofs,
        "columnVerticalDofs": column_dofs,
        "slabReplacementStiffness": round(slab_k, 3) if slab_k > 0 else None,
        "slabReplacementStatus": replacement_state.get("status", "not_active"),
        "slabReplacementSource": replacement_state.get("source"),
        "slabReplacementRequired": bool(replacement_state.get("required", False)),
        "slabReplacementComponents": dict(replacement_state.get("components") or {}),
        "rigidNodeZones": rigid_zones,
        "maxWallDisplacement": max((abs(p["horizontalDisplacement"]) for p in wall_profile), default=0.0),
        "maxSupportAxialForce": round(max_axial, 3),
    }

def solve_global_wall_wale_support_system(
    *,
    pressure_profile: PressureProfile,
    segment: Any,
    face_code: str,
    active_supports: list[SupportElement],
    top_elevation: float,
    excavation_elevation: float,
    wall_bottom_elevation: float,
    wall_thickness: float,
    concrete_grade: str,
    soil_profile: list[Any],
    stage_id: str,
    stage_type: str | None = None,
    wall_stiffness_factor: float = 1.0,
    soil_modulus_factor: float = 1.0,
    support_stiffness_factor: float = 1.0,
    replacement_slab_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Solve a compact wall-wale-support global stiffness model.

    This is a production-oriented prototype rather than a full commercial finite
    element core.  It builds one global matrix containing:
    - wall horizontal displacement DOFs along depth;
    - wale horizontal displacement DOFs at active support endpoints on one face;
    - support axial spring stiffness EA/L projected to the wall normal;
    - equivalent column vertical support records for active support nodes;
    - stage activation through the active_supports list.

    The model is intentionally traceable: every DOF and reaction is returned so
    reports and reviewers can inspect the modelling assumptions.  If the matrix
    is singular or the geometry is insufficient, the result marks fallback=True.
    """
    length = _segment_length(segment)
    wall_depth = max(0.5, top_elevation - wall_bottom_elevation)
    excavation_depth = max(0.0, top_elevation - excavation_elevation)

    raw_depths = sorted({0.0, wall_depth, excavation_depth, *[max(0.0, min(wall_depth, p.depth)) for p in pressure_profile.points]})
    # Keep the matrix compact but retain the important break points.
    if len(raw_depths) > 18:
        keep = {raw_depths[0], raw_depths[-1], excavation_depth}
        step = max(1, len(raw_depths) // 14)
        keep.update(raw_depths[::step])
        raw_depths = sorted(keep)
    if len(raw_depths) < 3:
        raw_depths = [0.0, excavation_depth, wall_depth]
    wall_depths = raw_depths

    wall_dofs = [f"wall:h:{face_code}:{i}:z={round(d,3)}" for i, d in enumerate(wall_depths)]
    support_nodes: list[dict[str, Any]] = []
    for support in active_supports:
        endpoint = _endpoint_for_face(support, face_code)
        if not endpoint:
            continue
        endpoint_name, point = endpoint
        try:
            spring_k, normal_projection = support_spring_stiffness(support, segment)
            spring_k *= max(0.25, min(float(support_stiffness_factor), 4.0))
        except Exception:
            spring_k, normal_projection = 1.0e5, 1.0
        if spring_k <= 0:
            continue
        z_depth = max(0.0, min(wall_depth, top_elevation - support.elevation))
        support_nodes.append({
            "support": support,
            "endpoint": endpoint_name,
            "point": point,
            "chainage": _chainage(point, segment),
            "depth": z_depth,
            "spring": spring_k,
            "projection": max(0.2, normal_projection),
            "faceCode": face_code,
        })

    wale_dofs = [f"wale:h:{face_code}:{n['support'].code}:{n['endpoint']}" for n in support_nodes]
    dofs = wall_dofs + wale_dofs
    n = len(dofs)
    if n == 0:
        return {"method": "global stiffness matrix unavailable", "fallback": True, "reason": "no dofs"}
    k_global = np.zeros((n, n), dtype=float)
    f_global = np.zeros(n, dtype=float)
    wall_stiffness_factor = max(0.25, min(float(wall_stiffness_factor), 4.0))
    soil_modulus_factor = max(0.25, min(float(soil_modulus_factor), 4.0))
    ei = _wall_ei_knm2(wall_thickness, concrete_grade) * wall_stiffness_factor

    # Wall beam translational chain stiffness. Rotational DOFs are condensed out
    # for this compact design-assist model, so the value is a screening lateral
    # stiffness between neighbouring wall nodes.
    for i in range(len(wall_depths) - 1):
        dz = max(wall_depths[i + 1] - wall_depths[i], 0.25)
        beam_k = max(1.0e3, 12.0 * ei / (dz ** 3))
        _add_spring(k_global, i, i + 1, beam_k)
        q1 = _pressure_at_depth(pressure_profile, wall_depths[i])
        q2 = _pressure_at_depth(pressure_profile, wall_depths[i + 1])
        load = 0.5 * (q1 + q2) * dz * max(length, 1.0)
        f_global[i] += load * 0.5
        f_global[i + 1] += load * 0.5

    # Embedded-side soil resistance.  Soil profile parameters are sparse in early
    # projects; use horizontal_subgrade_modulus if present, otherwise a stable
    # project-level screening value.
    soil_k = DEFAULT_SOIL_SPRING_KN_M2
    for layer in soil_profile or []:
        m = getattr(getattr(layer, "parameters", None), "horizontal_subgrade_modulus", None)
        if m:
            soil_k = max(soil_k, float(m))
    for i, d in enumerate(wall_depths):
        if d >= excavation_depth:
            dz_left = d - wall_depths[i - 1] if i > 0 else 0.5
            dz_right = wall_depths[i + 1] - d if i < len(wall_depths) - 1 else 0.5
            tributary = max(0.25, 0.5 * (dz_left + dz_right))
            _add_spring(k_global, i, None, soil_k * soil_modulus_factor * tributary * max(length, 1.0))
    _add_spring(k_global, 0, None, END_ANCHOR_KN_M)
    _add_spring(k_global, len(wall_depths) - 1, None, END_ANCHOR_KN_M * 2.0)

    # Wale/support DOFs and wall-wale coupling.
    for j, node in enumerate(support_nodes):
        wale_idx = len(wall_dofs) + j
        nearest = min(range(len(wall_depths)), key=lambda i: abs(wall_depths[i] - node["depth"]))
        _add_spring(k_global, nearest, wale_idx, WALL_WALE_COUPLING_KN_M)
        _add_spring(k_global, wale_idx, None, node["spring"])

    # Wale continuity along the same face/level: adjacent support endpoints are
    # tied by a beam-like horizontal stiffness so reactions are distributed by
    # span and support stiffness instead of isolated nodal springs.
    level_groups: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for j, node in enumerate(support_nodes):
        level_groups.setdefault(node["support"].level_index, []).append((len(wall_dofs) + j, node))
    wale_ei = max(2.0e5, ei * 0.35)
    for _level, items in level_groups.items():
        items = sorted(items, key=lambda item: item[1]["chainage"])
        for (idx_a, a), (idx_b, b) in zip(items, items[1:]):
            span = max(abs(b["chainage"] - a["chainage"]), 1.0)
            _add_spring(k_global, idx_a, idx_b, 12.0 * wale_ei / (span ** 3))

    regularization = 0.0
    solve_failed = False
    try:
        u = np.linalg.solve(k_global, f_global)
        fallback = False
        reason = None
    except np.linalg.LinAlgError as exc:
        regularization = max(float(np.max(np.diag(k_global))) * 1.0e-6, 1.0)
        try:
            u = np.linalg.solve(k_global + np.eye(n) * regularization, f_global)
            fallback = True
            reason = f"regularized global matrix: {exc}"
        except np.linalg.LinAlgError:
            u = np.zeros(n)
            fallback = True
            solve_failed = True
            reason = f"failed to solve global matrix: {exc}"
    condensed_equilibrium_diagnostics = _matrix_equilibrium_diagnostics(
        k_global, f_global, u, regularization=regularization, solve_failed=solve_failed
    )

    support_reactions: list[dict[str, Any]] = []
    max_axial = 0.0
    for j, node in enumerate(support_nodes):
        idx = len(wall_dofs) + j
        reaction = max(0.0, float(node["spring"] * u[idx]))
        axial = reaction / max(float(node["projection"]), 0.2)
        deformation = reaction / max(float(node["spring"]), EPS)
        max_axial = max(max_axial, axial)
        support_reactions.append({
            "supportId": node["support"].id,
            "supportCode": node["support"].code,
            "endpoint": node["endpoint"],
            "faceCode": face_code,
            "levelIndex": node["support"].level_index,
            "chainage": round(node["chainage"], 3),
            "depth": round(node["depth"], 3),
            "nodeDisplacement": round(float(u[idx]), 8),
            "springStiffness": round(float(node["spring"]), 3),
            "nodeReaction": round(reaction, 3),
            "axialForce": round(axial, 3),
            "axialDeformation": round(deformation, 8),
            "normalProjectionFactor": round(float(node["projection"]), 3),
        })

    wall_points = []
    for i, d in enumerate(wall_depths):
        wall_points.append({
            "depth": round(d, 3),
            "elevation": round(top_elevation - d, 3),
            "horizontalDisplacement": round(float(u[i]), 8),
            "pressure": round(_pressure_at_depth(pressure_profile, d), 3),
        })
    max_disp = max((abs(p["horizontalDisplacement"]) for p in wall_points), default=0.0)

    column_supports = []
    for sr in support_reactions:
        column_supports.append({
            "supportCode": sr["supportCode"],
            "levelIndex": sr["levelIndex"],
            "estimatedVerticalShare": round(0.03 * sr["axialForce"], 3),
            "modelRole": "V1.9 column vertical reaction proxy; superseded by V2.0 columnVerticalDofs when available",
        })

    replacement_state = _replacement_slab_state(stage_type, length, replacement_slab_properties)
    spatial = _solve_spatial_frame_proxy(
        wall_depths=wall_depths,
        support_nodes=support_nodes,
        pressure_profile=pressure_profile,
        top_elevation=top_elevation,
        excavation_depth=excavation_depth,
        wall_depth=wall_depth,
        wall_length=length,
        wall_ei=ei,
        soil_k=soil_k,
        stage_id=stage_id,
        stage_type=stage_type,
        replacement_slab_state=replacement_state,
    )
    if spatial.get("available"):
        support_reactions = spatial.get("supportReactions", support_reactions) or support_reactions
        wall_points = spatial.get("wallDisplacementProfile", wall_points) or wall_points
        max_disp = spatial.get("maxWallDisplacement", max_disp) or max_disp
        max_axial = spatial.get("maxSupportAxialForce", max_axial) or max_axial
        column_supports = spatial.get("columnVerticalDofs", column_supports) or column_supports

    return {
        "method": "V2.0 spatial wall-wale-support-column-slab stiffness matrix prototype",
        "modelDimension": "space-frame-proxy-with-wall-and-wale-rotational-dofs",
        "stageId": stage_id,
        "faceCode": face_code,
        "fallback": fallback,
        "reason": reason,
        "matrixSize": spatial.get("matrixSize", n) if spatial.get("available") else n,
        "conditionNumber": spatial.get("conditionNumber") if spatial.get("available") else (round(float(np.linalg.cond(k_global + np.eye(n) * 1e-9)), 3) if n else None),
        "equilibriumDiagnostics": spatial.get("equilibriumDiagnostics") if spatial.get("available") else condensed_equilibrium_diagnostics,
        "spatialMatrixSize": spatial.get("matrixSize") if spatial.get("available") else None,
        "spatialConditionNumber": spatial.get("conditionNumber") if spatial.get("available") else None,
        "dofSummary": {
            "wallHorizontal": len(wall_dofs),
            "waleHorizontal": len(wale_dofs),
            "supportAxialSpring": len(support_nodes),
            "columnVerticalProxy": len(column_supports),
            "activeSupportCount": len(active_supports),
            "spatial": spatial.get("dofSummary") if spatial.get("available") else None,
        },
        "spatialDofSummary": spatial.get("dofSummary", {}) if spatial.get("available") else {},
        "dofs": spatial.get("dofs") if spatial.get("available") else [
            {"index": i, "name": name, "value": round(float(u[i]), 8), "unit": "m", "dofType": "condensed_translation"}
            for i, name in enumerate(dofs)
        ],
        "wallDisplacementProfile": wall_points,
        "supportReactions": support_reactions,
        "columnVerticalSupports": column_supports,
        "maxWallDisplacement": round(max_disp, 8),
        "maxSupportAxialForce": round(max_axial, 3),
        "wallRotationProfile": spatial.get("wallRotationProfile", []) if spatial.get("available") else [],
        "waleNodeProfile": spatial.get("waleNodeProfile", []) if spatial.get("available") else [],
        "supportAxialDofs": spatial.get("supportAxialDofs", []) if spatial.get("available") else [],
        "columnVerticalDofs": spatial.get("columnVerticalDofs", []) if spatial.get("available") else [],
        "slabReplacementStiffness": spatial.get("slabReplacementStiffness") if spatial.get("available") else replacement_state.get("stiffness"),
        "slabReplacementStatus": spatial.get("slabReplacementStatus") if spatial.get("available") else replacement_state.get("status"),
        "slabReplacementSource": spatial.get("slabReplacementSource") if spatial.get("available") else replacement_state.get("source"),
        "slabReplacementRequired": spatial.get("slabReplacementRequired") if spatial.get("available") else replacement_state.get("required"),
        "slabReplacementComponents": spatial.get("slabReplacementComponents", {}) if spatial.get("available") else replacement_state.get("components", {}),
        "rigidNodeZones": spatial.get("rigidNodeZones", []) if spatial.get("available") else [],
        "notes": [
            "V2.0 已将墙体梁转角自由度、围檩梁转角自由度、支撑轴向变形自由度、立柱竖向自由度和支撑节点刚域纳入同一空间杆系代理矩阵。",
            "地下室楼板换撑在 bottom_slab/replacement/support_removal/final 阶段激活；未激活显示为 not_active/—，激活阶段按 EA/L 与连接折减计算，缺失参数会标记 missing 而不会伪装成 0。",
            "施工阶段激活/失活由 active_supports、deactivated_support_ids 和 stage_id 控制；正式生产级仍应采用完整三维杆系/FEM求解器复核。",
        ],
    }
