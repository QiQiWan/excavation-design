"""显式 EngineeringContext：有界、哈希稳定、变更可检测、端点一致。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import context
from app.main import app


def headers():
    return {'X-PitGuard-Key': 'key-designer'}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_DB_PATH', str(tmp_path / 'projects.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_API_KEYS', json.dumps({
        'key-designer': {'role': 'designer', 'actor': 'alice'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    with TestClient(app) as client:
        yield client


def _project(client, surcharge: float = 20) -> str:
    created = client.post('/api/projects', headers=headers(), json={
        'name': '上下文', 'location': '本地',
        'designSettings': {'surcharge': surcharge, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    })
    return created.json()['id']


def test_context_is_bounded_read_only_and_stable(api):
    pid = _project(api)
    first = context.build(pid, 'alice')
    second = context.build(pid, 'alice')
    assert first['schemaVersion'] == 'pitguard.engineering-context/1'
    assert first['contextHash'].startswith('ctx-')
    assert first['contextHash'] == second['contextHash']
    assert first['designBasis']['confirmed'] is False
    assert first['designBasis']['surcharge'] == 20
    assert first['retaining']['exists'] is False
    assert first['calculation']['count'] == 0
    assert first['agent']['findings'] == 0
    assert first['readOnly'] is True
    assert first['engineeringJudgement'] is False
    assert first['formalConstructionIssue'] is False


def test_context_hash_changes_with_design_basis(api):
    low = _project(api, 20)
    high = _project(api, 45)
    assert context.build(low, 'alice')['contextHash'] != context.build(high, 'alice')['contextHash']
    # 同一项目重复读取仍然稳定。
    assert context.build(low, 'alice')['contextHash'] == context.build(low, 'alice')['contextHash']


def test_context_endpoint_matches_builder_and_404(api):
    pid = _project(api)
    response = api.get('/api/agents/context', headers=headers(), params={'project_id': pid})
    assert response.status_code == 200
    assert response.json()['contextHash'] == context.build(pid, 'alice')['contextHash']
    missing = api.get('/api/agents/context', headers=headers(), params={'project_id': 'project-missing'})
    assert missing.status_code == 404


def test_joint_review_carries_context_hash(api):
    pid = _project(api)
    body = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'user_message', 'acceptMode': 'auto',
        'payload': {'text': '全流程体检'},
    }).json()
    outcome = body['execution']['outcome']
    assert outcome['context']['contextHash'].startswith('ctx-')
    assert outcome['context']['staleDuringRun'] is False
    assert outcome['context']['projectUpdatedAt']
