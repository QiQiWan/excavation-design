"""Minimum review package generated exclusively from one frozen section/result pair.

No Project/repository/legacy exporter is imported. Export does not solve, optimize,
adopt reinforcement, or change the sealed numerical record.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from threading import RLock
from typing import Any, Iterable
import zipfile

from app.section.models import FrozenSectionSnapshot, content_hash


EXPORT_SCHEMA = 'pitguard.section-minimum-package/1'
EXPORT_VERSION = '2.0'
# Matplotlib and ReportLab keep process-global font/render registries.
# Separate request threads may generate files concurrently, but not use those
# registries concurrently. Process-level output uniqueness is handled below.
_RENDER_LOCK = RLock()
REVIEW_TEXT = '未完成工程验证，不作为正式施工图或施工配筋依据。'
STATUS = {
    'complete': '计算完成', 'success': '计算完成', 'failed': '计算失败', 'not_run': '未运行', 'pass': '数值检查通过', 'fail': '数值检查未通过',
    'passed': '数值检查通过', 'review_required': '待工程复核', 'not_compared': '未完成对照',
    'not_calculated': '未计算', 'not_available': '无可用结果', 'unavailable': '无可用结果',
    'missing_input': '缺少输入', 'missing_provided_reinforcement': '缺少给定纵筋',
    'strength_pass': '本项抗弯检查通过', 'strength_fail': '本项抗弯检查未通过',
    'incomplete': '条件不完整', 'not_comparable': '条件不同，不能比较',
    'not_attached': '未绑定对照资料', 'conditions_mismatch': '条件不一致',
    'outside_method_scope': '超出本方法范围', 'warning': '需复核',
    'calculated': '仅应力计算', 'engineering_fail': '存在不满足项',
    'within_tolerance': '在声明容差内', 'outside_tolerance': '超过声明容差',
    'identity_mismatch': '量纲或语义不一致', 'projection_mismatch': '压力投影不同',
    'sign_mismatch': '正负号不一致',
}
COMPARISON_NAMES = {'hand': '手算与解析子问题', 'independent': '独立求解器', 'mesh': '网格对照', 'original': '原项目同条件比较'}


def _state(value: Any) -> str:
    return STATUS.get(str(value), str(value or '未提供'))


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding='utf-8')


def _csv(path: Path, columns: list[str], rows: Iterable[dict]) -> None:
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='raise')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: '' if row.get(key) is None else row.get(key) for key in columns})


def _identity(data: dict, result: dict) -> dict:
    return {'snapshot_hash': result['snapshotHash'], 'analysis_hash': result['analysisHash'],
            'result_hash': result['resultHash'], 'section_id': data['section_id'],
            'source': data['purpose'], 'source_project_id': data.get('source_project_id'),
            'source_revision': data.get('source_revision')}


IDENTITY_COLUMNS = ['snapshot_hash', 'analysis_hash', 'result_hash', 'section_id', 'source', 'source_project_id', 'source_revision']
ENVELOPE_COLUMNS = IDENTITY_COLUMNS + ['basis', 'depth_m', 'elevation_m', 'side',
    'moment_min_kn_m', 'moment_max_kn_m', 'moment_min_stage_id', 'moment_max_stage_id',
    'shear_min_kn', 'shear_max_kn', 'shear_min_stage_id', 'shear_max_stage_id',
    'displacement_min_mm', 'displacement_max_mm', 'displacement_min_stage_id', 'displacement_max_stage_id',
    'force_basis', 'status', 'note']
REBAR_COLUMNS = IDENTITY_COLUMNS + ['zone_id', 'from_depth_m', 'to_depth_m', 'governing_stage_id',
    'shear_governing_stage_id', 'moment_design_kn_m', 'shear_design_kn', 'strength_required_area_mm2',
    'minimum_area_mm2', 'required_area_mm2', 'provided_area_mm2', 'provided_bars_label',
    'provided_bar_count', 'provided_bar_diameter_mm', 'bar_axis_cover_mm', 'capacity_kn_m',
    'utilization', 'stirrup_label', 'status', 'unverified_checks', 'notes', 'demand_scope', 'engineering_check_status']


def _write_tables(data: dict, result: dict, output: Path, curves_generated: bool) -> None:
    identity = _identity(data, result)
    top = float(data['datum']['wall_top_elevation_m'])
    rows = []
    if curves_generated:
        for point in result['envelope']['points']:
            for basis in ('characteristic', 'design'):
                design = basis == 'design'
                prefix_m, prefix_v = ('md', 'vd') if design else ('m', 'v')
                rows.append({**identity, 'basis': basis, 'depth_m': point['depthM'],
                    'elevation_m': top-float(point['depthM']), 'side': point.get('side'),
                    'moment_min_kn_m': point.get(prefix_m+'MinKnM'), 'moment_max_kn_m': point.get(prefix_m+'MaxKnM'),
                    'moment_min_stage_id': point.get('mMinStageId'), 'moment_max_stage_id': point.get('mMaxStageId'),
                    'shear_min_kn': point.get(prefix_v+'MinKn'), 'shear_max_kn': point.get(prefix_v+'MaxKn'),
                    'shear_min_stage_id': point.get('vMinStageId'), 'shear_max_stage_id': point.get('vMaxStageId'),
                    'displacement_min_mm': None if design else point.get('uMinMm'),
                    'displacement_max_mm': None if design else point.get('uMaxMm'),
                    'displacement_min_stage_id': None if design else point.get('uMinStageId'),
                    'displacement_max_stage_id': None if design else point.get('uMaxStageId'),
                    'force_basis': 'single_pile', 'status': result['status'],
                    'note': '设计内力按冻结效应系数一次放大；不定义设计位移' if design else '完整阶段正负包络；支点上侧与下侧分列'})
    _csv(output/'envelopes.csv', ENVELOPE_COLUMNS, rows)
    rebar = data['reinforcement']
    reinforcement = result.get('reinforcement') or {}
    rebar_rows = []
    # Failed/partial results can retain diagnostic rows in JSON, but never expose
    # them as a current reinforcement demand schedule.
    if curves_generated:
        for row in reinforcement.get('rows') or []:
            rebar_rows.append({**identity, 'zone_id': row.get('id'), 'from_depth_m': row.get('fromDepthM'),
                'to_depth_m': row.get('toDepthM'), 'governing_stage_id': row.get('governingStageId'),
                'shear_governing_stage_id': row.get('shearGoverningStageId'),
                'moment_design_kn_m': row.get('momentDesignKnM'), 'shear_design_kn': row.get('shearDesignKn'),
                'strength_required_area_mm2': row.get('strengthRequiredAreaMm2'), 'minimum_area_mm2': row.get('minimumAreaMm2'),
                'required_area_mm2': row.get('requiredAreaMm2'),
                'provided_area_mm2': row.get('providedAreaMm2') if rebar.get('bar_count') is not None else None,
                'provided_bars_label': row.get('providedBarsLabel') if rebar.get('bar_count') is not None else None,
                'provided_bar_count': rebar.get('bar_count'), 'provided_bar_diameter_mm': rebar.get('bar_diameter_mm'),
                'bar_axis_cover_mm': rebar['bar_axis_cover_mm'], 'capacity_kn_m': row.get('capacityKnM'),
                'utilization': row.get('utilization'), 'stirrup_label': row.get('stirrupLabel'), 'status': row.get('status'),
                'unverified_checks': '; '.join(reinforcement.get('unverifiedChecks') or []),
                'demand_scope': 'N=0 pure bending reference; engineering checks are separate',
                'engineering_check_status': (result.get('engineeringChecks') or {}).get('status', 'not_calculated'),
                'notes': '; '.join(str(note) for note in row.get('notes') or [])})
    _csv(output/'reinforcement.csv', REBAR_COLUMNS, rebar_rows)
    stage_columns = IDENTITY_COLUMNS + ['stage_id', 'stage_name', 'basis', 'depth_m', 'elevation_m', 'side',
        'displacement_mm', 'rotation_rad', 'moment_kn_m', 'shear_kn', 'status']
    stage_rows = []
    if curves_generated:
        for stage in result.get('stages') or []:
            if stage.get('status') not in {'complete', 'success'}:
                continue
            for point in stage.get('points') or []:
                for basis in ('characteristic', 'design'):
                    design = basis == 'design'
                    stage_rows.append({**identity, 'stage_id': stage['id'], 'stage_name': stage.get('name'), 'basis': basis,
                        'depth_m': point['depthM'], 'elevation_m': top-float(point['depthM']), 'side': point.get('side'),
                        'displacement_mm': None if design else point.get('displacementMm'),
                        'rotation_rad': None if design else point.get('rotationRad'),
                        'moment_kn_m': point.get('momentDesignKnM' if design else 'momentCharacteristicKnM'),
                        'shear_kn': point.get('shearDesignKn' if design else 'shearCharacteristicKn'), 'status': stage['status']})
    _csv(output/'stage_results.csv', stage_columns, stage_rows)
    comparison_rows = []
    for kind, comparison in (result.get('comparisons') or {}).items():
        if isinstance(comparison, dict):
            for key, value in _leaves(comparison):
                comparison_rows.append({**identity, 'comparison': kind, 'status': comparison.get('status'),
                                        'field': key, 'value': value})
    _csv(output/'comparison.csv', IDENTITY_COLUMNS+['comparison', 'status', 'field', 'value'], comparison_rows)
    _csv(output/'input_sources.csv', IDENTITY_COLUMNS+['parameter', 'kind', 'document', 'document_sha256', 'page', 'note'],
         [{**identity, 'parameter': r['parameter'], 'kind': r['kind'], 'document': r['source'],
           'document_sha256': r.get('source_sha256'), 'page': r.get('page'), 'note': r.get('note')}
          for r in data.get('evidence') or []])


def _leaves(value: Any, prefix: str = ''):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _leaves(child, f'{prefix}[{index}]')
    else:
        yield prefix, value


def _omml_run(value: str):
    from docx.oxml import OxmlElement
    run = OxmlElement('m:r')
    text = OxmlElement('m:t')
    text.text = str(value)
    run.append(text)
    return run


def _math_part(kind: str, base: str, value: str):
    from docx.oxml import OxmlElement
    element = OxmlElement('m:'+kind)
    for tag, content in [('e', base), ('sup' if kind == 'sSup' else 'sub', value)]:
        child = OxmlElement('m:'+tag)
        child.append(_omml_run(content))
        element.append(child)
    return element


def _fraction(numerator: list, denominator: list):
    from docx.oxml import OxmlElement
    element = OxmlElement('m:f')
    for tag, contents in [('num', numerator), ('den', denominator)]:
        child = OxmlElement('m:'+tag)
        for value in contents:
            child.append(_omml_run(value) if isinstance(value, str) else value)
        element.append(child)
    return element


def _equation(document, contents: list) -> None:
    from docx.oxml import OxmlElement
    from docx.shared import Pt
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(3)
    paragraph.paragraph_format.space_after = Pt(5)
    paragraph.paragraph_format.keep_together = True
    math_paragraph, math = OxmlElement('m:oMathPara'), OxmlElement('m:oMath')
    for value in contents:
        math.append(_omml_run(value) if isinstance(value, str) else value)
    math_paragraph.append(math)
    paragraph._p.append(math_paragraph)


def _table(document, headers: list[str], rows: Iterable[Iterable], widths: list[float]):
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    from app.section.render_helper import number
    rows = list(rows)
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    props = table._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        border = OxmlElement('w:'+side)
        for key, val in [('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')]:
            border.set(qn('w:'+key), val)
        borders.append(border)
    props.append(borders)
    for index, width in enumerate(widths):
        table.columns[index].width = Inches(width)
    repeat = OxmlElement('w:tblHeader')
    table.rows[0]._tr.get_or_add_trPr().append(repeat)

    def fill(row, values, header=False):
        keep = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(keep)
        for index, value in enumerate(values):
            cell = row.cells[index]
            cell.width = Inches(widths[index])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc = cell._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for side, amount in [('top', 65), ('bottom', 65), ('start', 85), ('end', 85)]:
                margin = OxmlElement('w:'+side)
                margin.set(qn('w:w'), str(amount)); margin.set(qn('w:type'), 'dxa')
                margins.append(margin)
            tc.append(margins)
            if header:
                shade = OxmlElement('w:shd'); shade.set(qn('w:fill'), 'E7EDF2'); tc.append(shade)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.line_spacing = 1.12
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if header or isinstance(value, (int, float)) else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(number(value) if not isinstance(value, str) else value)
            run.bold = header; run.font.size = Pt(9.2); run.font.color.rgb = RGBColor(0, 0, 0)
    fill(table.rows[0], headers, True)
    for values in rows:
        fill(table.add_row(), list(values))
    # Keep short engineering summaries together instead of leaving the sole
    # governing stage or last soil layer on the next page. Long schedules may
    # span pages and retain the repeating header.
    if len(rows) <= 8:
        for row in table.rows[:-1]:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.keep_with_next = True
    document.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def _comparison_details(doc, comparisons: dict) -> None:
    """Readable recorded evidence, with units; never calculate a new comparison."""
    from app.section.render_helper import number

    def precision(value):
        return f'{value:.5g}' if isinstance(value, (int, float)) and not isinstance(value, bool) else number(value)

    hand = comparisons.get('hand') or {}
    if hand.get('sameSnapshot') or any((comparisons.get(key) or {}).get(field) for key, field in
           [('hand', 'checks'), ('independent', 'stages'), ('mesh', 'stages')]):
        doc.add_paragraph('数值验收逐值采用 |a−b| ≤ 绝对容差 + 相对容差 × max(|a|, |b|)。归一化误差为实际差值除以该允许差；不大于 1 为通过。每项阈值及完整点值对照保留在 comparison.csv。')
    same = hand.get('sameSnapshot') or {}
    if same:
        doc.add_heading('同一冻结剖面手工核对', level=2)
        doc.add_paragraph(f"本节核对本计算书的实际冻结剖面，状态为{_state(same.get('status'))}。"
                          f"控制阶段 {same.get('stageId', '未提供')}；输入快照 {str(same.get('snapshotHash') or '')[:16]}，"
                          f"离散输入 {str(same.get('discreteInputHash') or '')[:16]}。完整哈希与核对明细见 section_result.json。")
        if same.get('checks'):
            _table(doc, ['本剖面检查项与公式', '手工值', '程序值', '单位', '绝对差', '绝对容差', '状态'], [
                [str(row.get('name') or row.get('id'))+'\n'+str(row.get('formula') or ''),
                 precision(row.get('expected')), precision(row.get('actual')), row.get('unit'),
                 precision(row.get('maxAbsoluteDifference')), precision(row.get('absoluteTolerance')),
                 '通过' if row.get('passed') else '未通过'] for row in same['checks']],
                [2.35, .9, .9, .7, .85, .8, .6])
            relative = sorted({row['relativeTolerance'] for row in same['checks'] if 'relativeTolerance' in row})
            if relative:
                doc.add_paragraph('本剖面上述检查的相对容差：'+'、'.join(precision(value) for value in relative)+'。')
        elif same.get('reason'):
            doc.add_paragraph(str(same['reason']))
        worked = same.get('soilPressureWorkedRows') or []
        if worked:
            doc.add_paragraph('分段有效应力：深度自桩顶向下，段长单位 m，γ′单位 kN/m³，q 与压力单位 kPa。下表按土层界面和水位分段，逐项列出已封存的代入值。')
            _equation(doc, ['σ′ = Σ ', _math_part('sSub', 'γ′', 'i'), ' Δ', _math_part('sSub', 'z', 'i'), ' + q'])
            pressure_rows = []
            for row in worked:
                expected = row['expected']
                segments = [f"{part['layerId']}: {precision(part['effectiveUnitWeightKnM3'])}×"
                            f"({number(part['toM'], 8)}−{number(part['fromM'], 8)}) = {precision(part['contributionKpa'])}"
                            for part in row['verticalStressSegments']]
                segments.append(f"Σγ′Δz + {precision(row['surchargeKpa'])} = {precision(expected['sigmaKpa'])}")
                side = '坑外' if row['side'] == 'outside' else '坑内'
                pressure_rows.append([
                    f"{side} z={number(row['depthM'], 8)} m\nc={precision(row['cohesionKpa'])} kPa\nφ={precision(row['phiDeg'])}°\n"
                    +('本行通过' if row.get('passed') else '本行未通过'),
                    '\n'.join(segments),
                    f"K0={precision(row['k0'])}\nKa={precision(row['ka'])}\nKp={precision(row['kp'])}",
                    f"p0={precision(expected['p0Kpa'])}\npa={precision(expected['activeKpa'])}\npp={precision(expected['passiveKpa'])}",
                ])
            _table(doc, ['土侧、积分深度与参数', '分段代入与 σ′ / kPa', '压力系数', '基准/界限压力\n手工值 / kPa'],
                   pressure_rows, [1.25, 2.8, 1.15, 1.9])
            doc.add_paragraph('p0 为 K0 基准压力，pa、pp 为主动与被动界限；它们不等同于已经收敛的位移土反力。表内手工值与程序值已按记录阈值核对，各项差值保留在 comparison.csv。')
        equilibrium = same.get('stageEquilibrium') or []
        if equilibrium:
            doc.add_paragraph(f"全阶段平衡：{len(equilibrium)} 个阶段，最大 |ΣF| = "
                              f"{precision(max(abs(row['forceResidualKn']) for row in equilibrium))} kN，最大 |ΣM桩顶| = "
                              f"{precision(max(abs(row['momentResidualKnM']) for row in equilibrium))} kN·m；"
                              f"最大相对力、矩残差分别为 {precision(max(row['relativeForceResidual'] for row in equilibrium))}、"
                              f"{precision(max(row['relativeMomentResidual'] for row in equilibrium))}，限值 "
                              f"{precision(same.get('equilibriumToleranceRelative'))}。"
                              +('全部阶段记录通过' if all(row.get('passed') for row in equilibrium) else '存在未通过阶段')
                              +'，逐阶段证据见 comparison.csv。')
        doc.add_paragraph('同剖面核对范围：'+str(same.get('scope') or '以封存比较记录为准。')+'。本节不构成原项目模型复现证明。')
    if hand.get('checks'):
        doc.add_heading('解析子问题', level=2)
        inputs = hand.get('inputs') or {}
        doc.add_paragraph(f"验证参数：L = {number(inputs.get('lengthM'))} m，EI = {number(inputs.get('eiKnM2'))} kN·m²，"
                          f"F = {number(inputs.get('headForceKn'))} kN，支撑刚度 K = {number(inputs.get('supportStiffnessKnPerM'))} kN/m。")
        doc.add_paragraph('解析子问题相对容差：'+precision(hand.get('toleranceRelative'))+'；逐项绝对容差见下表。')
        _table(doc, ['检查项与公式', '解析值', '计算值', '单位', '最大绝对差', '绝对容差', '状态'], [
            [str(row.get('name', row.get('id', '')))+'\n'+str(row.get('formula') or ''),
             precision(row.get('expected')), precision(row.get('actual')), row.get('unit'),
             precision(row.get('maxAbsoluteDifference')), precision(row.get('absoluteTolerance')), '通过' if row.get('passed') else '未通过']
            for row in hand['checks']], [2.3, .85, .85, .6, .95, .95, .6])
        doc.add_paragraph('解析比较范围：'+str(hand.get('scope') or '以封存比较记录为准。'))

    independent = comparisons.get('independent') or {}
    if independent.get('stages'):
        doc.add_heading('独立求解器同条件对照', level=2)
        doc.add_paragraph(f"求解器 {independent.get('solver', '未提供')}，版本 {independent.get('solverVersion', '未提供')}。"
                          '相同离散输入和构件本构，逐阶段比较节点位移、转角、梁端力、土弹簧状态及活动支点。')
        doc.add_paragraph('下表汇总全部阶段的最大绝对差与最大归一化误差；控制阶段按归一化误差确定。')
        names = {'nodalDisplacementM': ('节点位移', 'm'), 'nodalRotation': ('节点转角', 'rad'),
                 'elementShearKn': ('梁端剪力', 'kN'), 'elementMomentKnM': ('梁端弯矩', 'kN·m'),
                 'pressureKn': ('土弹簧节点力', 'kN'), 'plasticDisplacementM': ('土弹簧塑性位移', 'm'),
                 'supportReactionKn': ('活动支点反力', 'kN'), 'supportReferenceM': ('支点安装参考位移', 'm')}
        rows = []
        for key, (name, unit) in names.items():
            records = [(stage.get('stageId'), (stage.get('metrics') or {}).get(key)) for stage in independent['stages']]
            records = [(identity, value) for identity, value in records if value]
            if not records:
                continue
            control, worst = max(records, key=lambda pair: pair[1].get('maxNormalizedError', float('inf')))
            absolute = max((item.get('maxAbsoluteDifference', 0.0) for _, item in records), default=0.0)
            rows.append([name, unit, precision(absolute), precision(worst.get('absoluteTolerance')),
                         precision(worst.get('relativeTolerance')), precision(worst.get('maxNormalizedError')),
                         str(control)+' / '+('通过' if all(item.get('passed') for _, item in records) else '未通过')])
        _table(doc, ['比较量', '单位', '最大绝对差', '绝对容差', '相对容差', '最大归一\n化误差', '控制阶段\n与状态'], rows,
               [1.45, .5, 1.05, .9, .85, 1.15, .9])
        doc.add_paragraph('独立比较范围：'+str(independent.get('scope') or '以封存比较记录为准。'))

    mesh = comparisons.get('mesh') or {}
    if mesh.get('stages'):
        doc.add_heading('网格减半检查', level=2)
        doc.add_paragraph(f"基准网格 {number(mesh.get('coarseNodeCount'), 0)} 个节点，细网格 {number(mesh.get('fineNodeCount'), 0)} 个节点；"
                          f"相对容差 {precision(mesh.get('toleranceRelative'))}。表中为各组最大归一化误差，按前述绝对与相对组合阈值验收；不大于 1 为通过。")
        keys = ('maxAbsDisplacementMm', 'maxAbsMomentKnM', 'maxAbsShearKn', 'supportReactionsKn')
        _table(doc, ['阶段', '|u| 峰值', '|M| 峰值', '|V| 峰值', '活动支点反力', '状态'], [
            [stage.get('stageId')]+[precision((stage.get('metrics', {}).get(key) or {}).get('maxNormalizedError')) for key in keys]
            + ['通过' if stage.get('passed') else '未通过'] for stage in mesh['stages']], [1.0, 1.2, 1.2, 1.2, 1.5, .7])
        doc.add_paragraph('网格检查范围：'+str(mesh.get('scope') or '以封存比较记录为准。'))

    original = comparisons.get('original') or {}
    if original.get('referenceStageExtrema'):
        doc.add_heading('原资料摘录与条件缺项', level=2)
        doc.add_paragraph(str(original.get('reference') or '来源见输入证据表。')+'。以下仅保留原资料标注，未生成同条件误差百分比。')
        _table(doc, ['原阶段', 'u 范围\nmm', 'M 标注范围\nkN·m', 'V 标注范围\nkN'], [
            [row.get('stage'), f"{number(row.get('displacementMinMm'))} 至 {number(row.get('displacementMaxMm'))}",
             f"{number(row.get('momentMinLabel'))} 至 {number(row.get('momentMaxLabel'))}",
             f"{number(row.get('shearMinLabel'))} 至 {number(row.get('shearMaxLabel'))}"]
            for row in original['referenceStageExtrema']], [1.0, 1.7, 2.2, 2.2])
        for note in original.get('notes') or []:
            doc.add_paragraph(str(note))
        if original.get('missingConditions'):
            doc.add_paragraph('同条件对照缺项：'+'；'.join(str(value) for value in original['missingConditions'])+'。')


def _build_docx(data: dict, result: dict, output: Path, font, curves_generated: bool, reference=None) -> dict:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    from app.section.render_helper import number

    doc = Document()
    # python-docx's base template may carry a decorative Title paragraph rule.
    # The calculation book uses plain engineering headings without rules.
    for border in list(doc.styles.element.iter(qn('w:pBdr'))):
        border.getparent().remove(border)
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.65)
    section.left_margin = section.right_margin = Inches(.7)
    section.header_distance = section.footer_distance = Inches(.25)
    for style_name, size in [('Normal', 10.5), ('Title', 20), ('Subtitle', 11), ('Heading 1', 14), ('Heading 2', 11.5), ('Caption', 9), ('List Bullet', 10.5)]:
        style = doc.styles[style_name]
        style.font.name = font.family_name
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.underline = False
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn('w:rFonts'))
        if rfonts is None:
            rfonts = OxmlElement('w:rFonts'); rpr.append(rfonts)
        rfonts.set(qn('w:eastAsia'), font.family_name)
        style.paragraph_format.space_after = Pt(5)
        style.paragraph_format.line_spacing = 1.16
        if style_name.startswith('Heading'):
            style.paragraph_format.space_before = Pt(10)
            style.paragraph_format.keep_with_next = True
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run(f"剖面 {data['section_id']}  |  快照 {result['snapshotHash'][:12]}  |  结果 {result['resultHash'][:12]}  |  ")
    run.font.size = Pt(7.5)
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE'); footer._p.append(field)
    doc.core_properties.title = data['name'] + ' 剖面验证计算书'
    doc.core_properties.subject = f"snapshot={result['snapshotHash']}; result={result['resultHash']}"
    doc.core_properties.author = 'PitGuard'
    doc.add_paragraph(data['name'] + ' 剖面验证计算书', 'Title')
    doc.add_paragraph('冻结输入与结果同源成果', 'Subtitle')
    status = _state(result['status'])
    doc.add_paragraph(f'本计算书记录剖面 {data["section_id"]} 的已冻结输入、分阶段计算结果和配筋核对。当前状态为{status}，工程结论待复核。{REVIEW_TEXT}')
    if data['purpose'] == 'verification_fixture':
        doc.add_paragraph('本例为完整定义的合成验证模型，用于检验计算与成果链；其数值不代表原项目已完成同条件复核。')
    if not curves_generated:
        doc.add_paragraph('本次未取得完整、有效的阶段包络，因此不生成内力曲线或配筋需求表。失败证据保留在 section_result.json，剖面图仅表达冻结输入几何。')
    _table(doc, ['记录', '内容'], [
        ['工程来源', f"{data.get('source_project_id') or '无原项目身份'}  修订 {data.get('source_revision') or '未提供'}"],
        ['输入快照', result['snapshotHash'][:32]+'\n'+result['snapshotHash'][32:]],
        ['分析结果', result['resultHash'][:32]+'\n'+result['resultHash'][32:]],
        ['标高与方向', f"{data['datum']['label']}；{data['datum']['elevation_system']}。深度从桩顶向下，位移向坑内为正。"],
        ['量纲', '长度 m；位移 mm；单桩 M 为 kN·m、V 与支点反力为 kN；钢筋面积为每桩 mm²。'],
    ], [1.05, 6.05])
    doc.add_heading('一 输入与来源', level=1)
    pile, analysis, rebar = data['pile'], data['analysis'], data['reinforcement']
    _table(doc, ['参数', '数值', '参数', '数值'], [
        ['桩径 D', f"{number(pile['diameter_m'])} m", '桩距 s', f"{number(pile['spacing_m'])} m"],
        ['桩长 L', f"{number(pile['length_m'])} m", '桩顶标高', f"{number(data['datum']['wall_top_elevation_m'])} m"],
        ['弹性模量 E', f"{number(pile['elastic_modulus_kpa'], 0)} kN/m²", '刚度折减系数', pile['inertia_factor']],
        ['土弹簧有效宽度', f"{number(data['soil_effective_width_m'])} m", '桩端边界', {'free': '自由', 'fixed': '固定', 'pinned': '铰支'}[pile['toe_boundary']]],
        ['最大单元长度', f"{number(analysis['max_element_size_m'])} m", '水重度', f"{number(analysis['water_unit_weight_kn_m3'])} kN/m³"],
        ['内力效应系数', analysis['design_effect_factor'], '纯弯基准轴力 N', '0 kN'],
    ], [1.65, 1.9, 1.65, 1.9])
    doc.add_paragraph('效应系数依据：'+analysis['design_effect_factor_basis'])
    _table(doc, ['土层', '深度 m', '重度\n干/饱和', 'c kPa', 'φ °', 'K0', 'm 内/外\nkN/m⁴'], [
        [f"{s['id']} {s['name']}", f"{number(s['top_m'])} 至 {number(s['bottom_m'])}",
         f"{number(s['gamma_unsat_kn_m3'])}/{number(s['gamma_sat_kn_m3'])}", s['cohesion_kpa'], s['phi_deg'],
         s['k0'], f"{number(s['m_inside_kn_m4'], 0)}/{number(s['m_outside_kn_m4'], 0)}"] for s in data['soil_layers']
    ], [1.4, 1.05, .95, .6, .55, .55, 2.0])
    doc.add_paragraph('重度单位为 kN/m³。土层深度和支点位置均以同一桩顶深度坐标表达。桩距用于压力归集，土弹簧有效宽度单独冻结，不混用二者。')
    for evidence in data.get('evidence') or []:
        kind = {'source_fact': '资料事实', 'explicit_assumption': '明示假设', 'verification_definition': '验证定义'}[evidence['kind']]
        page = f" 第{evidence['page']}页" if evidence.get('page') else ''
        doc.add_paragraph(f"{kind}  {evidence['parameter']}：{evidence['source']}{page}。{evidence.get('note') or ''}")
    doc.add_heading('二 方法与实际代入', level=1)
    extensions = data.get('method_extensions')
    sigma_symbol = 'σ*' if extensions else 'σ′'
    doc.add_paragraph('围护桩采用 Euler–Bernoulli 梁。土体采用坑内、坑外独立的理想弹塑性 Winkler 弹簧，以K0压力为初态、Rankine 主被动压力为界限。开挖删除坑内已挖区弹簧，存续弹簧保留材料身份。')
    if extensions:
        doc.add_paragraph('本版逐层明确土水合算或分算。以下σ*为该压力站采用的总竖向应力或有效竖向应力，再加均布及有限邻载竖向增量；分算另加静水压力，合算不重复添加。两种模式的c、φ和K0必须分别具备对应的应力参数依据。')
        _table(doc, ['土层', '压力口径', '参数依据'], [
            [r['layer_id'], '有效应力加外水' if r['mode']=='effective_separated' else '总应力不另加水', r['basis']]
            for r in extensions['layer_stress_modes']], [1.0, 1.8, 4.3])
        doc.add_paragraph('m法依据：'+extensions['m_basis'])
        if extensions['surface_loads']:
            _table(doc, ['邻载', 'q kPa', '近远边距 m', '沿墙坐标 m', '位置依据'], [
                [r['id'], r['q_kpa'], f"{number(r['near_m'])} 至 {number(r['far_m'])}",
                 '无限条带' if r['kind']=='strip' else f"{number(r['along_start_m'])} 至 {number(r['along_end_m'])}", r['basis']]
                for r in extensions['surface_loads']], [1.0, .6, 1.2, 1.3, 3.0])
            doc.add_paragraph('有限邻载采用均质弹性半空间竖向应力影响，再进入K0与Rankine压力计算。条带沿墙无限，矩形沿墙起止位置均显式给定；当前不将其标为FRWS9.0原方法。')
            _equation(doc, ['Δσz = ', _fraction(['q'], ['π']), '[atan(b/z) − atan(a/z) + ',
                _fraction(['b z'], ['b² + z²']), ' − ', _fraction(['a z'], ['a² + z²']), ']'])
            _equation(doc, ['矩形 Δσz = ∫∫ ', _fraction(['3 q ', _math_part('sSup', 'z', '3')],
                ['2 π ', _math_part('sSup', '(x² + y² + z²)', '5/2')]), ' dx dy'])
    else:
        doc.add_paragraph('本v1快照使用有效应力K0初态，静水压力作为外荷载计入一次。')
    _equation(doc, ['I = ', _fraction(['π', _math_part('sSup', 'D', '4')], ['64']), '； EI = E η I'])
    trace = result.get('trace') or {}
    doc.add_paragraph(f"代入 D = {number(pile['diameter_m'])} m，E = {number(pile['elastic_modulus_kpa'], 0)} kN/m²，η = {number(pile['inertia_factor'])}。求解记录 I = {number(trace.get('inertiaM4'), 8)} m⁴，EI = {number(trace.get('eiKnM2'))} kN·m²。")
    _equation(doc, [_math_part('sSub', 'K', 'a'), ' = tan²(45° − φ/2)； ', _math_part('sSub', 'K', 'p'), ' = 1/', _math_part('sSub', 'K', 'a')])
    _equation(doc, [_math_part('sSub', 'p', '0'), ' = ', _math_part('sSub', 'K', '0'), sigma_symbol+'； ',
                   _math_part('sSub', 'p', 'a'), ' = max(0, ', _math_part('sSub', 'K', 'a'), sigma_symbol+' − 2c√', _math_part('sSub', 'K', 'a'), ')'])
    _equation(doc, [_math_part('sSub', 'p', 'p'), ' = ', _math_part('sSub', 'K', 'p'), sigma_symbol+' + 2c√', _math_part('sSub', 'K', 'p')])
    doc.add_paragraph(('总自重和孔压分别从实际地面与水位完整计算，当前土层决定压力口径，上层的合分算标志不改变自重积分。' if extensions else 'σ′按各阶段实际地面以下的有效重度积分；坑外地面均布荷载单独加入有效竖向应力。')+'每个积分点的压力乘桩距和积分权重形成单桩节点力。土弹簧刚度使用 m 值、该侧地面以下深度、有效宽度和积分权重。')
    doc.add_paragraph('离散方法：基准压力和水力积分保持总力与力矩；位移土弹簧采用节点 lumping 近似，须通过网格检查。此处不宣称非线性土反力的连续体一次矩精确。')
    _equation(doc, [_math_part('sSub', 'K', 'soil'), ' = m h ', _math_part('sSub', 'b', 'eff'), ' Δz； ',
                   _math_part('sSub', 'P', 'trial'), ' = ', _math_part('sSub', 'P', '0'), ' + ', _math_part('sSub', 'K', 'soil'), '(s u − ', _math_part('sSub', 'u', 'pl'), ')'])
    _equation(doc, ['P = clip(', _math_part('sSub', 'P', 'trial'), ', ', _math_part('sSub', 'P', 'a'), ', ', _math_part('sSub', 'P', 'p'), ')'])
    doc.add_paragraph('上式 P 为节点力 kN，Ksoil 为节点刚度 kN/m；s 表示土侧方向。塑性历史只在阶段收敛后提交，不在迭代中重复累积。完整离散/收敛证据以结果 JSON 为准。')
    _equation(doc, ['R = K(', 'u − ', _math_part('sSub', 'u', 'install'), ')； ',
                   _math_part('sSub', 'M', 'd'), ' = γ ', _math_part('sSub', 'M', 'k'), '； ',
                   _math_part('sSub', 'V', 'd'), ' = γ ', _math_part('sSub', 'V', 'k')])
    doc.add_paragraph(f"支撑零预力安装，安装时记录参考位移；刚性换撑为精确位移约束。γ = {number(trace.get('designEffectFactor', analysis['design_effect_factor']))} 只作用于内力效应一次；位移为标准阶段位移，不乘该系数。")
    doc.add_paragraph('符号约定：'+str(trace.get('signConvention') or '以结果点值所列符号为准，未给出额外受拉侧对应关系。'))

    doc.add_heading('三 施工阶段与支点', level=1)
    _table(doc, ['阶段', '事件', '开挖 m', '水位深度\n坑内/坑外 m', '均布荷载\nkPa', '安装或拆除'], [
        [f"{s['id']} {s['name']}", {'initial': '初态', 'excavate': '开挖', 'install': '安装', 'remove': '拆除', 'water_or_load': '水位或荷载', 'soil_reset': '土体重置'}[s['event']],
         s['excavation_depth_m'], f"{number(s['water_inside_depth_m'])}/{number(s['water_outside_depth_m'])}", s['surcharge_kpa'],
         '安装 '+','.join(s['install_support_ids']) if s['install_support_ids'] else ('拆除 '+','.join(s['remove_support_ids']) if s['remove_support_ids'] else '无')]
        for s in data['stages']], [1.9, .6, .65, 1.25, .75, 1.95])
    if data['supports']:
        _table(doc, ['支点', '深度 m', '类型', '单桩刚度 kN/m', '方向', '预力 kN'], [
            [s['id'], s['depth_m'], '刚性约束' if s['kind']=='rigid' else '弹性',
             '精确位移约束' if s['kind']=='rigid' else s['stiffness_kn_per_m'], '仅受压' if s['compression_only'] else '双向', s['preload_kn']]
            for s in data['supports']], [1.5, .8, 1.0, 1.8, 1.2, .8])
    doc.add_paragraph('剖面图按最后一个冻结输入阶段绘制；所有阶段的几何和操作见本表。相同开挖深度的安装、拆除操作各为独立事件，不能合并。')
    if extensions:
        from .review_export import HISTORY_NAMES
        _table(doc, ['阶段', '激活有限邻载', '土体历史操作'], [
            [r['stage_id'], ', '.join(r['active_load_ids']) or '无', HISTORY_NAMES[r['soil_history_action']]]
            for r in extensions['stage_options']], [1.0, 1.8, 4.3])
        doc.add_paragraph('继承使用上阶段收敛塑性位移；清零将土弹簧塑性偏移设为零；重设将其设为上阶段该侧局部位移。三者都不清除梁位移或支点安装参考。原软件采用何种卸载或重置规则，须由原生选项和小算例确认。')

    doc.add_heading('四 计算结果与包络', level=1)
    if curves_generated:
        _table(doc, ['阶段', '最大 |M|\nkN·m', '最大 |V|\nkN', '最大 |u|\nmm', '状态'], [
            [s['id'], s.get('maxAbsMomentKnM'), s.get('maxAbsShearKn'), s.get('maxAbsDisplacementMm'), _state(s.get('status'))]
            for s in result['stages']], [1.3, 1.45, 1.4, 1.4, 1.55])
        governing = result['envelope'].get('governing') or {}
        for key, label, val, unit in [('momentAbs', '标准弯矩', 'valueKnM', 'kN·m'), ('shearAbs', '标准剪力', 'valueKn', 'kN'), ('displacementAbs', '位移', 'valueMm', 'mm')]:
            row = governing.get(key) or {}
            doc.add_paragraph(f"控制{label} {number(row.get(val))} {unit}，阶段 {row.get('stageId', '未提供')}，深度 {number(row.get('depthM'))} m，节点侧别 {row.get('side', '未提供')}。")
        for filename, caption in [('envelope_characteristic.png', '标准特征内力与位移包络'), ('envelope_design.png', '设计内力包络')]:
            p = doc.add_paragraph()
            p.paragraph_format.keep_with_next = True
            p.add_run().add_picture(str(output/filename), width=Inches(6.9))
            doc.add_paragraph(caption, 'Caption')
        support_rows = [[s['id'], r.get('id'), r.get('depthM'),
                         {'active': '激活', 'removed': '拆除', 'not_installed': '未装', 'not_calculated': '未算'}.get(r.get('status'), '激活' if r.get('active') else '未激活'),
                         r.get('referenceDisplacementMm'),
                         r.get('reactionCharacteristicKn'), r.get('reactionDesignKn')]
                        for s in result['stages'] for r in s.get('supports') or []]
        if support_rows:
            _table(doc, ['阶段', '支点', '深度 m', '状态', '安装参考\n位移 mm', '标准反力\nkN', '设计反力\nkN'],
                   support_rows, [.6, 1.4, .7, .5, 1.3, 1.3, 1.3])
    else:
        doc.add_paragraph('没有完整可用的包络。请先解决求解失败或阶段证据缺项，再生成内力和配筋成果。')
        for row in result.get('stages') or []:
            if row.get('status') not in {'complete', 'success'}:
                doc.add_paragraph(f"{row.get('id')}：{_state(row.get('status'))}。{row.get('message') or row.get('failureReason') or ''}")

    doc.add_heading('五 配筋需求与给定筋核对', level=1)
    doc.add_paragraph('圆截面采用给定纵筋沿圆周的实际离散布置，按 N=0 的应变协调与截面合力平衡检算。混凝土采用显式抛物线平台受压曲线并忽略受拉；钢筋采用对称理想弹塑性。需求面积与给定面积分别保留，未进行选筋或自动采用。')
    _equation(doc, [_math_part('sSub', 'A', 's,provided'), ' = n ', _fraction(['π', _math_part('sSup', 'd', '2')], ['4']),
                   '； ', _math_part('sSub', 'A', 's,min'), ' = ', _math_part('sSub', 'ρ', 'min'), _fraction(['π', _math_part('sSup', 'D', '2')], ['4'])])
    _equation(doc, ['ε(y) = ', _math_part('sSub', 'ε', '0'), ' + κ y； N = ∫σ dA = 0； M = ∫σ y dA'])
    provided = '未提供，不作补填' if rebar.get('bar_count') is None else f"{rebar['bar_count']} 根，直径 {number(rebar['bar_diameter_mm'])} mm"
    _table(doc, ['输入', '数值', '输入', '数值'], [
        ['给定纵筋', provided, '纵筋中心至边缘', f"{number(rebar['bar_axis_cover_mm'])} mm"],
        ['混凝土设计强度', f"{number(rebar['concrete_design_strength_mpa'])} MPa", '钢筋设计强度', f"{number(rebar['steel_design_strength_mpa'])} MPa"],
        ['混凝土峰值/极限应变', f"{rebar['concrete_peak_strain']:g}/{rebar['concrete_ultimate_strain']:g}", '最小纵筋率', rebar['minimum_longitudinal_ratio']],
        ['给定箍筋直径', number(rebar.get('stirrup_diameter_mm'))+' mm', '给定箍筋间距', number(rebar.get('stirrup_spacing_mm'))+' mm'],
        ['纵筋沿全长布置', '是' if rebar.get('longitudinal_extent') == 'full_length' else '未确认', '圆周起始角度', number(rebar.get('bar_start_angle_deg'))+' °'],
    ], [1.7, 1.85, 1.7, 1.85])
    doc.add_paragraph('最小配筋率依据：'+rebar['minimum_ratio_basis'])
    reinforcement = result.get('reinforcement') or {}
    if curves_generated and reinforcement.get('rows'):
        _table(doc, ['分区与控制阶段', 'Md\nkN·m', '需求\nmm²', '最小\nmm²', '给定\nmm²', '承载力\nkN·m', '状态'], [
            [f"{r.get('id')}\n{number(r.get('fromDepthM'))} 至 {number(r.get('toDepthM'))} m\n{r.get('governingStageId')}",
             r.get('momentDesignKnM'), r.get('requiredAreaMm2'), r.get('minimumAreaMm2'),
             r.get('providedAreaMm2') if rebar.get('bar_count') is not None else None,
             r.get('capacityKnM'), _state(r.get('status'))] for r in reinforcement['rows']
        ], [1.6, .8, .8, .8, .8, .9, 1.4])
        for row in reinforcement['rows']:
            for note in row.get('notes') or []:
                doc.add_paragraph(str(note))
    else:
        doc.add_paragraph('本次不提供配筋需求数值。给定筋仍作为冻结输入保留，不作为计算通过结论。')
    if not extensions:
        for limitation in reinforcement.get('limitations') or []:
            doc.add_paragraph(str(limitation))
        doc.add_paragraph('抗剪、裂缝、锚固、接头、间距、最小保护层、箍筋构造与稳定性未由此抗弯检查覆盖，需分别复核。')
    else:
        doc.add_paragraph('以上纯弯基准不代替其他工程检查。抗剪、轴弯、保护层、箍筋、锚固及使用阶段的实际计算与缺项在下节逐项列出；整体稳定和施工节点仍须另行验证。')
    if extensions:
        from .review_export import append_engineering_document
        append_engineering_document(doc, data, result, _table, _state)
        doc.add_paragraph('reinforcement.csv保存N=0纯弯基准需求；engineering_checks.csv保存工程检查的独立状态、适用域与作用来源。rebar_geometry.pdf和DXF仅核对实际给定纵筋及外箍几何，不生成未获依据的锚固节点。')

    doc.add_heading('六 对照与适用范围', level=1)
    comparisons = result.get('comparisons') or {}
    _table(doc, ['比较项', '状态', '说明'], [
        [name, _state((comparisons.get(key) or {}).get('status')),
         str((comparisons.get(key) or {}).get('reason') or (comparisons.get(key) or {}).get('summary') or (comparisons.get(key) or {}).get('message') or '各指标及阈值见 comparison.csv；完整结构见 section_result.json。')]
        for key, name in COMPARISON_NAMES.items()
    ], [1.4, 1.4, 4.3])
    _comparison_details(doc, comparisons)
    if reference:
        from .review_export import append_reference_document
        append_reference_document(doc, reference, _table, _state)
    limitations = list(dict.fromkeys([str(x) for x in data.get('limitations') or []]
        + [str(x) for x in (result.get('scope') or {}).get('limitations') or []]))
    for limitation in limitations:
        doc.add_paragraph(limitation, 'List Bullet')
    doc.add_paragraph('同条件比较必须同时匹配几何、荷载、水土算法、边界、阶段操作、单位和内力归集宽度。参考资料缺项或方法不同的记录保持未比较或不可比较，不据此宣称原工程通过。')
    doc.add_paragraph('成果对应关系：section_A3.pdf 与 section.dxf 共用真实几何和精确比例；envelopes.csv 与 stage_results.csv 保留完整点值；reinforcement.csv 区分需求和给定；所有文件的哈希在 manifest.json 和 SHA256SUMS.txt 中登记。')
    doc.add_paragraph(REVIEW_TEXT)
    from .docx_fonts import embed_document_font
    from .render_helper import resolve_cjk_font
    embedded = embed_document_font(doc, resolve_cjk_font())
    if embedded['sourceSha256'] != font.source_sha256 or embedded['family'] != font.family_name:
        raise ValueError('DOCX and PDF font sources changed during export')
    doc.save(output/'calculation.docx')
    return embedded


def _inspect_outputs(data: dict, result: dict, scene: dict, output: Path) -> dict:
    import ezdxf
    from lxml import etree
    checks = []
    def check(name, passed, details):
        checks.append({'name': name, 'status': 'pass' if passed else 'fail', 'details': details})
    dxf = ezdxf.readfile(output/'section.dxf')
    viewport = list(dxf.layouts.get('A3-SECTION').query('VIEWPORT[layer=="SV_VIEWPORT"]'))[0]
    ratio = viewport.dxf.view_height/viewport.dxf.height
    check('dxf_true_scale', abs(ratio-scene['scale']) < 1e-9, {'expected': scene['scale'], 'measured': ratio})
    measured = [abs(float(item.get_measurement())) for item in dxf.modelspace().query('DIMENSION')]
    expected = scene['lengthM']*1000
    check('native_vertical_dimension_mm', any(abs(v-expected) < 1e-5 for v in measured), {'expected': expected, 'measurements': measured})
    check('dxf_units_and_audit', dxf.header.get('$INSUNITS') == 4 and not dxf.audit().errors, {'units': 'mm'})
    raw = (output/'section_A3.pdf').read_bytes()
    match = re.search(rb'/MediaBox\s*\[\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*\]', raw)
    media = list(map(float, match.groups())) if match else None
    check('pdf_a3_paper', bool(media and abs((media[2]-media[0])-420/25.4*72)<.03
                             and abs((media[3]-media[1])-297/25.4*72)<.03), {'mediaBoxPoints': media})
    with zipfile.ZipFile(output/'calculation.docx') as archive:
        xml = etree.fromstring(archive.read('word/document.xml'))
    equations = xml.findall('.//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath')
    check('docx_native_equations', len(equations) >= 5, {'equationCount': len(equations)})
    stored = json.loads((output/'section_result.json').read_text(encoding='utf-8'))
    check('sealed_result_unchanged', stored == result, {'resultHash': result['resultHash']})
    if any(item['status'] == 'fail' for item in checks):
        raise ValueError('Section export consistency check failed: '+str([c['name'] for c in checks if c['status']=='fail']))
    return {'schemaVersion': 'pitguard.section-delivery-check/1', 'snapshotHash': result['snapshotHash'],
            'resultHash': result['resultHash'], 'status': 'pass', 'checks': checks,
            'visualReview': 'not_automatically_certified', 'engineeringStatus': 'review_required'}


def build_minimum_package(snapshot: FrozenSectionSnapshot, result: dict, output_dir: str | Path, *, reference=None) -> Path:
    """Return a ZIP and retain the unpacked files beside it, with exact source hashes."""
    from app.section.service import validate_result
    validate_result(snapshot, result)  # must run before creating any output
    if reference is not None:
        from .reference_workflow import reference_supplement
        expected = reference_supplement(snapshot, result, reference.get('contract'),
                                       reference.get('referenceCurves'), reference.get('comparison'))
        if reference != expected:
            raise ValueError('Reference supplement hash or result identity mismatch')
    elif snapshot.payload().get('reference_contract_hash') is not None:
        raise ValueError('本快照已绑定原项目条件合同，导出须同时提供该冻结合同')
    from app.section.render_helper import (build_scene, export_dxf, export_envelopes,
                                          export_section_pdf, prepare_render_font, STATIC_TEXT)
    data = snapshot.payload()
    sealed = deepcopy(result)
    reference = deepcopy(reference)
    before = content_hash(sealed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r'[^A-Za-z0-9_.-]+', '-', data['section_id']).strip('-.')[:60] or 'section'
    name = f'section_{safe_id}_{snapshot.snapshot_hash[:10]}_{sealed["resultHash"][:10]}'
    with tempfile.TemporaryDirectory(prefix='.section-export-', dir=output_dir) as working:
        temp = Path(working)
        package = temp/'package'
        package.mkdir()
        with _RENDER_LOCK:
            from .review_export import REVIEW_GLYPHS, write_review_data, export_pressure_pages, export_rebar_geometry
            font = prepare_render_font(temp/'font', STATIC_TEXT+REVIEW_GLYPHS+json.dumps(data, ensure_ascii=False)
                                       +json.dumps(sealed, ensure_ascii=False)+json.dumps(reference, ensure_ascii=False))
            scene = build_scene(data, sealed)
            curves_generated = bool(sealed.get('status') == 'complete' and (sealed.get('envelope') or {}).get('complete')
                                    and (sealed.get('envelope') or {}).get('points'))
            _json(package/'frozen_section.json', snapshot.to_record())
            _json(package/'section_result.json', sealed)
            _write_tables(data, sealed, package, curves_generated)
            write_review_data(snapshot, sealed, package, reference)
            export_dxf(data, sealed, scene, package/'section.dxf')
            export_section_pdf(data, sealed, scene, font, package/'section_A3.pdf')
            if curves_generated:
                export_envelopes(data, sealed, font, package)
            cage_generated = False
            if curves_generated and data.get('method_extensions'):
                export_pressure_pages(data, sealed, font, package)
                cage_generated = export_rebar_geometry(data, sealed, font, package)
            embedded_docx_font = _build_docx(data, sealed, package, font, curves_generated, reference)
        consistency = _inspect_outputs(data, sealed, scene, package)
        _json(package/'consistency.json', consistency)
        (package/'README.txt').write_text(
            f"PitGuard 独立剖面验证成果\n{REVIEW_TEXT}\n\n"
            f"剖面：{data['section_id']}\n输入快照：{sealed['snapshotHash']}\n分析结果：{sealed['resultHash']}\n"
            f"计算状态：{_state(sealed['status'])}\n剖面图：A3横向，水平竖向1:{scene['scale']}。打印时按实际尺寸100%，勿使用适合页面。\n"
            "DXF：R2018，模型空间1:1 mm，纸空间A3精确比例锁定视口。中文文字引用PIT_CN样式，可在CAD指定支持中文的字体。\n"
            "量纲：单桩内力M kN·m、V kN；位移mm；钢筋面积每桩mm²，不能按每米墙宽直接使用。\n"
            "标准和设计内力分列。设计效应系数只施加一次；设计位移没有独立定义，CSV中留空。\n"
            "支点上下侧分别保存剪力跳跃；未知值留空，不等同于零。给定钢筋缺失不由建议补填。\n"
            "工程范围与未验证事项见计算书和封存JSON。数值对照通过不表示整体工程安全或施工图可发行。\n"
            "所有文件来自本包内同一snapshot/result。SHA256SUMS.txt核对实际文件字节；manifest不参与自身哈希。\n",
            encoding='utf-8')
        files = {p.relative_to(package).as_posix(): {'bytes': p.stat().st_size,
                 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(package.rglob('*')) if p.is_file()}
        manifest = {'schemaVersion': EXPORT_SCHEMA, 'exportVersion': EXPORT_VERSION,
            'createdAt': datetime.now(timezone.utc).isoformat(), 'sectionId': data['section_id'],
            'snapshotHash': sealed['snapshotHash'], 'analysisHash': sealed['analysisHash'], 'resultHash': sealed['resultHash'],
            'source': data['purpose'], 'sourceProjectId': data.get('source_project_id'), 'sourceRevision': data.get('source_revision'),
            'analysisStatus': sealed['status'], 'engineeringStatus': 'review_required', 'formalConstructionIssue': False,
            'engineeringCheckStatus': (sealed.get('engineeringChecks') or {}).get('status', 'not_calculated'),
            'referenceSupplementHash': reference['supplementHash'] if reference else None,
            'givenCageGeometryGenerated': cage_generated,
            'curvesGenerated': curves_generated, 'reinforcementScheduleGenerated': curves_generated,
            'forceBasis': 'single_pile', 'units': {'geometry': 'm', 'dxfModel': 'mm', 'moment': 'kN·m', 'shear': 'kN', 'displacement': 'mm', 'steelArea': 'mm²/pile'},
            'drawing': {'paper': 'A3', 'widthMm': 420, 'heightMm': 297, 'scaleDenominator': scene['scale'],
                        'stageId': scene['stage']['id'], 'dimensions': 'native_mm', 'horizontalVerticalSameScale': True},
            'font': {'sourceFile': font.source_name, 'sourceSha256': font.source_sha256,
                     'pdfEmbedding': 'Unicode TrueType subset', 'docxEmbedding': embedded_docx_font},
            'consistencyStatus': consistency['status'], 'visualReview': consistency['visualReview'], 'files': files}
        _json(package/'manifest.json', manifest)
        sums = '\n'.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(package).as_posix()}'
                         for p in sorted(package.rglob('*')) if p.is_file())+'\n'
        (package/'SHA256SUMS.txt').write_text(sums, encoding='utf-8')
        if content_hash(sealed) != before or content_hash(result) != before:
            raise ValueError('Result changed during export / 导出期间结果被修改')
        # mkdtemp reserves a distinct final name atomically across threads and
        # workers, including two downloads of the same immutable result.
        destination = Path(tempfile.mkdtemp(prefix=name+'_', dir=output_dir))
        try:
            for generated in package.iterdir():
                generated.rename(destination/generated.name)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    zip_path = destination.parent/(destination.name+'.zip')
    with tempfile.NamedTemporaryFile(prefix='.section-zip-', suffix='.tmp', dir=output_dir, delete=False) as temporary:
        temporary_zip = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for path in sorted(destination.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(destination).as_posix())
        temporary_zip.replace(zip_path)
    finally:
        temporary_zip.unlink(missing_ok=True)
    return zip_path
