from __future__ import annotations

import importlib
import pytest

from app.section.models import freeze_section, verification_example
from app.section.service import analyze_section
from app.section.preprocess import build_solver_model
from app.section.solver import solve_stages


def api():
    try:
        return importlib.import_module('app.section.benchmarks')
    except ModuleNotFoundError:
        pytest.fail('Explicit same-condition comparison is not implemented')


def test_hand_calculation_checks_real_solver_for_cantilever_and_installation():
    report = api().run_hand_checks()
    assert report['status'] == 'passed'
    tip = next(item for item in report['checks'] if item['id'] == 'cantilever_tip_displacement')
    assert tip['expected'] == pytest.approx(10 * 5 ** 3 / (3 * 100000))
    assert tip['actual'] == pytest.approx(tip['expected'], rel=0.001)
    assert all(item['passed'] for item in report['checks'])


def test_comparison_checks_input_identity_and_detects_changed_response():
    module = api()
    snapshot = freeze_section(verification_example())
    result = analyze_section(snapshot)
    independent = solve_stages(build_solver_model(snapshot))
    independent.update(solver='test comparator fixture', solverVersion='fixture',
                       discreteInputHash=result['discreteInputHash'])
    report = module.compare_independent(snapshot, result, independent)
    assert report['status'] == 'passed'
    independent['stages'][3]['nodalDisplacementM'][0] += 0.01
    assert module.compare_independent(snapshot, result, independent)['status'] == 'failed'
    independent['discreteInputHash'] = '0' * 64
    assert module.compare_independent(snapshot, result, independent)['status'] == 'conditions_mismatch'


def test_mesh_halving_is_reported_with_both_distinct_snapshot_identities():
    snapshot = freeze_section(verification_example())
    report = api().run_mesh_check(snapshot, analyze_section(snapshot))
    assert report['status'] == 'passed'
    assert report['coarseSnapshotHash'] == snapshot.snapshot_hash
    assert report['fineSnapshotHash'] != snapshot.snapshot_hash
    assert report['fineNodeCount'] > report['coarseNodeCount']
    assert report['toleranceRelative'] == 0.02
    assert len(report['stages']) == 6


def test_hand_audit_uses_the_same_frozen_section_water_soil_and_stage_forces():
    module = api()
    snapshot = freeze_section(verification_example())
    result = analyze_section(snapshot)
    assert hasattr(module, 'same_snapshot_hand_checks'), 'Hand comparison must also use the actual frozen case'
    report = module.same_snapshot_hand_checks(snapshot, result)
    assert report['status'] == 'passed'
    assert report['snapshotHash'] == snapshot.snapshot_hash
    assert report['stageId'] == result['envelope']['governing']['momentAbs']['stageId']
    water = next(row for row in report['checks'] if row['id'] == 'same_snapshot_water_force')
    assert water['expected'] == pytest.approx(.9 * 9.81 / 2 * (8.5**2 - 4.6**2))
    assert len(report['stageEquilibrium']) == 6
    assert {row['side'] for row in report['soilPressureWorkedRows']} == {'inside', 'outside'}
    assert all(row['verticalStressSegments'] for row in report['soilPressureWorkedRows'])
