from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from app.calculation.numerical_conditioning import ConditionThresholds, solve_scaled_symmetric
from app.schemas.domain import Project

_EPS = 1.0e-10


def _point_key(x: float, y: float, z: float) -> tuple[float, float, float]:
    return (round(float(x), 5), round(float(y), 5), round(float(z), 5))


def _distance_xy(point: Any, a: Any, b: Any) -> float:
    dx, dy = float(b.x) - float(a.x), float(b.y) - float(a.y)
    denom = dx * dx + dy * dy
    if denom <= _EPS:
        return math.hypot(float(point.x) - float(a.x), float(point.y) - float(a.y))
    t = max(0.0, min(1.0, ((float(point.x) - float(a.x)) * dx + (float(point.y) - float(a.y)) * dy) / denom))
    px, py = float(a.x) + t * dx, float(a.y) + t * dy
    return math.hypot(float(point.x) - px, float(point.y) - py)


def _material_e_g(material: Any) -> tuple[float, float]:
    explicit = getattr(material, "elastic_modulus", None)
    if explicit:
        e = float(explicit)
        if e < 1.0e6:
            e *= 1000.0
    else:
        grade = str(getattr(material, "grade", "")).upper()
        e = 2.06e8 if grade.startswith(("Q", "S")) else 3.15e7
    nu = 0.30 if e > 1.0e8 else 0.20
    return e, e / (2.0 * (1.0 + nu))


def _section_props(section: Any) -> dict[str, float]:
    d = float(getattr(section, "diameter", 0.0) or 0.0)
    t = float(getattr(section, "wall_thickness", 0.0) or 0.0)
    b = float(getattr(section, "width", 0.0) or 0.0)
    h = float(getattr(section, "height", 0.0) or 0.0)
    if d > 0.0:
        inner = max(d - 2.0 * max(t, 0.0), 0.0)
        area = math.pi * (d**2 - inner**2) / 4.0 if t > 0.0 else math.pi * d**2 / 4.0
        iy = iz = math.pi * (d**4 - inner**4) / 64.0 if t > 0.0 else math.pi * d**4 / 64.0
        j = 2.0 * iy
    else:
        b = max(b, 0.20)
        h = max(h, 0.20)
        area = b * h
        iy = b * h**3 / 12.0
        iz = h * b**3 / 12.0
        # Saint-Venant approximation for a solid rectangle.
        short, long = min(b, h), max(b, h)
        beta = max(0.141, 1.0 / 3.0 - 0.21 * short / long * (1.0 - short**4 / (12.0 * long**4)))
        j = beta * long * short**3
    return {"A": area, "Iy": iy, "Iz": iz, "J": max(j, _EPS)}


def beam_local_stiffness_3d(e: float, g: float, a: float, iy: float, iz: float, j: float, length: float) -> np.ndarray:
    """12x12 Euler-Bernoulli 3-D beam stiffness in local coordinates."""
    l = max(float(length), 1.0e-6)
    k = np.zeros((12, 12), dtype=float)
    ea = e * a / l
    gj = g * j / l
    k[0, 0] = k[6, 6] = ea
    k[0, 6] = k[6, 0] = -ea
    k[3, 3] = k[9, 9] = gj
    k[3, 9] = k[9, 3] = -gj

    # Bending in local x-y plane, rotation about z, using Iz.
    c1, c2, c3, c4 = 12 * e * iz / l**3, 6 * e * iz / l**2, 4 * e * iz / l, 2 * e * iz / l
    ids = [1, 5, 7, 11]
    block = np.array([[c1, c2, -c1, c2], [c2, c3, -c2, c4], [-c1, -c2, c1, -c2], [c2, c4, -c2, c3]])
    k[np.ix_(ids, ids)] += block

    # Bending in local x-z plane, rotation about y, using Iy.
    c1, c2, c3, c4 = 12 * e * iy / l**3, 6 * e * iy / l**2, 4 * e * iy / l, 2 * e * iy / l
    ids = [2, 4, 8, 10]
    block = np.array([[c1, -c2, -c1, -c2], [-c2, c3, c2, c4], [-c1, c2, c1, c2], [-c2, c4, c2, c3]])
    k[np.ix_(ids, ids)] += block
    return k


