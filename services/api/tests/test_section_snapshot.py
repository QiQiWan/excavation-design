from __future__ import annotations

import copy
import importlib
import json

import pytest


def api():
    try:
        return importlib.import_module('app.section.models')
    except ModuleNotFoundError:
        pytest.fail('Frozen section input contract is not implemented')


def test_frozen_snapshot_returns_isolated_copies_and_is_reproducible():
    m = api()
    data = m.verification_example()
    original = copy.deepcopy(data)
    snap = m.freeze_section(data)
    data['pile']['diameter_m'] = 1.2
    exposed = snap.payload()
    exposed['stages'][1]['excavation_depth_m'] = 99
    assert snap.payload() == m.SectionInput.model_validate(original).model_dump(mode='json')
    assert snap.snapshot_hash == m.freeze_section(original).snapshot_hash
    assert len(snap.snapshot_hash) == 64


def test_snapshot_import_rejects_changed_payload_and_unknown_fields():
    m = api()
    snap = m.freeze_section(m.verification_example())
    record = snap.to_record()
    record['input']['pile']['length_m'] += 1
    with pytest.raises(ValueError, match='hash|哈希'):
        m.FrozenSectionSnapshot.from_record(record)
    data = m.verification_example()
    data['pile']['auto_optimize'] = True
    with pytest.raises(ValueError):
        m.freeze_section(data)


@pytest.mark.parametrize('mutation', ['nonfinite', 'missing_e', 'preload', 'bad_stage_order', 'combined_change', 'rigid_compression', 'layer_gap'])
def test_unknown_or_unsupported_physics_cannot_be_silently_repaired(mutation):
    m = api()
    data = m.verification_example()
    if mutation == 'nonfinite':
        data['pile']['elastic_modulus_kpa'] = float('nan')
    elif mutation == 'missing_e':
        del data['pile']['elastic_modulus_kpa']
    elif mutation == 'preload':
        data['supports'][0]['preload_kn'] = 10
    elif mutation == 'bad_stage_order':
        data['stages'][2]['install_support_ids'] = []
    elif mutation == 'combined_change':
        data['stages'][2]['water_inside_depth_m'] += 0.5
    elif mutation == 'rigid_compression':
        data['supports'][1]['compression_only'] = True
    else:
        data['soil_layers'][1]['top_m'] += 0.2
    with pytest.raises(ValueError):
        m.freeze_section(data)


def test_analysis_identity_includes_method_mesh_and_rebar_but_not_display_name():
    m = api()
    data = m.verification_example()
    first = m.freeze_section(data)
    data['name'] = '另一个显示名称'
    second = m.freeze_section(data)
    assert first.snapshot_hash != second.snapshot_hash
    assert first.analysis_hash == second.analysis_hash
    data['analysis']['max_element_size_m'] *= 0.5
    assert m.freeze_section(data).analysis_hash != second.analysis_hash


def test_snapshot_record_roundtrip_uses_no_mutable_project_reference():
    m = api()
    snap = m.freeze_section(m.verification_example())
    restored = m.FrozenSectionSnapshot.from_record(json.loads(json.dumps(snap.to_record())))
    assert restored == snap
    with pytest.raises((AttributeError, TypeError)):
        snap.snapshot_hash = '0' * 64


@pytest.mark.parametrize('cover,count,diameter', [(1, 16, 18), (59, 100, 30)])
def test_bar_circles_must_fit_inside_the_pile_without_overlap(cover, count, diameter):
    m = api()
    data = m.verification_example()
    data['reinforcement'].update(bar_axis_cover_mm=cover, bar_count=count, bar_diameter_mm=diameter)
    with pytest.raises(ValueError, match='钢筋|重叠'):
        m.freeze_section(data)
