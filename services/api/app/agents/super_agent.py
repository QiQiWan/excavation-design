"""Super-agent 双层架构：常驻 Router + 8 个隔离 Worker。

参考 DeepSeek Harness《Super-agent 设计探索》(2026-10-31 开源
``dsh-superagent-prototype``，11-01 设计文章)。这里落地的是架构骨架，
不是把 9 个聊天窗口摆出来：

- Router：唯一对外入口（桌宠对话），只做事件准入、分派与统一回复，
  **不亲自执行工程任务**。当前为确定性实现，语义 Slow Path 只留接口。
- Worker：A1~A8 各一个专业角色，每个 Ticket 一个隔离上下文；Worker 不
  直接对人说话，结果由 Router 回写到会话。
- Trigger：环境事件；Fast Path 程序规则先筛，Slow Path 留给模型。
- Ticket + Mandate：授权范围创建时冻结，不可自我扩张。
- 订阅直通：同一 Thread 的后续事件直接进已有 Ticket，不再绕 Router。
- observe 默认：只记录「会派给谁」，不执行；显式 auto 才真正派单。
- Watchdog：巡检卡住或失败的任务，给恢复建议。

红线：正式施工图签发恒关闭；L2 采用必须人工；候选只写独立工作版；
模型不参与数值与规范判定。
"""
from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from . import design_basis_intake as intake
from . import orchestrator
from .catalog import AGENTS
from .contracts import StartRun
from .engine import AgentRuntime
from .model_gateway import configuration
from .store import AgentStore

SCHEMA = 'pitguard.super-agent/1'
MANDATE_SCHEMA = 'pitguard.agent-mandate/1'

# 环境事件白名单。未登记的类型在 Fast Path 直接丢弃，不进模型。
TRIGGER_KINDS: dict[str, dict[str, str]] = {
    'user_message': {'label': '工程师在桌宠输入', 'defaultRole': 'A1'},
    'document_uploaded': {'label': '上传资料文件', 'defaultRole': 'A1'},
    'design_input_changed': {'label': '设计输入变更', 'defaultRole': 'A1'},
    'calculation_failed': {'label': '计算验算失败', 'defaultRole': 'A6'},
    'run_completed': {'label': 'Agent 任务完成', 'defaultRole': 'A5'},
    'review_requested': {'label': '请求人工复核', 'defaultRole': 'A8'},
}

STAGE_OF_ROLE = {
    'A1': 'DESIGN.BASIS', 'A2': 'DESIGN.INPUT', 'A3': 'DESIGN.SCHEME',
    'A4': 'DESIGN.CONSTRUCTION', 'A5': 'DESIGN.CALCULATION',
    'A6': 'DESIGN.OPTIMIZATION', 'A7': 'DESIGN.REINFORCEMENT',
    'A8': 'DELIVERY.REVIEW',
}

# 8 个 Worker。implemented=True 的复用现有执行器，其余明确 blocked。
WORKERS: tuple[dict[str, Any], ...] = (
    {'id': 'A1', 'implemented': True, 'permissionCeiling': 'L1',
     'writeMode': 'design_basis_only', 'executor': 'design_basis_intake',
     'tools': ('get_project_context',)},
    {'id': 'A2', 'implemented': False, 'permissionCeiling': 'L0',
     'writeMode': 'read_only', 'executor': None, 'tools': ('get_project_context',)},
    {'id': 'A3', 'implemented': False, 'permissionCeiling': 'L1',
     'writeMode': 'isolated_candidate', 'executor': None,
     'tools': ('get_project_context', 'generate_scheme_candidates')},
    {'id': 'A4', 'implemented': False, 'permissionCeiling': 'L1',
     'writeMode': 'isolated_candidate', 'executor': None,
     'tools': ('get_calculation_findings',)},
    {'id': 'A5', 'implemented': True, 'permissionCeiling': 'L1',
     'writeMode': 'isolated_candidate', 'executor': 'agent_runtime',
     'tools': ('get_calculation_findings', 'run_stage_analysis',
               'evaluate_design_changes', 'generate_scheme_candidates')},
    {'id': 'A6', 'implemented': True, 'permissionCeiling': 'L1',
     'writeMode': 'isolated_candidate', 'executor': 'agent_runtime',
     'tools': ('get_calculation_findings', 'run_stage_analysis',
               'evaluate_design_changes', 'generate_scheme_candidates')},
    {'id': 'A7', 'implemented': False, 'permissionCeiling': 'L1',
     'writeMode': 'isolated_candidate', 'executor': None,
     'tools': ('get_calculation_findings',)},
    {'id': 'A8', 'implemented': False, 'permissionCeiling': 'L1',
     'writeMode': 'working_copy_only', 'executor': None,
     'tools': ('prepare_delivery_package',)},
)

