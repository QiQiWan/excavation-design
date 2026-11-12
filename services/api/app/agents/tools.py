"""Agent 工具注册表：声明、参数校验、权限分级与调用审计。

权限按重构方案第七节落地：
- L0 只读：查询项目、计算结果、规范目录与问题
- L1 试算：生成候选、复算，只作用于独立候选，不改主项目
- L2 采用：必须由授权人员在人工端点确认，不向自动流程开放
- L3 正式发行：不进入工具表

所有调用都要落库（工具、权限、调用人、项目、参数、结果、错误、耗时），
对应方案「可追溯性」：能回答谁在什么时候用什么工具改了什么。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .store import AgentStore

L0, L1, L2 = 'L0', 'L1', 'L2'


@dataclass(frozen=True)
class Tool:
    name: str
    permission: str
    side_effects: bool | str
    scope: str
    description: str
    params: dict[str, str] = field(default_factory=dict)
    handler: Callable[[dict], dict] | None = None
    unavailable_reason: str | None = None


def _count(value: Any) -> int:
    try:
        return len(value or [])
    except TypeError:
        return 0


def _project_context(ctx: dict) -> dict:
    """Agent 读取的显式项目上下文（只读、有界、带哈希）。"""
    from . import context as engineering_context
    return engineering_context.build(ctx['projectId'], ctx['actor'])


def _calculation_findings(ctx: dict) -> dict:
    from . import assistant
    digest = assistant.project_digest(ctx['projectId'])
    active = digest.get('activeStage')
    return {
        'projectId': ctx['projectId'],
        'statusUnavailable': bool(digest.get('statusUnavailable')),
        'activeStage': (active or {}).get('title') if isinstance(active, dict) else active,
        'failCount': digest.get('failCount'),
        'warningCount': digest.get('warningCount'),
        'blockers': (digest.get('blockers') or [])[:10],
        'doneStages': digest.get('doneStages') or [],
        'formalConstructionIssue': False,
    }


def _standard_methods(ctx: dict) -> dict:
    from app.section.norms.registry import catalog as norm_catalog
    data = norm_catalog() or {}
    standards = data.get('standards') or {}
    rows = [
        {'code': code, 'name': item.get('name'), 'effective': item.get('effective'),
         'authority': item.get('authority')}
        for code, item in standards.items() if isinstance(item, dict)
    ]
    return {
        'schemaVersion': data.get('schemaVersion'),
        'sections': sorted(data.keys()),
        'standardCount': len(rows),
        'standards': rows[:20],
        'note': '这里只给标准与方法目录，不返回条文正文；判定仍由规则程序执行。',
    }


def _run_candidates(ctx: dict) -> dict:
    run_id = ctx['params']['runId']
    run = AgentStore().get(run_id)
    plan = run.get('plan') or {}
    actions = plan.get('actions') or []
    return {
        'runId': run_id, 'status': run.get('status'), 'step': run.get('step'),
        'count': len(actions), 'candidates': actions,
        'sourceResultHash': run.get('sourceResultHash'),
    }


def _run_stage_analysis(ctx: dict) -> dict:
    from .engine import AgentRuntime
    run_id = ctx['params']['runId']
    result = AgentRuntime().process_one(run_id)
    if result is None:
        return {
            'runId': run_id, 'advanced': False,
            'note': '当前没有可推进的步骤：任务可能已完成，或已被其他 worker 领取。',
        }
    return {
        'runId': run_id, 'advanced': True, 'status': result.get('status'),
        'step': result.get('step'), 'progress': result.get('progress'),
    }


def _prepare_delivery_package(ctx: dict) -> dict:
    from . import delivery_agent
    outcome = delivery_agent.prepare(ctx['projectId'], ctx['actor'], '出成果包')
    return {
        'label': outcome.get('label'), 'status': outcome.get('status'),
        'taskId': outcome.get('taskId'), 'summary': outcome.get('summary'),
        'formalConstructionIssue': False,
    }


def _evaluate_changes(ctx: dict) -> dict:
    run_id = ctx['params']['runId']
    rows = AgentStore().candidates(run_id)
    return {
        'runId': run_id,
        'count': len(rows),
        'candidates': [
            {
                'id': row.get('id'), 'index': row.get('proposalIndex'),
                'status': row.get('status'), 'metrics': row.get('metrics'),
                'resultHash': row.get('resultHash'), 'snapshotHash': row.get('snapshotHash'),
                'error': row.get('error'),
            }
            for row in rows
        ],
    }


TOOLS = (
    Tool('get_project_context', L0, False, 'project',
         '读取项目身份、输入计数与设计基准关键项', {}, _project_context),
    Tool('get_calculation_findings', L0, False, 'project',
         '读取当前计算的失败/警告数与阻断项', {}, _calculation_findings),
    Tool('query_standard_method', L0, False, 'norm',
         '查询适用规范与方法目录（不返回条文正文）', {}, _standard_methods),
    Tool('generate_scheme_candidates', L1, False, 'run',
         '读取某个任务已生成的候选清单', {'runId': 'string'}, _run_candidates),
    Tool('run_stage_analysis', L1, 'isolated_candidate', 'run',
         '对任务推进一步复算，只作用于独立候选', {'runId': 'string'}, _run_stage_analysis),
    Tool('evaluate_design_changes', L0, False, 'run',
         '读取候选复算结果的对比数据', {'runId': 'string'}, _evaluate_changes),
    Tool('adopt_design_candidate', L2, 'checker_approved_selection_only', 'run',
         '采用候选方案到工作版', {'runId': 'string', 'candidateId': 'string'}, None,
         '必须由授权人员在 /api/agents/runs/{id}/decision 人工确认；工具层不开放'),
    Tool('prepare_delivery_package', L1, 'artifact_only', 'delivery',
         '提交评审版成果包导出任务（不签发正式施工图）', {}, _prepare_delivery_package),
)

BY_NAME = {tool.name: tool for tool in TOOLS}

PERMISSION_NOTES = {
    'L0': '只读：查询项目、结果、规范与问题',
    'L1': '试算：生成独立候选并复算，不改主项目',
    'L2': '采用：必须由授权人员人工确认',
    'L3': '正式发行：不进入工具表',
}


def registry() -> dict:
    return {
        'schemaVersion': 'pitguard.agent-tools/1',
        'permissions': PERMISSION_NOTES,
        'tools': [
            {
                'name': tool.name,
                'permission': tool.permission,
                'sideEffects': tool.side_effects,
                'scope': tool.scope,
                'description': tool.description,
                'params': tool.params,
                'autoInvocable': tool.handler is not None,
                'unavailableReason': tool.unavailable_reason,
            }
            for tool in TOOLS
        ],
        'approveFormalIssue': {'registered': False, 'reason': '正式签发不向工具层开放'},
    }


def validate_params(tool: Tool, params: Any) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError('参数必须是对象')
    unknown = sorted(set(params) - set(tool.params))
    if unknown:
        raise ValueError('不认识的参数：' + '、'.join(unknown))
    checked: dict[str, Any] = {}
    for key, kind in tool.params.items():
        if key not in params:
            raise ValueError(f'缺少参数：{key}')
        value = params[key]
        if kind == 'string' and not isinstance(value, str):
            raise ValueError(f'参数 {key} 必须是字符串')
        if kind == 'integer' and not isinstance(value, int):
            raise ValueError(f'参数 {key} 必须是整数')
        checked[key] = value
    return checked


def invoke(name: str, *, project_id: str | None, actor: str, params: Any) -> dict:
    tool = BY_NAME.get(name)
    if tool is None:
        # Unknown names are probing attempts too; keep them in the audit trail.
        AgentStore().record_tool_call(
            tool_name=name, permission='-', actor=actor, project_id=project_id,
            params=params if isinstance(params, dict) else {}, ok=False, result=None,
            error='KeyError: 工具未注册', duration_ms=0,
        )
        raise KeyError(name)

    store = AgentStore()
    started = time.time()
    checked: dict[str, Any] = {}
    try:
        checked = validate_params(tool, params)
        if tool.handler is None:
            raise PermissionError(tool.unavailable_reason or '该工具不允许自动调用')
        result = tool.handler({
            'projectId': project_id, 'actor': actor, 'params': checked, 'store': store,
        })
        store.record_tool_call(
            tool_name=name, permission=tool.permission, actor=actor, project_id=project_id,
            params=checked, ok=True, result=result, error=None,
            duration_ms=int((time.time() - started) * 1000),
        )
        return {
            'tool': name, 'permission': tool.permission, 'result': result,
            'formalConstructionIssue': False,
        }
    except Exception as exc:
        store.record_tool_call(
            tool_name=name, permission=tool.permission, actor=actor, project_id=project_id,
            params=checked, ok=False, result=None,
            error=type(exc).__name__ + ': ' + str(exc)[:180],
            duration_ms=int((time.time() - started) * 1000),
        )
        raise
