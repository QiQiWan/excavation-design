#!/usr/bin/env python
"""从站点自带单剖面验证样例重建验收冻结快照与案例集（可重复运行）。

背景：冻结快照存放在 ``runtime/one_strut``，该目录被 .gitignore 忽略。
历史清理把快照删掉后，验收运行器读不到证据，指标会变成 not_measured。
这里用仓库内自带的 ``assets/verification_example.json`` 与产品自己的
``Repository.compute()`` 重新算出三个刚度变体，写入运行库并回写案例哈希。

案例的 ``expectedTop3`` 是 V3.88 验收时工程师按规范缺口人工标注的，
**不从算法排序结果读取**；脚本只负责生成真实快照、真实哈希和实测位移。
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / 'services' / 'api'))

from app.agents.engine import diagnostics  # noqa: E402
from app.engineering.single_braced_pile.repository import Repository  # noqa: E402

EXAMPLE = (ROOT / 'services' / 'api' / 'app' / 'engineering'
           / 'single_braced_pile' / 'assets' / 'verification_example.json')

# 工程师标注的规范缺口，先于「来源缺失」被指出。与诊断排序无关。
ENGINEER_EXPECTED_TOP3 = [
    'STABILITY_NOT_INTEGRATED',
    'REBAR_NOT_FULLY_VERIFIED',
    'NORMATIVE_3_1_LOAD_COMBINATIONS',
]

# 同一剖面、同一地层，只改围护桩刚度，用来检验诊断是否随位移漂移。
VARIANTS = [
    {'id': 'case-01', 'factor': 1.0,
     'title': '单桩剖面：整体稳定与配筋验算缺口',
     'rationale': '该剖面缺整体稳定集成、缺轴弯抗剪裂缝验算，且荷载组合未生成；这三项应先于来源缺失被指出。'},
    {'id': 'case-02', 'factor': 0.7,
     'title': '同一剖面降低桩刚度：缺口是否稳定复现',
     'rationale': '桩刚度降到 70% 后位移上升，规范缺口不应变化；若排序漂移说明诊断不稳定。'},
    {'id': 'case-03', 'factor': 1.4,
     'title': '同一剖面提高桩刚度：位移下降但结论一致',
     'rationale': '桩刚度提到 140% 后位移下降，仍缺同一批验算；用于确认诊断不是只盯位移。'},
]


def build(store: Path) -> list[dict]:
    base = json.loads(EXAMPLE.read_text(encoding='utf-8'))
    repo = Repository(root=store)
    rows: list[dict] = []
    for variant in VARIANTS:
        payload = copy.deepcopy(base)
        e0 = float(payload['pile']['elastic_modulus_kpa'])
        payload['pile']['elastic_modulus_kpa'] = e0 * variant['factor']
        result = repo.compute(payload)
        snapshot = result.get('snapshot') or {}
        diag = diagnostics(result)
        rows.append({
            'id': variant['id'],
            'kind': 'diagnosis',
            'title': variant['title'],
            'projectId': snapshot.get('project_id'),
            'sectionId': snapshot.get('section_id'),
            'resultHash': result['resultHash'],
            'source': 'runtime/one_strut/results',
            'sourceInput': 'services/api/app/engineering/single_braced_pile/assets/verification_example.json',
            'stiffnessFactor': variant['factor'],
            'displacementMm': round(float(diag['displacement']['valueMm'] or 0.0), 3),
            'expectedTop3': list(ENGINEER_EXPECTED_TOP3),
            'rationale': variant['rationale'],
            'analysisStatus': result['analysisStatus'],
        })
    hashes = [row['resultHash'] for row in rows]
    if len(set(hashes)) != len(hashes):
        raise ValueError('三个变体生成了相同的结果哈希，无法构成独立案例')
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--store', default=str(ROOT / 'runtime' / 'one_strut'))
    parser.add_argument('--cases', default=str(Path(__file__).parent / 'cases.json'))
    args = parser.parse_args()

    rows = build(Path(args.store))
    bundle = {
        'schemaVersion': 'pitguard.acceptance/1',
        'note': ('案例由 build_cases.py 从站点自带单剖面验证样例的三个刚度变体重新算出；'
                 'expectedTop3 沿用 V3.88 工程师按规范缺口的人工标注，不依赖算法排序结果。'
                 'runtime/ 不入库，快照丢失时重跑本脚本即可恢复。'),
        'cases': rows,
    }
    cases_path = Path(args.cases)
    cases_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n',
                          encoding='utf-8')
    for row in rows:
        print(f"{row['id']}  hash={row['resultHash'][:16]}  "
              f"E×{row['stiffnessFactor']}  位移={row['displacementMm']}mm")
    print(f'cases: {cases_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
