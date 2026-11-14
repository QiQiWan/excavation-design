"""验收案例集：证据缺失必须记为不可测，不能伪装成算法 0 分。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'verification' / 'acceptance' / 'run_acceptance.py'


def _load_runner():
    spec = importlib.util.spec_from_file_location('acceptance_runner', RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_missing_frozen_snapshot_is_not_a_zero_score(tmp_path):
    runner = _load_runner()
    case = {
        'id': 'case-missing',
        'expectedTop3': ['A', 'B', 'C'],
        'resultHash': 'a' * 64,
    }
    rows = runner.run_cases([case], str(tmp_path / 'one_strut'))
    assert len(rows) == 1
    row = rows[0]
    assert row['evaluated'] is False
    assert row['hit'] is False
    assert row['matched'] == []
    assert row['error'] and 'FileNotFoundError' in row['error']
    # 上层要能据此把命中率记为 None（not_measured），而不是 0.0
    evaluated = [item for item in rows if item['evaluated']]
    assert not evaluated


def test_all_cases_unavailable_makes_case_hit_rate_none(tmp_path):
    runner = _load_runner()
    cases = [
        {'id': f'case-{i}', 'expectedTop3': ['A'], 'resultHash': str(i) * 64}
        for i in range(1, 4)
    ]
    rows = runner.run_cases(cases, str(tmp_path / 'one_strut'))
    hit_rate = (sum(1 for r in rows if r['hit']) / len(rows)) if rows else None
    evaluated = [r for r in rows if r['evaluated']]
    assert not evaluated
    assert (sum(1 for r in evaluated if r['hit']) / len(evaluated) if evaluated else None) is None
    # 旧口径会把它们算成 0 分，这里显式记录区别
    assert hit_rate == 0.0
