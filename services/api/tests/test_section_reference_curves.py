from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest

from app.section.models import content_hash, freeze_section, verification_example

ROOT = Path(__file__).resolve().parents[3]


def api():
    try:
        return importlib.import_module('app.section.reference_curves')
    except ModuleNotFoundError:
        pytest.fail('Complete reference-curve exchange is not implemented')


def curve(quantity='V', values=(1.,3.,2.,4.)):
    return {'quantity':quantity, 'unit':'kN' if quantity=='V' else 'mm',
            'basis':'single_pile' if quantity=='V' else 'geometric',
            'pressureIdentity':None, 'projection':'element_end_limits',
            'samples':[{'z':0.,'value':values[0],'side':'below'},
                       {'z':1.,'value':values[1],'side':'above'},
                       {'z':1.,'value':values[2],'side':'below'},
                       {'z':2.,'value':values[3],'side':'above'}]}


def bundle():
    return {'schemaVersion':'pitguard.reference-curves/1', 'caseId':'PROTOCOL-ONLY', 'contractHash':None,
            'source':{'kind':'synthetic_protocol_fixture','sha256':'a'*64,'software':'test definitions','version':'1'},
            'datum':{'origin':'wall_top','positive':'down','offsetToWallTopM':0.,'evidence':'explicit test definition'},
            'forceBasis':{'kind':'single_pile','pileSpacingM':.9,'conversion':'identity'},
            'signs':{'u':'toward_excavation','M':'solver_positive_curvature','V':'solver_positive_section_shear',
                     'pressure':'compressive_positive','reaction':'opposes_positive_displacement'},
            'effectBasis':'characteristic', 'requiredStageIds':['S1'],
            'stages':[{'id':'S1','sourceStageId':'1','event':'excavate','domainM':[0.,2.], 'boundariesM':[1.],
                       'curves':[curve()], 'supports':[], 'design':None}],
            'reinforcement':[], 'tolerances':{'absolute':{'u':.01,'M':.01,'V':.01,'pressure_outside':.01,
                                                         'pressure_inside':.01,'N':.01,'support':.01,'rebar':.01},
                                             'relative':1e-4,'depthM':1e-8}}


def test_bundle_freeze_copies_and_preserves_shear_jump_limits():
    original = bundle()
    before = copy.deepcopy(original)
    frozen = api().freeze_curve_bundle(original)
    assert original == before
    assert frozen['stages'][0]['curves'][0]['samples'][1]['value'] == 3.
    assert frozen['stages'][0]['curves'][0]['samples'][2]['value'] == 2.
    frozen['stages'][0]['curves'][0]['samples'][1]['value'] = 0.
    with pytest.raises(ValueError, match='hash'):
        api().validate_curve_bundle(frozen)


@pytest.mark.parametrize('corruption', ['nan','duplicate','unknown_unit','extrema_only','unknown_stage','unexplained_line_basis'])
def test_invalid_import_is_rejected_not_normalized(corruption):
    data = bundle()
    c = data['stages'][0]['curves'][0]
    if corruption=='nan': c['samples'][0]['value'] = float('nan')
    elif corruption=='duplicate': c['samples'].append({'z':1.,'value':999,'side':'above'})
    elif corruption=='unknown_unit': c['unit'] = 'arbitrary'
    elif corruption=='extrema_only': c['samples'] = []; c['maximum'] = 4
    elif corruption=='unknown_stage': data['stages'][0]['id']='OTHER'
    else: c['basis']='per_metre'; c['unit']='kN/m'
    with pytest.raises(ValueError): api().freeze_curve_bundle(data)


def test_comparison_preserves_jumps_and_never_guesses_opposite_side():
    module=api()
    a=curve(); b=curve(values=(1.,3.,12.,4.))
    metric=module._compare_channel(a,b,[0.,2.],[1.],.01,0.,1e-8)
    assert metric['maxAbsoluteError'] == 10.
    assert metric['maxErrorAt'] == {'z':1.,'side':'below'}
    assert metric['status']=='outside_tolerance'
    b['samples']=[p for p in b['samples'] if p['side']!='below' or p['z']!=1.]
    metric=module._compare_channel(a,b,[0.,2.],[1.],.01,0.,1e-8)
    assert metric['status']=='incomplete'
    assert metric['missingSamples']


def test_no_extrapolation_and_no_interpolation_across_missing_layer_boundary():
    module=api(); a=curve(); b=curve()
    b['samples']=[b['samples'][0],b['samples'][-1]]
    metric=module._compare_channel(a,b,[0.,2.],[1.],.01,0.,1e-8)
    assert metric['status']=='incomplete'
    assert 1. in metric['missingBoundaryDepthsM']
    b['samples']=[{'z':.5,'value':1.,'side':'continuous'},{'z':1.5,'value':2.,'side':'continuous'}]
    metric=module._compare_channel(a,b,[0.,2.],[],.01,0.,1e-8)
    assert metric['status']=='incomplete'
    assert metric['coverageFraction']<1


