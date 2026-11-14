"""V3.91 联合流程：一次请求汇总 A2/A3/A4/A7/A8，且严格只读。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import joint_flow, super_agent
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
        'name': '联合体检', 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    return created.json()['id']


def test_joint_flow_covers_all_stages_in_dependency_order():
    assert [row[0] for row in joint_flow.STAGES] == ['A2', 'A3', 'A4', 'A7', 'A8']


def test_joint_review_runs_all_stages_and_stays_read_only(api):
    pid = _project(api)
    before = api.get(f'/api/projects/{pid}', headers=headers()).json()
    body = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'user_message', 'acceptMode': 'auto',
        'payload': {'text': '帮我做个全流程体检'},
    }).json()
    assert body['decision'] == 'accepted'
    execution = body['execution']
    assert execution['roleId'] == 'JOINT'
    assert execution['dispatched'] is True
    outcome = execution['outcome']
    assert outcome['schemaVersion'] == 'pitguard.joint-review/1'
    assert outcome['stageCount'] == 5
    assert [row['roleId'] for row in outcome['stages']] == ['A2', 'A3', 'A4', 'A7', 'A8']
    assert outcome['status'] == 'blocked'
    assert all(row['status'] == 'blocked' for row in outcome['stages'])
    assert outcome['readOnly'] is True
    assert outcome['formalConstructionIssue'] is False
    assert outcome['suggestedNext']
    # 只读：项目版本与更新时间都不能变。
    after = api.get(f'/api/projects/{pid}', headers=headers()).json()
    assert after['updatedAt'] == before['updatedAt']


def test_joint_review_observe_mode_does_not_execute(api):
    pid = _project(api)
    body = api.post('/api/agents/super/triggers', headers=headers(), json={
        'projectId': pid, 'kind': 'user_message', 'acceptMode': 'observe',
        'payload': {'text': '全流程体检'},
    }).json()
    assert body['decision'] == 'observed'
    assert 'execution' not in body
    route = super_agent._JOINT_HINTS
    assert any(word in '帮我做个全流程体检' for word in route)
