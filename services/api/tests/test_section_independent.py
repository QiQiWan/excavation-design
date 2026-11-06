"""Independent, hand-derived checks for the optional OpenSees section adapter.

The examples use a length-one cantilever with EI=1000/3, whose free-head
lateral stiffness is exactly 1000 kN/m.  Expectations do not use the primary
section solver or its matrix builder.
"""
from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util

import pytest


def _solver(require_backend: bool = True):
    if require_backend and importlib.util.find_spec("openseespy") is None:
        pytest.skip("OpenSeesPy optional benchmark dependency is not installed")
    try:
        module = importlib.import_module("app.section.independent")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Independent section solver is not implemented: {exc}")
    return module.solve_stages


def _stage(identity, force=0.0, *, soil=(), install=(), remove=()):
    return {
        "id": identity,
        "name": identity,
        "event": "verification",
        "externalForcesKn": [force, 0.0],
        "soilSprings": deepcopy(list(soil)),
        "installSupports": deepcopy(list(install)),
        "removeSupports": list(remove),
    }


def _model(stages):
    return {
        "nodesM": [0.0, 1.0],
        "eiKnM2": 1000.0 / 3.0,
        "toeBoundary": "fixed",
        "stages": stages,
    }


def _soil(sign=1, **changes):
    result = {
        "id": "soil",
        "node": 0,
        "sign": sign,
        "side": "inside" if sign == 1 else "outside",
        "depthM": 0.0,
        "p0Kn": 50.0,
        "kKnPerM": 1000.0,
        "lowerKn": 20.0,
        "upperKn": 150.0,
    }
    result.update(changes)
    return result


def _support(identity="S1", *, kind="elastic", compression_only=False):
    return {
        "id": identity,
        "node": 0,
        "kind": kind,
        "stiffnessKnPerM": 500.0 if kind == "elastic" else None,
        "compressionOnly": compression_only,
        "preloadKn": 0.0,
    }


def _successful(result):
    assert result["solver"] == "OpenSees"
    assert result["solverVersion"]
    assert all(s["status"] == "success" for s in result["stages"]), result
    for stage in result["stages"]:
        assert stage["relativeResidual"] < 1e-8
        assert stage["equilibrium"]["relativeForceResidual"] < 1e-8
        assert stage["equilibrium"]["relativeMomentResidual"] < 1e-8
    return result["stages"]


def _by_id(states, identity):
    return next(state for state in states if state["id"] == identity)


def test_independent_cantilever_matches_hand_solution_and_end_force_signs():
    # Catches wrong EI, axes, beam mapping, and transverse-force recovery.
    model = _model([_stage("load", 10.0)])
    before = deepcopy(model)
    stage = _successful(_solver()(model))[0]
    assert model == before
    assert stage["nodalDisplacementM"] == pytest.approx([0.01, 0.0])
    assert stage["nodalRotation"] == pytest.approx([-0.015, 0.0])
    assert stage["elementEndForces"][0] == pytest.approx([10.0, 0.0, -10.0, 10.0], abs=1e-10)
    assert stage["toeReaction"] == pytest.approx({"forceKn": -10.0, "momentKnM": 10.0})


def test_installation_and_rigid_replacement_retain_their_nonzero_reference():
    # Catches fixed-to-zero replacement, elastic reference double subtraction,
    # and combining new-support installation with old-support removal.
    stages = _successful(_solver()(_model([
        _stage("excavate", 10.0),
        _stage("install", 10.0, install=[_support()]),
        _stage("deeper", 20.0),
        _stage("transfer", 20.0, install=[_support("T1", kind="rigid")]),
        _stage("remove_old", 20.0, remove=["S1"]),
        _stage("remove_transfer", 20.0, remove=["T1"]),
    ])))
    assert [s["nodalDisplacementM"][0] for s in stages] == pytest.approx([
        0.01, 0.01, 1.0/60.0, 1.0/60.0, 1.0/60.0, 0.02,
    ], abs=1e-10)
    assert _by_id(stages[1]["supportStates"], "S1")["reactionKn"] == pytest.approx(0.0, abs=1e-10)
    assert _by_id(stages[2]["supportStates"], "S1")["reactionKn"] == pytest.approx(10.0/3.0)
    transfer = _by_id(stages[3]["supportStates"], "T1")
    assert transfer["referenceDisplacementM"] == pytest.approx(1.0/60.0)
    assert transfer["reactionKn"] == pytest.approx(0.0, abs=1e-10)
    assert _by_id(stages[4]["supportStates"], "T1")["reactionKn"] == pytest.approx(10.0/3.0)
    assert stages[4]["removedSupportIds"] == ["S1"]


