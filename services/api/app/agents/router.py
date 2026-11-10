"""Authenticated, fail-closed Agent workbench API. No direct model-driven tool endpoint."""
from __future__ import annotations
from fastapi import APIRouter,HTTPException,Request,Query,UploadFile,File
import json
from datetime import datetime,timezone
from .contracts import StartRun,RunDecision,ConversationCreate,MessageCreate,ToolInvoke,OrchestratorDispatch
from .store import AgentStore,ConflictError
from .catalog import catalog
from .engine import AgentRuntime
from .model_gateway import configuration
from . import assistant
from . import document_agent
from . import tools as tool_registry
from . import orchestrator
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


def _check_conversation(request:Request,conversation_id:str):
    identity=_actor(request)
    store=AgentStore()
    try:conversation=store.get_conversation(conversation_id)
    except KeyError as exc:raise HTTPException(404,detail='会话不存在') from exc
    if conversation['actor']!=identity.actor and ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='无权访问其他工程师的会话')
    return conversation


@router.get('/catalog')
def get_agent_catalog():
    return catalog()


@router.get('/model/configuration')
def model_configuration():
    return configuration()


@router.get('/roles')
def agent_roles():
    """The eight professional roles and whether each one can run today."""
    return {'roles': assistant.role_roster(),'formalConstructionIssue':False}


@router.get('/assistant/status')
def assistant_project_status(request:Request,project_id:str=Query(min_length=1,max_length=100)):
    """Lightweight project state for the assistant's unattended notices."""
    _actor(request)
    return {'digest':assistant.project_digest(project_id),'formalConstructionIssue':False}


