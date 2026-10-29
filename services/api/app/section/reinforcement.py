"""Circular pure-bending check with explicit, frozen material laws.

This is a numerical section-capacity check, not a claim of a full code design.
Axial load, shear, cracking, bond, anchorage, links and detailing are separate
checks and must not be inferred from the bending utilisation.
"""
from __future__ import annotations

from functools import lru_cache
from math import pi, radians

import numpy as np

from app.section.models import FrozenSectionSnapshot, SectionInput


@lru_cache(maxsize=8)
def _quadrature(count: int):
    nodes, weights = np.polynomial.legendre.leggauss(count)
    nodes.flags.writeable = False
    weights.flags.writeable = False
    return nodes, weights


def circular_capacity(model: SectionInput, *, total_steel_area_mm2: float | None = None,
                      sign: int = 1, integration_points: int = 384) -> dict:
    """N=0 at maximum concrete compressive strain, discrete uniform bars.

    Concrete: zero tension, parabola up to eps_peak, then constant fc.
    Steel: bilinear symmetric elastic-perfectly-plastic. All strengths are
    already design strengths. Displaced concrete is subtracted at each bar.
    The neutral axis is solved by force equilibrium; no rectangular beam
    effective-depth formula is reused for a circular pile.
    """
    rebar = model.reinforcement
    if rebar.bar_count is None or rebar.bar_diameter_mm is None:
        raise ValueError('无法在缺少给定钢筋数量与布置的情况下检算圆截面')
    if sign not in (-1, 1) or integration_points < 16:
        raise ValueError('Invalid bending sign or quadrature order')
    radius = model.pile.diameter_m * 500  # mm
    steel_radius = radius - rebar.bar_axis_cover_mm
    area = total_steel_area_mm2 if total_steel_area_mm2 is not None else rebar.bar_count * pi * rebar.bar_diameter_mm ** 2 / 4
    if area < 0 or area > 0.04 * pi * radius ** 2 + 1e-8:
        raise ValueError('数值配筋检索范围为0至截面面积的4%；不自动放宽范围')
    bar_area = area / rebar.bar_count
    angles = np.arange(rebar.bar_count) * 2 * pi / rebar.bar_count + radians(rebar.bar_start_angle_deg)
    bar_y = sign * steel_radius * np.cos(angles)
    xg, wg = _quadrature(integration_points)

    def concrete_stress(strain):
        ratio = np.clip(strain / rebar.concrete_peak_strain, 0, 1)
        return rebar.concrete_design_strength_mpa * (2 * ratio - ratio ** 2)

    def forces(neutral_axis_mm: float):
        compression_depth = min(2 * radius, neutral_axis_mm)
        plateau_end = min(compression_depth, max(0.0, neutral_axis_mm * (1 - rebar.concrete_peak_strain / rebar.concrete_ultimate_strain)))
        concrete_force = concrete_moment = 0.0
        for lo, hi in ((0.0, plateau_end), (plateau_end, compression_depth)):
            if hi <= lo:
                continue
            depths = (lo + hi) / 2 + (hi - lo) / 2 * xg
            y = radius - depths
            strip_areas = 2 * np.sqrt(np.maximum(0.0, radius ** 2 - y ** 2)) * wg * (hi - lo) / 2
            strains = rebar.concrete_ultimate_strain * (1 - depths / neutral_axis_mm)
            loads = concrete_stress(strains) * strip_areas
            concrete_force += float(np.sum(loads))
            concrete_moment += float(np.dot(loads, y))
        bar_strain = rebar.concrete_ultimate_strain * (1 - (radius - bar_y) / neutral_axis_mm)
        steel_stress = np.clip(rebar.steel_modulus_mpa * bar_strain,
                               -rebar.steel_design_strength_mpa, rebar.steel_design_strength_mpa)
        # Concrete integration covers the gross circle, so exclude the area
        # occupied by every bar instead of counting both materials there.
        net_bar_forces = (steel_stress - concrete_stress(bar_strain)) * bar_area
        axial_force = concrete_force + float(np.sum(net_bar_forces))
        moment = concrete_moment + float(np.dot(net_bar_forces, bar_y))
        return axial_force, moment, concrete_force, float(np.sum(net_bar_forces))

    lo, hi = radius * 1e-8, radius * 20
    f_lo, f_hi = forces(lo)[0], forces(hi)[0]
    if f_lo > 1e-8 or f_hi < -1e-8:
        raise ValueError('圆截面轴力平衡根未被包围，不能给出承载力')
    for _ in range(85):
        middle = (lo + hi) / 2
        if forces(middle)[0] > 0:
            hi = middle
        else:
            lo = middle
    neutral_axis = (lo + hi) / 2
    axial, moment, concrete, steel = forces(neutral_axis)
    return {'capacityKnM': abs(moment) / 1e6, 'neutralAxisMm': neutral_axis,
            'axialResidualKn': axial / 1000, 'concreteForceKn': concrete / 1000,
            'netSteelForceKn': steel / 1000, 'steelAreaMm2': area,
            'integrationPointsPerSegment': integration_points, 'bendingSign': sign}


