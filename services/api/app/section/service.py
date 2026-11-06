from __future__ import annotations

from bisect import bisect_left, bisect_right
import copy
from hashlib import sha256
from math import pi
from pathlib import Path

import numpy as np

from app.section.models import FrozenSectionSnapshot, algorithm_for, content_hash
from app.section.preprocess import build_solver_model
from app.section.reinforcement import check_reinforcement
from app.section.solver import solve_stages


def seal_result(result: dict) -> dict:
    sealed = copy.deepcopy(result)
    sealed.pop('resultHash', None)
    sealed['resultHash'] = content_hash(sealed)
    return sealed


def validate_result(snapshot: FrozenSectionSnapshot, result: dict) -> None:
    snapshot.payload()  # Verify even a directly constructed dataclass instance.
    if result.get('snapshotHash') != snapshot.snapshot_hash or result.get('analysisHash') != snapshot.analysis_hash:
        raise ValueError('结果与冻结输入的hash/哈希不一致，禁止混合出图')
    without_hash = {key: value for key, value in result.items() if key != 'resultHash'}
    if result.get('resultHash') != content_hash(without_hash):
        raise ValueError('Result hash mismatch / 结果哈希不一致，内容已被更改')
    if [s['id'] for s in result.get('stages', [])] != [s['id'] for s in snapshot.payload()['stages']]:
        raise ValueError('结果阶段身份不完整或顺序不匹配')
    try:
        _validate_result_contract(snapshot, result)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('结果结构缺失或类型不符合冻结剖面合同') from exc


