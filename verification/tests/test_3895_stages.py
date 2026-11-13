"""阶段注册表：三套 P 编号不混用，Agent 阶段单一来源。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import orchestrator, stages, super_agent
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


def test_registry_covers_all_agent_stages_and_pipeline_codes():
    data = stages.registry()
    assert data['schemaVersion'] == 'pitguard.stage-registry/1'
    assert [row['code'] for row in data['pipelineStages']] == [f'P{i}' for i in range(1, 9)]
    assert len(data['agentStages']) == 8
    pipeline_codes = {row['code'] for row in data['pipelineStages']}
    for row in data['agentStages']:
        assert row['pipeline'], row['id']
        assert set(row['pipeline']) <= pipeline_codes, row['id']
    # A8 同时覆盖成果包（P7）与校审发行（P8）
    assert {row['id'] for row in data['agentStages'] if row['role'] == 'A8'} == {
        'DESIGN.DELIVERABLES', 'DELIVERY.REVIEW'}


def test_primary_stage_matches_super_agent_mapping():
    primary = stages.primary_stage_of_role()
    assert primary['A8'] == 'DELIVERY.REVIEW'
    for role, stage_id in primary.items():
        assert super_agent.STAGE_OF_ROLE[role] == stage_id
    # A6 复用计算阶段，不单独造一个阶段号
    assert super_agent.STAGE_OF_ROLE['A6'] == 'DESIGN.CALCULATION'


def test_normalize_accepts_agent_ids_and_rejects_bare_p_codes():
    assert stages.normalize('design.basis') == 'DESIGN.BASIS'
    assert stages.normalize('  DELIVERY.REVIEW ') == 'DELIVERY.REVIEW'
    described = stages.describe('DESIGN.SCHEME')
    assert described['role'] == 'A3'
    assert described['pipeline'] == ['P2', 'P3']
    with pytest.raises(ValueError, match='多个命名空间'):
        stages.normalize('P1')
    with pytest.raises(ValueError, match='未登记'):
        stages.normalize('DESIGN.UNKNOWN')


def test_orchestrator_stage_ids_exist_in_registry():
    for spec in orchestrator.STAGE_SPECS.values():
        assert stages.normalize(spec['stageId']) == spec['stageId']


def test_stage_registry_endpoint(api):
    response = api.get('/api/agents/stages', headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body['schemaVersion'] == 'pitguard.stage-registry/1'
    assert body['namespaces']['acceptance'].startswith('compliance')
    assert body['formalConstructionIssue'] is False
