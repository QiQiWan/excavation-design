"""V3.88.2 A5: auditable read-only check registry for a *sealed* one-strut result.

This adapter reports what an existing solver actually checked. It does not
recompute code equations, infer source authenticity, or convert a screening into
normative PASS. Unknown/partial check states remain review blockers.
"""
from __future__ import annotations
from hashlib import sha256
from math import isfinite
from typing import Any
from .store import encode

# Coverage of this *existing single-strut solver*, not all checks that may
# apply to a real excavation. Required entries disappearing is a regression.
SUPPORTED_SOLVER_CHECK_IDS=frozenset({
 '4.2.2_embedment','4.2.3_global_sliding','4.2.4_basal_heave',
 '4.2.5_soft_soil_heave','4.2.6_groundwater','4.2.7_minimum_embedment',
 '3.1_load_combinations','construction_adverse_stability','4.9_internal_brace',
 'concrete_circular_pile','concrete_circular_matched_nm_v',
 'concrete_circular_crack_control',
 'concrete_circular_independent_nm','concrete_circular_independent_shear',
 'concrete_circular_width_reference','concrete_circular_cage_construction',
})
PASS={'pass','passed'}
FAIL={'fail','failed','engineering_fail','fail_screening'}
NOT_APPLICABLE={'not_applicable'}
KNOWN_INCOMPLETE={'missing_input','not_calculated','outside_method_scope','review_required',
    'calculated_scope_limited','screening_only','not_submitted','blocked',
    'not_generally_mandated_for_supported_type','incomplete','not_verified',
    'unverified','pending_review','unknown',
    'solver_failure','invalid_adverse_input','calculated','numerical_screening_only',
    'width_method_required','pass_limited_tension_check',
    'not_required_for_this_class_combination','geometry_checked_capacity_pending'}


def _finite(value: Any) -> bool:
    return type(value) in (int,float) and isfinite(value)


def _assessment(raw: Any)->str:
    status=str(raw or '').lower()
    if status in PASS:return 'pass'
    if status in FAIL:return 'fail'
    if status in NOT_APPLICABLE:return 'not_applicable_unverified_scope'
    if status in KNOWN_INCOMPLETE:return 'incomplete'
    return 'unrecognized_status'


def _source_index(snapshot:dict)->dict:
    index={}
    for item in snapshot.get('sources') or []:
        if isinstance(item,dict) and isinstance(item.get('path'),str):
            index.setdefault(item['path'],[]).append({
                'path':item['path'],'state':item.get('state'),
                'origin':item.get('origin'),'reference':item.get('reference'),
                'reviewer':item.get('approved_by'),'metadataOnly':True,
                'originalBytesAuthenticated':False})
    return index


def _stage_records(result:dict,check:dict)->list[dict]:
    rows=check.get('stages') or []
    if not isinstance(rows,list):return []
    stage_ids={str(s.get('id')) for s in result.get('stages') or [] if isinstance(s,dict)}
    out=[]
    for index,row in enumerate(rows):
        if not isinstance(row,dict):continue
        stage=row.get('stageId')
        stage_ok=stage is None or (str(stage) in stage_ids)
        actual=row.get('safetyFactor')
        required=row.get('requiredFactor')
        out.append({'stageId':stage,'recognizedStage':stage_ok,'status':row.get('status'),
            'assessment':_assessment(row.get('status')),
            'calculatedSafetyFactor':actual if _finite(actual) else None,
            'requiredSafetyFactor':required if _finite(required) else None,
            'hasComparableSafetyFactors':_finite(actual) and _finite(required),
            'safetyFactorMargin':round(actual-required,9) if _finite(actual) and _finite(required) else None,
            'requiredInputs':row.get('requiredInputs') if isinstance(row.get('requiredInputs'),list) else [],
            'methodStandard':row.get('standard'),'methodClause':row.get('clause'),
            'sourcePointer':f'/normativeCoverage/requiredChecks/__KEY__/stages/{index}'})
    return out



# Method labels disclose which normative provisions were reviewed; recording a
# clause is not evidence of its project-level applicability or complete design.
CIRCULAR_METHOD_BASIS={
 'concrete_circular_matched_nm_v':('JGJ 120-2012; GB/T 50010-2010 (2024)', '4.3.2 / Appendix B; 6.2 / 6.3'),
 'concrete_circular_independent_nm':('GB/T 50010-2010 (2024), engineering comparison only', 'Annex E, independent fiber quadrature'),
 'concrete_circular_independent_shear':('GB/T 50010-2010 (2024), arithmetic audit only', '6.3.12 / 6.3.15'),
 'concrete_circular_width_reference':('JTG 3362-2018, bridge reference, not applicable as automatic JGJ 120 approval', '6.4.3-6.4.5'),
 'concrete_circular_cage_construction':('Project-declared detailing criteria, independent professional review pending', 'cage spacing / cover / end-zone hoops'),
 'concrete_circular_nm_benchmark':('JGJ120-2012; GB/T50010-2010 (2024), numerical benchmark only','4.3.2 / Appendix B; Appendix E'),
 'concrete_circular_project_crack_review':('Project-specific circular crack method qualification, pending original report validation','GB55008 crack control and GB/T50010 7.1 scope'),
 'concrete_circular_second_order_reference':('Ideal Euler column reference; soil-spring second-order method pending','GB/T50010 second-order scope'),
 'concrete_circular_cage_fabrication':('Project-declared cage manufacturing transport and splice feasibility','fabrication, node, hook and joint evidence'),
}


def _member_station_records(result:dict,check_id:str,method:dict,pointer:str)->tuple[list[dict],int]:
    """Bound original per-station calculations to existing stage IDs and JSON pointers.

    Output is bounded for UI performance. The full original station array always
    remains in the sealed result; totals and governing stations are retained.
    """
    records=method.get('cases') or []
    if not isinstance(records,list):return [],0
    stage_ids={str(s.get('id')) for s in result.get('stages') or [] if isinstance(s,dict)}
    entries=[]
    for index,raw in enumerate(records):
        if not isinstance(raw,dict):continue
        stage=raw.get('stageId')
        numerical=raw.get('independent') if isinstance(raw.get('independent'),dict) else {}
        observed=raw.get('status') or numerical.get('status')
        shear=raw.get('shearStatus')
        utility=max((v for v in (raw.get('nmUtilization'),raw.get('shearUtilization')) if _finite(v)),default=None)
        entry={'stageId':stage,'recognizedStage':stage is None or str(stage) in stage_ids,
            'combination':raw.get('combination'), 'depthM':raw.get('depthM'),
            'side':raw.get('side'), 'rawStatus':observed,
            'shearRawStatus':shear,
            'nmUtilization':raw.get('nmUtilization') if _finite(raw.get('nmUtilization')) else None,
            'shearUtilization':raw.get('shearUtilization') if _finite(raw.get('shearUtilization')) else None,
            'maximumUtilization':utility,
            'numericalAgreement':numerical.get('status') if numerical else raw.get('status'),
            'methodQualified':False,
            'sourcePointer':pointer+'/result/cases/'+str(index)}
        entries.append(entry)
    # Rank independently calculated FAILs first, followed by controlling ULS
    # utilization and then other nodes. No interpolation of stations is used.
    def priority(r):
        fail=(r['rawStatus']=='fail' or r['shearRawStatus']=='fail' or
              r['numericalAgreement'] in ('numerical_disagreement','independent_solver_failed') or
              r['recognizedStage'] is False)
        return (0 if fail else 1,-(r['maximumUtilization'] if r['maximumUtilization'] is not None else -1.))
    return sorted(entries,key=priority)[:48],len(entries)

def build_check_contract(result:dict)->dict:
    """Return canonical, source-located engineering findings with explicit unknowns.

    Supported: existing one-strut method identity only. No new code clause is
    certified by this representation layer.
    """
    result_hash=result['resultHash'];snapshot=result.get('snapshot') or {}
    coverage=result.get('normativeCoverage') or {}
    checks=coverage.get('requiredChecks') or {}
    if not isinstance(checks,dict):raise ValueError('规范结果 requiredChecks 必须为字典')
    stage_ids={str(s.get('id')) for s in result.get('stages') or [] if isinstance(s,dict)}
    sources=_source_index(snapshot)
    rows=[]
    for key in sorted(set(checks) | SUPPORTED_SOLVER_CHECK_IDS):
        implemented=key in checks
        entry=checks[key] if implemented else {'status':'not_calculated',
          'reason':'该受限求解器的预期验算条目缺失，必须补齐实现并核对范围'}
        if not isinstance(entry,dict):raise ValueError('规范检查条目必须为字典: '+key)
        pointer=f'/normativeCoverage/requiredChecks/{key}'
        stage_records=_stage_records(result,entry)
        for row in stage_records:row['sourcePointer']=row['sourcePointer'].replace('__KEY__',key)
        gov=entry.get('governingStage') if isinstance(entry.get('governingStage'),dict) else {}
        sid=gov.get('stageId')
        if sid is None:
            failed=next((s for s in stage_records if s['assessment']=='fail'),None)
            sid=(failed or next((s for s in stage_records if s['assessment']=='incomplete'),{})).get('stageId')
        method=(entry.get('result') or {}) if isinstance(entry.get('result'),dict) else {}
        default_basis=CIRCULAR_METHOD_BASIS.get(key,(None,None))
        standard=next((s['methodStandard'] for s in stage_records if s['methodStandard']),None) or default_basis[0]
        clause=next((s['methodClause'] for s in stage_records if s['methodClause']),None) or default_basis[1]
        stations,station_count=_member_station_records(result,key,method,pointer)
        if sid is None and stations:
            # First explicitly violated station, else largest physical N/M/V
            # utilization. An unavailable stage identity remains unknown.
            governing=next((s for s in stations if s['stageId'] is not None),None)
            if governing:
                sid=governing['stageId']
                if not gov:gov={'stageId':sid,'depthM':governing['depthM'],'side':governing['side']}
        needed=method.get('requiredInputs') or entry.get('requiredInputs') or []
        for s in stage_records:needed=[*needed,*s['requiredInputs']]
        needed=sorted({str(x) for x in needed})
        evidence={path:sources.get(path,[]) for path in needed}
        raw=entry.get('status')
        overall=_assessment(raw)
        contradictory=(overall=='pass' and any(s['assessment'] in {'fail','incomplete','unrecognized_status'}
                     or s['recognizedStage'] is False for s in stage_records))
        invalid_stage=bool(sid is not None and str(sid) not in stage_ids) or any(not s['recognizedStage'] for s in stations)
        if contradictory or invalid_stage:overall='inconsistent_evidence'
        row={'id':sha256(encode({'resultHash':result_hash,'checkId':key}).encode()).hexdigest()[:24],
             'checkId':key,'rawStatus':raw,'assessment':overall,
             'implementedInFrozenResult':implemented,
             'methodStandard':standard,'methodClause':clause,
             'methodLabel':method.get('method') or entry.get('method'),
             'methodVersion':result.get('methodVersion'),
             'governingStageId':sid,'governingStation':{
                  k:gov.get(k) for k in ('depthM','side') if k in gov},
             'governingStageRecognized':not invalid_stage,
             'stageChecks':stage_records,'memberStationChecks':stations,
             'memberStationCount':station_count,'memberStationChecksShown':len(stations),
             'requiredInputs':needed,
             'declaredSourceEvidence':evidence,'originalSourceBytesVerified':False,
             'resultPointer':pointer if implemented else None,'frozenResultHash':result_hash,
             'failureReason':entry.get('reason') or method.get('reason'),
             'engineeringReviewed':False,
             'formalConstructionIssue':False}
        rows.append(row)
    dis=(result.get('checks') or {}).get('displacement') or {}
    v,lim=dis.get('valueMm'),dis.get('limitMm')
    gov=(result.get('governing') or {}).get('uMm') or {}
    bounded=_finite(v) and _finite(lim) and lim>0
    project_checks=[{'id':'project.displacement','type':'project_limit','unit':'mm',
        'assessment':'pass' if bounded and v<=lim else 'fail' if bounded and v>lim else 'incomplete',
        'actual':v if _finite(v) else None,'limit':lim if _finite(lim) else None,
        'governingStageId':gov.get('stageId'),'depthM':gov.get('depthM'),
        'sourcePointer':'/checks/displacement','method':'project_declared_displacement_limit',
        'originalSourceBytesVerified':False}]
    passive=(result.get('checks') or {}).get('passiveResultant') or {}
    project_checks.append({'id':'project.passive_resultant','type':'solver_screening',
        'assessment':'pass' if passive.get('satisfied') is True else
            'fail' if passive.get('satisfied') is False else 'incomplete',
        'sourcePointer':'/checks/passiveResultant','method':'frozen_solver_passive_resultant',
        'originalSourceBytesVerified':False})
    rebar=result.get('reinforcement') or {}
    p,minimum=rebar.get('providedAreaMm2'),rebar.get('minimumAreaMm2')
    area_valid=_finite(p) and _finite(minimum) and minimum>0
    project_checks.append({'id':'project.minimum_longitudinal_area','type':'partial_detailing',
        'assessment':'pass' if area_valid and p>=minimum else 'fail' if area_valid else 'incomplete',
        'providedAreaMm2':p if _finite(p) else None,
        'requiredAreaMm2':minimum if _finite(minimum) else None,
        'sourcePointer':'/reinforcement','method':'minimum_area_only_not_full_NMV_crack',
        'originalSourceBytesVerified':False})
    bad=[r['checkId'] for r in rows if r['assessment']=='fail']
    unresolved=[r['checkId'] for r in rows if r['assessment'] not in {'pass'}]
    invalid=[r['checkId'] for r in rows if r['assessment'] in {'unrecognized_status','inconsistent_evidence'}]
    return {'schemaVersion':'pitguard.a5-check-contract/3.88.2',
       'memberStationContractRevision':'3.88.6',
       'projectId':result.get('projectId'),'sectionId':result.get('sectionId'),
       'resultHash':result_hash,'snapshotHash':result.get('snapshotHash'),
       'methodVersion':result.get('methodVersion'),
       'normativeChecks':rows,'projectAndScreeningChecks':project_checks,
       'summary':{'totalNormativeChecks':len(rows),'explicitFailures':bad,
                  'notFullyVerified':unresolved,'inconsistentOrUnknown':invalid,
                  'knownProjectFailures':[r['id'] for r in project_checks if r['assessment']=='fail'],
                  'requiredChecksAllPassed':len(rows)>0 and not unresolved,
                  'missingSolverCheckImplementations':sorted(set(SUPPORTED_SOLVER_CHECK_IDS)-set(checks))},
       'allNormativeRequirementsVerified':False if unresolved or not coverage.get('fullNormativeVerificationComplete') else True,
       'originalSourceBytesIndependentlyAuthenticated':False,
       'engineeringQualified':False,'formalConstructionIssue':False,
       'limitations':['只转写封存数值及方法状态，不能证明原地质条件、规范适用域或第三方来源真实性',
                      '局部配筋面积、位移、被动合力筛查不等于完整施工图规范验算']}
