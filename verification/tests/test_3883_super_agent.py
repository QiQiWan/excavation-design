"""Super-agent 双层架构：Router 准入、Ticket/Mandate、订阅直通、Worker 隔离。

参考 DeepSeek Harness Super-agent 设计；本测试只验架构骨架，不验模型能力。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import super_agent
from app.main import app


def headers(key='key-designer'):
    return {'X-PitGuard-Key': key}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_DB_PATH', str(tmp_path / 'projects.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE', str(tmp_path / 'one_strut'))
    monkeypatch.setenv('PITGUARD_AGENT_WORKER', 'external')
    monkeypatch.setenv('PITGUARD_API_KEYS', json.dumps({
        'key-designer': {'role': 'designer', 'actor': 'alice'},
        'key-viewer': {'role': 'viewer', 'actor': 'view'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    with TestClient(app) as client:
        yield client


def _project(api, name='双层架构测试'):
    return api.post('/api/projects', headers=headers(), json={'name': name}).json()['id']


def test_blueprint_exposes_router_and_eight_workers(api):
    data = api.get('/api/agents/super/blueprint', headers=headers()).json()
    assert data['schemaVersion'] == super_agent.SCHEMA
    assert data['workerCount'] == 8
    # A1-A8 全部接入执行链。
    assert data['executableWorkers'] == ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8']
    assert data['router']['defaultAcceptMode'] == 'observe'
    assert data['router']['mode'] == 'rules-then-model'
    assert data['router']['modelSlowPath'] == 'wired'
    assert data['formalConstructionIssue'] is False
    assert any('正式施工图' in line for line in data['redLines'])
    planned = [w for w in data['workers'] if not w['executable']]
    assert planned == []
    assert all(w['blockedReason'] for w in planned)


def test_unknown_trigger_ignored_by_fast_path(api):
    pid = _project(api, '快筛')
    result = super_agent.ingest_trigger(
        project_id=pid, kind='random_chat_noise', payload={}, actor='alice')
    assert result['decision'] == 'ignored'
    assert result['ticketId'] is None
    assert result['policy']['used'] is False


def test_observe_mode_records_but_never_executes(api):
    pid = _project(api, '观察模式')
    before = api.get('/api/projects/' + pid, headers=headers()).json()['designSettings']
    response = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'user_message',
        'payload': {'text': '施工荷载 88kPa', 'roleId': 'A1'},
    })
    assert response.status_code == 200
    body = response.json()
    assert body['decision'] == 'observed'
    assert body['acceptMode'] == 'observe'
    assert body['ticket']['status'] == 'observed'
    assert 'execution' not in body
    after = api.get('/api/projects/' + pid, headers=headers()).json()['designSettings']
    assert after['surcharge'] == before['surcharge']
    assert body['ticket']['mandate']['immutable'] is True
    assert body['ticket']['mandate']['formalConstructionIssue'] is False


def test_auto_mode_dispatches_a1_and_router_replies(api):
    pid = _project(api, '自动派单')
    conversation = api.post('/api/agents/conversations', headers=headers(),
                            json={'project_id': pid, 'title': 'Router 会话'}).json()
    response = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'document_uploaded',
        'conversationId': conversation['id'], 'acceptMode': 'auto',
        'payload': {'roleId': 'A1', 'text': '施工荷载 66kPa，混凝土等级 C45'},
    })
    assert response.status_code == 200
    body = response.json()
    assert body['decision'] == 'accepted'
    assert body['execution']['dispatched'] is True
    assert body['execution']['roleId'] == 'A1'
    assert body['execution']['executor'] == 'design_basis_intake'

    settings = api.get('/api/projects/' + pid, headers=headers()).json()['designSettings']
    assert settings['surcharge'] == 66
    assert settings['defaultConcreteGrade'] == 'C45'

    turns = api.get('/api/agents/conversations/' + conversation['id'],
                    headers=headers()).json()['messages']
    report = next((m for m in turns
                   if m['metadata'].get('answerKind') == 'worker_report'), None)
    assert report is not None
    assert report['role'] == 'tool'
    assert report['metadata']['ticketId'] == body['ticketId']
    assert report['metadata']['engineeringJudgement'] is False


def test_same_thread_goes_direct_to_existing_ticket(api):
    pid = _project(api, '订阅直通')
    first = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'user_message', 'threadId': 'thread-alpha',
        'payload': {'roleId': 'A1'},
    }).json()
    assert first['decision'] == 'observed'
    second = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'design_input_changed', 'threadId': 'thread-alpha',
        'payload': {'note': '后续事件'},
    }).json()
    assert second['decision'] == 'direct'
    assert second['subscriptionDirect'] is True
    assert second['ticketId'] == first['ticketId']
    tickets = api.get('/api/agents/super/tickets', headers=headers(),
                      params={'project_id': pid}).json()['tickets']
    assert len(tickets) == 1
    events = api.get('/api/agents/super/tickets/' + first['ticketId'],
                     headers=headers()).json()['events']
    assert any(event['kind'] == 'trigger_direct' for event in events)


def test_mandate_cannot_be_widened(api):
    pid = _project(api, '授权不可扩张')
    created = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'calculation_failed', 'acceptMode': 'auto',
        'payload': {'roleId': 'A6'},
    }).json()
    ticket_id = created['ticketId']
    denied = api.post('/api/agents/super/tickets/' + ticket_id + '/dispatch',
                      headers=headers(), json={'requestedTools': ['approve_formal_issue']})
    assert denied.status_code == 200
    body = denied.json()
    assert body['dispatched'] is False
    assert '授权书不允许' in body['reason']
    detail = api.get('/api/agents/super/tickets/' + ticket_id,
                     headers=headers()).json()
    assert detail['ticket']['mandate']['allowedTools'] == created['ticket']['mandate']['allowedTools']


def test_unimplemented_worker_is_blocked_not_faked(api, monkeypatch):
    # 防线仍在：把 A7 临时标成未实现，派发必须明确 blocked，不能伪造结果。
    monkeypatch.setitem(super_agent.WORKER_BY_ID, 'A7', {
        **super_agent.WORKER_BY_ID['A7'], 'implemented': False, 'executor': None,
    })
    pid = _project(api, '未建角色')
    body = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'review_requested', 'acceptMode': 'auto',
        'payload': {'roleId': 'A7'},
    }).json()
    assert body['decision'] == 'accepted'
    assert body['execution']['dispatched'] is False
    assert '执行链尚未建立' in body['execution']['reason']


def test_watchdog_and_role_gate(api):
    pid = _project(api, '巡检')
    watchdog = api.get('/api/agents/super/watchdog', headers=headers(),
                       params={'project_id': pid}).json()
    assert watchdog['schemaVersion'] == 'pitguard.super-agent-watchdog/1'
    assert watchdog['formalConstructionIssue'] is False
    denied = api.post('/api/agents/super/triggers',
                      headers=headers('key-viewer'),
                      json={'projectId': pid, 'kind': 'user_message'})
    assert denied.status_code == 403


# —— 模型 Router（Slow Path）测试：不依赖真实网络，用 mock urlopen ——

class _RouterReply:
    headers = {'Content-Length': '0'}
    def __init__(self, payload: bytes):
        self._payload = payload
    def read(self, _limit=-1):
        return self._payload
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False


def _router_chat(message: dict) -> bytes:
    return json.dumps({'choices': [{'message': message}]}).encode('utf-8')


@pytest.fixture
def router_env(monkeypatch):
    monkeypatch.setenv('PITGUARD_LLM_ENABLE', '1')
    monkeypatch.setenv('PITGUARD_LLM_API_KEY', 'unit-test-placeholder')
    monkeypatch.setenv('PITGUARD_LLM_MODEL_FAST', 'unit-test-model')
    monkeypatch.setenv('PITGUARD_LLM_BASE_URL', 'http://127.0.0.1:9/v1')
    monkeypatch.setenv('PITGUARD_LLM_ALLOWED_HOSTS', '127.0.0.1')
    monkeypatch.setenv('PITGUARD_LLM_TIMEOUT', '3')


def test_router_fast_path_never_calls_model(router_env, monkeypatch):
    called = []
    monkeypatch.setattr(super_agent.intake.urllib.request, 'urlopen',
                        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError))
    policy = super_agent._semantic_policy(
        'user_message', {'text': '施工荷载 42kPa'}, {'owner': 'A1'})
    assert policy['provider'] == 'deterministic'
    assert policy['used'] is True
    assert policy['role'] == 'A1'
    assert not called


def test_router_slow_path_uses_model_when_rules_miss(router_env, monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        return _RouterReply(_router_chat({'content': '{"role":"A2"}'}))

    monkeypatch.setattr(super_agent.intake.urllib.request, 'urlopen', fake_urlopen)
    policy = super_agent._semantic_policy(
        'user_message', {'text': '这个项目整体怎么样'}, {'owner': 'A1'})
    assert policy['provider'] == 'llm-router'
    assert policy['used'] is True
    assert policy['role'] == 'A2'


def test_router_reads_reasoning_content_when_body_empty(router_env, monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        return _RouterReply(_router_chat({'content': None,
                                          'reasoning_content': '{"role":"A6"}'}))

    monkeypatch.setattr(super_agent.intake.urllib.request, 'urlopen', fake_urlopen)
    policy = super_agent._semantic_policy(
        'user_message', {'text': '这个项目整体怎么样'}, {'owner': 'A1'})
    assert policy['provider'] == 'llm-router'
    assert policy['role'] == 'A6'


def test_router_whitelists_model_role(router_env, monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        return _RouterReply(_router_chat({'content': '{"role":"A999"}'}))

    monkeypatch.setattr(super_agent.intake.urllib.request, 'urlopen', fake_urlopen)
    policy = super_agent._semantic_policy(
        'user_message', {'text': '这个项目整体怎么样'}, {'owner': 'A1'})
    assert policy['provider'] == 'fallback'
    assert policy['used'] is False
    assert policy['role'] is None


def test_router_model_failure_falls_back_honestly(router_env, monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=None, context=None):
        raise urllib.error.HTTPError(request.full_url, 429, 'Too Many Requests', {}, None)

    monkeypatch.setattr(super_agent.intake.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(super_agent.intake.time, 'sleep', lambda s: None)
    policy = super_agent._semantic_policy(
        'user_message', {'text': '这个项目整体怎么样'}, {'owner': 'A1'})
    assert policy['provider'] == 'fallback'
    assert policy['used'] is False
    assert policy['role'] is None


def test_trigger_audit_records_model_route_evidence(router_env, monkeypatch, tmp_path):
    """模型分派的决策证据（provider/role/detail）要落到 trigger 审计里。"""
    monkeypatch.setenv('PITGUARD_DB_PATH', str(tmp_path / 'projects.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_ONE_STRUT_STORE', str(tmp_path / 'one_strut'))
    from app.storage.repository import ProjectRepository, shared_project_store
    from app.schemas.domain import Project

    repo = ProjectRepository(store=shared_project_store(tmp_path / 'projects.sqlite3'))
    pid = repo.create(Project(name='证据链')).id

    def fake_urlopen(request, timeout=None, context=None):
        return _RouterReply(_router_chat({'content': '{"role":"A3"}'}))

    monkeypatch.setattr(super_agent.intake.urllib.request, 'urlopen', fake_urlopen)
    result = super_agent.ingest_trigger(
        project_id=pid, kind='user_message',
        payload={'text': '这个项目整体怎么样'}, actor='alice')
    assert result['policy']['provider'] == 'llm-router'

    trigger = result['trigger']
    assert trigger['policyProvider'] == 'llm-router'
    assert trigger['policyRole'] == 'A3'
    assert trigger['policyDetail'] and 'Slow Path' in trigger['policyDetail']


def test_trigger_audit_marks_fast_path_without_model(api):
    """规则命中的决策，审计里 provider 是 deterministic、无模型角色。"""
    pid = _project(api, '规则证据')
    result = super_agent.ingest_trigger(
        project_id=pid, kind='user_message',
        payload={'text': '施工荷载 42kPa'}, actor='alice')
    assert result['policy']['provider'] == 'deterministic'
    trigger = result['trigger']
    assert trigger['policyProvider'] == 'deterministic'
    assert trigger['policyRole'] == 'A1'
