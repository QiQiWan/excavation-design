from __future__ import annotations

import json
import math
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
from scipy import linalg as scipy_linalg

from app.calculation.numerical_conditioning import solve_scaled_symmetric
from app.calculation.planar_transfer_frame import _frame_stiffness, _truss_stiffness
from app.calculation.transfer_node_spatial import solve_spatial_node_rotations
from app.schemas.domain import Point2D
from app.version import ALGORITHM_VERSION, SOFTWARE_VERSION


def _assemble_case(nodes: dict[int, Point2D], frames: list[dict[str, Any]], trusses: list[dict[str, Any]], fixed: set[int], loads: dict[int, tuple[float, float, float]]) -> dict[str, Any]:
    ndof = 3 * len(nodes)
    K = np.zeros((ndof, ndof), dtype=float)
    F = np.zeros(ndof, dtype=float)
    for frame in frames:
        i, j = int(frame["i"]), int(frame["j"])
        kg, _, _ = _frame_stiffness(float(frame["E"]), float(frame["A"]), float(frame["I"]), nodes[i], nodes[j])
        dofs = [3*(i-1), 3*(i-1)+1, 3*(i-1)+2, 3*(j-1), 3*(j-1)+1, 3*(j-1)+2]
        K[np.ix_(dofs, dofs)] += kg
    for truss in trusses:
        i, j = int(truss["i"]), int(truss["j"])
        kg, _, _, _ = _truss_stiffness(float(truss["E"]), float(truss["A"]), nodes[i], nodes[j])
        dofs = [3*(i-1), 3*(i-1)+1, 3*(i-1)+2, 3*(j-1), 3*(j-1)+1, 3*(j-1)+2]
        K[np.ix_(dofs, dofs)] += kg
    for node_id, load in loads.items():
        F[3*(node_id-1):3*(node_id-1)+3] += np.asarray(load, dtype=float)
    fixed_dofs = {3*(node_id-1)+offset for node_id in fixed for offset in range(3)}
    free = np.array([index for index in range(ndof) if index not in fixed_dofs], dtype=int)
    displacement, gate = solve_scaled_symmetric(K[np.ix_(free, free)], F[free], allow_screening_regularization=False)
    if displacement is None:
        return {"status": "fail", "numericalGate": gate}
    full = np.zeros(ndof, dtype=float)
    full[free] = displacement
    reactions = K @ full - F
    return {
        "status": "pass",
        "displacements": {str(node_id): full[3*(node_id-1):3*(node_id-1)+3].tolist() for node_id in nodes},
        "reactions": {str(node_id): reactions[3*(node_id-1):3*(node_id-1)+3].tolist() for node_id in fixed},
        "numericalGate": gate,
    }


def _opensees_case(nodes: dict[int, Point2D], frames: list[dict[str, Any]], trusses: list[dict[str, Any]], fixed: set[int], loads: dict[int, tuple[float, float, float]]) -> dict[str, Any]:
    try:
        import openseespy.opensees as ops
        import openseespy  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    for node_id, point in nodes.items():
        ops.node(node_id, float(point.x), float(point.y))
    for node_id in fixed:
        ops.fix(node_id, 1, 1, 1)
    ops.geomTransf("Linear", 1)
    element_id = 1
    for frame in frames:
        ops.element(
            "elasticBeamColumn", element_id, int(frame["i"]), int(frame["j"]),
            float(frame["A"]), float(frame["E"]), float(frame["I"]), 1,
        )
        element_id += 1
    if trusses:
        material_tags: dict[tuple[float, float], int] = {}
        for truss in trusses:
            key = (float(truss["E"]), float(truss["A"]))
            if key not in material_tags:
                tag = 100 + len(material_tags)
                ops.uniaxialMaterial("Elastic", tag, key[0])
                material_tags[key] = tag
            ops.element("truss", element_id, int(truss["i"]), int(truss["j"]), key[1], material_tags[key])
            element_id += 1
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for node_id, load in loads.items():
        ops.load(node_id, *[float(value) for value in load])
    ops.system("BandGeneral")
    ops.numberer("Plain")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    result = ops.analyze(1)
    if result != 0:
        return {"status": "fail", "analysisCode": result}
    ops.reactions()
    displacements = {str(node_id): [float(value) for value in ops.nodeDisp(node_id)] for node_id in nodes}
    reactions = {str(node_id): [float(value) for value in ops.nodeReaction(node_id)] for node_id in fixed}
    try:
        version = metadata.version("openseespy")
    except metadata.PackageNotFoundError:
        version = getattr(openseespy, "__version__", None)
    ops.wipe()
    return {"status": "pass", "displacements": displacements, "reactions": reactions, "openseespyVersion": version}