def test_explicit_line_to_pile_conversion_is_positive_spacing_not_sign_fitting():
    module=api(); data=bundle()
    data['forceBasis']={'kind':'per_metre','pileSpacingM':.9,'conversion':'line_to_pile_multiply_spacing'}
    data['stages'][0]['curves'][0]['unit']='kN/m'
    data['stages'][0]['curves'][0]['basis']='per_metre'
    canonical=module._normalize_bundle(module.freeze_curve_bundle(data))
    assert canonical['stages'][0]['curves'][0]['samples'][1]['value']==pytest.approx(2.7)
    assert data['stages'][0]['curves'][0]['samples'][1]['value']==3.


def test_section_export_keeps_synthetic_origin_and_gauss_projection_limits():
    from app.section.service import analyze_section
    snapshot=freeze_section(verification_example())
    result=analyze_section(snapshot)
    exported=api().section_curve_bundle(snapshot,result)
    api().validate_curve_bundle(exported)
    assert exported['source']['kind']=='synthetic_protocol_fixture'
    assert len(exported['stages'])==6
    pressure=next(c for c in exported['stages'][1]['curves'] if c['quantity']=='pressure_outside')
    assert pressure['projection']=='gauss_weighted_nodal_lumping'
    assert pressure['pressureIdentity']['stress']=='total'
    assert min(p['z'] for p in pressure['samples'])>0
    assert exported['stages'][2]['supports'][0]['referenceDisplacementMm'] is not None


def test_synthetic_round_trip_and_missing_condition_hash_never_certify_original():
    from app.section.service import analyze_section
    from app.section.reference_contract import build_a3_reference_contract
    snapshot=freeze_section(verification_example()); result=analyze_section(snapshot)
    contract=build_a3_reference_contract(json.loads((ROOT/'examples/frozen-section/A3-3-reference-audit.json').read_text()))
    ref=api().section_curve_bundle(snapshot,result)
    comparison=api().compare_reference_curves(snapshot,result,contract,ref)
    assert comparison['status'] in {'incomplete','conditions_mismatch'}
    assert comparison['sameConditionsVerified'] is False
    assert 'reference_contract_hash_mismatch' in comparison['blockers']
    assert 'synthetic_or_digitized_reference' in comparison['blockers']
    assert comparison['coverage']['fullCurveAcceptance'] is False


def test_sign_mismatch_is_reported_without_silent_reversal():
    from app.section.service import analyze_section
    from app.section.reference_contract import build_a3_reference_contract
    snapshot=freeze_section(verification_example()); result=analyze_section(snapshot)
    contract=build_a3_reference_contract(json.loads((ROOT/'examples/frozen-section/A3-3-reference-audit.json').read_text()))
    ref=api().section_curve_bundle(snapshot,result); ref.pop('bundleHash')
    ref['signs']['M']='opposite_to_solver_positive_curvature'
    comparison=api().compare_reference_curves(snapshot,result,contract,api().freeze_curve_bundle(ref))
    assert 'sign_convention_mismatch:M' in comparison['blockers']
    assert not comparison['sameConditionsVerified']


def test_removed_support_keeps_installation_reference_and_axial_is_optional():
    from app.section.service import analyze_section
    from app.section.reference_contract import build_a3_reference_contract
    snapshot=freeze_section(verification_example()); result=analyze_section(snapshot)
    exported=api().section_curve_bundle(snapshot,result)
    removed=exported['stages'][5]['supports'][0]
    assert removed['status']=='removed'
    assert removed['referenceDisplacementMm']==pytest.approx(result['stages'][2]['supports'][0]['referenceDisplacementMm'])
    assert removed['reaction']['value']==0.
    contract=build_a3_reference_contract(json.loads((ROOT/'examples/frozen-section/A3-3-reference-audit.json').read_text()))
    comparison=api().compare_reference_curves(snapshot,result,contract,exported)
    active=next(s for s in comparison['supports'] if s['stageId']=='S3' and s['supportId']=='STRUT-1')
    assert active['status']=='within_tolerance'
    assert active['metrics']['memberAxialForceKn']['status']=='not_applicable'
    assert comparison['coverage']['displacementMomentShearComplete'] is True


def test_missing_stage_is_coverage_gap_not_an_implicit_unchanged_stage():
    from app.section.service import analyze_section
    from app.section.reference_contract import build_a3_reference_contract
    snapshot=freeze_section(verification_example()); result=analyze_section(snapshot)
    reference=api().section_curve_bundle(snapshot,result); reference.pop('bundleHash')
    reference['stages']=[s for s in reference['stages'] if s['id']!='S4']
    contract=build_a3_reference_contract(json.loads((ROOT/'examples/frozen-section/A3-3-reference-audit.json').read_text()))
    comparison=api().compare_reference_curves(snapshot,result,contract,api().freeze_curve_bundle(reference))
    assert comparison['coverage']['missingStages']==['S4']
    assert comparison['coverage']['displacementMomentShearComplete'] is False
    assert not comparison['coverage']['fullCurveAcceptance']