def _validate_result_contract(snapshot: FrozenSectionSnapshot, result: dict) -> None:
    """A digest detects changes; it is not a substitute for internal consistency.

    Check recorded arrays and their derived views without re-solving the model
    or regenerating a reinforcement proposal. The API exports server-stored
    results, never caller-supplied result JSON.
    """
    inputs = snapshot.validated_input()
    physical = build_solver_model(snapshot)
    nodes = physical['nodesM']
    factor = inputs.analysis.design_effect_factor
    if (result['schemaVersion'] != 'pitguard.section-result/1' or result['algorithmVersion'] != algorithm_for(snapshot.payload()) or
        result['sectionId'] != inputs.section_id or result['discreteInputHash'] != content_hash(physical)):
        raise ValueError('算法、剖面身份或离散输入不匹配')
    if result['formalDesignReady'] is not False or result['engineeringStatus'] != 'review_required':
        raise ValueError('本剖面通道不具备正式工程设计放行状态')
    if result['mesh']['nodesM'] != nodes or result['mesh']['nodeCount'] != len(nodes):
        raise ValueError('结果网格与冻结输入不一致')
    if result['trace']['designEffectFactor'] != factor or result['trace']['forceBasis'] != 'single_pile':
        raise ValueError('结果内力系数或单桩宽度身份不一致')
    stages = result['stages']
    for given, stage, physical_stage in zip(inputs.stages, stages, physical['stages']):
        if stage['event'] != given.event or stage['name'] != given.name:
            raise ValueError('阶段事件或名称不匹配')
        for output, field in (('excavationDepthM', 'excavation_depth_m'), ('waterInsideDepthM', 'water_inside_depth_m'),
                              ('waterOutsideDepthM', 'water_outside_depth_m'), ('surchargeKpa', 'surcharge_kpa')):
            if stage[output] != getattr(given, field):
                raise ValueError('阶段物理状态与冻结输入不匹配')
        if inputs.method_extensions:
            for key in ('soilHistoryAction', 'soilHistoryBasis', 'activeSurfaceLoadIds'):
                if stage[key] != physical_stage[key]:
                    raise ValueError('阶段荷载或土体历史规则与冻结输入不匹配')
        if stage['status'] not in {'success', 'failed', 'not_calculated'}:
            raise ValueError('未知阶段状态')
        if stage['status'] == 'success':
            for key, shape in (('nodalDisplacementM', (len(nodes),)), ('nodalRotation', (len(nodes),)),
                                ('elementEndForces', (len(nodes) - 1, 4))):
                values = np.asarray(stage[key], dtype=float)
                if values.shape != shape or not np.all(np.isfinite(values)):
                    raise ValueError(f'成功阶段{stage["id"]}的{key}不完整或包含非有限数')
            if [s['id'] for s in stage['supports']] != [s.id for s in inputs.supports]:
                raise ValueError('支撑结果身份不完整')
            for support in stage['supports']:
                force = support['reactionCharacteristicKn']
                if support['reactionDesignKn'] != (force * factor if force is not None else None):
                    raise ValueError('支撑反力的设计值与特征值系数不一致')
        elif stage['points'] or any(stage[key] is not None for key in ('maxAbsDisplacementMm', 'maxAbsMomentKnM', 'maxAbsShearKn', 'nodalDisplacementM', 'nodalRotation', 'elementEndForces')):
            raise ValueError('失败或未计算阶段不得保留有效曲线或数值')
    locations = _evaluation_locations(stages, nodes)
    for stage in stages:
        if stage['status'] != 'success':
            continue
        expected_points = [_point(stage, nodes, depth, side, factor) for depth, side in locations]
        if stage['points'] != expected_points:
            raise ValueError('阶段曲线与其记录的梁端力或位移不一致')
        for peak, key in (('maxAbsDisplacementMm', 'displacementMm'), ('maxAbsMomentKnM', 'momentCharacteristicKnM'), ('maxAbsShearKn', 'shearCharacteristicKn')):
            if stage[peak] != max(abs(p[key]) for p in expected_points):
                raise ValueError('阶段峰值与曲线不一致')
    expected_envelope = _envelope(stages, factor)
    if result['envelope'] != expected_envelope:
        raise ValueError('最终包络与全部阶段曲线不一致')
    complete = expected_envelope['complete']
    if result['status'] != ('complete' if complete else 'failed'):
        raise ValueError('总体状态与阶段完整性不一致')
    reinforcement = result['reinforcement']
    if reinforcement['formalDesignReady'] is not False:
        raise ValueError('纯弯截面检算不得代替正式配筋设计放行')
    if not complete:
        if reinforcement['rows'] or reinforcement['status'] != 'not_calculated':
            raise ValueError('不完整包络不得生成配筋需求')
    else:
        if len(reinforcement['rows']) != 1:
            raise ValueError('本版全长给定纵筋必须对应唯一配筋核对行')
        row = reinforcement['rows'][0]
        control = expected_envelope['governing']
        r = inputs.reinforcement
        supplied = None if r.bar_count is None else r.bar_count * pi * r.bar_diameter_mm ** 2 / 4
        if (row['momentDesignKnM'] != abs(control['momentAbs']['valueKnM']) * factor or
            row['shearDesignKn'] != abs(control['shearAbs']['valueKn']) * factor or
            row['governingStageId'] != control['momentAbs']['stageId'] or
            row['shearGoverningStageId'] != control['shearAbs']['stageId'] or
            row['providedAreaMm2'] != supplied or row['fromDepthM'] != 0.0 or row['toDepthM'] != inputs.pile.length_m):
            raise ValueError('配筋表需求、给定钢筋或控制阶段与最终包络不一致')
        if row['capacityKnM'] is not None and row['utilization'] != row['momentDesignKnM'] / row['capacityKnM']:
            raise ValueError('配筋表利用系数与需求和容量不一致')
    if result['comparisons']['original'] != original_comparison_status(snapshot):
        raise ValueError('原项目对照证据状态不符合本快照绑定资料')
    if inputs.method_extensions:
        engineering = result['engineeringChecks']
        if engineering['formalDesignReady'] is not False or engineering['binding'] != _engineering_binding(snapshot, stages, expected_envelope):
            raise ValueError('工程配筋检查与冻结输入、全阶段需求或最终包络不一致')


def _engineering_binding(snapshot, stages, envelope):
    data = snapshot.payload()
    return {'snapshotHash': snapshot.snapshot_hash,
            'engineeringInputHash': content_hash({key: data[key] for key in ('pile', 'reinforcement', 'engineering_design', 'analysis')}),
            'stageDemandHash': content_hash([{'id': s['id'], 'status': s['status'], 'points': s['points']} for s in stages]),
            'envelopeHash': content_hash(envelope)}


def _hermite_coefficients(raw: dict, nodes: list[float], element: int) -> tuple[float, float, float, float]:
    length = nodes[element + 1] - nodes[element]
    yi, yj = raw['nodalDisplacementM'][element:element + 2]
    ti, tj = raw['nodalRotation'][element:element + 2]
    # y(t)=a*t^3+b*t^2+c*t+d, t=(z-z_i)/L.
    return (2 * yi - 2 * yj + length * (ti + tj),
            -3 * yi + 3 * yj - length * (2 * ti + tj), length * ti, yi)


