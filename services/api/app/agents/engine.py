"""A5/A6 pilot: diagnosis -> bounded proposals -> immutable FE re-solves -> human review.

Never mutate a project, falsify a PASS or have an LLM select a safety factor.
"""
from __future__ import annotations
from hashlib import sha256
from threading import Event, Thread
from uuid import uuid4
import json,math,time
from .store import AgentStore,ConflictError,encode
from .findings import diagnose as _diagnose
from .search_rules import apply_action,proposals as _proposals,numerical_score,changed_fields,ALLOWED_FIELDS
from .model_gateway import ModelGateway
from .normative_contract import build_check_contract
from .constraints import evaluate_constraints
from .pareto import pareto_report,objectives
from app.engineering.single_braced_pile.repository import Repository

UNQUALIFIED={'fail','missing_input','not_calculated','outside_method_scope','review_required','calculated_scope_limited'}


def diagnostics(result:dict)->dict:
    report=_diagnose(result)
    report['checkContract']=build_check_contract(result)
    return report


def proposals(result,max_candidates:int,*,visited=None,focus='auto'):
    return _proposals(result,max_candidates,visited=visited,focus=focus)


def changed_snapshot(original:dict,action:dict)->dict:
    return apply_action(original,action)


def _norm_status(result):
    return {code:val.get('status') for code,val in
            ((result.get('normativeCoverage') or {}).get('requiredChecks') or {}).items()}


def measure(candidate:dict,source:dict)->dict:
    c=(candidate.get('checks') or {}).get('displacement') or {}
    s=(source.get('checks') or {}).get('displacement') or {}
    cv=c.get('valueMm');sv=s.get('valueMm');cn=_norm_status(candidate);sn=_norm_status(source)
    base_rb=source.get('reinforcement') or {};rb=candidate.get('reinforcement') or {}
    area_regression=(isinstance(base_rb.get('providedAreaMm2'),(int,float)) and
         isinstance(rb.get('providedAreaMm2'),(int,float)) and
         rb['providedAreaMm2'] < base_rb['providedAreaMm2'] and
         rb.get('minimumAreaMet') is False)
    newly_failing=sorted(k for k in set(cn)|set(sn) if cn.get(k)=='fail' and sn.get(k)!='fail')
    downgraded=sorted(k for k in sn if sn[k]=='pass' and cn.get(k)!='pass')
    regression=(candidate.get('analysisStatus')!='complete'
                or (s.get('satisfied') is True and c.get('satisfied') is not True)
                or (source.get('checks',{}).get('passiveResultant',{}).get('satisfied') is True
                    and candidate.get('checks',{}).get('passiveResultant',{}).get('satisfied') is False)
                or (isinstance(cv,(int,float)) and isinstance(sv,(int,float))
                      and cv>sv+max(.01,abs(sv)*.005))
                or bool(newly_failing or downgraded or area_regression))
    cleared=sorted(k for k in sn if sn.get(k)=='fail' and cn.get(k)=='pass')
    inc=(isinstance(cv,(int,float)) and isinstance(sv,(int,float)) and cv<sv-1e-5)
    source_confirmed=source.get('preflight',{}).get('engineeringEvidenceComplete') is True
    work_eligible=(candidate.get('analysisStatus')=='complete' and not regression
        and c.get('satisfied') is True
        and candidate.get('checks',{}).get('passiveResultant',{}).get('satisfied') is not False
        and candidate['snapshot'].get('purpose')=='project_review' and source_confirmed)
    score=numerical_score(candidate,source)
    constraints=evaluate_constraints(candidate)
    multi_objectives=objectives(candidate,source)
    return {'analysisStatus':candidate.get('analysisStatus'),'baselineDisplacementMm':sv,
            'candidateDisplacementMm':cv,'displacementLimitMm':c.get('limitMm'),
            'displacementSatisfied':c.get('satisfied'), 'improvedDisplacement':bool(inc),
            'regression':bool(regression),'newNormativeFailures':newly_failing,
            'downgradedNormativePasses':downgraded,'clearedNormativeFailures':cleared,
            'normativeUnqualified':sorted(k for k,v in cn.items() if v in UNQUALIFIED),
            'workingSelectionEligible':bool(work_eligible),
            'changedParameterEvidenceNeedsReconfirmation':bool(changed_fields(source['snapshot'],candidate['snapshot'])),
            'candidateParameterSourcesReconfirmed':False,
            'numericalScore':score,
            'constraintEvaluation':constraints,
            'multiObjectiveEvaluation':multi_objectives,
            'engineeringQualified':False,'formalConstructionIssue':False,
            'qualificationNote':'数值比较和工作版资格独立于适用规范全项校核；采用前必须核验全部修改参数原始依据'}


