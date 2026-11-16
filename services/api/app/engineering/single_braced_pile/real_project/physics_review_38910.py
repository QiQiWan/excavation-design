"""V3.89.10: five-stage evidence-led soil, rock, brace and stability review.

All numerical candidates are declared counterfactual scenarios. The 2020 PDF
printed extrema are never used as inverse-fitting objectives or as engineering
qualification. This service is read-only and can be run without FRWS arrays.
"""
from __future__ import annotations
from .luoyu_three_three import produce_luoyu_research_curves
from .normative_actions_38910 import load_comparison, METHOD as LOAD_METHOD
from .stability_38910 import extended_stability_review,SCHEMA as STABILITY_METHOD
from .normative_elastic_beam import run_luoyu_normative_beam_recheck
from ..normative.reference_review import luoyu_three_three
from ..analysis import canonical_hash

SCHEMA='pitguard.luoyu-3-3-physical-normative-review/3.89.10'


def _peaks(model:dict,reference:dict) -> dict:
    stages={s['id']:s for s in reference['stages']}
    result=[]
    for row in model['stages']:
        sid=row['id']
        ref=stages[sid]
        item={'stageId':sid,'calculationStatus':row['status'],'sourcePdfPhysicalPage':33 if sid in {'S1','S2'} else 34}
        for key,reference_key,unit in (
           ('peakDisplacementMm','displacementRangeMm','mm'),
           ('peakMomentKnM','momentRangeKnM','kNm'),
           ('peakShearKn','shearRangeKn','kN')):
            value=row.get(key)
            reported=max(abs(float(x)) for x in ref[reference_key])
            item[key]={'computed':value,'reportPrintedAbsPeak':reported,
                'absoluteNumericGap':(abs(value-reported) if value is not None else None),
                'unit':unit,'sameConditionError':False,'usedForOptimization':False}
        result.append(item)
    return {'stages':result,'reportNativeDepthSamplesPresent':False}


def _rock_segments(reference):
    f=reference['facts'];toe=f['pileTopOriginalGroundDepthM']+f['pileLengthM']
    rows=[];depth=0.
    for layer in f['soilLayersOriginalGround']:
        bottom=depth+layer['thicknessM']
        if '风化岩' in layer['name']:
            rows.append({'name':layer['name'],'pileIntersectionLengthM':max(0.,min(bottom,toe)-depth),
                'source':'2020 design report stratigraphic table, unconfirmed borehole',
                'designRockModulusMeasured':False})
        depth=bottom
    return {'pileToeOriginalGroundDepthM':toe,'rockIntersections':rows,
        'evidence':'design report table only',
        'toeTransferStiffnessDeterminedFromRockName':False,
        'rockSocketStiffnessApproved':False}


