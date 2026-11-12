"""EngineeringFinding / ProposedAction：诊断结果与建议动作的持久对象。

A5/A6 每次诊断、每次生成试算方案，都落成可追溯的记录：

- Finding：问题代码、严重度、类别、原文、证据（run + 冻结结果哈希）、状态；
- Action：改哪个字段、改前/改后、理由、参数、状态（proposed/accepted/rejected）。

接受只记录人工决策，**不修改工程数据**；真正采用仍走 `/api/agents/runs/{id}/decision`
的候选采用端点。
"""
from __future__ import annotations

from .store import AgentStore

SCHEMA = 'pitguard.findings/1'

_BLOCKING_CATEGORIES = {'physical_constraint', 'missing_engineering_evidence'}


def _severity(issue: dict) -> str:
    return 'blocker' if str(issue.get('category')) in _BLOCKING_CATEGORIES else 'warning'


def _title(issue: dict) -> str:
    code = str(issue.get('code') or 'UNKNOWN')[:90]
    field = str(issue.get('field') or '').strip()
    return f'{code} · {field}' if field else code


def record_diagnosis(*, project_id: str, run_id: str, issues: list[dict],
                     evidence: dict | None = None, source: str = 'A5/A6 diagnosis') -> list[dict]:
    """把一次诊断的问题落库；同一 run 重复调用只更新，不新增。"""
    store = AgentStore()
    base = {'runId': run_id, **(evidence or {})}
    rows = []
    for issue in issues or []:
        code = str(issue.get('code') or 'UNKNOWN')[:90]
        rows.append(store.upsert_finding(
            project_id=project_id, run_id=run_id, code=code,
            severity=_severity(issue), category=str(issue.get('category') or '')[:60] or None,
            title=_title(issue), detail=str(issue.get('message') or '')[:800],
            source=source, evidence=base,
        ))
    return rows


def _action_title(action: dict) -> str:
    field = str(action.get('field') or '').replace('.', ' · ')
    before, after = action.get('before'), action.get('after')
    try:
        return f'{field}：{float(before):g} → {float(after):g}'[:240]
    except (TypeError, ValueError):
        return f'{field}：{before} → {after}'[:240]


def record_plan(*, project_id: str, run_id: str, actions: list[dict],
                created_by: str = 'agent') -> list[dict]:
    """把有界试算方案落成建议动作；同一 run + 标题幂等。"""
    store = AgentStore()
    rows = []
    for action in actions or []:
        rows.append(store.create_action(
            project_id=project_id, run_id=run_id, kind='parameter_trial',
            title=_action_title(action), rationale=str(action.get('reason') or '')[:800],
            params=action, created_by=created_by,
        ))
    return rows


def overview(project_id: str, *, status: str | None = None) -> dict:
    store = AgentStore()
    findings = store.list_findings(project_id, status=status)
    actions = store.list_actions(project_id)
    blockers = sum(1 for row in findings if row['severity'] == 'blocker')
    proposed = sum(1 for row in actions if row['status'] == 'proposed')
    return {
        'schemaVersion': SCHEMA,
        'projectId': project_id,
        'counts': {
            'findings': len(findings), 'blockers': blockers,
            'warnings': len(findings) - blockers,
            'actions': len(actions), 'proposed': proposed,
        },
        'findings': findings,
        'actions': actions,
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }


def decide(action_id: str, *, decision: str, actor: str, note: str | None = None) -> dict:
    """人工接受/拒绝建议动作；只改决策状态，不改工程。"""
    return AgentStore().decide_action(action_id, decision=decision, actor=actor, note=note)
