"""设计基准自动录入：一段话 / 一份文件 → 填入 → 反馈还缺什么。

当前用确定性规则抽取，不调用模型。模型接入点已经留好：``interpret_with_model``
读取 ``PITGUARD_LLM_*`` 配置，未配置时明确返回 ``not_configured`` 并回退规则，
绝不假装用了模型。以后有 API 时只替换「谁产出 fields」，白名单校验、写入、
缺项计算、会话回写都不用动。

模型输出不得参与数值或规范判定，也不得越过人工确认与正式施工图签发。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from . import assistant, document_agent
from .model_gateway import configuration
from .store import AgentStore, now

RULES_PROVIDER = 'deterministic-rules/v1'
MODEL_PROVIDER = 'llm/design-basis-intake/v1'


class ModelNotConfigured(RuntimeError):
    """LLM API 尚未提供；调用方必须如实回退，不能伪造模型输出。"""


def provider_status(requested: str = 'auto') -> dict:
    """模型接入状态。现在永远回退规则，但字段与以后接模型时完全一致。"""
    config = configuration()
    configured = bool(config.get('enabled') and config.get('configured'))
    if requested == 'model' and not configured:
        status = 'not_configured'
    else:
        status = 'not_configured' if not configured else 'available_but_not_wired'
    return {
        'requested': requested,
        'configured': configured,
        'modelSettings': {
            'modelSlots': config.get('modelSlots'),
            'allowedHosts': config.get('allowedHosts'),
            'toolExecutionByModel': config.get('toolExecutionByModel'),
        },
        'modelStatus': status,
        'provider': RULES_PROVIDER,
        'providerUsed': RULES_PROVIDER,
        'note': '模型接入点已预留；未配置 PITGUARD_LLM_* 时一律使用确定性规则抽取',
    }


def interpret_with_model(text: str) -> list[dict]:
    """以后接 LLM 的唯一入口：把文本交给模型，返回结构化字段。

    当前没有 API，所以直接抛出；调用方据此回退规则并在响应里写明状态。
    """
    raise ModelNotConfigured('模型 API 尚未接入，已回退确定性规则')


# 允许自动写入的设计基准字段。键是 designSettings 的 camelCase 别名。
FIELDS: dict[str, dict[str, Any]] = {
    'surcharge': {
        'label': '施工荷载', 'unit': 'kPa',
        'validate': lambda v: isinstance(v, (int, float)) and 0 <= float(v) <= 500,
    },
    'excavationSafetyLevel': {
        'label': '基坑安全等级', 'unit': '',
        'validate': lambda v: v in ('一级', '二级', '三级'),
    },
    'importanceFactor': {
        'label': '侧壁重要性系数', 'unit': '',
        'validate': lambda v: isinstance(v, (int, float)) and 0.8 <= float(v) <= 1.5,
    },
    'defaultConcreteGrade': {
        'label': '混凝土等级', 'unit': '',
        'validate': lambda v: bool(re.fullmatch(r'C\d{2,3}', str(v))),
    },
    'defaultRebarGrade': {
        'label': '钢筋等级', 'unit': '',
        'validate': lambda v: bool(re.fullmatch(r'HRB\d{3,4}E?', str(v), re.IGNORECASE)),
    },
    'defaultCoverMm': {
        'label': '保护层厚度', 'unit': 'mm',
        'validate': lambda v: isinstance(v, (int, float)) and 20 <= float(v) <= 120,
    },
    'bearingCapacityKpa': {
        'label': '地基承载力特征值', 'unit': 'kPa',
        'validate': lambda v: isinstance(v, (int, float)) and float(v) > 0,
    },
    'projectGrade': {
        'label': '工程等级', 'unit': '',
        'validate': lambda v: v in ('一级', '二级', '三级'),
    },
    'siteComplexity': {
        'label': '场地复杂程度', 'unit': '',
        'validate': lambda v: v in ('简单', '中等', '复杂'),
    },
    'surroundingEnvironmentLevel': {
        'label': '周边环境等级', 'unit': '',
        'validate': lambda v: v in ('一般', '较高', '高'),
    },
}

_GRADE = lambda raw: raw + '级' if raw in ('一', '二', '三') else raw

TEXT_RULES: tuple[dict[str, Any], ...] = (
    {
        'key': 'surcharge', 'kind': 'float',
        'patterns': (
            r'(?:周边)?施工荷载[^。；\n]{0,16}?(\d+(?:\.\d+)?)\s*kPa',
            r'地面超载[^。；\n]{0,16}?(\d+(?:\.\d+)?)\s*kPa',
        ),
    },
    {
        'key': 'excavationSafetyLevel', 'kind': 'grade',
        'patterns': (r'基坑(?:工程)?(?:设计)?(?:安全)?等级[^。；\n]{0,6}?([一二三])级',),
    },
    {
        'key': 'importanceFactor', 'kind': 'float',
        'patterns': (r'(?:侧壁)?重要性系数\s*[γyΓ]?\s*0?\s*[=＝]\s*(\d+(?:\.\d+)?)',),
    },
    {
        'key': 'defaultConcreteGrade', 'kind': 'upper',
        'patterns': (r'混凝土(?:强度)?等级[^。；\n]{0,8}?(C\d{2,3})',),
    },
    {
        'key': 'defaultRebarGrade', 'kind': 'upper',
        'patterns': (r'(?:钢筋|纵筋)(?:强度)?等级[^。；\n]{0,8}?(hrb\d{3,4}e?)',),
    },
    {
        'key': 'defaultCoverMm', 'kind': 'float',
        'patterns': (r'保护层(?:厚度)?[^。；\n]{0,10}?(\d+(?:\.\d+)?)\s*mm',),
    },
    {
        'key': 'bearingCapacityKpa', 'kind': 'float',
        'patterns': (r'地基承载力(?:特征值)?[^。；\n]{0,14}?(\d+(?:\.\d+)?)\s*kPa',),
    },
    {
        'key': 'projectGrade', 'kind': 'grade',
        'patterns': (r'(?<!基坑)工程等级\s*[：:]?\s*([一二三])级',),
    },
    {
        'key': 'siteComplexity', 'kind': 'plain',
        'patterns': (r'场地复杂程度[^。；\n]{0,6}?(简单|中等|复杂)',),
    },
    {
        'key': 'surroundingEnvironmentLevel', 'kind': 'plain',
        'patterns': (r'周边环境(?:等级)?[^。；\n]{0,6}?(较高|一般|高)',),
    },
)

# 兼容 "surcharge=30" / "施工荷载: 30" 这类半结构化输入。
_ALIASES: dict[str, tuple[str, ...]] = {
    'surcharge': ('surcharge', '施工荷载', '地面超载'),
    'excavationSafetyLevel': ('excavationSafetyLevel', '基坑安全等级'),
    'importanceFactor': ('importanceFactor', '重要性系数'),
    'defaultConcreteGrade': ('defaultConcreteGrade', '混凝土等级'),
    'defaultRebarGrade': ('defaultRebarGrade', '钢筋等级'),
    'defaultCoverMm': ('defaultCoverMm', '保护层厚度'),
    'bearingCapacityKpa': ('bearingCapacityKpa', '地基承载力特征值'),
    'projectGrade': ('projectGrade', '工程等级'),
    'siteComplexity': ('siteComplexity', '场地复杂程度'),
    'surroundingEnvironmentLevel': ('surroundingEnvironmentLevel', '周边环境等级'),
}


def _coerce(kind: str, raw: str) -> Any:
    if kind == 'float':
        return float(raw)
    if kind == 'grade':
        return _GRADE(raw)
    if kind == 'upper':
        return raw.upper()
    return raw


def _field(key: str, value: Any, *, raw: str, page: int | None = None) -> dict:
    meta = FIELDS[key]
    return {
        'key': key, 'label': meta['label'], 'value': value,
        'unit': meta['unit'], 'raw': raw[:80], 'page': page,
        'target': key, 'confidence': 'high',
    }


def interpret_text(text: str) -> dict:
    """从一段自然语言 / 半结构化文本中抽取无歧义标量。"""
    fields: dict[str, dict] = {}
    warnings: list[str] = []
    for rule in TEXT_RULES:
        hits: list[tuple[Any, str]] = []
        for pattern in rule['patterns']:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                raw = match.group(1)
                try:
                    value = _coerce(rule['kind'], raw)
                except (TypeError, ValueError):
                    continue
                if not FIELDS[rule['key']]['validate'](value):
                    continue
                hits.append((value, ' '.join(match.group(0).split())))
        if hits:
            value, raw = hits[0]
            distinct = sorted({str(item[0]) for item in hits})
            if len(distinct) > 1:
                warnings.append(
                    f"{FIELDS[rule['key']]['label']}出现多个值：{'、'.join(distinct)}；采用 {value}，请核对。")
            fields[rule['key']] = _field(rule['key'], value, raw=raw)

    for key, aliases in _ALIASES.items():
        if key in fields:
            continue
        pattern = r'(?:' + '|'.join(re.escape(a) for a in aliases) + r')\s*[:=＝]\s*([^\s,，;；。]+)'
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).rstrip('kPaKPa')
        try:
            value = float(raw) if re.fullmatch(r'\d+(?:\.\d+)?', raw) else _coerce(
                'grade' if key in ('excavationSafetyLevel', 'projectGrade') else 'plain', raw)
        except (TypeError, ValueError):
            continue
        if FIELDS[key]['validate'](value):
            fields[key] = _field(key, value, raw=' '.join(match.group(0).split()))

    return {'fields': list(fields.values()), 'warnings': warnings}


def interpret_file(filename: str, data: bytes) -> dict:
    """复用 A1 的 PDF 抽取，再把字段映射到设计基准白名单。"""
    extracted = document_agent.extract(filename, data)
    key_map = {
        'surchargeGeneral': 'surcharge',
        'safetyLevel': 'excavationSafetyLevel',
        'importanceFactor': 'importanceFactor',
        'concreteGrade': 'defaultConcreteGrade',
    }
    fields = []
    for item in extracted['fields']:
        key = key_map.get(item['key']) or item['targetField'].split('.')[-1]
        if key not in FIELDS or not FIELDS[key]['validate'](item['value']):
            continue
        fields.append(_field(key, item['value'], raw=item.get('raw', ''), page=item.get('page')))
    return {
        'fields': fields,
        'warnings': list(extracted.get('warnings') or []),
        'document': extracted['document'],
    }


def normalize(fields: list[dict]) -> list[dict]:
    """白名单二次校验：未知字段、越界值一律拒绝，不静默丢弃到数据库。"""
    accepted: list[dict] = []
    seen: set[str] = set()
    for item in fields:
        key = str(item.get('key') or item.get('target') or '')
        if key not in FIELDS or key in seen:
            continue
        value = item.get('value')
        if not FIELDS[key]['validate'](value):
            continue
        seen.add(key)
        accepted.append(_field(key, value, raw=str(item.get('raw') or ''),
                               page=item.get('page')))
    return accepted


# 设计基准必填清单：默认值来自软件模板，未经资料确认的算「还缺」。
REQUIRED: tuple[dict[str, Any], ...] = (
    {'key': 'designBasisConfirmed', 'label': '设计基准确认', 'default': False,
     'note': '需工程师确认后才可进入计算'},
    {'key': 'surcharge', 'label': '施工荷载', 'default': 20.0},
    {'key': 'excavationSafetyLevel', 'label': '基坑安全等级', 'default': '二级'},
    {'key': 'importanceFactor', 'label': '侧壁重要性系数', 'default': 1.0},
    {'key': 'defaultConcreteGrade', 'label': '混凝土等级', 'default': 'C35'},
    {'key': 'defaultRebarGrade', 'label': '钢筋等级', 'default': 'HRB400'},
    {'key': 'defaultCoverMm', 'label': '保护层厚度', 'default': 50.0},
    {'key': 'bearingCapacityKpa', 'label': '地基承载力特征值', 'default': None},
    {'key': 'groundwaterLevel', 'label': '地下水位', 'default': -1.5,
     'note': '勘察值与计算采用值口径不同，需人工选定'},
)


def missing_summary(project: Any) -> dict:
    settings = project.design_settings.model_dump(mode='json', by_alias=True)
    advanced = dict(project.advanced_engineering or {})
    applied = set((advanced.get('designBasisIntake') or {}).get('appliedKeys') or [])
    items = []
    for row in REQUIRED:
        key = row['key']
        value = settings.get(key)
        if key in applied:
            continue
        if key == 'designBasisConfirmed':
            present = value is True
        elif value is None or value == '' or value is False:
            present = False
        else:
            present = value != row['default']
        if not present:
            items.append({'key': key, 'label': row['label'],
                          'reason': row.get('note') or '仍为模板默认值，未由资料确认'})
    labels = [item['label'] for item in items[:2]]
    text = '设计基准已齐，可以确认' if not items else (
        '还缺：' + '、'.join(labels) + (f' 等 {len(items)} 项' if len(items) > len(labels) else ''))
    return {
        'count': len(items), 'items': items, 'text': text,
        'confirmed': bool(settings.get('designBasisConfirmed')),
    }


def _repository(actor: str):
    import os
    from app.storage.repository import ProjectRepository, shared_project_store

    return ProjectRepository(
        store=shared_project_store(os.getenv('PITGUARD_DB_PATH') or None),
        default_actor=actor,
    )


def apply(project_id: str, fields: list[dict], *, actor: str,
          conversation_id: str | None = None, source_label: str = '一段话',
          provider: dict | None = None) -> dict:
    """写入设计基准、回写会话、计算剩余缺项。写入通道与模型无关。"""
    from app.routers.projects import _apply_project_patch

    provider = provider or provider_status()
    accepted = normalize(fields)
    store = AgentStore()
    repo = _repository(actor)
    project = repo.require(project_id)
    current = project.design_settings.model_dump(mode='json', by_alias=True)
    advanced = dict(project.advanced_engineering or {})
    marker = dict(advanced.get('designBasisIntake') or {})
    applied_keys = set(marker.get('appliedKeys') or [])

    if not accepted:
        summary = missing_summary(project)
        if conversation_id:
            _write_turn(store, conversation_id, source_label,
                        '没有识别到可填的设计基准字段，项目未改动。' + summary['text'])
        return {
            'mode': 'receipt', 'applied': [], 'remaining': summary,
            **provider, 'formalConstructionIssue': False,
        }

    merged = {**current, **{item['key']: item['value'] for item in accepted}}
    applied_keys.update(item['key'] for item in accepted)
    marker.update({
        'appliedKeys': sorted(applied_keys),
        'updatedAt': now(),
        'lastProvider': provider['providerUsed'],
        'source': source_label,
    })
    advanced['designBasisIntake'] = marker
    updated = _apply_project_patch(
        project_id,
        {'designSettings': merged, 'advancedEngineering': advanced},
        repo, expected_revision=None, actor=actor,
    )
    summary = missing_summary(updated)

    if conversation_id:
        display = (source_label if source_label.endswith(('.pdf', '.json'))
                   or len(source_label) <= 8 else '输入内容')
        lines = [f"已从{display}填入 {len(accepted)} 项设计基准："]
        lines.extend(f"  · {item['label']} = {item['value']}{item['unit']}" for item in accepted)
        lines.append(summary['text'] + '。设计输入变更会作废旧计算结果，请重新验算。')
        _write_turn(store, conversation_id, source_label, '\n'.join(lines))

    return {
        'mode': 'filled',
        'applied': accepted,
        'remaining': summary,
        'requiresRecalculation': bool(updated.advanced_engineering.get('requiresRecalculation')),
        **provider,
        'formalConstructionIssue': False,
    }


def _write_turn(store: AgentStore, conversation_id: str, source_label: str, content: str) -> None:
    try:
        store.append_message(conversation_id, role='user', content=source_label)
        store.append_message(
            conversation_id, role='tool', content=content,
            metadata={
                'answerKind': 'design_basis_intake',
                'engineeringJudgement': False,
                'source': source_label,
            },
        )
    except (KeyError, ValueError):
        # 会话存在但已失效时不影响写入结果本身。
        pass


def ingest(*, project_id: str, actor: str, text: str | None = None,
           filename: str | None = None, data: bytes | None = None,
           conversation_id: str | None = None, provider_requested: str = 'auto',
           raw_fields: list[dict] | None = None,
           source_label: str | None = None) -> dict:
    """统一入口：文本 / PDF / 已有提取结果 JSON，都走同一条写入通道。"""
    provider = provider_status(provider_requested)
    model_tried = False
    warnings: list[str] = []
    if provider_requested == 'model' and text:
        try:
            model_fields = interpret_with_model(text)
            raw_fields = model_fields
            provider['providerUsed'] = MODEL_PROVIDER
            provider['modelStatus'] = 'used'
        except ModelNotConfigured:
            model_tried = True
            warnings.append('模型 API 未配置，已按确定性规则抽取（未伪造模型输出）。')

    if raw_fields is not None:
        fields = raw_fields
        source_label = source_label or '已有提取结果'
    elif data is not None:
        parsed = interpret_file(filename or 'document.pdf', data)
        fields = parsed['fields']
        warnings.extend(parsed['warnings'])
        source_label = f"文件 {filename or 'document.pdf'}（{parsed['document']['pages']} 页）"
    elif text:
        parsed = interpret_text(text)
        fields = parsed['fields']
        warnings.extend(parsed['warnings'])
        source_label = text.strip()[:120]
    else:
        raise ValueError('需要提供一段文本、一份 PDF，或已有的提取结果 JSON')

    if not fields:
        # 没有命中字段时退回普通状态回执，不改动项目、不写入候选。
        if conversation_id:
            _append_status_receipt(conversation_id, text or source_label)
        return {
            'mode': 'receipt', 'applied': [], 'remaining': missing(project_id, actor=actor),
            'warnings': warnings, 'modelTried': model_tried,
            **provider, 'formalConstructionIssue': False,
        }

    result = apply(project_id, fields, actor=actor,
                   conversation_id=conversation_id,
                   source_label=source_label, provider=provider)
    result['warnings'] = warnings
    result['modelTried'] = model_tried
    return result


def _append_status_receipt(conversation_id: str, text: str) -> None:
    """没有识别到字段时，按普通提问处理：留下用户回合 + 状态回执。"""
    store = AgentStore()
    try:
        conversation = store.get_conversation(conversation_id)
        store.append_message(conversation_id, role='user', content=text)
        assistant.respond(conversation, text)
    except (KeyError, ValueError):
        pass


def missing(project_id: str, *, actor: str) -> dict:
    project = _repository(actor).require(project_id)
    return {**missing_summary(project), **provider_status(),
            'projectId': project_id, 'formalConstructionIssue': False}


def parse_extraction_json(payload: str) -> list[dict]:
    """接受之前生成的提取结果 JSON（标量数组），键名统一映射到白名单。"""
    data = json.loads(payload)
    rows = data.get('scalarParameters') or data.get('fields') or []
    if not isinstance(rows, list):
        raise ValueError('提取结果里没有字段数组')
    legacy = {
        'surchargeGeneral': 'surcharge',
        'safetyLevel': 'excavationSafetyLevel',
        'concreteGrade': 'defaultConcreteGrade',
    }
    mapped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('key') or '')
        key = legacy.get(key, key)
        if key not in FIELDS:
            target = str(row.get('targetField') or '').split('.')[-1]
            key = legacy.get(target, target)
        if key in FIELDS:
            mapped.append({'key': key, 'value': row.get('value'),
                           'raw': row.get('raw') or '', 'page': row.get('page')})
    return mapped