WORKER_BY_ID = {row['id']: row for row in WORKERS}
ROLE_META = {row[0]: {'name': row[1], 'capability': row[2],
                      'implementation': row[3]} for row in AGENTS}

RED_LINES = (
    '正式施工图签发始终关闭，approve_formal_issue 不注册为工具',
    'L2 采用候选必须由授权人员在人工端点确认，Worker 不得自行采用',
    'Agent 只写独立候选 / 工作版，不改主项目或签发成果',
    '模型输出不参与数值计算与规范通过判定',
)


def _project_exists(project_id: str) -> bool:
    try:
        from app.storage.repository import ProjectRepository, shared_project_store

        ProjectRepository(
            store=shared_project_store(os.getenv('PITGUARD_DB_PATH') or None)
        ).require_workspace(project_id)
        return True
    except Exception:
        return False


# 桌宠意图分流：先看是不是参数录入，再看诊断/优化。全部确定性关键词，
# 不额外调模型，避免为一句普通提问付一次模型延迟。
_PARAM_HINTS = ('施工荷载', '地面超载', '地面堆载', '混凝土', '保护层', '钢筋',
                '等级', '重要性系数', '承载力', '水位', '高程', '土层')
_DIAGNOSE_HINTS = ('诊断', '哪里失败', '为什么失败', '为什么超限', '失败原因', '超限',
                   '不满足', '验算', '校核', '过不了', '没通过', '问题在哪')
_OPTIMIZE_HINTS = ('优化', '调整', '改一下', '改小', '改大', '试算', '复算', '重算',
                   '方案', '降低', '减小', '增大', '加粗', '加长')


def _route_role(kind: str, payload: dict) -> str | None:
    """把一句人话分给合适的 Worker；无法判断时交给总控的下一阶段。"""
    if kind != 'user_message':
        return None
    text = str(payload.get('text') or '').strip()
    if not text:
        return None
    if any(word in text for word in _PARAM_HINTS):
        return 'A1'
    if any(word in text for word in _OPTIMIZE_HINTS):
        return 'A6'
    if any(word in text for word in _DIAGNOSE_HINTS):
        return 'A5'
    return None


def _semantic_policy(kind: str, payload: dict, plan_next: dict) -> dict:
    """Slow Path 预留：模型未配置或尚未接线时如实回退确定性规则。"""
    config = configuration()
    if config.get('enabled') and config.get('configured'):
        return {
            'provider': 'reserved', 'used': False,
            'note': '模型策略尚未接线，本次仍由确定性规则决策（未伪造模型调用）',
        }
    return {
        'provider': 'deterministic', 'used': False,
        'note': 'PITGUARD_LLM_* 未配置，Fast Path 规则直接决策',
    }


def _mandate(*, project_id: str, stage_id: str, role_id: str,
             worker: dict, trigger: dict, actor: str, plan_next: dict) -> dict:
    """创建时冻结的授权书；dispatch 时只允许读取，不允许扩张。"""
    return {
        'schemaVersion': MANDATE_SCHEMA,
        'projectId': project_id,
        'stageId': stage_id,
        'roleId': role_id,
        'permissionCeiling': worker['permissionCeiling'],
        'writeMode': worker['writeMode'],
        'allowedTools': list(worker['tools']),
        'requiresHumanAdoption': True,
        'formalConstructionIssue': False,
        'sourceTriggerId': trigger['id'],
        'sourceSnapshotHash': trigger['payload'].get('sourceSnapshotHash'),
        'gateAtIssue': plan_next.get('gate', {}).get('type'),
        'issuedBy': actor,
        'immutable': True,
        'note': 'Ticket 生命周期内不得扩大工具范围、权限或写入模式',
    }