def _rotation_matrix(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
    vector = b - a
    length = float(np.linalg.norm(vector))
    if length <= _EPS:
        raise ValueError("zero-length 3-D member")
    ex = vector / length
    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(ex, reference))) > 0.95:
        reference = np.array([0.0, 1.0, 0.0])
    ey = np.cross(reference, ex)
    ey /= max(float(np.linalg.norm(ey)), _EPS)
    ez = np.cross(ex, ey)
    ez /= max(float(np.linalg.norm(ez)), _EPS)
    return np.vstack([ex, ey, ez]), length


def _beam_transform(rotation: np.ndarray) -> np.ndarray:
    t = np.zeros((12, 12), dtype=float)
    for start in (0, 3, 6, 9):
        t[start:start + 3, start:start + 3] = rotation
    return t


def _truss_global_stiffness(ea_over_l: float, direction: np.ndarray) -> np.ndarray:
    c = np.asarray(direction, dtype=float).reshape(3, 1)
    k3 = float(ea_over_l) * (c @ c.T)
    k = np.zeros((12, 12), dtype=float)
    k[0:3, 0:3] = k3
    k[0:3, 6:9] = -k3
    k[6:9, 0:3] = -k3
    k[6:9, 6:9] = k3
    return k


def _find_ring_endpoint(support: Any, beams: list[Any]) -> tuple[Any, Any] | None:
    candidates = []
    for endpoint_name, endpoint in (("start", support.start), ("end", support.end)):
        distance = min(
            (_distance_xy(endpoint, beam.axis.points[0], beam.axis.points[-1]) for beam in beams if int(getattr(beam, "support_level", 0) or 0) == int(support.level_index or 0)),
            default=1.0e9,
        )
        candidates.append((distance, endpoint_name, endpoint))
    candidates.sort(key=lambda row: row[0])
    if not candidates or candidates[0][0] > 0.25:
        return None
    ring_name = candidates[0][1]
    return (support.start, support.end) if ring_name == "end" else (support.end, support.start)


