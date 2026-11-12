"""A7 构件深化：盘点配筋与节点深化状态，必要时提交深化任务。

盘点只读：逐根钢筋几何、构造筛查、节点与预埋件状态都从现有数据算出来，
不写项目。只有明确要求「生成深化」时才提交 P3 节点与钢筋深化任务——该任务
会写工作版（advancedEngineering.p3DetailingClosure），并可能触发配筋方案重新
闭合与必要复算，这一点在回执里必须写明。
"""
from __future__ import annotations

from typing import Any

from .design_basis_intake import _repository

SCHEMA = 'pitguard.detailing-assessment/1'


def _gap(rows: list[dict], key: str, label: str, reason: str, suggestion: str) -> None:
    rows.append({'key': key, 'label': label, 'reason': reason, 'suggestion': suggestion})


def _has_scheme(scheme: dict) -> bool:
    """只有带配筋分区的方案才算已应用；只有 checks/constructability 的空壳不算。"""
    return bool(scheme.get('wallZones'))


def _scheme_stats(scheme: dict) -> dict:
    zones = [z for z in (scheme.get('wallZones') or []) if isinstance(z, dict)]
    faces = [f for z in zones for f in (z.get('faces') or []) if isinstance(f, dict)]
    return {'zones': len(zones), 'faces': len(faces)}


def assess(project_id: str, actor: str) -> dict:
    """A7：配筋方案、逐根钢筋、构造筛查与节点深化的现状盘点。"""
    project = _repository(actor).require(project_id)
    system = project.retaining_system
    scheme = dict(getattr(system, 'rebar_design_scheme', {}) or {}) if system else {}
    advanced = dict(project.advanced_engineering or {})
    closure = dict(advanced.get('p3DetailingClosure') or scheme.get('p3DetailingClosure') or {})
    stats = _scheme_stats(scheme)

    gaps: list[dict[str, Any]] = []
    if system is None:
        _gap(gaps, 'retainingSystem', '围护结构', '项目还没有围护结构',
             '先生成围护墙与支撑体系，再做钢筋深化')
    if system is not None and not _has_scheme(scheme):
        _gap(gaps, 'rebarScheme', '配筋方案', '还没有生成配筋设计方案',
             '先生成并应用配筋方案，再进入逐根钢筋深化')
    if not project.calculation_results:
        _gap(gaps, 'calculation', '计算结果', '当前没有计算结果，深化缺少内力依据',
             '先完成当前施工阶段计算并通过复核')

    geometry = {'bars': [], 'summary': {}}
    screening = {'checks': [], 'summary': {}}
    if system is not None and _has_scheme(scheme):
        try:
            from app.services.rebar_detailing import build_individual_rebar_geometry

            # 只读且有界：单个项目最多展开 4000 根，剩余计入 omitted，
            # 不能用截断后的数量冒充完整钢筋量。
            geometry = build_individual_rebar_geometry(project, max_bars=4000)
        except Exception as exc:
            _gap(gaps, 'barGeometry', '逐根钢筋', f'钢筋几何读取失败：{type(exc).__name__}',
                 '检查配筋方案与围护构件是否一致')
        try:
            from app.services.rebar_constructability import build_rebar_constructability

            screening = build_rebar_constructability(project, scheme)
        except Exception as exc:
            _gap(gaps, 'constructability', '构造筛查', f'构造筛查失败：{type(exc).__name__}',
                 '检查钢筋直径、净距与保护层设置')

    geometry_summary = dict(geometry.get('summary') or {})
    screen_summary = dict(screening.get('summary') or {})
    bar_count = int(geometry_summary.get('individualBarCount') or 0)
    omitted = int(geometry_summary.get('omittedBarCount') or 0)
    fail_count = int(screen_summary.get('failCount') or 0)
    warning_count = int(screen_summary.get('warningCount') or 0)

    if _has_scheme(scheme) and not bar_count:
        _gap(gaps, 'barGeometry', '逐根钢筋', '配筋方案里没有可展开的钢筋',
             '检查墙段与支撑的配筋分区是否已填写')
    if fail_count:
        _gap(gaps, 'constructabilityFail', '构造筛查',
             f'{fail_count} 项构造筛查不通过（钢筋拥挤 / 净距 / 锚固 / 搭接）',
             '调整钢筋层数、直径或截面尺寸后重新筛查')
    if warning_count:
        _gap(gaps, 'constructabilityReview', '构造复核',
             f'{warning_count} 项需要人工复核（机械连接、锚固与搭接要求）',
             '在施工图与钢筋表中明确套筒等级、接头错开与检验批')

    closure_status = str(closure.get('status') or '') or None
    blocking = list(closure.get('blockingGroups') or [])
    warnings = list(closure.get('warningGroups') or [])
    if _has_scheme(scheme) and not closure:
        _gap(gaps, 'deepening', '节点深化', '尚未执行节点与钢筋深化闭环',
             '说明「生成深化」后提交 P3 深化任务')
    elif closure_status and closure_status not in ('pass', 'ok'):
        detail = '；'.join(str(x.get('title') or x.get('reasonCode') or x)[:60]
                          for x in blocking[:3]) or '见深化报告'
        _gap(gaps, 'deepeningStatus', '深化状态',
             f'深化闭环状态为 {closure_status}：{detail}',
             '按深化报告的阻断项逐条关闭后再出版')
    if warnings:
        _gap(gaps, 'deepeningWarnings', '深化警告',
             f'{len(warnings)} 组深化警告待核对',
             '在节点详图与钢筋排布中逐条确认')

    if system is None or not _has_scheme(scheme) or not project.calculation_results:
        status = 'blocked'
    elif gaps:
        status = 'incomplete'
    else:
        status = 'ready'

    closure_summary = dict(closure.get('summary') or {})
    bar_text = f'{bar_count} 根单根钢筋' + (f'（另有 {omitted} 根未展开）' if omitted else '')
    if status == 'ready':
        summary_text = (f"构件深化可用：{bar_text}、"
                        f"{stats['zones']} 个配筋分区；构造筛查 "
                        f"{int(screen_summary.get('passCount') or 0)} 项通过，深化闭环已完成。")
    elif status == 'blocked':
        summary_text = (f"构件深化尚不具备条件：{stats['zones']} 个配筋分区、"
                        f"{bar_text}，共 {len(gaps)} 项待补。")
    else:
        summary_text = (f"构件深化已建立基础：{bar_text}、"
                        f"{stats['zones']} 个配筋分区、{stats['faces']} 个配筋面；"
                        f"还有 {len(gaps)} 项待核对。")

    return {
        'schemaVersion': SCHEMA,
        'mode': 'assessment',
        'label': '构件深化盘点',
        'projectId': project_id,
        'status': status,
        'scheme': {
            'applied': _has_scheme(scheme),
            'mode': scheme.get('mode'),
            'zones': stats['zones'],
            'faces': stats['faces'],
        },
        'bars': {
            'count': bar_count,
            'omitted': omitted,
            'totalWeightKg': round(float(geometry_summary.get('totalWeightKg') or 0.0), 1),
        },
        'checks': {
            'count': int(screen_summary.get('checkCount') or 0),
            'pass': int(screen_summary.get('passCount') or 0),
            'warning': warning_count,
            'fail': fail_count,
            'governing': screen_summary.get('governingStatus'),
        },
        'deepening': {
            'executed': bool(closure),
            'status': closure_status,
            'nodeSubmodels': int(closure_summary.get('nodeSubmodelCount') or 0),
            'blockingGroups': len(blocking),
            'warningGroups': len(warnings),
        },
        'gaps': gaps,
        'summary': summary_text,
        'suggestedNext': [g['suggestion'] for g in gaps[:3]],
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }


