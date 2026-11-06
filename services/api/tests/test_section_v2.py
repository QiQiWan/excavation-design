"""Versioning and state semantics: old records stay frozen; new rules are explicit."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.section.models import FrozenSectionSnapshot, freeze_section, verification_example
from app.section.preprocess import build_solver_model
from app.section.service import validate_result


def v2_input():
    data = verification_example()
    data['schema_version'] = 'pitguard.frozen-section/2'
    data['analysis']['pressure_method'] = 'explicit_rankine_stress_modes_v2'
    data['method_extensions'] = {
        'schema_version': 'pitguard.method-extensions/1',
        'geometry_basis': 'flat_wall_top_surface',
        'm_law': 'k_h=m*z', 'm_depth_reference': 'current_side_surface',
        'm_basis': '验证例明确采用从每侧现地表起算的线性m法，并非原FRWS模型设置。',
        'surface_load_method': 'boussinesq_vertical_rankine_k_mapping_v1',
        'surface_loads': [],
        'layer_stress_modes': [
            {'layer_id': layer['id'], 'mode': 'effective_separated',
             'parameter_basis': 'effective', 'basis': '验证例明确使用有效应力c、phi及K0参数。'}
            for layer in data['soil_layers']
        ],
        'stage_options': [
            {'stage_id': stage['id'], 'active_load_ids': [],
             'soil_history_action': 'carry_plastic_history_v1',
             'history_basis': '验证例明确继承已收敛土弹簧的塑性位移，原FRWS规则未确认。'}
            for stage in data['stages']
        ],
    }
    data['engineering_design'] = None
    data['reference_contract_hash'] = None
    return data


def test_historical_v1_snapshot_and_sealed_result_remain_readable():
    examples = Path(__file__).resolve().parents[3] / 'examples' / 'frozen-section'
    snapshot = FrozenSectionSnapshot.from_record(json.loads((examples / 'validated-snapshot.json').read_text()))
    assert snapshot.snapshot_hash == '2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd'
    validate_result(snapshot, json.loads((examples / 'validated-result.json').read_text()))
    assert 'method_extensions' not in snapshot.payload()


def test_v2_records_roundtrip_and_freeze_method_identity():
    data = v2_input()
    first = freeze_section(data)
    assert first.to_record()['schemaVersion'] == 'pitguard.frozen-section/2'
    assert FrozenSectionSnapshot.from_record(first.to_record()) == first
    data['method_extensions']['stage_options'][3]['soil_history_action'] = 'reset_plastic_offset_to_zero_v1'
    assert freeze_section(data).analysis_hash != first.analysis_hash
    record = first.to_record()
    record['schemaVersion'] = 'pitguard.frozen-section/1'
    with pytest.raises(ValueError):
        FrozenSectionSnapshot.from_record(record)


@pytest.mark.parametrize('problem', ['v1_options', 'missing_v2_options', 'missing_layer', 'wrong_basis',
                                     'missing_stage', 'duplicate_stage', 'install_reset', 'unknown_load'])
def test_incomplete_or_ambiguous_method_options_are_rejected(problem):
    data = v2_input()
    options = data['method_extensions']
    if problem == 'v1_options':
        data['schema_version'] = 'pitguard.frozen-section/1'
        data['analysis']['pressure_method'] = 'effective_rankine_bounds_k0_v1'
    elif problem == 'missing_v2_options':
        data['method_extensions'] = None
    elif problem == 'missing_layer':
        options['layer_stress_modes'].pop()
    elif problem == 'wrong_basis':
        options['layer_stress_modes'][0]['parameter_basis'] = 'total'
    elif problem == 'missing_stage':
        options['stage_options'].pop()
    elif problem == 'duplicate_stage':
        options['stage_options'][1]['stage_id'] = options['stage_options'][0]['stage_id']
    elif problem == 'install_reset':
        options['stage_options'][2]['soil_history_action'] = 'rebase_soil_at_current_displacement_v1'
    else:
        options['stage_options'][1]['active_load_ids'] = ['undeclared']
    with pytest.raises(ValueError):
        freeze_section(data)


def test_finite_load_activation_is_separate_from_support_installation():
    data = v2_input()
    data['method_extensions']['surface_loads'] = [{
        'id': 'Q', 'kind': 'strip', 'q_kpa': 30, 'near_m': 4.3, 'far_m': 16.3,
        'along_start_m': None, 'along_end_m': None,
        'basis': '独立验证例的显式条带邻载，距离按墙中心线且不是原项目换算。'}]
    # Installing support cannot silently introduce a new neighboring load.
    data['method_extensions']['stage_options'][2]['active_load_ids'] = ['Q']
    with pytest.raises(ValueError):
        freeze_section(data)
    for row in data['method_extensions']['stage_options'][1:]:
        row['active_load_ids'] = ['Q']
    physical = build_solver_model(freeze_section(data))
    assert physical['stages'][1]['activeSurfaceLoadIds'] == ['Q']
    outside = next(s for s in physical['stages'][1]['soilSprings'] if s['side'] == 'outside')
    assert outside['surchargeVerticalKpa'] > data['stages'][1]['surcharge_kpa']
    assert outside['pressureBasis'] == 'effective'
    assert outside['externalWaterKpa'] >= 0


def test_no_new_options_can_change_v1_discrete_model_or_historical_force_basis():
    data = verification_example()
    before = build_solver_model(freeze_section(data))
    assert 'methodExtensions' not in before
    assert 'soilHistoryAction' not in before['stages'][0]
    data['method_extensions'] = None
    data['engineering_design'] = None
    data['reference_contract_hash'] = None
    assert build_solver_model(freeze_section(data)) == before


def test_combined_and_separated_phi_zero_have_identical_total_pressure_no_duplicate_water():
    data = v2_input()
    for layer in data['soil_layers']:
        layer.update(gamma_unsat_kn_m3=20, gamma_sat_kn_m3=20,
                     cohesion_kpa=0, phi_deg=0, k0=1)
    data['analysis']['water_unit_weight_kn_m3'] = 10
    for stage in data['stages']:
        stage['water_outside_depth_m'] = 0
        stage['water_inside_depth_m'] = stage['excavation_depth_m']
    separated = build_solver_model(freeze_section(data))
    for mode in data['method_extensions']['layer_stress_modes']:
        mode.update(mode='total_combined', parameter_basis='total')
    combined = build_solver_model(freeze_section(data))
    for stage_s, stage_c in zip(separated['stages'], combined['stages']):
        assert all(abs(f) < 1e-12 for f in stage_c['externalForcesKn'])
        for s, c in zip(stage_s['soilSprings'], stage_c['soilSprings']):
            assert s['p0Kpa'] + s['externalWaterKpa'] == pytest.approx(c['p0Kpa'])
            assert c['externalWaterKpa'] == 0
            assert c['pressureBasis'] == 'total'


def test_mixed_layer_modes_do_not_change_vertical_stress_integration():
    data = v2_input()
    separated = build_solver_model(freeze_section(data))
    data['method_extensions']['layer_stress_modes'][0].update(mode='total_combined', parameter_basis='total')
    mixed = build_solver_model(freeze_section(data))
    for a, b in zip(separated['stages'][-1]['soilSprings'], mixed['stages'][-1]['soilSprings']):
        assert a['totalVerticalKpa'] == b['totalVerticalKpa']
        assert a['effectiveVerticalKpa'] == b['effectiveVerticalKpa']
        if b['layerId'] == data['soil_layers'][1]['id']:
            assert a['p0Kn'] == b['p0Kn']
            assert a['externalWaterKpa'] == b['externalWaterKpa']


def test_same_snapshot_audit_uses_active_loads_and_current_layer_pressure_basis():
    from app.section.service import analyze_section
    from app.section.benchmarks import same_snapshot_hand_checks
    data = v2_input()
    # Mixed basis is an explicit verification definition, not a fit to FRWS.
    data['method_extensions']['layer_stress_modes'][0].update(mode='total_combined', parameter_basis='total')
    data['method_extensions']['surface_loads'] = [{
        'id': 'Q', 'kind': 'rectangle', 'q_kpa': 30, 'near_m': 4.3, 'far_m': 16.3,
        'along_start_m': -32.5, 'along_end_m': 32.5,
        'basis': '验证例明确位于65m矩形邻载中点，仅为验证定义，不是原项目确认。'}]
    for row in data['method_extensions']['stage_options'][1:]:
        row['active_load_ids'] = ['Q']
    snap = freeze_section(data)
    result = analyze_section(snap)
    report = same_snapshot_hand_checks(snap, result)
    assert report['status'] == 'passed'
    assert report['soilPressureWorkedRows'][0]['finiteSurchargeKpa'] > 0
    assert result['stages'][1]['activeSurfaceLoadIds'] == ['Q']
    assert result['stages'][1]['soilHistoryAction'] == 'carry_plastic_history_v1'
    assert 'engineeringChecks' in result
    assert result['engineeringChecks']['formalDesignReady'] is False
