"""One-at-a-time, controlled physical hypotheses for Luoyu 3-3.

Report extrema are frozen external *diagnostic* values. Hypothesis changes are
never optimized against them; no automatic adoption or code qualification.
"""
from __future__ import annotations
from pathlib import Path
import json

from .luoyu_three_three import produce_luoyu_research_curves
from .normative_elastic_beam import run_luoyu_normative_beam_recheck
from .normative_stability import stability_portfolio
from ..analysis import canonical_hash
from ..normative.reference_review import luoyu_three_three

METHOD = 'pitguard.luoyu-3-3-physical-diagnostics/3.89.9'

# No private auto-fitting based on PDF. Every hypothetical parameter choice is
# a predeclared engineering WHAT-IF with source status 'unverified'.
TRIALS = (
  ('toe_pinned','toe_boundary','pinned','toe_support_hypothesis'),
  ('toe_fixed','toe_boundary','fixed','toe_support_hypothesis'),
  ('e_reduced_25gpa','elastic_modulus_mpa',25000.,'concrete_modulus_assumption'),
  ('e_increased_35gpa','elastic_modulus_mpa',35000.,'concrete_modulus_assumption'),
  ('ei_reduction_0_8','inertia_factor',.80,'flexural_cracking_reduction_hypothesis'),
  ('crown_half','crown_multiplier',.5,'crown_reaction_boundary_hypothesis'),
  ('crown_double','crown_multiplier',2.,'crown_reaction_boundary_hypothesis'),
  ('support_half','support_multiplier',.5,'inclined_strut_mapping_hypothesis'),
  ('support_double','support_multiplier',2.,'inclined_strut_mapping_hypothesis'),
  ('water_shallow','exterior_water_depth_m',.3,'water_head_hypothesis'),
  ('water_deep','exterior_water_depth_m',.8,'water_head_hypothesis'),
  ('surcharge_20kpa','adjacent_surcharge_kpa',20.,'adjacent_load_hypothesis'),
  ('surcharge_35kpa','adjacent_surcharge_kpa',35.,'adjacent_load_hypothesis'),
  ('fill_gamma_16','terrain_fill_gamma_kn_m3',16.,'slope_overburden_hypothesis'),
  ('fill_gamma_20','terrain_fill_gamma_kn_m3',20.,'slope_overburden_hypothesis'),
  ('saturated_gamma_alt','saturated_gammas_kn_m3',(19.,19.,17.,22.,23.),'soil_saturation_hypothesis'),
  ('terrain_missing_diagnostic','terrain_mode','omitted_diagnostic','excluded_load_negative_control'),
)


def _peaks(stages: list[dict]) -> dict:
    bystage = {}
    for stage in stages:
        bystage[stage['id']] = {
           'maximumDisplacementMm':stage.get('peakDisplacementMm'),
           'absoluteMomentPeakKnM':stage.get('peakMomentKnM'),
           'absoluteShearPeakKn':stage.get('peakShearKn'),
           'converged':stage['status']=='success',
           'localPassiveExceedanceCount':stage.get('localPassiveExceedanceCount'),
           'passiveResultantLimitSatisfied':stage.get('passiveResultantLimitSatisfied')}
    return bystage


def _trial(name: str, change: dict, basis: str, baseline_peaks:dict) -> dict:
    run = produce_luoyu_research_curves(mesh_max_m=.2,local_passive_cap=True,**change)
    peaks=_peaks(run['stages'])
    changed=any(abs(peaks[st][key]-baseline_peaks[st][key])>1e-8
        for st in ('S1','S2','S3','S4','S5')
        for key in ('maximumDisplacementMm','absoluteMomentPeakKnM','absoluteShearPeakKn')
        if peaks[st][key] is not None and baseline_peaks[st][key] is not None)
    return {'id':name,'group':basis,'modifiedFields':list(change),
      'unverifiedInputValues':change,'solverStatus':run['solverStatus'],
      'physicalConditionsIdenticalToFRWS':False,
      'stages':peaks,'candidateHash':run['discreteHash'],
      'anyStagePeakChanged':changed,
      'inactiveInfluenceExplanation':(
          'The retained single-wall model uses combined total-stress treatment at all soil depths below the pile top; a water datum change does not affect those 1-D response quantities. Hydraulic stability remains unresolved.'
          if not changed and 'exterior_water_depth_m' in change else None),
      'engineeringQualified':False}


def diagnostic_distance_only(rows: list[dict], original: dict) -> list[dict]:
    printed = {s['id']:s for s in original['stages']}
    result=[]
    for stage in rows:
        ref=printed[stage['id']]
        metric_pairs=(('peakDisplacementMm','displacementRangeMm'),
                      ('peakMomentKnM','momentRangeKnM'),
                      ('peakShearKn','shearRangeKn'))
        measures={}
        for key,report_key in metric_pairs:
            model=stage.get(key)
            printed_peak=max(abs(float(v)) for v in ref[report_key])
            measures[key]={'printedAbsPeak':printed_peak,'computedAbsPeak':model,
                'absoluteNumericGapDifferentConditions':abs(model-printed_peak) if model is not None else None,
                'isErrorMetric':False}
        result.append({'stageId':stage['id'],'measurements':measures})
    return result