def _relative_error(a: float, b: float) -> float:
    return abs(float(a) - float(b)) / max(abs(float(b)), 1.0e-12)


def _opensees_spatial_rotational_node(
    members: list[dict[str, Any]],
    moment_vector: tuple[float, float, float],
) -> dict[str, Any]:
    try:
        import openseespy.opensees as ops
        import openseespy  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    # Central translations are fixed; all three rotations remain active.
    ops.fix(1, 1, 1, 1, 0, 0, 0)
    element_id = 1
    for index, member in enumerate(members, start=2):
        bx, by = (float(value) for value in member["axis"])
        norm = max(math.hypot(bx, by), 1.0e-12)
        bx, by = bx / norm, by / norm
        length = float(member["effectiveLengthM"])
        ops.node(index, bx * length, by * length, 0.0)
        ops.fix(index, 1, 1, 1, 1, 1, 1)
        transform_tag = 100 + index
        # Horizontal members use global Z as the orientation vector.  This
        # makes local y the in-plane normal and local z the global vertical.
        ops.geomTransf("Linear", transform_tag, 0.0, 0.0, 1.0)
        ops.element(
            "elasticBeamColumn", element_id, 1, index,
            float(member["A"]), float(member["E"]), float(member["G"]),
            float(member["J"]), float(member["Iy"]), float(member["Iz"]), transform_tag,
        )
        element_id += 1
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(1, 0.0, 0.0, 0.0, *[float(value) for value in moment_vector])
    ops.system("BandGeneral")
    ops.numberer("Plain")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    result = ops.analyze(1)
    if result != 0:
        ops.wipe()
        return {"status": "fail", "analysisCode": result}
    displacement = [float(value) for value in ops.nodeDisp(1)]
    try:
        version = metadata.version("openseespy")
    except metadata.PackageNotFoundError:
        version = getattr(openseespy, "__version__", None)
    ops.wipe()
    return {
        "status": "pass",
        "rotationsRad": displacement[3:6],
        "openseespyVersion": version,
    }


