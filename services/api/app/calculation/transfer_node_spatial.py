from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from app.calculation.numerical_conditioning import solve_scaled_symmetric
from app.schemas.domain import Point2D, Project


def _distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))


def _point_segment_distance(point: Point2D, a: Point2D, b: Point2D) -> float:
    dx, dy = float(b.x) - float(a.x), float(b.y) - float(a.y)
    length2 = dx * dx + dy * dy
    if length2 <= 1.0e-12:
        return _distance(point, a)
    t = max(0.0, min(1.0, ((float(point.x) - float(a.x)) * dx + (float(point.y) - float(a.y)) * dy) / length2))
    projection = Point2D(x=float(a.x) + t * dx, y=float(a.y) + t * dy)
    return _distance(point, projection)


def _beam_properties(beam: Any) -> tuple[float, float, float, float, float]:
    width = max(float(beam.section.width or beam.section.diameter or 1.0), 0.2)
    height = max(float(beam.section.height or beam.section.diameter or width), 0.2)
    e = 3.25e7 if str(getattr(beam.material, "grade", "")).upper().startswith("C40") else 3.0e7
    g = e / (2.0 * (1.0 + 0.2))
    i = width * height**3 / 12.0
    # Saint-Venant torsional constant approximation for a rectangular section.
    b, h = min(width, height), max(width, height)
    beta = max(0.141, (1.0 / 3.0) - 0.21 * (b / h) * (1.0 - (b**4) / (12.0 * h**4)))
    j = beta * h * b**3
    return e, g, i, j, max(width, height)


