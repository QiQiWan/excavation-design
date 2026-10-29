from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def api():
    try:
        return importlib.import_module('app.section.reference_contract')
    except ModuleNotFoundError:
        pytest.fail('Original reference contract is not implemented')


def audit():
    return json.loads((ROOT / 'examples/frozen-section/A3-3-reference-audit.json').read_text())


def test_original_missing_modulus_is_not_a_c30_default_and_inputs_are_unchanged():
    data = audit()
    before = copy.deepcopy(data)
    contract = api().build_a3_reference_contract(data)
    fields = {p['id']: p for p in contract['parameters']}
    assert fields['materials.pile_concrete_E']['value'] is None
    assert fields['materials.pile_concrete_E']['status'] == 'unconfirmed'
    assert fields['identity.software']['value'] == '同济启明星软件FRWS9.0'
    assert fields['historical_code_edition']['value'] == 'DB33/T1096-2014'
    assert fields['current_review_basis']['value'] is None
    assert data == before
    assert api().assess_reference_contract(contract)['status'] == 'incomplete'


def test_original_datum_transform_retains_negative_water_and_source_values():
    contract = api().build_a3_reference_contract(audit())
    fields = {p['id']: p for p in contract['parameters']}
    assert fields['stages.0.event_depth']['value'] == 2.05
    for key, expected in [('datum.local_excavation_depths_m', [.75,.75,3.6,3.6,3.6]),
                          ('datum.local_support_depths_m', {'S1':.325,'T1':3.3}),
                          ('datum.local_outside_water_m', -.8),
                          ('datum.local_layer_interfaces_m', [-1.3,-.2,.5,5.7,6.3,56.3])]:
        p = fields[key]
        assert p['value'] == expected
        assert p['status'] == 'derived_interpretation'
        assert p['derivation']
        assert p['sourceRefs']


def test_contract_tamper_and_missing_critical_gate_cannot_be_resealed():
    module = api()
    contract = module.build_a3_reference_contract(audit())
    contract['parameters'][0]['value'] = 'tampered'
    with pytest.raises(ValueError, match='hash'):
        module.validate_reference_contract(contract)
    contract = module.build_a3_reference_contract(audit())
    contract['criticalConditionIds'] = []
    contract.pop('contractHash')
    with pytest.raises(ValueError, match='critical'):
        module.freeze_reference_contract(contract)


def test_fake_native_filename_or_caller_approval_does_not_close_unknown_conditions():
    module = api()
    contract = module.build_a3_reference_contract(audit())
    contract.pop('contractHash')
    contract['sourceRegistry'].append({'id':'FAKE', 'kind':'original_solver_export',
                                     'file':'project.frws', 'sha256':'a'*64})
    frozen = module.freeze_reference_contract(contract)
    assert module.assess_reference_contract(frozen)['status'] == 'incomplete'
    contract['approved'] = True
    with pytest.raises(ValueError):
        module.freeze_reference_contract(contract)


def test_unknown_cannot_be_marked_original_without_a_new_supplied_source():
    module = api()
    contract = module.build_a3_reference_contract(audit())
    contract.pop('contractHash')
    p = next(p for p in contract['parameters'] if p['id']=='materials.pile_concrete_E')
    p.update(value=30000, status='original_fact')
    with pytest.raises(ValueError, match='confirmation|source'):
        module.freeze_reference_contract(contract)


def test_unknown_and_unsupported_original_methods_are_named_as_blockers():
    contract = api().build_a3_reference_contract(audit())
    ids = {p['id'] for p in api().assess_reference_contract(contract)['missingConditions']}
    assert {'materials.pile_concrete_E','soil.reaction_formula',
            'soil.effective_soil_reaction_width', 'boundary_and_solver_missing.output_force_basis',
            'loads.adjacent_surcharge.lateral_pressure_distribution',
            'supports.crown_beam_equivalent_support.application_location_and_combination'} <= ids
