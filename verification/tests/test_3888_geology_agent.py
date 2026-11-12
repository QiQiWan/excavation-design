"""A2 地质与场景：意图分流、参数缺口识别、空项目盘点。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agents import geology_agent, super_agent
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


def _project(client, name='地质盘点'):
    created = client.post('/api/projects', json={
        'name': name, 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    assert created.status_code in (200, 201)
    return created.json()['id']


def test_route_geology_questions_to_a2():
    route = super_agent._route_role
    assert route('user_message', {'text': '地质资料够不够，钻孔覆盖怎么样'}) == 'A2'
    assert route('user_message', {'text': '地下水位记录有哪些'}) == 'A2'
    assert route('user_message', {'text': '地层参数缺不缺'}) == 'A2'
    # 参数录入仍然优先给 A1
    assert route('user_message', {'text': '施工荷载 40kPa，混凝土 C45'}) == 'A1'


def test_stratum_gaps_flag_missing_and_unverified_parameters():
    strata = [
        SimpleNamespace(
            code='②-1',
            parameters=SimpleNamespace(unit_weight=18.0, cohesion=None,
                                       friction_angle=22.0, elastic_modulus=8.0),
            parameter_source='empirical', confidence='low'),
        SimpleNamespace(
            code='②-2',
            parameters=SimpleNamespace(unit_weight=19.0, cohesion=12.0,
                                       friction_angle=26.0, elastic_modulus=15.0),
            parameter_source='test', confidence='high'),
    ]
    incomplete, empirical, low = geology_agent._stratum_gaps(strata)
    assert len(incomplete) == 1
    assert '②-1' in incomplete[0] and '黏聚力' in incomplete[0]
    assert empirical == ['②-1']
    assert low == ['②-1']


def test_assess_empty_project_is_blocked_with_actionable_gaps(api):
    pid = _project(api)
    result = geology_agent.assess(pid, 'alice')
    assert result['mode'] == 'assessment'
    assert result['status'] == 'blocked'
    keys = {gap['key'] for gap in result['gaps']}
    assert {'boreholes', 'strata', 'geologicalModel'} <= keys
    assert all(gap['suggestion'] for gap in result['gaps'])
    assert result['suggestedNext']
    assert result['engineeringJudgement'] is False
    assert result['formalConstructionIssue'] is False
    assert '钻孔' in result['summary']
