from copy import deepcopy

import pytest

from app.section.models import freeze_section
from app.section.examples import verification_example_v2
from app.section.service import analyze_section


def modules():
    from app.section import reference_workflow
    from app.section.storage import SectionStore
    from app.routers import sections
    return reference_workflow, SectionStore, sections


def test_original_intake_is_available_in_installed_package_and_preserves_missing_settings(tmp_path):
    workflow, store_type, routes = modules()
    response = routes.a3_reference(store_type(tmp_path))
    assert response['assessment']['status'] == 'incomplete'
    fields = {p['id']: p for p in response['contract']['parameters']}
    assert fields['materials.pile_concrete_E']['value'] is None
    assert all(not curve['samples'] for stage in response['curveTemplate']['stages'] for curve in stage['curves'])
    assert store_type(tmp_path).load_reference_contract(response['contract']['contractHash']) == response['contract']


def test_comparison_is_immutable_bound_to_actual_result_and_cannot_promote_empty_original_curves(tmp_path):
    workflow, store_type, routes = modules()
    store = store_type(tmp_path)
    intake = routes.a3_reference(store)
    data = verification_example_v2()
    data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot = freeze_section(data)
    result = analyze_section(snapshot)
    store.save_snapshot(snapshot); store.save_result(snapshot, result)
    request = routes.ReferenceComparisonRequest(contract_hash=intake['contract']['contractHash'],
                                                curve_bundle_hash=intake['curveTemplate']['bundleHash'])
    record = routes.compare_reference(result['resultHash'], request, store)
    assert record['snapshotHash'] == snapshot.snapshot_hash
    assert record['resultHash'] == result['resultHash']
    assert record['report']['sameConditionsVerified'] is False
    assert record['report']['coverage']['fullCurveAcceptance'] is False
    assert store.load_reference_comparison(record['comparisonHash']) == record
    assert store.load_latest_reference_comparison(result['resultHash']) == record
    assert store.load_result(result['resultHash']) == result
    bad = deepcopy(record); bad['resultHash'] = 'b'*64
    with pytest.raises(ValueError):
        store.save_reference_comparison(bad)


def test_rehashed_comparison_flags_are_recomputed_from_frozen_evidence(tmp_path):
    workflow, store_type, routes = modules()
    store = store_type(tmp_path); intake = routes.a3_reference(store)
    snapshot = freeze_section(verification_example_v2()); result = analyze_section(snapshot)
    record = workflow.make_reference_comparison(snapshot, result, intake['contract'], intake['curveTemplate'])
    record['report']['sameConditionsVerified'] = True
    from app.section.models import content_hash
    record['comparisonHash'] = content_hash({k:v for k,v in record.items() if k!='comparisonHash'})
    with pytest.raises(ValueError, match='comparison|比较'):
        workflow.validate_reference_comparison(record, snapshot, result, intake['contract'], intake['curveTemplate'])


def test_numeric_curve_download_is_bound_to_the_saved_result(tmp_path):
    _, store_type, routes = modules()
    store = store_type(tmp_path)
    snapshot = freeze_section(verification_example_v2()); result = analyze_section(snapshot)
    store.save_snapshot(snapshot); store.save_result(snapshot, result)
    bundle = routes.result_curves(result['resultHash'], store)
    assert bundle['source']['sha256'] == result['resultHash']
    assert bundle['source']['kind'] == 'synthetic_protocol_fixture'
    assert len(bundle['stages']) == len(result['stages'])


@pytest.mark.parametrize('corruption', ['invalid_bundle', 'contract_mismatch', 'case_mismatch'])
def test_uncompared_supplement_validates_curve_identity(corruption):
    from app.section.reference_curves import freeze_curve_bundle
    workflow, _, _ = modules()
    intake = workflow.a3_reference_assets()
    data = verification_example_v2(); data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot = freeze_section(data)
    # This function binds attachment metadata, without calculating a new result.
    result = {'resultHash': 'a'*64}
    curves = deepcopy(intake['curveTemplate'])
    if corruption == 'invalid_bundle':
        curves = {'bundleHash': 'b'*64}
    else:
        curves.pop('bundleHash')
        if corruption == 'contract_mismatch':
            curves['contractHash'] = 'f'*64
        else:
            curves['caseId'] = 'OTHER-PROJECT'
        curves = freeze_curve_bundle(curves)
    with pytest.raises(ValueError):
        workflow.reference_supplement(snapshot, result, intake['contract'], curves)


def test_uncompared_supplement_accepts_valid_attached_curves_without_claiming_comparison():
    workflow, _, _ = modules()
    intake = workflow.a3_reference_assets()
    data = verification_example_v2(); data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot = freeze_section(data)
    supplement = workflow.reference_supplement(snapshot, {'resultHash':'a'*64}, intake['contract'], intake['curveTemplate'])
    assert supplement['referenceCurves']['bundleHash'] == intake['curveTemplate']['bundleHash']
    assert supplement['comparison'] is None
    assert supplement['assessment']['status'] == 'incomplete'


@pytest.mark.parametrize('missing_dependency', ['reference_curves', 'reference_comparisons'])
def test_saved_comparison_dependency_loss_is_not_silently_replaced_by_contract_only(tmp_path, missing_dependency):
    workflow, store_type, routes = modules()
    store = store_type(tmp_path); intake = routes.a3_reference(store)
    data = verification_example_v2(); data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot = freeze_section(data); result = analyze_section(snapshot)
    store.save_snapshot(snapshot); store.save_result(snapshot, result)
    record = workflow.make_reference_comparison(snapshot, result, intake['contract'], intake['curveTemplate'])
    store.save_reference_comparison(record)
    identity = intake['curveTemplate']['bundleHash'] if missing_dependency == 'reference_curves' else record['comparisonHash']
    (tmp_path/missing_dependency/f'{identity}.json').unlink()
    with pytest.raises(ValueError, match='missing|损坏|缺失|incomplete|dependency'):
        store.reference_supplement(snapshot, result)


def test_no_saved_comparison_is_distinct_from_broken_evidence(tmp_path):
    workflow, store_type, routes = modules()
    store = store_type(tmp_path); intake = routes.a3_reference(store)
    data = verification_example_v2(); data['reference_contract_hash'] = intake['contract']['contractHash']
    snapshot = freeze_section(data)
    supplement = store.reference_supplement(snapshot, {'resultHash':'a'*64})
    assert supplement['contract']['contractHash'] == intake['contract']['contractHash']
    assert supplement['referenceCurves'] is None
    assert supplement['comparison'] is None
