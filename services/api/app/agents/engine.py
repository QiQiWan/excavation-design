"""A5/A6 pilot: diagnosis -> bounded proposals -> immutable FE re-solves -> human review.

Never mutate a project, falsify a PASS or have an LLM select a safety factor.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from threading import Event, Thread
from uuid import uuid4
import json,math,time
from .store import AgentStore,ConflictError,encode
from .model_gateway import ModelGateway
from app.engineering.single_braced_pile.repository import Repository

ALLOWED_FIELDS={'strut.stiffness_kn_m','pile.inertia_factor','pile.length_m'}
UNQUALIFIED={'fail','missing_input','not_calculated','outside_method_scope','review_required','calculated_scope_limited'}


def diagnostics(result:dict)->dict:
    issues=[]
    for raw in result.get('issues',[])[:120]:
        code=str(raw.get('code','UNKNOWN'))[:90]
        kind=('physical_constraint' if code in ('DISPLACEMENT_OVER_LIMIT','PASSIVE_RESULTANT_LIMIT','SOLVER_FAILURE','INVALID_CAGE_GEOMETRY')
              else 'missing_engineering_evidence' if code in ('STABILITY_NOT_INTEGRATED','REBAR_NOT_FULLY_VERIFIED')
              or code.startswith('NORMATIVE_') else 'engineering_review')
        issues.append({'code':code,'category':kind,'field':str(raw.get('field',''))[:140],
                       'message':str(raw.get('message',''))[:360]})
    norms=(result.get('normativeCoverage') or {}).get('requiredChecks') or {}
    for key,raw in norms.items():
        if raw.get('status') in UNQUALIFIED:
            issues.append({'code':'CHECK_'+str(key)[:80], 'category':'normative_coverage',
                           'field':str(key)[:140], 'message':'验算状态：'+str(raw.get('status'))})
    measured=(result.get('checks') or {}).get('displacement') or {}
    return {'schemaVersion':'pitguard.agent-diagnosis/1','analysisStatus':result['analysisStatus'],
            'physicalFailureCount':sum(x['category']=='physical_constraint' for x in issues),
            'unresolvedCount':len(issues),'issues':issues,
            'displacement':{'valueMm':measured.get('valueMm'),'limitMm':measured.get('limitMm'),
                            'satisfied':measured.get('satisfied')},
            'formalConstructionIssue':False}


def _action(snapshot,field,factor,why):
    a,b=field.split('.')
    old=snapshot[a][b]
    new=round(old*factor,8)
    if field=='pile.inertia_factor': new=min(1.,new)
    if field=='pile.length_m':new=round(old+factor,3)
    if math.isclose(new,old,rel_tol=1e-8,abs_tol=1e-8):return None
    return {'field':field,'before':old,'after':new,'reason':why,
            'permission':'L1_candidate_only','method':'bounded_parameter_trial'}


def proposals(result,max_candidates:int)->list[dict]:
    """Permitted mechanical trial variables only. No soil/water or norm limit edits."""
    snap=result['snapshot'];issues={x.get('code') for x in result.get('issues',[])}
    pool=[]
    if 'PASSIVE_RESULTANT_LIMIT' in issues:
        # Length can only be changed within the explicitly supplied strata.
        max_depth=snap['layers'][-1]['bottom_m'];old=snap['pile']['length_m']
        for delta in (.5,1.0,1.5):
            if old+delta<=max_depth and old+delta<=50:
                pool.append(_action(snap,'pile.length_m',delta,'被动土反力合力超限；试算嵌固深度'))
    if 'DISPLACEMENT_OVER_LIMIT' in issues or result['analysisStatus']=='complete':
        pool.extend([_action(snap,'strut.stiffness_kn_m',factor,'对支撑刚度进行有界敏感性试算')
                     for factor in (1.20,1.50,1.80)])
    if snap['pile']['inertia_factor']<.99:
        pool.append(_action(snap,'pile.inertia_factor',1.15,'刚度折减敏感性，仅在经工程师确认后可采用'))
    if 'PASSIVE_RESULTANT_LIMIT' in issues and not pool:
        pool.append(_action(snap,'strut.stiffness_kn_m',1.25,'支撑约束敏感性试算，不能替代嵌固验算'))
    clean=[];seen=set()
    for act in pool:
        if not act:continue
        key=(act['field'],act['after'])
        if key not in seen:
            clean.append(act);seen.add(key)
        if len(clean)>=max_candidates:break
    return clean


def changed_snapshot(original:dict,action:dict)->dict:
    field=action['field']
    if field not in ALLOWED_FIELDS:raise ValueError('禁止Agent修改未授权的工程字段')
    a,b=field.split('.')
    snap=deepcopy(original)
    if not isinstance(snap.get(a),dict) or snap[a].get(b)!=action['before']:
        raise ValueError('候选参数与冻结原值不匹配')
    snap[a][b]=action['after']
    # An engineering source confirming the OLD value does not approve a new
    # candidate value. Downgrade its provenance until separately rechecked.
    for source in snap.get('sources',[]):
        if source.get('path')==field and source.get('state')=='confirmed':
            source['state']='candidate'
            source['approved_by']=None
    return snap


def _norm_status(result):
    return {code:val.get('status') for code,val in
            ((result.get('normativeCoverage') or {}).get('requiredChecks') or {}).items()}


def measure(candidate:dict,source:dict)->dict:
    c=(candidate.get('checks') or {}).get('displacement') or {}
    s=(source.get('checks') or {}).get('displacement') or {}
    cv=c.get('valueMm');sv=s.get('valueMm');cn=_norm_status(candidate);sn=_norm_status(source)
    regression=(candidate.get('analysisStatus')!='complete'
                or (isinstance(cv,(int,float)) and isinstance(sv,(int,float)) and cv>sv+max(.01,abs(sv)*.005))
                or any(cn.get(k)=='fail' and sn.get(k)!='fail' for k in cn))
    cleared=sorted(k for k in sn if sn.get(k)=='fail' and cn.get(k)=='pass')
    inc=(isinstance(cv,(int,float)) and isinstance(sv,(int,float)) and cv<sv-1e-5)
    source_confirmed=source.get('preflight',{}).get('engineeringEvidenceComplete') is True
    selection_eligible=(candidate.get('analysisStatus')=='complete' and not regression
        and candidate['snapshot'].get('purpose')=='project_review' and source_confirmed
        )
    return {'analysisStatus':candidate.get('analysisStatus'),'baselineDisplacementMm':sv,
            'candidateDisplacementMm':cv,'displacementLimitMm':c.get('limitMm'),
            'displacementSatisfied':c.get('satisfied'), 'improvedDisplacement':bool(inc),
            'regression':bool(regression),'clearedNormativeFailures':cleared,
            'normativeUnqualified':sorted(k for k,v in cn.items() if v in UNQUALIFIED),
            'workingSelectionEligible':bool(selection_eligible),
            'changedParameterEvidenceNeedsReconfirmation':not bool(candidate.get('preflight',{}).get('engineeringEvidenceComplete')),
            'engineeringQualified':False,'formalConstructionIssue':False,
            'qualificationNote':'本阶段仅形成候选试算与工作版采用条件，未覆盖的规范验算继续阻断正式签发'}


def evaluate_candidate(result:dict,action:dict,repo:Repository)->dict:
    try:
        candidate=repo.compute(changed_snapshot(result['snapshot'],action))
        metrics=measure(candidate,result)
        return {'status':'calculated' if candidate['analysisStatus']=='complete' else 'solver_blocked',
                'snapshotHash':candidate['snapshotHash'],'resultHash':candidate['resultHash'],
                'metrics':metrics,'error':None}
    except (ValueError,RuntimeError,ArithmeticError,KeyError) as exc:
        return {'status':'invalid_or_failed','snapshotHash':None,'resultHash':None,
                'metrics':{'engineeringQualified':False,'workingSelectionEligible':False,
                           'formalConstructionIssue':False},
                'error':(type(exc).__name__+': '+str(exc))[:280]}


class AgentRuntime:
    def __init__(self,store=None,repo=None,gateway=None):
        self.store=store or AgentStore()
        self.repo=repo or Repository()
        self.gateway=gateway or ModelGateway()

    def create(self,body,actor):
        result=self.repo.load(body.result_hash)
        return self.store.create(result=result,actor=actor,max_candidates=body.max_candidates,
                                 model_mode=body.model_mode,client_request_id=body.client_request_id)

    def _compute_with_heartbeat(self,run,owner,base,action):
        stop=Event()
        def heartbeat():
            while not stop.wait(12):
                try:
                    if not self.store.heartbeat(run['id'],owner,lease_seconds=180):break
                except Exception:break
        thread=Thread(target=heartbeat,daemon=True)
        thread.start()
        try:return evaluate_candidate(base,action,self.repo)
        finally:
            stop.set();thread.join(timeout=.5)

    def process_one(self,run_id=None)->dict|None:
        owner='agent-worker-'+uuid4().hex
        run=self.store.claim(owner,lease_seconds=180,run_id=run_id)
        if run is None:return None
        rid=run['id'];step=run['step']
        try:
            base=self.repo.load(run['sourceResultHash'])
            if base['snapshotHash']!=run['sourceSnapshotHash'] or base['projectId']!=run['projectId']:
                raise ConflictError('冻结工程结果身份发生变化')
            if step=='diagnose':
                found=diagnostics(base)
                annotation=self.gateway.annotate(project_id=run['projectId'],issues=found['issues'],mode=run['modelMode'])
                found['modelExplanation']=annotation
                return self.store.advance(rid,owner,step,status='queued',step='plan',progress=20,
                    diagnostics=found,event_key='diagnosis',event_type='diagnosis_completed',
                    payload={'issueCount':found['unresolvedCount'],'modelStatus':annotation['status']})
            if step=='plan':
                actions=proposals(base,run['maxCandidates'])
                plan={'actions':actions,'cursor':0,'sourceSnapshotHash':run['sourceSnapshotHash'],
                      'engineeringMethodValidated':False,'formalConstructionIssue':False}
                return self.store.advance(rid,owner,step,status='queued',step='evaluate' if actions else 'summarize',
                    progress=30,plan=plan,event_key='planned',event_type='candidate_plan_created',
                    payload={'candidateCount':len(actions)})
            if step=='evaluate':
                plan=run['plan'] or {}
                cursor=int(plan.get('cursor',0));actions=plan.get('actions') or []
                if cursor>=len(actions):
                    return self.store.advance(rid,owner,step,status='queued',step='summarize',progress=90,
                        event_key='evaluated_all',event_type='candidate_evaluation_completed',payload={})
                action=actions[cursor]
                evaluated=self._compute_with_heartbeat(run,owner,base,action)
                cid=sha256(encode({'runId':rid,'proposalIndex':cursor,'patch':action}).encode()).hexdigest()
                candidate={'id':cid,'proposalIndex':cursor,'patch':action,**evaluated}
                next_plan=dict(plan,cursor=cursor+1)
                last=(cursor+1)>=len(actions)
                progress=min(89,30+round(59*(cursor+1)/max(1,len(actions))))
                return self.store.advance(rid,owner,step,status='queued',step='summarize' if last else 'evaluate',
                    progress=progress,plan=next_plan,candidate=candidate,
                    event_key='candidate:'+str(cursor),event_type='candidate_evaluated',
                    payload={'candidateId':cid,'index':cursor,'status':evaluated['status'],
                             'resultHash':evaluated.get('resultHash')})
            if step=='summarize':
                cand=self.store.candidates(rid)
                viable=[c for c in cand if c['status']=='calculated']
                eligible=[c['id'] for c in viable if c['metrics'].get('workingSelectionEligible')]
                output={'schemaVersion':'pitguard.agent-summary/1',
                        'baselineResultHash':run['sourceResultHash'],
                        'candidateCount':len(cand),'calculatedCount':len(viable),
                        'workingCopyEligibleCandidateIds':eligible,
                        'unresolvedEngineeringIssues':(run['diagnostics'] or {}).get('unresolvedCount'),
                        'engineeringQualified':False,'formalConstructionIssue':False,
                        'selectionRequiresCheckerDecision':True,
                        'note':'方案试算完成；未通过方法与工程证据不能被候选优化替代'}
                state='awaiting_review' if cand else 'completed_no_candidates'
                return self.store.advance(rid,owner,step,status=state,step='review' if cand else 'done',
                    progress=95 if cand else 100,summary=output,
                    event_key='summarized',event_type='candidate_comparison_completed',payload=output)
            raise ValueError('未知或不允许的Agent执行阶段')
        except Exception as exc:
            self.store.fail(rid,owner,type(exc).__name__+': '+str(exc))
            return self.store.get(rid)

    def process_until_idle(self,run_id,limit=20):
        for _ in range(limit):
            progressed=self.process_one(run_id)
            if progressed is None:break
            if progressed['status'] not in ('queued','running'):break
        return self.store.get(run_id)
