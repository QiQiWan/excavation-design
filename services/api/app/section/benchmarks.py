"""Fixed acceptance criteria and independent comparison, never result fitting."""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from math import atan, fsum, pi, radians, sqrt, tan
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

from app.section.models import FrozenSectionSnapshot, content_hash, freeze_section
from app.section.preprocess import build_solver_model
from app.section.service import analyze_section, seal_result, validate_result
from app.section.solver import solve_stages


RELATIVE_TOLERANCE = 0.001
MESH_TOLERANCE = 0.02


def _metric(left, right, absolute_tolerance: float, relative_tolerance=RELATIVE_TOLERANCE) -> dict:
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if a.shape != b.shape or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return {'passed': False, 'reason': 'Shape mismatch or non-finite values',
                'leftShape': list(a.shape), 'rightShape': list(b.shape)}
    difference = abs(a - b)
    permitted = absolute_tolerance + relative_tolerance * np.maximum(abs(a), abs(b))
    return {'passed': bool(np.all(difference <= permitted)),
            'maxAbsoluteDifference': float(np.max(difference)) if a.size else 0.0,
            'maxNormalizedError': float(np.max(difference / permitted)) if a.size else 0.0,
            'count': int(a.size), 'absoluteTolerance': absolute_tolerance,
            'relativeTolerance': relative_tolerance}


def run_hand_checks() -> dict:
    length, ei, force, stiffness = 5.0, 100000.0, 10.0, 2000.0
    nodes = list(map(float, range(6)))
    def event(identity, magnitude, installs=(), removes=()):
        return {'id': identity, 'name': identity, 'event': identity,
                'externalForcesKn': [magnitude] + [0.0] * 5, 'soilSprings': [],
                'installSupports': list(installs), 'removeSupports': list(removes)}
    model = {'nodesM': nodes, 'eiKnM2': ei, 'toeBoundary': 'fixed', 'stages': [
        event('load', force),
        event('install', force, [{'id': 'S', 'node': 0, 'kind': 'elastic',
                                 'stiffnessKnPerM': stiffness, 'compressionOnly': False, 'preloadKn': 0.0}]),
        event('add_load', 2 * force), event('remove', 2 * force, removes=['S']),
    ]}
    result = solve_stages(model)
    if result['status'] != 'success':
        return {'status': 'failed', 'reason': 'Analytic beam/support case failed', 'checks': []}
    stages = result['stages']
    compliance = length ** 3 / (3 * ei)
    expected_increment = force * compliance / (1 + stiffness * compliance)
    definitions = [
        ('cantilever_tip_displacement', '悬臂梁端位移', 'F L³/(3 EI)', 'm', force * compliance, stages[0]['nodalDisplacementM'][0], 1e-8),
        ('cantilever_fixed_moment', '悬臂梁固定端弯矩', 'F L', 'kN·m', force * length, stages[0]['elementEndForces'][-1][3], 1e-4),
        ('cantilever_shear', '悬臂梁剪力', 'F', 'kN', force, stages[0]['elementEndForces'][0][0], 1e-4),
        ('zero_force_installation', '既有位移处安装反力', 'K(u_install−u_install)=0', 'kN', 0.0, stages[1]['supportStates'][0]['reactionKn'], 1e-4),
        ('staged_support_displacement', '安装后增荷位移', 'u_install + ΔF C/(1+K C)', 'm', force * compliance + expected_increment, stages[2]['nodalDisplacementM'][0], 1e-8),
        ('staged_support_reaction', '安装后增荷反力', 'K ΔF C/(1+K C)', 'kN', stiffness * expected_increment, stages[2]['supportStates'][0]['reactionKn'], 1e-4),
        ('removal_redistribution', '拆撑后位移', '2 F L³/(3 EI)', 'm', 2 * force * compliance, stages[3]['nodalDisplacementM'][0], 1e-8),
    ]
    checks = []
    for identity, name, formula, unit, expected, actual, tolerance in definitions:
        checks.append({'id': identity, 'name': name, 'formula': formula, 'unit': unit,
                       'expected': expected, 'actual': actual,
                       **_metric([expected], [actual], tolerance)})
    return {'status': 'passed' if all(check['passed'] for check in checks) else 'failed',
            'scope': '解析梁与支撑安装拆除子问题；不代表整套基坑工程模型已获手算证明',
            'inputHash': content_hash(model),
            'inputs': {'lengthM': length, 'eiKnM2': ei, 'headForceKn': force, 'supportStiffnessKnPerM': stiffness},
            'toleranceRelative': RELATIVE_TOLERANCE, 'checks': checks}