def check_reinforcement(snapshot: FrozenSectionSnapshot, envelope: dict) -> dict:
    model = snapshot.validated_input()
    rebar = model.reinforcement
    common = {
        'method': 'circular_discrete_bars_strain_compatibility_v1',
        'status': 'review_required', 'formalDesignReady': False,
        'unverifiedChecks': ['axial_flexural_interaction', 'shear', 'crack_width', 'minimum_cover',
                             'bar_spacing', 'anchorage', 'lap', 'stirrup_detailing'],
        'limitations': ['仅检算N=0的圆形截面纯弯承载力和已给定最小纵筋率；不能替代完整规范设计。',
                        '混凝土采用显式抛物线平台受压曲线且忽略受拉，钢筋采用对称理想弹塑性。',
                        '需求面积保持已给定钢筋圆周位置，未进行钢筋选型或改变原实配；纵筋按全长布置输入。',
                        '箍筋仅原样列示，抗剪、裂缝、锚固与构造未验证，不能据此签发施工配筋图。'],
        'rows': [],
    }
    if not envelope.get('complete'):
        common['status'] = 'not_calculated'
        return common
    governing = envelope['governing']
    demand = abs(governing['momentAbs']['valueKnM']) * model.analysis.design_effect_factor
    shear = abs(governing['shearAbs']['valueKn']) * model.analysis.design_effect_factor
    gross = pi * (model.pile.diameter_m * 1000) ** 2 / 4
    minimum = gross * rebar.minimum_longitudinal_ratio
    row = {'id': 'FULL-PILE', 'fromDepthM': 0.0, 'toDepthM': model.pile.length_m,
           'governingStageId': governing['momentAbs']['stageId'],
           'shearGoverningStageId': governing['shearAbs']['stageId'],
           'momentDesignKnM': demand, 'shearDesignKn': shear,
           'requiredAreaMm2': None, 'providedAreaMm2': None, 'providedBarsLabel': '未提供',
           'minimumAreaMm2': minimum, 'capacityKnM': None, 'utilization': None,
           'status': 'missing_provided_reinforcement', 'notes': [],
           'stirrupLabel': ('未提供' if rebar.stirrup_diameter_mm is None else
                           f'Φ{rebar.stirrup_diameter_mm:g}@{rebar.stirrup_spacing_mm:g}（未作抗剪与构造验算）')}
    common['rows'] = [row]
    if rebar.bar_count is None:
        row['notes'].append('未提供实配钢筋，不由建议配筋补成给定值。')
        return common
    provided = rebar.bar_count * pi * rebar.bar_diameter_mm ** 2 / 4
    row.update({'providedAreaMm2': provided, 'providedBarsLabel': f'{rebar.bar_count}Φ{rebar.bar_diameter_mm:g}'})
    if provided > gross * 0.04:
        row.update(status='outside_method_scope')
        row['notes'].append('给定配筋率超过本数值检算定义的4%范围。')
        return common
    positive = circular_capacity(model, sign=1)
    negative = circular_capacity(model, sign=-1)
    capacity = min(positive['capacityKnM'], negative['capacityKnM'])
    row.update(capacityKnM=capacity, capacityPositiveKnM=positive['capacityKnM'],
               capacityNegativeKnM=negative['capacityKnM'], utilization=demand / capacity,
               status='strength_pass' if demand <= capacity and provided >= minimum else 'strength_fail')
    common['capacityTrace'] = {'positive': positive, 'negative': negative}

    def limiting_capacity(area):
        return min(circular_capacity(model, total_steel_area_mm2=area, sign=direction)['capacityKnM'] for direction in (-1, 1))

    ceiling = gross * 0.04
    if limiting_capacity(ceiling) < demand:
        row['notes'].append('所需纵筋面积超过当前4%检索范围，需求面积保持未知；应调整方案或扩大经验证的设计方法。')
    else:
        lo, hi = 0.0, ceiling
        for _ in range(42):
            mid = (lo + hi) / 2
            if limiting_capacity(mid) >= demand:
                hi = mid
            else:
                lo = mid
        row['strengthRequiredAreaMm2'] = hi
        row['requiredAreaMm2'] = max(hi, minimum)
    row['notes'].append('内力效应系数只施加一次；控制弯矩与剪力允许来自不同阶段，各自保留身份。')
    return common
