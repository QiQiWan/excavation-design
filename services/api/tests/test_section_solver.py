"""Mechanics assertions use analytical values, never the solver's own assembly."""
from __future__ import annotations

import copy

import numpy as np
import pytest

try:
    from app.section.solver import solve_stages
except ModuleNotFoundError as exc:
    if exc.name not in {"app.section", "app.section.solver"}:
        raise
    solve_stages = None


def solve(model):
    assert callable(solve_stages), "The staged section solver has not been implemented"
    return solve_stages(model)


def stage(name, force, *, soils=None, install=None, remove=None):
    return {"id": name, "name": name, "event": "test_event",
            "externalForcesKn": list(force), "soilSprings": list(soils or []),
            "installSupports": list(install or []), "removeSupports": list(remove or [])}


def model(stages, *, length=1.0, ei=1000.0 / 3.0, toe="fixed"):
    return {"nodesM": [0.0, length], "eiKnM2": ei, "toeBoundary": toe,
            "stages": stages}


def test_fixed_toe_cantilever_matches_tip_load_solution_and_end_force_signs():
    # Catch EI/length powers, rotational signs, accidental head restraint and
    # double soil/load scaling against the textbook cantilever closed form.
    result = solve(model([stage("tip_load", [10.0, 0.0])], length=2.0, ei=1000.0))
    actual = result["stages"][0]
    assert actual["status"] == "success"
    assert actual["nodalDisplacementM"] == pytest.approx([2.0 / 75.0, 0.0], abs=1e-12)
    assert actual["nodalRotation"] == pytest.approx([-0.02, 0.0], abs=1e-12)
    assert actual["elementEndForces"][0] == pytest.approx([10.0, 0.0, -10.0, 20.0], abs=1e-10)
    assert actual["toeReaction"] == pytest.approx({"forceKn": -10.0, "momentKnM": 20.0})
    assert actual["equilibrium"]["relativeForceResidual"] < 1e-12
    assert actual["equilibrium"]["relativeMomentResidual"] < 1e-12


def soil(name="inside", *, node=0, sign=1, p0=50.0, k=1000.0, lower=20.0, upper=150.0, depth=0.0):
    return {"id": name, "node": node, "sign": sign, "p0Kn": p0,
            "kKnPerM": k, "lowerKn": lower, "upperKn": upper,
            "side": "inside" if sign == 1 else "outside", "depthM": depth}


def support(name="S1", *, kind="elastic", compression=False):
    return {"id": name, "node": 0, "kind": kind, "stiffnessKnPerM": 500.0 if kind == "elastic" else None,
            "compressionOnly": compression, "preloadKn": 0.0}


def test_symmetric_two_sided_initial_stress_balances_without_a_zero_output_override():
    springs = [soil(f"{side}-{node}", node=node, sign=side, depth=float(node))
               for side in (-1, 1) for node in (0, 1)]
    result = solve(model([stage("initial", [0.0, 0.0], soils=springs)], toe="free"))["stages"][0]
    assert result["status"] == "success"
    assert result["nodalDisplacementM"] == pytest.approx([0.0, 0.0], abs=1e-13)
    assert [row["pressureKn"] for row in result["soilStates"]] == pytest.approx([50.0] * 4)
    assert [row["forceOnWallKn"] for row in result["soilStates"]] == pytest.approx([50.0, 50.0, -50.0, -50.0])
    assert result["toeReaction"] == {"forceKn": 0.0, "momentKnM": 0.0}


def test_soil_yield_unload_and_reverse_reload_preserves_committed_plastic_displacement():
    # Beam tip stiffness is exactly 1000. Pressure numbers were hand-derived
    # independently and reproduced using an OpenSees ElasticPP material probe.
    forces = [50.0, 150.0, 300.0, 200.0, -80.0, 20.0]
    result = solve(model([stage(f"step-{i}", [force, 0.0], soils=[soil()])
                          for i, force in enumerate(forces)]))
    assert result["status"] == "success"
    assert [x["nodalDisplacementM"][0] for x in result["stages"]] == pytest.approx(
        [0.0, 0.05, 0.15, 0.10, -0.10, -0.05], abs=1e-10)
    assert [x["soilStates"][0]["pressureKn"] for x in result["stages"]] == pytest.approx(
        [50.0, 100.0, 150.0, 100.0, 20.0, 70.0], abs=1e-8)
    assert [x["soilStates"][0]["plasticDisplacementM"] for x in result["stages"]] == pytest.approx(
        [0.0, 0.0, 0.05, 0.05, -0.07, -0.07], abs=1e-10)


def test_excavation_parameter_update_keeps_surviving_spring_plastic_history():
    actual = solve(model([
        stage("yield", [300.0, 0.0], soils=[soil()]),
        stage("unload", [180.0, 0.0], soils=[soil(p0=40.0, k=800.0, lower=10.0, upper=120.0)]),
    ]))["stages"][1]
    assert actual["nodalDisplacementM"][0] == pytest.approx(0.1, abs=1e-10)
    assert actual["soilStates"][0]["pressureKn"] == pytest.approx(80.0, abs=1e-8)
    assert actual["soilStates"][0]["plasticDisplacementM"] == pytest.approx(0.05, abs=1e-10)


