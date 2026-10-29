from __future__ import annotations

import copy
import importlib

import pytest

from app.section.models import freeze_section, verification_example


def api():
    try:
        return importlib.import_module('app.section.service')
    except ModuleNotFoundError:
        pytest.fail('Frozen section result pipeline is not implemented')


def test_same_frozen_snapshot_reproduces_result_and_real_installation_history():
    module = api()
    snapshot = freeze_section(verification_example())
    result = module.analyze_section(snapshot)
    again = module.analyze_section(snapshot)
    assert result['resultHash'] == again['resultHash']
    assert result['status'] == 'complete'
    assert len(result['stages']) == 6
    assert result['stages'][0]['maxAbsDisplacementMm'] == 0
    old = result['stages'][2]['supports'][0]
    assert abs(old['reactionCharacteristicKn']) < 1e-6
    new = result['stages'][4]['supports'][1]
    assert abs(new['reactionCharacteristicKn']) < 1e-5
    assert new['displacementMm'] == pytest.approx(new['referenceDisplacementMm'], abs=1e-8)
    assert result['stages'][5]['supports'][0]['status'] == 'removed'
    assert result['comparisons']['original']['status'] == 'incomplete'
    assert result['formalDesignReady'] is False


def test_envelope_keeps_shear_jumps_and_all_stage_sources_and_controls_rebar():
    snapshot = freeze_section(verification_example())
    result = api().analyze_section(snapshot)
    points = result['envelope']['points']
    above = next(p for p in points if p['depthM'] == 1.625 and p['side'] == 'above')
    below = next(p for p in points if p['depthM'] == 1.625 and p['side'] == 'below')
    assert above['vMaxKn'] != pytest.approx(below['vMaxKn'], abs=0.01)
    stage_ids = {s['id'] for s in result['stages']}
    assert all(p['mMaxStageId'] in stage_ids and p['mMinStageId'] in stage_ids for p in points)
    for p in points:
        assert p['mdMaxKnM'] == pytest.approx(1.25 * p['mMaxKnM'])
    governing = result['envelope']['governing']['momentAbs']
    row = result['reinforcement']['rows'][0]
    assert row['momentDesignKnM'] == pytest.approx(abs(governing['valueKnM']) * 1.25)
    assert row['governingStageId'] == governing['stageId']


def test_export_validation_rejects_modified_results_or_a_different_input():
    module = api()
    snapshot = freeze_section(verification_example())
    result = module.analyze_section(snapshot)
    module.validate_result(snapshot, result)
    changed = copy.deepcopy(result)
    changed['envelope']['points'][0]['mMaxKnM'] += 1
    with pytest.raises(ValueError, match='hash|哈希'):
        module.validate_result(snapshot, changed)
    data = verification_example()
    data['analysis']['design_effect_factor'] = 1.5
    with pytest.raises(ValueError):
        module.validate_result(freeze_section(data), result)


def test_failed_stage_prevents_a_complete_envelope_and_any_reinforcement_design():
    data = verification_example()
    for index, stage in enumerate(data['stages'][1:], 1):
        stage['excavation_depth_m'] = 9.0 if index <= 2 else 9.5
        stage['water_inside_depth_m'] = stage['excavation_depth_m'] + 0.5
    result = api().analyze_section(freeze_section(data))
    assert result['status'] == 'failed'
    assert result['envelope']['complete'] is False
    assert result['envelope']['points'] == []
    assert result['reinforcement']['rows'] == []
    failed = next(s for s in result['stages'] if s['status'] == 'failed')
    assert failed['maxAbsMomentKnM'] is None
    assert failed['points'] == []


@pytest.mark.parametrize('corruption', ['empty_arrays', 'false_demand', 'different_algorithm', 'false_envelope'])
def test_content_hash_does_not_replace_internal_result_contract(corruption):
    module = api()
    snapshot = freeze_section(verification_example())
    result = module.analyze_section(snapshot)
    if corruption == 'empty_arrays':
        result['stages'][3]['nodalDisplacementM'] = []
        result['stages'][3]['elementEndForces'] = []
    elif corruption == 'false_demand':
        result['reinforcement']['rows'][0]['momentDesignKnM'] = 0
    elif corruption == 'different_algorithm':
        result['algorithmVersion'] = 'different-unverified-method'
    else:
        result['envelope']['points'][1]['mdMaxKnM'] += 10
    with pytest.raises(ValueError):
        module.validate_result(snapshot, module.seal_result(result))


def test_missing_final_solver_stage_is_rejected_before_enveloping(monkeypatch):
    module = api()
    original = module.solve_stages
    def truncate(model):
        actual = original(model)
        actual['stages'] = actual['stages'][:-1]
        return actual
    monkeypatch.setattr(module, 'solve_stages', truncate)
    with pytest.raises(ValueError, match='阶段'):
        module.analyze_section(freeze_section(verification_example()))
