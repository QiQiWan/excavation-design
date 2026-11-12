"""A2 地质与场景：只读盘点，不自动改任何地质或水位参数。

文档要求 A2 的交付是「地质模型、荷载和边界工况」，并区分已提取 / 已核对 /
已确认。第一版先回答「现有资料够不够、缺什么、建议补什么」，为后续自动建模
提供明确前置；勘察值与计算采用值口径不同，本模块一律不写。
"""
from __future__ import annotations

from typing import Any

from .design_basis_intake import _repository

SCHEMA = 'pitguard.geology-assessment/1'

_CORE_PARAMETERS = (
    ('unit_weight', '重度 γ'),
    ('cohesion', '黏聚力 c'),
    ('friction_angle', '内摩擦角 φ'),
    ('elastic_modulus', '弹性模量 E'),
)


def _stratum_gaps(strata: list) -> tuple[list[str], list[str], list[str]]:
    incomplete: list[str] = []
    empirical: list[str] = []
    low_confidence: list[str] = []
    for stratum in strata:
        params = getattr(stratum, 'parameters', None)
        missing = [label for name, label in _CORE_PARAMETERS
                   if params is None or getattr(params, name, None) is None]
        if missing:
            incomplete.append(f"{stratum.code}（缺 {'、'.join(missing)}）")
        if str(getattr(stratum, 'parameter_source', '')) in ('empirical', 'manual'):
            empirical.append(stratum.code)
        if str(getattr(stratum, 'confidence', '')) == 'low':
            low_confidence.append(stratum.code)
    return incomplete, empirical, low_confidence


def assess(project_id: str, actor: str) -> dict:
    """返回地质资料盘点与缺项建议；engineeringJudgement 恒为 False。"""
    project = _repository(actor).require(project_id)
    settings = project.design_settings
    boreholes = list(project.boreholes or [])
    strata = list(project.strata or [])
    model = project.geological_model

    located = [b for b in boreholes if b.x is not None and b.y is not None]
    verified = [b for b in boreholes if getattr(b, 'source_verified', False)]
    water_records = [w for b in boreholes for w in (b.water_levels or [])]
    rejected_water = [w for w in water_records if getattr(w, 'quality', '') == 'rejected']
    verified_water = [w for w in water_records if getattr(w, 'quality', '') == 'verified']

    incomplete, empirical, low_confidence = _stratum_gaps(strata)
    model_built = bool(model and (model.surfaces or model.surface_previews or model.volumes))
    coverage = dict((model.coverage_audit if model else {}) or {})
    model_warnings = list((model.warnings if model else []) or [])

    gaps: list[dict[str, Any]] = []

    def gap(key: str, label: str, reason: str, suggestion: str) -> None:
        gaps.append({'key': key, 'label': label, 'reason': reason,
                     'suggestion': suggestion})

    if not boreholes:
        gap('boreholes', '钻孔资料', '项目里没有任何钻孔记录',
            '导入勘察钻孔表；至少 3 个钻孔才能形成稳定地层面')
    elif len(boreholes) < 3:
        gap('boreholes', '钻孔数量', f'当前只有 {len(boreholes)} 个钻孔',
            '补充钻孔，或说明依据不足时采用的经验地层剖面')
    if boreholes and len(located) < len(boreholes):
        gap('boreholeCoordinates', '钻孔坐标',
            f'{len(boreholes) - len(located)} 个钻孔缺平面坐标',
            '补齐坐标，否则无法判断钻孔是否覆盖基坑轮廓')

    if not strata:
        gap('strata', '地层参数', '项目里没有地层记录',
            '按钻孔分层整理地层，并填写 γ / c / φ / E')
    elif incomplete:
        gap('strataParameters', '地层参数完整度',
            '以下地层缺关键参数：' + '；'.join(incomplete[:6]),
            '补勘察试验值；若只能用经验值，请登记参数来源与依据')
    if empirical:
        gap('strataSource', '参数来源',
            '以下地层使用经验值或手工值：' + '、'.join(empirical[:8]),
            '在参数溯源中登记依据；经验值不得当作已确认设计参数')
    if low_confidence:
        gap('strataConfidence', '参数可信度',
            '以下地层置信度为低：' + '、'.join(low_confidence[:8]),
            '补充试验或复核后再用于正式计算')

    if not water_records and settings.groundwater_level is None:
        gap('groundwater', '地下水位', '既没有钻孔水位记录，也没有设计水位',
            '补勘察水位，并区分勘察值与计算采用值')
    elif not water_records:
        gap('groundwaterRecords', '水位记录',
            '只有设计水位，没有钻孔实测水位记录',
            '导入实测水位，便于判断设计水位是否偏于不安全')
    if rejected_water:
        gap('groundwaterQuality', '水位质量',
            f'{len(rejected_water)} 条水位记录被标记为 rejected',
            '剔除或重新核实异常水位记录')

    if not model_built:
        gap('geologicalModel', '地质模型', '尚未生成三维地质模型',
            '在工程输入步骤生成地质模型后再进入方案与计算')
    if model_warnings:
        gap('modelWarnings', '模型警告', '；'.join(str(w)[:80] for w in model_warnings[:3]),
            '按警告逐条核对地层插值与外推范围')
    if coverage and coverage.get('status') not in (None, 'pass', 'ok'):
        gap('coverage', '钻孔覆盖',
            f"覆盖审计状态：{coverage.get('status')}",
            '补充基坑轮廓附近的钻孔，减少外推')

    if not boreholes or not strata:
        status = 'blocked'
    elif gaps:
        status = 'incomplete'
    else:
        status = 'ready'

    if status == 'ready':
        summary = (f"地质资料齐备：{len(boreholes)} 个钻孔、{len(strata)} 个地层、"
                   f"{len(water_records)} 条水位记录，三维地质模型已建立。")
    elif status == 'blocked':
        summary = (f"地质资料不足：{len(boreholes)} 个钻孔、{len(strata)} 个地层，"
                   f"共 {len(gaps)} 项待补；暂不具备建模与计算条件。")
    else:
        summary = (f"地质资料基本可用：{len(boreholes)} 个钻孔、{len(strata)} 个地层、"
                   f"{len(water_records)} 条水位记录；还有 {len(gaps)} 项待补。")

    return {
        'schemaVersion': SCHEMA,
        'mode': 'assessment',
        'projectId': project_id,
        'status': status,
        'boreholes': {
            'count': len(boreholes), 'located': len(located),
            'verified': len(verified),
            'withWater': sum(1 for b in boreholes if b.water_levels),
        },
        'strata': {
            'count': len(strata),
            'complete': len(strata) - len(incomplete),
            'empirical': empirical,
            'lowConfidence': low_confidence,
        },
        'groundwater': {
            'records': len(water_records),
            'verified': len(verified_water),
            'rejected': len(rejected_water),
            'designLevel': settings.groundwater_level,
            'designLevelInside': settings.groundwater_level_inside,
        },
        'model': {
            'built': model_built,
            'surfaces': len(model.surfaces) if model and model.surfaces else 0,
            'previews': len(model.surface_previews) if model and model.surface_previews else 0,
            'coverageStatus': coverage.get('status'),
        },
        'gaps': gaps,
        'summary': summary,
        'suggestedNext': ([g['suggestion'] for g in gaps[:3]]),
        'engineeringJudgement': False,
        'formalConstructionIssue': False,
    }
