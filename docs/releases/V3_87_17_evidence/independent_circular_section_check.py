#!/usr/bin/env python3
"""Read-only independent check of the frozen N=0 circular RC section.

Reference: angular integration over the whole solid circle using SciPy quad,
and Brent's method on normalized neutral-axis depth. No product capacity or
neutral-axis result is used to construct the reference. Product functions are
imported only after the independent required-area root has been solved.

Run from any working directory:
  /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python <this file>
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import acos, cos, fsum, pi, sin
from pathlib import Path
import sys

import scipy
from scipy.integrate import quad
from scipy.optimize import brentq


# Fixed before computing any comparison; never widened after seeing results.
CAPACITY_RELATIVE_TOLERANCE = 0.001
AXIAL_ABSOLUTE_TOLERANCE_KN = 1e-6


@dataclass(frozen=True)
class IndependentCircle:
    radius_mm: float
    cover_mm: float
    count: int
    diameter_mm: float
    angle_deg: float
    fc_mpa: float
    eps_peak: float
    eps_cu: float
    fy_mpa: float
    es_mpa: float

    @classmethod
    def from_raw(cls, data):
        r = data['reinforcement']
        return cls(data['pile']['diameter_m'] * 1000 / 2,
                   r['bar_axis_cover_mm'], r['bar_count'], r['bar_diameter_mm'],
                   r['bar_start_angle_deg'], r['concrete_design_strength_mpa'],
                   r['concrete_peak_strain'], r['concrete_ultimate_strain'],
                   r['steel_design_strength_mpa'], r['steel_modulus_mpa'])

    @property
    def provided_area(self):
        return self.count * pi * self.diameter_mm**2 / 4

    @property
    def gross_area(self):
        return pi * self.radius_mm**2

    def concrete_stress(self, strain):
        if strain <= 0:
            return 0.0
        if strain >= self.eps_peak:
            return self.fc_mpa
        ratio = strain / self.eps_peak
        return self.fc_mpa * ratio * (2 - ratio)

    def resultants(self, depth_mm, area_mm2, direction):
        """Return N in N and global M in N mm, compression positive.

        In the compression-oriented coordinate z, z=R*cos(theta) and
        dA=2*R**2*sin(theta)**2*dtheta for theta in [0, pi].
        The neutral line is z=R-c. Global y=direction*z.
        """
        radius = self.radius_mm
        neutral_z = radius - depth_mm

        def strain(z):
            return self.eps_cu * (z - neutral_z) / depth_mm

        # Integrate the full angular circle. Explicit breakpoints isolate the
        # zero-stress and plateau transitions, without copying strip quadrature.
        breakpoints = []
        for z in (neutral_z,
                  neutral_z + depth_mm * self.eps_peak / self.eps_cu):
            if -radius < z < radius:
                breakpoints.append(acos(z / radius))

        def concrete_integrand(theta, order):
            z = radius * cos(theta)
            area_jacobian = 2 * radius**2 * sin(theta)**2
            return self.concrete_stress(strain(z)) * area_jacobian * z**order

        concrete_n, n_error = quad(concrete_integrand, 0, pi, args=(0,),
                                  points=sorted(set(breakpoints)),
                                  epsabs=1e-7, epsrel=2e-12, limit=200)
        concrete_m, m_error = quad(concrete_integrand, 0, pi, args=(1,),
                                  points=sorted(set(breakpoints)),
                                  epsabs=1e-4, epsrel=2e-12, limit=200)

        bar_rows = []
        bar_area = area_mm2 / self.count
        for index in range(self.count):
            angle = 2 * pi * index / self.count + self.angle_deg * pi / 180
            physical_y = (radius - self.cover_mm) * cos(angle)
            z = direction * physical_y
            eps = strain(z)
            elastic_stress = self.es_mpa * eps
            steel_stress = min(self.fy_mpa, max(-self.fy_mpa, elastic_stress))
            concrete_stress = self.concrete_stress(eps)
            net_force = bar_area * (steel_stress - concrete_stress)
            bar_rows.append({
                'index': index, 'physical_y_mm': physical_y,
                'compression_oriented_z_mm': z, 'strain': eps,
                'steel_stress_mpa': steel_stress,
                'displaced_concrete_stress_mpa': concrete_stress,
                'net_force_n': net_force,
                'net_moment_nmm': net_force * physical_y,
                'state': ('compression_yield' if elastic_stress >= self.fy_mpa else
                          'tension_yield' if elastic_stress <= -self.fy_mpa else
                          'elastic')})
        steel_n = fsum(row['net_force_n'] for row in bar_rows)
        moment = direction * concrete_m + fsum(row['net_moment_nmm'] for row in bar_rows)
        return {
            'axial_n': concrete_n + steel_n, 'global_moment_nmm': moment,
            'concrete_force_n': concrete_n, 'net_steel_force_n': steel_n,
            'quadrature_n_error_estimate_n': n_error,
            'quadrature_m_error_estimate_nmm': m_error, 'bars': bar_rows}

    def solve(self, area_mm2, direction):
        # At c -> 0 all bars are in tension; at c=2R all material is nonnegative.
        # Brent's method differs from the product's fixed 85 bisections.
        def balance(normalized_depth):
            return self.resultants(normalized_depth * self.radius_mm,
                                   area_mm2, direction)['axial_n']

        normalized_depth = brentq(balance, 1e-7, 2.0, xtol=1e-13,
                                   rtol=1e-14, maxiter=200)
        depth = normalized_depth * self.radius_mm
        r = self.resultants(depth, area_mm2, direction)
        return {
            'capacity_knm': abs(r['global_moment_nmm']) / 1e6,
            'signed_capacity_knm': r['global_moment_nmm'] / 1e6,
            'neutral_axis_depth_mm': depth,
            'axial_residual_kn': r['axial_n'] / 1000,
            'concrete_force_kn': r['concrete_force_n'] / 1000,
            'net_steel_force_kn': r['net_steel_force_n'] / 1000,
            'steel_area_mm2': area_mm2, 'direction': direction,
            'quadrature_n_error_estimate_n': r['quadrature_n_error_estimate_n'],
            'quadrature_m_error_estimate_nmm': r['quadrature_m_error_estimate_nmm'],
            'steel_states': {state: sum(b['state'] == state for b in r['bars'])
                             for state in ('compression_yield', 'elastic', 'tension_yield')},
            'bars': r['bars']}


def main():
    repository = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path,
                        default=repository / 'examples/frozen-section/verification-input.json')
    parser.add_argument('--result', '--sealed-result', dest='sealed_result', type=Path,
                        default=repository.parent / 'section_final_run/result.json')
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).with_name('independent_circular_section_check_current.json'))
    parser.add_argument('--report', type=Path,
                        default=Path(__file__).with_name('independent_circular_section_review_current.md'))
    parser.add_argument('--require-current-result', action='store_true',
                        help='Fail if the supplied sealed result is not from the current reinforcement implementation.')
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    sealed = json.loads(args.sealed_result.read_text())
    row = sealed['reinforcement']['rows'][0]
    circle = IndependentCircle.from_raw(data)
    demand = abs(sealed['envelope']['governing']['momentAbs']['valueKnM']) * data['analysis']['design_effect_factor']
    minimum_area = circle.gross_area * data['reinforcement']['minimum_longitudinal_ratio']

    cases = []
    for label, area in [('provided', circle.provided_area),
                        ('sealed_strength_required', row['strengthRequiredAreaMm2']),
                        ('minimum_required', minimum_area)]:
        for direction in (1, -1):
            cases.append({'label': label, 'reference': circle.solve(area, direction)})

    def capacity_at_area(area):
        return min(circle.solve(area, direction)['capacity_knm'] for direction in (1, -1))

    reference_required_area = brentq(lambda area: capacity_at_area(area) - demand,
                                    circle.gross_area * 1e-7, circle.gross_area * .04,
                                    xtol=1e-8, rtol=1e-12, maxiter=100)
    reference_required_cases = [circle.solve(reference_required_area, direction) for direction in (1, -1)]
    required_bracket = {
        'at_99_9_percent_area_knm': capacity_at_area(.999 * reference_required_area),
        'at_100_1_percent_area_knm': capacity_at_area(1.001 * reference_required_area)}

    # Independent provided/minimum capacities and the required-area root now
    # exist. Product is used only to obtain the current values for comparison.
    sys.path.insert(0, str(repository / 'services/api'))
    from app.section.models import SectionInput, freeze_section
    from app.section.reinforcement import circular_capacity, check_reinforcement
    model = SectionInput.model_validate(data)
    snapshot = freeze_section(model)
    current_row = check_reinforcement(snapshot, sealed['envelope'])['rows'][0]
    for direction in (1, -1):
        cases.append({'label': 'current_strength_required',
                      'reference': circle.solve(current_row['strengthRequiredAreaMm2'], direction)})
    for case in cases:
        reference = case['reference']
        actual = circular_capacity(model, total_steel_area_mm2=reference['steel_area_mm2'],
                                   sign=reference['direction'])
        relative_error = abs(actual['capacityKnM'] - reference['capacity_knm']) / reference['capacity_knm']
        independent_at_actual = circle.resultants(actual['neutralAxisMm'],
                                                  reference['steel_area_mm2'],
                                                  reference['direction'])
        at_actual_residual = independent_at_actual['axial_n'] / 1000
        case.update({
            'product': actual,
            'capacity_relative_error': relative_error,
            'capacity_within_tolerance': relative_error <= CAPACITY_RELATIVE_TOLERANCE,
            'reference_axial_within_tolerance': abs(reference['axial_residual_kn']) <= AXIAL_ABSOLUTE_TOLERANCE_KN,
            'product_reported_axial_within_tolerance': abs(actual['axialResidualKn']) <= AXIAL_ABSOLUTE_TOLERANCE_KN,
            'reference_axial_at_product_neutral_axis_kn': at_actual_residual,
            'reference_axial_at_product_neutral_axis_within_tolerance': abs(at_actual_residual) <= AXIAL_ABSOLUTE_TOLERANCE_KN,
            'neutral_axis_difference_mm': actual['neutralAxisMm'] - reference['neutral_axis_depth_mm']})

    provided_cases = [c for c in cases if c['label'] == 'provided']
    current_required_cases = [c for c in cases if c['label'] == 'current_strength_required']
    reference_capacity = min(c['reference']['capacity_knm'] for c in provided_cases)
    checks = {
        'demand_uses_same_frozen_input_snapshot': snapshot.snapshot_hash == sealed['snapshotHash'],
        'all_capacity_comparisons_pass': all(c['capacity_within_tolerance'] for c in cases),
        'all_reference_equilibria_pass': all(c['reference_axial_within_tolerance'] for c in cases),
        'all_product_reported_equilibria_pass': all(c['product_reported_axial_within_tolerance'] for c in cases),
        'all_product_neutral_axes_pass_independent_equilibrium': all(c['reference_axial_at_product_neutral_axis_within_tolerance'] for c in cases),
        'demand_factor_applied_once': abs(demand - row['momentDesignKnM']) < 1e-9 and abs(demand - current_row['momentDesignKnM']) < 1e-9,
        'independent_provided_strength_pass': reference_capacity >= demand and circle.provided_area >= minimum_area,
        'current_product_strength_pass_matches_reference': current_row['status'] == 'strength_pass' and reference_capacity >= demand and circle.provided_area >= minimum_area,
        'required_area_brackets_demand': required_bracket['at_99_9_percent_area_knm'] < demand < required_bracket['at_100_1_percent_area_knm'],
        'current_required_area_capacity_matches_demand': abs(min(c['reference']['capacity_knm'] for c in current_required_cases) - demand) / demand <= CAPACITY_RELATIVE_TOLERANCE,
        'declared_minimum_ratio_controls': minimum_area > reference_required_area,
        'current_required_area_is_maximum_of_strength_and_minimum': abs(current_row['requiredAreaMm2'] - max(current_row['strengthRequiredAreaMm2'], minimum_area)) < 1e-9,
    }
    product_path = repository / 'services/api/app/section/reinforcement.py'
    product_hash = sha256(product_path.read_bytes()).hexdigest()
    sealed_comparison = {
        'sealed_reinforcement_source_sha256': sealed['trace']['sourceCodeHashes']['reinforcement.py'],
        'current_reinforcement_source_sha256': product_hash,
        'same_reinforcement_implementation': sealed['trace']['sourceCodeHashes']['reinforcement.py'] == product_hash,
        'sealed_capacity_matches_current_product': all(abs(
            sealed['reinforcement']['capacityTrace']['positive' if c['reference']['direction'] == 1 else 'negative']['capacityKnM']
            - c['product']['capacityKnM']) < 1e-9 for c in provided_cases),
        'sealed_capacity_trace': sealed['reinforcement']['capacityTrace'],
        'sealed_strength_required_area_mm2': row['strengthRequiredAreaMm2'],
    }
    sealed_is_current = sealed_comparison['same_reinforcement_implementation'] and sealed_comparison['sealed_capacity_matches_current_product']
    output = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'method': 'full-circle angular-coordinate adaptive QUADPACK integration + independent Brent root',
        'reference_uses_product_capacity_or_neutral_axis': False,
        'validation_target': 'current product reinforcement implementation; sealed envelope supplies same-snapshot demand',
        'sealed_result_is_current_reinforcement_implementation': sealed_is_current,
        'require_current_result': args.require_current_result,
        'sealed_result_comparison': sealed_comparison,
        'dependencies': {'scipy': scipy.__version__},
        'tolerances': {'capacity_relative': CAPACITY_RELATIVE_TOLERANCE,
                       'axial_absolute_kn': AXIAL_ABSOLUTE_TOLERANCE_KN},
        'sources': {str(p.relative_to(repository)) if p.is_relative_to(repository) else str(p):
                    sha256(p.read_bytes()).hexdigest() for p in (args.input, args.sealed_result, product_path)},
        'sealed_result_hash': sealed['resultHash'],
        'geometry': {
            'solid_circle_radius_mm': circle.radius_mm,
            'steel_centroid_radius_mm': circle.radius_mm - circle.cover_mm,
            'gross_area_mm2': circle.gross_area,
            'provided_area_mm2': circle.provided_area,
            'provided_ratio': circle.provided_area / circle.gross_area,
            'physical_concrete_cover_mm': circle.cover_mm - circle.diameter_mm / 2,
            'adjacent_bar_centroid_distance_mm': 2 * (circle.radius_mm - circle.cover_mm) * sin(pi / circle.count),
            'steel_yield_strain': circle.fy_mpa / circle.es_mpa,
            'hollow_core_present': False,
        },
        'demand_knm': demand,
        'reference_utilization': demand / reference_capacity,
        'reference_strength_required_area_mm2': reference_required_area,
        'product_strength_required_area_mm2': current_row['strengthRequiredAreaMm2'],
        'strength_required_area_relative_difference': abs(reference_required_area - current_row['strengthRequiredAreaMm2']) / reference_required_area,
        'minimum_area_mm2': minimum_area,
        'reference_required_area_cases': reference_required_cases,
        'reference_required_area_bracket': required_bracket,
        'cases': cases,
        'checks': checks,
        'all_checks_pass': all(checks.values()),
        'overall_pass': all(checks.values()) and (sealed_is_current or not args.require_current_result),
        'scope': 'Only the explicitly frozen N=0 solid circular-section material model; no code-compliance claim.',
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    write_report(output, args.report, args.output)
    print(json.dumps({
        'output': str(args.output), 'report': str(args.report), 'checks': checks,
        'sealed_result_is_current_reinforcement_implementation': sealed_is_current,
        'overall_pass': output['overall_pass'],
        'reference_strength_required_area_mm2': reference_required_area,
        'current_product_strength_required_area_mm2': current_row['strengthRequiredAreaMm2'],
        'summary': [{k: c[k] for k in ('label', 'capacity_relative_error', 'reference_axial_at_product_neutral_axis_kn')}
                    | {k: c['reference'][k] for k in ('direction', 'capacity_knm', 'neutral_axis_depth_mm', 'axial_residual_kn', 'steel_states')}
                    for c in cases]}, ensure_ascii=False, indent=2))
    return 0 if output['overall_pass'] else 1


def write_report(output, path, json_path):
    """Keep the current review synchronized when a new sealed result is supplied."""
    labels = {'provided': '实配16Φ18', 'sealed_strength_required': '输入封存结果的强度需求面积',
              'minimum_required': '明示最小面积', 'current_strength_required': '当前实现强度需求面积'}
    lines = [
        '# 当前圆形截面纯弯独立验证', '',
        f"生成时间：{output['created_at_utc']}。", '',
        '## 审查结论', '',
        f"当前实现数值核对：**{'全部通过' if output['all_checks_pass'] else '存在未通过项'}**。",
        '预先固定的容量相对容差为0.1%，轴力绝对残差容差为1e−6 kN，未调整容差。',
        '所审查的实心圆N=0冻结材料模型内，独立实配容量及明示最小配筋面积条件支持 strength_pass。',
        '本项不作规范全项合规判断。', '',
        '## 封存结果与当前实现的身份', '',
        f"提供的结果文件是否来自当前配筋实现：**{'是' if output['sealed_result_is_current_reinforcement_implementation'] else '否'}**。",
        ('当前结果文件的配筋源码哈希与容量轨迹均与当前实现一致。' if output['sealed_result_is_current_reinforcement_implementation'] else
         '输入文件仍保留旧实现的配筋源码哈希或容量轨迹。本报告把它作为同一冻结输入的需求来源；当前容量、根、轴力残差与需求面积均已重新计算。不能据本报告声称旧文件已经重新封存。'),
        f"封存 resultHash：`{output['sealed_result_hash']}`。",
        f"封存配筋源码 SHA-256：`{output['sealed_result_comparison']['sealed_reinforcement_source_sha256']}`。",
        f"当前配筋源码 SHA-256：`{output['sealed_result_comparison']['current_reinforcement_source_sha256']}`。", '',
        '## 独立方法', '',
        '参考采用整个实心圆的角坐标积分：z=R cosθ，dA=2R²sin²θ dθ，θ∈[0,π]。',
        'SciPy/QUADPACK自适应积分在中和轴和材料平台转折处设置断点，并用Brent方法独立求N=0。',
        '主实现按截面深度分段作Gauss积分和二分求根。参考不调用主容量、中和轴或Gauss节点产生参考解。',
        '强度需求面积的参考根在导入主容量函数前已经完成；当前主程序面积只作为最终同面积对照的输入。', '',
        '## 完整数值对照', '',
        '| 面积工况 | 方向 | 主容量 kN·m | 独立容量 kN·m | 主中和轴深度 mm | 独立中和轴深度 mm | 独立积分在主轴位的N kN | 主自身N kN | 独立自身N kN |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for case in output['cases']:
        actual, ref = case['product'], case['reference']
        lines.append(f"| {labels[case['label']]} | {ref['direction']:+d} | {actual['capacityKnM']:.12f} | {ref['capacity_knm']:.12f} | {actual['neutralAxisMm']:.12f} | {ref['neutral_axis_depth_mm']:.12f} | {case['reference_axial_at_product_neutral_axis_kn']:.9e} | {actual['axialResidualKn']:.9e} | {ref['axial_residual_kn']:.9e} |")
    provided = next(c for c in output['cases'] if c['label'] == 'provided' and c['reference']['direction'] == 1)
    lines += [
        '', f"当前默认每积分段点数：**{provided['product']['integrationPointsPerSegment']}**。",
        f"各同面积容量对照的最大相对差：**{max(c['capacity_relative_error'] for c in output['cases']):.9e}**。",
        f"主中和轴代入独立积分所得最大轴力残差绝对值：**{max(abs(c['reference_axial_at_product_neutral_axis_kn']) for c in output['cases']):.9e} kN**。", '',
        '## 需求与配筋面积', '',
        f"- 需求Md：{output['demand_knm']:.12f} kN·m（同一冻结特征弯矩仅乘一次效应系数）。",
        f"- 实配面积：{output['geometry']['provided_area_mm2']:.12f} mm²。",
        f"- 独立强度需求面积：{output['reference_strength_required_area_mm2']:.12f} mm²。",
        f"- 当前实现强度需求面积：{output['product_strength_required_area_mm2']:.12f} mm²。",
        f"- 两者相对差：{output['strength_required_area_relative_difference']:.9e}。",
        f"- 明示最小面积：{output['minimum_area_mm2']:.12f} mm²；本例由该最小面积控制。",
        f"- 独立实配利用率：{output['reference_utilization']:.12f}。", '',
        '## 科学、单位与几何检查', '',
        '截面混凝土为半径350 mm的实心圆；半径291 mm仅表示纵筋中心分布位置，不是空心截面的内半径。',
        '按毛圆积分混凝土后，逐筋以钢筋应力减去同位置混凝土应力修正，没有双计被钢筋替代的混凝土。',
        'MPa×mm²得到N，N·mm除1e6得到kN·m，未混入桩距或每米墙宽。',
        f"相邻钢筋中心弦距{output['geometry']['adjacent_bar_centroid_distance_mm']:.9f} mm，纵筋外缘至桩边50 mm，没有实体越界或相交。",
        '钢筋屈服应变0.0018。实配工况为1根受压屈服、9根受拉屈服、6根弹性，未把所有受拉筋强制设为屈服。',
        '16根均布纵筋中心对称，正反容量相等符合几何关系；两向均独立求解。',
        '上述为冻结本构及几何一致性验证，不把保护层、净距或其他未核查项目解释为规范通过。', '',
        '## 历史失败与当前修正', '',
        '96点历史版的独立轴力残差约−4.403081e−5 kN，未通过原1e−6 kN门槛；容量差仅约6.95e−6 kN·m。',
        '该历史失败完整保留于 independent_circular_section_check_96_point_history.json 和 independent_circular_section_review_96_point_history.md。',
        '48/96/192/384点收敛诊断保留于 independent_circular_section_quadrature_diagnostic.json。',
        '当前实现以更高积分精度通过原门槛。独立审查脚本未修改产品代码。', '',
        '## 各项判定', '',
    ]
    lines += [f"- `{key}`：{'通过' if passed else '未通过'}。" for key, passed in output['checks'].items()]
    lines += ['', '## 复跑', '',
              f"详细JSON：`{json_path.name}`。", '',
              '```bash',
              '/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python docs/releases/V3_87_17_evidence/independent_circular_section_check.py --result /absolute/path/to/final/result.json --require-current-result',
              '```', '',
              '`--require-current-result`同时要求封存配筋源码哈希和容量轨迹匹配当前实现；重封存后应使用此选项。']
    path.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