def same_snapshot_hand_checks(snapshot: FrozenSectionSnapshot, result: dict) -> dict:
    """Worked hand-auditable quantities of this actual frozen section.

    The nonlinear beam history has no claimed closed-form hand solution.
    These independently summed pressures/actions, equilibrium equations and
    zero-force installation checks complement the separate analytic fixtures.
    """
    validate_result(snapshot, result)
    if result['status'] != 'complete':
        return {'status': 'not_calculated', 'snapshotHash': snapshot.snapshot_hash,
                'reason': '实际剖面阶段未全部完成，不能声称同快照手工核对通过', 'checks': []}
    data = snapshot.payload()
    extensions = data.get('method_extensions')
    modes = {r['layer_id']: r['mode'] for r in extensions['layer_stress_modes']} if extensions else {}
    physical = build_solver_model(snapshot)
    by_id = {s['id']: s for s in physical['stages']}
    control = result['envelope']['governing']['momentAbs']['stageId']
    stage = next(s for s in result['stages'] if s['id'] == control)
    model_stage = by_id[control]
    length, spacing = data['pile']['length_m'], data['pile']['spacing_m']
    gamma_w = data['analysis']['water_unit_weight_kn_m3']
    water_out, water_in = stage['waterOutsideDepthM'], stage['waterInsideDepthM']
    def water_triangle(water):
        height = max(0.0, length - water)
        force = gamma_w * spacing * height ** 2 / 2
        return force, force * (water + 2 * height / 3)
    out_force, out_moment = water_triangle(water_out)
    in_force, in_moment = water_triangle(water_in)
    if extensions:
        # Independently integrate only stations whose current layer uses
        # separated water. Combined total stress already contains that water.
        def separated_water(water, surface):
            force, moment = 0.0, 0.0
            for layer in data['soil_layers']:
                if modes[layer['id']] != 'effective_separated':
                    continue
                a, b = max(surface, water, layer['top_m']), min(length, layer['bottom_m'])
                if b > a:
                    force += gamma_w * spacing * ((b*b-a*a)/2-water*(b-a))
                    moment += gamma_w * spacing * ((b**3-a**3)/3-water*(b*b-a*a)/2)
            return force, moment
        out_force, out_moment = separated_water(water_out, 0.0)
        in_force, in_moment = separated_water(water_in, stage['excavationDepthM'])
    checks = []
    def check(identity, name, unit, formula, expected, actual, tolerance=1e-6):
        checks.append({'id': identity, 'name': name, 'unit': unit, 'formula': formula,
                       'expected': expected, 'actual': actual, **_metric([expected], [actual], tolerance)})
    inertia = pi * data['pile']['diameter_m'] ** 4 / 64
    check('same_snapshot_ei', '本剖面单桩抗弯刚度', 'kN·m²', 'E·η·πD⁴/64',
          data['pile']['elastic_modulus_kpa'] * data['pile']['inertia_factor'] * inertia, physical['eiKnM2'])
    check('same_snapshot_water_force', f'{control} 静水压力差合力', 'kN',
          '分算土层区间Σ∫s·γw(z−zw)dz；合算层不再添加外水' if extensions else 'γw·s·[(L−zw,out)₊²−(L−zw,in)₊²]/2',
          out_force - in_force, fsum(model_stage['externalForcesKn']))
    check('same_snapshot_water_moment', f'{control} 水力对桩顶力矩', 'kN·m',
          '分算土层区间Σ∫s·γw(z−zw)·z dz' if extensions else 'Σ Fwater·[zw+2(L−zw)/3]', out_moment - in_moment,
          fsum(z * force for z, force in zip(physical['nodesM'], model_stage['externalForcesKn'])))

    worked_rows = []
    for side, surface, water, surcharge in (
        ('outside', 0.0, water_out, stage['surchargeKpa']),
        ('inside', stage['excavationDepthM'], water_in, 0.0),
    ):
        candidates = [s for s in model_stage['soilSprings'] if s['side'] == side]
        station = min(candidates, key=lambda spring: abs(spring['depthM'] - (stage['excavationDepthM'] + 1)))
        depth = station['depthM']
        bounds = {surface, depth}
        for boundary in [water] + [value for layer in data['soil_layers'] for value in (layer['top_m'], layer['bottom_m'])]:
            if surface < boundary < depth:
                bounds.add(boundary)
        ordered = sorted(bounds)
        segments = []
        for lo, hi in zip(ordered, ordered[1:]):
            midpoint = (lo + hi) / 2
            layer = next(layer for layer in data['soil_layers'] if layer['top_m'] <= midpoint < layer['bottom_m'])
            effective_weight = (layer['gamma_sat_kn_m3'] - gamma_w if midpoint >= water else layer['gamma_unsat_kn_m3'])
            segments.append({'fromM': lo, 'toM': hi, 'layerId': layer['id'],
                             'effectiveUnitWeightKnM3': effective_weight, 'contributionKpa': effective_weight * (hi - lo)})
        effective = fsum(row['contributionKpa'] for row in segments)
        finite_surcharge = 0.0
        if extensions and side == 'outside':
            active = next(r['active_load_ids'] for r in extensions['stage_options'] if r['stage_id'] == control)
            finite_surcharge = fsum(_independent_surface_integral(load, depth) for load in extensions['surface_loads'] if load['id'] in active)
        pore = gamma_w * max(0.0, depth-water)
        pressure_basis = 'total' if modes.get(station['layerId']) == 'total_combined' else 'effective'
        sigma = effective + (pore if pressure_basis == 'total' else 0) + surcharge + finite_surcharge
        layer = next(layer for layer in data['soil_layers'] if layer['id'] == station['layerId'])
        ka = tan(pi / 4 - radians(layer['phi_deg']) / 2) ** 2
        expected = {'sigmaKpa': sigma, 'p0Kpa': layer['k0'] * sigma,
                    'activeKpa': max(0.0, ka * sigma - 2 * layer['cohesion_kpa'] * sqrt(ka)),
                    'passiveKpa': sigma / ka + 2 * layer['cohesion_kpa'] / sqrt(ka)}
        actual_sigma = (station['totalVerticalKpa'] if pressure_basis == 'total' else station['effectiveVerticalKpa']) + station['surchargeVerticalKpa'] if extensions else station['effectiveVerticalKpa']
        actual = {'sigmaKpa': actual_sigma, 'p0Kpa': station['p0Kpa'],
                  'activeKpa': station['activeKpa'], 'passiveKpa': station['passiveKpa']}
        passed = _metric(list(expected.values()), list(actual.values()), 1e-6)
        worked_rows.append({'side': side, 'depthM': depth, 'materialStationId': station['id'],
                            'verticalStressSegments': segments, 'surchargeKpa': surcharge,
                            'phiDeg': layer['phi_deg'], 'cohesionKpa': layer['cohesion_kpa'],
                            'ka': ka, 'kp': 1 / ka, 'k0': layer['k0'], 'expected': expected, 'actual': actual, **passed})
        if extensions:
            worked_rows[-1].update(pressureBasis=pressure_basis, finiteSurchargeKpa=finite_surcharge,
                                  porePressureKpa=pore, totalVerticalKpa=effective+pore,
                                  finiteLoadCheckMethod='independent quadrature of Boussinesq point kernel')
        check(f'same_snapshot_{side}_effective_stress', f'{control} {"坑外" if side == "outside" else "坑内"}压力对应竖向应力',
              'kPa', 'σ′v 或 σv + 均布q + 有限邻载独立积分' if extensions else 'Σ γ′i·Δzi + q', sigma, actual_sigma)

    equilibrium = []
    installed_forces = []
    stage_inputs = {s['id']: s for s in data['stages']}
    for item in result['stages']:
        external = by_id[item['id']]['externalForcesKn']
        applied = list(external)
        for spring in item['soilStates']:
            applied[spring['node']] += spring['forceOnWallKn']
        for support in item['supports']:
            if support['active']:
                node = physical['nodesM'].index(round(support['depthM'], 10))
                applied[node] -= support['reactionCharacteristicKn']
            if support['id'] in stage_inputs[item['id']]['install_support_ids']:
                installed_forces.append(support['reactionCharacteristicKn'])
        toe = item['toeReaction']
        force_residual = fsum(applied) + toe['forceKn']
        moment_residual = fsum(z * force for z, force in zip(physical['nodesM'], applied)) + length * toe['forceKn'] + toe['momentKnM']
        force_scale = max(1.0, fsum(abs(f) for f in external), fsum(abs(s['pressureKn']) for s in item['soilStates']))
        passed = abs(force_residual) <= 1e-6 * force_scale and abs(moment_residual) <= 1e-6 * force_scale * length
        equilibrium.append({'stageId': item['id'], 'forceResidualKn': force_residual,
                             'momentResidualKnM': moment_residual, 'forceScaleKn': force_scale,
                             'relativeForceResidual': abs(force_residual) / force_scale,
                             'relativeMomentResidual': abs(moment_residual) / (force_scale * length),
                             'passed': passed})
    check('same_snapshot_zero_force_installation', '实际阶段新支点零力安装', 'kN',
          'R=K(u−u_install)=0；刚性支点保持安装位移', 0.0,
          max(map(abs, installed_forces), default=0.0), 1e-5)
    passed = all(c['passed'] for c in checks) and all(c['passed'] for c in worked_rows) and all(c['passed'] for c in equilibrium)
    return {'status': 'passed' if passed else 'failed', 'snapshotHash': snapshot.snapshot_hash,
            'discreteInputHash': result['discreteInputHash'], 'stageId': control, 'checks': checks,
            'soilPressureWorkedRows': worked_rows, 'stageEquilibrium': equilibrium,
            'equilibriumToleranceRelative': 1e-6,
            'scope': '同一冻结剖面的手工可复核项：刚度、分层有效应力、压力界限、水力合力/力矩、零力安装及全阶段平衡；非线性整梁解另与独立求解器比较'}


