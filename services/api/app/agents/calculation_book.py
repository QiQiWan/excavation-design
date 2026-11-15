"""计算书一体化入口：一份文件 → 设计基准 → 六步链 → 成果检验。

它不发明能力，只把已有能力串成一条可追溯的路径：
- A1 的 `design_basis_intake.ingest` 负责解读（模型优先、规则兜底，含页码溯源）；
- 六步链 `chain.start` 负责产出（遇闸口停，不自动签发）；
- 本模块负责把「读了什么、填了什么、还缺什么、链停在哪」一次讲清楚。

边界：计算书只提供设计基准，不含钻孔与几何；缺这些时链会停在 input，
如实返回缺项，不猜、不补、不伪造。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import chain as chain_flow
from . import design_basis_intake as intake
from .design_basis_intake import _repository

SCHEMA = 'pitguard.calculation-book/1'


def run(*, actor: str, filename: str, data: bytes,
        project_id: str | None = None, provider: str = 'auto',
        start_chain: bool = True) -> dict:
    """解读一份计算书并（可选）立刻推进六步链到第一个停点。"""
    repo = _repository(actor)
    created = False
    if project_id:
        project = repo.require(project_id)
    else:
        from app.schemas.domain import Project

        stem = Path(filename).stem.strip() or '计算书导入'
        project = repo.create(Project(name=f'{stem[:60]}（计算书导入）'))
        created = True

    ingested = intake.ingest(
        project_id=project.id, actor=actor, filename=filename, data=data,
        provider_requested=provider, source_label=f'计算书 {filename}',
    )
    missing = ingested.get('remaining') or {}
    result: dict[str, Any] = {
        'schemaVersion': SCHEMA,
        'projectId': project.id,
        'createdProject': created,
        'document': {'filename': filename, 'bytes': len(data)},
        'interpretation': {
            'mode': ingested.get('mode'),
            'providerUsed': ingested.get('providerUsed'),
            'modelStatus': ingested.get('modelStatus'),
            'applied': [{'key': row['key'], 'value': row['value'],
                         'raw': row.get('raw')} for row in ingested.get('applied', [])],
            'warnings': list(ingested.get('warnings') or []),
            'conflicts': list(ingested.get('conflicts') or []),
            'missingCount': missing.get('count'),
            'missing': list(missing.get('items') or [])[:12],
            'missingText': missing.get('text'),
        },
        'formalConstructionIssue': False,
    }
    if not start_chain:
        return result

    flow = chain_flow.start(project.id, actor)
    result['pipeline'] = {
        'flowId': flow.get('flowId'),
        'status': flow.get('status'),
        'progress': flow.get('progress'),
        'summary': flow.get('summary'),
        'steps': [{'stageKey': row.get('stageKey'), 'state': row.get('state'),
                   'note': str(row.get('note') or '')[:160]}
                  for row in flow.get('steps', [])],
        'next': flow.get('next'),
        'stoppedBecause': (flow.get('next') or {}).get('gate', {}).get('reason'),
    }
    return result


def status(*, actor: str, project_id: str) -> dict:
    """看一条计算书流水线走到哪、卡在什么上、成果检验结论是什么。"""
    repo = _repository(actor)
    project = repo.require(project_id)
    latest = project.calculation_results[-1] if project.calculation_results else None
    flows = chain_flow.history(project_id, limit=1)
    flow = flows[0] if flows else None
    basis = intake.missing_summary(project)
    return {
        'schemaVersion': SCHEMA,
        'projectId': project_id,
        'designBasis': {
            'confirmed': basis['confirmed'],
            'missingCount': basis['count'],
            'missing': basis['items'][:12],
            'text': basis['text'],
        },
        'flow': None if not flow else {
            'flowId': flow.get('id'), 'status': flow.get('status'),
            'currentStage': flow.get('currentStage'),
            'steps': [{'stageKey': row.get('stageKey'), 'state': row.get('state')}
                      for row in (flow.get('steps') or [])],
        },
        'calculation': None if latest is None else {
            'resultId': latest.id,
            'checkSummary': latest.check_summary,
        },
        'inputCounts': {
            'boreholes': len(project.boreholes or []),
            'strata': len(project.strata or []),
            'supports': len((project.retaining_system.supports if project.retaining_system else []) or []),
        },
        'formalConstructionIssue': False,
    }
