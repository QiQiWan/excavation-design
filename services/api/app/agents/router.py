"""Authenticated, fail-closed Agent workbench API. No direct model-driven tool endpoint."""
from __future__ import annotations
from fastapi import APIRouter,HTTPException,Request,Query
from .contracts import StartRun,RunDecision,CandidateEvidenceReview
from .store import AgentStore,ConflictError
from .catalog import catalog
from .engine import AgentRuntime
from .model_gateway import configuration
from app.services.access_control import ROLE_RANK

router=APIRouter(prefix='/api/agents',tags=['engineering-agent-v388'])


def _actor(request:Request):
    identity=getattr(request.state,'pitguard_identity',None)
    if identity is None:raise HTTPException(401,detail='必须登录')
    return identity


def _check_run(request:Request,run_id:str):
    run=AgentStore().get(run_id)
    identity=_actor(request)
    if run['actor']!=identity.actor and ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='无权访问其他工程师的Agent任务')
    return run


@router.get('/catalog')
def get_agent_catalog():
    return catalog()


@router.get('/model/configuration')
def model_configuration():
    return configuration()


@router.post('/runs',status_code=201)
def create_agent_run(body:StartRun,request:Request):
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='Agent任务要求设计人员权限')
    try:return AgentRuntime().create(body,actor=identity.actor)
    except FileNotFoundError as exc:raise HTTPException(404,detail='原封存计算结果不存在') from exc
    except (ValueError,ConflictError) as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.get('/runs')
def list_agent_runs(request:Request,project_id:str=Query(min_length=1,max_length=100)):
    identity=_actor(request)
    rows=AgentStore().list(project_id=project_id)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        rows=[r for r in rows if r['actor']==identity.actor]
    return {'runs':rows,'formalConstructionIssue':False}


@router.get('/runs/{run_id}')
def get_agent_run(run_id:str,request:Request):
    return _check_run(request,run_id)


@router.get('/runs/{run_id}/events')
def agent_events(run_id:str,request:Request):
    _check_run(request,run_id)
    return {'events':AgentStore().events(run_id)}


@router.get('/runs/{run_id}/candidates')
def agent_candidates(run_id:str,request:Request):
    _check_run(request,run_id)
    return {'candidates':AgentStore().candidates(run_id)}


@router.get('/runs/{run_id}/overview')
def agent_overview(run_id:str,request:Request):
    run=_check_run(request,run_id)
    store=AgentStore()
    candidate_rows=store.candidates(run_id)
    # File-byte revalidation happens at adoption, never silently during poll.
    for candidate in candidate_rows:
        if candidate['status']=='calculated' and candidate.get('resultHash'):
            candidate['evidenceGate']=store.candidate_evidence(run_id,candidate['id'])
    return {'run':run,'candidates':candidate_rows,
            'events':store.events(run_id),'selection':store.selection(run['projectId'],run['sectionId']),
            'formalConstructionIssue':False}


@router.get('/runs/{run_id}/selection')
def agent_selection(run_id:str,request:Request):
    run=_check_run(request,run_id)
    return AgentStore().selection(run['projectId'],run['sectionId'])


