"""扫描件视觉转录：结构归一化、空白页、未配置时的行为。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents import document_agent, vision_reader  # noqa: E402


def _payload(content, finish="stop") -> bytes:
    return json.dumps({"choices": [{"finish_reason": finish,
                                    "message": {"content": content}}]}).encode()


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("PITGUARD_LLM_ENABLE", "1")
    monkeypatch.setenv("PITGUARD_LLM_API_KEY", "test-key")
    monkeypatch.setenv("PITGUARD_LLM_MODEL_FAST", "test-model")
    monkeypatch.setenv("PITGUARD_LLM_BASE_URL", "https://example.com/v1")
    return monkeypatch


def test_read_image_returns_text(llm_env):
    text = vision_reader.read_image(
        b"\xff\xd8fake-jpeg", "image/jpeg",
        transport=lambda endpoint, payload, key: _payload("桩径：0.7m\n桩长：7.6m"))
    assert "桩径" in text and "7.6" in text


def test_read_image_joins_block_content(llm_env):
    blocks = [{"type": "text", "text": "第一行\n"}, {"type": "text", "text": "第二行"}]
    text = vision_reader.read_image(
        b"\xff\xd8fake-jpeg", "image/jpeg",
        transport=lambda endpoint, payload, key: _payload(blocks))
    assert text == "第一行\n第二行"


def test_read_image_allows_blank_slice(llm_env):
    text = vision_reader.read_image(
        b"\xff\xd8fake-jpeg", "image/jpeg",
        transport=lambda endpoint, payload, key: _payload(""))
    assert text == ""


def test_read_image_requires_configuration(monkeypatch):
    monkeypatch.setenv("PITGUARD_LLM_ENABLE", "0")
    with pytest.raises(ValueError):
        vision_reader.read_image(b"\xff\xd8x", "image/jpeg")


def test_transcribe_fills_pages_without_text(monkeypatch, llm_env):
    monkeypatch.setattr(vision_reader, "document_pages",
                        lambda data: ["施工荷载按 30kPa 计算，基坑安全等级为三级。"])
    pages, used, warnings = document_agent._pages_with_vision(b"%PDF-fake", ["", "文字层已有内容足够长"])
    assert used is True
    assert pages[0].startswith("施工荷载")
    assert pages[1] == "文字层已有内容足够长"
    assert warnings and "视觉模型" in warnings[0]


def test_transcribe_skips_when_vision_unavailable(monkeypatch):
    monkeypatch.setenv("PITGUARD_LLM_ENABLE", "0")
    pages, used, warnings = document_agent._pages_with_vision(b"%PDF-fake", ["", ""])
    assert used is False and warnings == [] and pages == ["", ""]
