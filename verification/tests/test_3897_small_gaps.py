"""三个口径：参数冲突留痕、候选指到构件、模型调用费用。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import design_basis_intake as intake
from app.agents import engine, findings
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
    monkeypatch.delenv('PITGUARD_LLM_ENABLE', raising=False)
    with TestClient(app) as client:
        yield client


def _project(client):
    created = client.post('/api/projects', json={
        'name': '口径验证', 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    assert created.status_code in (200, 201)
    return created.json()['id']


def test_overriding_a_previously_filled_parameter_is_flagged(api):
    pid = _project(api)
    first = intake.ingest(project_id=pid, actor='alice', provider_requested='rules',
                          text='施工荷载 44kPa，混凝土等级 C45，保护层厚度 62mm')
    assert first['mode'] == 'filled'
    assert first['conflicts'] == []

    second = intake.ingest(project_id=pid, actor='alice', provider_requested='rules',
                           text='施工荷载 52kPa，混凝土等级 C45，保护层厚度 66mm')
    assert second['mode'] == 'filled'
    keys = {row['key'] for row in second['conflicts']}
    assert keys == {'surcharge', 'defaultCoverMm'}
    by_key = {row['key']: row for row in second['conflicts']}
    assert by_key['surcharge']['previous'] == 44
    assert by_key['surcharge']['value'] == 52

    overview = findings.overview(pid)
    conflicts = [row for row in overview['findings']
                 if row['code'].startswith('PARAMETER_CONFLICT:')]
    assert len(conflicts) == 2
    assert {row['severity'] for row in conflicts} == {'warning'}
    assert {row['category'] for row in conflicts} == {'parameter_conflict'}

    # 值改回一致时，旧冲突标记应关闭，而不是一直挂着。
    third = intake.ingest(project_id=pid, actor='alice', provider_requested='rules',
                          text='施工荷载 52kPa，混凝土等级 C45，保护层厚度 62mm')
    assert {row['key'] for row in third['conflicts']} == {'defaultCoverMm'}
    rows = {row['code']: row['status']
            for row in findings.overview(pid)['findings']
            if row['code'].startswith('PARAMETER_CONFLICT:')}
    assert rows['PARAMETER_CONFLICT:defaultCoverMm'] == 'open'
    assert rows.get('PARAMETER_CONFLICT:surcharge', 'resolved') == 'resolved'


def test_candidate_action_points_to_a_concrete_member(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'actions.sqlite3'))
    snapshot = {
        'project_id': 'project-demo', 'section_id': 'SEC-1',
        'pile': {'diameter_m': 0.8, 'length_m': 18.0,
                 'inertia_factor': 0.8, 'elastic_modulus_kpa': 3e7},
        'strut': {'id': 'STRUT-1', 'depth_m': 1.6, 'stiffness_kn_m': 30000},
    }
    pile_action = engine._action(snapshot, 'pile.length_m', 1.0, '嵌固不足')
    assert pile_action['member']['type'] == 'pile'
    assert pile_action['member']['id'] == 'project-demo/SEC-1'
    assert pile_action['operation'] == 'calculate_isolated_candidate'

    strut_action = engine._action(snapshot, 'strut.stiffness_kn_m', 1.2, '位移超限')
    assert strut_action['member'] == {
        'type': 'strut', 'id': 'STRUT-1',
        'label': '水平支撑（标高 1.6 m）', 'resolved': True,
    }

    rows = findings.record_plan(project_id='project-demo', run_id='run-1',
                                actions=[pile_action, strut_action])
    assert len(rows) == 2
    saved = {row['memberType']: row for row in rows}
    assert saved['pile']['memberId'] == 'project-demo/SEC-1'
    assert saved['strut']['memberId'] == 'STRUT-1'


def test_model_call_cost_is_estimated_only_when_prices_configured(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'calls.sqlite3'))
    monkeypatch.delenv('PITGUARD_LLM_PRICE_INPUT_PER_MTOK', raising=False)
    monkeypatch.delenv('PITGUARD_LLM_PRICE_OUTPUT_PER_MTOK', raising=False)

    intake._audit_model_call('project-x', 'qwen3.8-27b', True, 'used', 1, 0.0,
                             {'prompt_tokens': 1200, 'completion_tokens': 300}, None)
    rows = intake.AgentStore().model_calls(project_id='project-x', limit=5)
    assert rows[0]['costUsd'] is None
    assert rows[0]['costSource'] == 'not_configured'

    monkeypatch.setenv('PITGUARD_LLM_PRICE_INPUT_PER_MTOK', '1.0')
    monkeypatch.setenv('PITGUARD_LLM_PRICE_OUTPUT_PER_MTOK', '2.0')
    intake._audit_model_call('project-x', 'qwen3.8-27b', True, 'used', 1, 0.0,
                             {'prompt_tokens': 1_000_000, 'completion_tokens': 500_000}, None)
    rows = intake.AgentStore().model_calls(project_id='project-x', limit=5)
    assert rows[0]['costUsd'] == pytest.approx(2.0)
    assert rows[0]['costSource'] == 'configured_per_mtok'