def verify_candidate_lineage(connection,run_row,candidate_row,repo):
    """Reconstruct every immutable trial from its actual parent, preventing forged lineage."""
    from app.engineering.single_braced_pile.analysis import canonical_hash
    seen=set();node=candidate_row;hops=0
    while True:
        hops+=1
        if hops>32 or node['id'] in seen:
            raise ValueError('候选父代链存在循环或超出最大深度')
        seen.add(node['id'])
        if node['status']!='calculated' or not node['result_hash']:
            raise ValueError('候选未完成可靠的独立复算')
        parent_hash=node['parent_result_hash'] or run_row['source_result_hash']
        parent=repo.load(parent_hash);child=repo.load(node['result_hash'])
        action=json.loads(node['patch_json'])
        expected=changed_snapshot(parent['snapshot'],action)
        if (canonical_hash(expected)!=child['snapshotHash'] or child['snapshotHash']!=node['snapshot_hash']
                or parent['projectId']!=child['projectId'] or parent['sectionId']!=child['sectionId']):
            raise ValueError('候选修改字段、父代身份或封存结果不一致')
        if parent_hash==run_row['source_result_hash']:
            if parent['snapshotHash']!=run_row['source_snapshot_hash']:
                raise ValueError('原始冻结快照不一致')
            return hops
        ancestor=connection.execute('SELECT * FROM candidates WHERE run_id=? AND result_hash=?',
                                    (run_row['id'],parent_hash)).fetchone()
        if ancestor is None:raise ValueError('候选父代不属于同一 Agent 运行')
        node=ancestor


