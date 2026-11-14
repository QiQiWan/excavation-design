"""验收案例集：排序规则与案例文件自检（不依赖运行库）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from verification.acceptance.run_acceptance import ranked_codes


def test_ranking_puts_actionable_failures_before_source_noise():
    issues = [
        {'code': 'SOURCE_MISSING'},
        {'code': 'NORMATIVE_3_1_LOAD_COMBINATIONS'},
        {'code': 'REBAR_NOT_FULLY_VERIFIED'},
        {'code': 'DISPLACEMENT_OVER_LIMIT'},
        {'code': 'SOURCE_MISSING'},
    ]
    assert ranked_codes(issues) == [
        'DISPLACEMENT_OVER_LIMIT',
        'REBAR_NOT_FULLY_VERIFIED',
        'NORMATIVE_3_1_LOAD_COMBINATIONS',
        'SOURCE_MISSING',
    ]


def test_ranking_deduplicates_and_ignores_empty_codes():
    issues = [{'code': 'CHECK_A'}, {'code': ''}, {'code': 'CHECK_A'}, {'code': 'PASSIVE_RESULTANT_LIMIT'}]
    assert ranked_codes(issues) == ['PASSIVE_RESULTANT_LIMIT', 'CHECK_A']


def test_case_file_hashes_and_expectations_are_well_formed():
    path = Path(__file__).resolve().parents[1] / 'acceptance' / 'cases.json'
    bundle = json.loads(path.read_text(encoding='utf-8'))
    assert bundle['schemaVersion'] == 'pitguard.acceptance/1'
    assert len(bundle['cases']) >= 3
    ids = set()
    for case in bundle['cases']:
        assert case['id'] not in ids
        ids.add(case['id'])
        assert re.fullmatch(r'[0-9a-f]{64}', case['resultHash']), case['resultHash']
        assert len(case['expectedTop3']) == 3
        assert all(isinstance(code, str) and code for code in case['expectedTop3'])
        assert case.get('rationale')
