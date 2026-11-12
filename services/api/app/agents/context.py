"""显式 EngineeringContext：八个 Agent 读同一份有界项目上下文。

只做读取与汇总：不跑几何、不做模型调用、不写项目。所有字段都是标量或计数，
并给出 ``contextHash``——项目状态一变，哈希就变，结论可以据此判断是否过期。
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from .design_basis_intake import _repository
from .store import AgentStore, encode

SCHEMA = 'pitguard.engineering-context/1'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scheme_applied(system) -> bool:
    scheme = dict(getattr(system, 'rebar_design_scheme', {}) or {}) if system else {}
    return bool(scheme.get('wallZones'))


def build(project_id: str, actor: str = 'system') -> dict:
    """构建只读上下文；同一项目状态得到同一 contextHash。"""
    project = _repository(actor).require(project_id)
    settings = project.design_settings
    excavation = project.excavation
    system = project.retaining_system
    model = project.geological_model
    latest = project.calculation_results[-1] if project.calculation_results else None
    supports = list(system.supports) if system else []
    levels = sorted({int(row.level_index) for row in supports})
    controls = list(project.design_control_stages or [])
    plans = list(project.construction_plan_stages or [])
    coverage = dict((model.coverage_audit if model else {}) or {})

    store = AgentStore()
    findings = store.list_findings(project_id)
    actions = store.list_actions(project_id)
    blockers = sum(1 for row in findings if row['severity'] == 'blocker')
    proposed = sum(1 for row in actions if row['status'] == 'proposed')

    updated_at = str(getattr(project, 'updated_at', '') or '')
    revision = int(getattr(project, 'revision', 0) or 0)
    review_status = str(getattr(getattr(project, 'review_workflow', None), 'status', '') or '')

    fingerprint = {
        'project': project_id,
        'updatedAt': updated_at,
        'revision': revision,
        'basis': [
            bool(settings.design_basis_confirmed), settings.surcharge,
            settings.excavation_safety_level, settings.importance_factor,
            settings.default_concrete_grade, settings.default_rebar_grade,
            settings.default_cover_mm, settings.groundwater_level,
            settings.groundwater_level_inside,
        ],
        'excavation': [
            bool(excavation), round(float(getattr(excavation, 'depth', 0) or 0), 3),
            len(excavation.outline.points) if excavation else 0,
        ],
        'retaining': [
            bool(system), len(system.diaphragm_walls) if system else 0,
            len(supports), levels, _scheme_applied(system),
        ],
        'stages': [len(controls), len(plans)],
        'calculation': [
            len(project.calculation_results),
            getattr(latest, 'id', None),
            getattr(latest, 'result_hash', None),
            getattr(latest, 'calculation_contract_id', None),
            bool((project.advanced_engineering or {}).get('requiresRecalculation')),
        ],
    }
    context_hash = 'ctx-' + sha256(encode(fingerprint).encode()).hexdigest()[:20]

    return {
        'schemaVersion': SCHEMA,
        'projectId': project_id,
        'contextHash': context_hash,
        'generatedAt': _now(),
        'identity': {
            'name': project.name,
            'location': project.location,
            'updatedAt': updated_at,
            'revision': revision,
        },
        'designBasis': {
            'confirmed': bool(settings.design_basis_confirmed),
            'surcharge': settings.surcharge,
            'safetyLevel': settings.excavation_safety_level,
            'importanceFactor': settings.importance_factor,
            'concreteGrade': settings.default_concrete_grade,
            'rebarGrade': settings.default_rebar_grade,
            'coverMm': settings.default_cover_mm,
            'groundwaterLevel': settings.groundwater_level,
            'groundwaterLevelInside': settings.groundwater_level_inside,
        },
        'excavation': {
            'exists': excavation is not None,
            'depthM': round(float(getattr(excavation, 'depth', 0) or 0), 3) if excavation else 0,
            'outlinePoints': len(excavation.outline.points) if excavation else 0,
            'segments': len(excavation.segments) if excavation else 0,
        },
        'geology': {
            'boreholes': len(project.boreholes or []),
            'strata': len(project.strata or []),
            'waterRecords': sum(len(b.water_levels or []) for b in (project.boreholes or [])),
            'modelBuilt': bool(model and (model.surfaces or model.surface_previews or model.volumes)),
            'previews': len(model.surface_previews or []) if model else 0,
            'coverageStatus': coverage.get('status'),
        },
        'retaining': {
            'exists': system is not None,
            'walls': len(system.diaphragm_walls) if system else 0,
            'supports': len(supports),
            'columns': len(system.columns) if system else 0,
            'supportLevels': levels,
            'rebarSchemeApplied': _scheme_applied(system),
        },
        'stages': {
            'designControl': len(controls),
            'constructionPlan': len(plans),
        },
        'calculation': {
            'count': len(project.calculation_results),
            'latestId': getattr(latest, 'id', None),
            'resultHash': getattr(latest, 'result_hash', None),
            'contractId': getattr(latest, 'calculation_contract_id', None),
            'requiresRecalculation': bool((project.advanced_engineering or {}).get('requiresRecalculation')),
        },
        'review': {'status': review_status or None},
        'agent': {
            'findings': len(findings), 'blockers': blockers,
            'actions': len(actions), 'proposed': proposed,
        },
        'readOnly': True,
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }
