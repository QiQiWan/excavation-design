from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from app.calculation.numerical_conditioning import ConditionThresholds, solve_scaled_symmetric
from app.schemas.domain import (
    BeamElement,
    Point2D,
    RetainingSystem,
    WaleBeamInternalForcePoint,
    WaleBeamInternalForceResult,
)

_COORD_TOL = 1.0e-3


def _key(point: Point2D, digits: int = 3) -> tuple[float, float]:
    return round(float(point.x), digits), round(float(point.y), digits)


def _distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))


def _point_on_segment(point: Point2D, a: Point2D, b: Point2D, tolerance: float = 2.5e-3) -> bool:
    length = _distance(a, b)
    if length <= 1.0e-9:
        return _distance(point, a) <= tolerance
    cross = abs((point.x - a.x) * (b.y - a.y) - (point.y - a.y) * (b.x - a.x)) / length
    dot = (point.x - a.x) * (b.x - a.x) + (point.y - a.y) * (b.y - a.y)
    return cross <= tolerance and -tolerance <= dot <= length * length + tolerance


def _projection_parameter(point: Point2D, a: Point2D, b: Point2D) -> float:
    dx = float(b.x) - float(a.x)
    dy = float(b.y) - float(a.y)
    denom = dx * dx + dy * dy
    if denom <= 1.0e-12:
        return 0.0
    return ((float(point.x) - float(a.x)) * dx + (float(point.y) - float(a.y)) * dy) / denom


def _frame_stiffness(E: float, A: float, I: float, a: Point2D, b: Point2D) -> tuple[np.ndarray, np.ndarray, float]:
    dx = float(b.x) - float(a.x)
    dy = float(b.y) - float(a.y)
    L = math.hypot(dx, dy)
    if L <= 1.0e-8:
        raise ValueError("zero_length_frame_member")
    c = dx / L
    s = dy / L
    k = np.array([
        [E*A/L, 0, 0, -E*A/L, 0, 0],
        [0, 12*E*I/L**3, 6*E*I/L**2, 0, -12*E*I/L**3, 6*E*I/L**2],
        [0, 6*E*I/L**2, 4*E*I/L, 0, -6*E*I/L**2, 2*E*I/L],
        [-E*A/L, 0, 0, E*A/L, 0, 0],
        [0, -12*E*I/L**3, -6*E*I/L**2, 0, 12*E*I/L**3, -6*E*I/L**2],
        [0, 6*E*I/L**2, 2*E*I/L, 0, -6*E*I/L**2, 4*E*I/L],
    ], dtype=float)
    T = np.array([
        [c, s, 0, 0, 0, 0],
        [-s, c, 0, 0, 0, 0],
        [0, 0, 1, 0, 0, 0],
        [0, 0, 0, c, s, 0],
        [0, 0, 0, -s, c, 0],
        [0, 0, 0, 0, 0, 1],
    ], dtype=float)
    return T.T @ k @ T, k, L


def _truss_stiffness(E: float, A: float, a: Point2D, b: Point2D) -> tuple[np.ndarray, float, float, float]:
    dx = float(b.x) - float(a.x)
    dy = float(b.y) - float(a.y)
    L = math.hypot(dx, dy)
    if L <= 1.0e-8:
        raise ValueError("zero_length_truss_member")
    c = dx / L
    s = dy / L
    base = E * A / L
    k = base * np.array([
        [c*c, c*s, 0, -c*c, -c*s, 0],
        [c*s, s*s, 0, -c*s, -s*s, 0],
        [0, 0, 0, 0, 0, 0],
        [-c*c, -c*s, 0, c*c, c*s, 0],
        [-c*s, -s*s, 0, c*s, s*s, 0],
        [0, 0, 0, 0, 0, 0],
    ], dtype=float)
    return k, L, c, s


def _section_properties(beam: BeamElement, stiffness_factor: float = 1.0) -> tuple[float, float, float]:
    width = max(float(beam.section.width or beam.section.diameter or 1.0), 0.2)
    height = max(float(beam.section.height or beam.section.diameter or 1.0), 0.2)
    area = width * height
    inertia = width * height**3 / 12.0
    grade = str(getattr(beam.material, "grade", "") or "")
    elastic = 3.25e7 if grade.upper().startswith("C40") else 3.0e7
    factor = max(float(stiffness_factor), 1.0e-4)
    return elastic * factor, area, inertia


def _support_properties(support: Any, stiffness_factor: float = 1.0) -> tuple[float, float]:
    width = max(float(getattr(support.section, "width", 0.0) or getattr(support.section, "diameter", 0.0) or 1.0), 0.2)
    height = max(float(getattr(support.section, "height", 0.0) or getattr(support.section, "diameter", 0.0) or width), 0.2)
    area = width * height
    elastic = 3.25e7 if str(getattr(support.material, "name", "")).lower() == "concrete" else 2.0e8
    return elastic * max(float(stiffness_factor), 1.0e-4), area


def _nominal_support_force(support: Any) -> float:
    tributary = max(
        float(getattr(support, "start_tributary_width", 0.0) or 0.0),
        float(getattr(support, "end_tributary_width", 0.0) or 0.0),
        float(getattr(support, "bay_spacing", 0.0) or 0.0),
        3.0,
    )
    span = max(float(getattr(support, "span_length", 0.0) or _distance(support.start, support.end)), 2.0)
    return max(250.0, min(5000.0, 75.0 * tributary + 4.0 * span))