def _evaluation_locations(raw_stages: list[dict], nodes: list[float]) -> list[tuple[float, str]]:
    locations = {(nodes[i], 'below') for i in range(len(nodes) - 1)}
    locations.update((nodes[i], 'above') for i in range(1, len(nodes)))
    for raw in raw_stages:
        if raw['status'] != 'success':
            continue
        for element in range(len(nodes) - 1):
            a, b, c, _ = _hermite_coefficients(raw, nodes, element)
            coefficients = np.trim_zeros(np.array([3 * a, 2 * b, c]), 'f')
            if len(coefficients) < 2:
                continue
            for root in np.roots(coefficients):
                if abs(root.imag) < 1e-9 and 1e-7 < root.real < 1 - 1e-7:
                    depth = nodes[element] + float(root.real) * (nodes[element + 1] - nodes[element])
                    locations.add((round(depth, 10), 'within'))
    ordering = {'above': 0, 'within': 1, 'below': 2}
    return sorted(locations, key=lambda item: (item[0], ordering[item[1]]))


def _point(raw: dict, nodes: list[float], depth: float, side: str, factor: float) -> dict:
    element = (bisect_left(nodes, depth) - 1 if side == 'above' else bisect_right(nodes, depth) - 1)
    element = max(0, min(element, len(nodes) - 2))
    length = nodes[element + 1] - nodes[element]
    x = depth - nodes[element]
    t = x / length
    a, b, c, d = _hermite_coefficients(raw, nodes, element)
    displacement = ((a * t + b) * t + c) * t + d
    rotation = (3 * a * t ** 2 + 2 * b * t + c) / length
    vi, mi, _, _ = raw['elementEndForces'][element]
    moment = -mi + vi * x
    return {'depthM': depth, 'side': side, 'elementIndex': element,
            'displacementMm': displacement * 1000, 'rotationRad': rotation,
            'momentCharacteristicKnM': moment, 'shearCharacteristicKn': vi,
            'momentDesignKnM': moment * factor, 'shearDesignKn': vi * factor}


def _envelope(stages: list[dict], factor: float) -> dict:
    if any(stage['status'] != 'success' for stage in stages):
        return {'complete': False, 'points': [], 'governing': None,
                'reason': '至少一个阶段失败或未计算，禁止从部分结果宣称完整包络'}
    rows = []
    for point_index in range(len(stages[0]['points'])):
        points = [(stage['id'], stage['points'][point_index]) for stage in stages]
        first = points[0][1]
        row = {'depthM': first['depthM'], 'side': first['side']}
        for short, key, unit in (('m', 'momentCharacteristicKnM', 'KnM'),
                                 ('v', 'shearCharacteristicKn', 'Kn'), ('u', 'displacementMm', 'Mm')):
            for suffix, chooser in (('Min', min), ('Max', max)):
                identity, point = chooser(points, key=lambda pair: pair[1][key])
                row[f'{short}{suffix}{unit}'] = point[key]
                row[f'{short}{suffix}StageId'] = identity
                if short != 'u':
                    row[f'{short}d{suffix}{unit}'] = point[key] * factor
        rows.append(row)
    governing = {}
    for name, key, value_key in (('momentAbs', 'momentCharacteristicKnM', 'valueKnM'),
                                  ('shearAbs', 'shearCharacteristicKn', 'valueKn'),
                                  ('displacementAbs', 'displacementMm', 'valueMm')):
        identity, point = max(((stage['id'], point) for stage in stages for point in stage['points']),
                              key=lambda pair: abs(pair[1][key]))
        governing[name] = {value_key: point[key], 'stageId': identity,
                           'depthM': point['depthM'], 'side': point['side']}
    return {'complete': True, 'points': rows, 'governing': governing,
            'forceBasis': 'single_pile', 'designEffectFactor': factor,
            'designDisplacementStatus': 'not_calculated',
            'scope': 'All committed stages; signed force envelopes; both sides of nodal jumps retained'}


