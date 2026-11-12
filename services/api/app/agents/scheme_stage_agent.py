"""A3 支护方案 / A4 施工阶段：只读盘点，不生成、不改动。

文档 V3.89 要求 A3/A4 能生成并比较候选方案、保持阶段事件一致。这一版先把
「现在有什么、缺什么、为什么下一步按钮点不了」盘清楚，作为自动生成的前置；
所有输出 engineeringJudgement=False，且不写项目。
"""
from __future__ import annotations

from typing import Any

from .design_basis_intake import _repository

SCHEME_SCHEMA = 'pitguard.scheme-assessment/1'
STAGE_SCHEMA = 'pitguard.stage-assessment/1'


def _gap(rows: list[dict], key: str, label: str, reason: str, suggestion: str) -> None:
    rows.append({'key': key, 'label': label, 'reason': reason, 'suggestion': suggestion})


def assess_scheme(project_id: str, actor: str) -> dict:
    """A3：围护体系、支撑布置与候选比选的现状盘点。"""
    project = _repository(actor).require(project_id)
    system = project.retaining_system
    walls = list(system.diaphragm_walls) if system else []
    supports = list(system.supports) if system else []
    columns = list(system.columns) if system else []
    levels = sorted({int(s.level_index) for s in supports})
    summary = dict((system.layout_summary if system else {}) or {})
    candidates = list(summary.get('candidateSchemes') or [])
    compared = [c for c in candidates
                if isinstance(c, dict) and (c.get('fullCalculation') or c.get('comparison'))]
    warnings = list((system.warnings if system else []) or [])

    gaps: list[dict[str, Any]] = []
    if system is None:
        _gap(gaps, 'retainingSystem', '围护体系', '项目还没有围护结构',
             '先在方案步骤生成围护墙与支撑体系')
    elif not walls:
        _gap(gaps, 'diaphragmWalls', '围护墙', '围护体系里没有墙段',
             '按基坑轮廓生成围护墙分段')
    if system is not None and not supports:
        _gap(gaps, 'supports', '水平支撑', '还没有支撑构件',
             '生成对撑 / 角撑 / 环撑的支撑布置')
    if supports and not columns:
        _gap(gaps, 'columns', '立柱', '有支撑但没有立柱',
             '为长跨支撑设置立柱，避免支撑计算长度过大')
    if not candidates:
        _gap(gaps, 'candidates', '候选方案', '还没有生成 A/B/C 候选',
             '生成多方案候选后再做比选')
    elif len(candidates) < 2:
        _gap(gaps, 'candidates', '候选方案', f'只有 {len(candidates)} 个候选，无法横向比选',
             '至少生成 2 个有实质差异的候选')
    if candidates and not compared:
        _gap(gaps, 'comparison', '完整比选', '候选尚未做完整计算比选',
             '对候选逐一套完整阶段计算，再比较位移、内力与稳定')
    if warnings:
        _gap(gaps, 'warnings', '布置警告', '；'.join(str(w)[:80] for w in warnings[:3]),
             '按警告逐条核对支撑几何、传力路径与施工通道')

    if system is None or not walls:
        status = 'blocked'
    elif gaps:
        status = 'incomplete'
    else:
        status = 'ready'

    if status == 'ready':
        summary_text = (f"支护方案可用：{len(walls)} 段墙、{len(supports)} 根支撑、"
                        f"{len(columns)} 根立柱，{len(candidates)} 个候选已完成完整比选。")
    elif status == 'blocked':
        summary_text = (f"支护方案尚不具备比选条件：{len(walls)} 段墙、{len(supports)} 根支撑，"
                        f"共 {len(gaps)} 项待补。")
    else:
        summary_text = (f"支护方案已建立：{len(walls)} 段墙、{len(supports)} 根支撑、"
                        f"{len(levels)} 道支撑；{len(candidates)} 个候选，还有 {len(gaps)} 项待补。")

    return {
        'schemaVersion': SCHEME_SCHEMA,
        'mode': 'assessment',
        'label': '支护方案盘点',
        'projectId': project_id,
        'status': status,
        'system': {
            'exists': system is not None,
            'walls': len(walls), 'supports': len(supports),
            'columns': len(columns), 'levels': levels,
        },
        'candidates': {
            'count': len(candidates),
            'compared': len(compared),
            'names': [str(c.get('name') or c.get('id') or '')[:40]
                      for c in candidates[:5] if isinstance(c, dict)],
        },
        'gaps': gaps,
        'summary': summary_text,
        'suggestedNext': [g['suggestion'] for g in gaps[:3]],
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }


def _span(stage) -> tuple[float, float]:
    a, b = float(stage.excavation_elevation_lower), float(stage.excavation_elevation_upper)
    return (min(a, b), max(a, b))


def assess_stages(project_id: str, actor: str) -> dict:
    """A4：设计控制阶段与施工计划阶段的一致性与完整性盘点。"""
    project = _repository(actor).require(project_id)
    system = project.retaining_system
    levels = sorted({int(s.level_index) for s in (system.supports if system else [])})
    controls = list(project.design_control_stages or [])
    plans = list(project.construction_plan_stages or [])

    gaps: list[dict[str, Any]] = []
    if not controls:
        _gap(gaps, 'controlStages', '设计控制阶段', '还没有设计控制阶段',
             '按开挖深度与支撑标高生成设计控制阶段')

    ordered = sorted(controls, key=lambda s: _span(s)[0])
    for prev, nxt in zip(ordered, ordered[1:]):
        prev_top = _span(prev)[1]
        next_bottom = _span(nxt)[0]
        if next_bottom > prev_top + 0.05:
            _gap(gaps, 'stageGap', '阶段连续性',
                 f"{prev.name} 与 {nxt.name} 之间缺少过渡阶段（间隔 {next_bottom - prev_top:.2f} m）",
                 '补一个开挖或支撑安装阶段，避免计算工况跳空')
        elif next_bottom < prev_top - 0.05:
            _gap(gaps, 'stageOverlap', '阶段重叠',
                 f"{prev.name} 与 {nxt.name} 深度区间重叠",
                 '核对阶段标高，避免同一深度重复开挖')

    if levels:
        unknown = sorted({int(lv) for s in controls
                          for lv in (s.required_support_levels or [])
                          if int(lv) not in levels})
        if unknown:
            _gap(gaps, 'unknownSupportLevels', '支撑层引用',
                 '控制阶段引用了不存在的支撑层：' + '、'.join(str(x) for x in unknown),
                 '重新生成支撑布置，或把阶段要求改到现有支撑层')

    manual = [s.name for s in controls if str(getattr(s, 'transfer_path_status', '')) == 'manual_review']
    if manual:
        _gap(gaps, 'transferPath', '换撑路径',
             '以下阶段换撑路径需人工复核：' + '、'.join(manual[:5]),
             '在阶段编辑器里明确换撑顺序与临时传力路径')

    if plans:
        control_ids = {s.id for s in controls}
        orphan = [p for p in plans if p.design_control_stage_id not in control_ids]
        if orphan:
            _gap(gaps, 'orphanPlans', '计划阶段引用',
                 f'{len(orphan)} 条施工计划阶段引用了不存在的控制阶段',
                 '重新绑定控制阶段，或删除失效计划')
        unapproved = [p for p in plans if str(getattr(p, 'approval_status', '')) != 'approved']
        if unapproved:
            _gap(gaps, 'planApproval', '计划审批',
                 f'{len(unapproved)} 条施工计划阶段尚未批准',
                 '完成承包商计划审批后再用于正式计算')
    elif controls:
        _gap(gaps, 'planStages', '施工计划阶段',
             '只有设计控制阶段，没有施工计划阶段',
             '导入承包商施工计划，便于对照实际工序')

    if not controls:
        status = 'blocked'
    elif gaps:
        status = 'incomplete'
    else:
        status = 'ready'

    if status == 'ready':
        summary_text = (f"施工阶段可用：{len(controls)} 个设计控制阶段、"
                        f"{len(plans)} 条施工计划阶段，未发现阶段断档或悬空引用。")
    elif status == 'blocked':
        summary_text = f"施工阶段尚未建立：{len(controls)} 个控制阶段，共 {len(gaps)} 项待补。"
    else:
        summary_text = (f"施工阶段已建立：{len(controls)} 个控制阶段、{len(plans)} 条计划阶段，"
                        f"还有 {len(gaps)} 项待核对。")

    return {
        'schemaVersion': STAGE_SCHEMA,
        'mode': 'assessment',
        'label': '施工阶段盘点',
        'projectId': project_id,
        'status': status,
        'stages': {
            'controlCount': len(controls),
            'planCount': len(plans),
            'supportLevels': levels,
            'named': [str(s.name)[:40] for s in controls[:8]],
        },
        'gaps': gaps,
        'summary': summary_text,
        'suggestedNext': [g['suggestion'] for g in gaps[:3]],
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }
