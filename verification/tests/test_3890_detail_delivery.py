"""A2 建模、A7 构件深化、A8 成果与校审：路由、空项目盘点与正式签发拒绝。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import delivery_agent, detailing_agent, geology_agent, super_agent
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


def _project(client, name='深化与成果'):
    created = client.post('/api/projects', json={
        'name': name, 'location': '本地',
        'designSettings': {'surcharge': 20, 'defaultConcreteGrade': 'C35',
                           'defaultCoverMm': 70},
    }, headers=headers())
    assert created.status_code in (200, 201)
    return created.json()['id']


def test_route_detail_and_delivery_questions():
    route = super_agent._route_role
    assert route('user_message', {'text': '配筋和节点深化现在什么情况'}) == 'A7'
    assert route('user_message', {'text': '钢筋笼下料和套筒排得怎么样'}) == 'A7'
    assert route('user_message', {'text': '成果包能出了吗，审签到哪一步'}) == 'A8'
    assert route('user_message', {'text': '图纸校审还差什么'}) == 'A8'
    # 参数录入与诊断仍然优先
    assert route('user_message', {'text': '施工荷载 40kPa，混凝土 C45'}) == 'A1'
    assert route('user_message', {'text': '这个计算哪里失败了'}) == 'A5'


def test_blueprint_lists_all_eight_executable(api):
    data = api.get('/api/agents/super/blueprint', headers=headers()).json()
    assert data['workerCount'] == 8
    assert data['executableWorkers'] == ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8']
    assert all(row['executable'] for row in data['workers'])
    assert data['formalConstructionIssue'] is False


def test_geology_build_without_boreholes_reports_honestly(api):
    pid = _project(api, '空地质')
    result = geology_agent.assess(pid, 'alice', build=True)
    assert result['generated'] == 0
    assert result['label'] == '地质模型生成失败'
    assert '没有钻孔' in (result['buildError'] or '')
    assert result['status'] == 'blocked'
    # 盘点结论仍然完整，且没有伪造模型
    assert result['model']['built'] is False
    assert result['formalConstructionIssue'] is False


def test_scheme_shell_without_zones_is_not_treated_as_applied():
    # 真实缺陷回归：只有 checks/constructability 的 rebarDesignScheme 空壳
    # 曾被当成"已应用方案"，导致 A7 误报可深化。
    assert detailing_agent._has_scheme({}) is False
    assert detailing_agent._has_scheme({'checks': {}, 'constructability': {}}) is False
    assert detailing_agent._has_scheme({'wallZones': []}) is False
    assert detailing_agent._has_scheme({'wallZones': [{'zoneId': 'Z1'}]}) is True


def test_detail_assessment_is_blocked_on_empty_project(api):
    pid = _project(api, '空深化')
    result = detailing_agent.assess(pid, 'alice')
    assert result['mode'] == 'assessment'
    assert result['status'] == 'blocked'
    keys = {gap['key'] for gap in result['gaps']}
    assert {'retainingSystem', 'calculation'} <= keys
    assert result['bars']['count'] == 0
    assert result['deepening']['executed'] is False
    assert result['suggestedNext']
    assert result['formalConstructionIssue'] is False


def test_detail_generate_refuses_without_prerequisites(api):
    pid = _project(api, '空深化生成')
    result = detailing_agent.generate(pid, 'alice')
    assert result['status'] == 'blocked'
    assert '设计基准' in result['summary'] or '围护结构' in result['summary']
    assert result['formalConstructionIssue'] is False


def test_delivery_assessment_is_blocked_without_calculation(api):
    pid = _project(api, '空成果')
    result = delivery_agent.assess(pid, 'alice')
    assert result['status'] == 'blocked'
    keys = {gap['key'] for gap in result['gaps']}
    assert 'calculation' in keys
    assert result['readiness']['fail'] >= 1
    assert result['review']['status']
    assert result['formalConstructionIssue'] is False


def test_delivery_prepare_refuses_formal_issue(api):
    pid = _project(api, '正式签发')
    result = delivery_agent.prepare(pid, 'alice', '帮我正式签发施工图并盖章出图')
    assert result['status'] == 'blocked'
    assert '正式施工图签发不向 Agent 开放' in result['summary']
    assert 'taskId' not in result
    assert result['formalConstructionIssue'] is False


def test_delivery_prepare_blocks_without_calculation(api):
    pid = _project(api, '空成果包')
    result = delivery_agent.prepare(pid, 'alice', '出成果包')
    assert result['status'] == 'blocked'
    assert '没有计算结果' in result['summary']


def test_invalidated_calculation_is_reported_as_stale_not_missing():
    from types import SimpleNamespace
    from app.agents.detailing_agent import _calculation_note

    fresh = SimpleNamespace(advanced_engineering={})
    stale = SimpleNamespace(advanced_engineering={
        'invalidatedCalculationArchive': {'resultId': 'calc-old'},
        'requiresRecalculation': True,
    })
    assert '没有计算结果' in _calculation_note(fresh)
    assert '已失效' in _calculation_note(stale)