def test_initial_equal_pressure_on_opposite_sides_balances_naturally():
    # Catches accidental double-counting of p0 or failure to orient outside soil.
    inside = _soil(id="inside")
    outside = _soil(-1, id="outside")
    stage = _successful(_solver()(_model([_stage("initial", soil=[inside, outside])])))[0]
    assert stage["nodalDisplacementM"] == pytest.approx([0.0, 0.0], abs=1e-12)
    assert [s["pressureKn"] for s in stage["soilStates"]] == pytest.approx([50.0, 50.0])
    assert [s["forceOnWallKn"] for s in stage["soilStates"]] == pytest.approx([-50.0, 50.0])


@pytest.mark.parametrize('sign', [-1, 1])
def test_fixed_pressure_zero_stiffness_has_same_physical_contract_and_hand_equilibrium(sign):
    # k=0 is only meaningful when pressure is exactly fixed. It is a load,
    # never a restraint, and both solvers must accept that identical input.
    from app.section.solver import solve_stages as primary
    fixed = _soil(sign, kKnPerM=0.0, p0Kn=50.0, lowerKn=50.0, upperKn=50.0)
    model = _model([_stage('fixed_pressure', 100.0, soil=[fixed])])
    native = _successful(_solver()(model))[0]
    first = primary(model)['stages'][0]
    expected = (100.0-sign*50.0)/1000.0
    for stage in (native, first):
        assert stage['nodalDisplacementM'][0] == pytest.approx(expected, abs=1e-12)
        assert stage['soilStates'][0]['pressureKn'] == 50.0
        assert stage['soilStates'][0]['tangentKnPerM'] == 0.0


def test_zero_stiffness_with_an_unfixed_pressure_range_is_rejected():
    model = _model([_stage('undefined', 10.0, soil=[_soil(kKnPerM=0.0)])])
    with pytest.raises(ValueError, match='fixed pressure'):
        _solver(require_backend=False)(model)


@pytest.mark.parametrize("sign", [1, -1])
def test_yield_unload_parameter_change_and_transfer_keep_plastic_history(sign):
    # Catches InitStrain wrapper initialization committing an artificial yield
    # while rebuilding at y=0, plastic-history reset, and old-K restoration.
    soil = _soil(sign)
    changed = _soil(sign, p0Kn=60.0, kKnPerM=2000.0, lowerKn=10.0, upperKn=200.0)
    stages = _successful(_solver()(_model([
        _stage("initial", sign*50.0, soil=[soil]),
        _stage("yield", sign*300.0, soil=[soil]),
        _stage("unload", sign*200.0, soil=[soil]),
        _stage("change_water_and_soil_parameters", sign*200.0, soil=[changed]),
        _stage("install_transfer", sign*200.0, soil=[changed], install=[_support("T", kind="rigid")]),
        _stage("delete_soil", sign*200.0),
    ])))
    assert [s["nodalDisplacementM"][0] for s in stages] == pytest.approx([
        0.0, sign*0.15, sign*0.10, sign*0.08, sign*0.08, sign*0.08,
    ], abs=1e-10)
    assert [s["soilStates"][0]["pressureKn"] for s in stages[:5]] == pytest.approx([50.0,150.0,100.0,120.0,120.0])
    assert [s["soilStates"][0]["plasticDisplacementM"] for s in stages[:5]] == pytest.approx([0.0,0.05,0.05,0.05,0.05], abs=1e-10)
    assert stages[1]["soilStates"][0]["yieldState"] == "upper"
    assert stages[2]["soilStates"][0]["yieldState"] == "elastic"
    assert _by_id(stages[4]["supportStates"], "T")["reactionKn"] == pytest.approx(0.0, abs=1e-9)
    assert _by_id(stages[5]["supportStates"], "T")["reactionKn"] == pytest.approx(sign*120.0)


def test_active_limit_unloading_then_reload_retains_signed_plastic_offset():
    # Catches clipping without committed hysteresis and wrong lower-yield sign.
    soil = _soil()
    stages = _successful(_solver()(_model([
        _stage("yield_upper", 300.0, soil=[soil]),
        _stage("unload_elastic", 200.0, soil=[soil]),
        _stage("yield_lower", -80.0, soil=[soil]),
        _stage("reload", 20.0, soil=[soil]),
    ])))
    assert [s["nodalDisplacementM"][0] for s in stages] == pytest.approx([0.15,0.10,-0.10,-0.05])
    assert [s["soilStates"][0]["pressureKn"] for s in stages] == pytest.approx([150.0,100.0,20.0,70.0])
    assert [s["soilStates"][0]["plasticDisplacementM"] for s in stages] == pytest.approx([0.05,0.05,-0.07,-0.07])


