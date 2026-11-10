"""A1 资料与参数：确定性的文档参数提取。

A1 读一份文本型 PDF，给出设计基准字段的候选值与页码依据，**不写入**：
采用哪一项由使用者决定。这里没有模型调用，也没有超出模式匹配的工程判断。

设计上刻意只碰四类无歧义标量：施工荷载、基坑安全等级、侧壁重要性系数、
混凝土等级。地下水位（勘察值/计算值）、高程（绝对/相对）、土层 c·φ·γ
（原值/折减值）都存在多种口径，规则抽不准，一律不自动给值。
"""
from __future__ import annotations

import hashlib
import io
import re

from pypdf import PdfReader

ROLE = {'id': 'A1', 'name': '资料与参数', 'capability': 'document_parameters'}

MAX_BYTES = 20 * 1024 * 1024
PDF_MAGIC = b'%PDF'

RULES = (
    {
        'key': 'surchargeGeneral',
        'label': '施工荷载',
        'targetField': 'designSettings.surcharge',
        'unit': 'kPa',
        'patterns': (r'施工荷载[^。；\n]{0,14}?(\d+(?:\.\d+)?)\s*kPa',),
    },
    {
        'key': 'safetyLevel',
        'label': '基坑安全等级',
        'targetField': 'designSettings.excavationSafetyLevel',
        'unit': '',
        'patterns': (
            r'基坑(?:工程)?设计?等级\s*(?:为|：|:)?\s*([一二三])级',
            r'基坑安全等级\s*(?:为|：|:)?\s*([一二三])级',
        ),
    },
    {
        'key': 'importanceFactor',
        'label': '侧壁重要性系数',
        'targetField': 'designSettings.importanceFactor',
        'unit': '',
        'patterns': (r'重要性系数\s*[γy]?\s*0?\s*[=＝]\s*(\d+(?:\.\d+)?)',),
    },
    {
        'key': 'concreteGrade',
        'label': '混凝土等级',
        'targetField': 'designSettings.defaultConcreteGrade',
        'unit': '',
        'patterns': (r'混凝土(?:强度)?等级\s*(?:为|：|:)?\s*(C\d{2,3})',),
    },
)

NOT_ATTEMPTED = (
    '地下水位：勘察实测值与计算采用值可能不同，需人工选定口径',
    '高程：绝对高程与相对标高需先统一基准',
    '土层 c/φ/γ：原值与括号内折减值需人工选择',
    '钻孔坐标与基坑轮廓：多在图纸或矢量图层，文本提取不可靠',
)


def _coerce(key: str, raw: str):
    text = raw.strip()
    if key in ('surchargeGeneral', 'importanceFactor'):
        try:
            return float(text)
        except ValueError:
            return None
    if key == 'safetyLevel':
        return f'{text}级' if text in ('一', '二', '三') else None
    if key == 'concreteGrade':
        return text.upper()
    return text


def _valid(key: str, value) -> bool:
    if value is None:
        return False
    if key == 'surchargeGeneral':
        return isinstance(value, float) and 0 <= value <= 500
    if key == 'importanceFactor':
        return isinstance(value, float) and 0.8 <= value <= 1.5
    if key == 'safetyLevel':
        return value in ('一级', '二级', '三级')
    if key == 'concreteGrade':
        return bool(re.fullmatch(r'C\d{2,3}', str(value)))
    return False


def _read_pages(data: bytes) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ValueError('这份文件不是可读的 PDF，或结构已损坏') from exc
    if reader.is_encrypted:
        try:
            reader.decrypt('')
        except Exception as exc:
            raise ValueError('PDF 已加密，无法读取；请先解除密码保护') from exc
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or '')
        except Exception:
            pages.append('')
    return pages


def _collect(pages: list[str]) -> tuple[list[dict], list[str]]:
    """从逐页文本里抽字段。纯函数，便于回归测试。"""
    fields: list[dict] = []
    warnings: list[str] = []

    for rule in RULES:
        hits: list[dict] = []
        for page_number, page_text in enumerate(pages, start=1):
            for pattern in rule['patterns']:
                for match in re.finditer(pattern, page_text):
                    value = _coerce(rule['key'], match.group(1))
                    if not _valid(rule['key'], value):
                        continue
                    hits.append({
                        'value': value,
                        'page': page_number,
                        'raw': ' '.join(match.group(0).split())[:60],
                    })
        if not hits:
            continue
        first = hits[0]
        distinct = sorted({str(hit['value']) for hit in hits})
        if len(distinct) > 1:
            warnings.append(
                f"{rule['label']}在文中出现多个值：{'、'.join(distinct)}；"
                f"采用第 {first['page']} 页的 {first['value']}，请核对。"
            )
        fields.append({
            'key': rule['key'],
            'label': rule['label'],
            'value': first['value'],
            'unit': rule['unit'],
            'page': first['page'],
            'raw': first['raw'],
            'targetField': rule['targetField'],
            'confidence': 'high' if len(distinct) == 1 else 'medium',
        })

    return fields, warnings


def extract(filename: str, data: bytes) -> dict:
    """A1 的一次文档提取。返回候选，不写任何项目数据。"""
    if not data:
        raise ValueError('文件是空的')
    if len(data) > MAX_BYTES:
        raise ValueError(f'文件超过 {MAX_BYTES // (1024 * 1024)} MB，请拆分后再上传')
    if not data.startswith(PDF_MAGIC):
        raise ValueError('这不是 PDF 文件；如需导入已有提取结果，请选择 .json')

    pages = _read_pages(data)
    text_chars = sum(len(page.strip()) for page in pages)
    if text_chars < 20:
        raise ValueError('这份 PDF 没有可提取的文字层（可能是扫描件或纯图纸）；需要 OCR，当前版本不支持')

    fields, warnings = _collect(pages)

    return {
        'agent': ROLE,
        'document': {
            'name': filename,
            'pages': len(pages),
            'textChars': text_chars,
            'sha256': hashlib.sha256(data).hexdigest(),
        },
        'fields': fields,
        'warnings': warnings,
        'notAttempted': list(NOT_ATTEMPTED),
        'requiresHumanConfirmation': True,
        'formalConstructionIssue': False,
    }