def original_comparison_status(snapshot: FrozenSectionSnapshot) -> dict:
    if snapshot.payload()['comparison_reference'] is None:
        return {'status': 'not_attached', 'sameConditionsConfirmed': False,
                'reason': '本快照未绑定原项目对照模型或资料。'}
    return {
        'status': 'incomplete', 'sameConditionsConfirmed': False,
        'reason': '原项目PDF提供部分结果，尚未具备原生模型和完整方法参数，不能进行可判定的同条件比较。',
        'reference': 'A3-3 / uploaded calculation report / PDF pages 32–37',
        'sourceSha256': 'ac339cb5f33e83b5ed51407aa032fa7c309e0a57f6d8a3090b6fa9d03340bbfb',
        'comparisonType': 'reference_evidence_only',
        'currentCasePurpose': snapshot.payload()['purpose'],
        'missingConditions': [
            '原生FRWS模型及版本与计算选项', '数值E与EI折减系数', 'm法具体定义、有效土宽与屈服设置',
            '桩端边界与冠梁等效支撑生效阶段', 'M/V单桩或每米宽度身份',
            '坡台、有限宽邻载与土水合算的同条件实现', '原设计/特征内力组合及修正系数完整来源',
        ],
        'referenceStageExtrema': [
            {'stage': '1', 'displacementMinMm': 0.6, 'displacementMaxMm': 2.6, 'momentMinLabel': 0, 'momentMaxLabel': 87.7, 'shearMinLabel': -43, 'shearMaxLabel': 49.9},
            {'stage': '2', 'displacementMinMm': 0.6, 'displacementMaxMm': 2.6, 'momentMinLabel': 0, 'momentMaxLabel': 87.7, 'shearMinLabel': -43, 'shearMaxLabel': 49.9},
            {'stage': '3', 'displacementMinMm': 2.9, 'displacementMaxMm': 6.0, 'momentMinLabel': 0, 'momentMaxLabel': 206.7, 'shearMinLabel': -91.8, 'shearMaxLabel': 120.6},
            {'stage': '4', 'displacementMinMm': 2.9, 'displacementMaxMm': 6.0, 'momentMinLabel': 0, 'momentMaxLabel': 206.7, 'shearMinLabel': -91.8, 'shearMaxLabel': 120.6},
            {'stage': '5', 'displacementMinMm': 3.6, 'displacementMaxMm': 6.0, 'momentMinLabel': 0, 'momentMaxLabel': 176.9, 'shearMinLabel': -80.2, 'shearMaxLabel': 109.6},
        ],
        'notes': ['原资料曲线标注M为kNm、V为kN，宽度身份尚待确认，因此未生成误差百分比。',
                  '本文验证例与原A3-3的土层、地形和边界不同；数值接近不构成原模型复现。'],
    }