def _compare_spatial_rotational_node() -> dict[str, Any]:
    raw_members = [
        {"axis": (1.0, 0.0), "effectiveLengthM": 4.0, "E": 3.0e7, "G": 1.25e7, "A": 0.90, "J": 0.050, "Iy": 0.080, "Iz": 0.080},
        {"axis": (0.0, 1.0), "effectiveLengthM": 3.2, "E": 3.0e7, "G": 1.25e7, "A": 0.90, "J": 0.050, "Iy": 0.080, "Iz": 0.080},
        {"axis": (-0.7071067811865476, 0.7071067811865476), "effectiveLengthM": 5.1, "E": 3.0e7, "G": 1.25e7, "A": 0.90, "J": 0.050, "Iy": 0.080, "Iz": 0.080},
    ]
    local_members = []
    for index, member in enumerate(raw_members, start=1):
        length = float(member["effectiveLengthM"])
        local_members.append({
            "beamCode": f"SP-{index}",
            "axis": member["axis"],
            "torsionStiffness": float(member["G"]) * float(member["J"]) / length,
            "bendingStiffness": 4.0 * float(member["E"]) * float(member["Iy"]) / length,
            "outOfPlaneBendingStiffness": 4.0 * float(member["E"]) * float(member["Iy"]) / length,
            "inPlaneBendingStiffness": 4.0 * float(member["E"]) * float(member["Iz"]) / length,
            "effectiveLength": length,
            "rigidZoneLength": 0.0,
        })
    moment = (850.0, -430.0, 260.0)
    pitguard = solve_spatial_node_rotations(moment, local_members)
    reference = _opensees_spatial_rotational_node(raw_members, moment)
    if reference.get("status") == "unavailable":
        return {
            "name": "spatial_reduced_rotational_node",
            "status": "unavailable",
            "pitguard": pitguard,
            "reference": reference,
            "referenceSoftware": "OpenSeesPy",
        }
    if pitguard.get("status") not in {"pass", "warning"} or reference.get("status") != "pass":
        return {
            "name": "spatial_reduced_rotational_node",
            "status": "fail",
            "pitguard": pitguard,
            "reference": reference,
        }
    errors = []
    components = []
    for index, label in enumerate(("rx", "ry", "rz")):
        pg = float(pitguard["rotationsRad"][index])
        ref = float(reference["rotationsRad"][index])
        error = _relative_error(pg, ref) if abs(ref) > 1.0e-11 or abs(pg) > 1.0e-11 else 0.0
        errors.append(error)
        components.append({"component": label, "pitguard": pg, "reference": ref, "relativeError": error})
    maximum = max(errors, default=0.0)
    return {
        "name": "spatial_reduced_rotational_node",
        "status": "pass" if maximum <= 1.0e-6 else "fail",
        "maximumRelativeDisplacementError": maximum,
        "tolerance": 1.0e-6,
        "components": components,
        "pitguardNumericalGate": pitguard.get("numericalGate"),
        "pitguardEquilibriumResidual": pitguard.get("relativeEquilibriumResidual"),
        "referenceSoftware": "OpenSeesPy",
        "referenceVersion": reference.get("openseespyVersion"),
    }


def _compare(name: str, nodes: dict[int, Point2D], frames: list[dict[str, Any]], trusses: list[dict[str, Any]], fixed: set[int], loads: dict[int, tuple[float, float, float]]) -> dict[str, Any]:
    pitguard = _assemble_case(nodes, frames, trusses, fixed, loads)
    reference = _opensees_case(nodes, frames, trusses, fixed, loads)
    if reference.get("status") == "unavailable":
        return {
            "name": name,
            "status": "unavailable",
            "pitguard": pitguard,
            "reference": reference,
            "referenceSoftware": "OpenSeesPy",
        }
    if pitguard.get("status") != "pass" or reference.get("status") != "pass":
        return {"name": name, "status": "fail", "pitguard": pitguard, "reference": reference}
    errors: list[float] = []
    component_rows: list[dict[str, Any]] = []
    for node_id in nodes:
        for component, label in enumerate(("ux", "uy", "rz")):
            pg = float(pitguard["displacements"][str(node_id)][component])
            ref = float(reference["displacements"][str(node_id)][component])
            error = _relative_error(pg, ref) if abs(ref) > 1.0e-11 or abs(pg) > 1.0e-11 else 0.0
            errors.append(error)
            component_rows.append({"nodeId": node_id, "component": label, "pitguard": pg, "reference": ref, "relativeError": error})
    maximum = max(errors, default=0.0)
    return {
        "name": name,
        "status": "pass" if maximum <= 1.0e-6 else "fail",
        "maximumRelativeDisplacementError": maximum,
        "tolerance": 1.0e-6,
        "components": component_rows,
        "pitguardNumericalGate": pitguard.get("numericalGate"),
        "referenceSoftware": "OpenSeesPy",
        "referenceVersion": reference.get("openseespyVersion"),
    }