def solve_spatial_node_rotations(
    moment_vector_knm: np.ndarray | list[float] | tuple[float, float, float],
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Solve a reduced three-rotation transfer-node stiffness system.

    Each incident member contributes torsional stiffness about its own axis,
    out-of-plane bending stiffness about the in-plane normal, and in-plane
    bending stiffness about the global vertical axis.  The far end of each
    member is represented as fixed after rigid-zone shortening and semi-rigid
    stiffness reduction.  This is an auditable node submodel, not a substitute
    for a global six-degree-of-freedom spatial frame.
    """
    applied = np.asarray(moment_vector_knm, dtype=float).reshape(3)
    stiffness = np.zeros((3, 3), dtype=float)
    prepared: list[dict[str, Any]] = []
    for member in members:
        bx, by = (float(value) for value in member["axis"])
        axis_norm = max(math.hypot(bx, by), 1.0e-12)
        e_axis = np.array([bx / axis_norm, by / axis_norm, 0.0], dtype=float)
        n_axis = np.array([-e_axis[1], e_axis[0], 0.0], dtype=float)
        z_axis = np.array([0.0, 0.0, 1.0], dtype=float)
        kt = max(float(member["torsionStiffness"]), 1.0e-9)
        kb_out = max(float(member.get("outOfPlaneBendingStiffness", member["bendingStiffness"])), 1.0e-9)
        kb_in = max(float(member.get("inPlaneBendingStiffness", member["bendingStiffness"])), 1.0e-9)
        member_stiffness = (
            kt * np.outer(e_axis, e_axis)
            + kb_out * np.outer(n_axis, n_axis)
            + kb_in * np.outer(z_axis, z_axis)
        )
        stiffness += member_stiffness
        prepared.append({
            **member,
            "axisVector": e_axis,
            "normalVector": n_axis,
            "verticalVector": z_axis,
            "memberRotationalStiffness": member_stiffness,
            "torsionStiffness": kt,
            "outOfPlaneBendingStiffness": kb_out,
            "inPlaneBendingStiffness": kb_in,
        })

    if not prepared:
        return {
            "status": "fail",
            "blocked": True,
            "message": "节点没有可参与空间转动平衡的构件。",
            "rotationsRad": [0.0, 0.0, 0.0],
            "members": [],
        }

    rotations, numerical_gate = solve_scaled_symmetric(stiffness, applied, allow_screening_regularization=False)
    if rotations is None or numerical_gate.get("blocked"):
        return {
            "status": "fail",
            "blocked": True,
            "message": "节点三维转动刚度矩阵病态、秩亏或求解失败，已自动阻断。",
            "rotationsRad": [0.0, 0.0, 0.0],
            "stiffnessMatrixKnmPerRad": stiffness.tolist(),
            "numericalGate": numerical_gate,
            "members": [],
        }

    member_rows: list[dict[str, Any]] = []
    recovered = np.zeros(3, dtype=float)
    for member in prepared:
        vector = member["memberRotationalStiffness"] @ rotations
        recovered += vector
        e_axis = member["axisVector"]
        n_axis = member["normalVector"]
        z_axis = member["verticalVector"]
        torsion = float(np.dot(vector, e_axis))
        out_of_plane = float(np.dot(vector, n_axis))
        in_plane = float(np.dot(vector, z_axis))
        member_rows.append({
            "beam": member.get("beam"),
            "beamCode": getattr(member.get("beam"), "code", member.get("beamCode")),
            "momentVectorKnm": vector.tolist(),
            "torsionKnm": torsion,
            "outOfPlaneMomentKnm": out_of_plane,
            "inPlaneEccentricMomentKnm": in_plane,
            "torsionalRotationRad": float(np.dot(rotations, e_axis)),
            "outOfPlaneRotationRad": float(np.dot(rotations, n_axis)),
            "inPlaneRotationRad": float(np.dot(rotations, z_axis)),
            "rigidZoneLengthM": float(member.get("rigidZoneLength", 0.0)),
            "effectiveLengthM": float(member.get("effectiveLength", 0.0)),
            "torsionStiffnessKnmPerRad": float(member["torsionStiffness"]),
            "outOfPlaneBendingStiffnessKnmPerRad": float(member["outOfPlaneBendingStiffness"]),
            "inPlaneBendingStiffnessKnmPerRad": float(member["inPlaneBendingStiffness"]),
        })

    equilibrium_residual = recovered - applied
    relative_residual = float(np.linalg.norm(equilibrium_residual) / max(np.linalg.norm(applied), 1.0))
    maximum_rotation = float(np.max(np.abs(rotations)))
    status = "pass"
    if relative_residual > 1.0e-6:
        status = "fail"
    elif numerical_gate.get("status") == "warning" or maximum_rotation > 0.01:
        status = "warning"
    return {
        "status": status,
        "blocked": status == "fail",
        "message": (
            "节点三维转动平衡通过。" if status == "pass"
            else "节点三维转动平衡通过，但转角或数值条件需要复核。" if status == "warning"
            else "节点三维转动平衡未满足残差门限。"
        ),
        "rotationsRad": rotations.tolist(),
        "maximumAbsoluteRotationRad": maximum_rotation,
        "appliedMomentVectorKnm": applied.tolist(),
        "recoveredMomentVectorKnm": recovered.tolist(),
        "equilibriumResidualVectorKnm": equilibrium_residual.tolist(),
        "relativeEquilibriumResidual": relative_residual,
        "stiffnessMatrixKnmPerRad": stiffness.tolist(),
        "numericalGate": numerical_gate,
        "members": member_rows,
    }


def analyze_transfer_node_spatial_effects(project: Project, transfer_envelope: dict[str, Any]) -> dict[str, Any]:
    """Resolve eccentric support actions through reduced 3-D node submodels."""
    system = project.retaining_system
    beams = [
        beam for beam in (system.ring_beams or [])
        if str(getattr(beam, "beam_role", "")).startswith("transfer_")
        or str(beam.code).startswith(("TR-", "TF-", "TB-"))
    ]
    supports = [support for support in (system.supports or []) if str(support.support_role) == "ring_strut"]
    if not beams or not supports:
        return {"schema": "pitguard-transfer-node-spatial-v2", "status": "not_applicable", "nodeCount": 0}

    rotational_factor = max(0.05, min(float(project.design_settings.joint_rotational_stiffness_factor), 2.0))
    rigid_factor = max(0.0, min(float(project.design_settings.rigid_zone_length_factor), 0.25))
    beam_demands: dict[str, dict[str, float]] = defaultdict(lambda: {
        "maxTorsionKnm": 0.0,
        "maxOutOfPlaneMomentKnm": 0.0,
        "maxInPlaneEccentricMomentKnm": 0.0,
        "maxJointRotationRad": 0.0,
        "maximumRigidZoneLengthM": 0.0,
        "nodeCount": 0.0,
    })
    node_rows: list[dict[str, Any]] = []

    for support in supports:
        support_force = float(getattr(support, "design_axial_force", 0.0) or 0.0)
        if support_force <= 0.0:
            continue
        candidates: list[tuple[float, Any, Point2D]] = []
        for endpoint in (support.start, support.end):
            for beam in beams:
                if int(beam.support_level or 0) != int(support.level_index or 0):
                    continue
                a, b = beam.axis.points[0], beam.axis.points[-1]
                candidates.append((_point_segment_distance(endpoint, a, b), beam, endpoint))
        candidates.sort(key=lambda row: row[0])
        if not candidates or candidates[0][0] > 0.15:
            continue
        ring_point = candidates[0][2]
        incident = [row[1] for row in candidates if row[2] == ring_point and row[0] <= 0.15]
        incident = list({beam.id: beam for beam in incident}.values())
        if not incident:
            continue
        wall_point = support.start if ring_point == support.end else support.end
        dx, dy = float(ring_point.x) - float(wall_point.x), float(ring_point.y) - float(wall_point.y)
        length = max(math.hypot(dx, dy), 1.0e-9)
        fx, fy = support_force * dx / length, support_force * dy / length
        support_height = max(float(support.section.height or support.section.diameter or 0.8), 0.2)
        beam_height = max(float(incident[0].section.height or incident[0].section.diameter or 1.0), 0.2)
        ez = max(0.03, 0.5 * abs(support_height - beam_height))
        # The layout ``centerline_offset_m`` is a wall-clearance/layout value,
        # not a connection eccentricity.  The node submodel uses the actual
        # endpoint-to-beam-axis mismatch plus recorded construction deviation.
        construction_offset = abs(float(getattr(support, "construction_deviation_mm", 0.0) or 0.0)) / 1000.0
        geometric_offset = max(float(candidates[0][0]), 0.0)
        exy = max(0.02, construction_offset, geometric_offset)
        nx, ny = -dy / length, dx / length
        rx, ry, rz = exy * nx, exy * ny, ez
        # M = r x F, with Fz=0.
        mx, my, mz = -rz * fy, rz * fx, rx * fy - ry * fx
        stiffness_rows: list[dict[str, Any]] = []
        for beam in incident:
            a, b = beam.axis.points[0], beam.axis.points[-1]
            beam_dx, beam_dy = float(b.x) - float(a.x), float(b.y) - float(a.y)
            beam_length = max(math.hypot(beam_dx, beam_dy), 0.25)
            bx, by = beam_dx / beam_length, beam_dy / beam_length
            e, g, inertia, torsion_j, section_depth = _beam_properties(beam)
            rigid_zone = min(0.20 * beam_length, max(0.05, rigid_factor * section_depth))
            effective_length = max(beam_length - 2.0 * rigid_zone, 0.25 * beam_length)
            kt = max(g * torsion_j / effective_length * rotational_factor, 1.0)
            kb = max(4.0 * e * inertia / effective_length * rotational_factor, 1.0)
            stiffness_rows.append({
                "beam": beam,
                "axis": (bx, by),
                "torsionStiffness": kt,
                "bendingStiffness": kb,
                "outOfPlaneBendingStiffness": kb,
                "inPlaneBendingStiffness": kb,
                "rigidZoneLength": rigid_zone,
                "effectiveLength": effective_length,
            })

        local = solve_spatial_node_rotations([mx, my, mz], stiffness_rows)
        node_beams: list[dict[str, Any]] = []
        for item in local.get("members") or []:
            beam = item.get("beam")
            if beam is None:
                continue
            torsion = abs(float(item["torsionKnm"]))
            out_moment = abs(float(item["outOfPlaneMomentKnm"]))
            in_plane_moment = abs(float(item["inPlaneEccentricMomentKnm"]))
            joint_rotation = max(
                abs(float(item["torsionalRotationRad"])),
                abs(float(item["outOfPlaneRotationRad"])),
                abs(float(item["inPlaneRotationRad"])),
            )
            demand = beam_demands[str(beam.code)]
            demand["maxTorsionKnm"] = max(demand["maxTorsionKnm"], torsion)
            demand["maxOutOfPlaneMomentKnm"] = max(demand["maxOutOfPlaneMomentKnm"], out_moment)
            demand["maxInPlaneEccentricMomentKnm"] = max(demand["maxInPlaneEccentricMomentKnm"], in_plane_moment)
            demand["maxJointRotationRad"] = max(demand["maxJointRotationRad"], joint_rotation)
            demand["maximumRigidZoneLengthM"] = max(demand["maximumRigidZoneLengthM"], float(item["rigidZoneLengthM"]))
            demand["nodeCount"] += 1.0
            node_beams.append({
                "beamCode": beam.code,
                "torsionKnm": round(torsion, 3),
                "outOfPlaneMomentKnm": round(out_moment, 3),
                "inPlaneEccentricMomentKnm": round(in_plane_moment, 3),
                "jointRotationRad": float(f"{joint_rotation:.6e}"),
                "rigidZoneLengthM": round(float(item["rigidZoneLengthM"]), 4),
            })
        node_status = str(local.get("status") or "fail")
        node_rows.append({
            "supportId": support.id,
            "supportCode": support.code,
            "levelIndex": support.level_index,
            "location": {"x": ring_point.x, "y": ring_point.y, "z": support.elevation},
            "supportForceKn": round(support_force, 3),
            "verticalEccentricityM": round(ez, 4),
            "inPlaneEccentricityM": round(exy, 4),
            "jointMomentVectorKnm": {"mx": round(mx, 3), "my": round(my, 3), "mz": round(mz, 3)},
            "jointRotationVectorRad": {
                "rx": float((local.get("rotationsRad") or [0.0, 0.0, 0.0])[0]),
                "ry": float((local.get("rotationsRad") or [0.0, 0.0, 0.0])[1]),
                "rz": float((local.get("rotationsRad") or [0.0, 0.0, 0.0])[2]),
            },
            "semiRigidRotationalFactor": rotational_factor,
            "relativeEquilibriumResidual": local.get("relativeEquilibriumResidual"),
            "numericalGate": local.get("numericalGate"),
            "beamDemands": node_beams,
            "status": node_status,
            "message": local.get("message"),
        })

    for beam in beams:
        beam_demands.setdefault(str(beam.code), {
            "maxTorsionKnm": 0.0,
            "maxOutOfPlaneMomentKnm": 0.0,
            "maxInPlaneEccentricMomentKnm": 0.0,
            "maxJointRotationRad": 0.0,
            "maximumRigidZoneLengthM": 0.0,
            "nodeCount": 0.0,
        })
    by_code = {beam.code: beam for beam in beams}
    blocked_beams: set[str] = set()
    for row in node_rows:
        if row["status"] == "fail":
            blocked_beams.update(str(item["beamCode"]) for item in row.get("beamDemands") or [])
    for code, demand in beam_demands.items():
        beam = by_code.get(code)
        if beam is None:
            continue
        beam.design_torsion = round(float(demand["maxTorsionKnm"]), 3)
        beam.design_out_of_plane_moment = round(float(demand["maxOutOfPlaneMomentKnm"]), 3)
        beam.design_eccentric_in_plane_moment = round(float(demand["maxInPlaneEccentricMomentKnm"]), 3)
        beam.spatial_analysis_status = "fail" if code in blocked_beams else "calculated"
        envelope = (transfer_envelope.get("beamEnvelope") or {}).get(code)
        if isinstance(envelope, dict):
            envelope["maxTorsion"] = beam.design_torsion
            envelope["maxOutOfPlaneMoment"] = beam.design_out_of_plane_moment
            envelope["maxInPlaneEccentricMoment"] = beam.design_eccentric_in_plane_moment
            envelope["maxJointRotation"] = float(demand["maxJointRotationRad"])
            envelope["maximumRigidZoneLengthM"] = float(demand["maximumRigidZoneLengthM"])

    fail_count = sum(row["status"] == "fail" for row in node_rows)
    warning_count = sum(row["status"] == "warning" for row in node_rows)
    status = "fail" if fail_count else "warning" if warning_count else "pass" if node_rows else "fail"
    gates = [row.get("numericalGate") for row in node_rows if isinstance(row.get("numericalGate"), dict)]
    result = {
        "schema": "pitguard-transfer-node-spatial-v2",
        "analysisMode": "reduced-3rotation-eccentricity-torsion-rigid-zone-semirigid-joint-submodel",
        "status": status,
        "nodeCount": len(node_rows),
        "warningCount": warning_count,
        "failCount": fail_count,
        "maximumTorsionKnm": max((row["maxTorsionKnm"] for row in beam_demands.values()), default=0.0),
        "maximumOutOfPlaneMomentKnm": max((row["maxOutOfPlaneMomentKnm"] for row in beam_demands.values()), default=0.0),
        "maximumInPlaneEccentricMomentKnm": max((row["maxInPlaneEccentricMomentKnm"] for row in beam_demands.values()), default=0.0),
        "maximumJointRotationRad": max((row["maxJointRotationRad"] for row in beam_demands.values()), default=0.0),
        "maximumScaledConditionNumber": max((float(gate.get("scaledConditionNumber") or 0.0) for gate in gates), default=0.0),
        "maximumRelativeEquilibriumResidual": max((float(row.get("relativeEquilibriumResidual") or 0.0) for row in node_rows), default=0.0),
        "beamEnvelope": dict(beam_demands),
        "nodes": node_rows,
        "modelBoundary": "三转动自由度节点子模型已考虑偏心、扭转、刚域和半刚性；正式工程仍需全局 6-DOF 空间杆系或有限元复核。",
    }
    project.advanced_engineering["concaveTransferSpatialAnalysis"] = result
    return result