def test_compression_only_support_opens_and_recloses_without_plastic_shift():
    # Catches incorrectly using a yielding material to model elastic contact.
    stages = _successful(_solver()(_model([
        _stage("before", 10.0),
        _stage("install", 10.0, install=[_support(compression_only=True)]),
        _stage("load", 20.0),
        _stage("open", 5.0),
        _stage("close", 20.0),
    ])))
    assert [s["nodalDisplacementM"][0] for s in stages] == pytest.approx([0.01,0.01,1/60,0.005,1/60])
    assert _by_id(stages[3]["supportStates"], "S1")["reactionKn"] == pytest.approx(0.0, abs=1e-10)
    assert not _by_id(stages[3]["supportStates"], "S1")["engaged"]
    assert _by_id(stages[4]["supportStates"], "S1")["reactionKn"] == pytest.approx(10/3)


@pytest.mark.parametrize("kind", ["preload", "rigid_compression", "reuse_soil", "reuse_support", "move_soil", "duplicate_rigid", "rigid_at_toe", "nan_EI", "mixed_event"])
def test_unsupported_or_ambiguous_history_is_rejected_before_solving(kind):
    # Catches silent substitution of unsupported mechanics and state identities.
    soil = _soil()
    model = _model([_stage("first", soil=[soil])])
    if kind == "preload":
        support = _support(); support["preloadKn"] = -1.0
        model["stages"][0]["installSupports"] = [support]
    elif kind == "rigid_compression":
        model["stages"][0]["installSupports"] = [_support(kind="rigid", compression_only=True)]
    elif kind == "reuse_soil":
        model["stages"] += [_stage("delete"), _stage("restore", soil=[soil])]
    elif kind == "reuse_support":
        model["stages"][0]["installSupports"] = [_support()]
        model["stages"] += [_stage("delete", remove=["S1"]), _stage("restore", install=[_support()])]
    elif kind == "move_soil":
        moved = _soil(node=1, depthM=1.0)
        model["stages"] += [_stage("move", soil=[moved])]
    elif kind == "duplicate_rigid":
        model["stages"][0]["installSupports"] = [_support("T1",kind="rigid"),_support("T2",kind="rigid")]
    elif kind == "rigid_at_toe":
        support = _support(kind="rigid"); support["node"] = 1
        model["stages"][0]["installSupports"] = [support]
    elif kind == "mixed_event":
        model["stages"][0]["installSupports"] = [_support()]
        model["stages"] += [_stage("install_and_remove", install=[_support("T", kind="rigid")], remove=["S1"])]
    else:
        model["eiKnM2"] = float("nan")
    with pytest.raises(ValueError):
        _solver(require_backend=False)(model)


def test_unsupported_mechanism_cannot_return_zero_curves_as_success():
    # Catches artificial weak springs or fallback zeros after solver failure.
    model = _model([_stage("unstable", 10.0),_stage("later", 20.0)])
    model["toeBoundary"] = "free"
    result = _solver()(model)
    assert result["stages"][0]["status"] == "failed"
    assert result["stages"][0]["nodalDisplacementM"] is None
    assert result["stages"][0]["elementEndForces"] is None
    assert result["stages"][0]["failureReason"]
    assert result["stages"][1]["status"] == "not_calculated"


@pytest.mark.parametrize("toe", ["free", "pinned"])
def test_balanced_plastic_plateau_cannot_pass_as_a_unique_equilibrium(toe):
    # Each head soil pair resists at most 100-20=80 kN. At this exact
    # capacity, any further rigid translation (free) or rotation (pinned)
    # remains balanced. A zero force residual alone cannot validate a
    # displacement on this mechanism; actual material tangents must restrain
    # both beam rigid-body modes before committing its state.
    soils = [_soil(side, id=f"{side}-{node}", node=node, depthM=float(node), upperKn=100.0)
             for node in (0, 1) for side in (-1, 1)]
    initial = _stage("initial", soil=soils)
    plateau = _stage("plateau", 80.0, soil=soils)
    plateau["externalForcesKn"][1] = 80.0 if toe == "free" else 0.0
    later = deepcopy(plateau); later["id"] = later["name"] = "later"
    model = _model([initial, plateau, later]); model["toeBoundary"] = toe
    result = _solver()(model)
    assert result["stages"][0]["status"] == "success"
    assert result["status"] == "failed"
    assert result["stages"][1]["status"] == "failed"
    assert "tangent" in result["stages"][1]["failureReason"]
    assert result["stages"][1]["nodalDisplacementM"] is None
    assert result["stages"][1]["elementEndForces"] is None
    assert result["stages"][2]["status"] == "not_calculated"


