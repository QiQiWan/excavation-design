"""LLM 接入后的安全边界：模型只产候选字段，白名单与回退始终生效。

不依赖真实外部服务；用可控响应验证解析、过滤与诚实回退。
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from app.agents import design_basis_intake as intake


class _Reply:
    """最小 urlopen 替身。"""

    headers = {'Content-Length': '0'}

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, _limit=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _chat_response(content: str) -> bytes:
    return json.dumps({
        'choices': [{'message': {'content': content}}],
        'usage': {'total_tokens': 12},
    }).encode('utf-8')


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv('PITGUARD_LLM_ENABLE', '1')
    monkeypatch.setenv('PITGUARD_LLM_API_KEY', 'unit-test-placeholder')
    monkeypatch.setenv('PITGUARD_LLM_MODEL_FAST', 'unit-test-model')
    monkeypatch.setenv('PITGUARD_LLM_BASE_URL', 'http://127.0.0.1:9/v1')
    monkeypatch.setenv('PITGUARD_LLM_ALLOWED_HOSTS', '127.0.0.1')
    monkeypatch.setenv('PITGUARD_LLM_TIMEOUT', '3')


def test_unconfigured_model_raises_and_never_calls_network(monkeypatch):
    for name in ('PITGUARD_LLM_ENABLE', 'PITGUARD_LLM_API_KEY',
                 'PITGUARD_LLM_MODEL_FAST', 'PITGUARD_LLM_MODEL_REASONING'):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(intake.ModelNotConfigured):
        intake.interpret_with_model('施工荷载 30kPa')
    assert intake.provider_status('model')['modelStatus'] == 'not_configured'


def test_model_output_is_whitelisted_and_coerced(llm_env, monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(request.full_url)
        return _Reply(_chat_response('```json\n' + json.dumps({'fields': [
            {'key': 'surcharge', 'value': '66'},              # 字符串数字应归一
            {'key': 'defaultConcreteGrade', 'value': 'C60'},   # 合法
            {'key': 'groundwaterLevel', 'value': -3},          # 不在白名单，拒绝
            {'key': 'surcharge', 'value': 99999},              # 越界，拒绝
            {'key': 'excavationSafetyLevel', 'value': '特级'},  # 非法枚举，拒绝
            {'key': 'defaultCoverMm', 'value': 55},            # 合法
        ]}) + '\n```'))

    monkeypatch.setattr(intake.urllib.request, 'urlopen', fake_urlopen)
    fields = intake.interpret_with_model('这个基坑超载取 66kPa，混凝土用 C60，保护层 55mm')
    assert calls
    assert {item['key'] for item in fields} == {
        'surcharge', 'defaultConcreteGrade', 'defaultCoverMm',
    }
    assert next(x['value'] for x in fields if x['key'] == 'surcharge') == 66
    # 后续写入通道仍会再做一次白名单校验
    assert {item['key'] for item in intake.normalize(fields)} == {
        'surcharge', 'defaultConcreteGrade', 'defaultCoverMm',
    }


def test_model_http_failure_falls_back_honestly(llm_env, monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, None)

    monkeypatch.setattr(intake.urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(intake.ModelCallFailed) as exc:
        intake.interpret_with_model('施工荷载 30kPa')
    assert '401' in str(exc.value)


def test_rules_provider_never_contacts_model(llm_env, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('rules 模式不应调用模型')

    monkeypatch.setattr(intake.urllib.request, 'urlopen', forbidden)
    status = intake.provider_status('rules')
    assert status['modelStatus'] == 'rules_forced'
    assert status['providerUsed'] == intake.RULES_PROVIDER
