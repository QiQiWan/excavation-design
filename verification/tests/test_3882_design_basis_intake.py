"""设计基准自动录入：规则抽取、白名单、自动填入与缺项反馈。

LLM 尚未接入；测试明确断言「如实回退、不伪造模型输出」。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents import design_basis_intake as intake
from app.main import app
from app.schemas.domain import DesignSettings


class _Project:
    def __init__(self, applied=None):
        self.design_settings = DesignSettings()
        self.advanced_engineering = {'designBasisIntake': {'appliedKeys': applied or []}}


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
        'key-viewer': {'role': 'viewer', 'actor': 'view'},
    }))
    monkeypatch.delenv('PITGUARD_USERS', raising=False)
    with TestClient(app) as client:
        yield client


def test_interpret_text_extracts_only_unambiguous_scalars():
    text = ('施工荷载按 30kPa 计算，基坑安全等级三级，重要性系数 γ0=1.0，'
            '混凝土等级 C30，钢筋等级 HRB400，地基承载力特征值 180kPa，'
            '地下水位埋深 0.5m。')
    parsed = intake.interpret_text(text)
    keys = {field['key'] for field in parsed['fields']}
    assert {'surcharge', 'excavationSafetyLevel', 'importanceFactor',
            'defaultConcreteGrade', 'defaultRebarGrade', 'bearingCapacityKpa'} <= keys
    # 水位有勘察值/计算值口径歧义，规则不自动给值
    assert 'groundwaterLevel' not in keys


def test_normalize_rejects_unknown_and_out_of_range():
    accepted = intake.normalize([
        {'key': 'surcharge', 'value': 30},
        {'key': 'hack', 'value': 1},
        {'key': 'importanceFactor', 'value': 99},
    ])
    assert [item['key'] for item in accepted] == ['surcharge']


def test_model_provider_not_configured_falls_back_honestly():
    status = intake.provider_status('model')
    assert status['modelStatus'] == 'not_configured'
    assert status['providerUsed'] == intake.RULES_PROVIDER
    assert status['modelSettings']['toolExecutionByModel'] is False
    with pytest.raises(intake.ModelNotConfigured):
        intake.interpret_with_model('随便一段话')


def test_missing_summary_shrinks_after_fields_applied():
    fresh = intake.missing_summary(_Project())
    assert fresh['count'] == len(intake.REQUIRED)
    assert '还缺' in fresh['text']
    filled = intake.missing_summary(_Project([
        'surcharge', 'excavationSafetyLevel', 'importanceFactor', 'defaultConcreteGrade']))
    assert filled['count'] < fresh['count']
    assert '施工荷载' not in filled['text']


def test_parse_legacy_extraction_json():
    payload = json.dumps({'scalarParameters': [
        {'key': 'surchargeGeneral', 'value': 30, 'page': 4},
        {'key': 'safetyLevel', 'value': '三级', 'page': 2},
        {'key': 'unknown', 'value': 1},
    ]}, ensure_ascii=False)
    rows = intake.parse_extraction_json(payload)
    assert [(row['key'], row['value']) for row in rows] == [
        ('surcharge', 30), ('excavationSafetyLevel', '三级')]


def test_http_ingest_text_writes_and_reports_remaining(api):
    project = api.post('/api/projects', headers=headers(), json={'name': '录入测试'}).json()
    pid = project['id']
    conversation = api.post('/api/agents/conversations', headers=headers(),
                            json={'project_id': pid, 'title': '录入会话'}).json()

    response = api.post('/api/agents/design-basis/ingest', headers=headers(), data={
        'project_id': pid,
        'conversation_id': conversation['id'],
        'text': '施工荷载 30kPa，基坑安全等级三级，混凝土等级 C30，重要性系数 γ0=1.0。',
    })
    assert response.status_code == 200
    body = response.json()
    assert body['mode'] == 'filled'
    assert body['modelStatus'] == 'not_configured'
    assert body['providerUsed'] == intake.RULES_PROVIDER
    assert body['formalConstructionIssue'] is False
    assert {item['key'] for item in body['applied']} >= {
        'surcharge', 'excavationSafetyLevel', 'defaultConcreteGrade', 'importanceFactor'}

    updated = api.get('/api/projects/' + pid, headers=headers()).json()['designSettings']
    assert updated['surcharge'] == 30
    assert updated['excavationSafetyLevel'] == '三级'
    assert updated['defaultConcreteGrade'] == 'C30'
    assert updated['importanceFactor'] == 1.0

    gap = api.get('/api/agents/design-basis/missing', headers=headers(),
                  params={'project_id': pid}).json()
    assert gap['count'] < body['remaining']['count'] + 1
    assert '施工荷载' not in gap['text']

    turns = api.get('/api/agents/conversations/' + conversation['id'],
                    headers=headers()).json()['messages']
    assert any(turn['role'] == 'tool'
               and turn['metadata'].get('answerKind') == 'design_basis_intake'
               for turn in turns)


def test_http_ordinary_question_does_not_change_project(api):
    project = api.post('/api/projects', headers=headers(), json={'name': '普通提问'}).json()
    pid = project['id']
    before = api.get('/api/projects/' + pid, headers=headers()).json()['designSettings']['surcharge']
    response = api.post('/api/agents/design-basis/ingest', headers=headers(), data={
        'project_id': pid, 'text': '这一步现在卡在哪儿？',
    })
    assert response.status_code == 200
    assert response.json()['mode'] == 'receipt'
    assert api.get('/api/projects/' + pid, headers=headers()).json()['designSettings']['surcharge'] == before


def test_http_model_request_is_honest_about_fallback(api):
    project = api.post('/api/projects', headers=headers(), json={'name': '模型回退'}).json()
    response = api.post('/api/agents/design-basis/ingest', headers=headers(), data={
        'project_id': project['id'], 'provider': 'model', 'text': '施工荷载 30kPa',
    })
    assert response.status_code == 200
    body = response.json()
    assert body['modelTried'] is True
    assert body['modelStatus'] == 'not_configured'
    assert body['providerUsed'] == intake.RULES_PROVIDER
    assert any('未配置' in warning for warning in body['warnings'])


def test_http_viewer_cannot_write_and_bad_provider_rejected(api):
    project = api.post('/api/projects', headers=headers(), json={'name': '权限'}).json()
    denied = api.post('/api/agents/design-basis/ingest',
                      headers={'X-PitGuard-Key': 'key-viewer'},
                      data={'project_id': project['id'], 'text': '施工荷载 30kPa'})
    assert denied.status_code == 403
    bad = api.post('/api/agents/design-basis/ingest', headers=headers(),
                   data={'project_id': project['id'], 'text': '施工荷载 30kPa',
                         'provider': 'magic'})
    assert bad.status_code == 400
