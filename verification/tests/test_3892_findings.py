"""EngineeringFinding / ProposedAction：幂等、严重度映射、人工决策与权限。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import findings
from app.agents.store import AgentStore, ConflictError
from app.main import app


def headers(key: str = 'key-designer'):
    return {'X-PitGuard-Key': key}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('PITGUARD_DB_PATH', str(tmp_path / 'projects.sqlite3'))
    monkeypatch.setenv('PITGUARD_AGENT_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('PITGUARD_API_KEYS', json.dumps({
        'key-designer': {'role': 'designer', 'actor': 'alice'},
        'key-checker': {'role': 'checker', 'actor': 'bob'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    with TestClient(app) as client:
        yield client


_ISSUES = [
    {'code': 'DISPLACEMENT_OVER_LIMIT', 'category': 'physical_constraint',
     'field': 'checks.displacement', 'message': '位移 42mm 超过限值 30mm'},
    {'code': 'NORMATIVE_STABILITY', 'category': 'normative_coverage',
     'field': 'stability', 'message': '验算状态：fail'},
]


def test_diagnosis_findings_are_idempotent_and_severity_mapped(api):
    first = findings.record_diagnosis(project_id='p1', run_id='r1', issues=_ISSUES,
                                      evidence={'resultHash': 'abc'})
    assert [row['severity'] for row in first] == ['blocker', 'warning']
    assert first[0]['code'] == 'DISPLACEMENT_OVER_LIMIT'
    assert first[0]['evidence']['resultHash'] == 'abc'
    assert first[0]['status'] == 'open'

    again = findings.record_diagnosis(project_id='p1', run_id='r1', issues=_ISSUES)
    assert [row['id'] for row in again] == [row['id'] for row in first]
    assert len(AgentStore().list_findings('p1')) == 2


def test_plan_actions_are_idempotent_and_need_manual_decision(api):
    actions = [{'field': 'strut.stiffness_kn_m', 'before': 1.0, 'after': 1.2,
                'reason': '对支撑刚度进行有界敏感性试算',
                'permission': 'L1_candidate_only'}]
    rows = findings.record_plan(project_id='p1', run_id='r1', actions=actions)
    assert rows[0]['title'] == 'strut · stiffness_kn_m：1 → 1.2'
    assert rows[0]['status'] == 'proposed'
    assert rows[0]['params']['after'] == 1.2

    findings.record_plan(project_id='p1', run_id='r1', actions=actions)
    assert len(AgentStore().list_actions('p1')) == 1

    decided = findings.decide(rows[0]['id'], decision='accepted', actor='bob', note='同意试算')
    assert decided['status'] == 'accepted'
    assert decided['decidedBy'] == 'bob'
    with pytest.raises(ConflictError):
        findings.decide(rows[0]['id'], decision='rejected', actor='bob')


def test_deciding_requires_checker_and_never_touches_project(api):
    action = AgentStore().create_action(
        project_id='p-demo', run_id='r1', title='pile · length_m：18 → 19',
        rationale='被动土反力合力超限',
        params={'field': 'pile.length_m', 'before': 18, 'after': 19})

    denied = api.post(f"/api/agents/actions/{action['id']}/decision",
                      headers=headers(), json={'decision': 'accepted'})
    assert denied.status_code == 403

    accepted = api.post(f"/api/agents/actions/{action['id']}/decision",
                        headers=headers('key-checker'),
                        json={'decision': 'accepted', 'note': '同意做一次试算'})
    assert accepted.status_code == 200
    body = accepted.json()
    assert body['status'] == 'accepted' and body['decidedBy'] == 'bob'
    # 决策只改动作状态，没有任何工程数据被改写。
    assert body['params']['before'] == 18

    repeat = api.post(f"/api/agents/actions/{action['id']}/decision",
                      headers=headers('key-checker'), json={'decision': 'rejected'})
    assert repeat.status_code == 409

    overview = api.get('/api/agents/findings', headers=headers(),
                       params={'project_id': 'p-demo'}).json()
    assert overview['counts'] == {'findings': 0, 'blockers': 0, 'warnings': 0,
                                  'actions': 1, 'proposed': 0}
    assert overview['formalConstructionIssue'] is False


def test_unknown_action_and_bad_status(api):
    missing = api.post('/api/agents/actions/act-missing/decision',
                       headers=headers('key-checker'), json={'decision': 'accepted'})
    assert missing.status_code == 404
    bad = api.get('/api/agents/findings', headers=headers(),
                  params={'project_id': 'p-demo', 'status': 'weird'})
    assert bad.status_code == 422
