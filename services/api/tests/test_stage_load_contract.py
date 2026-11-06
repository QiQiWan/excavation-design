"""Analytic load checks: expectations are hand-derived, not solver-generated."""
import json

import pytest

from app.calculation.earth_pressure import calculate_lateral_pressure_profile
from app.schemas.domain import DesignSettings, GeologicalLayer, SoilParameters


def soil(cohesion=0.0, phi=30.0, k0=None):
    return [GeologicalLayer(stratum_code="S1", stratum_name="Test soil", top_elevation=0,
        bottom_elevation=-30, thickness=30, parameters=SoilParameters(
            unit_weight=18, saturated_unit_weight=20, cohesion=cohesion,
            friction_angle=phi, k0=k0))]


def profile(**changes):
    args = dict(soil_profile=soil(), excavation_depth=10, groundwater_level=-30,
                groundwater_level_inside=-30, surcharge=0, calculation_depth=12, step=1)
    args.update(changes)
    return calculate_lateral_pressure_profile(**args)


def test_saturated_pressure_has_separate_soil_and_water_components():
    p = profile(groundwater_level=0, groundwater_level_inside=-10).points[5]
    assert p.vertical_stress_total == pytest.approx(100)
    assert p.vertical_stress_effective == pytest.approx(50.95)
    assert p.earth_pressure == pytest.approx(50.95 / 3, abs=1e-6)
    assert p.water_pressure == pytest.approx(49.05)
    assert p.total_pressure == pytest.approx(66.033333, abs=1e-6)


def test_water_above_pit_base_is_retained_on_inside_wall():
    p = profile(groundwater_level=0, groundwater_level_inside=-2).points[5]
    assert p.inside_water_pressure == pytest.approx(29.43)
    assert p.water_pressure == pytest.approx(19.62)
    assert p.total_pressure == pytest.approx(36.603333, abs=1e-6)


def test_active_soil_tension_cutoff_does_not_remove_hydrostatic_pressure():
    p = profile(soil_profile=soil(cohesion=20), groundwater_level=0).points[1]
    assert p.active_earth_pressure == 0
    assert p.total_pressure == pytest.approx(9.81)


def test_zero_excavation_is_initial_stage_with_positive_wall_depth():
    p = profile(excavation_depth=0)
    assert p.points[-1].depth == 12
    assert p.points[-1].total_pressure == 0
    with pytest.raises(ValueError, match="calculation_depth"):
        profile(excavation_depth=0, calculation_depth=None)


@pytest.mark.parametrize("method", ["combined", "unrecognized"])
def test_unsupported_water_soil_method_fails_explicitly(method):
    with pytest.raises(ValueError, match="water_soil_method|total.stress"):
        profile(water_soil_method=method)


def test_dry_active_at_rest_and_passive_hand_values():
    assert profile().points[5].total_pressure == pytest.approx(30)
    assert profile(mode="at_rest").points[5].total_pressure == pytest.approx(45)
    assert profile(mode="at_rest", soil_profile=soil(k0=.7)).points[5].total_pressure == pytest.approx(63)
    assert profile(mode="passive").points[5].total_pressure == pytest.approx(270)


def test_below_pit_soil_resistance_and_water_difference_are_distinct():
    p = profile(groundwater_level=0, groundwater_level_inside=-10).points[12]
    assert p.active_earth_pressure == pytest.approx(40.76)
    assert p.passive_earth_pressure == pytest.approx(61.14)
    assert p.earth_pressure == pytest.approx(-20.38)
    assert p.water_pressure == pytest.approx(98.1)
    assert p.total_pressure == pytest.approx(77.72)


@pytest.mark.parametrize("field,value", [("excavation_depth", -1), ("step", 0),
    ("surcharge", -1), ("surcharge", float("nan")), ("groundwater_level", float("inf")),
    ("calculation_depth", -1), ("mode", "bad")])
def test_invalid_pressure_inputs_are_rejected(field, value):
    with pytest.raises(ValueError):
        profile(**{field: value})


def test_vertical_stress_integrates_groundwater_crossing_exactly():
    p = profile(groundwater_level=-.123).points[1]
    assert p.vertical_stress_total == pytest.approx(19.754)


