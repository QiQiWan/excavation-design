#!/usr/bin/env python
"""模型 Router 可行性验证（独立脚本，不改产品代码）。

目的：验证端砚模型能不能当 Super-agent 的 Router 大脑——把一句人话分给
正确的 Worker（A1..A8），并和现有确定性关键词路由对比。

输出：逐条意图的「模型分派 vs 规则分派 vs 期望」，以及准确率统计。
只读、不写任何数据、不调产品接口。
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services' / 'api'))

from app.agents import super_agent  # noqa: E402

KEY = os.getenv('PITGUARD_LLM_API_KEY', '').strip()
MODEL = os.getenv('PITGUARD_LLM_MODEL_FAST', 'deepseek-v4.1-flash-1017').strip()
BASE = os.getenv('PITGUARD_LLM_BASE_URL', 'https://discovery-api.intern-ai.org.cn/v1').rstrip('/')

# 期望分派：与 test_3887 等现有测试一致的权威答案。
CASES = [
    ('施工荷载 42kPa，混凝土 C45，保护层 60mm', 'A1'),
    ('把地面堆载调整到 50kPa', 'A1'),
    ('这个计算哪里失败了，帮我诊断一下', 'A5'),
    ('支撑轴力超限是什么原因', 'A5'),
    ('帮我把支撑截面优化一下，试算两个方案', 'A6'),
    ('复算一版改小桩径的', 'A6'),
    ('钻孔数据不全，帮我看看地层', 'A2'),
    ('生成围护墙和支撑布置', 'A3'),
    ('施工阶段开挖顺序帮我排一下', 'A4'),
    ('配筋深化一下，出钢筋加工图', 'A7'),
    ('导出评审版成果包', 'A8'),
    ('你好', None),
    ('走一遍全流程', 'chain'),
    ('帮我看看现在有哪些问题', 'A5'),
]

ROLES = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'chain', 'none']

SYSTEM = (
    '你是岩土基坑设计系统的任务路由器（Router）。\n'
    '根据用户一句话，判断应该交给哪个 Worker 处理，只输出一个 JSON：'
    '{"role":"<ROLE>"}。\n'
    '可选角色：\n'
    'A1 资料与参数（施工荷载/超载/混凝土/保护层/钢筋等级/安全等级等设计基准）\n'
    'A2 地质与场景（钻孔/地层/水位/勘察）\n'
    'A3 支护方案（围护墙/支撑布置/候选比选）\n'
    'A4 施工阶段（开挖顺序/换撑/拆撑/工况）\n'
    'A5 计算与验算（诊断失败/哪里超限/查问题清单）\n'
    'A6 优化闭环（优化/试算/复算/改参数重算）\n'
    'A7 构件深化（配筋/钢筋笼/节点/加工图）\n'
    'A8 成果与校审（出图/导出成果包/签发）\n'
    'chain 连续执行（走一遍全流程/自动按流程走）\n'
    'none 无法判断或只是闲聊。\n'
    '只输出 JSON，不要解释。'
)


def ask(text: str) -> str:
    body = json.dumps({
        'model': MODEL,
        'temperature': 0,
        'max_tokens': 64,
        'messages': [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': text},
        ],
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(BASE + '/chat/completions', data=body, method='POST', headers={
        'Authorization': 'Bearer ' + KEY,
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=90, context=ssl.create_default_context()) as r:
        data = json.loads(r.read().decode('utf-8'))
    content = data['choices'][0]['message'].get('content') or ''
    content = content.strip()
    if content.startswith('```'):
        content = content.strip('`').strip()
    start, end = content.find('{'), content.rfind('}')
    if start < 0 or end <= start:
        return 'parse_error'
    try:
        role = json.loads(content[start:end + 1]).get('role', 'parse_error')
    except json.JSONDecodeError:
        return 'parse_error'
    return str(role)


def main() -> int:
    if not KEY:
        print('缺少 PITGUARD_LLM_API_KEY')
        return 2
    correct = 0
    total = 0
    print(f'model={MODEL}')
    print('-' * 70)
    for text, expected in CASES:
        expected_role = expected or 'none'
        rule = super_agent._route_role('user_message', {'text': text})
        # 规则路由不产 chain，把「走一遍全流程」这类单独标出来对比
        rule_mapped = 'chain' if expected == 'chain' else (rule or 'none')
        try:
            model_role = ask(text)
        except Exception as exc:
            model_role = f'ERR:{type(exc).__name__}'
        ok = (model_role == expected_role)
        total += 1
        if ok:
            correct += 1
        print(f'[{ "OK" if ok else "  " }] 期望={expected_role:6} 模型={model_role:14} 规则={rule_mapped:6} | {text}')
    print('-' * 70)
    print(f'准确率: {correct}/{total} = {correct/total:.0%}')
    return 0 if correct / total >= 0.8 else 1


if __name__ == '__main__':
    raise SystemExit(main())
