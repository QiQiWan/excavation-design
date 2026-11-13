"""V3.91 连续执行链：停在闸口、确认后继续、任务失败不重复提交。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import chain, orchestrator
from app.main import app


def headers():
    return {'X-PitGuard-Key': 'key-designer'}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_DB_PATH', str(tmp_path / 'projects.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE', str(tmp_path / 'one_strut'))
    monkeypatch.setenv('PITGUARD_AGENT_WORKER', 'external')
    monkeypatch.setenv('PITGUARD_API_KEYS', json.dumps({
        'key-designer': {'role': 'designer', 'actor': 'alice'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    with TestClient(app) as client:
        yield client


def _project(client):
    created = client.post('/api/projects', json={
        'name': '连续执行链', 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    assert created.status_code in (200, 201)
    return created.json()['id']


_ORDER = (
    ('basis', 'DESIGN.BASIS', 'A1', '设计基准'),
    ('input', 'DESIGN.INPUT', 'A2', '地质与场景'),
    ('scheme', 'DESIGN.SCHEME', 'A3', '支护方案'),
    ('calculation', 'DESIGN.CALCULATION', 'A5', '计算与验算'),
    ('reinforcement', 'DESIGN.REINFORCEMENT', 'A7', '构件深化'),
    ('deliverables', 'DESIGN.DELIVERABLES', 'A8', '成果与校审'),
)


def _plan(overrides: dict) -> dict:
    """用最小阶段表模拟总控投影；只关心状态与闸口。"""
    stages = []
    for key, stage_id, owner, title in _ORDER:
        base = {'state': 'blocked',
                'gate': {'type': 'upstream_incomplete', 'requiresHuman': False,
                         'reason': '上一步尚未完成'}}
        base.update(overrides.get(key) or {})
        stages.append({'key': key, 'stageId': stage_id, 'owner': owner,
                       'title': title, **base})
    nxt = next((s for s in stages if s['state'] != 'done'), stages[-1])
    return {'stages': stages, 'next': nxt, 'formalConstructionIssue': False}


_DONE = {'state': 'done', 'gate': {'type': 'done', 'requiresHuman': False}}


def _gate(reason='需要工程师确认', action=None):
    gate = {'type': 'human_confirm', 'requiresHuman': True, 'reason': reason}
    if action:
        gate['action'] = action
    return {'state': 'ready', 'gate': gate}


def test_chain_stops_at_first_human_gate_without_dispatching(api, monkeypatch):
    pid = _project(api)
    plan = _plan({'basis': _gate('设计基准必须由工程师确认',
                                 {'type': 'confirm_design_basis'})})
    monkeypatch.setattr(chain.orchestrator, 'build_plan',
                        lambda project_id, refresh=False: plan)
    dispatched = []
    monkeypatch.setattr(chain.orchestrator, 'dispatch',
                        lambda *a, **k: dispatched.append(k) or {'dispatched': False})

    body = api.post('/api/agents/chain/start', json={'projectId': pid},
                    headers=headers()).json()

    assert body['status'] == 'waiting_human'
    assert body['currentStage']['stageKey'] == 'basis'
    assert body['progress'] == {'done': 0, 'total': 6}
    assert dispatched == []
    assert body['formalConstructionIssue'] is False


def test_confirm_does_not_leak_to_a_gate_the_chain_cannot_execute(api, monkeypatch):
    pid = _project(api)
    plan = _plan({'basis': _gate('设计基准必须由工程师确认',
                                 {'type': 'confirm_design_basis'})})
    monkeypatch.setattr(chain.orchestrator, 'build_plan',
                        lambda project_id, refresh=False: plan)
    started = api.post('/api/agents/chain/start', json={'projectId': pid},
                       headers=headers()).json()

    # 人在页面上还没确认，链不能替他确认，也不能顺势跳去后面的步骤。
    body = api.post(f"/api/agents/chain/{started['flowId']}/advance",
                    json={'confirm': True}, headers=headers()).json()

    assert body['status'] == 'waiting_human'
    assert body['currentStage']['stageKey'] == 'basis'
    assert body['steps'][0].get('taskId') is None


def test_confirmed_action_submits_task_then_continues(api, monkeypatch):
    pid = _project(api)
    state = {'scheme_submitted': False}
    scheme_gate = _gate('生成方案会写工作版', {
        'type': 'generate_scheme',
        'endpoint': 'POST /api/agents/chain/{flowId}/advance',
        'impact': 'writes_working_copy_and_invalidates_calculation',
    })

    def build_plan(project_id, refresh=False):
        overrides = {'basis': _DONE, 'input': _DONE,
                     'scheme': ({'state': 'done', 'gate': {'type': 'done'}}
                                if state['scheme_submitted'] else scheme_gate)}
        return _plan(overrides)

    monkeypatch.setattr(chain.orchestrator, 'build_plan', build_plan)
    submitted = []

    def fake_generate(project_id, actor):
        submitted.append((project_id, actor))
        return {'taskId': 'task-demo', 'summary': '已提交方案生成任务 task-demo'}

    monkeypatch.setattr(chain, '_ACTIONS', {'generate_scheme': fake_generate})
    monkeypatch.setattr(chain, '_task_status', lambda task_id: ('running', ''))

    started = api.post('/api/agents/chain/start', json={'projectId': pid},
                       headers=headers()).json()
    assert started['currentStage']['stageKey'] == 'scheme'

    waiting = api.post(f"/api/agents/chain/{started['flowId']}/advance",
                       json={'confirm': True}, headers=headers()).json()
    assert submitted == [(pid, 'alice')]
    assert waiting['status'] == 'waiting_task'
    assert waiting['steps'][2]['taskId'] == 'task-demo'

    # 任务完成、阶段就绪后，链继续往下走（下一步是计算，缺冻结结果）。
    state['scheme_submitted'] = True
    monkeypatch.setattr(chain, '_task_status', lambda task_id: ('success', ''))
    calc_gate = {'state': 'blocked', 'missing': ['可启动的冻结单剖面结果'],
                 'gate': {'type': 'needs_frozen_result', 'requiresHuman': True,
                          'reason': '该项目没有冻结结果。'}}
    monkeypatch.setattr(chain.orchestrator, 'build_plan',
                        lambda project_id, refresh=False: _plan({
                            'basis': _DONE, 'input': _DONE, 'scheme': _DONE,
                            'calculation': calc_gate}))
    moved = api.post(f"/api/agents/chain/{started['flowId']}/advance",
                     json={}, headers=headers()).json()
    assert moved['status'] == 'waiting_human'
    assert moved['currentStage']['stageKey'] == 'calculation'
    assert moved['progress']['done'] == 3


def test_failed_task_blocks_and_is_not_resubmitted(api, monkeypatch):
    pid = _project(api)
    scheme_gate = _gate('生成方案会写工作版', {'type': 'generate_scheme'})
    monkeypatch.setattr(chain.orchestrator, 'build_plan',
                        lambda project_id, refresh=False: _plan({
                            'basis': _DONE, 'input': _DONE, 'scheme': scheme_gate}))
    calls = []
    monkeypatch.setattr(chain, '_ACTIONS', {
        'generate_scheme': lambda project_id, actor: calls.append(1) or {
            'taskId': 'task-fail', 'summary': '已提交'},
    })
    monkeypatch.setattr(chain, '_task_status', lambda task_id: ('running', ''))

    started = api.post('/api/agents/chain/start', json={'projectId': pid},
                       headers=headers()).json()
    api.post(f"/api/agents/chain/{started['flowId']}/advance",
             json={'confirm': True}, headers=headers())

    monkeypatch.setattr(chain, '_task_status',
                        lambda task_id: ('failed', '当前计算仍有 10 个硬失败'))
    blocked = api.post(f"/api/agents/chain/{started['flowId']}/advance",
                       json={'confirm': True}, headers=headers()).json()

    assert blocked['status'] == 'blocked'
    assert '10 个硬失败' in blocked['summary']
    assert calls == [1]  # 没有再次提交

    again = api.post(f"/api/agents/chain/{started['flowId']}/advance",
                     json={'confirm': True}, headers=headers()).json()
    assert again['status'] == 'blocked'
    assert calls == [1]


def test_chain_completes_when_all_stages_done(api, monkeypatch):
    pid = _project(api)
    monkeypatch.setattr(chain.orchestrator, 'build_plan',
                        lambda project_id, refresh=False: _plan({
                            key: _DONE for key, *_ in _ORDER}))

    body = api.post('/api/agents/chain/start', json={'projectId': pid},
                    headers=headers()).json()

    assert body['status'] == 'done'
    assert body['progress'] == {'done': 6, 'total': 6}
    assert '全部就绪' in body['summary']

    closed = api.post(f"/api/agents/chain/{body['flowId']}/advance",
                      json={}, headers=headers())
    assert closed.status_code == 409
    assert '已结束' in closed.json()['detail']


def test_orchestrator_gates_reflect_executable_roles(tmp_path, monkeypatch):
    """闸口不能再写「尚未建立执行链」：A3/A7/A8 已可执行。"""
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'orch.sqlite3'))
    stages = [
        {'key': 'basis', 'title': '设计基准', 'status': 'done', 'message': None},
        {'key': 'input', 'title': '地质与场景', 'status': 'done', 'message': None},
        {'key': 'scheme', 'title': '支护方案', 'status': 'pending', 'message': None},
    ]
    status = {'summary': {}, 'stages': stages}
    counts = {'boreholeCount': 4, 'stratumCount': 3, 'outlinePointCount': 6}
    monkeypatch.setattr(orchestrator, '_workspace_status', lambda pid: (status, counts))
    monkeypatch.setattr(orchestrator, '_frozen_results', lambda pid: [])
    orchestrator._plan_cache.clear()

    plan = orchestrator.build_plan('P-chain')
    scheme = next(row for row in plan['stages'] if row['key'] == 'scheme')

    assert scheme['gate']['type'] == 'human_confirm'
    assert scheme['gate']['action']['type'] == 'generate_scheme'
    assert scheme['gate']['action']['impact'] == 'writes_working_copy_and_invalidates_calculation'
    assert '尚未建立执行链' not in scheme['gate']['reason']


def test_dock_starts_chain_and_reports_the_gate(api, monkeypatch):
    """「走一遍全流程」应交给链，而不是落到 A1 参数录入。"""
    pid = _project(api)
    plan = _plan({'basis': _gate('设计基准必须由工程师确认',
                                 {'type': 'confirm_design_basis'})})
    monkeypatch.setattr(chain.orchestrator, 'build_plan',
                        lambda project_id, refresh=False: plan)

    body = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'user_message', 'acceptMode': 'auto',
        'payload': {'text': '走一遍全流程'},
    }).json()

    execution = body['execution']
    assert execution['roleId'] == 'CHAIN'
    assert execution['outcome']['mode'] == 'chain'
    assert execution['outcome']['flowStatus'] == 'waiting_human'
    assert execution['outcome']['currentStage']['stageKey'] == 'basis'
    assert body['decision'] == 'accepted'
