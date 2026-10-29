"""Staged beam with two-sided bounded elastoplastic soil springs.

Depth z increases downwards, y points into the excavation, theta=dy/dz.
Local soil compression x=sign*y, inside sign=+1, outside sign=-1.
Soil pressure p>=0 acts on the wall as -sign*p. External forces MUST NOT
already include soil pressure. End forces are conjugate to [yi,ti,yj,tj];
within an unloaded element section M(x)=-Mi+Vi*x and V(x)=Vi.
This independent-spring approximation is not a continuum soil stress model.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

METHOD = "two_sided_elastoplastic_winkler_v1"
_ABS_FORCE_TOL = 1e-8
_REL_FORCE_TOL = 1e-10
_MAX_ITERATIONS = 100
_DIRECTION_TOL = 1e-12
_BOUND_ROUNDOFF = 64.0 * np.finfo(float).eps


class _SolveFailure(RuntimeError):
    """Physical/numerical failure, distinct from an invalid input contract."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _node(value: Any, count: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or not 0 <= value < count:
        raise ValueError("node must identify a node in the fixed mesh")
    return int(value)


def _validate(model: dict) -> tuple[np.ndarray, float]:
    """Check the whole event path before advancing any material state."""
    nodes = np.asarray([_finite(x, "nodesM") for x in model["nodesM"]], dtype=float)
    if nodes.ndim != 1 or len(nodes) < 2 or np.any(np.diff(nodes) <= 0):
        raise ValueError("nodesM must contain at least two strictly increasing depths")
    ei = _finite(model["eiKnM2"], "eiKnM2")
    if ei <= 0:
        raise ValueError("eiKnM2 must be positive")
    if model["toeBoundary"] not in {"free", "fixed", "pinned"}:
        raise ValueError("toeBoundary must be free, fixed or pinned")
    if not model["stages"]:
        raise ValueError("At least one explicit stage is required")
    stage_ids, previous_soil_ids, support_ids = set(), set(), set()
    soil_identities, active_supports = {}, {}
    for item in model["stages"]:
        history = item.get('soilHistoryAction', 'carry_plastic_history_v1')
        if history not in {'carry_plastic_history_v1', 'reset_plastic_offset_to_zero_v1',
                           'rebase_soil_at_current_displacement_v1'}:
            raise ValueError('Unknown soil history action; no default may replace an explicit rule')
        if history != 'carry_plastic_history_v1' and (not stage_ids or item['installSupports'] or item['removeSupports']):
            raise ValueError('Soil history reset needs a previous stage and a separate support event')
        identity = item["id"]
        if not isinstance(identity, str) or not identity or identity in stage_ids:
            raise ValueError("Stage IDs must be nonempty and unique")
        stage_ids.add(identity)
        if not isinstance(item.get("event"), str) or not item["event"]:
            raise ValueError("Each stage needs an explicit event")
        if len(item["externalForcesKn"]) != len(nodes):
            raise ValueError("externalForcesKn must contain one value per node")
        for value in item["externalForcesKn"]:
            _finite(value, "externalForcesKn")
        current_ids = set()
        for spring in item["soilSprings"]:
            sid = spring["id"]
            if not isinstance(sid, str) or not sid or sid in current_ids:
                raise ValueError("Soil IDs must be nonempty and unique within a stage")
            node = _node(spring["node"], len(nodes))
            sign, side = spring["sign"], spring["side"]
            if isinstance(sign, bool) or sign not in (-1, 1):
                raise ValueError("Soil sign must be +1 inside or -1 outside")
            if side != ("inside" if sign == 1 else "outside"):
                raise ValueError("Soil side and pressure direction disagree")
            definition = (node, sign, side, _finite(spring["depthM"], "depthM"))
            if sid in soil_identities:
                if sid not in previous_soil_ids:
                    raise ValueError(f"Excavated soil spring {sid} cannot be reactivated")
                if soil_identities[sid] != definition:
                    raise ValueError(f"Soil spring {sid} changed its material identity")
            soil_identities[sid] = definition
            current_ids.add(sid)
            p0, k, lower, upper = (_finite(spring[key], key)
                                  for key in ("p0Kn", "kKnPerM", "lowerKn", "upperKn"))
            if k < 0 or not 0 <= lower <= p0 <= upper:
                raise ValueError("Soil requires k>=0 and 0<=lower<=p0<=upper")
            if k == 0 and not lower == p0 == upper:
                raise ValueError("Zero soil stiffness needs a single fixed pressure bound")
        previous_soil_ids = current_ids
        installs, removes = item["installSupports"], item["removeSupports"]
        if installs and removes:
            raise ValueError("Installation and removal must be separate events")
        if len(set(removes)) != len(removes):
            raise ValueError("A support cannot be removed twice in one event")
        for sid in removes:
            if sid not in active_supports:
                raise ValueError(f"Cannot remove inactive support {sid}")
            del active_supports[sid]
        for support in installs:
            sid = support["id"]
            if not isinstance(sid, str) or not sid or sid in support_ids:
                raise ValueError("Support IDs cannot be reused or reactivated")
            node = _node(support["node"], len(nodes))
            if _finite(support["preloadKn"], "preloadKn") != 0:
                raise ValueError("Nonzero preload requires a validated jack/lockoff model")
            if not isinstance(support["compressionOnly"], bool):
                raise ValueError("compressionOnly must be boolean")
            if support["kind"] == "elastic":
                if _finite(support["stiffnessKnPerM"], "support stiffness") <= 0:
                    raise ValueError("Elastic support stiffness must be positive")
            elif support["kind"] == "rigid":
                if support["compressionOnly"] or support["stiffnessKnPerM"] is not None:
                    raise ValueError("Rigid support requires a bilateral exact constraint and no stiffness")
                if node == len(nodes) - 1 and model["toeBoundary"] != "free":
                    raise ValueError("Rigid support and toe reactions would be indeterminate")
                if any(s["kind"] == "rigid" and s["node"] == node for s in active_supports.values()):
                    raise ValueError("Rigid supports at one node have indeterminate reaction sharing")
            else:
                raise ValueError("Support kind must be elastic or rigid")
            active_supports[sid] = support
            support_ids.add(sid)
    return nodes, ei