@pytest.mark.parametrize("bound", ["lower", "upper"])
@pytest.mark.parametrize("force", [-1.0, 0.0, 1.0])
def test_complementary_soil_limits_restrain_both_directions(bound, force):
    # Hand-derived aggregate stiffness is 1000 kN/m, because only one side
    # engages in either direction. The opposite-side pair must not be deleted
    # at a corner or replaced with a physical 2000 kN/m bilateral spring.
    p0 = 10.0 if bound == "lower" else 100.0
    soils = [_soil(side, id=f"{side}-{node}", node=node, depthM=float(node),
                   p0Kn=p0, lowerKn=10.0, upperKn=100.0)
             for node in (0, 1) for side in (-1, 1)]
    first = _stage("complementary", force, soil=soils)
    first["externalForcesKn"][1] = force
    model = _model([first]); model.update(eiKnM2=10000.0, toeBoundary="free")
    actual = _successful(_solver()(model))[0]
    # The existing force criterion at the upper corner is 1e-10*400=4e-8
    # kN; with aggregate stiffness 1000 kN/m this bounds y error by 4e-11 m.
    assert actual["nodalDisplacementM"] == pytest.approx([force / 1000.0] * 2, abs=5e-11)
    assert actual["nodalRotation"] == pytest.approx([0.0, 0.0], abs=1e-11)
    assert actual["elementEndForces"][0] == pytest.approx([0.0] * 4, abs=1e-8)


def test_three_one_sided_soils_can_close_the_rigid_motion_cone_without_local_pairs():
    soils = [_soil(sign, id=str(node), node=node, depthM=depth, p0Kn=10.0, lowerKn=10.0, upperKn=100.0)
             for node, sign, depth in ((0, 1, 0.0), (1, -1, .5), (2, 1, 1.0))]
    first = _stage("closed_cone", soil=soils); first["externalForcesKn"] = [10.0, -10.0, 10.0]
    model = _model([first]); model.update(nodesM=[0.0, .5, 1.0], toeBoundary="free")
    actual = _successful(_solver()(model))[0]
    assert actual["nodalDisplacementM"] == pytest.approx([0.0] * 3, abs=1e-12)


def test_public_input_with_k0_equal_to_active_limit_has_a_stable_complete_history():
    from math import pi, radians, tan
    from app.section.models import freeze_section, verification_example
    from app.section.preprocess import build_solver_model

    data = verification_example()
    for layer in data["soil_layers"]:
        layer["cohesion_kpa"] = 0.0
        layer["k0"] = tan(pi / 4 - radians(layer["phi_deg"]) / 2) ** 2
    actual = _successful(_solver()(build_solver_model(freeze_section(data))))
    assert actual[0]["nodalDisplacementM"] == pytest.approx([0.0] * 83, abs=1e-12)


def _observed_native_yield_band_station():
    # Actual S2 station from the mixed total/effective integration fixture.
    # Its inside distance is 1.7930e-14 kN < k*DBL_EPSILON=2.2112e-14.
    return _soil(1, p0Kn=.12944014443502652, kKnPerM=99.58160130077921,
        lowerKn=.06181914520747147, upperKn=.1970611436625816,
        trialPressureKn=.19706114366256366, pressureKn=.1970611436625816,
        tangentKnPerM=0., localDisplacementM=.002605836055945841,
        previousPlasticDisplacementM=.001926784922561345)


def test_native_epp_yield_band_is_a_one_sided_corner_not_an_invalid_elastic_point():
    from app.section.independent import _check_final_tangent_restraint
    station = _observed_native_yield_band_station()
    report = _check_final_tangent_restraint([0.,1.], 'fixed', [station], [])
    assert report['nativeYieldBandCornerCount'] == 1
    assert report['oneSidedConstraintCount'] == 1
    assert report['strictlyPlasticBranchesPromoted'] is False
    # A single corner plus a pinned toe still permits rotation in one
    # direction. Treating the corner as bilateral would wrongly pass this.
    with pytest.raises(RuntimeError, match='rigid-motion'):
        _check_final_tangent_restraint([0.,1.], 'pinned', [station], [])


def test_zero_native_tangent_further_inside_still_fails_the_material_consistency_check():
    from app.section.independent import _check_final_tangent_restraint
    station = _observed_native_yield_band_station()
    station['trialPressureKn'] = station['upperKn'] - 1e-10
    with pytest.raises(RuntimeError, match='interior elastic'):
        _check_final_tangent_restraint([0.,1.], 'fixed', [station], [])


def test_native_yield_band_never_promotes_a_strictly_plastic_branch_to_restraint():
    from app.section.independent import _check_final_tangent_restraint
    station = _observed_native_yield_band_station()
    station['trialPressureKn'] = station['upperKn'] + 1e-14
    with pytest.raises(RuntimeError, match='rigid-motion'):
        _check_final_tangent_restraint([0.,1.], 'pinned', [station], [])


def test_native_yield_band_requires_native_pressure_at_the_corresponding_limit():
    from app.section.independent import _check_final_tangent_restraint
    station = _observed_native_yield_band_station()
    station['pressureKn'] = station['lowerKn']
    with pytest.raises(RuntimeError, match='interior elastic'):
        _check_final_tangent_restraint([0.,1.], 'fixed', [station], [])