def blueprint() -> dict:
    """架构蓝图：给人和前端看，明确哪些 Worker 现在真能干活。"""
    workers = []
    for row in WORKERS:
        meta = ROLE_META[row['id']]
        workers.append({
            'id': row['id'], 'name': meta['name'], 'capability': meta['capability'],
            'stageId': STAGE_OF_ROLE[row['id']],
            'implementation': meta['implementation'],
            'executable': row['implemented'],
            'permissionCeiling': row['permissionCeiling'],
            'writeMode': row['writeMode'],
            'tools': list(row['tools']),
            'executor': row['executor'],
            'blockedReason': None if row['implemented'] else '执行链尚未建立，不伪造执行结果',
        })
    return {
        'schemaVersion': SCHEMA,
        'reference': {
            'name': 'DeepSeek Harness · Super-agent 设计探索',
            'prototype': 'deepseek-ai/dsh-superagent-prototype',
            'published': '2026-10-31',
            'note': '研究原型，不是正式产品；这里只采用其 Router+Worker 架构思想',
        },
        'router': {
            'role': '常驻唯一对外入口，只分派不执行',
            'entry': '桌面助手对话（单一会话）',
            'mode': 'deterministic',
            'defaultAcceptMode': 'observe',
            'modelSlowPath': 'reserved',
        },
        'workers': workers,
        'workerCount': len(workers),
        'executableWorkers': [w['id'] for w in workers if w['executable']],
        'triggers': [{'kind': key, **value} for key, value in TRIGGER_KINDS.items()],
        'mechanisms': {
            'fastPath': '程序规则：事件白名单、项目存在性、订阅直通、闸口准入',
            'slowPath': '语义策略预留，模型未配置时如实回退确定性规则',
            'subscriptionDirect': '同一 Thread 的后续 Trigger 直接进已有 Ticket',
            'mandateImmutable': '授权范围创建时冻结，dispatch 二次校验',
            'watchdog': '巡检超时/失败任务并给恢复建议',
            'unifiedReply': 'Worker 只向 Router 汇报，Router 统一回写会话',
        },
        'redLines': list(RED_LINES),
        'formalConstructionIssue': False,
    }


def ingest_trigger(*, project_id: str, kind: str, payload: dict | None = None,
                   conversation_id: str | None = None, thread_id: str | None = None,
                   accept_mode: str = 'observe', actor: str = 'system') -> dict:
    """Router 入口：一个环境事件 → 准入决策 → 可选的 Ticket。"""
    if accept_mode not in ('observe', 'auto'):
        raise ValueError('acceptMode 只能是 observe 或 auto')
    payload = dict(payload or {})
    for key, value in list(payload.items()):
        if isinstance(value, str) and len(value) > 2000:
            payload[key] = value[:2000]
    store = AgentStore()
    trigger_id = uuid4().hex

    def _record(decision: str, reason: str, ticket_id: str | None,
                policy: dict, extra: dict | None = None) -> dict:
        trigger = store.record_trigger(
            project_id=project_id, conversation_id=conversation_id,
            thread_id=thread_id, kind=kind, payload=payload,
            decision=decision, reason=reason,
            policy_provider=policy['provider'], ticket_id=ticket_id,
            trigger_id=trigger_id,
        )
        return {
            'schemaVersion': SCHEMA, 'decision': decision, 'reason': reason,
            'trigger': trigger, 'ticketId': ticket_id, 'policy': policy,
            'acceptMode': accept_mode, **(extra or {}),
            'formalConstructionIssue': False,
        }

    # --- Fast Path：纯程序规则，不消耗模型 ---------------------------------
    if kind not in TRIGGER_KINDS:
        return _record('ignored', f'未登记的 Trigger 类型：{kind}', None,
                       {'provider': 'deterministic', 'used': False})
    if not _project_exists(project_id):
        return _record('ignored', '项目不存在或状态不可用', None,
                       {'provider': 'deterministic', 'used': False})

    # 订阅直通：同 Thread 已有活跃 Ticket，事件直接投递，不再走 Router 决策。
    active = store.active_ticket_for_thread(project_id, thread_id)
    if active:
        store.append_ticket_event(active['id'], 'trigger_direct', {
            'kind': kind, 'payload': payload, 'via': 'subscription_direct',
        })
        return _record('direct', f"同一 Thread 直通 Ticket {active['id'][:12]}，未重复派单",
                       active['id'], {'provider': 'deterministic', 'used': False},
                       {'ticket': active, 'subscriptionDirect': True})

    plan = orchestrator.build_plan(project_id)
    plan_next = plan['next']
    policy = _semantic_policy(kind, payload, plan_next)

    requested_role = str(payload.get('roleId') or '')
    intent_role = _route_role(kind, payload)
    if requested_role and requested_role not in ('A1', 'auto'):
        # 调用方显式指定了 A1 以外的角色，尊重它。
        role_id = requested_role if requested_role in WORKER_BY_ID else plan_next['owner']
    elif intent_role:
        role_id = intent_role
    elif requested_role in WORKER_BY_ID:
        role_id = requested_role
    else:
        role_id = plan_next['owner']
    if role_id not in WORKER_BY_ID:
        role_id = TRIGGER_KINDS[kind]['defaultRole']
    worker = WORKER_BY_ID[role_id]
    stage_id = STAGE_OF_ROLE[role_id]

    mandate = _mandate(project_id=project_id, stage_id=stage_id, role_id=role_id,
                       worker=worker,
                       trigger={'id': trigger_id, 'payload': payload},
                       actor=actor, plan_next=plan_next)
    status = 'accepted' if accept_mode == 'auto' else 'observed'
    ticket = store.create_ticket(
        project_id=project_id, conversation_id=conversation_id,
        thread_id=thread_id, role_id=role_id,
        stage_id=stage_id, mandate=mandate, status=status,
        accept_mode=accept_mode,
        # 绑定项目版本：排队期间项目被改过就不执行，避免覆盖工程师的新修改。
        source_project_updated_at=intake.project_version(project_id, actor),
    )
    store.append_ticket_event(ticket['id'], 'ticket_created', {
        'roleId': role_id, 'stageId': stage_id, 'gate': mandate['gateAtIssue'],
        'acceptMode': accept_mode,
    })
    decision = 'accepted' if accept_mode == 'auto' else 'observed'
    reason = (f"Router 分派给 {role_id} · {ROLE_META[role_id]['name']}（{stage_id}）；"
              f"闸口 {mandate['gateAtIssue']}，{accept_mode} 模式")
    if not worker['implemented']:
        reason += '；该 Worker 执行链尚未建立'
    result = _record(decision, reason, ticket['id'], policy, {'ticket': ticket})
    if accept_mode == 'auto':
        result['execution'] = dispatch_ticket(
            ticket['id'], actor=actor, text=payload.get('text'),
            result_hash=payload.get('resultHash'),
            requested_tools=payload.get('requestedTools'),
            raw_fields=payload.get('fields'),
            source_label=payload.get('documentName'),
        )
    return result