def analyze_section(snapshot: FrozenSectionSnapshot) -> dict:
    inputs = snapshot.validated_input()
    discrete = build_solver_model(snapshot)
    raw_result = solve_stages(discrete)
    expected_events = [(stage.id, stage.event) for stage in inputs.stages]
    actual_events = [(stage.get('id'), stage.get('event')) for stage in raw_result.get('stages', [])]
    if actual_events != expected_events:
        raise ValueError('求解器返回的阶段数量、顺序或事件身份不完整；不生成最终包络')
    nodes = discrete['nodesM']
    locations = _evaluation_locations(raw_result['stages'], nodes)
    factor = inputs.analysis.design_effect_factor
    stages = []
    installed_references = {}
    ever_installed = set()
    for stage_input, raw in zip(inputs.stages, raw_result['stages']):
        available = raw['status'] == 'success'
        points = [_point(raw, nodes, depth, side, factor) for depth, side in locations] if available else []
        support_states = {support['id']: support for support in raw.get('supportStates') or []}
        installed_references.update({key: state['referenceDisplacementM'] for key, state in support_states.items()})
        ever_installed.update(stage_input.install_support_ids)
        supports = []
        for definition in inputs.supports:
            state = support_states.get(definition.id)
            status = ('not_calculated' if not available else 'active' if state else
                      'removed' if definition.id in ever_installed else 'not_installed')
            reaction = state['reactionKn'] if state else 0.0 if status == 'removed' else None
            reference = installed_references.get(definition.id)
            supports.append({'id': definition.id, 'depthM': definition.depth_m, 'kind': definition.kind,
                             'active': state is not None, 'status': status,
                             'engaged': state.get('engaged') if state else False,
                             'referenceDisplacementMm': reference * 1000 if reference is not None else None,
                             'displacementMm': state['displacementM'] * 1000 if state else None,
                             'reactionCharacteristicKn': reaction,
                             'reactionDesignKn': reaction * factor if reaction is not None else None,
                             'forceMeaning': '单桩分担的水平支点反力，非实体斜撑轴力'})
        stages.append({'id': raw['id'], 'name': raw['name'], 'event': raw['event'],
                       'status': raw['status'], 'failureReason': raw.get('failureReason'),
                       'excavationDepthM': stage_input.excavation_depth_m,
                       'waterInsideDepthM': stage_input.water_inside_depth_m,
                       'waterOutsideDepthM': stage_input.water_outside_depth_m,
                       'surchargeKpa': stage_input.surcharge_kpa,
                       'maxAbsDisplacementMm': max(abs(p['displacementMm']) for p in points) if available else None,
                       'maxAbsMomentKnM': max(abs(p['momentCharacteristicKnM']) for p in points) if available else None,
                       'maxAbsShearKn': max(abs(p['shearCharacteristicKn']) for p in points) if available else None,
                       'points': points, 'supports': supports, 'soilStates': raw.get('soilStates'),
                       'equilibrium': raw.get('equilibrium'), 'iterations': raw.get('iterations'),
                       'residualInfKn': raw.get('residualInfKn'), 'toeReaction': raw.get('toeReaction'),
                       'nodalDisplacementM': raw.get('nodalDisplacementM'),
                       'nodalRotation': raw.get('nodalRotation'), 'elementEndForces': raw.get('elementEndForces')})
        if inputs.method_extensions:
            physical_stage = discrete['stages'][len(stages)-1]
            for key in ('soilHistoryAction', 'soilHistoryBasis', 'activeSurfaceLoadIds'):
                stages[-1][key] = physical_stage[key]
    envelope = _envelope(stages, factor)
    source_files = ['models.py', 'preprocess.py', 'solver.py', 'service.py', 'reinforcement.py']
    if inputs.method_extensions:
        source_files += ['method_models.py', 'geotech_actions.py', 'engineering_models.py', 'engineering_rebar.py']
    code_hashes = {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                   for name in source_files}
    result = {'schemaVersion': 'pitguard.section-result/1', 'sectionId': inputs.section_id,
              'snapshotHash': snapshot.snapshot_hash, 'analysisHash': snapshot.analysis_hash,
              'algorithmVersion': algorithm_for(snapshot.payload()),
              'implementationHash': content_hash(code_hashes),
              'discreteInputHash': content_hash(discrete),
              'status': 'complete' if envelope['complete'] else 'failed',
              'engineeringStatus': 'review_required', 'formalDesignReady': False,
              'scope': {'method': inputs.analysis.soil_method,
                        'limitations': list(inputs.limitations) + [
                            '独立双侧弹簧近似；不预测连续土体应力重分布、土拱、摩擦或地表沉降。',
                            '不含整体稳定、抗隆起、渗透稳定、空间支撑传力及规范全项审查。',
                            '支撑反力为单桩分担的水平力，需另行进行围檩与实体支撑轴力转换。',
                        ]},
              'mesh': {'nodesM': nodes, 'nodeCount': len(nodes), 'discretisation': discrete['discretisation']},
              'stages': stages, 'envelope': envelope,
              'reinforcement': check_reinforcement(snapshot, envelope),
              'comparisons': {
                  'hand': {'status': 'not_run', 'reason': '尚未附加解析子问题结果验证'},
                  'independent': {'status': 'not_run', 'reason': '尚未运行独立OpenSees求解器'},
                  'mesh': {'status': 'not_run', 'reason': '尚未进行网格减半比较'},
                  'original': original_comparison_status(snapshot),
              },
              'trace': {'inertiaM4': pi * inputs.pile.diameter_m ** 4 / 64, 'eiKnM2': discrete['eiKnM2'],
                        'sourceCodeHashes': code_hashes,
                        'designEffectFactor': factor,
                        'designEffectFactorBasis': inputs.analysis.design_effect_factor_basis,
                        'forceBasis': 'single_pile',
                        'designDisplacementStatus': 'not_calculated',
                        'signConvention': 'z向下，u向坑内，θ=du/dz，M=EI·d²u/dz²，V=dM/dz；above/below保留节点剪力跳跃',
                        'pressureEquation': 'p=clip(p0+k·(s·u−x_pl_committed),pa,pp)',
                        'loadRule': '两侧土压力只由土弹簧给出；外荷载仅包含静水压力差，均布地面荷载进入坑外有效竖向应力',
                        'supportRule': 'R=K·(u−u_install)，零预力安装；刚性换撑为u=u_install精确约束'}}
    if inputs.method_extensions:
        from app.section.engineering_rebar import check_engineering_rebar
        result['engineeringChecks'] = check_engineering_rebar(snapshot.payload(), envelope,
                                    inputs.engineering_design.model_dump(mode='json') if inputs.engineering_design else None, stages)
        result['engineeringChecks']['binding'] = _engineering_binding(snapshot, stages, envelope)
        result['trace']['loadRule'] = '分层显式选择总/有效应力压力；分算只加一次外水；条带/矩形荷载竖向影响进入压力界限与K0初态；有限邻载沿墙位置为显式输入'
        result['trace']['soilHistoryRule'] = '逐阶段显式指定继承、塑性偏移清零或以此前收敛位移重设；不改变支撑安装参考位移'
        result['trace']['methodExtensions'] = inputs.method_extensions.model_dump(mode='json')
        result['trace']['referenceContractHash'] = inputs.reference_contract_hash
        result['trace']['referenceMethodEquivalence'] = 'unconfirmed_until_source_conditions_and_full_curves_are_verified'
    return seal_result(result)
