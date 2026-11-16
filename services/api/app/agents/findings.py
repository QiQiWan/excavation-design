"""A5: locate frozen calculation findings by member, stage, station and evidence.

Rule-based diagnostics report *what the numerical result contains*. They never
invent a governing section, a code check, or a missing project source.
"""
from __future__ import annotations
from hashlib import sha256
from .store import encode

FAILED = {'fail', 'failed', 'blocked'}
INCOMPLETE = {'missing_input', 'not_calculated', 'outside_method_scope',
              'review_required', 'calculated_scope_limited', 'screening_only'}


def _governing(result: dict, field: str) -> dict:
    mapping = {'displacement_limit_mm': 'uMm'}
    source = (result.get('governing') or {}).get(mapping.get(field, ''))
    return dict(source) if isinstance(source, dict) else {}


def _target(field: str, code: str) -> tuple[str, list[str]]:
    if code == 'DISPLACEMENT_OVER_LIMIT':
        return 'pile_and_support', ['support_stiffness', 'support_elevation', 'pile_stiffness']
    if code == 'PASSIVE_RESULTANT_LIMIT':
        return 'pile_toe', ['embedded_length']
    if code == 'INVALID_CAGE_GEOMETRY':
        return 'reinforcement_cage', ['rebar_layout_review']
    if field.startswith('cage') or code.startswith('CHECK_concrete_circular'):
        return 'reinforcement_cage', ['reinforcement_review']
    if field.startswith('strut') or code.startswith('CHECK_4.9'):
        return 'strut', ['support_member_review']
    if field.startswith('pile'):
        return 'pile', ['pile_design_review']
    if field.startswith('layers') or field.startswith('groundwater'):
        return 'ground_model', ['source_review']
    return 'project', ['source_review']


