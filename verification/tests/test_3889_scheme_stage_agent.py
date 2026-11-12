"""A3 支护方案 / A4 施工阶段：意图分流与空项目盘点。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import scheme_stage_agent, super_agent
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


def _project(client, name='方案阶段盘点'):
    created = client.post('/api/projects', json={
        'name': name, 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    assert created.status_code in (200, 201)
    return created.json()['id']


def test_route_scheme_and_stage_questions():
    route = super_agent._route_role
    assert route('user_message', {'text': '支护方案够不够，有没有比选过'}) == 'A3'
    assert route('user_message', {'text': '围护墙和支撑布置现在什么情况'}) == 'A3'
    assert route('user_message', {'text': '施工阶段有没有断档，换撑怎么排'}) == 'A4'
    assert route('user_message', {'text': '开挖顺序合理吗'}) == 'A4'
    # 优化类仍然优先 A6，参数类仍然 A1
    assert route('user_message', {'text': '帮我把支撑截面优化一下'}) == 'A6'
    assert route('user_message', {'text': '施工荷载 40kPa'}) == 'A1'


def test_scheme_assessment_on_empty_project(api):
    pid = _project(api)
    result = scheme_stage_agent.assess_scheme(pid, 'alice')
    assert result['mode'] == 'assessment'
    assert result['label'] == '支护方案盘点'
    assert result['status'] == 'blocked'
    keys = {gap['key'] for gap in result['gaps']}
    assert {'retainingSystem', 'candidates'} <= keys
    assert result['engineeringJudgement'] is False
    assert result['formalConstructionIssue'] is False


def test_requested_action_only_generates_on_explicit_words():
    assert super_agent._requested_action('帮我生成一版支护方案') == 'generate'
    assert super_agent._requested_action('出几个候选方案看看') == 'generate'
    assert super_agent._requested_action('支护方案现在什么情况') == 'assess'
    assert super_agent._requested_action(None) == 'assess'


def test_scheme_generation_requires_confirmed_basis(api):
    pid = _project(api)
    result = scheme_stage_agent.generate_scheme(pid, 'alice')
    assert result['status'] == 'blocked'
    assert result['gaps'][0]['key'] == 'designBasisConfirmed'
    assert '设计基准' in result['summary']


def test_stage_generation_without_excavation_does_not_fake_success(api):
    pid = _project(api)
    result = scheme_stage_agent.assess_stages(pid, 'alice', generate=True)
    assert result['generated'] == 0
    assert result['label'] in ('施工阶段未生成', '施工阶段生成失败')
    assert result['engineeringJudgement'] is False


def test_stage_assessment_on_empty_project(api):
    pid = _project(api)
    result = scheme_stage_agent.assess_stages(pid, 'alice')
    assert result['label'] == '施工阶段盘点'
    assert result['status'] == 'blocked'
    keys = {gap['key'] for gap in result['gaps']}
    assert 'controlStages' in keys
    assert result['suggestedNext']
    assert result['engineeringJudgement'] is False