def run_luoyu_38910_review(*,original_report:dict|None=None,
                            scope:str='quick',include_stage_curves:bool=False)->dict:
    if scope not in {'none','quick','full'}:
        raise ValueError('Only none / quick / full fixed research scopes are permitted')
    original=luoyu_three_three()
    # Validate the submitted PDF report identity using the existing immutable
    # PDF evidence protocol; never trust a user-provided hash alone.
    verified=run_luoyu_normative_beam_recheck(
        original_report=original_report,mesh_max_m=.2,include_stage_curves=False)
    source=verified['source']
    kwargs={'mesh_max_m':.2,'local_passive_cap':True}
    baseline=produce_luoyu_research_curves(**kwargs)
    if baseline['solverStatus']!='success':
        raise RuntimeError('Frozen staged normative beam failed; no hypothetical output permitted')
    conditions={
      'adjacent_347':{'adjacent_load_mode':'jgj120_3_4_7'},
      'slope_348_zero_cohesion_hypothesis':{
          'terrain_mode':'jgj120_3_4_8','terrain_cohesion_override_kpa':0.},
      'rock_socket_linear_hypothesis':{
          'toe_elastic_horizontal_kn_m':20000.,'toe_elastic_rotation_knm_rad':2000.},
      'inclined_two_wall_movement_hypothesis':{
          'brace_angle_deg':45.,'brace_axial_stiffness_kn_m':100000.,
          'opposite_wall_stiffness_kn_m':40000.,
          'opposite_wall_stage_free_displacements_m':{
              'S2':0.,'S3':.0003,'S4':.0002,'S5':.0001}},
     }
    ids=() if scope=='none' else (('adjacent_347','slope_348_zero_cohesion_hypothesis')
                                  if scope=='quick' else tuple(conditions))
    trials=[]
    for identity in ids:
        changes=conditions[identity]
        try:
            current=produce_luoyu_research_curves(**kwargs,**changes)
            trials.append({'id':identity,'inputs':changes,
                'solverStatus':current['solverStatus'],
                'resultIdentity':current['discreteHash'],
                'comparisons':_peaks(current,original),
                'engineeredParameters':False,'source': 'fixed explicit hypothetical scenario',
                'engineeringQualified':False})
        except (ValueError,RuntimeError) as exc:
            trials.append({'id':identity,'inputs':changes,'solverStatus':'failed',
                'failureReason':str(exc),'engineeringQualified':False})
    levels=[.25,.8,1.7,2.,3.,4.5,6.5]
    load_report=load_comparison(original,levels,slope_cohesion_kpa=0.)
    # Evaluate the same formula with original c=10 kPa, never silently replace
    # the original filling soil by a zero-cohesion hypothesis.
    actual_declared=load_comparison(original,levels,slope_cohesion_kpa=None)
    hydro=extended_stability_review(original['facts'],original['stages'],search_budget=60)
    flags=['not_same_boundary_as_original_FRWS',
      'report_peak_comparisons_are_numeric_diagnostics_not_verified_errors',
      'original_fill_cohesion_makes_3_4_8_tensile_cutout_condition_unmet',
      'rock_socket_stiffness_unconfirmed_without_site_rock_tests',
      'inclined_brace_and_opposite_wall_history_are_synthetic_what_if_values',
      '4_2_3_overall_slip_is_supplementary_for_a_one_strut_case',
      '4_2_5_soft_bottom_pivot_needs_verified_fixed_pivot_slices',
      'appendix_C_hydraulic_mechanism_inputs_and_aquifer_evidence_missing',
      'Boussinesq_terrain_and_JGJ_slope_are_alternate_models_no_double_counting',
      'local_exceedance_of_passive_springs_checked_separately_from_stability']
    result={'schemaVersion':SCHEMA,'project':original['project'],'section':original['section'],
         'source':source,'originalReportBytesVerified':source['originalReportBytesVerified'],
         'reportPdfSha256':source['sourcePdfSha256'],
         'caseScope':'standard_6.6m_pile_only_short_5.1m_case_excluded',
         'baseline':{'resultIdentity':baseline['discreteHash'],
             'comparisons':_peaks(baseline,original),'originalExtremaUsedForCalibration':False,
             'solverStatus':baseline['solverStatus'],
             'elasticToeWasInventedFromRockName':False},
         'slopeAndSurcharge':{'method':LOAD_METHOD,'reportedUpperSoilCohesionKpa':10.,
             'declaredConditionMethod':actual_declared,
             'explicitZeroCohesionSensitivityOnly':load_report},
         'rockSocket':_rock_segments(original),
         'coupledWallSupportScope':'conditional two-boundary reduction, not a true three-dimensional frame',
         'scenarios':trials,'stability':hydro,
         'normativeMethodEvidence':{
             'terrain':'JGJ120-2012/3.4.7/3.4.8',
             'strut':'JGJ120-2012/4.1.8-4.1.10',
             'overallSlide':'JGJ120-2012/4.2.3 supplemental research due to support-type scope',
             'softBottom':'JGJ120-2012/4.2.5',
             'hydraulic':'JGJ120-2012/4.2.6 and Appendix C'},
         'hardBlockers':flags,'fullStabilityQualified':False,
         'engineeringQualified':False,'formalConstructionIssue':False}
    if include_stage_curves:
        result['baseline']['calculationCurves']={s['id']:s.get('curve') for s in baseline['stages']}
    result['auditHash']=canonical_hash(result)
    return result