def test_zero_force_installation_and_removal_use_the_installed_configuration():
    actual = solve(model([
        stage("before", [10.0, 0.0]),
        stage("install", [10.0, 0.0], install=[support()]),
        stage("load", [20.0, 0.0]),
        stage("remove", [20.0, 0.0], remove=["S1"]),
    ]))["stages"]
    assert [s["nodalDisplacementM"][0] for s in actual] == pytest.approx([.01, .01, 1.0 / 60.0, .02], abs=1e-12)
    assert actual[1]["supportStates"][0]["reactionKn"] == pytest.approx(0.0, abs=1e-10)
    assert actual[2]["supportStates"][0]["reactionKn"] == pytest.approx(10.0 / 3.0, abs=1e-10)
    assert actual[2]["supportStates"][0]["referenceDisplacementM"] == pytest.approx(.01)
    assert actual[3]["supportStates"] == []


def test_compression_only_support_can_open_and_reengage_without_plastic_reference_drift():
    actual = solve(model([
        stage("before", [10.0, 0.0]),
        stage("install", [10.0, 0.0], install=[support(compression=True)]),
        stage("open", [0.0, 0.0]),
        stage("close", [20.0, 0.0]),
    ]))["stages"]
    assert actual[2]["nodalDisplacementM"][0] == pytest.approx(0.0, abs=1e-12)
    assert actual[2]["supportStates"][0]["reactionKn"] == pytest.approx(0.0)
    assert actual[3]["supportStates"][0]["reactionKn"] == pytest.approx(10.0 / 3.0)
    assert actual[3]["supportStates"][0]["referenceDisplacementM"] == pytest.approx(.01)


def test_rigid_replacement_locks_the_true_install_displacement_and_receives_released_force():
    actual = solve(model([
        stage("before", [10.0, 0.0]),
        stage("install", [10.0, 0.0], install=[support()]),
        stage("load", [20.0, 0.0]),
        stage("rigid", [20.0, 0.0], install=[support("slab", kind="rigid")]),
        stage("remove", [20.0, 0.0], remove=["S1"]),
    ]))["stages"]
    assert actual[3]["nodalDisplacementM"][0] == actual[2]["nodalDisplacementM"][0]
    assert actual[4]["nodalDisplacementM"][0] == actual[2]["nodalDisplacementM"][0]
    assert actual[3]["supportStates"][1]["reactionKn"] == pytest.approx(0.0, abs=1e-10)
    assert actual[4]["supportStates"][0]["reactionKn"] == pytest.approx(10.0 / 3.0, abs=1e-10)
    assert actual[4]["equilibrium"]["relativeForceResidual"] < 1e-10


def test_free_unrestrained_mechanism_is_failed_and_later_stages_are_not_zero_filled():
    actual = solve(model([stage("mechanism", [10.0, 0.0]), stage("later", [0.0, 0.0])], toe="free"))
    assert actual["status"] == "failed"
    assert [s["status"] for s in actual["stages"]] == ["failed", "not_calculated"]
    assert all(s["nodalDisplacementM"] is None for s in actual["stages"])
    assert all(s["elementEndForces"] is None for s in actual["stages"])
    assert actual["stages"][0]["failureReason"]


def test_capacity_exhaustion_cannot_be_hidden_by_a_fake_soil_stiffness_or_fixed_toe():
    springs = [soil(f"{side}-{node}", node=node, sign=side, p0=50, lower=20, upper=100, depth=node)
               for side in (-1, 1) for node in (0, 1)]
    actual = solve(model([stage("overload", [500.0, 500.0], soils=springs)], toe="free"))
    assert actual["status"] == "failed"
    assert actual["stages"][0]["nodalDisplacementM"] is None


@pytest.mark.parametrize("change", ["nan_force", "preload", "soil_reactivation", "identity_change", "unsupported_rigid_contact"])
def test_invalid_or_unmodelled_physics_is_rejected(change):
    data = model([stage("base", [50.0, 0.0], soils=[soil()])])
    assert solve(data)["status"] == "success"
    if change == "nan_force":
        data["stages"][0]["externalForcesKn"][0] = float("nan")
    elif change == "preload":
        value = support(); value["preloadKn"] = 1.0
        data["stages"][0]["installSupports"] = [value]
    elif change == "soil_reactivation":
        data["stages"] += [stage("remove_soil", [0.0, 0.0]), stage("reappear", [50.0, 0.0], soils=[soil()])]
    elif change == "identity_change":
        data["stages"] += [stage("moved", [50.0, 0.0], soils=[soil(node=1, depth=1.0)])]
    else:
        data["stages"][0]["installSupports"] = [support(kind="rigid", compression=True)]
    with pytest.raises(ValueError):
        solve(data)