def _beam(nodes: np.ndarray, ei: float):
    matrix = np.zeros((2 * len(nodes), 2 * len(nodes)))
    elements = []
    for i, length in enumerate(np.diff(nodes)):
        local = ei / length**3 * np.array([
            [12, 6 * length, -12, 6 * length],
            [6 * length, 4 * length**2, -6 * length, 2 * length**2],
            [-12, -6 * length, 12, -6 * length],
            [6 * length, 2 * length**2, -6 * length, 4 * length**2],
        ])
        dofs = np.arange(2 * i, 2 * i + 4)
        matrix[np.ix_(dofs, dofs)] += local
        elements.append((dofs, local))
    return matrix, elements


def _scaled_solve(matrix: np.ndarray, force: np.ndarray) -> tuple[np.ndarray, float]:
    """Scale units only; no additional stiffness and no pseudo-inverse."""
    if not len(force):
        return np.empty(0), 1.0
    diagonal = np.diag(matrix)
    if not np.all(np.isfinite(matrix)) or np.any(diagonal <= 0):
        raise _SolveFailure("Singular tangent: insufficient physical restraint")
    scaling = 1.0 / np.sqrt(diagonal)
    scaled = matrix * scaling[:, None] * scaling[None, :]
    eigenvalues = np.linalg.eigvalsh(scaled)
    if eigenvalues[0] <= max(1.0, eigenvalues[-1]) * 1e-13:
        raise _SolveFailure("Singular or ill-conditioned physical tangent; no artificial restraint added")
    try:
        # The matrix factorization remains float64. The residual and running
        # displacement use extended precision for mixed-precision refinement;
        # casting the tiny correction RHS here does not discard accumulated
        # sub-ULP displacement corrections at a refined-mesh equilibrium.
        answer = scaling * np.linalg.solve(scaled, scaling * np.asarray(force, dtype=float))
    except np.linalg.LinAlgError as exc:
        raise _SolveFailure("Physical tangent could not be solved") from exc
    if not np.all(np.isfinite(answer)):
        raise _SolveFailure("Non-finite displacement increment")
    return answer, float(eigenvalues[-1] / eigenvalues[0])