@router.post('/runs/{run_id}/advance')
def advance_agent_run(run_id:str,request:Request):
    run=_check_run(request,run_id)
    if ROLE_RANK.get(_actor(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='只有设计人员可以启动Agent运行')
    try:
        result=AgentRuntime().process_one(run_id)
        return result or AgentStore().get(run_id)
    except (ValueError,ConflictError) as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.post('/runs/{run_id}/pause')
def pause_agent_run(run_id:str,request:Request):
    _check_run(request,run_id)
    if ROLE_RANK.get(_actor(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='缺少暂停权限')
    try:return AgentStore().pause(run_id)
    except ConflictError as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.post('/runs/{run_id}/cancel')
def cancel_agent_run(run_id:str,request:Request):
    _check_run(request,run_id)
    if ROLE_RANK.get(_actor(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='缺少取消权限')
    return AgentStore().cancel(run_id)


@router.post('/runs/{run_id}/resume')
def resume_agent_run(run_id:str,request:Request):
    _check_run(request,run_id)
    if ROLE_RANK.get(_actor(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='缺少恢复权限')
    try:return AgentStore().resume(run_id)
    except ConflictError as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.post('/runs/{run_id}/decision')
def decide_agent_run(run_id:str,body:RunDecision,request:Request):
    _check_run(request,run_id)
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='候选采用需要具备校审权限的工程师确认')
    if body.decision=='adopt_for_working_copy' and not body.candidate_id:
        raise HTTPException(422,detail='采用操作必须指定候选ID')
    try:
        return AgentStore().decide(run_id,candidate_id=body.candidate_id,decision=body.decision,
               expected_snapshot_hash=body.expected_active_snapshot_hash,
               acknowledgement=body.acknowledgement,comment=body.comment,reviewer=identity.actor)
    except (ValueError,ConflictError) as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.get('/runs/{run_id}/candidates/{candidate_id}/evidence')
def read_candidate_evidence(run_id:str,candidate_id:str,request:Request):
    _check_run(request,run_id)
    try:return AgentStore().candidate_evidence(run_id,candidate_id)
    except (KeyError,ValueError,FileNotFoundError) as exc:
        raise HTTPException(404,detail='候选或封存工程结果不存在') from exc


@router.post('/runs/{run_id}/candidates/{candidate_id}/evidence')
def record_candidate_evidence(run_id:str,candidate_id:str,body:CandidateEvidenceReview,request:Request):
    _check_run(request,run_id)
    actor=_actor(request)
    if ROLE_RANK.get(actor.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='来源重新确认需要校审角色，模型和设计角色无权批准')
    try:
        return AgentStore().register_evidence(run_id,candidate_id,field_path=body.field_path,
             relative_path=body.source_relative_path,expected_sha256=body.source_sha256,
             declared_value=body.declared_value,source_reference=body.source_reference,
             method_basis=body.method_basis,reviewer=actor.actor)
    except (KeyError,ValueError,ConflictError,FileNotFoundError) as exc:
        raise HTTPException(409,detail=str(exc)) from exc


@router.get('/runs/{run_id}/normative-checks')
def agent_normative_checks(run_id:str,request:Request):
    """M5 check/evidence register backed by a real frozen solver result."""
    from app.engineering.single_braced_pile.repository import Repository
    from .normative_contract import build_check_contract
    run=_check_run(request,run_id)
    try:return build_check_contract(Repository().load(run['sourceResultHash']))
    except (FileNotFoundError,ValueError) as exc:
        raise HTTPException(409,detail='工程计算结果或规范合同不可用：'+str(exc)) from exc


@router.get('/runs/{run_id}/constraints')
def agent_constraint_register(run_id:str,request:Request):
    """M6 feasibility explicitly includes unknown normative requirements."""
    from app.engineering.single_braced_pile.repository import Repository
    from .constraints import evaluate_constraints
    run=_check_run(request,run_id)
    try:return evaluate_constraints(Repository().load(run['sourceResultHash']))
    except (FileNotFoundError,ValueError) as exc:
        raise HTTPException(409,detail='工程约束不可用：'+str(exc)) from exc


@router.get('/runs/{run_id}/pareto')
def agent_pareto(run_id:str,request:Request):
    """Review candidates in original source-relative objective units/proxies."""
    from app.engineering.single_braced_pile.repository import Repository
    from .pareto import pareto_report
    run=_check_run(request,run_id)
    repo=Repository()
    try:return pareto_report(AgentStore().candidates(run_id),repo.load(run['sourceResultHash']),repo)
    except (FileNotFoundError,ValueError) as exc:
        raise HTTPException(409,detail='Pareto计算结果不可用：'+str(exc)) from exc
