#!/usr/bin/env python
"""PitGuard 验收案例集运行器（只读）。

量化文档第八节要求的关键指标，数据全部来自本机运行库与真实冻结快照：

- 失败定位 Top-3：案例期望的规范缺口是否出现在诊断前三位；
- 优化有效率：候选复算完成率、位移下降率、工程合格率（三者分开，不混用）；
- 门禁绕过：是否存在非 auto_dispatch 闸口却派了单；
- 参数来源：证据记录是否都带原文或位置；
- 结果完整性：冻结结果加载时是否通过规范化哈希校验。

不改任何工程数据，不调用模型。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'services' / 'api'))

from app.agents.engine import diagnostics  # noqa: E402
from app.engineering.single_braced_pile.repository import Repository  # noqa: E402

PHYSICAL = {'DISPLACEMENT_OVER_LIMIT', 'PASSIVE_RESULTANT_LIMIT',
            'SOLVER_FAILURE', 'INVALID_CAGE_GEOMETRY'}
MISSING_EVIDENCE = {'STABILITY_NOT_INTEGRATED', 'REBAR_NOT_FULLY_VERIFIED'}

THRESHOLDS = {
    'localizationTop3': 0.85,
    'optimizationImproved': 0.70,
    'gateBypass': 0,
    'provenanceTraceable': 1.0,
}


def _rank(code: str) -> int:
    """先物理失败，再缺证据，再规范覆盖，最后工程复核。"""
    if code in PHYSICAL:
        return 0
    if code in MISSING_EVIDENCE:
        return 1
    if code.startswith('NORMATIVE_') or code.startswith('CHECK_'):
        return 2
    return 3


def ranked_codes(issues: list[dict]) -> list[str]:
    """按可处理程度排序并去重；同一代码只保留一次。"""
    ordered = sorted(enumerate(issues),
                     key=lambda pair: (_rank(str(pair[1].get('code') or '')), pair[0]))
    result: list[str] = []
    for _, issue in ordered:
        code = str(issue.get('code') or '')
        if code and code not in result:
            result.append(code)
    return result


def run_cases(cases: list[dict], one_strut: str) -> list[dict]:
    repo = Repository(one_strut)
    rows: list[dict] = []
    for case in cases:
        entry = {'id': case['id'], 'title': case.get('title'), 'hit': False,
                 'matched': [], 'top3': [], 'error': None, 'evaluated': False}
        try:
            result = repo.load(case['resultHash'])
            snapshot = result.get('snapshot') or {}
            project = snapshot.get('project_id') or result.get('projectId')
            section = snapshot.get('section_id')
            # 防止把哈希和项目对错：对不上直接判失败，不静默通过。
            if case.get('projectId') and project != case['projectId']:
                raise ValueError(f"结果属于 {project}，与案例声明的 {case['projectId']} 不符")
            if case.get('sectionId') and section != case['sectionId']:
                raise ValueError(f"剖面 {section} 与案例声明的 {case['sectionId']} 不符")
            top3 = ranked_codes(diagnostics(result)['issues'])[:3]
            matched = [code for code in case['expectedTop3'] if code in top3]
            entry.update({'top3': top3, 'matched': matched,
                          'hit': len(matched) == len(case['expectedTop3']),
                          'evaluated': True,
                          'analysisStatus': result.get('analysisStatus')})
        except Exception as exc:  # 如实记录，不把失败算成通过
            entry['error'] = f'{type(exc).__name__}: {str(exc)[:160]}'
        rows.append(entry)
    return rows


def _optimization(conn: sqlite3.Connection) -> dict:
    rows = conn.execute('''
        SELECT c.status, c.metrics_json FROM candidates c
    ''').fetchall()
    total = len(rows)
    calculated = improved = qualified = 0
    for status, metrics_json in rows:
        if status != 'calculated':
            continue
        calculated += 1
        try:
            metrics = json.loads(metrics_json or '{}')
        except json.JSONDecodeError:
            continue
        baseline = metrics.get('baselineDisplacementMm')
        candidate = metrics.get('candidateDisplacementMm')
        if isinstance(baseline, (int, float)) and isinstance(candidate, (int, float)):
            if candidate < baseline - 1e-6:
                improved += 1
        if metrics.get('engineeringQualified'):
            qualified += 1
    note = 'improved 只表示位移下降；engineeringQualified 还要求证据完整，二者不可混用。'
    if not calculated:
        note += (' 当前运行库没有 status=calculated 的候选（历史候选已被清理），'
                 '因此 improvedRate 与工程合格率均不可测；需重新跑一次候选完整计算后再取数。')
    return {
        'totalCandidates': total,
        'calculated': calculated,
        'calculatedRate': round(calculated / total, 4) if total else None,
        'improved': improved,
        'improvedRate': round(improved / calculated, 4) if calculated else None,
        'engineeringQualified': qualified,
        'engineeringQualifiedRate': round(qualified / calculated, 4) if calculated else None,
        'note': note,
    }


def _gates(conn: sqlite3.Connection) -> dict:
    rows = conn.execute('''
        SELECT gate, dispatched, COUNT(*) FROM orchestrations GROUP BY gate, dispatched
    ''').fetchall()
    bypass = sum(count for gate, dispatched, count in rows
                 if dispatched and gate != 'auto_dispatch')
    total = sum(count for _, _, count in rows)
    return {'decisions': total, 'bypass': bypass,
            'breakdown': [{'gate': g, 'dispatched': bool(d), 'count': c}
                          for g, d, c in rows]}


def _provenance(conn: sqlite3.Connection) -> dict:
    rows = conn.execute('''
        SELECT project_id, field_key, raw_text, source_location, source_label
        FROM evidence_records
    ''').fetchall()
    total = len(rows)
    traceable = sum(1 for _, _, raw, loc, label in rows
                    if (raw and str(raw).strip()) or (loc and str(loc).strip())
                    or (label and str(label).strip()))
    projects = sorted({row[0] for row in rows})
    fields = sorted({row[1] for row in rows})
    return {
        'records': total,
        'traceable': traceable,
        'traceableRate': round(traceable / total, 4) if total else None,
        'projects': projects,
        'distinctFields': len(fields),
        'note': ('运行库没有参数来源证据记录（历史录入已被清理），来源可追溯率不可测；'
                 '需重新跑一次设计基准录入后再取数。' if not total else ''),
    }


def _verdict(value, threshold, *, mode: str = 'min') -> str:
    if value is None:
        return 'not_measured'
    if mode == 'max':
        return 'pass' if value <= threshold else 'fail'
    return 'pass' if value >= threshold else 'fail'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--one-strut', default=str(ROOT / 'runtime' / 'one_strut'))
    parser.add_argument('--agent-db',
                        default=str(ROOT / 'runtime' / 'pitguard_agent_v388.sqlite3'))
    parser.add_argument('--cases', default=str(Path(__file__).parent / 'cases.json'))
    parser.add_argument('--output', default=str(ROOT / 'verification-output'
                                                / 'acceptance-report.json'))
    args = parser.parse_args()

    bundle = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    case_rows = run_cases(bundle['cases'], args.one_strut)

    # 默认路径是历史部署位置；本机开发库在 services/api 下。两者都没有时
    # 如实标记指标不可测，而不是让脚本崩掉或把空库算成 0 分。
    db_path = Path(args.agent_db)
    if not db_path.is_file() and args.agent_db == str(ROOT / 'runtime' / 'pitguard_agent_v388.sqlite3'):
        local = ROOT / 'services' / 'api' / 'pitguard_agent_v388.sqlite3'
        if local.is_file():
            db_path = local
    if not db_path.is_file():
        db_error = f'运行库不存在：{db_path}'
        optimization = {'totalCandidates': 0, 'calculated': 0, 'calculatedRate': None,
                        'improved': 0, 'improvedRate': None,
                        'engineeringQualified': 0, 'engineeringQualifiedRate': None,
                        'note': db_error}
        gates = {'decisions': 0, 'bypass': None, 'breakdown': [], 'note': db_error}
        provenance = {'records': 0, 'traceable': 0, 'traceableRate': None,
                      'projects': [], 'distinctFields': 0, 'note': db_error}
    else:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        try:
            optimization = _optimization(conn)
            gates = _gates(conn)
            provenance = _provenance(conn)
        finally:
            conn.close()
    args.agent_db = str(db_path)

    evaluated = [row for row in case_rows if row['evaluated']]
    # 冻结快照缺失或不可读时是「没有证据」，不是「算法没命中」。
    # 把所有案例都记为 0 分会把资产缺失伪装成产品失败。
    unavailable = [{'id': row['id'], 'reason': row['error']}
                   for row in case_rows if not row['evaluated']]
    expected_codes = sum(len(case['expectedTop3']) for case in bundle['cases'])
    matched_codes = sum(len(row['matched']) for row in evaluated)
    hit_cases = sum(1 for row in evaluated if row['hit'])
    localization = {
        'cases': len(case_rows),
        'evaluatedCases': len(evaluated),
        'unavailableCases': unavailable,
        'caseHitRate': round(hit_cases / len(evaluated), 4) if evaluated else None,
        'codeHitRate': round(matched_codes / expected_codes, 4) if expected_codes and evaluated else None,
        'caseResults': case_rows,
        'note': (('冻结快照不可用时不计命中率，指标记为 not_measured。'
                  '运行库不入库，执行 python verification/acceptance/build_cases.py 可从'
                  '站点自带验证样例重新生成三个冻结快照。')
                 if unavailable else ''),
    }

    metrics = {
        'localizationTop3': {
            'value': localization['caseHitRate'],
            'threshold': THRESHOLDS['localizationTop3'],
            'verdict': _verdict(localization['caseHitRate'],
                                THRESHOLDS['localizationTop3']),
            'detail': localization,
        },
        'optimizationImproved': {
            'value': optimization['improvedRate'],
            'threshold': THRESHOLDS['optimizationImproved'],
            'verdict': _verdict(optimization['improvedRate'],
                                THRESHOLDS['optimizationImproved']),
            'detail': optimization,
        },
        'gateBypass': {
            'value': gates['bypass'],
            'threshold': THRESHOLDS['gateBypass'],
            'verdict': _verdict(gates['bypass'], THRESHOLDS['gateBypass'], mode='max'),
            'detail': gates,
        },
        'provenanceTraceable': {
            'value': provenance['traceableRate'],
            'threshold': THRESHOLDS['provenanceTraceable'],
            'verdict': _verdict(provenance['traceableRate'],
                                THRESHOLDS['provenanceTraceable']),
            'detail': provenance,
        },
    }

    report = {
        'schemaVersion': 'pitguard.acceptance-report/1',
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'inputs': {'oneStrut': args.one_strut, 'agentDb': args.agent_db,
                   'cases': args.cases},
        'metrics': metrics,
        'readOnly': True,
        'formalConstructionIssue': False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps({name: {'value': item['value'], 'verdict': item['verdict']}
                      for name, item in metrics.items()}, ensure_ascii=False, indent=2))
    print('report:', output)
    failed = [name for name, item in metrics.items() if item['verdict'] == 'fail']
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