def test_solver_never_changes_the_input_snapshot_or_previous_outputs():
    data = model([stage("one", [300.0, 0.0], soils=[soil()]), stage("two", [200.0, 0.0], soils=[soil()])])
    before = copy.deepcopy(data)
    result = solve(data)
    assert data == before
    result["stages"][1]["soilStates"][0]["plasticDisplacementM"] = 123.0
    assert result["stages"][0]["soilStates"][0]["plasticDisplacementM"] == pytest.approx(.05)


def test_refined_full_section_can_remove_strut_without_roundoff_stalling():
    # At h=.0625 m, multiplying the uncentred global stiffness by the total
    # displacement loses the force residual to cancellation. This fixture
    # must still meet the unchanged physical force/moment acceptance limit.
    from app.section.models import freeze_section, verification_example
    from app.section.preprocess import build_solver_model

    data = verification_example()
    data["analysis"]["max_element_size_m"] = .0625
    actual = solve(build_solver_model(freeze_section(data)))
    assert actual["status"] == "success"
    assert all(s["status"] == "success" for s in actual["stages"])
    last = actual["stages"][-1]
    assert last["equilibrium"]["relativeForceResidual"] <= 1e-6
    assert last["equilibrium"]["relativeMomentResidual"] <= 1e-6
    assert last["residualInfKn"] < 2e-7
    assert all(s["id"] != "STRUT-1" for s in last["supportStates"])


@pytest.mark.parametrize("bound", ["lower", "upper"])
@pytest.mark.parametrize("force", [-1.0, 0.0, 1.0])
def test_complementary_soil_limits_restrain_both_directions(bound, force):
    # At the common lower limit, positive motion engages inside soil and
    # negative motion engages outside soil. At the upper limit the roles
    # reverse. The combined restoring force is 1000*y, not zero or 2000*y.
    p0 = 10.0 if bound == "lower" else 100.0
    springs = [soil(f"{side}-{node}", node=node, sign=side, depth=float(node),
                    p0=p0, lower=10.0, upper=100.0)
               for node in (0, 1) for side in (-1, 1)]
    actual = solve(model([stage("complementary", [force, force], soils=springs)],
                         ei=10000.0, toe="free"))["stages"][0]
    assert actual["status"] == "success", actual["failureReason"]
    assert actual["nodalDisplacementM"] == pytest.approx([force / 1000.0] * 2, abs=1e-11)
    assert actual["nodalRotation"] == pytest.approx([0.0, 0.0], abs=1e-11)
    assert np.asarray(actual["elementEndForces"]) == pytest.approx(np.zeros((1, 4)), abs=1e-8)


def test_three_one_sided_soils_can_close_the_rigid_motion_cone_without_local_pairs():
    # For affine rigid motion, v(0)<=0, v(.5)>=0, v(1)<=0 together force v=0.
    springs = [soil(str(node), node=node, sign=sign, depth=depth,
                    p0=10.0, lower=10.0, upper=100.0)
               for node, sign, depth in ((0, 1, 0.0), (1, -1, .5), (2, 1, 1.0))]
    data = model([stage("closed_cone", [10.0, -10.0, 10.0], soils=springs)], toe="free")
    data["nodesM"] = [0.0, .5, 1.0]
    actual = solve(data)["stages"][0]
    assert actual["status"] == "success", actual["failureReason"]
    assert actual["nodalDisplacementM"] == pytest.approx([0.0] * 3, abs=1e-12)


@pytest.mark.parametrize("toe", ["free", "pinned"])
def test_exact_capacity_plateau_still_has_a_free_rigid_motion_direction(toe):
    springs = [soil(f"{side}-{node}", node=node, sign=side, depth=float(node), upper=100.0)
               for node in (0, 1) for side in (-1, 1)]
    data = model([stage("plateau", [80.0, 80.0 if toe == "free" else 0.0], soils=springs)], toe=toe)
    actual = solve(data)["stages"][0]
    assert actual["status"] == "failed"
    assert actual["nodalDisplacementM"] is None


@pytest.mark.parametrize("maximum_size", [.125, .0625])
def test_public_input_with_k0_equal_to_active_limit_has_a_stable_complete_history(maximum_size):
    from math import pi, radians, tan
    from app.section.models import freeze_section, verification_example
    from app.section.preprocess import build_solver_model

    data = verification_example()
    data["analysis"]["max_element_size_m"] = maximum_size
    for layer in data["soil_layers"]:
        layer["cohesion_kpa"] = 0.0
        layer["k0"] = tan(pi / 4 - radians(layer["phi_deg"]) / 2) ** 2
    physical = build_solver_model(freeze_section(data))
    actual = solve(physical)
    assert actual["status"] == "success", [(s["id"], s["failureReason"]) for s in actual["stages"]]
    assert actual["stages"][0]["nodalDisplacementM"] == pytest.approx([0.0] * len(physical["nodesM"]), abs=1e-12)
