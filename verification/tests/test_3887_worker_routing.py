"""桌宠意图分流与任务进展文案（V3.88.1）。"""
from __future__ import annotations

from app.agents import super_agent
from app.agents.store import AgentStore


def test_route_role_by_intent():
    route = super_agent._route_role
    # 参数描述优先给 A1，不能被「调整」之类的词抢走
    assert route('user_message', {'text': '施工荷载 42kPa，混凝土 C45，保护层 60mm'}) == 'A1'
    assert route('user_message', {'text': '把地面堆载调整到 50kPa'}) == 'A1'
    # 诊断
    assert route('user_message', {'text': '这个计算哪里失败了，帮我诊断一下'}) == 'A5'
    assert route('user_message', {'text': '支撑轴力超限是什么原因'}) == 'A5'
    # 优化
    assert route('user_message', {'text': '帮我把支撑截面优化一下，试算两个方案'}) == 'A6'
    assert route('user_message', {'text': '复算一版改小桩径的'}) == 'A6'
    # 无法判断 / 非用户消息
    assert route('user_message', {'text': '你好'}) is None
    assert route('document_uploaded', {'text': '诊断'}) is None


def test_event_notes_carry_concrete_progress():
    note = AgentStore._event_note
    assert '58' in note('diagnosis_completed', {'issueCount': 58})
    assert '2' in note('candidate_plan_created', {'candidateCount': 2})
    assert '候选 1' in note('candidate_evaluated', {'index': 0, 'status': 'calculated'})
    summary = note('candidate_comparison_completed',
                   {'candidateCount': 2, 'calculatedCount': 1})
    assert '2' in summary and '1' in summary and '人工复核' in summary
    assert '失败' in note('step_failed', {'step': 'evaluate', 'errorType': 'ConflictError'})