def _reply_from_router(ticket: dict, kind: str, text: str, extra: dict) -> None:
    """Worker 不直接对人说话；由 Router 统一回写会话。"""
    conversation_id = ticket.get('conversationId')
    if not conversation_id:
        return
    store = AgentStore()
    try:
        store.get_conversation(conversation_id)
        store.append_message(
            conversation_id, role='tool', content=text,
            metadata={
                'answerKind': 'worker_report', 'ticketId': ticket['id'],
                'roleId': ticket['roleId'], 'stageId': ticket['stageId'],
                'eventKind': kind, 'engineeringJudgement': False,
                **extra,
            },
        )
    except (KeyError, ValueError):
        pass


def dispatch_ticket(ticket_id: str, *, actor: str,
                    text: str | None = None, result_hash: str | None = None,
                    requested_tools: list[str] | None = None,
                    raw_fields: list[dict] | None = None,
                    source_label: str | None = None) -> dict:
    """把一个 Ticket 交给对应 Worker 执行；越权或未实现都如实拒绝。"""
    store = AgentStore()
    ticket = store.get_ticket(ticket_id)
    mandate = ticket['mandate']
    worker = WORKER_BY_ID.get(ticket['roleId'])

    def _blocked(reason: str, status: str = 'blocked') -> dict:
        store.update_ticket(ticket_id, status=status)
        store.append_ticket_event(ticket_id, 'worker_blocked', {'reason': reason})
        _reply_from_router(ticket, 'worker_blocked',
                           f"任务未执行：{reason}", {'dispatched': False})
        return {'dispatched': False, 'ticketId': ticket_id,
                'roleId': ticket['roleId'], 'reason': reason,
                'status': status, 'formalConstructionIssue': False}

    bound_version = ticket.get('sourceProjectUpdatedAt')
    current_version = intake.project_version(ticket['projectId'], actor)
    if bound_version and current_version and bound_version != current_version:
        return _blocked('项目在任务排队期间已被修改，为避免覆盖新改动，请重新提交', 'stale')

    if ticket['status'] == 'observed':
        return _blocked('observe 模式只记录会派给谁，不执行；需以 auto 重新提交', 'observed')
    if ticket['status'] in ('done', 'waiting_review'):
        return _blocked(f"Ticket 已处于 {ticket['status']}，不重复执行", ticket['status'])
    if mandate.get('formalConstructionIssue') is not False:
        return _blocked('授权书不符合正式签发关闭约束')
    if worker is None:
        return _blocked('未登记的 Worker')

    requested = list(requested_tools or [])
    unknown = sorted(set(requested) - set(mandate['allowedTools']))
    if unknown:
        return _blocked('授权书不允许的工具：' + '、'.join(unknown))
    if not worker['implemented']:
        return _blocked(f"{ticket['roleId']} 执行链尚未建立，不伪造执行结果")

    store.append_ticket_event(ticket_id, 'worker_started', {
        'executor': worker['executor'], 'requestedTools': requested,
    })

    try:
        if worker['executor'] == 'design_basis_intake':
            outcome = intake.ingest(
                project_id=ticket['projectId'], actor=actor, text=text,
                raw_fields=raw_fields, conversation_id=ticket.get('conversationId'),
                source_label=source_label,
            )
            summary = (f"A1 已处理输入：{outcome['mode']}，"
                       f"填入 {len(outcome['applied'])} 项；{outcome['remaining']['text']}。")
            run_id = None
            next_status = 'waiting_review'
        elif worker['executor'] == 'agent_runtime':
            chosen = result_hash
            if not chosen:
                frozen = [row for row in orchestrator._frozen_results(ticket['projectId'])
                          if row['launchable']]
                chosen = frozen[0]['resultHash'] if frozen else None
            if not chosen:
                return _blocked('没有可启动的冻结单剖面结果；'
                                '请先在单剖面工作台完成一次计算并冻结结果')
            run = AgentRuntime().create(
                StartRun(result_hash=chosen,
                         conversation_id=ticket.get('conversationId')),
                actor=actor,
            )
            outcome = {'mode': 'run_created', 'run': {
                'id': run['id'], 'status': run['status'], 'step': run['step']}}
            run_id = run['id']
            role_name = ROLE_META[ticket['roleId']]['name']
            summary = (f"{ticket['roleId']} {role_name} 已受理：任务 {run['id'][:12]} "
                       f"在后台执行（{run['step']}），诊断和候选复算结果会陆续回到这里；"
                       f"采用候选需在人工复核端点确认。")
            next_status = 'waiting_review'
        else:
            return _blocked('执行器未登记')
    except Exception as exc:
        return _blocked(f"执行器返回错误：{type(exc).__name__}: {str(exc)[:160]}", 'failed')

    store.append_ticket_event(ticket_id, 'worker_completed', {
        'executor': worker['executor'], 'outcome': outcome,
    })
    store.update_ticket(ticket_id, status=next_status, worker_run_id=run_id)
    _reply_from_router(ticket, 'worker_completed', summary, {
        'dispatched': True, 'runId': run_id,
    })
    return {
        'dispatched': True, 'ticketId': ticket_id, 'roleId': ticket['roleId'],
        'executor': worker['executor'], 'status': next_status,
        'runId': run_id, 'outcome': outcome,
        'formalConstructionIssue': False,
    }


