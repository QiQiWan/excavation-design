"""3.87.28 independent P1-P3 acceptance intake regression with adverse cases."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from zipfile import ZipFile, ZIP_DEFLATED
import csv
import json
from math import isclose, log, pi
import pytest
from pydantic import ValidationError

from app.engineering.single_braced_pile.assurance.field_hydrology import (
    ObservationReviewInput,reconcile_observations,
    PumpingTestInterpretationInput,interpret_thiem)
from app.engineering.single_braced_pile.assurance.circular_review import (
    CrackCrosscheckInput,crosscheck_crack_reports,
    EulerSecondOrderScreenInput,screen_euler_second_order)
from app.engineering.single_braced_pile.real_project.native_numeric_import import (
    import_native_numeric,compare_native_with_candidate,make_blank_numeric_template,
    DATA_FILES,CURVE_COLUMNS,PRESSURE_COLUMNS,BRACE_COLUMNS)
from app.engineering.single_braced_pile.assurance.dossier_verify import verify_review_package

H='a'*64


def observation(side, minute, head, well='A',stage='S1',quality='valid',day=1):
    return {'project_id':'case','section_id':'3-3','stage_id':stage,'well_id':well,
            'side':side,'observed_at':(datetime(2026,10,day,8,tzinfo=timezone.utc)+timedelta(minutes=minute)).isoformat(),
            'head_m':head,'source_sha256':H,'source_row':minute+1,'quality':quality}


def water_request(rows, **kw):
    return ObservationReviewInput.model_validate({'project_id':'case','section_id':'3-3','observations':rows,**kw})


def pump_payload(**kw):
    basic=dict(project_id='case',section_id='3-3',test_id='pump-A',discharge_m3_s=.002,
               near_radius_m=5,far_radius_m=20,near_drawdown_m=1.2,far_drawdown_m=.6,
               confined_aquifer_thickness_m=8,confined=True,steady_state=True,
               homogeneous_isotropic=True,fully_penetrating_well=True,
               well_loss_negligible=True,source_sha256=H)
    basic.update(kw)
    return PumpingTestInterpretationInput.model_validate(basic)


def crack_payload(**kw):
    base=dict(result_hash='b'*64,snapshot_hash=H,combination_name='SLS-1',
              stage_id='S3',depth_m=3.25,side='above',crack_limit_mm=.25,
              absolute_tolerance_mm=.02,relative_tolerance=.10,
              first={'reviewer_id':'checker-A','method_id':'external_method_1',
                     'method_scope':'independently declared circular pile SLS',
                     'source_sha256':'c'*64,'crack_width_mm':.12},
              second={'reviewer_id':'checker-B','method_id':'external_method_2',
                      'method_scope':'other independent circular pile calculation',
                      'source_sha256':'d'*64,'crack_width_mm':.125})
    base.update(kw)
    return CrackCrosscheckInput.model_validate(base)


def frozen_result():
    return {'resultHash':'b'*64,'snapshotHash':H,
            'snapshot':{'crack_control':{'width_limit_mm':.25}},
            'normativeCoverage':{'requiredChecks':{'concrete_circular_crack_control':{
                'result':{'cases':[{'stageId':'S3','combinationName':'SLS-1',
                          'depthM':3.25,'side':'above'}]}}}}}


def valid_zip(*,active_brace=False,method=True):
    from app.engineering.single_braced_pile.real_project.native_numeric_import import STAGES
    def emit(cols,rows):
        buffer=StringIO(newline='');w=csv.writer(buffer);w.writerow(cols);w.writerows(rows)
        return buffer.getvalue().encode('utf-8-sig')
    datasets={
        'stages.json':json.dumps([{'id':sid,'activeSupportIds':['B1'] if active_brace else []}
                                  for sid in STAGES]).encode(),
        'curve.csv':emit(CURVE_COLUMNS,[(sid,0,'above',1.,2.,3.) for sid in STAGES]+
                                    [(sid,1,'below',1.5,2.2,3.5) for sid in STAGES]),
        'pressure.csv':emit(PRESSURE_COLUMNS,[(sid,0,side,'total',100.) for sid in STAGES for side in ('inside','outside')]),
        'brace.csv':emit(BRACE_COLUMNS,[(sid,'B1',10.5,1.2) for sid in STAGES] if active_brace else [])}
    meta={'schemaVersion':'pitguard.luoyu-native-numeric/1','projectId':'native-case',
          'sectionId':'3-3','depthDatum':'original_ground_down_m','forceBasis':'single_pile',
          'displacementUnit':'mm','momentUnit':'kN.m','shearUnit':'kN','pressureUnit':'kPa',
          'reactionUnit':'kN/pile','referenceUnit':'mm','methodAndBoundaryConfirmed':method,
          'fileDigests':{name:sha256(datasets[name]).hexdigest() for name in DATA_FILES}}
    datasets['metadata.json']=json.dumps(meta).encode()
    return _zip(datasets)


def _zip(data):
    buffer=BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as z:
        for name,content in sorted(data.items()):z.writestr(name,content)
    return buffer.getvalue()


def edit_zip(blob, filename, edit, *,update_hash=True):
    with ZipFile(BytesIO(blob)) as z:data={x:z.read(x) for x in z.namelist()}
    data[filename]=edit(data[filename])
    if filename!='metadata.json' and update_hash:
        meta=json.loads(data['metadata.json'])
        meta['fileDigests'][filename]=sha256(data[filename]).hexdigest()
        data['metadata.json']=json.dumps(meta).encode()
    return _zip(data)


def test_observation_pairing_same_stage_and_one_to_one():
    req=water_request([observation('inside',0,10),observation('outside',4,12),
                       observation('outside',8,18),observation('inside',12,11)])
    r=reconcile_observations(req)
    assert r['pairedCount']==2
    assert r['maxObservedPairedDeltaHeadM']==7
    assert r['formalDesignReady'] is False


def test_observation_stage_boundary_not_blended():
    req=water_request([observation('inside',0,2,stage='S1'),
                       observation('outside',1,8,stage='S2')])
    r=reconcile_observations(req)
    assert r['pairedCount']==0
    assert r['assessmentStatus']=='insufficient_synchronous_observations'


def test_observation_time_tolerance_rejects_stale_values():
    req=water_request([observation('inside',0,2),observation('outside',100,9)],max_pair_gap_minutes=10)
    assert reconcile_observations(req)['pairedCount']==0


def test_observation_multiaquifer_well_requires_selection():
    req=water_request([observation('inside',0,2,'A'),observation('inside',2,1,'B'),
                       observation('outside',0,8,'C')])
    assert reconcile_observations(req)['assessmentStatus']=='well_selection_required'


def test_observation_explicit_well_selection():
    req=water_request([observation('inside',0,2,'A'),observation('inside',2,1,'B'),
                       observation('outside',0,8,'C')],inside_well_id='B',outside_well_id='C')
    assert reconcile_observations(req)['pairedCount']==1


def test_observation_with_timezone_offset():
    a=observation('inside',0,1)
    b=observation('outside',0,2)
    b['observed_at']='2026-10-01T16:00:00+08:00'
    assert reconcile_observations(water_request([a,b]))['pairedCount']==1


def test_observation_exclusion_is_not_silently_used():
    r=reconcile_observations(water_request([observation('inside',0,2,quality='excluded'),
                                            observation('outside',0,8)]))
    assert r['excludedObservationCount']==1 and r['pairedCount']==0


@pytest.mark.parametrize('change',[
    {'observed_at':'2026-10-01T08:00:00'}, {'head_m':float('nan')}, {'head_m':float('inf')},
    {'source_sha256':'0'}, {'source_row':0}, {'stage_id':''}, {'well_id':''},
])
def test_observation_input_negative_validation(change):
    data=[observation('inside',0,2),observation('outside',1,8)]
    data[0].update(change)
    with pytest.raises(ValidationError): water_request(data)


def test_observation_duplicate_time_is_rejected():
    with pytest.raises(ValidationError):
        water_request([observation('inside',0,2),observation('inside',0,3),observation('outside',1,4)])


def test_pumping_thiem_formula_with_units():
    r=interpret_thiem(pump_payload())
    t=.002*log(20/5)/(2*pi*.6)
    assert isclose(r['transmissivityM2PerS'],t,rel_tol=1e-12)
    assert isclose(r['hydraulicConductivityMPerS'],t/8,rel_tol=1e-12)
    assert r['verticalHydraulicConductivityIdentified'] is False


@pytest.mark.parametrize('flag', ['confined','steady_state','homogeneous_isotropic','fully_penetrating_well','well_loss_negligible'])
def test_pumping_inapplicable_assumption_fails_closed(flag):
    r=interpret_thiem(pump_payload(**{flag:False}))
    assert r['assessmentStatus']=='outside_Thiem_method_scope'
    assert r['hydraulicConductivityMPerS'] is None


def test_pumping_nonphysical_drawdown_fails_closed():
    r=interpret_thiem(pump_payload(near_drawdown_m=.3,far_drawdown_m=.6))
    assert 'near_drawdown_exceeds_far_drawdown' in r['outsideScopeReasons']


@pytest.mark.parametrize('kw',[{'near_radius_m':20,'far_radius_m':5},{'discharge_m3_s':0},
                              {'confined_aquifer_thickness_m':0},{'near_drawdown_m':float('nan')}])
def test_pumping_bad_inputs_rejected(kw):
    with pytest.raises(ValidationError):pump_payload(**kw)


def test_crack_numeric_agreement_does_not_grant_method_approval():
    r=crosscheck_crack_reports(crack_payload(),frozen_result())
    assert r['assessmentStatus']=='numerically_agreed_method_unqualified'
    assert r['formalDesignReady'] is False


@pytest.mark.parametrize('width,status',[(.22,'numerical_disagreement'),(.27,'exceeds_declared_limit')])
def test_crack_disagreement_and_limit_fail(width,status):
    second=crack_payload().second.model_dump()
    second['crack_width_mm']=width
    r=crosscheck_crack_reports(crack_payload(second=second),frozen_result())
    assert r['assessmentStatus']==status


@pytest.mark.parametrize('change',[
 {'snapshot_hash':'0'*64},{'result_hash':'0'*64},{'combination_name':'OTHER'},
 {'depth_m':3.26},{'crack_limit_mm':.3}])
def test_crack_wrong_snapshot_or_sls_station_rejected(change):
    with pytest.raises(ValueError):crosscheck_crack_reports(crack_payload(**change),frozen_result())


def test_crack_same_reviewer_or_source_rejected():
    payload=crack_payload(); second=payload.second.model_dump()
    second['reviewer_id']=payload.first.reviewer_id
    with pytest.raises(ValidationError):crack_payload(second=second)
    second['reviewer_id']='other';second['source_sha256']=payload.first.source_sha256
    with pytest.raises(ValidationError):crack_payload(second=second)


def test_euler_reference_is_not_structural_second_order_pass():
    input=EulerSecondOrderScreenInput(project_id='case',section_id='3-3',axial_compression_kn=100,
           flexural_rigidity_kn_m2=1000,effective_length_m=10,
           effective_length_basis='manual theoretical column length',governing_stage_id='S2')
    r=screen_euler_second_order(input)
    assert isclose(r['criticalEulerCompressionKn'],pi*pi*10)
    assert r['assessmentStatus']=='at_or_above_euler_instability'
    assert r['normativeSecondOrderDesignComplete'] is False


@pytest.mark.parametrize('kw',[{'axial_compression_kn':-1},{'flexural_rigidity_kn_m2':0},
                              {'effective_length_m':0},{'effective_length_basis':'unknown'}])
def test_euler_bad_input_rejected(kw):
    fields=dict(project_id='case',section_id='3-3',axial_compression_kn=100,
         flexural_rigidity_kn_m2=1000,effective_length_m=10,
         effective_length_basis='assumed theoretical column model',governing_stage_id='S2')
    fields.update(kw)
    with pytest.raises(ValidationError):EulerSecondOrderScreenInput(**fields)


def test_native_blank_template_is_deliberately_not_valid_reference():
    blob=make_blank_numeric_template()
    with pytest.raises(ValueError,match='All numeric channels'):
        import_native_numeric(blob)


def test_native_valid_five_stage_all_channels():
    r=import_native_numeric(valid_zip(active_brace=True))
    assert list(r['stationCounts'])==['S1','S2','S3','S4','S5']
    assert r['stationCounts']['S3']['brace']==1
    assert r['originalSourceBytesIndependentlyAuthenticated'] is False
    assert r['formalSameConditionVerification'] is False


def test_native_zero_support_explicit_empty_reactions():
    r=import_native_numeric(valid_zip())
    assert r['stationCounts']['S1']['brace']==0
    assert r['nativeReference']['stages'][0]['activeSupportIds']==[]


def test_native_comparison_matched_but_unqualified():
    blob=valid_zip()
    ref=import_native_numeric(blob)['nativeReference']
    cand={'stages':ref['stages']}
    r=compare_native_with_candidate(blob,cand)
    assert r['assessmentStatus']=='all_channels_compared_source_peer_review_pending'
    assert r['formalSameConditionVerification'] is False
    assert r['comparison']['stages'][0]['channelRMSE']['momentKnM']==0


def test_native_comparison_no_method_declaration_blocks_numeric_promotion():
    blob=valid_zip(method=False)
    ref=import_native_numeric(blob)['nativeReference']
    r=compare_native_with_candidate(blob,{'stages':ref['stages']})
    assert r['assessmentStatus']=='blocked'
    assert 'verified_physical_method_and_boundary' in r['comparison']['requiredEvidence']


def test_native_original_pressure_both_sides_required():
    def mutate(data):
        text=data.decode('utf-8-sig')
        return '\n'.join(line for line in text.splitlines() if ',inside,' not in line).encode()
    blob=edit_zip(valid_zip(),'pressure.csv',mutate)
    with pytest.raises(ValueError,match='Both pressure sides'):import_native_numeric(blob)


def test_native_sha_mismatch_rejected():
    blob=edit_zip(valid_zip(),'curve.csv',lambda x:x+b'\n',update_hash=False)
    with pytest.raises(ValueError,match='hash mismatch'):import_native_numeric(blob)


def test_native_missing_stage_rejected():
    blob=edit_zip(valid_zip(),'stages.json',lambda d:json.dumps(json.loads(d)[:-1]).encode())
    with pytest.raises(ValueError,match='stages.json'):import_native_numeric(blob)


def test_native_wrong_datum_rejected():
    def bad(data):
        x=json.loads(data);x['depthDatum']='pile_top_down_m';return json.dumps(x).encode()
    blob=edit_zip(valid_zip(),'metadata.json',bad)
    with pytest.raises(ValueError,match='unit/datum'):import_native_numeric(blob)


def test_native_nonnumeric_rejected():
    blob=edit_zip(valid_zip(),'curve.csv',lambda x:x.replace(b',1.0,2.0,3.0',b',nan,2.0,3.0'))
    with pytest.raises(ValueError,match='Invalid numeric'):import_native_numeric(blob)


def test_native_reaction_missing_for_active_support_rejected():
    blob=valid_zip(active_brace=True)
    blob=edit_zip(blob,'brace.csv',lambda x:x.replace(b'S5,B1,10.5,1.2',b''))
    with pytest.raises(ValueError,match='support.*reaction'):import_native_numeric(blob)


def test_native_archive_extra_file_rejected():
    with ZipFile(BytesIO(valid_zip())) as z:files={x:z.read(x) for x in z.namelist()}
    files['evil.txt']=b'extra'
    with pytest.raises(ValueError,match='exactly'):import_native_numeric(_zip(files))


def test_native_corrupted_zip_rejected():
    with pytest.raises(ValueError):import_native_numeric(b'fake zip content')


def test_native_duplicate_json_keys_rejected():
    blob=edit_zip(valid_zip(),'stages.json',lambda b:b.replace(b'"id": "S1"',b'"id": "S1", "id": "S1"'))
    with pytest.raises(ValueError,match='duplicate JSON key'):import_native_numeric(blob)


def test_native_zip_path_traversal_rejected():
    with ZipFile(BytesIO(valid_zip())) as z: files={x:z.read(x) for x in z.namelist()}
    files['../escape.csv']=files.pop('brace.csv')
    with pytest.raises(ValueError):import_native_numeric(_zip(files))


def test_dossier_rejects_non_zip():
    with pytest.raises(ValueError):verify_review_package(b'not a reviewed dossier')