def _independent_surface_integral(load: dict, depth: float) -> float:
    """Different numerical route from production's signed corner formula.

    Substitute x=z*tan(t), integrate the Boussinesq vertical point kernel
    over y analytically, then use 96-point Gaussian quadrature in t. An
    independent 48-point evaluation must agree before this evidence is used.
    """
    a, b = atan(load['near_m']/depth), atan(load['far_m']/depth)
    def integrate(order):
        xi, weights = np.polynomial.legendre.leggauss(order)
        t = (a+b)/2 + (b-a)*xi/2
        x = depth*np.tan(t)
        square = x*x + depth*depth
        if load['kind'] == 'strip':
            line = 2*load['q_kpa']*depth**3/(pi*square*square)
        else:
            def primitive(y):
                return y*(3*square+2*y*y)/(3*square*square*(square+y*y)**1.5)
            line = 3*load['q_kpa']*depth**3/(2*pi)*(primitive(load['along_end_m'])-primitive(load['along_start_m']))
        return float(np.sum(weights * line * depth / np.cos(t)**2) * (b-a)/2)
    coarse, fine = integrate(48), integrate(96)
    if abs(coarse-fine) > 1e-7 + 1e-6*abs(fine):
        raise ValueError('邻载独立积分未收敛，不能作为同快照核对证据')
    return fine