def analyze_global_six_dof_verification(project: Project) -> dict[str, Any]:
    def store(payload: dict[str, Any]) -> dict[str, Any]:
        project.advanced_engineering["sixDofSpatialVerification"] = payload
        return payload

    system = project.retaining_system
    if not system or not project.design_settings.enable_six_dof_verification:
        return store({"schema": "pitguard-global-6dof-verification-v1", "status": "not_applicable", "analysisMode": "disabled"})
    beams = [
        beam for beam in (system.ring_beams or [])
        if str(getattr(beam, "beam_role", "")).startswith("transfer_") or str(beam.code).startswith(("TR-", "TF-", "TB-"))
    ]
    supports = [support for support in (system.supports or []) if str(support.support_role) == "ring_strut"]
    if not beams or not supports:
        return store({"schema": "pitguard-global-6dof-verification-v1", "status": "not_applicable", "analysisMode": "no transfer system"})

    nodes: dict[tuple[float, float, float], int] = {}
    node_xyz: list[np.ndarray] = []
    fixed_nodes: set[int] = set()
    members: list[dict[str, Any]] = []

    def node_index(x: float, y: float, z: float) -> int:
        key = _point_key(x, y, z)
        if key not in nodes:
            nodes[key] = len(node_xyz)
            node_xyz.append(np.array(key, dtype=float))
        return nodes[key]

    rotational_factor = max(0.05, min(float(project.design_settings.joint_rotational_stiffness_factor), 2.0))
    rigid_factor = max(0.0, min(float(project.design_settings.rigid_zone_length_factor), 0.25))

    # Ring struts frequently land inside a transfer-beam span.  The six-DOF
    # verification model must split that beam at the attachment point; otherwise
    # the strut end becomes an unconnected node and creates a false mechanism.
    support_pairs: list[dict[str, Any]] = []
    split_points: dict[int, list[Any]] = defaultdict(list)
    for support in supports:
        pair = _find_ring_endpoint(support, beams)
        if pair is None:
            continue
        wall_point, ring_point = pair
        same_level = [beam for beam in beams if abs(float(beam.elevation) - float(support.elevation)) <= 1.0e-4]
        candidates = same_level or beams
        nearest = min(
            candidates,
            key=lambda beam: _distance_xy(ring_point, beam.axis.points[0], beam.axis.points[-1]),
        )
        support_pairs.append({"support": support, "wallPoint": wall_point, "ringPoint": ring_point, "beam": nearest})
        split_points[id(nearest)].append(ring_point)

    column_restraints: list[tuple[Any, float]] = []
    beam_levels = sorted({round(float(beam.elevation), 5) for beam in beams})
    for column in system.columns or []:
        point = column.location
        for level in beam_levels:
            same_level = [beam for beam in beams if abs(float(beam.elevation) - level) <= 1.0e-4]
            if not same_level:
                continue
            nearest = min(same_level, key=lambda beam: _distance_xy(point, beam.axis.points[0], beam.axis.points[-1]))
            distance = _distance_xy(point, nearest.axis.points[0], nearest.axis.points[-1])
            if distance <= 0.35:
                split_points[id(nearest)].append(point)
                column_restraints.append((point, level))

    for beam in beams:
        p1, p2 = beam.axis.points[0], beam.axis.points[-1]
        dx, dy = float(p2.x) - float(p1.x), float(p2.y) - float(p1.y)
        denom = max(dx * dx + dy * dy, _EPS)
        points = [p1, p2, *split_points.get(id(beam), [])]
        keyed: dict[tuple[float, float], tuple[float, Any]] = {}
        for point in points:
            t = ((float(point.x) - float(p1.x)) * dx + (float(point.y) - float(p1.y)) * dy) / denom
            t = max(0.0, min(1.0, t))
            x, y = float(p1.x) + t * dx, float(p1.y) + t * dy
            keyed[(round(x, 5), round(y, 5))] = (t, type("SplitPoint", (), {"x": x, "y": y})())
        ordered = [row[1] for row in sorted(keyed.values(), key=lambda row: row[0])]
        for segment_index, (a_point, b_point) in enumerate(zip(ordered, ordered[1:]), start=1):
            z = float(beam.elevation)
            i, jn = node_index(a_point.x, a_point.y, z), node_index(b_point.x, b_point.y, z)
            a, b = node_xyz[i], node_xyz[jn]
            rotation, gross_length = _rotation_matrix(a, b)
            props = _section_props(beam.section)
            e, g = _material_e_g(beam.material)
            depth = max(float(beam.section.height or beam.section.diameter or 0.8), 0.2)
            rigid_zone = min(0.20 * gross_length, max(0.0, rigid_factor * depth))
            effective_length = max(gross_length - 2.0 * rigid_zone, 0.25 * gross_length)
            local = beam_local_stiffness_3d(
                e, g, props["A"], props["Iy"] * rotational_factor,
                props["Iz"] * rotational_factor, props["J"] * rotational_factor,
                effective_length,
            )
            transform = _beam_transform(rotation)
            members.append({
                "type": "beam", "code": f"{beam.code}:S{segment_index}", "parentCode": beam.code,
                "i": i, "j": jn, "local": local, "transform": transform,
                "global": transform.T @ local @ transform,
                "grossLengthM": gross_length, "effectiveLengthM": effective_length,
            })

    support_rows: list[dict[str, Any]] = []
    for pair in support_pairs:
        support, wall_point, ring_point = pair["support"], pair["wallPoint"], pair["ringPoint"]
        z = float(support.elevation)
        i = node_index(wall_point.x, wall_point.y, z)
        jn = node_index(ring_point.x, ring_point.y, z)
        fixed_nodes.add(i)
        a, b = node_xyz[i], node_xyz[jn]
        vector = b - a
        length = max(float(np.linalg.norm(vector)), 1.0e-6)
        direction = vector / length
        props = _section_props(support.section)
        e, _g = _material_e_g(support.material)
        global_k = _truss_global_stiffness(e * props["A"] / length, direction)
        members.append({"type": "truss", "code": support.code, "i": i, "j": jn, "global": global_k, "direction": direction, "lengthM": length, "EA": e * props["A"]})
        support_rows.append({"support": support, "wallNode": i, "ringNode": jn, "direction": direction})

    ndof = 6 * len(node_xyz)
    if ndof == 0 or not fixed_nodes:
        return store({"schema": "pitguard-global-6dof-verification-v1", "status": "fail", "blocked": True, "message": "空间模型缺少有效节点或边界约束。"})
    k = np.zeros((ndof, ndof), dtype=float)
    f = np.zeros(ndof, dtype=float)
    for member in members:
        dofs = [6 * member["i"] + q for q in range(6)] + [6 * member["j"] + q for q in range(6)]
        k[np.ix_(dofs, dofs)] += member["global"]

    # Apply support action to the transfer-system endpoint. The wall endpoint is
    # fixed and the support truss remains in the model, so the verification
    # captures axial compatibility and spatial beam response.
    for row in support_rows:
        support = row["support"]
        force = abs(float(getattr(support, "design_axial_force", 0.0) or 0.0))
        if force <= 0.0:
            continue
        direction = row["direction"]
        ring = row["ringNode"]
        f[6 * ring:6 * ring + 3] += -force * direction

    fixed_dofs = {6 * node + q for node in fixed_nodes for q in range(6)}
    vertical_restraint_nodes: set[int] = set()
    for point, level in column_restraints:
        key = _point_key(point.x, point.y, level)
        if key in nodes:
            node = nodes[key]
            vertical_restraint_nodes.add(node)
            fixed_dofs.add(6 * node + 2)
    free = [idx for idx in range(ndof) if idx not in fixed_dofs]
    if not free:
        return store({"schema": "pitguard-global-6dof-verification-v1", "status": "fail", "blocked": True, "message": "空间模型没有自由自由度。"})
    kff = k[np.ix_(free, free)]
    ff = f[free]
    u_free, numerical = solve_scaled_symmetric(kff, ff, thresholds=ConditionThresholds(), allow_screening_regularization=False)
    if u_free is None or numerical.get("blocked"):
        return store({
            "schema": "pitguard-global-6dof-verification-v1", "status": "fail", "blocked": True,
            "analysisMode": "global-6dof-linear-space-frame-verification",
            "message": "六自由度空间验证模型病态、秩亏或求解失败。",
            "nodeCount": len(node_xyz), "memberCount": len(members), "numericalGate": numerical,
        })
    u = np.zeros(ndof, dtype=float)
    u[free] = u_free
    residual = k @ u - f
    free_residual = residual[free]
    relative_residual = float(np.linalg.norm(free_residual) / max(np.linalg.norm(ff), 1.0))

    member_rows: list[dict[str, Any]] = []
    max_moment = max_torsion = max_axial = 0.0
    for member in members:
        dofs = [6 * member["i"] + q for q in range(6)] + [6 * member["j"] + q for q in range(6)]
        ug = u[dofs]
        if member["type"] == "beam":
            ul = member["transform"] @ ug
            fl = member["local"] @ ul
            axial = max(abs(float(fl[0])), abs(float(fl[6])))
            torsion = max(abs(float(fl[3])), abs(float(fl[9])))
            moment = max(abs(float(fl[4])), abs(float(fl[5])), abs(float(fl[10])), abs(float(fl[11])))
            max_axial, max_torsion, max_moment = max(max_axial, axial), max(max_torsion, torsion), max(max_moment, moment)
            member_rows.append({"code": member["code"], "type": "beam", "axialKn": axial, "torsionKnm": torsion, "maximumBendingMomentKnm": moment})
        else:
            direction = member["direction"]
            relative = u[6 * member["j"]:6 * member["j"] + 3] - u[6 * member["i"]:6 * member["i"] + 3]
            axial = abs(float(member["EA"] / member["lengthM"] * np.dot(relative, direction)))
            max_axial = max(max_axial, axial)
            member_rows.append({"code": member["code"], "type": "truss", "axialKn": axial})

    translations = [float(np.linalg.norm(u[6 * idx:6 * idx + 3])) for idx in range(len(node_xyz))]
    rotations = [float(np.linalg.norm(u[6 * idx + 3:6 * idx + 6])) for idx in range(len(node_xyz))]
    max_translation = max(translations, default=0.0)
    max_rotation = max(rotations, default=0.0)

    planar = dict((project.advanced_engineering or {}).get("concaveTransferFrameAnalysis") or {})
    planar_disp = float(planar.get("maximumDisplacementM") or planar.get("maxDisplacementM") or 0.0)
    planar_moment = 0.0
    for values in (planar.get("beamEnvelope") or {}).values():
        if isinstance(values, dict):
            planar_moment = max(planar_moment, abs(float(values.get("maxMoment") or values.get("maximumMomentKnm") or 0.0)))
    displacement_difference = abs(max_translation - planar_disp) / max(abs(planar_disp), 1.0e-9) if planar_disp > 0.0 else None
    moment_difference = abs(max_moment - planar_moment) / max(abs(planar_moment), 1.0e-9) if planar_moment > 0.0 else None
    tolerance_d = float(project.design_settings.verification_displacement_tolerance_ratio)
    tolerance_f = float(project.design_settings.verification_force_tolerance_ratio)
    differences = [value for value in (displacement_difference, moment_difference) if value is not None]
    max_difference = max(differences, default=0.0)
    if relative_residual > 1.0e-6:
        status = "fail"
    elif max_difference > max(tolerance_d, tolerance_f) * 2.0 or max_rotation > 0.03:
        status = "fail"
    elif max_difference > max(tolerance_d, tolerance_f) or max_rotation > 0.01 or numerical.get("status") == "warning":
        status = "warning"
    else:
        status = "pass"
    result = {
        "schema": "pitguard-global-6dof-verification-v1",
        "analysisMode": "global-6dof-linear-space-frame-verification",
        "status": status,
        "blocked": status == "fail",
        "nodeCount": len(node_xyz),
        "memberCount": len(members),
        "beamCount": sum(member["type"] == "beam" for member in members),
        "supportTrussCount": sum(member["type"] == "truss" for member in members),
        "fixedNodeCount": len(fixed_nodes),
        "columnVerticalRestraintNodeCount": len(vertical_restraint_nodes),
        "matrixSize": len(free),
        "maximumTranslationM": max_translation,
        "maximumRotationRad": max_rotation,
        "maximumBeamMomentKnm": max_moment,
        "maximumBeamTorsionKnm": max_torsion,
        "maximumMemberAxialKn": max_axial,
        "relativeEquilibriumResidual": relative_residual,
        "numericalGate": numerical,
        "planarComparison": {
            "planarMaximumDisplacementM": planar_disp or None,
            "sixDofMaximumDisplacementM": max_translation,
            "displacementRelativeDifference": displacement_difference,
            "planarMaximumBeamMomentKnm": planar_moment or None,
            "sixDofMaximumBeamMomentKnm": max_moment,
            "momentRelativeDifference": moment_difference,
            "maximumRelativeDifference": max_difference,
            "displacementToleranceRatio": tolerance_d,
            "forceToleranceRatio": tolerance_f,
        },
        "memberEnvelope": sorted(member_rows, key=lambda row: max(float(row.get("maximumBendingMomentKnm") or 0.0), float(row.get("axialKn") or 0.0)), reverse=True)[:200],
        "modelBoundary": "全局六自由度线弹性杆系验证已考虑轴向、双向弯曲、扭转、刚域折减和半刚性刚度折减；土体非线性、接触、材料开裂和二阶效应仍需高级复核。",
    }
    return store(result)
