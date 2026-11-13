"""V3.91 连续执行链：按六步顺序推进，遇到人工闸口就停，确认后从原地继续。

设计边界：

- 不跳过闸口。设计基准确认、方案生成、深化、成果包、候选采用都必须人工点头；
- 不伪造任务结果。任务失败就停在失败步骤并给出真实原因；
- 不批量确认。``confirm=True`` 只作用于当前停住的那一个闸口，不会顺带确认后面的；
- 不改工程数据本身。真正写库仍由各 Worker 的既有入口执行，链只负责按顺序调用。

状态机：``waiting_human`` → ``waiting_run`` / ``waiting_task`` → ``done`` /
``blocked`` / ``aborted``。每一步都写进 ``agent_flows.steps_json``。
"""
from __future__ import annotations

from typing import Any, Callable

from . import delivery_agent, detailing_agent, orchestrator, scheme_stage_agent
from .design_basis_intake import _repository
from .store import AgentStore

SCHEMA = 'pitguard.chain/1'

_TERMINAL_FLOW = ('done', 'aborted')
_ACTIVE_RUN = ('queued', 'running', 'paused')
_TASK_TERMINAL = ('success', 'failed', 'cancelled')

# 只有这些动作允许链在人工确认后自动提交；其余闸口必须人去页面完成。
_ACTIONS: dict[str, Callable[[str, str], dict]] = {
    'generate_scheme': scheme_stage_agent.generate_scheme,
    'generate_detailing': detailing_agent.generate,
    'prepare_delivery': lambda pid, actor: delivery_agent.prepare(pid, actor, '出成果包'),
}


def _task_status(task_id: str) -> tuple[str, str]:
    """返回 (状态, 说明)；任务记录读不到时不假装成功。"""
    from app.tasks.manager import task_manager

    try:
        task = task_manager.get(task_id)
    except Exception as exc:  # 任务存储不可用时如实退回
        return 'unknown', f'{type(exc).__name__}: {str(exc)[:100]}'
    if task is None:
        return 'unknown', '任务记录不存在（可能已被清理）'
    reason = ''
    if getattr(task, 'status', '') == 'failed':
        reason = str(getattr(task, 'error', '') or '')[:200]
    return str(getattr(task, 'status', '')), reason


def _run_status(run_id: str) -> tuple[str, str]:
    try:
        run = AgentStore().get(run_id)
    except KeyError:
        return 'missing', '运行记录不存在'
    except Exception as exc:
        return 'unknown', f'{type(exc).__name__}: {str(exc)[:100]}'
    return str(run.get('status')), str(run.get('error') or '')[:200]


def _step_record(stage: dict, previous: dict | None = None) -> dict:
    record = dict(previous or {})
    record.update({
        'stageKey': stage['key'], 'stageId': stage['stageId'],
        'title': stage['title'], 'owner': stage['owner'],
    })
    return record


def _summary(status: str, stage: dict | None, note: str | None,
             done: int, total: int) -> str:
    where = f"「{stage['title']}」" if stage else ''
    if status == 'done':
        return f'连续执行完成：{total} 个环节全部就绪。'
    if status == 'waiting_human':
        return f'当前停在{where}：{note} 需要工程师确认后继续。'
    if status == 'waiting_run':
        return f'{where}已派单，正在运行；完成后再次推进即可接着走。'
    if status == 'waiting_task':
        return f'{where}任务已提交，正在执行；完成后再次推进即可接着走。'
    if status == 'blocked':
        return f'{where}被阻断：{note}'
    return f'已推进 {done}/{total} 个环节。'