def diagnose(result: dict) -> dict:
    findings=[]
    src_hash=result['resultHash']
    from .normative_contract import build_check_contract
    contract=build_check_contract(result)
    checks_by_id={x['checkId']:x for x in contract['normativeChecks']}
    source_entries={}
    for ev in (result.get('snapshot') or {}).get('sources',[]) or []:
        if isinstance(ev,dict):source_entries.setdefault(ev.get('path'),[]).append(ev)
    raw_issues=list(result.get('issues') or [])[:300]
    cover=((result.get('normativeCoverage') or {}).get('requiredChecks') or {})
    for name, check in cover.items():
        if not isinstance(check, dict):
            continue
        if check.get('status') in FAILED | INCOMPLETE:
            raw_issues.append({'code': 'CHECK_'+name, 'field': name,
                               'message': '规范验算状态: '+str(check.get('status')),
                               'status': check.get('status'),
                               'governing': check.get('governingStage')})
    for index, raw in enumerate(raw_issues):
        code=str(raw.get('code','UNKNOWN'))[:96]
        field=str(raw.get('field',''))[:120]
        status=raw.get('status')
        reason=str(raw.get('message',''))[:420]
        gov=raw.get('governing') if isinstance(raw.get('governing'),dict) else _governing(result,field)
        if code=='PASSIVE_RESULTANT_LIMIT':
            governing_stage=next((x for x in result.get('stages',[])
                if x.get('passiveLimitSatisfied') is False),None)
            if governing_stage:
                gov={'stageId':governing_stage.get('id'),
                     'passiveResultantKn':governing_stage.get('passiveResultantKn'),
                     'passiveReferenceKn':governing_stage.get('passiveReferenceKn')}
        if code.startswith('CHECK_') and not gov:
            check=cover.get(code[6:]) or {}
            records=check.get('stages') or []
            item=next((q for q in records if isinstance(q,dict) and q.get('status') in FAILED),None)
            if item:
                gov={'stageId':item.get('stageId'),
                     'calculatedSafetyFactor':item.get('safetyFactor'),
                     'requiredSafetyFactor':item.get('requiredFactor')}
        if code.startswith('CHECK_') and not gov:
            contract_check=checks_by_id.get(code[6:]) or {}
            member=(contract_check.get('memberStationChecks') or [])
            station=next((s for s in member if s.get('stageId') is not None),None)
            if station:
                gov={'stageId':station['stageId'],'depthM':station.get('depthM'),
                    'side':station.get('side'),'combination':station.get('combination'),
                    'nmUtilization':station.get('nmUtilization'),
                    'shearUtilization':station.get('shearUtilization'),
                    'sourcePointer':station.get('sourcePointer')}
        category=('physical_constraint' if code in {'DISPLACEMENT_OVER_LIMIT','PASSIVE_RESULTANT_LIMIT',
                     'SOLVER_FAILURE','INVALID_CAGE_GEOMETRY'} or status in FAILED
                  else 'normative_coverage' if code.startswith('CHECK_') or code.startswith('NORMATIVE_')
                  else 'missing_engineering_evidence' if code.startswith('SOURCE_') or code in
                    {'STABILITY_NOT_INTEGRATED','REBAR_NOT_FULLY_VERIFIED'} else 'engineering_review')
        component,families=_target(field,code)
        if component=='strut':object_id=(result.get('snapshot') or {}).get('strut',{}).get('id')
        elif component in ('pile','pile_toe','pile_and_support'):
            object_id=(result.get('snapshot') or {}).get('section_id')
        elif component=='reinforcement_cage':object_id='CAGE:'+str(result.get('sectionId',''))
        else:object_id=None
        ident={'resultHash':src_hash,'code':code,'field':field,'index':index,
               'stage':gov.get('stageId'),'depth':gov.get('depthM'),'side':gov.get('side')}
        linked_check=checks_by_id.get(code[6:]) if code.startswith('CHECK_') else None
        source_records=[{'path':entry.get('path'),'reference':entry.get('reference'),
                         'state':entry.get('state'),'reviewer':entry.get('approved_by'),
                         'originalBytesAuthenticated':False} for entry in source_entries.get(field,[])]
        cause=('explicit_violated_constraint' if category=='physical_constraint' else
               'unverified_normative_method_or_input' if linked_check else
               'missing_or_unconfirmed_source' if code.startswith('SOURCE_') else
               'unclassified_require_engineer_diagnosis')
        evidence_pointer=(linked_check['resultPointer'] if linked_check else
            '/issues/'+str(index) if index<len(result.get('issues') or []) else None)
        findings.append({'id':sha256(encode(ident).encode()).hexdigest()[:24],
                         'code':code,'category':category,'field':field,'message':reason,
                         'objectType':component,'objectId':object_id,
                         'stageId':gov.get('stageId'), 'depthM':gov.get('depthM'),
                         'sectionSide':gov.get('side'),
                         'governing':gov if gov else None,
                         'trace':{'frozenResultHash':src_hash,'evidencePointer':evidence_pointer,
                                  'governingMemberStationPointer':gov.get('sourcePointer') if gov else None,
                                  'methodClause':linked_check.get('methodClause') if linked_check else None,
                                  'methodLabel':linked_check.get('methodLabel') if linked_check else None,
                                  'sourceMetadata':source_records,'sourceOriginalBytesAuthenticated':False},
                         'causeClassification':cause,'causeRequiresEngineeringConfirmation':True,
                         'proposedActionFamilies':families,
                         'autoRepairable': code in {'DISPLACEMENT_OVER_LIMIT','PASSIVE_RESULTANT_LIMIT'} and
                             result.get('analysisStatus')=='complete',
                         'numericalFinding':category=='physical_constraint',
                         'reviewRequired':True})
    order={'physical_constraint':0,'normative_coverage':1,'engineering_review':2,'missing_engineering_evidence':3}
    findings.sort(key=lambda x:(order.get(x['category'],4),not x['autoRepairable'],x['code'],x['field']))
    displaced=(result.get('checks') or {}).get('displacement') or {}
    min_rb=result.get('reinforcement') or {}
    gap=None
    if isinstance(min_rb.get('providedAreaMm2'),(float,int)) and isinstance(min_rb.get('minimumAreaMm2'),(float,int)):
        gap=max(0.,min_rb['minimumAreaMm2']-min_rb['providedAreaMm2'])
    return {'schemaVersion':'pitguard.a5-member-findings/2',
            'resultHash':src_hash, 'analysisStatus':result.get('analysisStatus'),
            'issues':[{'code':x['code'],'category':x['category'],'field':x['field'],
                       'message':x['message'],'id':x['id'],'stageId':x['stageId'],
                       'depthM':x['depthM'],'objectId':x['objectId']} for x in findings],
            'findings':findings,'unresolvedCount':len(findings),
            'checkContractSummary':contract['summary'],
            'physicalFailureCount':sum(x['category']=='physical_constraint' for x in findings),
            'evidenceBlockerCount':sum(x['category']=='missing_engineering_evidence' for x in findings),
            'displacement':{'valueMm':displaced.get('valueMm'),'limitMm':displaced.get('limitMm'),
                            'satisfied':displaced.get('satisfied')},
            'reinforcement':{'minimumAreaGapMm2':gap,'fullRebarVerification':min_rb.get('fullRebarVerification') is True,
                              'minimumAreaMet':min_rb.get('minimumAreaMet')},
            'engineeringQualified':False,'formalConstructionIssue':False}
