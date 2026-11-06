"""Closed-form spring/beam checks distinguish three stage reset policies."""
from copy import deepcopy

import pytest

from app.section.solver import solve_stages


def fixture(action):
    soil = {'id': 'in', 'node': 0, 'sign': 1, 'side': 'inside', 'depthM': 0.0,
            'p0Kn': 50.0, 'kKnPerM': 1000.0, 'lowerKn': 20.0, 'upperKn': 150.0}
    stages = []
    for i, force in enumerate([300.0, 180.0, 180.0]):
        stages.append({'id': f'S{i}', 'name': f'S{i}', 'event': 'test_event',
                       'externalForcesKn': [force, 0.0], 'soilSprings': [deepcopy(soil)],
                       'installSupports': [], 'removeSupports': [],
                       'soilHistoryAction': action if i == 1 else 'carry_plastic_history_v1'})
    return {'nodesM': [0.0, 1.0], 'eiKnM2': 1000.0 / 3.0,
            'toeBoundary': 'fixed', 'stages': stages}


@pytest.mark.parametrize('action,offset,u,p', [
    ('carry_plastic_history_v1', .05, .09, 90.0),
    ('reset_plastic_offset_to_zero_v1', 0, .065, 115.0),
    ('rebase_soil_at_current_displacement_v1', .15, .14, 40.0),
])
def test_reset_is_explicit_and_uses_previous_converged_state(action, offset, u, p):
    # Beam tip K=1000; first F=300 gives u=.15, p=150, x_pl=.05.
    # Second equilibrium: 1000u + clip(50+1000(u-offset),20,150)=180.
    result = solve_stages(fixture(action))
    assert result['status'] == 'success'
    step = result['stages'][1]
    assert step['nodalDisplacementM'][0] == pytest.approx(u, abs=1e-11)
    assert step['soilStates'][0]['previousPlasticDisplacementM'] == pytest.approx(offset)
    assert step['soilStates'][0]['pressureKn'] == pytest.approx(p, abs=1e-8)
    assert step['soilHistoryAction'] == action
    assert result['stages'][2]['nodalDisplacementM'][0] == pytest.approx(u, abs=1e-11)


def test_unknown_reset_rule_is_not_silently_treated_as_history_carry():
    with pytest.raises(ValueError, match='history|历史'):
        solve_stages(fixture('FRWS_default_unknown'))


def test_support_installation_reference_survives_later_soil_rebase():
    physical = fixture('rebase_soil_at_current_displacement_v1')
    installed = deepcopy(physical['stages'][0])
    installed.update(id='install', event='install', installSupports=[
        {'id': 'strut', 'node': 0, 'kind': 'elastic', 'stiffnessKnPerM': 500.0,
         'compressionOnly': False, 'preloadKn': 0.0}])
    physical['stages'].insert(1, installed)
    result = solve_stages(physical)
    assert result['status'] == 'success'
    assert result['stages'][1]['supportStates'][0]['reactionKn'] == pytest.approx(0, abs=1e-8)
    assert result['stages'][2]['supportStates'][0]['referenceDisplacementM'] == pytest.approx(.15)
    # 1000u + 50+1000(u-.15) + 500(u-.15) = 180 -> u=.142.
    assert result['stages'][2]['nodalDisplacementM'][0] == pytest.approx(.142, abs=1e-11)


@pytest.mark.parametrize('action,u,p', [
    ('carry_plastic_history_v1', .09, 90.),
    ('reset_plastic_offset_to_zero_v1', .065, 115.),
    ('rebase_soil_at_current_displacement_v1', .14, 40.),
])
def test_real_opensees_independently_applies_each_reset_policy(action, u, p):
    pytest.importorskip('openseespy')
    from app.section.independent import solve_stages as independent
    result = independent(fixture(action))
    assert result['solver'] == 'OpenSees'
    assert result['status'] == 'success'
    assert result['stages'][1]['nodalDisplacementM'][0] == pytest.approx(u, abs=1e-11)
    assert result['stages'][1]['soilStates'][0]['pressureKn'] == pytest.approx(p, abs=1e-8)
    assert result['stages'][1]['soilHistoryAction'] == action