def _walk(flow: dict, actor: str, *, confirm: bool) -> dict:
    project_id = flow['projectId']
    conversation_id = flow.get('conversationId')
    steps: dict[str, Any] = dict(flow.get('steps') or {})
    plan = orchestrator.build_plan(project_id, refresh=True)
    stages = list(plan.get('stages') or [])
    total = len(stages)

    status = 'running'
    current: dict | None = None
    note = ''
    done = 0

    for stage in stages:
        key = stage['key']
        gate = stage.get('gate') or {}
        gate_type = gate.get('type')
        record = _step_record(stage, steps.get(key))

        # 上一次派出的运行时任务：先看它结束没有。
        if record.get('runId'):
            run_state, run_error = _run_status(str(record['runId']))
            if run_state in _ACTIVE_RUN:
                record['state'] = 'running'
                steps[key] = record
                status, current = 'waiting_run', stage
                note = f"任务 {record['runId'][:12]} 处于 {run_state}。"
                break
            if run_state in ('missing', 'unknown'):
                record['state'] = 'blocked'
                record['note'] = f'无法确认运行状态：{run_error}'
                steps[key] = record
                status, current, note = 'blocked', stage, record['note']
                break
            # 运行已结束：保留 lastRunId 作为证据，交给阶段投影判断是否真的完成。
            record['lastRunId'] = record.pop('runId')
            if stage.get('state') != 'done':
                record['state'] = 'blocked'
                record['note'] = (f"上次运行 {str(record['lastRunId'])[:12]} 已结束，但该环节仍未就绪："
                                  + str(stage.get('message') or '请检查工作台。'))
                steps[key] = record
                status, current, note = 'blocked', stage, record['note']
                break

        # 上一次提交的后台任务：同样先看终态。
        if record.get('taskId'):
            task_state, task_error = _task_status(str(record['taskId']))
            if task_state not in _TASK_TERMINAL and task_state != 'unknown':
                record['state'] = 'running'
                steps[key] = record
                status, current = 'waiting_task', stage
                note = f"任务 {record['taskId']} 仍在执行（{task_state}）。"
                break
            if task_state == 'failed':
                record['state'] = 'failed'
                record['note'] = f'任务失败：{task_error or "原因未记录"}'
                steps[key] = record
                status, current, note = 'blocked', stage, record['note']
                break
            if task_state == 'unknown':
                record['state'] = 'blocked'
                record['note'] = f'无法确认任务状态：{task_error}'
                steps[key] = record
                status, current, note = 'blocked', stage, record['note']
                break
            # success：如果阶段仍没就绪，不重复提交，停在人工处并说明。
            if stage.get('state') != 'done' and task_state == 'success':
                record['state'] = 'waiting_human'
                record['note'] = ('任务已完成，但该环节尚未就绪：'
                                  + str(stage.get('message') or '请检查工作台。'))
                steps[key] = record
                status, current, note = 'waiting_human', stage, record['note']
                break

        if stage.get('state') == 'done':
            record['state'] = 'done'
            steps[key] = record
            done += 1
            continue

        if gate_type == 'upstream_incomplete':
            record['state'] = 'blocked'
            record['note'] = str(gate.get('reason') or '上一步尚未完成')
            steps[key] = record
            status, current, note = 'blocked', stage, record['note']
            break

        if gate_type == 'auto_dispatch':
            result = orchestrator.dispatch(
                project_id, actor=actor, conversation_id=conversation_id,
            )
            if not result.get('dispatched'):
                record['state'] = 'blocked'
                record['note'] = str(result.get('reason') or '派单被拒绝')
                steps[key] = record
                status, current, note = 'blocked', stage, record['note']
                break
            record['runId'] = result.get('runId')
            record['state'] = 'running'
            record['note'] = '已派单给运行时'
            steps[key] = record
            status, current = 'waiting_run', stage
            note = f"已派单，运行 {str(result.get('runId'))[:12]}。"
            break

        action = dict(gate.get('action') or gate.get('humanAction') or {})
        action_type = str(action.get('type') or '')

        if confirm and action_type in _ACTIONS:
            outcome = _ACTIONS[action_type](project_id, actor)
            task_id = outcome.get('taskId')
            if task_id:
                record['taskId'] = task_id
                record['state'] = 'running'
                record['note'] = str(outcome.get('summary') or '')
                steps[key] = record
                status, current = 'waiting_task', stage
                note = f"已提交任务 {task_id}。"
                break
            # 生成被拒绝（缺前置条件）：如实停在阻断，不假装成功。
            record['state'] = 'blocked'
            record['note'] = str(outcome.get('summary') or '生成动作被拒绝')
            steps[key] = record
            status, current, note = 'blocked', stage, record['note']
            break

        record['state'] = 'waiting_human'
        record['note'] = str(gate.get('reason') or '需要工程师确认')
        if action:
            record['action'] = {
                'type': action_type,
                'endpoint': action.get('endpoint'),
                'note': action.get('note'),
                'impact': action.get('impact'),
                'executableByChain': action_type in _ACTIONS,
            }
        steps[key] = record
        status, current, note = 'waiting_human', stage, record['note']
        break
    else:
        status = 'done'

    summary = _summary(status, current, note, done, total)
    updated = AgentStore().update_flow(
        flow['id'], status=status, current_stage=(current or {}).get('stageKey'),
        steps=steps,
    )
    payload = {
        'schemaVersion': SCHEMA,
        'flowId': flow['id'],
        'projectId': project_id,
        'status': status,
        'currentStage': ({
            'stageKey': current['key'], 'stageId': current['stageId'],
            'title': current['title'], 'owner': current['owner'],
        } if current else None),
        'steps': [
            {**steps[stage['key']], 'title': stage['title'], 'stageId': stage['stageId'],
             'owner': stage['owner'], 'state': steps[stage['key']].get('state')}
            for stage in stages if stage['key'] in steps
        ],
        'progress': {'done': done, 'total': total},
        'summary': summary,
        'next': plan.get('next'),
        'formalConstructionIssue': False,
    }
    _write_turn(updated, payload)
    return payload