def _soil_arrays(springs: list[dict], committed: dict[str, float]):
    return {
        "node": np.asarray([s["node"] for s in springs], dtype=int),
        "sign": np.asarray([s["sign"] for s in springs], dtype=float),
        "p0": np.asarray([s["p0Kn"] for s in springs], dtype=float),
        "k": np.asarray([s["kKnPerM"] for s in springs], dtype=float),
        "lower": np.asarray([s["lowerKn"] for s in springs], dtype=float),
        "upper": np.asarray([s["upperKn"] for s in springs], dtype=float),
        "plastic": np.asarray([committed.get(s["id"], 0.0) for s in springs], dtype=float),
    }


def _primitive(value, lower, upper):
    return np.where(value < lower, lower * value - .5 * lower**2,
                    np.where(value > upper, upper * value - .5 * upper**2, .5 * value**2))


def _beam_response(displacement, nodes, ei):
    """Evaluate the same element equations after removing rigid motion.

    A refined mesh makes the individual terms in K*d much larger than the
    residual. Subtracting the chord rotation first avoids cancellation of
    rigid translation/rotation in every stiffness row. These end forces and
    this energy are used consistently by equilibrium and result recovery.
    """
    length = np.diff(np.asarray(nodes, dtype=np.longdouble))
    y, rotation = displacement[::2], displacement[1::2]
    chord_rotation = np.diff(y) / length
    first = rotation[:-1] - chord_rotation
    second = rotation[1:] - chord_rotation
    moment_i = 2.0 * ei / length * (2.0 * first + second)
    moment_j = 2.0 * ei / length * (first + 2.0 * second)
    shear = (moment_i + moment_j) / length
    forces = np.column_stack((shear, moment_i, -shear, moment_j))
    internal = np.zeros_like(displacement)
    internal[:-2:2] += shear
    internal[2::2] -= shear
    internal[1:-2:2] += moment_i
    internal[3::2] += moment_j
    energy = float(np.sum(2.0 * ei / length * (first**2 + first * second + second**2)))
    return internal, energy, forces


def _evaluate(displacement, beam, nodes, ei, external, soil, supports):
    """Each trial uses the previous COMMITTED state, never another iterate."""
    count = len(displacement) // 2
    local = soil["sign"] * displacement[2 * soil["node"]]
    trial = soil["p0"] + soil["k"] * (local - soil["plastic"])
    pressure = np.clip(trial, soil["lower"], soil["upper"])
    soil_k = np.where((trial >= soil["lower"]) & (trial <= soil["upper"]), soil["k"], 0.0)
    internal = np.bincount(soil["node"], weights=np.asarray(soil["sign"] * pressure, dtype=float), minlength=count)
    nodal_k = np.bincount(soil["node"], weights=soil_k, minlength=count)
    tangent = beam.copy()
    indices = np.arange(0, 2 * count, 2)
    tangent[indices, indices] += nodal_k
    beam_internal, beam_energy, end_forces = _beam_response(displacement, nodes, ei)
    residual = beam_internal - external
    residual[::2] += internal
    energy = beam_energy - float(displacement @ external)
    positive = soil["k"] > 0
    if np.any(positive):
        offset = soil["p0"][positive] - soil["k"][positive] * soil["plastic"][positive]
        energy += float(np.sum((
            _primitive(trial[positive], soil["lower"][positive], soil["upper"][positive])
            - _primitive(offset, soil["lower"][positive], soil["upper"][positive])
        ) / soil["k"][positive]))
    if np.any(~positive):
        energy += float(np.sum(pressure[~positive] * local[~positive]))
    reactions = {}
    for sid, support in supports.items():
        if support["kind"] == "rigid":
            continue
        dof = 2 * support["node"]
        relative = displacement[dof] - support["referenceDisplacementM"]
        stiffness = float(support["stiffnessKnPerM"])
        compression = support["compressionOnly"]
        effective = max(0.0, relative) if compression else relative
        reaction = stiffness * effective
        residual[dof] += reaction
        if not compression or relative >= 0:
            tangent[dof, dof] += stiffness
        energy += .5 * stiffness * effective**2
        reactions[sid] = reaction
    return {"residual": residual, "tangent": tangent, "pressure": pressure,
            "trial": trial, "local": local, "soilTangent": soil_k, "soilInternal": internal,
            "supportReactions": reactions, "energy": energy, "elementEndForces": end_forces}