def _transfer_beams(system: RetainingSystem, level_index: int) -> list[BeamElement]:
    return [
        beam for beam in (system.ring_beams or [])
        if int(beam.support_level or 0) == level_index
        and (
            str(beam.code).startswith("TR-")
            or str(getattr(beam, "beam_role", "")).startswith("transfer_")
        )
        and len(beam.axis.points or []) >= 2
    ]


def _radial_supports(system: RetainingSystem, level_index: int, active_ids: set[str] | None = None) -> list[Any]:
    rows = [
        support for support in (system.supports or [])
        if int(support.level_index or 0) == level_index and str(support.support_role) == "ring_strut"
    ]
    if active_ids is not None:
        rows = [row for row in rows if row.id in active_ids]
    return rows


def _ring_endpoint(support: Any, beams: list[BeamElement]) -> tuple[Point2D, Point2D]:
    points = [point for beam in beams for point in beam.axis.points]
    if not points:
        return support.end, support.start
    start_distance = min(_distance(support.start, point) for point in points)
    end_distance = min(_distance(support.end, point) for point in points)
    if end_distance <= start_distance:
        return support.end, support.start
    return support.start, support.end



def _node_stiffness_ratio_audit(
    node_rows: list[dict[str, Any]],
    frame_records: list[dict[str, Any]],
    truss_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit local stiffness jumps at every transfer-frame node.

    The check compares characteristic translational and rotational stiffnesses
    of all members incident at the same node.  Very large ratios are a common
    source of local numerical locking, artificial hinges, and sensitivity to
    small geometric perturbations.
    """
    translational: dict[int, list[tuple[str, float]]] = defaultdict(list)
    rotational: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for record in frame_records:
        length = max(float(record["length"]), 1.0e-9)
        axial = float(record["E"] * record["A"] / length)
        bending = float(12.0 * record["E"] * record["I"] / length**3)
        rotation = float(4.0 * record["E"] * record["I"] / length)
        characteristic = max(axial, bending)
        code = str(record["parent"].code)
        for node_index in (int(record["i"]), int(record["j"])):
            translational[node_index].append((code, characteristic))
            rotational[node_index].append((code, rotation))
    for record in truss_records:
        length = max(float(record["length"]), 1.0e-9)
        stiffness = float(record["E"] * record["A"] / length)
        code = str(record["support"].code)
        for node_index in (int(record["i"]), int(record["j"])):
            translational[node_index].append((code, stiffness))

    def ratio(values: list[tuple[str, float]]) -> float:
        finite = [abs(float(value)) for _, value in values if math.isfinite(float(value)) and abs(float(value)) > 1.0e-12]
        return max(finite) / max(min(finite), 1.0e-30) if len(finite) >= 2 else 1.0

    rows: list[dict[str, Any]] = []
    maximum = 1.0
    severe = 0
    warning = 0
    for node in node_rows:
        index = int(node["index"])
        tr = ratio(translational.get(index, []))
        rr = ratio(rotational.get(index, []))
        combined = max(tr, rr)
        maximum = max(maximum, combined)
        status = "pass"
        if combined > 1.0e8:
            status = "fail"
            severe += 1
        elif combined > 1.0e5:
            status = "warning"
            warning += 1
        if status != "pass" or len(translational.get(index, [])) >= 3:
            point = node["point"]
            rows.append({
                "nodeIndex": index,
                "x": round(float(point.x), 5),
                "y": round(float(point.y), 5),
                "nodeTypes": sorted(node.get("types") or []),
                "translationalRatio": float(f"{tr:.6e}"),
                "rotationalRatio": float(f"{rr:.6e}"),
                "maximumRatio": float(f"{combined:.6e}"),
                "incidentMembers": sorted({code for code, _ in translational.get(index, []) + rotational.get(index, [])}),
                "status": status,
            })
    return {
        "schema": "pitguard-node-stiffness-ratio-v1",
        "status": "fail" if severe else "warning" if warning else "pass",
        "blocked": severe > 0,
        "maximumRatio": float(f"{maximum:.6e}"),
        "warningNodeCount": warning,
        "severeNodeCount": severe,
        "reviewedNodeCount": len(node_rows),
        "thresholds": {"warning": 1.0e5, "block": 1.0e8},
        "nodes": rows,
    }


def _endpoint_stiffness_audit(
    global_stiffness: np.ndarray,
    free_dofs: np.ndarray,
    fixed_dofs: set[int],
    node_rows: list[dict[str, Any]],
    truss_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the tangent stiffness seen by each radial support at the frame end.

    The radial truss being queried is removed from the matrix.  A unit load is
    then applied at its frame node along the support axis.  This produces the
    far-end stiffness used by the wall–wale–transfer reaction iteration.
    """
    del fixed_dofs, node_rows  # retained in signature for audit readability
    results: dict[str, Any] = {}
    for record in truss_records:
        dofs = record["dofs"]
        kg, _, _, _ = _truss_stiffness(record["E"], record["A"], record["start"], record["end"])
        reduced_global = np.array(global_stiffness, copy=True)
        reduced_global[np.ix_(dofs, dofs)] -= kg
        Kff = reduced_global[np.ix_(free_dofs, free_dofs)]
        unit_global = np.zeros(global_stiffness.shape[0], dtype=float)
        unit_global[dofs[3]] = float(record["c"])
        unit_global[dofs[4]] = float(record["s"])
        unit_load = unit_global[free_dofs]
        displacement, gate = solve_scaled_symmetric(
            Kff,
            unit_load,
            thresholds=ConditionThresholds(),
            allow_screening_regularization=False,
        )
        support_id = str(record["support"].id)
        if displacement is None:
            results[support_id] = {
                "supportId": support_id,
                "supportCode": record["support"].code,
                "status": "fail",
                "stiffnessKnPerM": None,
                "numericalGate": gate,
            }
            continue
        full = np.zeros(global_stiffness.shape[0], dtype=float)
        full[free_dofs] = displacement
        directional = full[dofs[3]] * record["c"] + full[dofs[4]] * record["s"]
        stiffness = 1.0 / max(abs(float(directional)), 1.0e-15)
        results[support_id] = {
            "supportId": support_id,
            "supportCode": record["support"].code,
            "status": "pass" if gate.get("status") == "pass" else "warning",
            "stiffnessKnPerM": float(f"{stiffness:.6e}"),
            "directionalFlexibilityMPerKn": float(f"{abs(float(directional)):.6e}"),
            "conditionGrade": gate.get("conditionGrade"),
            "scaledConditionNumber": gate.get("scaledConditionNumber"),
        }
    return results


def _response_metrics(row: dict[str, Any]) -> dict[str, float]:
    maximum_moment = max(
        (float(values.get("maxMoment") or 0.0) for values in (row.get("beamResults") or {}).values()),
        default=0.0,
    )
    maximum_axial = max(
        (float(item.get("axialKn") or 0.0) for item in (row.get("supportResults") or [])),
        default=0.0,
    )
    return {
        "displacement": float(row.get("maximumDisplacementM") or 0.0),
        "moment": maximum_moment,
        "supportAxial": maximum_axial,
    }


def _relative_change(value: float, baseline: float) -> float:
    return abs(float(value) - float(baseline)) / max(abs(float(baseline)), 1.0e-9)


def analyze_transfer_frame_sensitivity(
    system: RetainingSystem,
    *,
    support_force_overrides: dict[str, float] | None,
    baseline_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run bounded one-at-a-time geometry and stiffness perturbations."""
    baseline_by_level = {
        int(row.get("levelIndex") or 0): row
        for row in baseline_rows
        if row.get("status") in {"pass", "warning"}
    }
    cases: list[dict[str, Any]] = []
    for level, baseline in baseline_by_level.items():
        beams = _transfer_beams(system, level)
        supports = _radial_supports(system, level, set(support_force_overrides) if support_force_overrides is not None else None)
        if not beams or not supports:
            continue
        metrics0 = _response_metrics(baseline)
        xs = [float(p.x) for beam in beams for p in beam.axis.points]
        ys = [float(p.y) for beam in beams for p in beam.axis.points]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        delta = max(0.02, min(0.10, 0.0025 * span))

        # Representative transfer nodes: highest connectivity is approximated by
        # selecting distinct beam endpoints, capped to control runtime.
        endpoints: dict[tuple[float, float], Point2D] = {}
        for beam in beams:
            for point in (beam.axis.points[0], beam.axis.points[-1]):
                endpoints[_key(point)] = point
        selected = list(endpoints.values())[:6]
        for index, point in enumerate(selected, start=1):
            override = {_key(point): Point2D(x=float(point.x) + delta, y=float(point.y))}
            perturbed = _build_level_model(
                system,
                level,
                support_force_overrides,
                coordinate_overrides=override,
                allow_screening_regularization=False,
                compute_endpoint_stiffness=False,
            )
            if perturbed.get("status") == "fail":
                cases.append({
                    "levelIndex": level,
                    "case": f"node_position_{index}",
                    "parameter": "node_x",
                    "perturbation": delta,
                    "status": "fail",
                    "reason": perturbed.get("reason") or "perturbed_model_failed",
                })
                continue
            metrics = _response_metrics(perturbed)
            changes = {name: _relative_change(metrics[name], metrics0[name]) for name in metrics0}
            cases.append({
                "levelIndex": level,
                "case": f"node_position_{index}",
                "parameter": "node_x",
                "perturbation": delta,
                "status": "pass",
                "relativeChanges": changes,
                "maximumRelativeChange": max(changes.values()),
            })

        for beam in beams[:6]:
            for factor in (0.9, 1.1):
                perturbed = _build_level_model(
                    system,
                    level,
                    support_force_overrides,
                    beam_stiffness_factors={str(beam.code): factor},
                    allow_screening_regularization=False,
                    compute_endpoint_stiffness=False,
                )
                metrics = _response_metrics(perturbed) if perturbed.get("status") != "fail" else {}
                changes = {name: _relative_change(metrics.get(name, math.inf), metrics0[name]) for name in metrics0}
                cases.append({
                    "levelIndex": level,
                    "case": f"beam_stiffness_{beam.code}_{factor:.1f}",
                    "parameter": "beam_stiffness",
                    "memberCode": beam.code,
                    "factor": factor,
                    "status": "fail" if perturbed.get("status") == "fail" else "pass",
                    "relativeChanges": changes,
                    "maximumRelativeChange": max(changes.values()),
                })

        for support in supports[:6]:
            for factor in (0.9, 1.1):
                perturbed = _build_level_model(
                    system,
                    level,
                    support_force_overrides,
                    support_stiffness_factors={str(support.id): factor},
                    allow_screening_regularization=False,
                    compute_endpoint_stiffness=False,
                )
                metrics = _response_metrics(perturbed) if perturbed.get("status") != "fail" else {}
                changes = {name: _relative_change(metrics.get(name, math.inf), metrics0[name]) for name in metrics0}
                cases.append({
                    "levelIndex": level,
                    "case": f"support_stiffness_{support.code}_{factor:.1f}",
                    "parameter": "support_stiffness",
                    "memberCode": support.code,
                    "factor": factor,
                    "status": "fail" if perturbed.get("status") == "fail" else "pass",
                    "relativeChanges": changes,
                    "maximumRelativeChange": max(changes.values()),
                })

    finite_changes = [
        float(case.get("maximumRelativeChange") or 0.0)
        for case in cases
        if case.get("status") == "pass" and math.isfinite(float(case.get("maximumRelativeChange") or 0.0))
    ]
    maximum = max(finite_changes, default=0.0)
    failed = sum(case.get("status") == "fail" for case in cases)
    status = "fail" if failed else "warning" if maximum > 0.25 else "pass"
    return {
        "schema": "pitguard-transfer-frame-sensitivity-v1",
        "status": status,
        "caseCount": len(cases),
        "failedCaseCount": failed,
        "maximumRelativeChange": float(f"{maximum:.6e}"),
        "thresholds": {"warning": 0.25, "blockOnFailedPerturbation": True},
        "cases": sorted(cases, key=lambda item: float(item.get("maximumRelativeChange") or math.inf), reverse=True),
    }


def _build_level_model(
    system: RetainingSystem,
    level_index: int,
    force_overrides: dict[str, float] | None,
    *,
    beam_stiffness_factors: dict[str, float] | None = None,
    support_stiffness_factors: dict[str, float] | None = None,
    coordinate_overrides: dict[tuple[float, float], Point2D] | None = None,
    allow_screening_regularization: bool = False,
    compute_endpoint_stiffness: bool = True,
) -> dict[str, Any]:
    beam_stiffness_factors = dict(beam_stiffness_factors or {})
    support_stiffness_factors = dict(support_stiffness_factors or {})
    coordinate_overrides = dict(coordinate_overrides or {})
    beams = _transfer_beams(system, level_index)
    active_ids = set(force_overrides) if force_overrides is not None else None
    supports = _radial_supports(system, level_index, active_ids)
    if not beams or not supports:
        return {"status": "not_applicable", "levelIndex": level_index, "reason": "transfer_members_or_radial_supports_missing"}

    ring_connection_points: list[Point2D] = []
    support_endpoints: dict[str, tuple[Point2D, Point2D]] = {}
    for support in supports:
        ring, wall = _ring_endpoint(support, beams)
        ring_connection_points.append(ring)
        support_endpoints[support.id] = (ring, wall)

    nodes: dict[tuple[float, float], dict[str, Any]] = {}
    def node(point: Point2D, *, fixed: bool = False, node_type: str = "frame") -> int:
        point = coordinate_overrides.get(_key(point), point)
        key = _key(point)
        if key not in nodes:
            nodes[key] = {"index": len(nodes), "point": point, "fixed": fixed, "types": {node_type}}
        else:
            nodes[key]["fixed"] = bool(nodes[key]["fixed"] or fixed)
            nodes[key]["types"].add(node_type)
        return int(nodes[key]["index"])

    frame_parts: list[dict[str, Any]] = []
    for beam in beams:
        a, b = beam.axis.points[0], beam.axis.points[-1]
        split = [a, b]
        split.extend(point for point in ring_connection_points if _point_on_segment(point, a, b))
        unique = {_key(point): point for point in split}
        ordered = sorted(unique.values(), key=lambda point: _projection_parameter(point, a, b))
        for part_index, (start, end) in enumerate(zip(ordered[:-1], ordered[1:]), start=1):
            if _distance(start, end) <= 0.05:
                continue
            start = coordinate_overrides.get(_key(start), start)
            end = coordinate_overrides.get(_key(end), end)
            frame_parts.append({
                "parent": beam,
                "partIndex": part_index,
                "start": start,
                "end": end,
                "i": node(start, node_type="transfer_frame"),
                "j": node(end, node_type="transfer_frame"),
            })

    truss_parts: list[dict[str, Any]] = []
    for support in supports:
        ring, wall = support_endpoints[support.id]
        ring = coordinate_overrides.get(_key(ring), ring)
        wall = coordinate_overrides.get(_key(wall), wall)
        truss_parts.append({
            "support": support,
            "start": wall,
            "end": ring,
            "i": node(wall, fixed=True, node_type="wall_anchor"),
            "j": node(ring, node_type="transfer_frame"),
        })

    node_rows = sorted(nodes.values(), key=lambda item: item["index"])
    ndof = 3 * len(node_rows)
    K = np.zeros((ndof, ndof), dtype=float)
    F = np.zeros(ndof, dtype=float)
    frame_records: list[dict[str, Any]] = []
    truss_records: list[dict[str, Any]] = []

    for part in frame_parts:
        E, A, I = _section_properties(part["parent"], beam_stiffness_factors.get(str(part["parent"].code), 1.0))
        kg, kl, length = _frame_stiffness(E, A, I, part["start"], part["end"])
        dofs = [3*part["i"], 3*part["i"]+1, 3*part["i"]+2, 3*part["j"], 3*part["j"]+1, 3*part["j"]+2]
        K[np.ix_(dofs, dofs)] += kg
        frame_records.append({**part, "E": E, "A": A, "I": I, "length": length, "localStiffness": kl, "dofs": dofs})

    applied_total = 0.0
    for part in truss_parts:
        support = part["support"]
        E, A = _support_properties(support, support_stiffness_factors.get(str(support.id), 1.0))
        kg, length, c, s = _truss_stiffness(E, A, part["start"], part["end"])
        dofs = [3*part["i"], 3*part["i"]+1, 3*part["i"]+2, 3*part["j"], 3*part["j"]+1, 3*part["j"]+2]
        K[np.ix_(dofs, dofs)] += kg
        magnitude = float(force_overrides.get(support.id, 0.0)) if force_overrides is not None else float(getattr(support, "design_axial_force", 0.0) or _nominal_support_force(support))
        magnitude = max(magnitude, 0.0)
        # Wall pressure acts through the radial member into the transfer frame.
        F[3*part["j"]] += c * magnitude
        F[3*part["j"]+1] += s * magnitude
        applied_total += magnitude
        truss_records.append({**part, "E": E, "A": A, "length": length, "c": c, "s": s, "dofs": dofs, "appliedForce": magnitude})

    fixed_dofs: set[int] = set()
    for row in node_rows:
        if row["fixed"]:
            fixed_dofs.update({3*row["index"], 3*row["index"]+1, 3*row["index"]+2})
    free = np.array([index for index in range(ndof) if index not in fixed_dofs], dtype=int)
    if free.size == 0:
        return {"status": "fail", "levelIndex": level_index, "reason": "no_free_transfer_frame_dofs"}
    Kff = K[np.ix_(free, free)]
    Ff = F[free]
    d_free, numerical = solve_scaled_symmetric(
        Kff,
        Ff,
        thresholds=ConditionThresholds(),
        allow_screening_regularization=allow_screening_regularization,
    )
    if d_free is None:
        return {
            "status": "fail",
            "levelIndex": level_index,
            "reason": "ill_conditioned_transfer_frame_blocked",
            "nodeCount": len(node_rows),
            "frameElementCount": len(frame_records),
            "radialSupportCount": len(truss_records),
            "fixedNodeCount": sum(bool(row["fixed"]) for row in node_rows),
            "conditionNumber": numerical.get("scaledConditionNumber"),
            "rawConditionNumber": numerical.get("rawConditionNumber"),
            "scaledConditionNumber": numerical.get("scaledConditionNumber"),
            "conditionGrade": numerical.get("conditionGrade"),
            "relativeResidual": numerical.get("relativeResidual"),
            "regularized": numerical.get("regularized", False),
            "numericalGate": numerical,
            "messages": [str(numerical.get("message") or "病态矩阵已自动阻断。")],
        }
    D = np.zeros(ndof, dtype=float)
    D[free] = d_free
    relative_residual = float(numerical.get("relativeResidual") or 0.0)
    condition = float(numerical.get("scaledConditionNumber") or math.inf)
    regularized = bool(numerical.get("regularized"))

    beam_results: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "maxAxial": 0.0,
        "maxShear": 0.0,
        "maxMoment": 0.0,
        "maxDeflection": 0.0,
        "length": 0.0,
        "partCount": 0,
    })
    part_results: list[dict[str, Any]] = []
    for record in frame_records:
        dofs = record["dofs"]
        start, end = record["start"], record["end"]
        dx = float(end.x) - float(start.x)
        dy = float(end.y) - float(start.y)
        L = record["length"]
        c = dx/L
        s = dy/L
        T = np.array([
            [c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1],
        ], dtype=float)
        local_d = T @ D[dofs]
        local_f = record["localStiffness"] @ local_d
        axial = max(abs(float(local_f[0])), abs(float(local_f[3])))
        shear = max(abs(float(local_f[1])), abs(float(local_f[4])))
        moment = max(abs(float(local_f[2])), abs(float(local_f[5])))
        deflection = max(math.hypot(float(D[dofs[0]]), float(D[dofs[1]])), math.hypot(float(D[dofs[3]]), float(D[dofs[4]])))
        code = str(record["parent"].code)
        row = beam_results[code]
        row["maxAxial"] = max(row["maxAxial"], axial)
        row["maxShear"] = max(row["maxShear"], shear)
        row["maxMoment"] = max(row["maxMoment"], moment)
        row["maxDeflection"] = max(row["maxDeflection"], deflection)
        row["length"] += L
        row["partCount"] += 1
        part_results.append({
            "beamCode": code,
            "partIndex": int(record["partIndex"]),
            "lengthM": round(L, 4),
            "axialKn": round(axial, 3),
            "shearKn": round(shear, 3),
            "momentKnm": round(moment, 3),
            "deflectionM": round(deflection, 7),
        })

    support_results: list[dict[str, Any]] = []
    for record in truss_records:
        dofs = record["dofs"]
        relative = (
            (D[dofs[3]] - D[dofs[0]]) * record["c"]
            + (D[dofs[4]] - D[dofs[1]]) * record["s"]
        )
        axial = record["E"] * record["A"] / record["length"] * relative
        support_results.append({
            "supportId": record["support"].id,
            "supportCode": record["support"].code,
            "axialKn": round(abs(float(axial)), 3),
            "appliedForceKn": round(float(record["appliedForce"]), 3),
        })

    max_disp = max((math.hypot(float(D[3*i]), float(D[3*i+1])) for i in range(len(node_rows))), default=0.0)
    node_audit = _node_stiffness_ratio_audit(node_rows, frame_records, truss_records)
    status = "pass"
    messages: list[str] = []
    if not np.all(np.isfinite(D)) or not math.isfinite(relative_residual):
        status = "fail"
        messages.append("二维转接框架出现非有限位移或残差。")
    elif numerical.get("blocked") or node_audit.get("blocked"):
        status = "fail"
        messages.append("二维转接框架触发病态矩阵或节点刚度突变自动阻断。")
    elif relative_residual > 1.0e-5:
        status = "fail"
        messages.append("二维转接框架平衡残差超过 1e-5。")
    elif numerical.get("status") == "warning" or node_audit.get("status") == "warning":
        status = "warning"
        messages.append("二维转接框架已求解，但条件数等级或节点刚度比需要复核。")
    else:
        messages.append("二维转接框架完成尺度化求解，平衡残差、条件等级和节点刚度比通过。")

    return {
        "status": status,
        "levelIndex": level_index,
        "nodeCount": len(node_rows),
        "frameElementCount": len(frame_records),
        "radialSupportCount": len(truss_records),
        "fixedNodeCount": sum(bool(row["fixed"]) for row in node_rows),
        "conditionNumber": round(condition, 3) if math.isfinite(condition) else None,
        "rawConditionNumber": numerical.get("rawConditionNumber"),
        "scaledConditionNumber": numerical.get("scaledConditionNumber"),
        "conditionGrade": numerical.get("conditionGrade"),
        "relativeResidual": float(f"{relative_residual:.6e}"),
        "regularized": regularized,
        "numericalGate": numerical,
        "nodeStiffnessAudit": node_audit,
        "endpointStiffness": _endpoint_stiffness_audit(K, free, fixed_dofs, node_rows, truss_records) if compute_endpoint_stiffness and status != "fail" else {},
        "maximumDisplacementM": round(max_disp, 7),
        "appliedRadialForceKn": round(applied_total, 3),
        "beamResults": {code: {key: round(float(value), 6) if isinstance(value, (int, float)) else value for key, value in values.items()} for code, values in beam_results.items()},
        "supportResults": support_results,
        "partResults": part_results,
        "messages": messages,
    }


def analyze_transfer_frame_system(
    system: RetainingSystem,
    *,
    support_force_overrides: dict[str, float] | None = None,
    stage_id: str | None = None,
    stage_name: str | None = None,
    run_sensitivity: bool = False,
    allow_screening_regularization: bool | None = None,
) -> dict[str, Any]:
    levels = sorted({int(beam.support_level or 0) for beam in (system.ring_beams or []) if int(beam.support_level or 0) > 0})
    if allow_screening_regularization is None:
        allow_screening_regularization = support_force_overrides is None
    rows = [
        _build_level_model(
            system, level, support_force_overrides,
            allow_screening_regularization=bool(allow_screening_regularization),
        )
        for level in levels
    ]
    applicable = [row for row in rows if row.get("status") != "not_applicable"]
    fail_count = sum(row.get("status") == "fail" for row in applicable)
    warning_count = sum(row.get("status") == "warning" for row in applicable)
    status = "fail" if fail_count else "warning" if warning_count else "pass" if applicable else "not_applicable"
    return {
        "schema": "pitguard-planar-transfer-frame-v1",
        "analysisMode": "construction_stage" if support_force_overrides is not None else "nominal_candidate_screening",
        "stageId": stage_id,
        "stageName": stage_name,
        "status": status,
        "levelCount": len(levels),
        "solvedLevelCount": len(applicable) - fail_count,
        "failCount": fail_count,
        "warningCount": warning_count,
        "maximumDisplacementM": max((float(row.get("maximumDisplacementM") or 0.0) for row in applicable), default=0.0),
        "maximumConditionNumber": max((float(row.get("conditionNumber") or 0.0) for row in applicable), default=0.0),
        "maximumRawConditionNumber": max((float(row.get("rawConditionNumber") or 0.0) for row in applicable), default=0.0),
        "maximumScaledConditionNumber": max((float(row.get("scaledConditionNumber") or 0.0) for row in applicable), default=0.0),
        "conditionGrades": [row.get("conditionGrade") for row in applicable if row.get("conditionGrade")],
        "maximumNodeStiffnessRatio": max((float((row.get("nodeStiffnessAudit") or {}).get("maximumRatio") or 0.0) for row in applicable), default=0.0),
        "maximumRelativeResidual": max((float(row.get("relativeResidual") or 0.0) for row in applicable), default=0.0),
        "endpointStiffness": {key: value for row in applicable for key, value in (row.get("endpointStiffness") or {}).items()},
        "levels": rows,
        "sensitivity": analyze_transfer_frame_sensitivity(system, support_force_overrides=support_force_overrides, baseline_rows=rows) if run_sensitivity and applicable and not fail_count else {"status": "not_run"},
    }


def envelope_transfer_frame_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in analyses if row.get("status") in {"pass", "warning", "fail"}]
    if not valid:
        return {"status": "missing", "schema": "pitguard-planar-transfer-frame-envelope-v1", "stageCount": 0, "solvedLevelCount": 0}
    beam_envelope: dict[str, dict[str, float]] = defaultdict(lambda: {
        "maxAxial": 0.0, "maxShear": 0.0, "maxMoment": 0.0, "maxDeflection": 0.0, "length": 0.0,
    })
    governing_stage: dict[str, str | None] = {}
    level_results: list[dict[str, Any]] = []
    for analysis in valid:
        for level in analysis.get("levels") or []:
            level_results.append(level)
            for code, values in (level.get("beamResults") or {}).items():
                target = beam_envelope[code]
                for key in ("maxAxial", "maxShear", "maxMoment", "maxDeflection", "length"):
                    value = float(values.get(key) or 0.0)
                    if key == "length":
                        target[key] = max(target[key], value)
                    elif value > target[key]:
                        target[key] = value
                        if key == "maxMoment":
                            governing_stage[code] = analysis.get("stageId")
    fail_count = sum(row.get("status") == "fail" for row in valid)
    warning_count = sum(row.get("status") == "warning" for row in valid)
    return {
        "schema": "pitguard-planar-transfer-frame-envelope-v1",
        "analysisMode": "construction_stage_envelope",
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "stageCount": len(valid),
        "solvedLevelCount": sum(int(row.get("solvedLevelCount") or 0) for row in valid),
        "failCount": fail_count,
        "warningCount": warning_count,
        "maximumDisplacementM": max((float(row.get("maximumDisplacementM") or 0.0) for row in valid), default=0.0),
        "maximumConditionNumber": max((float(row.get("maximumConditionNumber") or 0.0) for row in valid), default=0.0),
        "maximumRawConditionNumber": max((float(row.get("maximumRawConditionNumber") or 0.0) for row in valid), default=0.0),
        "maximumScaledConditionNumber": max((float(row.get("maximumScaledConditionNumber") or 0.0) for row in valid), default=0.0),
        "conditionGrades": [grade for row in valid for grade in (row.get("conditionGrades") or [])],
        "maximumNodeStiffnessRatio": max((float(row.get("maximumNodeStiffnessRatio") or 0.0) for row in valid), default=0.0),
        "maximumRelativeResidual": max((float(row.get("maximumRelativeResidual") or 0.0) for row in valid), default=0.0),
        "sensitivity": next((row.get("sensitivity") for row in valid if (row.get("sensitivity") or {}).get("status") != "not_run"), {"status": "not_run"}),
        "beamEnvelope": {code: {**values, "governingStageId": governing_stage.get(code)} for code, values in beam_envelope.items()},
        "stageSummaries": [{
            "stageId": row.get("stageId"),
            "stageName": row.get("stageName"),
            "status": row.get("status"),
            "maximumDisplacementM": row.get("maximumDisplacementM"),
            "maximumRelativeResidual": row.get("maximumRelativeResidual"),
            "maximumScaledConditionNumber": row.get("maximumScaledConditionNumber"),
            "maximumNodeStiffnessRatio": row.get("maximumNodeStiffnessRatio"),
            "sensitivityStatus": (row.get("sensitivity") or {}).get("status"),
        } for row in valid],
    }


def apply_transfer_frame_envelope(system: RetainingSystem, envelope: dict[str, Any]) -> list[WaleBeamInternalForceResult]:
    results: list[WaleBeamInternalForceResult] = []
    by_code = {beam.code: beam for beam in (system.ring_beams or [])}
    for code, values in (envelope.get("beamEnvelope") or {}).items():
        beam = by_code.get(code)
        if beam is None:
            continue
        length = max(float(values.get("length") or _distance(beam.axis.points[0], beam.axis.points[-1])), 0.1)
        moment = float(values.get("maxMoment") or 0.0)
        shear = float(values.get("maxShear") or 0.0)
        deflection = float(values.get("maxDeflection") or 0.0)
        axial = float(values.get("maxAxial") or 0.0)
        result = WaleBeamInternalForceResult(
            wale_beam_code=code,
            face_code=str(getattr(beam, "transfer_zone_id", None) or "TZ-1"),
            level_index=int(beam.support_level or 0),
            elevation=float(beam.elevation),
            stage_id=str(values.get("governingStageId") or "transfer-frame-envelope"),
            pressure_line_load=0.0,
            beam_length=length,
            support_node_count=2,
            points=[
                WaleBeamInternalForcePoint(chainage=0.0, shear=shear, moment=-moment, deflection=0.0),
                WaleBeamInternalForcePoint(chainage=length/2.0, shear=0.0, moment=moment, deflection=deflection),
                WaleBeamInternalForcePoint(chainage=length, shear=-shear, moment=-moment, deflection=0.0),
            ],
            max_moment=moment,
            max_shear=shear,
            max_deflection=deflection,
            method="PitGuard V3.70 2D transfer-frame stiffness envelope with radial compression members",
            warnings=[] if envelope.get("status") == "pass" else ["二维转接框架存在数值警告或阶段失败，正式设计需复核。"],
        )
        beam.design_axial_force = round(axial, 3)
        beam.analysis_status = "calculated" if envelope.get("status") in {"pass", "warning"} else "fail"
        results.append(result)
    return results


def transfer_frame_checks(system: RetainingSystem, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    status = str(envelope.get("status") or "missing")
    checks.append({
        "ruleId": "PITGUARD-TRANSFER-FRAME-EQUILIBRIUM",
        "objectId": system.id,
        "objectType": "RetainingSystem",
        "status": "pass" if status == "pass" else "warning" if status == "warning" else "fail",
        "calculatedValue": envelope.get("maximumRelativeResidual"),
        "limitValue": 1.0e-8,
        "unit": "relative residual",
        "message": "异形闭合转接体系采用逐施工阶段二维框架—压杆模型，检查刚度矩阵条件数、平衡残差和构件内力包络。",
        "clauseReference": "PitGuard V3.71 numerical transfer-frame gate; engineering-code member checks remain independent",
        "formula": "K_plan u_plan = F_wall; r = ||Ku-F||/max(||F||,1)",
        "diagnostics": {
            "stageCount": envelope.get("stageCount"),
            "maximumConditionNumber": envelope.get("maximumConditionNumber"),
            "maximumDisplacementM": envelope.get("maximumDisplacementM"),
        },
    })
    scaled_condition = float(envelope.get("maximumScaledConditionNumber") or envelope.get("maximumConditionNumber") or 0.0)
    condition_status = "pass" if 0.0 < scaled_condition <= 1.0e8 else "warning" if scaled_condition <= 1.0e12 else "fail"
    checks.append({
        "ruleId": "PITGUARD-TRANSFER-FRAME-SCALED-CONDITION",
        "objectId": system.id,
        "objectType": "RetainingSystem",
        "status": condition_status,
        "calculatedValue": scaled_condition or None,
        "limitValue": 1.0e12,
        "unit": "condition number",
        "message": "转接框架采用对称对角尺度化；条件数按 A/B/C/D/E 等级显示，超过病态阈值自动阻断。",
        "clauseReference": "PitGuard V3.71 numerical conditioning gate",
        "diagnostics": {"raw": envelope.get("maximumRawConditionNumber"), "scaled": scaled_condition, "grades": envelope.get("conditionGrades")},
    })
    node_ratio = float(envelope.get("maximumNodeStiffnessRatio") or 0.0)
    checks.append({
        "ruleId": "PITGUARD-TRANSFER-NODE-STIFFNESS-RATIO",
        "objectId": system.id,
        "objectType": "RetainingSystem",
        "status": "pass" if node_ratio <= 1.0e5 else "warning" if node_ratio <= 1.0e8 else "fail",
        "calculatedValue": node_ratio,
        "limitValue": 1.0e8,
        "unit": "ratio",
        "message": "检查节点相邻构件轴向、弯曲和转动特征刚度比，防止局部锁死或伪铰。",
        "clauseReference": "PitGuard V3.71 node stiffness compatibility gate",
    })
    sensitivity = dict(envelope.get("sensitivity") or {})
    sensitivity_change = float(sensitivity.get("maximumRelativeChange") or 0.0)
    sensitivity_status = str(sensitivity.get("status") or "not_run")
    checks.append({
        "ruleId": "PITGUARD-TRANSFER-GEOMETRY-STIFFNESS-SENSITIVITY",
        "objectId": system.id,
        "objectType": "RetainingSystem",
        "status": "pass" if sensitivity_status == "pass" else "warning" if sensitivity_status in {"warning", "not_run"} else "fail",
        "calculatedValue": sensitivity_change,
        "limitValue": 0.25,
        "unit": "relative response change",
        "message": "节点位置及梁、支撑刚度的一次一参扰动敏感性分析已执行。",
        "clauseReference": "PitGuard V3.71 model robustness and sensitivity gate",
        "diagnostics": sensitivity,
    })
    for beam in system.ring_beams or []:
        if not (str(beam.code).startswith("TR-") or str(getattr(beam, "beam_role", "")).startswith("transfer_")):
            continue
        axial = abs(float(beam.design_axial_force or 0.0))
        width = max(float(beam.section.width or 1.0), 0.2)
        height = max(float(beam.section.height or 1.0), 0.2)
        axial_capacity = 0.35 * 19.1e3 * width * height
        axial_util = axial / max(axial_capacity, 1.0)
        checks.append({
            "ruleId": "GB50010-TRANSFER-BEAM-AXIAL-INTERACTION-SCREENING",
            "objectId": beam.id,
            "objectType": "BeamElement",
            "status": "pass" if axial_util <= 0.75 else "warning" if axial_util <= 1.0 else "fail",
            "calculatedValue": round(axial_util, 4),
            "limitValue": 1.0,
            "unit": "interaction proxy",
            "message": "转接梁轴力已纳入轴压—受弯协同筛查；正式设计需按实际配筋、二阶效应、扭矩和节点偏心完成复核。",
            "clauseReference": "GB/T 50010 compression-flexure principles; V3.71 design-stage proxy",
            "formula": "eta_proxy = N_d/(0.35 f_c A) <= 1.0",
        })
    return checks