def compare_independent(snapshot: FrozenSectionSnapshot, result: dict, independent: dict) -> dict:
    validate_result(snapshot, result)
    expected_hash = content_hash(build_solver_model(snapshot))
    if (result.get('discreteInputHash') != expected_hash or independent.get('discreteInputHash') != expected_hash or
        [s.get('id') for s in independent.get('stages', [])] != [s['id'] for s in result['stages']]):
        return {'status': 'conditions_mismatch', 'reason': '离散输入哈希或阶段身份不一致，禁止比较成通过'}
    if independent.get('status') != 'success' or result['status'] != 'complete':
        return {'status': independent.get('status') if independent.get('status') != 'success' else 'failed',
                'reason': independent.get('failureReason', '至少一个求解器未完成全部阶段'),
                'solver': independent.get('solver'), 'solverVersion': independent.get('solverVersion'),
                'discreteInputHash': expected_hash}
    stage_reports = []
    for primary, external in zip(result['stages'], independent['stages']):
        metrics = {}
        for key, tolerance in (('nodalDisplacementM', 1e-8), ('nodalRotation', 1e-8)):
            metrics[key] = _metric(primary[key], external[key], tolerance)
        primary_forces = np.asarray(primary['elementEndForces'])
        external_forces = np.asarray(external['elementEndForces'])
        metrics['elementShearKn'] = _metric(primary_forces[:, [0, 2]], external_forces[:, [0, 2]], 1e-4)
        metrics['elementMomentKnM'] = _metric(primary_forces[:, [1, 3]], external_forces[:, [1, 3]], 1e-4)
        left_soil = {s['id']: s for s in primary['soilStates']}
        right_soil = {s['id']: s for s in external['soilStates']}
        left_supports = {s['id']: s for s in primary['supports'] if s['active']}
        right_supports = {s['id']: s for s in external['supportStates']}
        identity_ok = set(left_soil) == set(right_soil) and set(left_supports) == set(right_supports)
        if identity_ok:
            for key, tolerance in (('pressureKn', 1e-4), ('plasticDisplacementM', 1e-8)):
                metrics[key] = _metric([left_soil[k][key] for k in sorted(left_soil)],
                                       [right_soil[k][key] for k in sorted(left_soil)], tolerance)
            metrics['supportReactionKn'] = _metric([left_supports[k]['reactionCharacteristicKn'] for k in sorted(left_supports)],
                                                   [right_supports[k]['reactionKn'] for k in sorted(left_supports)], 1e-4)
            metrics['supportReferenceM'] = _metric([left_supports[k]['referenceDisplacementMm'] / 1000 for k in sorted(left_supports)],
                                                   [right_supports[k]['referenceDisplacementM'] for k in sorted(left_supports)], 1e-8)
        stage_reports.append({'stageId': primary['id'], 'identitiesMatch': identity_ok,
                              'passed': identity_ok and all(value['passed'] for value in metrics.values()), 'metrics': metrics})
    return {'status': 'passed' if all(stage['passed'] for stage in stage_reports) else 'failed',
            'solver': independent.get('solver'), 'solverVersion': independent.get('solverVersion'),
            'discreteInputHash': expected_hash, 'snapshotHash': snapshot.snapshot_hash,
            'primaryImplementationHash': result['implementationHash'],
            'independentSourceHash': sha256(Path(__file__).with_name('independent.py').read_bytes()).hexdigest(),
            'toleranceRelative': RELATIVE_TOLERANCE,
            'acceptanceRule': '逐值 |a−b| ≤ absoluteTolerance + relativeTolerance·max(|a|,|b|)',
            'scope': '相同离散输入与构件本构的独立数值求解；不校准或证明土参数取值',
            'stages': stage_reports}


