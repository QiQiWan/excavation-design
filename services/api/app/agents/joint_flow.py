"""V3.91 多 Agent 联合流程：一次请求串起 A2 → A3 → A4 → A7 → A8。

只做**只读体检**：每个 Worker 仍走自己的盘点函数，谁都不写项目、不生成候选、
不签发成果。联合层的价值是"一次问完、按依赖顺序汇总、给出下一步"，
避免工程师在五个 Agent 之间来回问。

写操作仍然各自单干（A3/A4/A7 的生成动作、A8 的成果包），联合流程不代劳。
"""
from __future__ import annotations

from typing import Any, Callable

from . import context as engineering_context
from . import delivery_agent, detailing_agent, geology_agent, scheme_stage_agent
from .design_basis_intake import _repository, project_version

SCHEMA = 'pitguard.joint-review/1'

STAGES: tuple[tuple[str, str, str, Callable[[str, str], dict]], ...] = (
    ('A2', '地质与场景', 'DESIGN.INPUT', lambda pid, actor: geology_agent.assess(pid, actor)),
    ('A3', '支护方案', 'DESIGN.SCHEME', lambda pid, actor: scheme_stage_agent.assess_scheme(pid, actor)),
    ('A4', '施工阶段', 'DESIGN.CONSTRUCTION', lambda pid, actor: scheme_stage_agent.assess_stages(pid, actor)),
    ('A7', '构件深化', 'DESIGN.REINFORCEMENT', lambda pid, actor: detailing_agent.assess(pid, actor)),
    ('A8', '成果与校审', 'DELIVERY.REVIEW', lambda pid, actor: delivery_agent.assess(pid, actor)),
)

_RANK = {'ready': 0, 'incomplete': 1, 'blocked': 2, 'error': 3}


def execute(project_id: str, actor: str, *, roles: list[str] | None = None) -> dict:
    """按依赖顺序跑一遍只读体检，返回汇总与下一步。"""
    _repository(actor).require(project_id)
    version = project_version(project_id, actor)
    start_context = engineering_context.build(project_id, actor)
    stages: list[dict[str, Any]] = []
    for role_id, label, stage_id, assessor in STAGES:
        if roles and role_id not in roles:
            continue
        try:
            outcome = assessor(project_id, actor)
            gaps = list(outcome.get('gaps') or [])
            stages.append({
                'roleId': role_id, 'label': label, 'stageId': stage_id,
                'status': outcome.get('status'),
                'summary': outcome.get('summary'),
                'gapCount': len(gaps),
                'topGaps': [{'key': g.get('key'), 'label': g.get('label'),
                             'reason': g.get('reason')} for g in gaps[:3]],
                'suggestedNext': list(outcome.get('suggestedNext') or [])[:2],
            })
        except Exception as exc:
            stages.append({
                'roleId': role_id, 'label': label, 'stageId': stage_id,
                'status': 'error',
                'summary': f'盘点失败：{type(exc).__name__}: {str(exc)[:120]}',
                'gapCount': 0, 'topGaps': [], 'suggestedNext': [],
            })

    worst = max(stages, key=lambda row: _RANK.get(str(row['status']), 0), default=None)
    overall = str(worst['status']) if worst else 'ready'
    if overall == 'error':
        overall = 'blocked'

    blocked = [row for row in stages if row['status'] == 'blocked']
    incomplete = [row for row in stages if row['status'] == 'incomplete']
    ready = [row for row in stages if row['status'] == 'ready']
    gap_total = sum(int(row['gapCount']) for row in stages)
    actions: list[str] = []
    for row in blocked + incomplete:
        for item in row['suggestedNext']:
            if item and item not in actions:
                actions.append(item)

    if not blocked and not incomplete:
        summary = (f'全流程体检通过：{len(stages)} 个环节全部就绪'
                   f'（{"、".join(row["roleId"] for row in stages)}）。')
    else:
        parts = []
        if ready:
            parts.append(f'{len(ready)} 个就绪')
        if incomplete:
            parts.append(f'{len(incomplete)} 个待补')
        if blocked:
            parts.append(f'{len(blocked)} 个阻断')
        summary = (f'全流程体检：{len(stages)} 个环节，' + '、'.join(parts)
                   + f'；共 {gap_total} 项待办。')

    end_context = engineering_context.build(project_id, actor)
    stale = end_context['contextHash'] != start_context['contextHash']
    if stale:
        summary += ' 项目在体检期间发生变更，结论需重新核对。'

    return {
        'schemaVersion': SCHEMA,
        'mode': 'assessment',
        'label': '全流程体检',
        'projectId': project_id,
        'status': overall,
        'projectVersion': version,
        'context': {
            'contextHash': start_context['contextHash'],
            'projectUpdatedAt': start_context['identity']['updatedAt'],
            'staleDuringRun': stale,
        },
        'stageCount': len(stages),
        'stages': stages,
        'gapCount': gap_total,
        'gaps': [],
        'summary': summary,
        'suggestedNext': actions[:4],
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
        'readOnly': True,
    }
