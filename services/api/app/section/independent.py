"""Independent OpenSees verification of a frozen transverse section model.

This module imports neither the primary solver nor its matrices/results.  It
creates elasticBeamColumn and zeroLength elements from physical input only.
OpenSeesPy is an optional benchmark dependency and is imported on demand.

Each stage is one equilibrium step, rebuilt at zero nodal coordinates.  A soil
material represents f=p-lower with ElasticPP limits [0, upper-lower].  Its
native eps0 is x_pl_previous-(p0-lower)/k.  Do NOT replace this with an
InitStrainMaterial wrapper: that wrapper can commit an out-of-bounds initial
strain during construction/copy, creating a fictitious plastic excursion.
Only converged states obtained here supply this adapter's next-stage history.

References:
https://openseespydoc.readthedocs.io/en/latest/src/elasticBeamColumn.html
https://opensees.berkeley.edu/wiki/index.php/Elastic-Perfectly_Plastic_Material
https://github.com/OpenSees/OpenSees/blob/master/SRC/material/uniaxial/ElasticPPMaterial.cpp
https://github.com/OpenSees/OpenSees/blob/master/SRC/element/zeroLength/ZeroLength.cpp
https://opensees.github.io/OpenSeesDocumentation/user/manual/analysis/algorithm/ModifiedNewton.html
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
import math
import threading


_OPENSEES_LOCK = threading.RLock()
_METHOD = "two_sided_elastoplastic_winkler_v1"


def _number(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be an explicit finite number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} cannot be negative")
    return float(value)


def _node(value, count, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < count:
        raise ValueError(f"{name} must identify a model node")


def _identity(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _validate(model):
    """Independently reject histories whose physical interpretation is missing."""
    try:
        nodes = model["nodesM"]
        if not isinstance(nodes, list) or len(nodes) < 2:
            raise ValueError("nodesM must contain at least two nodes")
        for value in nodes:
            _number(value, "node depth")
        if any(right <= left for left, right in zip(nodes, nodes[1:])):
            raise ValueError("nodesM must be strictly increasing")
        _number(model["eiKnM2"], "eiKnM2", positive=True)
        if model["toeBoundary"] not in ("fixed", "pinned", "free"):
            raise ValueError("toeBoundary must be fixed, pinned, or free")
        if not isinstance(model["stages"], list) or not model["stages"]:
            raise ValueError("at least one explicit stage is required")
        stage_ids, soil_seen, previous_soil = set(), {}, set()
        support_seen, active_supports = set(), {}
        for stage in model["stages"]:
            policy = stage.get('soilHistoryAction', 'carry_plastic_history_v1')
            if policy not in ('carry_plastic_history_v1', 'reset_plastic_offset_to_zero_v1',
                              'rebase_soil_at_current_displacement_v1'):
                raise ValueError('unknown soil history action')
            if policy != 'carry_plastic_history_v1' and (not stage_ids or stage['installSupports'] or stage['removeSupports']):
                raise ValueError('soil history reset cannot precede an equilibrium or accompany support operations')
            _identity(stage["id"], "stage id")
            _identity(stage["event"], "stage event")
            if stage["id"] in stage_ids:
                raise ValueError("duplicate stage id")
            stage_ids.add(stage["id"])
            forces = stage["externalForcesKn"]
            if not isinstance(forces, list) or len(forces) != len(nodes):
                raise ValueError("externalForcesKn must have one force per node")
            for force in forces:
                _number(force, "external force")
            if not all(isinstance(stage[key], list) for key in ("soilSprings", "installSupports", "removeSupports")):
                raise ValueError("soil and support events must be explicit lists")
            current_soil = set()
            for soil in stage["soilSprings"]:
                sid = soil["id"]
                _identity(sid, "soil id")
                _node(soil["node"], len(nodes), "soil node")
                if sid in current_soil:
                    raise ValueError("duplicate soil id in stage")
                current_soil.add(sid)
                if isinstance(soil["sign"], bool) or soil["sign"] not in (-1, 1):
                    raise ValueError("soil sign must be -1 or +1")
                if soil["side"] != ("inside" if soil["sign"] == 1 else "outside"):
                    raise ValueError("soil side and sign disagree")
                _number(soil["depthM"], "soil station depth")
                stiffness = _number(soil["kKnPerM"], "soil stiffness", nonnegative=True)
                lower = _number(soil["lowerKn"], "soil lower bound", nonnegative=True)
                upper = _number(soil["upperKn"], "soil upper bound", nonnegative=True)
                p0 = _number(soil["p0Kn"], "soil initial pressure", nonnegative=True)
                if upper < lower or p0 < lower - 1e-9 or p0 > upper + 1e-9:
                    raise ValueError("soil initial pressure and bounds are inconsistent")
                if stiffness == 0 and not lower == p0 == upper:
                    raise ValueError("zero soil stiffness requires one exactly fixed pressure")
                identity = (soil["node"], soil["sign"], soil["side"], soil["depthM"], soil.get("layerId"))
                if sid in soil_seen and sid not in previous_soil:
                    raise ValueError("removed soil cannot reappear under its old id")
                if sid in soil_seen and soil_seen[sid] != identity:
                    raise ValueError("surviving soil id cannot change its material station")
                soil_seen[sid] = identity
            previous_soil = current_soil
            removals = stage["removeSupports"]
            if removals and stage["installSupports"]:
                raise ValueError("support installation and removal require separate events")
            if len(removals) != len(set(removals)):
                raise ValueError("duplicate support removal")
            for sid in removals:
                if sid not in active_supports:
                    raise ValueError("cannot remove an inactive support")
                del active_supports[sid]
            for support in stage["installSupports"]:
                sid = support["id"]
                _identity(sid, "support id")
                _node(support["node"], len(nodes), "support node")
                if sid in support_seen:
                    raise ValueError("support id cannot be installed more than once")
                if _number(support["preloadKn"], "support preload") != 0:
                    raise ValueError("nonzero support preload is not implemented")
                if not isinstance(support["compressionOnly"], bool):
                    raise ValueError("compressionOnly must be explicit boolean")
                if support["kind"] == "elastic":
                    _number(support["stiffnessKnPerM"], "support stiffness", positive=True)
                elif support["kind"] == "rigid":
                    if support["compressionOnly"]:
                        raise ValueError("one-sided rigid contact is not implemented")
                    if support["stiffnessKnPerM"] is not None:
                        raise ValueError("rigid support must not masquerade as finite stiffness")
                    if support["node"] == len(nodes)-1 and model["toeBoundary"] != "free":
                        raise ValueError("rigid support overlaps the prescribed toe translation")
                else:
                    raise ValueError("support kind must be elastic or rigid")
                support_seen.add(sid)
                active_supports[sid] = support
            rigid_nodes = [s["node"] for s in active_supports.values() if s["kind"] == "rigid"]
            if len(rigid_nodes) != len(set(rigid_nodes)):
                raise ValueError("multiple rigid constraints have indeterminate force allocation")
        json.dumps(model, allow_nan=False)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"incomplete independent solver physical model: {exc}") from exc


def _empty_stage(stage, status, reason):
    return {
        "id": stage["id"], "name": stage.get("name", stage["id"]), "event": stage["event"],
        "status": status, "failureReason": reason,
        "nodalDisplacementM": None, "nodalRotation": None, "elementEndForces": None,
        "soilStates": None, "supportStates": None, "toeReaction": None,
        "iterations": None, "residualInfKn": None, "relativeResidual": None,
        "equilibrium": None,
        "newlyInstalledSupportIds": [s["id"] for s in stage["installSupports"]],
        "removedSupportIds": list(stage["removeSupports"]),
    }


def _has_potential_restraint(nodes, toe, soils, supports):
    # A positive-EI connected beam has only transverse translation/rotation as
    # rigid-body modes. This check rejects an obvious mechanism, never adds a
    # regularizing spring. OpenSees still tests the actual nonlinear tangent.
    if toe == "fixed":
        return True
    depths = {nodes[s["node"]] for s in soils if s["upperKn"] > s["lowerKn"]}
    depths.update(nodes[s["node"]] for s in supports.values())
    if toe == "pinned":
        depths.add(nodes[-1])
    return len(depths) >= 2


def _check_final_tangent_restraint(nodes, toe, soils, supports):
    """Independently test the zero-stiffness cone in the two beam rigid modes.

    v=a+b*xi uses depth centered and divided by the total beam length. For
    zero energy, bilateral restraints give equalities; a lower soil corner
    gives sign*v<=0, an upper corner sign*v>=0. A touching compression-only
    support gives v<=0. Exceeded bounds/open gaps provide no local stiffness.
    Two complementary half-planes can therefore restrain a mode even when
    an arbitrarily selected OpenSees corner tangent happens to be zero.

    This implementation uses independent material trial states/tangents and
    pure two-dimensional geometry, without any primary-solver helper/matrix.
    """
    direction_tolerance = 1e-12
    roundoff = 64.0 * math.ulp(1.0)
    length = nodes[-1] - nodes[0]
    middle = nodes[0] + length / 2
    rows = []
    for depth in nodes:
        xi = (depth - middle) / length
        norm = math.hypot(1.0, xi)
        rows.append((1.0 / norm, xi / norm))
    equal, half = set(), set()
    native_yield_band_corners = 0
    if toe != "free":
        equal.add(rows[-1])
    if toe == "fixed":
        equal.add((0.0, 1.0))
    for soil in soils:
        lower, upper, trial = soil["lowerKn"], soil["upperKn"], soil["trialPressureKn"]
        if upper == lower or trial < lower or trial > upper:
            continue
        scale = max(abs(trial), abs(lower), abs(upper), abs(soil["p0Kn"]),
                    abs(soil["kKnPerM"] * soil["localDisplacementM"]),
                    abs(soil["kKnPerM"] * soil["previousPlasticDisplacementM"]), math.ulp(0.0))
        low_distance, high_distance = trial - lower, upper - trial
        row = rows[soil["node"]]
        distance = min(low_distance, high_distance)
        arithmetic_roundoff = roundoff * scale
        nearest_limit = lower if low_distance <= high_distance else upper
        # ElasticPPMaterial::setTrialStrain uses fYieldSurface=-E*DBL_EPSILON.
        # Thus its native tangent is zero in a documented narrow INSIDE band,
        # even when pressure arithmetic alone resolves trial just inside. See
        # https://github.com/OpenSees/OpenSees/blob/master/SRC/material/uniaxial/ElasticPPMaterial.cpp
        # Admit only this native, at-bound state as a ONE-SIDED corner. The
        # strict exterior test above remains first; no plastic branch is made
        # elastic and no artificial bilateral stiffness is introduced.
        native_band_corner = (soil['tangentKnPerM'] == 0.0 and
            distance <= soil['kKnPerM'] * math.ulp(1.0) + arithmetic_roundoff and
            abs(soil.get('pressureKn', math.inf) - nearest_limit) <= arithmetic_roundoff)
        if distance <= arithmetic_roundoff or native_band_corner:
            native_yield_band_corners += int(distance > arithmetic_roundoff and native_band_corner)
            orientation = soil["sign"] if low_distance <= high_distance else -soil["sign"]
            half.add((orientation * row[0], orientation * row[1]))
        else:
            if soil["tangentKnPerM"] <= 0:
                raise RuntimeError("OpenSees final tangent disagrees with an interior elastic soil branch")
            equal.add(row)
    for support in supports:
        row = rows[support["node"]]
        if support["kind"] == "rigid" or not support["compressionOnly"]:
            equal.add(row)
            continue
        current, reference = support["displacementM"], support["referenceDisplacementM"]
        difference = current - reference
        threshold = roundoff * max(abs(current), abs(reference), math.ulp(0.0))
        if difference > threshold:
            if support["tangentKnPerM"] <= 0:
                raise RuntimeError("OpenSees final tangent disagrees with an engaged elastic contact")
            equal.add(row)
        elif difference >= 0:
            half.add(row)

    def product(row, direction):
        return row[0] * direction[0] + row[1] * direction[1]

    if equal:
        first = next(iter(equal))
        perpendicular = (-first[1], first[0])
        rank = 2 if any(abs(product(row, perpendicular)) > direction_tolerance for row in equal) else 1
        candidates = () if rank == 2 else (perpendicular, (-perpendicular[0], -perpendicular[1]))
    else:
        rank = 0
        candidates = [(sign * -row[1], sign * row[0]) for row in half for sign in (-1, 1)] if half else [(1.0, 0.0)]
    for direction in candidates:
        if all(product(row, direction) <= direction_tolerance for row in half):
            raise RuntimeError("Final tangent admits a nonzero rigid-motion direction "
                               f"(a={direction[0]:.8g}, b={direction[1]:.8g}); equilibrium is not unique")
    return {"criterion": "rigid_motion_direction_cone", "bilateralRank": rank,
            "oneSidedConstraintCount": len(half), "nonzeroZeroStiffnessDirection": False,
            "coordinate": "xi=(z-mid_depth)/length", "directionTolerance": direction_tolerance,
            "boundRoundoffMultiplier": 64, "strictlyPlasticBranchesPromoted": False,
            "nativeYieldBandCornerCount": native_yield_band_corners,
            "nativeYieldSurfaceRule": "ElasticPP fYieldSurface=-k*DBL_EPSILON; inside band only, native stress at bound"}


def _one_stage(ops, model, stage, supports, plastic, *, initial_predictor=False):
    nodes, ei, toe = model["nodesM"], float(model["eiKnM2"]), model["toeBoundary"]
    if not _has_potential_restraint(nodes, toe, stage["soilSprings"], supports):
        raise RuntimeError("transverse rigid-body mechanism: insufficient independent restraints")
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    for index, depth in enumerate(nodes):
        tag = index+1
        ops.node(tag, float(depth), 0.0)
        at_toe = index == len(nodes)-1
        ops.fix(tag, 1, int(at_toe and toe != "free"), int(at_toe and toe == "fixed"))
    ops.geomTransf("Linear", 1)
    for index in range(len(nodes)-1):
        # E*I=ei; every axial DOF is restrained, so the artificial A=I=1
        # introduces no axial load or P-delta coupling into this bending model.
        ops.element("elasticBeamColumn", index+1, index+1, index+2, 1.0, ei, 1.0, 1)
    next_node, next_element, next_material = len(nodes)+1, len(nodes), 1
    soil_tags, support_tags = {}, {}
    adjusted_forces = list(map(float, stage["externalForcesKn"]))
    for soil in stage["soilSprings"]:
        sid, sign, node = soil["id"], soil["sign"], soil["node"]
        lower, upper, k = soil["lowerKn"], soil["upperKn"], soil["kKnPerM"]
        adjusted_forces[node] -= sign*lower
        if upper == lower:
            soil_tags[sid] = None
            continue
        eps0 = plastic.get(sid, 0.0) - (soil["p0Kn"]-lower)/k
        ops.uniaxialMaterial("ElasticPP", next_material, k, (upper-lower)/k, 0.0, eps0)
        ops.node(next_node, float(nodes[node]), 0.0)
        ops.fix(next_node, 1, 1, 1)
        # Reversing end nodes makes the material displacement sign*y.  The
        # material's lower pressure is applied once, as an explicit nodal load.
        ends = (next_node, node+1) if sign == 1 else (node+1, next_node)
        ops.element("zeroLength", next_element, *ends, "-mat", next_material, "-dir", 2)
        soil_tags[sid] = next_element
        next_material += 1; next_node += 1; next_element += 1
    for sid, support in supports.items():
        if support["kind"] == "rigid":
            continue
        node, k = support["node"], support["stiffnessKnPerM"]
        ops.uniaxialMaterial("Elastic", next_material, k, 0.0, 0.0 if support["compressionOnly"] else k)
        # The wrapped material here is reversible elastic contact, without
        # plastic history; initialization therefore cannot add a plastic shift.
        ops.uniaxialMaterial("InitStrainMaterial", next_material+1, next_material, -support["referenceDisplacementM"])
        ops.node(next_node, float(nodes[node]), 0.0)
        ops.fix(next_node, 1, 1, 1)
        ops.element("zeroLength", next_element, next_node, node+1, "-mat", next_material+1, "-dir", 2)
        support_tags[sid] = next_element
        next_material += 2; next_node += 1; next_element += 1
    ops.timeSeries("Constant", 1)
    ops.pattern("Plain", 1, 1)
    rigid_nodes = set()
    for support in supports.values():
        if support["kind"] == "rigid":
            rigid_nodes.add(support["node"])
            ops.sp(support["node"]+1, 2, support["referenceDisplacementM"])
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    for index, force in enumerate(adjusted_forces):
        ops.load(index+1, 0.0, force, 0.0)
    force_scale = max(1.0, sum(abs(f) for f in stage["externalForcesKn"]),
                      sum(s["p0Kn"] for s in stage["soilSprings"]))
    absolute_tolerance = 1e-10*force_scale
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormUnbalance", absolute_tolerance, 100, 0)
    # A full Newton step can alternate between the two EPP bounds after a
    # stiffness/bound change. Bisection searches trial iterates, without any
    # intermediate material commit. There is one full stage load step and no
    # artificial restoration/loading substeps from zero displacement.
    if initial_predictor:
        # Same original material force law, one full stage load step. Only
        # the SEARCH matrix uses the materials' native initial tangent.
        # Every residual uses the actual nonlinear restoring forces. No extra
        # spring, load substep, or averaged bilateral force is introduced.
        ops.algorithm("ModifiedNewton", "-initial")
    else:
        ops.algorithm("NewtonLineSearch", "-type", "Bisection")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    code = ops.analyze(1)
    if code != 0:
        if not initial_predictor:
            # Discard the failed attempt entirely. Rebuild from this adapter's
            # previous committed offsets, not from failed native trial state.
            return _one_stage(ops, model, stage, supports, plastic, initial_predictor=True)
        raise RuntimeError(f"OpenSees failed the single stage equilibrium step (code {code}); no state committed to following stages")
    iterations = int(ops.testIter())
    displacement = [float(ops.nodeDisp(i+1, 2)) for i in range(len(nodes))]
    rotation = [float(ops.nodeDisp(i+1, 3)) for i in range(len(nodes))]
    end_forces = []
    for index in range(len(nodes)-1):
        # With z-depth represented by OpenSees global X, global Y and rotation
        # directly follow [y, dy/dz]. No primary-solver matrix is involved.
        raw = ops.eleForce(index+1)
        end_forces.append([float(raw[1]), float(raw[2]), float(raw[4]), float(raw[5])])
    soil_states, next_plastic = [], {}
    wall_forces = list(map(float, stage["externalForcesKn"]))
    for soil in stage["soilSprings"]:
        sid, node, sign = soil["id"], soil["node"], soil["sign"]
        prior = plastic.get(sid, 0.0)
        local = sign*displacement[node]
        trial = soil["p0Kn"]+soil["kKnPerM"]*(local-prior)
        tag = soil_tags[sid]
        if tag is None:
            pressure, tangent = soil["lowerKn"], 0.0
        else:
            pressure = soil["lowerKn"]+float(ops.eleResponse(tag, "material", 1, "stress")[0])
            tangent = float(ops.eleResponse(tag, "material", 1, "tangent")[0])
        expected_pressure = min(soil["upperKn"], max(soil["lowerKn"], trial))
        if abs(pressure-expected_pressure) > 1e-8*max(1.0, abs(pressure)):
            raise RuntimeError("OpenSees soil material state disagrees with its independently carried committed offset")
        yield_state = "upper" if trial > soil["upperKn"]+1e-10 else ("lower" if trial < soil["lowerKn"]-1e-10 else "elastic")
        new_plastic = local-(pressure-soil["p0Kn"])/soil["kKnPerM"] if yield_state != "elastic" else prior
        next_plastic[sid] = new_plastic
        force_on_wall = -sign*pressure
        wall_forces[node] += force_on_wall
        soil_states.append({
            **deepcopy(soil), "pressureKn": pressure, "forceOnWallKn": force_on_wall,
            "plasticDisplacementM": new_plastic, "previousPlasticDisplacementM": prior,
            "localDisplacementM": local, "trialPressureKn": trial,
            "yieldState": yield_state, "tangentKnPerM": tangent,
        })
    ops.reactions()
    support_states = []
    for sid, support in supports.items():
        node = support["node"]
        if support["kind"] == "rigid":
            reaction = -float(ops.nodeReaction(node+1, 2))
            engaged = True
            tangent = None  # Exact constraint, not an invented finite stiffness.
        else:
            reaction = float(ops.eleResponse(support_tags[sid], "material", 1, "stress")[0])
            tangent = float(ops.eleResponse(support_tags[sid], "material", 1, "tangent")[0])
            engaged = not support["compressionOnly"] or displacement[node] >= support["referenceDisplacementM"]-1e-12
        wall_forces[node] -= reaction
        support_states.append({**deepcopy(support), "reactionKn": reaction,
                               "forceOnWallKn": -reaction,
                               "displacementM": displacement[node], "engaged": engaged,
                               "tangentKnPerM": tangent})
    stability = _check_final_tangent_restraint(nodes, toe, soil_states, support_states)
    toe_force = float(ops.nodeReaction(len(nodes), 2)) if toe != "free" else 0.0
    toe_moment = float(ops.nodeReaction(len(nodes), 3)) if toe == "fixed" else 0.0
    force_error = sum(wall_forces)+toe_force
    moment_error = sum(z*f for z, f in zip(nodes, wall_forces))+nodes[-1]*toe_force+toe_moment
    force_norm = max(1.0, sum(abs(f) for f in stage["externalForcesKn"])
                     +sum(abs(s["forceOnWallKn"]) for s in soil_states)
                     +sum(abs(s["reactionKn"]) for s in support_states)+abs(toe_force))
    moment_norm = max(1.0, force_norm*(nodes[-1]-nodes[0]), abs(toe_moment))
    free_force_residual, free_moment_residual = [], []
    for index in range(len(nodes)):
        if index not in rigid_nodes and not (index == len(nodes)-1 and toe != "free"):
            free_force_residual.append(abs(float(ops.nodeReaction(index+1, 2))))
        if not (index == len(nodes)-1 and toe == "fixed"):
            free_moment_residual.append(abs(float(ops.nodeReaction(index+1, 3))))
    force_inf, moment_inf = max(free_force_residual, default=0.0), max(free_moment_residual, default=0.0)
    relative_residual = max(force_inf/force_norm, moment_inf/moment_norm)
    if not all(math.isfinite(x) for x in displacement+rotation+[x for row in end_forces for x in row]):
        raise RuntimeError("OpenSees returned a non-finite response")
    if max(relative_residual, abs(force_error)/force_norm, abs(moment_error)/moment_norm) > 1e-7:
        raise RuntimeError("OpenSees response did not satisfy independent force/moment equilibrium")
    result = {
        "id": stage["id"], "name": stage.get("name", stage["id"]), "event": stage["event"],
        "status": "success", "failureReason": None,
        "nodalDisplacementM": displacement, "nodalRotation": rotation,
        "elementEndForces": end_forces, "soilStates": soil_states, "supportStates": support_states,
        "toeReaction": {"forceKn": toe_force, "momentKnM": toe_moment},
        "iterations": iterations, "residualInfKn": force_inf,
        "rotationalResidualInfKnM": moment_inf, "relativeResidual": relative_residual,
        "stability": stability,
        "equilibriumAlgorithm": "ModifiedNewton(initial)" if initial_predictor else "NewtonLineSearch(Bisection)",
        "discardedUncommittedAttempts": int(initial_predictor),
        "equilibrium": {"horizontalForceResidualKn": force_error, "momentResidualKnM": moment_error,
                        "relativeForceResidual": abs(force_error)/force_norm,
                        "relativeMomentResidual": abs(moment_error)/moment_norm},
        "newlyInstalledSupportIds": [s["id"] for s in stage["installSupports"]],
        "removedSupportIds": list(stage["removeSupports"]),
    }
    return result, next_plastic


def solve_stages(model: dict) -> dict:
    """Solve using independent OpenSees state, returning explicit failure gaps.

    Missing OpenSees is reported as unavailable; it never falls back to the
    primary solver. Invalid/ambiguous physical input raises ValueError before
    backend import. Numerical failure preserves completed stages and leaves
    the failed and subsequent responses null.
    """
    _validate(model)
    frozen = deepcopy(model)
    digest = hashlib.sha256(json.dumps(frozen, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    result = {"method": _METHOD, "solver": "OpenSees", "solverVersion": None,
              "status": "success", "discreteInputHash": digest,
              "snapshotHash": model.get("snapshotHash"), "analysisHash": model.get("analysisHash"),
              "historyImplementation": "single equilibrium step per stage; native ElasticPP eps0; independently carried plastic/reference offsets",
              "stages": []}
    try:
        ops = importlib.import_module("openseespy.opensees")
    except ImportError as exc:
        result["status"] = "unavailable"
        result["failureReason"] = f"optional OpenSeesPy backend unavailable: {exc}"
        result["stages"] = [_empty_stage(s, "not_calculated", result["failureReason"]) for s in frozen["stages"]]
        return result
    with _OPENSEES_LOCK:
        result["solverVersion"] = str(ops.version())
        supports, plastic = {}, {}
        previous_displacement = [0.0]*len(frozen["nodesM"])
        failed_reason = None
        try:
            for stage in frozen["stages"]:
                if failed_reason is not None:
                    result["stages"].append(_empty_stage(stage, "not_calculated", "a previous stage failed: "+failed_reason))
                    continue
                for sid in stage["removeSupports"]:
                    del supports[sid]
                for support in stage["installSupports"]:
                    supports[support["id"]] = {**deepcopy(support),
                        "referenceDisplacementM": previous_displacement[support["node"]]}
                policy = stage.get('soilHistoryAction', 'carry_plastic_history_v1')
                if policy == 'reset_plastic_offset_to_zero_v1':
                    reference_plastic = {soil['id']: 0.0 for soil in stage['soilSprings']}
                elif policy == 'rebase_soil_at_current_displacement_v1':
                    reference_plastic = {soil['id']: soil['sign'] * previous_displacement[soil['node']]
                                         for soil in stage['soilSprings']}
                else:
                    reference_plastic = dict(plastic)
                try:
                    output, next_plastic = _one_stage(ops, frozen, stage, supports, reference_plastic)
                except Exception as exc:
                    failed_reason = str(exc)
                    result["status"] = "failed"
                    result["failureReason"] = failed_reason
                    result["stages"].append(_empty_stage(stage, "failed", failed_reason))
                    continue
                if 'soilHistoryAction' in stage:
                    output['soilHistoryAction'] = policy
                result["stages"].append(output)
                previous_displacement = output["nodalDisplacementM"]
                plastic = next_plastic
        finally:
            ops.wipe()
    return result


if __name__ == "__main__":
    import sys
    print(json.dumps(solve_stages(json.load(sys.stdin)), ensure_ascii=False, allow_nan=False))