def run_mesh_check(snapshot: FrozenSectionSnapshot, result: dict) -> dict:
    validate_result(snapshot, result)
    data = snapshot.payload()
    data['analysis']['max_element_size_m'] *= 0.5
    try:
        fine_snapshot = freeze_section(data)
        fine_result = analyze_section(fine_snapshot)
    except ValueError as exc:
        return {'status': 'unavailable', 'reason': str(exc), 'toleranceRelative': MESH_TOLERANCE}
    common = {'coarseSnapshotHash': snapshot.snapshot_hash, 'fineSnapshotHash': fine_snapshot.snapshot_hash,
              'coarseAnalysisHash': snapshot.analysis_hash, 'fineAnalysisHash': fine_snapshot.analysis_hash,
              'coarseNodeCount': result['mesh']['nodeCount'], 'fineNodeCount': fine_result['mesh']['nodeCount'],
              'toleranceRelative': MESH_TOLERANCE,
              'scope': '网格最大长度减半，比较各阶段峰值与活动支点反力；不改变任何物理参数或阶段事件'}
    if result['status'] != 'complete' or fine_result['status'] != 'complete':
        return {**common, 'status': 'failed', 'reason': '粗细网格至少一方未完成全部阶段', 'stages': []}
    stages = []
    for coarse, fine in zip(result['stages'], fine_result['stages']):
        metrics = {}
        for key, tolerance in (('maxAbsDisplacementMm', 1e-5), ('maxAbsMomentKnM', 1e-4), ('maxAbsShearKn', 1e-4)):
            metrics[key] = {**_metric([coarse[key]], [fine[key]], tolerance, MESH_TOLERANCE),
                            'coarse': coarse[key], 'fine': fine[key]}
        a = {s['id']: s['reactionCharacteristicKn'] for s in coarse['supports'] if s['active']}
        b = {s['id']: s['reactionCharacteristicKn'] for s in fine['supports'] if s['active']}
        metrics['supportReactionsKn'] = _metric([a[k] for k in sorted(a)], [b[k] for k in sorted(a)], 1e-4, MESH_TOLERANCE)
        stages.append({'stageId': coarse['id'], 'passed': all(v['passed'] for v in metrics.values()), 'metrics': metrics})
    return {**common, 'status': 'passed' if all(stage['passed'] for stage in stages) else 'failed', 'stages': stages}