def test_coverage_uses_missing_length_not_dimensionless_depth_tolerance():
    a=curve(); b=curve()
    # A very long member missing 0.01m cannot be accepted by comparing a fraction
    # with a tolerance in metres.
    for item in (a,b):
        item['samples']=[{'z':.01,'side':'continuous','value':1.},
                         {'z':10000.,'side':'continuous','value':1.}]
    metric=api()._compare_channel(a,b,[0.,10000.],[],.01,0.,1e-5)
    assert metric['status']=='incomplete'
    assert metric['missingCoverageLengthM']==pytest.approx(.01)


def test_v2_interior_extrema_are_preserved_as_continuous_samples():
    from app.section.examples import verification_example_v2
    from app.section.service import analyze_section
    snapshot=freeze_section(verification_example_v2()); result=analyze_section(snapshot)
    originals=[(s['id'],p) for s in result['stages'] for p in s['points'] if p['side'] in {'within','interior'}]
    assert originals, 'Fixture must exercise actual Hermite interior extrema'
    exported=api().section_curve_bundle(snapshot,result)
    for stage_id,point in originals:
        stage=next(s for s in exported['stages'] if s['id']==stage_id)
        u=next(c for c in stage['curves'] if c['quantity']=='u')
        found=next(p for p in u['samples'] if p['z']==point['depthM'])
        assert found['side']=='continuous'
        assert found['value']==point['displacementMm']


def test_v2_total_combined_pressure_does_not_add_physical_pore_water_twice():
    from app.section.examples import verification_example_v2
    from app.section.service import analyze_section
    data=verification_example_v2()
    data['method_extensions']['layer_stress_modes'][1].update(mode='total_combined',parameter_basis='total')
    snapshot=freeze_section(data); result=analyze_section(snapshot)
    stage=result['stages'][3]
    selected=next(p for p in stage['soilStates'] if p['side']=='outside' and
                  p.get('pressureBasis')=='total' and p.get('waterPressureKpa',0)>0)
    peers=[p for p in stage['soilStates'] if p['side']=='outside' and p['depthM']==selected['depthM']]
    assert all(p['externalWaterKpa']==0 for p in peers)
    expected=sum(p['pressureKn'] for p in peers)/(.9*sum(p['weightM'] for p in peers))
    exported=api().section_curve_bundle(snapshot,result)
    curve=next(c for c in exported['stages'][3]['curves'] if c['quantity']=='pressure_outside')
    sample=next(p for p in curve['samples'] if p['z']==selected['depthM'])
    assert sample['value']==pytest.approx(expected)


def test_empty_original_template_has_no_reconstructed_samples_and_explicit_datum():
    from app.section.reference_contract import build_a3_reference_contract
    contract=build_a3_reference_contract(json.loads((ROOT/'examples/frozen-section/A3-3-reference-audit.json').read_text()))
    template=api().a3_curve_template(contract)
    assert template['requiredStageIds']==['S1','S2','S3','S4','S5']
    assert template['datum']['offsetToWallTopM']==-1.3
    assert template['forceBasis']['kind']=='unconfirmed'
    assert all(c['samples']==[] for s in template['stages'] for c in s['curves'])
    normalized=api()._normalize_bundle(template)
    assert normalized['stages'][0]['domainM']==pytest.approx([0.,6.6])
    assert template['stages'][0]['domainM']==[1.3,7.9]


def test_governing_envelope_retains_all_rounding_tied_stages():
    data=bundle()
    other=copy.deepcopy(data['stages'][0]);other['id']='S2'
    other['curves'][0]['samples'][-1]['value']=4.02
    data['stages'].append(other)
    summary=api()._governing(data,'V',.05)
    assert summary['maximum']==4.02
    assert summary['maximumStageIds']==['S1','S2']


def test_reinforcement_export_retains_tied_governing_stages():
    from app.section.service import analyze_section
    data=verification_example()
    data['stages'][-1].update(event='water_or_load',remove_support_ids=[])
    snapshot=freeze_section(data); result=analyze_section(snapshot)
    peak=max(s['maxAbsMomentKnM'] for s in result['stages'])
    tied=[s['id'] for s in result['stages'] if abs(s['maxAbsMomentKnM']-peak)<1e-8]
    assert len(tied)>1
    exported=api().section_curve_bundle(snapshot,result)
    assert exported['reinforcement'][0]['governingStageIds']==tied


@pytest.mark.parametrize('field,value', [('id',[]),('curves',[None]),('supports',[[]])])
def test_structurally_malformed_import_raises_validation_error(field,value):
    data=bundle();data['stages'][0][field]=value
    with pytest.raises(ValueError): api().freeze_curve_bundle(data)
