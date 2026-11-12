"""V3.88.0 收尾：参数来源、模型审计、项目版本冲突。

对应《PitGuard V3.87.28 智能体架构重构方案》第五、六节。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import design_basis_intake as intake
from app.agents import super_agent
from app.agents.store import AgentStore
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
    for name in ('PITGUARD_LLM_ENABLE', 'PITGUARD_LLM_API_KEY',
                 'PITGUARD_LLM_MODEL_FAST', 'PITGUARD_LLM_MODEL_REASONING'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(app) as client:
        yield client


def _project(client, name='数据合同测试'):
    created = client.post('/api/projects', json={
        'name': name, 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    assert created.status_code in (200, 201)
    return created.json()['id']


class _Reply:
    headers = {'Content-Length': '0'}

    def __init__(self, payload):
        self._payload = payload

    def read(self, _limit=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_every_applied_field_records_its_source(api):
    pid = _project(api)
    text = '施工荷载 37kPa，混凝土等级 C45，保护层厚度 66mm'
    response = api.post('/api/agents/design-basis/ingest',
                        data={'project_id': pid, 'text': text, 'provider': 'rules'},
                        headers=headers())
    assert response.status_code == 200
    body = response.json()
    applied = {x['key'] for x in body['applied']}
    assert {'surcharge', 'defaultConcreteGrade', 'defaultCoverMm'} <= applied

    evidence = api.get('/api/agents/evidence', params={'project_id': pid},
                       headers=headers()).json()['records']
    by_key = {row['fieldKey']: row for row in evidence}
    assert {'surcharge', 'defaultConcreteGrade', 'defaultCoverMm'} <= set(by_key)
    assert by_key['surcharge']['value'] == 37
    assert by_key['surcharge']['sourceType'] == 'text'
    assert by_key['surcharge']['provider'] == intake.RULES_PROVIDER
    assert by_key['surcharge']['capturedBy'] == 'alice'
    assert by_key['surcharge']['rawText']
    assert by_key['surcharge']['confirmed'] is False


def test_model_call_is_audited_even_when_it_falls_back(api, monkeypatch):
    pid = _project(api, '模型审计')
    monkeypatch.setenv('PITGUARD_LLM_ENABLE', '1')
    monkeypatch.setenv('PITGUARD_LLM_API_KEY', 'unit-test-placeholder')
    monkeypatch.setenv('PITGUARD_LLM_MODEL_FAST', 'unit-test-model')
    monkeypatch.setenv('PITGUARD_LLM_BASE_URL', 'http://127.0.0.1:9/v1')
    monkeypatch.setenv('PITGUARD_LLM_ALLOWED_HOSTS', '127.0.0.1')
    monkeypatch.setattr(intake.time, 'sleep', lambda seconds: None)

    def failing(request, timeout=None, context=None):
        import urllib.error
        raise urllib.error.URLError('connection refused')

    monkeypatch.setattr(intake.urllib.request, 'urlopen', failing)
    api.post('/api/agents/design-basis/ingest',
             data={'project_id': pid, 'text': '施工荷载 41kPa', 'provider': 'model'},
             headers=headers())

    calls = api.get('/api/agents/model-calls', params={'project_id': pid},
                    headers=headers()).json()['calls']
    assert calls
    latest = calls[0]
    assert latest['model'] == 'unit-test-model'
    assert latest['ok'] is False
    assert latest['status'] == 'fallback'
    assert latest['attempts'] >= 1
    assert latest['errorType'] == 'ModelCallFailed'


def test_ticket_is_stale_when_project_changed_while_queued(api):
    pid = _project(api, '版本冲突')
    store = AgentStore()
    ticket = store.create_ticket(
        project_id=pid, conversation_id=None, thread_id=None,
        role_id='A1', stage_id='DESIGN.BASIS',
        mandate={'formalConstructionIssue': False, 'allowedTools': [],
                 'writeMode': 'isolated_candidate'},
        status='accepted', accept_mode='auto',
        source_project_updated_at='2000-01-01T00:00:00+00:00',
    )
    result = super_agent.dispatch_ticket(ticket['id'], actor='alice',
                                         text='施工荷载 30kPa')
    assert result['dispatched'] is False
    assert result['status'] == 'stale'
    assert '已被修改' in result['reason']
    assert store.get_ticket(ticket['id'])['status'] == 'stale'
