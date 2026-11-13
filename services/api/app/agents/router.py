"""Authenticated, fail-closed Agent workbench API. No direct model-driven tool endpoint."""
from __future__ import annotations
from fastapi import APIRouter,HTTPException,Request,Query,UploadFile,File,Form
import json
from datetime import datetime,timezone
from .contracts import (StartRun,RunDecision,ConversationCreate,MessageCreate,
    ToolInvoke,OrchestratorDispatch,SuperTrigger,SuperDispatch,FindingDecision)
from .store import AgentStore,ConflictError
from .catalog import catalog
from .engine import AgentRuntime
from .model_gateway import configuration
from . import assistant
from . import document_agent
from . import tools as tool_registry
from . import orchestrator
from . import design_basis_intake as intake
from . import super_agent
from . import findings
from . import context as engineering_context
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


@router.post('/design-basis/ingest')
async def ingest_design_basis(request:Request,project_id:str=Form(...),
                              text:str|None=Form(None),
                              conversation_id:str|None=Form(None),
                              provider:str=Form('auto'),
                              file:UploadFile|None=File(None)):
    """一段话或一份文件 → 自动填入设计基准 → 返回还缺什么。

    模型接入点已预留：provider=model 且未配置 API 时明确回退规则，不伪造输出。
    """
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='设计基准录入要求设计人员权限')
    if provider not in ('auto','rules','model'):
        raise HTTPException(400,detail='provider 只能是 auto / rules / model')
    if conversation_id:
        try:
            conversation=AgentStore().get_conversation(conversation_id)
        except KeyError as exc:raise HTTPException(404,detail='会话不存在') from exc
        if conversation['projectId']!=project_id:
            raise HTTPException(409,detail='会话与项目不匹配')

    filename=None;data=None;raw_fields=None
    if file is not None:
        filename=file.filename or 'document.pdf'
        data=await file.read()
        if filename.lower().endswith('.json'):
            try:
                raw_fields=intake.parse_extraction_json(data.decode('utf-8'))
            except (ValueError,UnicodeDecodeError) as exc:
                raise HTTPException(400,detail='提取结果 JSON 解析失败：'+str(exc)[:120]) from exc
            data=None
    if not text and data is None and raw_fields is None:
        raise HTTPException(400,detail='需要提供一段文本、一份 PDF 或提取结果 JSON')
    try:
        return intake.ingest(project_id=project_id,actor=identity.actor,text=text,
                             filename=filename,data=data,raw_fields=raw_fields,
                             conversation_id=conversation_id,provider_requested=provider)
    except ValueError as exc:
        raise HTTPException(400,detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404,detail='项目不存在') from exc


@router.get('/design-basis/missing')
def design_basis_missing(request:Request,project_id:str=Query(min_length=1,max_length=100)):
    """当前设计基准还缺哪些字段，用于桌宠的常驻提示。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看设计基准缺项要求设计人员权限')
    try:
        return intake.missing(project_id,actor=identity.actor)
    except KeyError as exc:raise HTTPException(404,detail='项目不存在') from exc


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


@router.get('/evidence')
def list_evidence(request:Request,project_id:str=Query(min_length=1,max_length=100),
                  limit:int=Query(default=100,ge=1,le=500)):
    """参数来源追溯：每个自动写入的设计基准值来自哪句话 / 哪一页 / 谁写入。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看参数来源要求设计人员权限')
    return {'schemaVersion':'pitguard.evidence/1',
            'records':AgentStore().evidence(project_id=project_id,limit=limit),
            'formalConstructionIssue':False}