def evaluate_candidate(result:dict,action:dict,repo:Repository,*,baseline=None)->dict:
    try:
        candidate=repo.compute(changed_snapshot(result['snapshot'],action))
        metrics=measure(candidate,baseline or result)
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
                 max_rounds=body.max_rounds,focus=body.focus,model_mode=body.model_mode,
                 max_evaluations=body.max_evaluations,max_runtime_seconds=body.max_runtime_seconds,
                 client_request_id=body.client_request_id)

    def _compute_with_heartbeat(self,run,owner,parent,action,baseline):
        stop=Event()
        def heartbeat():
            while not stop.wait(12):
                try:
                    if not self.store.heartbeat(run['id'],owner,lease_seconds=180):break
                except Exception:break
        thread=Thread(target=heartbeat,daemon=True)
        thread.start()
        try:return evaluate_candidate(parent,action,self.repo,baseline=baseline)
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
                    payload={'issueCount':found['unresolvedCount'],'modelStatus':annotation['status'],
                             'locatedFindings':len(found['findings'])})
            if step=='plan':
                actions=proposals(base,run['maxCandidates'],focus=run['focus'])
                plan={'actions':actions,'cursor':0,'roundIndex':1,
                      'evaluationBudget':run['maxEvaluations'],
                      'runtimeBudgetSeconds':run['maxRuntimeSeconds'],
                      'parentResultHash':run['sourceResultHash'],'attempted':0,
                      'visited':[[a['field'],a['after']] for a in actions],
                      'roundSummaries':[],'sourceSnapshotHash':run['sourceSnapshotHash'],
                      'stopReason':None,'engineeringMethodValidated':False,'formalConstructionIssue':False}
                return self.store.advance(rid,owner,step,status='queued',step='evaluate' if actions else 'summarize',
                    progress=30,plan=plan,event_key='planned',event_type='candidate_plan_created',
                    payload={'candidateCount':len(actions),'maxRounds':run['maxRounds']})
            if step=='evaluate':
                plan=run['plan'] or {}
                cursor=int(plan.get('cursor',0));actions=plan.get('actions') or []
                elapsed=max(0.,time.time()-__import__('datetime').datetime.fromisoformat(run['createdAt']).timestamp())
                attempted=int(plan.get('attempted',0))
                if attempted>=run['maxEvaluations'] or elapsed>=run['maxRuntimeSeconds']:
                    why='evaluation_budget_exhausted' if attempted>=run['maxEvaluations'] else 'runtime_budget_exhausted'
                    final_plan=dict(plan,stopReason=why,budgetElapsedSeconds=round(elapsed,2))
                    return self.store.advance(rid,owner,step,status='queued',step='round_review',
                        progress=91,plan=final_plan,event_key='budget-stop:'+str(attempted),
                        event_type='budget_stop',payload={'reason':why,'evaluationsUsed':attempted})
                if cursor>=len(actions):
                    return self.store.advance(rid,owner,step,status='queued',step='round_review',progress=85,
                        event_key='round-ended:'+str(plan.get('roundIndex',1)),
                        event_type='iteration_evaluated',payload={})
                parent_hash=plan.get('parentResultHash') or run['sourceResultHash']
                parent=self.repo.load(parent_hash)
                action=actions[cursor]
                evaluated=self._compute_with_heartbeat(run,owner,parent,action,base)
                index=int(plan.get('attempted',0))
                round_index=int(plan.get('roundIndex',1))
                cid=sha256(encode({'runId':rid,'proposalIndex':index,'parent':parent_hash,
                                   'patch':action}).encode()).hexdigest()
                candidate={'id':cid,'proposalIndex':index,'patch':action,
                           'parentResultHash':parent_hash,'roundIndex':round_index,**evaluated}
                next_plan=dict(plan,cursor=cursor+1,attempted=index+1)
                last=cursor+1>=len(actions)
                progress=min(90,30+round(60*(index+1)/max(1,run['maxCandidates']*run['maxRounds'])))
                return self.store.advance(rid,owner,step,status='queued',
                    step='round_review' if last else 'evaluate',progress=progress,
                    plan=next_plan,candidate=candidate,
                    event_key='candidate:'+str(index),event_type='candidate_evaluated',
                    payload={'candidateId':cid,'roundIndex':round_index,'index':index,
                             'status':evaluated['status'],'resultHash':evaluated.get('resultHash')})
            if step=='round_review':
                plan=run['plan'] or {}
                round_index=int(plan.get('roundIndex',1))
                this_round=[c for c in self.store.candidates(rid) if c['roundIndex']==round_index]
                valid=[c for c in this_round if c['status']=='calculated' and
                       c['metrics'].get('regression') is False]
                valid.sort(key=lambda c:(c['metrics'].get('numericalScore',{}).get('score',1e10),c['id']))
                pareto=pareto_report(valid,base,self.repo)
                ranks={x['candidateId']:x['paretoRank'] for x in pareto['candidates']}
                # Parent is chosen among nondominated candidates, then by the
                # explicit numeric tie-breaker. A Pareto front is not compliance.
                if valid:
                    eligible_front=min(ranks.values())
                    valid.sort(key=lambda c:(ranks.get(c['id'],99)!=eligible_front,
                        c['metrics'].get('numericalScore',{}).get('score',1e10),c['id']))
                prior_hash=plan.get('parentResultHash') or run['sourceResultHash']
                prior=self.repo.load(prior_hash)
                prior_score=numerical_score(prior,base)['score']
                best=valid[0] if valid else None
                best_score=best['metrics']['numericalScore']['score'] if best else None
                improved=bool(best and best_score<prior_score-0.001)
                report={'roundIndex':round_index,'parentResultHash':prior_hash,
                        'evaluatedCount':len(this_round),'nonRegressiveCount':len(valid),
                        'bestCandidateId':best['id'] if best else None,
                        'parentScore':prior_score,'bestScore':best_score,
                        'numericalImprovement':improved,
                        'fullEngineeringComplianceVerified':False,
                        'paretoFronts':pareto['fronts'],
                        'evaluationsUsed':plan.get('attempted',0),
                        'evaluationBudget':run['maxEvaluations']}
                rounds=[*(plan.get('roundSummaries') or []),report]
                stop=plan.get('stopReason') or ('stagnation_or_new_constraint' if not improved else None)
                if not stop and round_index>=run['maxRounds']:stop='maximum_rounds_reached'
                if not stop and plan.get('attempted',0)>=run['maxEvaluations']:
                    stop='evaluation_budget_exhausted'
                if not stop and time.time()-__import__('datetime').datetime.fromisoformat(run['createdAt']).timestamp()>=run['maxRuntimeSeconds']:
                    stop='runtime_budget_exhausted'
                if not stop and best:
                    checks=best['metrics']['numericalScore']
                    if (checks['numericViolation']==0 and checks['minimumRebarGapRatio']==0
                            and checks['explicitNormativeFailureCount']==0):
                        stop='numerical_targets_satisfied_evidence_still_required'
                actions=[]
                if not stop:
                    selected=self.repo.load(best['resultHash'])
                    visited={tuple(x) for x in (plan.get('visited') or [])}
                    actions=proposals(selected,run['maxCandidates'],visited=visited,focus=run['focus'])
                    if not actions:stop='no_new_admissible_actions'
                if stop:
                    next_plan=dict(plan,roundSummaries=rounds,stopReason=stop)
                    return self.store.advance(rid,owner,step,status='queued',step='summarize',
                        progress=91,plan=next_plan,event_key='round:'+str(round_index),
                        event_type='iteration_stopped',payload={'reason':stop,**report})
                next_plan=dict(plan,roundIndex=round_index+1,roundSummaries=rounds,
                               cursor=0,parentResultHash=best['resultHash'],
                               actions=actions,visited=[*(plan.get('visited') or []),
                                                        *[[x['field'],x['after']] for x in actions]])
                return self.store.advance(rid,owner,step,status='queued',step='evaluate',
                    progress=min(90,30+round(60*plan.get('attempted',0)/max(1,run['maxCandidates']*run['maxRounds']))),
                    plan=next_plan,event_key='round:'+str(round_index),
                    event_type='iteration_continues',payload={'nextRound':round_index+1,**report})
            if step=='summarize':
                cand=self.store.candidates(rid)
                viable=[c for c in cand if c['status']=='calculated']
                eligible=[c['id'] for c in viable if c['metrics'].get('workingSelectionEligible')]
                sorted_candidates=sorted((c for c in viable if not c['metrics'].get('regression')),
                    key=lambda c:(c['metrics'].get('numericalScore',{}).get('score',1e10),c['id']))
                best=sorted_candidates[0]['id'] if sorted_candidates else None
                plan=run['plan'] or {}
                pareto=pareto_report(viable,base,self.repo)
                output={'schemaVersion':'pitguard.agent-summary/3',
                        'baselineResultHash':run['sourceResultHash'],
                        'candidateCount':len(cand),'calculatedCount':len(viable),
                        'workingCopyEligibleCandidateIds':eligible,
                        'bestNumericalCandidateId':best,'roundSummaries':plan.get('roundSummaries',[]),
                        'pareto':pareto,
                        'evaluationBudget':run['maxEvaluations'],
                        'evaluationsUsed':plan.get('attempted',0),
                        'runtimeBudgetSeconds':run['maxRuntimeSeconds'],
                        'convergenceReason':plan.get('stopReason') or 'no_admissible_proposal',
                        'unresolvedEngineeringIssues':(run['diagnostics'] or {}).get('unresolvedCount'),
                        'engineeringQualified':False,'formalConstructionIssue':False,
                        'selectionRequiresCheckerDecision':True,
                        'note':'各候选为同一冻结问题的数值试算；参数依据重新确认和全部适用规范复核不得省略'}
                state='awaiting_review' if cand else 'completed_no_candidates'
                return self.store.advance(rid,owner,step,status=state,step='review' if cand else 'done',
                    progress=95 if cand else 100,summary=output,
                    event_key='summarized',event_type='candidate_comparison_completed',payload=output)
            raise ValueError('未知或不允许的Agent执行阶段')
        except Exception as exc:
            self.store.fail(rid,owner,type(exc).__name__+': '+str(exc))
            return self.store.get(rid)

    def process_until_idle(self,run_id,limit=50):
        for _ in range(limit):
            progressed=self.process_one(run_id)
            if progressed is None:break
            if progressed['status'] not in ('queued','running'):break
        return self.store.get(run_id)
