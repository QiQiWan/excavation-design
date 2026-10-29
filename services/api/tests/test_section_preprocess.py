from __future__ import annotations

import importlib
import math

import pytest

from app.section.models import freeze_section, verification_example


def api():
    try:
        return importlib.import_module('app.section.preprocess')
    except ModuleNotFoundError:
        pytest.fail('Physical section pressure discretisation is not implemented')


def test_effective_stress_integrates_layer_and_water_boundaries():
    module = api()
    model = freeze_section(verification_example()).validated_input()
    # 0..1.5 dry layer1; 1.5..4 submerged layer1; 4..6 submerged layer2.
    expected = 1.5 * 18 + 2.5 * (20 - 9.81) + 2 * (21 - 9.81)
    assert module.effective_vertical_stress(model, 6, 0, 1.5) == pytest.approx(expected)
    # Excavation unloads all overburden above H; inside water changes separately.
    assert module.effective_vertical_stress(model, 6, 4.9, 5.4) == pytest.approx(0.5 * 19 + 0.6 * (21 - 9.81))


def test_initial_balance_is_a_consequence_of_equal_soil_and_water_forces():
    model = api().build_solver_model(freeze_section(verification_example()))
    stage = model['stages'][0]
    assert max(map(abs, stage['externalForcesKn'])) == 0
    resultant = [0.0] * len(model['nodesM'])
    for spring in stage['soilSprings']:
        resultant[spring['node']] += spring['sign'] * spring['p0Kn']
    assert max(map(abs, resultant)) < 1e-10
    assert any(spring['p0Kn'] > 1 for spring in stage['soilSprings'])


def test_water_force_and_moment_are_consistent_integrals_without_any_soil_force():
    snap = freeze_section(verification_example())
    model = api().build_solver_model(snap)
    stage = model['stages'][3]
    length, spacing, gamma = 10, 0.9, 9.81
    water_out, water_in = 1.5, 5.4
    force = spacing * gamma * ((length - water_out) ** 2 - (length - water_in) ** 2) / 2
    def moment(water):
        h = length - water
        return gamma * spacing * h ** 2 / 2 * (water + 2 * h / 3)
    assert sum(stage['externalForcesKn']) == pytest.approx(force, rel=1e-12)
    assert sum(z * f for z, f in zip(model['nodesM'], stage['externalForcesKn'])) == pytest.approx(moment(water_out) - moment(water_in), rel=1e-12)


def test_material_points_keep_identity_and_excavation_only_removes_inside_springs():
    model = api().build_solver_model(freeze_section(verification_example()))
    previous = {s['id']: s for s in model['stages'][0]['soilSprings']}
    for stage in model['stages'][1:]:
        current = {s['id']: s for s in stage['soilSprings']}
        assert set(current) <= set(previous)
        for key, spring in current.items():
            assert (spring['node'], spring['sign'], spring['depthM']) == (previous[key]['node'], previous[key]['sign'], previous[key]['depthM'])
            assert spring['kKnPerM'] > 0
            assert spring['lowerKn'] <= spring['p0Kn'] <= spring['upperKn']
        previous = current
    for support in (1.625, 4.6):
        assert support in model['nodesM']


def test_tributary_width_and_effective_soil_width_have_distinct_roles():
    module = api()
    data = verification_example()
    first = module.build_solver_model(freeze_section(data))
    data['soil_effective_width_m'] *= 2
    second = module.build_solver_model(freeze_section(data))
    a, b = first['stages'][3]['soilSprings'][0], second['stages'][3]['soilSprings'][0]
    assert b['kKnPerM'] == pytest.approx(2 * a['kKnPerM'])
    assert b['p0Kn'] == a['p0Kn']
    assert second['stages'][3]['externalForcesKn'] == first['stages'][3]['externalForcesKn']
    assert first['eiKnM2'] == pytest.approx(30e6 * math.pi * 0.7 ** 4 / 64)
