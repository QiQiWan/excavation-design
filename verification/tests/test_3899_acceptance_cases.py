"""验收案例集重建：快照必须真实可校验，且三个变体确实不同。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / 'verification' / 'acceptance' / 'build_cases.py'


def _load_builder():
    spec = importlib.util.spec_from_file_location('acceptance_builder', BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_rebuilt_snapshots_are_distinct_and_loadable(tmp_path):
    from app.engineering.single_braced_pile.repository import Repository
    from app.agents.engine import diagnostics

    builder = _load_builder()
    store = tmp_path / 'one_strut'
    rows = builder.build(store)
    assert len(rows) == 3
    hashes = [row['resultHash'] for row in rows]
    assert len(set(hashes)) == 3
    displacements = [row['displacementMm'] for row in rows]
    assert len(set(displacements)) == 3, '三个刚度变体应产生不同位移'
    assert all(row['analysisStatus'] == 'complete' for row in rows)

    repo = Repository(root=store)
    for row in rows:
        # load() 会重算规范哈希并校验快照，读不出来就说明证据不可信
        result = repo.load(row['resultHash'])
        assert result['snapshot']['project_id'] == row['projectId']
        assert result['snapshot']['section_id'] == row['sectionId']
        diag = diagnostics(result)
        assert diag['analysisStatus'] == 'complete'


def test_expected_top3_is_an_engineer_annotation_not_algorithm_output(tmp_path):
    builder = _load_builder()
    assert builder.ENGINEER_EXPECTED_TOP3 == [
        'STABILITY_NOT_INTEGRATED',
        'REBAR_NOT_FULLY_VERIFIED',
        'NORMATIVE_3_1_LOAD_COMBINATIONS',
    ]
    rows = builder.build(tmp_path / 'one_strut')
    assert all(row['expectedTop3'] == builder.ENGINEER_EXPECTED_TOP3 for row in rows)


def test_optimization_metric_reads_calculated_candidates():
    """真实候选计算的位移下降率能被验收运行器读出，而不是 not_measured。"""
    import json
    import sqlite3
    from app.agents.engine import measure
    from app.engineering.single_braced_pile.repository import Repository

    runner = importlib.util.spec_from_file_location(
        'run_acceptance', ROOT / 'verification' / 'acceptance' / 'run_acceptance.py')
    module = importlib.util.module_from_spec(runner)
    assert runner and runner.loader
    runner.loader.exec_module(module)

    # 用两个刚度不同的真实快照构造一条「基线→候选」记录，模拟候选计算写库。
    src = json.loads((ROOT / 'services' / 'api' / 'app' / 'engineering'
                      / 'single_braced_pile' / 'assets' / 'verification_example.json').read_text(encoding='utf-8'))
    repo = Repository(root=ROOT / 'runtime' / 'one_strut')
    base = repo.compute(src)
    changed = repo.compute({**src, 'pile': {**src['pile'],
                          'elastic_modulus_kpa': src['pile']['elastic_modulus_kpa'] * 1.3}})
    metrics = measure(changed, base)
    assert metrics['improvedDisplacement'] is True
    assert isinstance(metrics['baselineDisplacementMm'], float)
    assert isinstance(metrics['candidateDisplacementMm'], float)
    assert metrics['candidateDisplacementMm'] < metrics['baselineDisplacementMm']

    # 只读真实运行库，确认验收的 _optimization 能算出有限值。
    db = ROOT / 'services' / 'api' / 'pitguard_agent_v388.sqlite3'
    if not db.exists():
        pytest.skip('运行库不在，跳过优化有效率读取校验')
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        result = module._optimization(conn)
    finally:
        conn.close()
    assert result['calculated'] >= 1
    assert isinstance(result['improvedRate'], float)
    assert 0.0 <= result['improvedRate'] <= 1.0