def generate(project_id: str, actor: str, *, mode: str = 'balanced') -> dict:
    """A7：提交 P3 节点与钢筋深化任务（写工作版，可能触发复算）。"""
    project = _repository(actor).require(project_id)
    base = {'schemaVersion': SCHEMA, 'mode': 'assessment', 'projectId': project_id,
            'gaps': [], 'engineeringJudgement': False,
            'formalConstructionIssue': False}

    if not bool(getattr(project.design_settings, 'design_basis_confirmed', False)):
        return {**base, 'label': '深化任务未提交', 'status': 'blocked',
                'summary': '设计基准尚未确认，不能执行构件深化；请先在第一页确认设计基准。',
                'suggestedNext': ['先确认设计基准']}
    if project.retaining_system is None:
        return {**base, 'label': '深化任务未提交', 'status': 'blocked',
                'summary': '还没有围护结构，无法执行构件深化。',
                'suggestedNext': ['先生成围护墙与支撑体系']}
    if not project.calculation_results:
        return {**base, 'label': '深化任务未提交', 'status': 'blocked',
                'summary': '当前没有计算结果，深化缺少内力依据。',
                'suggestedNext': ['先完成当前施工阶段计算']}
    from app.tasks.manager import task_manager

    scheme = dict(getattr(project.retaining_system, 'rebar_design_scheme', {}) or {})
    if not _has_scheme(scheme):
        # 没有配筋分区时先补配筋方案，否则深化永远起不来。
        task = task_manager.submit(project_id, 'rebar_design',
                                   {'mode': mode, 'apply': True, 'recalculate': True})
        return {**base, 'label': '配筋方案已提交', 'status': 'submitted',
                'taskId': task.id,
                'summary': (f'还没有配筋方案，已先提交配筋设计任务 {task.id}（{mode}，应用并在必要时复算）。'
                            '注意：复算会替换当前计算结果，若计算不通过则停在未通过状态；'
                            '完成后回来说「生成深化」。'),
                'suggestedNext': ['在任务中心查看配筋进度；完成后让我继续深化']}

    task = task_manager.submit(project_id, 'p3_detailing_closure',
                               {'mode': mode, 'topNodeCount': 8})
    return {**base, 'label': '构件深化已提交', 'status': 'submitted',
            'taskId': task.id,
            'summary': (f'已提交节点与钢筋深化任务 {task.id}（{mode}）。任务会写工作版深化结果，'
                        '并可能触发配筋方案重新闭合与必要复算；完成后需人工复核才能用于出图。'),
            'suggestedNext': ['在任务中心查看进度；完成后回来让我盘点深化状态']}
