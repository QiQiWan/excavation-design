"""Immutable comparison evidence binds existing results; it never reruns a design."""
from copy import deepcopy
import json
from pathlib import Path

from .models import content_hash
from .reference_contract import validate_reference_contract, assess_reference_contract
from .reference_curves import validate_curve_bundle, compare_reference_curves, a3_curve_template


def a3_reference_assets() -> dict:
    # Bundled data, also available in the source examples. Installed API wheels
    # must not depend on a Git checkout, current directory or arbitrary file IO.
    contract = json.loads(Path(__file__).with_name('assets').joinpath('A3-3-reference-contract.json').read_text(encoding='utf-8'))
    validate_reference_contract(contract)
    return {'contract': contract, 'assessment': assess_reference_contract(contract),
            'curveTemplate': a3_curve_template(contract)}


def make_reference_comparison(snapshot, result: dict, contract: dict, curves: dict) -> dict:
    report = compare_reference_curves(snapshot, result, contract, curves)
    record = {'schemaVersion': 'pitguard.reference-comparison-record/1',
              'comparisonAlgorithm': 'whole-curve-comparison/1',
              'snapshotHash': snapshot.snapshot_hash, 'resultHash': result['resultHash'],
              'contractHash': contract['contractHash'], 'curveBundleHash': curves['bundleHash'],
              'report': report}
    record['comparisonHash'] = content_hash(record)
    return deepcopy(record)


def validate_reference_comparison(record, snapshot, result, contract, curves) -> None:
    validate_reference_contract(contract); validate_curve_bundle(curves)
    expected = make_reference_comparison(snapshot, result, contract, curves)
    if record != expected:
        raise ValueError('Stored reference comparison does not match its frozen evidence / 比较与冻结证据不符')


def reference_supplement(snapshot, result, contract=None, curves=None, comparison=None) -> dict | None:
    """Validate, then detach everything required to audit an export supplement."""
    if contract is None:
        if curves is not None or comparison is not None:
            raise ValueError('Reference supplement requires its contract')
        return None
    validate_reference_contract(contract)
    # A curve attachment is evidence even when nobody has compared it yet.
    # Hashing a supplement must not launder a malformed or different-case
    # bundle into an apparently validated export.
    if curves is not None:
        validate_curve_bundle(curves)
        if curves['contractHash'] != contract['contractHash'] or curves['caseId'] != contract['caseId']:
            raise ValueError('Reference curve attachment contract/case identity mismatch')
    if comparison is not None:
        if curves is None:
            raise ValueError('Comparison supplement requires the frozen original curve bundle')
        validate_reference_comparison(comparison, snapshot, result, contract, curves)
    elif snapshot.payload().get('reference_contract_hash') != contract['contractHash']:
        raise ValueError('Uncompared contract must be bound to this snapshot')
    payload = {'schemaVersion': 'pitguard.reference-supplement/1', 'snapshotHash': snapshot.snapshot_hash,
               'resultHash': result['resultHash'], 'contract': contract,
               'assessment': assess_reference_contract(contract), 'referenceCurves': curves,
               'comparison': comparison}
    payload['supplementHash'] = content_hash(payload)
    return deepcopy(payload)
