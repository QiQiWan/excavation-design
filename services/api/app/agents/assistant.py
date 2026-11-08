"""Deterministic assistant turns.

The assistant does not analyse the design yet. It records the engineer's
question and answers with a verifiable status receipt: which professional
roles exist, which of them can run today, and what currently blocks the
project. Every statement is read from stored project state; nothing about
the engineering itself is inferred here.

An assistant turn never carries engineering authority. It cannot adopt a
candidate, mark a check as passed, or enable construction issue.
"""
from __future__ import annotations
import os
import time

from .catalog import catalog
from .store import AgentStore

ROLE_LABEL = {'pilot': '可运行', 'planned': '待建'}

# The workspace projection already carries the headline check counts, so the
# receipt never has to rehydrate the heavy calculation evidence bundle.
_DIGEST_TTL_SECONDS = 30.0
_digest_cache: dict[str, tuple[float, dict]] = {}


def _project_status(project_id: str) -> dict | None:
    """Summarise current project state from the bounded workspace projection."""
    cached = _digest_cache.get(project_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _DIGEST_TTL_SECONDS:
        payload = dict(cached[1])
        payload['cachedSecondsAgo'] = round(now - cached[0], 1)
        return payload
    try:
        from app.storage.repository import ProjectRepository, shared_project_store
        repo = ProjectRepository(store=shared_project_store(os.getenv('PITGUARD_DB_PATH') or None))
        project = repo.require_workspace(project_id)
    except Exception:
        return None

    settings = project.design_settings
    retaining = project.retaining_system
    walls = list(getattr(retaining, 'diaphragm_walls', None) or [])
    supports = list(getattr(retaining, 'supports', None) or [])
    boreholes = list(project.boreholes or [])
    strata = list(project.strata or [])
    results = list(project.calculation_results or [])
    latest = results[-1] if results else None
    checks = dict(getattr(latest, 'check_summary', None) or {})
    fail = checks.get('fail')
    warning = checks.get('warning')

    basis_done = bool(settings.design_basis_confirmed)
    input_done = bool(boreholes and strata)
    scheme_done = bool(walls)
    calc_done = latest is not None and not fail

    stages = [
        {'key': 'basis', 'title': '设计基准',
         'status': 'done' if basis_done else 'active',
         'message': '等级、规范和荷载组合已确认' if basis_done else '确认工程等级、场地标准和荷载组合'},
        {'key': 'input', 'title': '工程输入',
         'status': 'done' if input_done else 'active',
         'message': f'钻孔 {len(boreholes)}，地层 {len(strata)}'},
        {'key': 'scheme', 'title': '围护方案',
         'status': 'done' if scheme_done else 'pending',
         'message': f'围护墙 {len(walls)}，支撑 {len(supports)}'},
        {'key': 'calculation', 'title': '计算验算',
         'status': 'done' if calc_done else ('active' if latest else 'pending'),
         'message': (f'当前计算仍有 {fail} 个硬失败。' if fail else
                     ('计算结果已生成且无硬失败。' if latest else '尚未生成当前方案计算结果。'))},
    ]

    blockers: list[str] = []
    if not basis_done:
        blockers.append('设计基准尚未确认')
    if fail:
        blockers.append(f'当前计算仍有 {fail} 个硬失败。')

    payload = {
        'stages': stages,
        'doneStages': [s['title'] for s in stages if s['status'] == 'done'],
        'activeStage': next((s for s in stages if s['status'] == 'active'), None),
        'failCount': fail,
        'warningCount': warning,
        'blockers': blockers,
        'wallCount': len(walls),
        'supportCount': len(supports),
        'boreholeCount': len(boreholes),
        'stratumCount': len(strata),
    }
    _digest_cache[project_id] = (now, payload)
    return payload


def project_digest(project_id: str) -> dict:
    """Read-only status digest for the floating assistant's proactive notices.

    This is the same bounded projection used by a conversation turn; it is
    exposed separately so the assistant can raise a notice without the
    engineer opening the panel or sending a message first.
    """
    return _project_status(project_id) or {
        'statusUnavailable': True,
        'stages': [],
        'doneStages': [],
        'activeStage': None,
        'failCount': None,
        'warningCount': None,
        'blockers': [],
    }


def role_roster() -> list[dict]:
    """The eight professional roles and their current implementation state."""
    return [
        {'id': role['id'], 'name': role['name'], 'capability': role['capability'],
         'implementation': role['implementation'],
         'stateLabel': ROLE_LABEL.get(role['implementation'], role['implementation']),
         'executable': role['implementation'] == 'pilot'}
        for role in catalog()['roles']
    ]


def respond(conversation: dict, content: str) -> dict:
    """Write the assistant's reply turn for one engineer message."""
    store = AgentStore()
    roles = role_roster()
    executable = [r['id'] + ' ' + r['name'] for r in roles if r['executable']]
    pending = [r['id'] for r in roles if not r['executable']]

    status = _project_status(conversation['projectId'])
    lines: list[str] = ['已记录本轮提问。当前为占位助手，不做工程判断。', '']
    digest: dict = {'answerKind': 'status_receipt', 'engineeringJudgement': False}

    if status:
        active = status.get('activeStage')
        blockers = list(status.get('blockers') or [])
        if active:
            lines.append(f"当前步骤：{active['title']} — {active['message']}")
        done = status.get('doneStages') or []
        if done:
            lines.append(f"已完成步骤：{'、'.join(done)}")
        if status.get('failCount') is not None:
            lines.append(f"验算未通过 {status['failCount']} 项"
                         + (f"，警告 {status['warningCount']} 项"
                            if status.get('warningCount') is not None else ""))
        if blockers:
            lines.append(f"当前阻断 {len(blockers)} 条：")
            lines.extend(f"  · {b}" for b in blockers[:4])
        digest['activeStage'] = active['key'] if active else None
        digest['failCount'] = status.get('failCount')
        digest['warningCount'] = status.get('warningCount')
        digest['blockerCount'] = len(blockers)
        digest['blockers'] = blockers[:8]
        if status.get('cachedSecondsAgo') is not None:
            digest['cachedSecondsAgo'] = status['cachedSecondsAgo']
    else:
        lines.append('当前未能读取工程状态，可能项目尚未建立或已不可用。')
        digest['statusUnavailable'] = True

    lines.append('')
    lines.append(f"可执行角色：{'、'.join(executable) if executable else '无'}（A6 目前仅限单排桩单剖面）")
    lines.append(f"待建角色：{'、'.join(pending) if pending else '无'}")
    lines.append('')
    lines.append('提问会被保留，等执行链接入后再由它作答。本回复不构成工程判断，也未改变任何设计。')

    return store.append_message(conversation['id'], role='assistant',
                                content='\n'.join(lines), metadata=digest)