def run_opensees_planar_benchmark_suite() -> dict[str, Any]:
    portal_nodes = {
        1: Point2D(x=0.0, y=0.0), 2: Point2D(x=0.0, y=3.0),
        3: Point2D(x=4.0, y=3.0), 4: Point2D(x=4.0, y=0.0),
    }
    portal_frames = [
        {"i": 1, "j": 2, "E": 2.0e8, "A": 0.020, "I": 8.0e-5},
        {"i": 2, "j": 3, "E": 2.0e8, "A": 0.020, "I": 8.0e-5},
        {"i": 3, "j": 4, "E": 2.0e8, "A": 0.020, "I": 8.0e-5},
    ]
    ring_nodes = {
        1: Point2D(x=-2.0, y=-2.0), 2: Point2D(x=2.0, y=-2.0),
        3: Point2D(x=2.0, y=2.0), 4: Point2D(x=-2.0, y=2.0),
        5: Point2D(x=-2.0, y=-5.0), 6: Point2D(x=5.0, y=-2.0),
        7: Point2D(x=2.0, y=5.0), 8: Point2D(x=-5.0, y=2.0),
    }
    ring_frames = [
        {"i": 1, "j": 2, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
        {"i": 2, "j": 3, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
        {"i": 3, "j": 4, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
        {"i": 4, "j": 1, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
    ]
    ring_trusses = [
        {"i": 5, "j": 1, "E": 3.0e7, "A": 0.64},
        {"i": 6, "j": 2, "E": 3.0e7, "A": 0.64},
        {"i": 7, "j": 3, "E": 3.0e7, "A": 0.64},
        {"i": 8, "j": 4, "E": 3.0e7, "A": 0.64},
    ]
    cases = [
        _compare("asymmetric_portal_frame", portal_nodes, portal_frames, [], {1, 4}, {2: (100.0, -20.0, 0.0)}),
        _compare("closed_ring_with_radial_trusses", ring_nodes, ring_frames, ring_trusses, {5, 6, 7, 8}, {1: (200.0, 80.0, 0.0), 3: (-120.0, -60.0, 0.0)}),
        _compare_spatial_rotational_node(),
    ]
    pass_count = sum(item.get("status") == "pass" for item in cases)
    unavailable_count = sum(item.get("status") == "unavailable" for item in cases)
    fail_count = sum(item.get("status") == "fail" for item in cases)
    errors = [
        float(item["maximumRelativeDisplacementError"])
        for item in cases
        if item.get("status") == "pass" and item.get("maximumRelativeDisplacementError") is not None
    ]
    status = "fail" if fail_count else "unavailable" if unavailable_count == len(cases) else "partial" if unavailable_count else "pass"
    return {
        "schema": "pitguard-opensees-benchmark-v3",
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "referenceSoftware": "OpenSeesPy/OpenSees",
        "status": status,
        "caseCount": len(cases),
        "passCount": pass_count,
        "unavailableCount": unavailable_count,
        "failCount": fail_count,
        "maximumRelativeDisplacementError": max(errors, default=None),
        "cases": cases,
        "scope": "linear 2D elasticBeamColumn/truss plus reduced 3D rotational-node benchmark; nonlinear geotechnical constitutive and global 6-DOF spatial verification remain separate",
        "message": (
            "OpenSeesPy benchmark completed." if status == "pass"
            else "OpenSeesPy is unavailable in the current runtime; no external-software equivalence claim is made." if status == "unavailable"
            else "OpenSeesPy benchmark is only partially available in the current runtime." if status == "partial"
            else "At least one OpenSeesPy benchmark case failed."
        ),
    }


def write_benchmark_certificate(path: str | Path) -> dict[str, Any]:
    result = run_opensees_planar_benchmark_suite()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _independent_frame_stiffness(E: float, A: float, I: float, a: Point2D, b: Point2D) -> np.ndarray:
    """Independent 2-D frame stiffness implementation for cross-checking.

    This path intentionally does not call the production element routines or
    the production scaled solver. It uses a separately assembled local matrix,
    an explicit transformation matrix and SciPy's dense symmetric solver.
    """
    dx = float(b.x) - float(a.x)
    dy = float(b.y) - float(a.y)
    length = math.hypot(dx, dy)
    if length <= 1.0e-12:
        raise ValueError("zero-length independent frame element")
    c = dx / length
    s = dy / length
    axial = E * A / length
    bend12 = 12.0 * E * I / length**3
    bend6 = 6.0 * E * I / length**2
    bend4 = 4.0 * E * I / length
    bend2 = 2.0 * E * I / length
    local = np.array([
        [ axial, 0.0, 0.0, -axial, 0.0, 0.0],
        [ 0.0, bend12, bend6, 0.0, -bend12, bend6],
        [ 0.0, bend6, bend4, 0.0, -bend6, bend2],
        [-axial, 0.0, 0.0, axial, 0.0, 0.0],
        [ 0.0, -bend12, -bend6, 0.0, bend12, -bend6],
        [ 0.0, bend6, bend2, 0.0, -bend6, bend4],
    ], dtype=float)
    transform = np.array([
        [c, s, 0.0, 0.0, 0.0, 0.0],
        [-s, c, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, c, s, 0.0],
        [0.0, 0.0, 0.0, -s, c, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ], dtype=float)
    return transform.T @ local @ transform


def _independent_truss_stiffness(E: float, A: float, a: Point2D, b: Point2D) -> np.ndarray:
    dx = float(b.x) - float(a.x)
    dy = float(b.y) - float(a.y)
    length = math.hypot(dx, dy)
    if length <= 1.0e-12:
        raise ValueError("zero-length independent truss element")
    c = dx / length
    s = dy / length
    factor = E * A / length
    matrix4 = factor * np.array([
        [c*c, c*s, -c*c, -c*s],
        [c*s, s*s, -c*s, -s*s],
        [-c*c, -c*s, c*c, c*s],
        [-c*s, -s*s, c*s, s*s],
    ], dtype=float)
    matrix6 = np.zeros((6, 6), dtype=float)
    indices = [0, 1, 3, 4]
    matrix6[np.ix_(indices, indices)] = matrix4
    return matrix6


def _independent_planar_case(
    nodes: dict[int, Point2D],
    frames: list[dict[str, Any]],
    trusses: list[dict[str, Any]],
    fixed: set[int],
    loads: dict[int, tuple[float, float, float]],
) -> dict[str, Any]:
    ndof = 3 * len(nodes)
    matrix = np.zeros((ndof, ndof), dtype=float)
    load_vector = np.zeros(ndof, dtype=float)
    try:
        for frame in frames:
            i, j = int(frame["i"]), int(frame["j"])
            element = _independent_frame_stiffness(
                float(frame["E"]), float(frame["A"]), float(frame["I"]), nodes[i], nodes[j]
            )
            dofs = [3*(i-1), 3*(i-1)+1, 3*(i-1)+2, 3*(j-1), 3*(j-1)+1, 3*(j-1)+2]
            matrix[np.ix_(dofs, dofs)] += element
        for truss in trusses:
            i, j = int(truss["i"]), int(truss["j"])
            element = _independent_truss_stiffness(
                float(truss["E"]), float(truss["A"]), nodes[i], nodes[j]
            )
            dofs = [3*(i-1), 3*(i-1)+1, 3*(i-1)+2, 3*(j-1), 3*(j-1)+1, 3*(j-1)+2]
            matrix[np.ix_(dofs, dofs)] += element
        for node_id, values in loads.items():
            load_vector[3*(node_id-1):3*(node_id-1)+3] += np.asarray(values, dtype=float)
        fixed_dofs = {3*(node_id-1)+offset for node_id in fixed for offset in range(3)}
        free = np.asarray([index for index in range(ndof) if index not in fixed_dofs], dtype=int)
        reduced = matrix[np.ix_(free, free)]
        rhs = load_vector[free]
        rank = int(np.linalg.matrix_rank(reduced))
        if rank < reduced.shape[0]:
            return {"status": "fail", "reason": "rank_deficient", "rank": rank, "size": reduced.shape[0]}
        displacement = scipy_linalg.solve(reduced, rhs, assume_a="sym")
    except Exception as exc:
        return {"status": "fail", "reason": str(exc)}
    full = np.zeros(ndof, dtype=float)
    full[free] = displacement
    reactions = matrix @ full - load_vector
    relative_residual = float(np.linalg.norm(matrix @ full - load_vector - reactions) / max(np.linalg.norm(load_vector), 1.0))
    return {
        "status": "pass",
        "displacements": {str(node_id): full[3*(node_id-1):3*(node_id-1)+3].tolist() for node_id in nodes},
        "reactions": {str(node_id): reactions[3*(node_id-1):3*(node_id-1)+3].tolist() for node_id in fixed},
        "relativeResidual": relative_residual,
        "solver": "scipy.linalg.solve",
    }


def _independent_spatial_rotational_node(
    members: list[dict[str, Any]],
    moment_vector: tuple[float, float, float],
) -> dict[str, Any]:
    matrix = np.zeros((3, 3), dtype=float)
    for member in members:
        bx, by = (float(value) for value in member["axis"])
        norm = max(math.hypot(bx, by), 1.0e-12)
        axis = np.array([bx / norm, by / norm, 0.0], dtype=float)
        normal = np.array([-axis[1], axis[0], 0.0], dtype=float)
        vertical = np.array([0.0, 0.0, 1.0], dtype=float)
        length = max(float(member["effectiveLengthM"]), 1.0e-12)
        torsion = float(member["G"]) * float(member["J"]) / length
        bend_out = 4.0 * float(member["E"]) * float(member["Iy"]) / length
        bend_in = 4.0 * float(member["E"]) * float(member["Iz"]) / length
        matrix += torsion * np.outer(axis, axis) + bend_out * np.outer(normal, normal) + bend_in * np.outer(vertical, vertical)
    try:
        rotations = scipy_linalg.solve(matrix, np.asarray(moment_vector, dtype=float), assume_a="sym")
    except Exception as exc:
        return {"status": "fail", "reason": str(exc)}
    return {"status": "pass", "rotationsRad": rotations.tolist(), "solver": "scipy.linalg.solve"}


def _compare_independent_planar(
    name: str,
    nodes: dict[int, Point2D],
    frames: list[dict[str, Any]],
    trusses: list[dict[str, Any]],
    fixed: set[int],
    loads: dict[int, tuple[float, float, float]],
) -> dict[str, Any]:
    pitguard = _assemble_case(nodes, frames, trusses, fixed, loads)
    reference = _independent_planar_case(nodes, frames, trusses, fixed, loads)
    if pitguard.get("status") != "pass" or reference.get("status") != "pass":
        return {"name": name, "status": "fail", "pitguard": pitguard, "reference": reference}
    rows: list[dict[str, Any]] = []
    errors: list[float] = []
    for node_id in nodes:
        for component, label in enumerate(("ux", "uy", "rz")):
            value = float(pitguard["displacements"][str(node_id)][component])
            expected = float(reference["displacements"][str(node_id)][component])
            error = _relative_error(value, expected) if abs(value) > 1.0e-11 or abs(expected) > 1.0e-11 else 0.0
            rows.append({"nodeId": node_id, "component": label, "pitguard": value, "reference": expected, "relativeError": error})
            errors.append(error)
    maximum = max(errors, default=0.0)
    return {
        "name": name,
        "status": "pass" if maximum <= 1.0e-8 else "fail",
        "maximumRelativeDisplacementError": maximum,
        "tolerance": 1.0e-8,
        "components": rows,
        "referenceSoftware": "independent SciPy dense implementation",
        "referenceSolver": reference.get("solver"),
    }


def _compare_independent_spatial() -> dict[str, Any]:
    raw_members = [
        {"axis": (1.0, 0.0), "effectiveLengthM": 4.0, "E": 3.0e7, "G": 1.25e7, "A": 0.90, "J": 0.050, "Iy": 0.080, "Iz": 0.080},
        {"axis": (0.0, 1.0), "effectiveLengthM": 3.2, "E": 3.0e7, "G": 1.25e7, "A": 0.90, "J": 0.050, "Iy": 0.080, "Iz": 0.080},
        {"axis": (-0.7071067811865476, 0.7071067811865476), "effectiveLengthM": 5.1, "E": 3.0e7, "G": 1.25e7, "A": 0.90, "J": 0.050, "Iy": 0.080, "Iz": 0.080},
    ]
    production_members = []
    for index, member in enumerate(raw_members, start=1):
        length = float(member["effectiveLengthM"])
        production_members.append({
            "beamCode": f"SP-{index}",
            "axis": member["axis"],
            "torsionStiffness": float(member["G"]) * float(member["J"]) / length,
            "bendingStiffness": 4.0 * float(member["E"]) * float(member["Iy"]) / length,
            "outOfPlaneBendingStiffness": 4.0 * float(member["E"]) * float(member["Iy"]) / length,
            "inPlaneBendingStiffness": 4.0 * float(member["E"]) * float(member["Iz"]) / length,
            "effectiveLength": length,
            "rigidZoneLength": 0.0,
        })
    moment = (850.0, -430.0, 260.0)
    pitguard = solve_spatial_node_rotations(moment, production_members)
    reference = _independent_spatial_rotational_node(raw_members, moment)
    if pitguard.get("status") not in {"pass", "warning"} or reference.get("status") != "pass":
        return {"name": "spatial_rotational_node", "status": "fail", "pitguard": pitguard, "reference": reference}
    errors = [
        _relative_error(float(a), float(b))
        for a, b in zip(pitguard["rotationsRad"], reference["rotationsRad"], strict=True)
    ]
    maximum = max(errors, default=0.0)
    return {
        "name": "spatial_rotational_node",
        "status": "pass" if maximum <= 1.0e-8 else "fail",
        "maximumRelativeDisplacementError": maximum,
        "tolerance": 1.0e-8,
        "referenceSoftware": "independent SciPy dense implementation",
        "referenceSolver": reference.get("solver"),
    }


def run_independent_reference_benchmark_suite() -> dict[str, Any]:
    """Run a dependency-light independent numerical cross-check.

    This suite is useful for continuous integration when OpenSeesPy is not
    installed. It is an independent implementation check, not a replacement
    for validation against a mature external structural-analysis package.
    """
    portal_nodes = {
        1: Point2D(x=0.0, y=0.0), 2: Point2D(x=0.0, y=3.0),
        3: Point2D(x=4.0, y=3.0), 4: Point2D(x=4.0, y=0.0),
    }
    portal_frames = [
        {"i": 1, "j": 2, "E": 2.0e8, "A": 0.020, "I": 8.0e-5},
        {"i": 2, "j": 3, "E": 2.0e8, "A": 0.020, "I": 8.0e-5},
        {"i": 3, "j": 4, "E": 2.0e8, "A": 0.020, "I": 8.0e-5},
    ]
    ring_nodes = {
        1: Point2D(x=-2.0, y=-2.0), 2: Point2D(x=2.0, y=-2.0),
        3: Point2D(x=2.0, y=2.0), 4: Point2D(x=-2.0, y=2.0),
        5: Point2D(x=-2.0, y=-5.0), 6: Point2D(x=5.0, y=-2.0),
        7: Point2D(x=2.0, y=5.0), 8: Point2D(x=-5.0, y=2.0),
    }
    ring_frames = [
        {"i": 1, "j": 2, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
        {"i": 2, "j": 3, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
        {"i": 3, "j": 4, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
        {"i": 4, "j": 1, "E": 3.0e7, "A": 1.0, "I": 0.0833333},
    ]
    ring_trusses = [
        {"i": 5, "j": 1, "E": 3.0e7, "A": 0.64},
        {"i": 6, "j": 2, "E": 3.0e7, "A": 0.64},
        {"i": 7, "j": 3, "E": 3.0e7, "A": 0.64},
        {"i": 8, "j": 4, "E": 3.0e7, "A": 0.64},
    ]
    cases = [
        _compare_independent_planar("asymmetric_portal_frame", portal_nodes, portal_frames, [], {1, 4}, {2: (100.0, -20.0, 0.0)}),
        _compare_independent_planar("closed_ring_with_radial_trusses", ring_nodes, ring_frames, ring_trusses, {5, 6, 7, 8}, {1: (200.0, 80.0, 0.0), 3: (-120.0, -60.0, 0.0)}),
        _compare_independent_spatial(),
    ]
    pass_count = sum(item.get("status") == "pass" for item in cases)
    errors = [float(item.get("maximumRelativeDisplacementError") or 0.0) for item in cases]
    return {
        "schema": "pitguard-independent-reference-benchmark-v1",
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "referenceSoftware": "independent SciPy dense implementation",
        "status": "pass" if pass_count == len(cases) else "fail",
        "caseCount": len(cases),
        "passCount": pass_count,
        "maximumRelativeDisplacementError": max(errors, default=0.0),
        "cases": cases,
        "scope": "independent linear algebra and element-assembly cross-check; it does not constitute external commercial-software validation",
    }