def _compare_to_3898(current:dict, baseline:dict)->list[dict]:
    keys=('maximumDisplacementMm','absoluteMomentPeakKnM','absoluteShearPeakKn')
    result=[]
    for stage in ('S1','S2','S3','S4','S5'):
        measures={}
        for key in keys:
            before=baseline[stage][key]; after=current[stage][key]
            measures[key]={'old3898':before,'current3899':after,
               'delta':after-before if before is not None and after is not None else None,
               'changedNumericalMethod':True}
        result.append({'stageId':stage,'measurements':measures})
    return result


def run_luoyu_physics_review(*, original_report=None, trial_scope='quick',
                              include_baseline_curves=False)->dict:
    """Quick API (~7 solves) or full CLI (~18 solves); fixed engineering scenarios.

    Any report / baseline comparison is a diagnostic; no operation infers or
    optimizes original model conditions from printed maxima.
    """
    if trial_scope not in {'quick','full','none'}:
        raise ValueError('trial_scope must be quick, full or none')
    ref=luoyu_three_three()
    baseline=run_luoyu_normative_beam_recheck(
         mesh_max_m=.2, original_report=original_report,
         include_stage_curves=include_baseline_curves)
    underlying=produce_luoyu_research_curves(mesh_max_m=.2,local_passive_cap=True)
    # Baseline V3.89.8 had NO local cap, while V3.89.9 local yield cap is on.
    baseline_file=Path(__file__).resolve().parent.parent/'references'/'luoyu_3_3_v3898_baseline.json'
    frozen_old=json.loads(baseline_file.read_text(encoding='utf-8'))
    if frozen_old['schemaVersion']!='pitguard.luoyu-frozen-numerical-baseline/3.89.8':
        raise RuntimeError('Unrecognized frozen numerical regression baseline')
    oldpeaks=frozen_old['stages']; nowpeaks=_peaks(underlying['stages'])
    stability=stability_portfolio(ref['facts'],ref['stages'])
    balances=[{'stageId':s['stageId'],**(s.get('globalBalance') or {})}
              for s in baseline['stages']]
    balanced=all(abs(x.get('relativeForceResidual',float('inf')))<1e-7 and
                 abs(x.get('relativeMomentResidual',float('inf')))<1e-7 for x in balances)
    all_cases=[]
    if trial_scope=='quick':
        selected={'toe_pinned','toe_fixed','crown_half','support_half','water_shallow'}
    elif trial_scope=='full':selected={t[0] for t in TRIALS}
    else:selected=set()
    for name,field,value,group in TRIALS:
        if name not in selected:continue
        try:
            all_cases.append(_trial(name,{field:list(value) if isinstance(value,tuple) else value},group,nowpeaks))
        except (RuntimeError, ValueError) as exc:
            # Individual physical failure must be retained; do not silently
            # discard a candidate or replace it by the successful base.
            all_cases.append({'id':name,'group':group,'status':'trial_failed',
                'failure':str(exc),'engineeringQualified':False})
    output={'schemaVersion':METHOD,'project':ref['project'],'section':'3-3',
        'referencePdfSha256':baseline['source']['sourcePdfSha256'],
        'originalReportBytesVerified':baseline['source']['originalReportBytesVerified'],
        'trialScope':trial_scope,
        'previousVersionMethod':'V3.89.8 global passive resultant; local bound was not enforced',
        'frozenV3898Baseline':{'file':baseline_file.name,'schemaVersion':frozen_old['schemaVersion'],
            'stageIds':frozen_old['solverStageIds'],'isOriginalFRWS':False},
        'currentMethod':'V3.89.9 local passive-cap plus global resultant check',
        'old3898VsNew3899':_compare_to_3898(nowpeaks,oldpeaks),
        'fiveStageGlobalEquilibrium':{'solverForceAndMomentBalancePassed':balanced,
             'stageResiduals':balances,'independentSoilMethodQualified':False},
        'baseline':{
           'solverStatus':underlying['solverStatus'],
           'stages':nowpeaks,'nativeNumericSamplesAvailable':False,
           'reportExtremaComparison':diagnostic_distance_only(underlying['stages'],ref),
           'resultHash':underlying['discreteHash'],
           'reportedExtremaNotUsedForCalibration':True,
           'engineeringQualified':False},
        'sensitivity':{'sensitivityType':'one_at_a_time_unverified_physical_hypotheses',
          'trialCount':len(all_cases),'cases':all_cases,
          'doesNotRankByDistanceToPDF':True,
          'noOptimalParametersDeclared':True},
        'independentStabilityReview':stability,
        'existingIndependentBeamReference':baseline['numericalBenchmark'],
        'methods':{
           'beam': 'JGJ120-2012/4.1.1-4.1.8',
           'localPassiveBound': 'JGJ120-2012/4.1.4-2',
           'stability': 'JGJ120-2012/4.2.1-4.2.7',
           'originalDesignLocalCode': 'DB33/T1096-2014 (2020 original report)'},
        'engineeringQualified':False,'formalConstructionIssue':False,
        'limitations':[
          'Site soil strength, borehole logs, calibrated saturated unit weights and pore-pressure boundaries are missing',
          'No opposite-wall displacement compatibility model or verified inclined-strut elastic conversion',
          'Terrain model uses integrated elastic vertical surcharge; original slope wedge method unverified',
          'The original 2020 software also cites Zhejiang DB33/T1096-2014; national code checks are not a substitute',
          '4.2 stability ledger retains unverified or absent safety mechanisms, full stability not passed',
          'All report peak comparisons are different-condition diagnostics; pointwise FRWS errors unavailable']}
    output['auditHash']=canonical_hash(output)
    return output
