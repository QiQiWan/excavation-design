"""V3.89.11 evidence-aware, reproducible Luoyu 3-3 review.

The fixed 2020 report has reported design geometry. It does NOT furnish
survey-measured geometry, instrumented pile/rock springs, opposite-wall staged
response or in-situ hydraulic heads. Uploaded bytes are authenticated but
unreviewed until a qualified person verifies their engineering provenance.
"""
from __future__ import annotations
from .field_evidence_38911 import decode_evidence_zip, ROLES,SCHEMA as EVIDENCE_SCHEMA
from .field_methods_38911 import (report_geometry_method,rock_socket_method,
        brace_two_wall_method,groundwater_stage_method,fixed_pivot_425_method)
from .luoyu_three_three import produce_luoyu_research_curves
from .physics_review_38910 import _peaks
from .normative_elastic_beam import run_luoyu_normative_beam_recheck
from ..normative.reference_review import luoyu_three_three
from ..analysis import canonical_hash

SCHEMA='pitguard.luoyu-3-3-sourced-physical-stability-review/3.89.11'


def run_luoyu_38911_review(*,evidence_zip:bytes|None=None,
         original_report:dict|None=None,scope:str='single',include_stage_curves:bool=False)->dict:
    if scope not in {'none','single','joint'}:
        raise ValueError('Invalid bounded review scope')
    ref=luoyu_three_three()
    report=run_luoyu_normative_beam_recheck(original_report=original_report,
            mesh_max_m=.2,include_stage_curves=False)
    evidence=decode_evidence_zip(evidence_zip)
    data={role:evidence['roles'][role]['data'] if role in evidence['roles'] else None
        for role in ROLES}
    geom=report_geometry_method(ref,data['site_geometry'])
    rock=rock_socket_method(ref,data['rock_socket_tests'])
    brace=brace_two_wall_method(ref,data['opposite_wall_response'])
    hydro=groundwater_stage_method(ref,data['groundwater_investigation'])
    pivot=fixed_pivot_425_method(ref,data['fixed_pivot_slip_geometry'])
    baseline=produce_luoyu_research_curves(mesh_max_m=.2,local_passive_cap=True)
    if baseline['solverStatus']!='success':
        raise RuntimeError('Original reproducible five-stage research reference did not converge')
    comparisons=_peaks(baseline,ref)
    scenarios=[]
    if scope!='none':
        candidates={}
        if data['site_geometry'] is not None:
            d=data['site_geometry'];q=d['surcharge']
            candidates['surveyed_geometry_one_factor']={
                'terrain_profile_points_m':d['profilePointsM'],
                'terrain_fill_gamma_kn_m3':d['fillGammaKnM3'],
                'adjacent_rectangle_geometry_m':{k:q[k] for k in
                       ('nearM','widthM','alongStartM','alongEndM')},
                'adjacent_surcharge_kpa':q['pressureKpa']}
        if rock.get('candidateToeElasticBoundary'):
            t=rock['candidateToeElasticBoundary']
            candidates['rock_toe_one_factor']={
                'toe_elastic_horizontal_kn_m':t['horizontalKnPerM'],
                'toe_elastic_rotation_knm_rad':t['rotationKnMPerRad']}
        if brace.get('stageInputs'):
            candidates['two_wall_stage_one_factor']=brace['stageInputs']
        if scope=='joint' and len(candidates)>=2:
            combined={}
            for v in candidates.values():combined.update(v)
            candidates['joint_uploaded_boundary_hypothesis']=combined
        for name,kwargs in candidates.items():
            try:
                calculation=produce_luoyu_research_curves(mesh_max_m=.2,
                          local_passive_cap=True,**kwargs)
                outcome={'status':calculation['solverStatus'],
                    'resultIdentity':calculation['discreteHash'],
                    'conditionalComparisons':_peaks(calculation,ref),
                    'sameConditionsAsOriginal':False,'inputFields':sorted(kwargs),
                    'engineeringQualified':False}
                if include_stage_curves:
                    outcome['stageCurves']={s['id']:s.get('curve') for s in calculation['stages']}
            except (ValueError,RuntimeError) as exc:
                outcome={'status':'failed_research_hypothesis','reason':str(exc),
                    'engineeringQualified':False}
            scenarios.append({'candidateId':name,**outcome})
    hard=[]
    for role in ROLES:
        if role in evidence['blockedRoles']:
            hard.append('missing_source_role:'+role)
        elif evidence['roles'][role]['sourceQuality']=='synthetic_test':
            hard.append('synthetic_test_source_not_field_evidence:'+role)
        else:
            hard.append('source_bytes_valid_but_engineering_authority_not_verified:'+role)
        if role in evidence['roles'] and evidence['documents'][role].get('originalByteAttachmentCount',0)==0:
            hard.append('interpreted_json_only_without_corresponding_original_field_file:'+role)
    if not report['source']['originalReportBytesVerified']:
        hard.append('2020_original_pdf_bytes_not_supplied_in_this_run')
    hard.extend([
      '2020_design_standard_DB33_T1096_2014_must_be_distinguished_from_JGJ120_2012_reference',
      'original_FRWS_native_five_stage_arrays_not_available_for_pointwise_error',
      'reported_K_32_3_MN_per_m2_and_axial_EA_over_L_must_not_be_added_as_parallel_supports',
      'site_groundwater_seepage_and_strength_method_require_qualified_review',
      'node_and_local_material_capacity_checks_out_of_scope_of_this_research_model'])
    result={'schemaVersion':SCHEMA,'project':ref['project'],'section':ref['section'],
       'caseVersion':'standard_pile_6.6m_only','reportedDesignBasis':'DB33/T 1096-2014 stated on PDF physical page 33',
       'independentReferenceMethod':'JGJ 120-2012; elastic-beam physical reference, conditional',
       'source':report['source'],'sourceRegistry':{k:v for k,v in evidence.items() if k!='roles'},
       'inputSourceQualities':{r:evidence['roles'][r]['sourceQuality'] for r in evidence['roles']},
       'terrainAndSurcharge':geom,'rockSocket':rock,'twoWallBrace':brace,
       'groundwaterStaged':hydro,'softSoilFixedPivot':pivot,
       'baseline':{'resultIdentity':baseline['discreteHash'],
           'solverStatus':baseline['solverStatus'],
           'comparisons':comparisons,
           'baselineUsesOnlyHistoricalReportAndResearchHypotheses':True},
       'conditionalRecalculations':scenarios,
       'hardBlockers':hard,'sourceBytesAuthenticatedIsNotProfessionalReview':True,
       'gapsAreNotSameBoundaryModelErrors':True,
       'engineeringQualified':False,'formalConstructionIssue':False}
    if include_stage_curves:
        result['baseline']['stageCurves']={s['id']:s.get('curve') for s in baseline['stages']}
    result['auditHash']=canonical_hash(result)
    return result