@router.get('/frozen-results')
def list_frozen_results(request:Request):
    """Read-only list of frozen single-strut results a pilot run may start from.

    A project+section holds one active working selection. Results from another
    snapshot are rejected by AgentStore.create, so mark them up front instead
    of offering a choice that can never start.
    """
    if ROLE_RANK.get(_actor(request).role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看冻结结果要求设计人员权限')
    from app.engineering.single_braced_pile.repository import Repository
    store=AgentStore()
    folder=Repository().root/'results'
    rows=[]
    if folder.is_dir():
        files=sorted((p for p in folder.glob('*.json') if len(p.stem)==64),
                     key=lambda p:p.stat().st_mtime,reverse=True)[:20]
        for path in files:
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                pr,sec=data.get('projectId'),data.get('sectionId')
                try:
                    active=store.selection(pr,sec)
                    launchable=active['snapshotHash']==data.get('snapshotHash')
                except KeyError:
                    launchable=True  # first run for this section creates the selection
                rows.append({'resultHash':path.stem,
                    'projectId':pr,'sectionId':sec,
                    'analysisStatus':data.get('analysisStatus'),
                    'launchable':launchable,
                    'createdAt':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()})
            except (OSError,ValueError,TypeError):
                continue
    rows.sort(key=lambda r:not r['launchable'])  # stable: newest first within each group
    return {'results':rows,'formalConstructionIssue':False}


@router.post('/documents/extract')
async def extract_document(request:Request,file:UploadFile=File(...)):
    """A1 资料与参数：读 PDF、给设计基准候选与页码依据，不写任何项目数据。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='文档提取要求设计人员权限')
    data=await file.read()
    try:
        return document_agent.extract(file.filename or 'document.pdf',data)
    except ValueError as exc:
        raise HTTPException(400,detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422,detail='文档解析失败：'+type(exc).__name__) from exc


@router.get('/tools')
def list_agent_tools(request:Request):
    """Registered Agent tools with permission grades and audit policy."""
    _actor(request)
    return {**tool_registry.registry(),'formalConstructionIssue':False}


@router.post('/tools/{tool_name}/invoke')
def invoke_agent_tool(tool_name:str,body:ToolInvoke,request:Request):
    """Run one registered tool. L0/L1 only; L2 must go through human decision."""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='调用Agent工具要求设计人员权限')
    try:
        return tool_registry.invoke(tool_name,project_id=body.projectId,
                                    actor=identity.actor,params=body.params)
    except KeyError:
        raise HTTPException(404,detail='工具不存在') from None
    except PermissionError as exc:
        raise HTTPException(403,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400,detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409,detail=type(exc).__name__+': '+str(exc)[:180]) from exc


@router.get('/tool-calls')
def list_tool_calls(request:Request,limit:int=Query(default=50,ge=1,le=200)):
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看工具调用记录要求设计人员权限')
    return {'calls':AgentStore().tool_calls(limit=limit),'formalConstructionIssue':False}


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
    return {'run':run,'candidates':store.candidates(run_id),
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


# --------------------------------------------------------------------------- #
# Assistant sessions.  A conversation records the designer's turns and the
# engineering objects those turns referenced.  It carries no engineering
# authority: nothing here can adopt a candidate or mark a check as passed.
# --------------------------------------------------------------------------- #

@router.post('/conversations',status_code=201)
def create_conversation(body:ConversationCreate,request:Request):
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='开启会话要求设计人员权限')
    return AgentStore().create_conversation(project_id=body.project_id,actor=identity.actor,
                                             title=body.title)


@router.get('/conversations')
def list_conversations(request:Request,project_id:str=Query(min_length=1,max_length=100)):
    identity=_actor(request)
    store=AgentStore()
    rows=store.list_conversations(project_id=project_id)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        rows=[r for r in rows if r['actor']==identity.actor]
    return {'conversations':rows,'formalConstructionIssue':False}


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id:str,request:Request):
    conversation=_check_conversation(request,conversation_id)
    store=AgentStore()
    return {'conversation':conversation,'messages':store.messages(conversation_id),
            'runs':store.runs_for_conversation(conversation_id),
            'formalConstructionIssue':False}


@router.get('/conversations/{conversation_id}/runs')
def list_conversation_runs(conversation_id:str,request:Request):
    """Runs linked to this assistant session (read-only)."""
    _check_conversation(request,conversation_id)
    return {'runs':AgentStore().runs_for_conversation(conversation_id),
            'formalConstructionIssue':False}


@router.get('/conversations/{conversation_id}/messages')
def list_conversation_messages(conversation_id:str,request:Request):
    _check_conversation(request,conversation_id)
    return {'messages':AgentStore().messages(conversation_id)}


@router.post('/conversations/{conversation_id}/messages',status_code=201)
def post_conversation_message(conversation_id:str,body:MessageCreate,request:Request):
    conversation=_check_conversation(request,conversation_id)
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='发送消息要求设计人员权限')
    store=AgentStore()
    try:
        message=store.append_message(conversation_id,role=body.role,content=body.content,
                                     metadata=body.metadata)
        reply=assistant.respond(conversation,body.content,metadata=body.metadata)
    except (ValueError,KeyError) as exc:raise HTTPException(409,detail=str(exc)) from exc
    return {'message':message,'reply':reply,'formalConstructionIssue':False}


@router.get('/orchestrator/plan')
def orchestrator_plan(request:Request,project_id:str=Query(min_length=1,max_length=100),
                      refresh:bool=False):
    """Six-stage scheduling plan. Read-only: it never starts or changes a run."""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看调度计划要求设计人员权限')
    try:
        return orchestrator.build_plan(project_id,refresh=refresh)
    except KeyError as exc:raise HTTPException(404,detail='项目不存在或状态不可用') from exc


@router.post('/orchestrator/dispatch')
def orchestrator_dispatch(body:OrchestratorDispatch,request:Request):
    """Start the next stage only when its gates are open; otherwise explain why not."""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='派单要求设计人员权限')
    try:
        return orchestrator.dispatch(
            body.project_id,actor=identity.actor,conversation_id=body.conversation_id,
            result_hash=body.result_hash,max_candidates=body.max_candidates,
            client_request_id=body.client_request_id)
    except KeyError as exc:raise HTTPException(404,detail='项目或会话不存在') from exc
    except ValueError as exc:raise HTTPException(409,detail=str(exc)) from exc


@router.get('/orchestrator/history')
def orchestrator_history(request:Request,project_id:str|None=None,limit:int=30):
    """Scheduling verdicts already recorded, newest first."""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看调度记录要求设计人员权限')
    rows=orchestrator.history(project_id=project_id,limit=limit)
    return {'schemaVersion':'pitguard.orchestration-history/1','decisions':rows,
            'count':len(rows),'formalConstructionIssue':False}