def _force_norm(vector, free, length):
    weighted = vector.copy()
    weighted[1::2] /= length
    return float(np.max(abs(weighted[free]), initial=0.0))


def _check_directional_restraint(displacement, nodes, soil, evaluation, supports, prescribed):
    """Exclude zero-energy directions without deleting complementary corners.

    Positive-EI beam energy vanishes only for v=a+b*xi, with the dimensionless
    coordinate xi=(z-mid_depth)/length. Interior elastic branches and exact
    constraints require row*q=0; a lower soil bound requires sign*row*q<=0
    for ZERO stiffness, and an upper bound requires -sign*row*q<=0. A closed
    compression-only contact requires row*q<=0. Strictly exceeded bounds and
    open contacts add no small-displacement restraint. These inequalities
    describe directions with no restoring stiffness, not averaged tangents.
    """
    length = float(nodes[-1] - nodes[0])
    coordinate = (nodes - (nodes[0] + .5 * length)) / length
    rows = np.column_stack((np.ones(len(nodes)), coordinate))
    rows /= np.linalg.norm(rows, axis=1)[:, None]
    equations, inequalities = [], []
    for dof in prescribed:
        equations.append(rows[dof // 2] if dof % 2 == 0 else np.array([0.0, 1.0]))
    for i, node in enumerate(soil["node"]):
        lower, upper, trial = soil["lower"][i], soil["upper"][i], evaluation["trial"][i]
        if soil["k"][i] <= 0 or upper == lower or trial < lower or trial > upper:
            continue
        # Roundoff tolerance only releases stiffness from the INTERIOR side.
        # A strictly plastic point is never promoted to a yield corner.
        scale = max(abs(trial), abs(lower), abs(upper), abs(soil["p0"][i]),
                    abs(soil["k"][i] * evaluation["local"][i]),
                    abs(soil["k"][i] * soil["plastic"][i]), np.finfo(float).tiny)
        near = _BOUND_ROUNDOFF * scale
        dl, du = trial - lower, upper - trial
        if min(dl, du) <= near:
            sense = soil["sign"][i] if dl <= du else -soil["sign"][i]
            inequalities.append(sense * rows[node])
        else:
            equations.append(rows[node])
    for support in supports.values():
        if support["kind"] == "rigid":
            continue  # Already included in prescribed displacement equations.
        node = support["node"]
        if not support["compressionOnly"]:
            equations.append(rows[node])
            continue
        actual, reference = displacement[2 * node], support["referenceDisplacementM"]
        relative = actual - reference
        near = _BOUND_ROUNDOFF * max(abs(actual), abs(reference), np.finfo(float).tiny)
        if relative > near:
            equations.append(rows[node])
        elif relative >= 0:
            inequalities.append(rows[node])
    equal = np.unique(np.asarray(equations).reshape(-1, 2), axis=0)
    half = np.unique(np.asarray(inequalities).reshape(-1, 2), axis=0)
    if len(equal):
        normal = equal[0]
        candidate = np.array([-normal[1], normal[0]])
        rank = 2 if np.any(abs(equal @ candidate) > _DIRECTION_TOL) else 1
        candidates = np.empty((0, 2)) if rank == 2 else np.array([candidate, -candidate])
    elif len(half):
        rank = 0
        boundary = np.column_stack((-half[:, 1], half[:, 0]))
        candidates = np.vstack((boundary, -boundary))
    else:
        rank = 0
        candidates = np.array([[1.0, 0.0]])
    for candidate in candidates:
        if not len(half) or np.all(half @ candidate <= _DIRECTION_TOL):
            raise _SolveFailure("Final tangent admits a nonzero rigid-motion direction "
                                f"(a={candidate[0]:.8g}, b={candidate[1]:.8g}); equilibrium is not unique")
    return {"criterion": "rigid_motion_direction_cone", "bilateralRank": rank,
            "oneSidedConstraintCount": len(half), "nonzeroZeroStiffnessDirection": False,
            "coordinate": "xi=(z-mid_depth)/length", "directionTolerance": _DIRECTION_TOL,
            "boundRoundoffMultiplier": 64, "strictlyPlasticBranchesPromoted": False}


def _solve_event(beam, nodes, ei, external, soil, supports, toe, previous):
    displacement = previous.copy()
    prescribed = {}
    if toe in {"fixed", "pinned"}:
        prescribed[len(displacement) - 2] = 0.0
    if toe == "fixed":
        prescribed[len(displacement) - 1] = 0.0
    for support in supports.values():
        if support["kind"] == "rigid":
            prescribed[2 * support["node"]] = support["referenceDisplacementM"]
    for dof, value in prescribed.items():
        displacement[dof] = value
    free = np.asarray([d for d in range(len(displacement)) if d not in prescribed], dtype=int)
    length = float(nodes[-1] - nodes[0])
    scale = max(1.0, float(np.sum(abs(external[::2]))), float(np.sum(abs(soil["p0"]))))
    tolerance = _ABS_FORCE_TOL + _REL_FORCE_TOL * scale
    # A full elastic predictor can cross a temporarily singular trial active
    # set. This is only a search-direction metric. The residual and final
    # physical stability check always use the real, bounded force law.
    predictor = beam.copy()
    indices = np.arange(0, len(displacement), 2)
    predictor[indices, indices] += np.bincount(soil["node"], weights=soil["k"], minlength=len(nodes))
    for support in supports.values():
        if support["kind"] == "elastic":
            predictor[2 * support["node"], 2 * support["node"]] += float(support["stiffnessKnPerM"])
    predictor_free = predictor[np.ix_(free, free)]
    _scaled_solve(predictor_free, np.zeros(len(free)))
    for iteration in range(_MAX_ITERATIONS + 1):
        evaluation = _evaluate(displacement, beam, nodes, ei, external, soil, supports)
        error = _force_norm(evaluation["residual"], free, length)
        if error <= tolerance:
            evaluation["stability"] = _check_directional_restraint(
                displacement, nodes, soil, evaluation, supports, prescribed)
            # This condition number describes the selected Newton search
            # matrix. Physical uniqueness is established by the cone above,
            # never by assigning bilateral stiffness to a yield corner.
            _, condition = _scaled_solve(evaluation["tangent"][np.ix_(free, free)], np.zeros(len(free)))
            return displacement, evaluation, iteration, condition, free, prescribed
        if iteration == _MAX_ITERATIONS:
            break
        rhs = -evaluation["residual"][free]
        try:
            step, _ = _scaled_solve(evaluation["tangent"][np.ix_(free, free)], rhs)
        except _SolveFailure:
            step, _ = _scaled_solve(predictor_free, rhs)
        slope = float(evaluation["residual"][free] @ step)
        if not math.isfinite(slope) or slope >= 0:
            raise _SolveFailure("No descent direction for the true force equilibrium")
        alpha = 1.0
        for line_search in range(32):
            candidate = displacement.copy()
            candidate[free] += alpha * step
            trial_evaluation = _evaluate(candidate, beam, nodes, ei, external, soil, supports)
            candidate_error = _force_norm(trial_evaluation["residual"], free, length)
            energy_slack = 1e-12 * max(1.0, abs(evaluation["energy"]))
            if (candidate_error < tolerance or candidate_error < error * (1.0 - 1e-4 * alpha)
                    or trial_evaluation["energy"] <= evaluation["energy"] + 1e-4 * alpha * slope + energy_slack):
                displacement = candidate
                break
            alpha *= .5
        else:
            raise _SolveFailure("Nonlinear line search failed; stage state was not committed")
    raise _SolveFailure(f"No equilibrium within {_MAX_ITERATIONS} iterations; resistance may be exhausted")


def _unavailable(item, status, reason):
    return {"id": item["id"], "name": item.get("name", item["id"]), "event": item["event"],
            "status": status, "failureReason": reason, "nodalDisplacementM": None,
            "nodalRotation": None, "elementEndForces": None, "soilStates": None,
            "supportStates": None, "toeReaction": None, "equilibrium": None,
            "residualInfKn": None, "relativeResidual": None, "iterations": None,
            "newlyInstalledSupportIds": [s["id"] for s in item["installSupports"]],
            "removedSupportIds": list(item["removeSupports"])}


def solve_stages(model: dict) -> dict:
    """Commit each event only after physical equilibrium and stability checks.

    Contract errors raise ValueError. Mechanisms/nonconvergence return failed
    and then not_calculated stages with unavailable curves, never zero curves.
    """
    nodes, ei = _validate(model)
    beam, _ = _beam(nodes, ei)
    # Fine-mesh stiffness can turn one float64 displacement ULP into a force
    # larger than the convergence threshold. Retain small Newton corrections
    # and evaluate beam curvature in extended precision, then publish regular
    # JSON floats. This changes no constitutive law or force tolerance.
    previous = np.zeros(2 * len(nodes), dtype=np.longdouble)
    committed_plastic, supports, outputs = {}, {}, []
    failed_reason = None
    toe = model["toeBoundary"]
    length = float(nodes[-1] - nodes[0])
    for item in model["stages"]:
        if failed_reason:
            outputs.append(_unavailable(item, "not_calculated", "Previous stage failed: " + failed_reason))
            continue
        next_supports = {sid: dict(support) for sid, support in supports.items()}
        for sid in item["removeSupports"]:
            del next_supports[sid]
        for source in item["installSupports"]:
            next_supports[source["id"]] = dict(
                source, referenceDisplacementM=float(previous[2 * source["node"]]))
        history = item.get('soilHistoryAction', 'carry_plastic_history_v1')
        # A reset changes the soil reference only. It never erases beam
        # displacements or the independently stored support install reference.
        if history == 'reset_plastic_offset_to_zero_v1':
            stage_plastic = {}
        elif history == 'rebase_soil_at_current_displacement_v1':
            stage_plastic = {s['id']: float(s['sign'] * previous[2 * s['node']]) for s in item['soilSprings']}
        else:
            stage_plastic = committed_plastic
        soil = _soil_arrays(item["soilSprings"], stage_plastic)
        external = np.zeros(2 * len(nodes))
        external[::2] = item["externalForcesKn"]
        try:
            displacement, evaluation, iterations, condition, free, prescribed = _solve_event(
                beam, nodes, ei, external, soil, next_supports, toe, previous)
        except _SolveFailure as exc:
            failed_reason = str(exc)
            outputs.append(_unavailable(item, "failed", failed_reason))
            continue
        raw_residual = evaluation["residual"]
        toe_force = float(raw_residual[-2]) if toe in {"pinned", "fixed"} else 0.0
        toe_moment = float(raw_residual[-1]) if toe == "fixed" else 0.0
        support_states, support_forces = [], np.zeros(len(nodes))
        for sid, support in next_supports.items():
            reaction = (-float(raw_residual[2 * support["node"]]) if support["kind"] == "rigid"
                        else float(evaluation["supportReactions"][sid]))
            support_forces[support["node"]] -= reaction
            support_states.append(dict(
                support, reactionKn=reaction, forceOnWallKn=-reaction,
                displacementM=float(displacement[2 * support["node"]]),
                engaged=support["kind"] == "rigid" or not support["compressionOnly"] or reaction > 0))
        pressure = evaluation["pressure"]
        plastic = soil["plastic"].copy()
        yielded = ((evaluation["trial"] < soil["lower"]) | (evaluation["trial"] > soil["upper"])) & (soil["k"] > 0)
        plastic[yielded] = (evaluation["local"][yielded]
                            - (pressure[yielded] - soil["p0"][yielded]) / soil["k"][yielded])
        soil_states = []
        for i, source in enumerate(item["soilSprings"]):
            yield_state = ("lower" if evaluation["trial"][i] <= soil["lower"][i] else
                           "upper" if evaluation["trial"][i] >= soil["upper"][i] else "elastic")
            soil_states.append(dict(
                source, pressureKn=float(pressure[i]), forceOnWallKn=float(-soil["sign"][i] * pressure[i]),
                plasticDisplacementM=float(plastic[i]), previousPlasticDisplacementM=float(soil["plastic"][i]),
                localDisplacementM=float(evaluation["local"][i]), trialPressureKn=float(evaluation["trial"][i]),
                yieldState=yield_state, tangentKnPerM=float(soil["k"][i]) if yield_state == "elastic" else 0.0))
        applied = external[::2] - evaluation["soilInternal"] + support_forces
        horizontal_error = float(math.fsum(applied.tolist()) + toe_force)
        moment_error = float(math.fsum((nodes * applied).tolist()) + nodes[-1] * toe_force + toe_moment)
        force_scale = max(1.0, float(np.sum(abs(external[::2]))), float(np.sum(abs(pressure))),
                          float(np.sum(abs(support_forces))), abs(toe_force))
        moment_scale = max(1.0, force_scale * length, abs(toe_moment))
        relative_force, relative_moment = abs(horizontal_error) / force_scale, abs(moment_error) / moment_scale
        if max(relative_force, relative_moment) > 1e-6:
            failed_reason = "Converged nodal equations failed total force/moment equilibrium"
            outputs.append(_unavailable(item, "failed", failed_reason))
            continue
        full_residual = raw_residual.copy()
        for dof in prescribed:
            full_residual[dof] = 0.0  # These physical reactions are reported separately.
        residual_inf = _force_norm(raw_residual, free, length)
        outputs.append({
            "id": item["id"], "name": item.get("name", item["id"]), "event": item["event"],
            "status": "success", "failureReason": None,
            "nodalDisplacementM": displacement[::2].astype(float).tolist(), "nodalRotation": displacement[1::2].astype(float).tolist(),
            "elementEndForces": evaluation["elementEndForces"].astype(float).tolist(),
            "soilStates": soil_states, "supportStates": support_states,
            "toeReaction": {"forceKn": toe_force, "momentKnM": toe_moment},
            "residualInfKn": residual_inf, "relativeResidual": residual_inf / force_scale,
            "equilibriumResidualVector": full_residual.astype(float).tolist(), "scaledTangentCondition": condition,
            "scaledTangentConditionScope": "selected Newton search matrix; physical uniqueness uses directional cone",
            "stability": evaluation["stability"],
            "iterations": iterations,
            "equilibrium": {"horizontalForceResidualKn": horizontal_error, "momentResidualKnM": moment_error,
                            "relativeForceResidual": relative_force, "relativeMomentResidual": relative_moment},
            "newlyInstalledSupportIds": [s["id"] for s in item["installSupports"]],
            "removedSupportIds": list(item["removeSupports"]),
        })
        if 'soilHistoryAction' in item:
            outputs[-1]['soilHistoryAction'] = history
        # No published list is reused as committed state.
        previous, supports = displacement.copy(), next_supports
        committed_plastic = {s["id"]: float(plastic[i]) for i, s in enumerate(item["soilSprings"])}
    return {"method": METHOD, "status": "failed" if failed_reason else "success", "stages": outputs,
            "forceBasis": model.get("forceBasis", "supplied_section"),
            "units": {"length": "m", "force": "kN", "moment": "kN*m", "rotation": "radian"},
            "solver": "Euler-Bernoulli FE; committed return mapping; exact displacement constraints",
            "historyTransfer": "committed_soil_plastic_displacement_and_support_installation_reference",
            "soilModelScope": "Independent bounded springs; not a continuum soil stress-path model"}