@router.get('/model-calls')
def list_model_calls(request:Request,project_id:str|None=None,
                     limit:int=Query(default=50,ge=1,le=200)):
    """模型调用审计：模型 ID、耗时、成功/回退、重试次数、token 用量。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看模型调用记录要求设计人员权限')
    return {'calls':AgentStore().model_calls(project_id=project_id,limit=limit),
            'formalConstructionIssue':False}


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
    conversation=_check_conversation(request,conversation_id)
    # 后台任务结束时把结果写回会话；同步失败不能挡住读消息。
    task_sync_error=None
    try:
        super_agent.sync_task_tickets(project_id=conversation['projectId'])
    except Exception as exc:
        task_sync_error=f'{type(exc).__name__}: {str(exc)[:120]}'
    payload={'messages':AgentStore().messages(conversation_id)}
    if task_sync_error:
        payload['taskSyncError']=task_sync_error
    return payload


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


@router.get('/super/blueprint')
def super_agent_blueprint(request:Request):
    """Router + 8 Workers 的架构蓝图与可执行状态。"""
    _actor(request)
    return super_agent.blueprint()


@router.post('/super/triggers')
def super_agent_trigger(body:SuperTrigger,request:Request):
    """Router 入口：环境事件准入、分派、observe/auto 决策。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='提交 Trigger 要求设计人员权限')
    if body.conversationId:
        try:
            conversation=AgentStore().get_conversation(body.conversationId)
        except KeyError as exc:raise HTTPException(404,detail='会话不存在') from exc
        if conversation['projectId']!=body.projectId:
            raise HTTPException(409,detail='会话与项目不匹配')
    try:
        return super_agent.ingest_trigger(
            project_id=body.projectId,kind=body.kind,payload=body.payload,
            conversation_id=body.conversationId,thread_id=body.threadId,
            accept_mode=body.acceptMode,actor=identity.actor)
    except ValueError as exc:raise HTTPException(400,detail=str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,detail='项目不存在') from exc


@router.get('/context')
def agent_context(request:Request,project_id:str):
    """显式项目上下文：所有 Agent 读同一份对象与哈希。"""
    _actor(request)
    try:
        return engineering_context.build(project_id)
    except KeyError as exc:
        raise HTTPException(404,detail='项目不存在') from exc


@router.get('/findings')
def agent_findings(request:Request,project_id:str,status:str|None=None):
    """项目级发现与建议动作；默认返回全部，可按状态过滤。"""
    _actor(request)
    if status not in (None,'open','acknowledged','resolved'):
        raise HTTPException(422,detail='status 只能是 open/acknowledged/resolved')
    return findings.overview(project_id,status=status)


@router.post('/actions/{action_id}/decision')
def agent_action_decision(action_id:str,body:FindingDecision,request:Request):
    """人工接受/拒绝建议动作；不改工程，采用候选另走 runs decision。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['checker']:
        raise HTTPException(403,detail='建议动作的接受/拒绝需要具备校审权限')
    try:
        return findings.decide(action_id,decision=body.decision,
                               actor=identity.actor,note=body.note)
    except KeyError as exc:
        raise HTTPException(404,detail='建议动作不存在') from exc
    except ConflictError as exc:
        raise HTTPException(409,detail=str(exc)) from exc


@router.get('/super/tickets')
def super_agent_tickets(request:Request,project_id:str|None=None,limit:int=30):
    _actor(request)
    return {'schemaVersion':super_agent.SCHEMA,
            'tickets':super_agent.tickets(project_id=project_id,limit=limit),
            'formalConstructionIssue':False}


@router.get('/super/tickets/{ticket_id}')
def super_agent_ticket_detail(ticket_id:str,request:Request):
    _actor(request)
    try:return super_agent.ticket_detail(ticket_id)
    except KeyError as exc:raise HTTPException(404,detail='Ticket 不存在') from exc


@router.post('/super/tickets/{ticket_id}/dispatch')
def super_agent_dispatch(ticket_id:str,body:SuperDispatch,request:Request):
    """把 Ticket 交给 Worker；observe 模式、越权工具、未实现角色一律拒绝。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='派单要求设计人员权限')
    try:
        return super_agent.dispatch_ticket(
            ticket_id,actor=identity.actor,text=body.text,
            result_hash=body.resultHash,requested_tools=body.requestedTools)
    except KeyError as exc:raise HTTPException(404,detail='Ticket 不存在') from exc


@router.get('/super/watchdog')
def super_agent_watchdog(request:Request,project_id:str=Query(min_length=1,max_length=100)):
    """巡检卡住或失败的任务，给恢复建议。"""
    identity=_actor(request)
    if ROLE_RANK.get(identity.role,0)<ROLE_RANK['designer']:
        raise HTTPException(403,detail='查看巡检结果要求设计人员权限')
    return super_agent.watchdog(project_id)