def loads(**changes):
    import importlib.util
    assert importlib.util.find_spec("app.calculation.stage_loads") is not None, "stage-load contract is missing"
    from app.calculation.stage_loads import build_stage_loads
    args = dict(soil_profile=soil(), excavation_depth=10, groundwater_level_outside=-30,
        groundwater_level_inside=-30, surcharge=30, top_elevation=0, calculation_depth=12,
        settings=DesignSettings(), step=1)
    args.update(changes)
    return build_stage_loads(**args)


def test_standard_design_and_separate_components_have_hand_calculated_loads():
    record = loads()
    assert record.standard_profile.points[5].total_pressure == pytest.approx(40)
    assert record.permanent_profile.points[5].total_pressure == pytest.approx(30)
    assert record.surcharge_profile.points[5].total_pressure == pytest.approx(10)
    assert record.design_profile.points[5].total_pressure == pytest.approx(54.5)


@pytest.mark.parametrize("changed,expected", [({"load_gamma_g":1.5},59),
    ({"load_gamma_q":1.8},58.5), ({"load_psi":.5},47.5),
    ({"importance_factor":1.2},65.4), ({"pressure_method":"at_rest"},81.75)])
def test_each_selected_setting_changes_design_pressure(changed, expected):
    record = loads(settings=DesignSettings(**changed))
    assert record.design_profile.points[5].total_pressure == pytest.approx(expected)
    if "pressure_method" not in changed:
        assert record.standard_profile.points[5].total_pressure == pytest.approx(40)


def test_favourable_passive_resistance_is_not_amplified_with_permanent_action():
    record = loads(groundwater_level_outside=0, groundwater_level_inside=-10,
        surcharge=0, settings=DesignSettings(load_gamma_g=1.5))
    p = record.design_profile.points[12]
    # Outside 40.76+117.72; inside 61.14+19.62. 1.5*158.48-80.76=156.96.
    assert p.total_pressure == pytest.approx(156.96)
    assert p.passive_earth_pressure == pytest.approx(61.14)
    assert p.inside_water_pressure == pytest.approx(19.62)
    assert record.permanent_action_profile.points[12].total_pressure == pytest.approx(158.48)
    assert record.resistance_profile.points[12].total_pressure == pytest.approx(80.76)


def test_surcharge_is_conservative_across_tension_cutoff():
    record = loads(soil_profile=soil(cohesion=20), surcharge=30)
    # At z=1: active raw=6-23.094; q increment=10: actual soil remains in tension.
    assert record.standard_profile.points[1].total_pressure == 0
    assert record.permanent_profile.points[1].total_pressure == 0
    assert record.surcharge_profile.points[1].total_pressure == pytest.approx(10)
    assert record.design_profile.points[1].total_pressure == pytest.approx(14)
    assert record.evidence["decomposition"]["standard_equals_component_sum"] is False


def test_stage_record_is_deterministic_and_defensively_immutable():
    record = loads()
    assert record.load_id == loads().load_id
    assert record.load_id != loads(surcharge=31).load_id
    assert record.load_id != loads(settings=DesignSettings(load_gamma_q=1.8)).load_id
    assert record.input_hash != loads(groundwater_level_inside=-3).input_hash
    exported = record.standard_profile
    exported.points[5].total_pressure = -999
    evidence = record.evidence
    evidence["units"]["pressure"] = "wrong"
    assert record.standard_profile.points[5].total_pressure == 40
    assert record.evidence["units"]["pressure"] == "kPa"
    assert json.loads(record.evidence_json)["load_id"] == record.load_id


@pytest.mark.parametrize("changes", [{"load_gamma_g":float("nan")}, {"load_gamma_q":-1},
    {"load_psi":1.1}, {"importance_factor":0}, {"pressure_method":"bad"}])
def test_contract_rejects_invalid_copied_settings(changes):
    with pytest.raises(ValueError):
        loads(settings=DesignSettings().model_copy(update=changes))


def test_ponded_inside_water_does_not_remove_submerged_soil_weight():
    p = profile(groundwater_level=0, groundwater_level_inside=-2).points[12]
    assert p.passive_earth_pressure == pytest.approx(61.14)
    assert p.inside_water_pressure == pytest.approx(98.1)
    assert p.total_pressure == 0


def test_negative_water_difference_is_retained_before_net_clipping():
    p = profile(groundwater_level=-30, groundwater_level_inside=-2).points[5]
    assert p.water_pressure == pytest.approx(-29.43)
    assert p.total_pressure == pytest.approx(.57)

