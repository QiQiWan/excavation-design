from __future__ import annotations

import importlib
import math

import pytest

from app.section.models import freeze_section, verification_example


def api():
    try:
        return importlib.import_module('app.section.reinforcement')
    except ModuleNotFoundError:
        pytest.fail('Circular section verification is not implemented')


def envelope(moment=120):
    return {'complete': True, 'governing': {
        'momentAbs': {'valueKnM': moment, 'stageId': 'S3', 'depthM': 3.0, 'side': 'below'},
        'shearAbs': {'valueKn': 80.0, 'stageId': 'S5', 'depthM': 4.6, 'side': 'above'},
    }}


def test_given_bars_are_checked_against_final_envelope_with_one_explicit_factor():
    snap = freeze_section(verification_example())
    report = api().check_reinforcement(snap, envelope())
    row = report['rows'][0]
    assert row['providedAreaMm2'] == pytest.approx(16 * math.pi * 18 ** 2 / 4)
    assert row['momentDesignKnM'] == 150
    assert row['shearDesignKn'] == 100
    assert row['governingStageId'] == 'S3'
    assert row['shearGoverningStageId'] == 'S5'
    assert row['utilization'] == pytest.approx(150 / row['capacityKnM'])
    assert snap.payload()['reinforcement']['bar_count'] == 16
    assert report['formalDesignReady'] is False
    assert 'shear' in report['unverifiedChecks']


def test_concrete_and_discrete_steel_forces_close_axial_equilibrium_and_mesh_converges():
    snap = freeze_section(verification_example())
    m = api()
    a = m.circular_capacity(snap.validated_input(), integration_points=96)
    b = m.circular_capacity(snap.validated_input(), integration_points=192)
    assert abs(a['axialResidualKn']) < 1e-6
    assert a['capacityKnM'] > 0
    assert a['capacityKnM'] == pytest.approx(b['capacityKnM'], rel=0.001)
    reverse = m.circular_capacity(snap.validated_input(), sign=-1)
    assert reverse['capacityKnM'] == pytest.approx(a['capacityKnM'], rel=0.001)


def test_missing_bars_and_incomplete_envelope_remain_unknown():
    data = verification_example()
    data['reinforcement']['bar_count'] = data['reinforcement']['bar_diameter_mm'] = None
    report = api().check_reinforcement(freeze_section(data), envelope())
    assert report['rows'][0]['providedAreaMm2'] is None
    assert report['rows'][0]['capacityKnM'] is None
    assert report['rows'][0]['status'] == 'missing_provided_reinforcement'
    incomplete = api().check_reinforcement(freeze_section(verification_example()), {'complete': False})
    assert incomplete['rows'] == []
    assert incomplete['status'] == 'not_calculated'


def test_excess_demand_does_not_change_provided_bars_or_claim_success():
    report = api().check_reinforcement(freeze_section(verification_example()), envelope(10000))
    row = report['rows'][0]
    assert row['status'] == 'strength_fail'
    assert row['requiredAreaMm2'] is None
    assert row['providedBarsLabel'] == '16Φ18'


def test_default_neutral_axis_meets_independent_angular_integration_equilibrium():
    # Independent adaptive integration in the circular angle coordinate, with
    # a Brent axial-equilibrium root, gives c=161.585215536337 mm. The 8e-8 mm
    # tolerance corresponds to the predeclared 1e-6 kN axial residual gate.
    # Evidence: independent_circular_section_check.py, not the Gauss routine.
    model = freeze_section(verification_example()).validated_input()
    actual = api().circular_capacity(model)
    assert actual['neutralAxisMm'] == pytest.approx(161.585215536337, abs=8e-8, rel=0)