def run_independent_process(snapshot: FrozenSectionSnapshot, timeout_seconds: int = 90) -> dict:
    """MPI/native backend failure cannot terminate the API process.

    The child receives only the shared physical input, never primary results.
    No shell is used; input/output names are private temporary files.
    """
    with tempfile.TemporaryDirectory(prefix='pitguard-independent-') as folder:
        input_path, output_path = Path(folder) / 'model.json', Path(folder) / 'independent.json'
        input_path.write_text(json.dumps(build_solver_model(snapshot), ensure_ascii=False, allow_nan=False), encoding='utf-8')
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + os.pathsep + environment.get('PYTHONPATH', '')
        try:
            completed = subprocess.run([sys.executable, '-m', 'app.section.independent_cli',
                                        '--input', str(input_path), '--output', str(output_path)],
                                       capture_output=True, text=True, timeout=timeout_seconds, check=False, env=environment)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'status': 'unavailable', 'failureReason': f'Independent subprocess did not complete: {exc}'}
        if completed.returncode != 0 or not output_path.exists():
            return {'status': 'unavailable', 'failureReason': '独立求解器进程无法运行；未使用主求解器替代',
                    'processExitCode': completed.returncode, 'diagnostic': completed.stderr[-2000:]}
        return json.loads(output_path.read_text(encoding='utf-8'))


def attach_comparisons(snapshot: FrozenSectionSnapshot, result: dict, *, independent: bool = True,
                       mesh: bool = True) -> dict:
    validate_result(snapshot, result)
    updated = copy.deepcopy(result)
    updated['comparisons']['hand'] = run_hand_checks()
    same_case = same_snapshot_hand_checks(snapshot, result)
    updated['comparisons']['hand']['sameSnapshot'] = same_case
    if same_case['status'] != 'passed':
        updated['comparisons']['hand']['status'] = 'failed' if same_case['status'] == 'failed' else 'incomplete'
    if mesh:
        updated['comparisons']['mesh'] = run_mesh_check(snapshot, result)
    if independent:
        external = run_independent_process(snapshot)
        if external.get('status') == 'unavailable':
            updated['comparisons']['independent'] = {'status': 'unavailable', 'reason': external.get('failureReason'),
                                                     'diagnostic': external.get('diagnostic')}
        else:
            updated['comparisons']['independent'] = compare_independent(snapshot, result, external)
    return seal_result(updated)