def _write_turn(flow: dict, payload: dict) -> None:
    conversation_id = flow.get('conversationId')
    if not conversation_id:
        return
    try:
        AgentStore().append_message(
            conversation_id, role='tool', content=payload['summary'],
            metadata={
                'answerKind': 'chain_progress', 'flowId': payload['flowId'],
                'status': payload['status'],
                'currentStage': (payload.get('currentStage') or {}).get('stageKey'),
                'engineeringJudgement': False,
            },
        )
    except (KeyError, ValueError):
        pass


def start(project_id: str, actor: str, *, conversation_id: str | None = None) -> dict:
    """新建一条连续执行链并推进到第一个停点。"""
    _repository(actor).require(project_id)
    store = AgentStore()
    conversation_id = _check_conversation(store, project_id, conversation_id)
    flow = store.create_flow(project_id=project_id, conversation_id=conversation_id,
                             created_by=actor)
    return _walk(flow, actor, confirm=False)


def advance(flow_id: str, actor: str, *, confirm: bool = False) -> dict:
    """从当前停点继续；``confirm`` 只对当前这一个闸口生效。"""
    flow = AgentStore().get_flow(flow_id)
    if flow['status'] in _TERMINAL_FLOW:
        raise ValueError(f"该流程已结束（{flow['status']}），不能继续推进")
    _repository(actor).require(flow['projectId'])
    return _walk(flow, actor, confirm=confirm)


def status(flow_id: str) -> dict:
    flow = AgentStore().get_flow(flow_id)
    return {**flow, 'schemaVersion': SCHEMA}


def history(project_id: str, *, limit: int = 20) -> list[dict]:
    return AgentStore().list_flows(project_id=project_id, limit=limit)


def _check_conversation(store: AgentStore, project_id: str,
                        conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    conversation = store.get_conversation(conversation_id)
    if conversation['projectId'] != project_id:
        raise ValueError('会话与项目不匹配')
    return conversation_id
