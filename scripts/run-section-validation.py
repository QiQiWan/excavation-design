#!/usr/bin/env python3
"""Freeze, solve, compare and export a minimum section review package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services' / 'api'))


def main() -> int:
    parser = argparse.ArgumentParser(description='PitGuard frozen section verification and same-snapshot deliverables')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--example', action='store_true', help='Use the fully defined synthetic verification case; it is not project A3-3')
    source.add_argument('--example-v2', action='store_true', help='Use the explicit methods and engineering-check synthetic case; not an original-project reproduction')
    source.add_argument('--input', type=Path, help='Explicit section input JSON or frozen snapshot record')
    reference = parser.add_mutually_exclusive_group()
    reference.add_argument('--a3-intake', action='store_true', help='Bind the documented A3-3 condition gaps and empty original-curve intake')
    reference.add_argument('--reference-contract', type=Path, help='Frozen original-condition contract JSON')
    parser.add_argument('--reference-curves', type=Path, help='Frozen original numerical-curve bundle JSON; requires a contract')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-independent', action='store_true', help='Record independent comparison as not run')
    parser.add_argument('--skip-mesh', action='store_true', help='Record mesh comparison as not run')
    parser.add_argument('--no-package', action='store_true', help='Only write the snapshot and complete result JSON')
    args = parser.parse_args()

    from app.section.models import FrozenSectionSnapshot, freeze_section, verification_example
    from app.section.examples import verification_example_v2
    from app.section.service import analyze_section, validate_result
    from app.section.benchmarks import attach_comparisons
    from app.section.export import build_minimum_package
    from app.section.reference_contract import validate_reference_contract
    from app.section.reference_curves import validate_curve_bundle
    from app.section.reference_workflow import a3_reference_assets, make_reference_comparison, reference_supplement

    if args.example:
        source_data = verification_example()
    elif args.example_v2:
        source_data = verification_example_v2()
    else:
        source_data = json.loads(args.input.read_text(encoding='utf-8'))
    contract = curves = None
    if args.a3_intake:
        assets = a3_reference_assets()
        contract, curves = assets['contract'], assets['curveTemplate']
    elif args.reference_contract:
        contract = json.loads(args.reference_contract.read_text(encoding='utf-8'))
        validate_reference_contract(contract)
    if args.reference_curves:
        if contract is None:
            parser.error('--reference-curves requires --reference-contract or --a3-intake')
        curves = json.loads(args.reference_curves.read_text(encoding='utf-8'))
        validate_curve_bundle(curves)
    if 'snapshotHash' in source_data:
        snapshot = FrozenSectionSnapshot.from_record(source_data)
        if contract and snapshot.payload().get('reference_contract_hash') != contract['contractHash']:
            parser.error('An immutable snapshot cannot be rebound; provide a new v2 input with the intended contract hash')
    else:
        if contract:
            if source_data.get('schema_version') != 'pitguard.frozen-section/2':
                parser.error('Reference binding requires explicit v2 input')
            bound = source_data.get('reference_contract_hash')
            if bound and bound != contract['contractHash']:
                parser.error('Input is bound to a different condition contract')
            source_data['reference_contract_hash'] = contract['contractHash']
        snapshot = freeze_section(source_data)
    args.output.mkdir(parents=True, exist_ok=True)
    print(f'[PitGuard section] snapshot {snapshot.snapshot_hash}', flush=True)
    result = analyze_section(snapshot)
    result = attach_comparisons(snapshot, result, independent=not args.skip_independent, mesh=not args.skip_mesh)
    validate_result(snapshot, result)
    (args.output / 'snapshot.json').write_text(json.dumps(snapshot.to_record(), ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(f'[PitGuard section] analysis {result["status"]}; result {result["resultHash"]}', flush=True)
    for kind, report in result['comparisons'].items():
        print(f'[PitGuard section] {kind}: {report["status"]}', flush=True)
    comparison = (make_reference_comparison(snapshot, result, contract, curves)
                  if contract is not None and curves is not None else None)
    supplement = reference_supplement(snapshot, result, contract, curves, comparison)
    if supplement:
        (args.output / 'reference_supplement.json').write_text(
            json.dumps(supplement, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    if comparison:
        print(f'[PitGuard section] original whole-curve review: {comparison["report"]["status"]}', flush=True)
    if result.get('engineeringChecks'):
        print(f'[PitGuard section] engineering checks: {result["engineeringChecks"]["status"]}', flush=True)
    if not args.no_package:
        archive = build_minimum_package(snapshot, result, args.output, reference=supplement)
        print(f'[PitGuard section] review package: {archive}', flush=True)
    print('[PitGuard section] Exit code checks numerical verification only; original/engineering acceptance remains separate; formalDesignReady=false.', flush=True)
    required = ['hand'] + ([] if args.skip_independent else ['independent']) + ([] if args.skip_mesh else ['mesh'])
    return 0 if result['status'] == 'complete' and all(result['comparisons'][key]['status'] == 'passed' for key in required) else 2


if __name__ == '__main__':
    raise SystemExit(main())