def tickets(*, project_id: str | None = None, limit: int = 30) -> list[dict]:
    return AgentStore().list_tickets(project_id=project_id, limit=limit)


def ticket_detail(ticket_id: str) -> dict:
    store = AgentStore()
    ticket = store.get_ticket(ticket_id)
    return {'ticket': ticket, 'events': store.ticket_events(ticket_id),
            'formalConstructionIssue': False}


def watchdog(project_id: str) -> dict:
    """外部看门狗视角：巡检卡住的任务与停滞的 Ticket。"""
    runs = AgentStore().list(project_id=project_id, limit=10)
    stalled_tickets = []
    for ticket in AgentStore().list_tickets(project_id=project_id, limit=30):
        if ticket['status'] in ('accepted', 'running'):
            age = orchestrator._age_seconds(ticket['updatedAt'])
            if age > 300:
                stalled_tickets.append({
                    'ticketId': ticket['id'], 'roleId': ticket['roleId'],
                    'status': ticket['status'], 'ageSeconds': round(age, 1),
                    'action': 'inspect_then_retry',
                })
    return {
        'schemaVersion': 'pitguard.super-agent-watchdog/1',
        'projectId': project_id,
        'runRecovery': orchestrator._recovery(runs),
        'stalledTickets': stalled_tickets,
        'formalConstructionIssue': False,
    }
