"""Verification-only exact oracle for the P2 rigid-motion cone correction.

This file is release evidence, not product code. The oracle uses Fraction
interval feasibility after fixing a=+/-1 or b=+/-1. It does not reuse the
production classifiers' perpendicular-ray enumeration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
import inspect
from itertools import product
import json
from pathlib import Path
from time import perf_counter

import numpy as np


def nonzero_feasible(equalities, inequalities):
    """Any nonzero homogeneous-cone member can scale one component to +/-1."""
    for fixed in (0, 1):
        for sign in (-1, 1):
            lo = hi = point = None
            good = True
            for row in equalities:
                slope, const = row[1-fixed], row[fixed]*sign
                if slope == 0:
                    if const != 0:
                        good = False
                        break
                else:
                    trial = -const / slope
                    if point is not None and point != trial:
                        good = False
                        break
                    point = trial
            if not good:
                continue
            for row in inequalities:
                slope, const = row[1-fixed], row[fixed]*sign
                if slope == 0:
                    if const > 0:
                        good = False
                        break
                elif slope > 0:
                    bound = -const / slope
                    hi = bound if hi is None else min(hi, bound)
                else:
                    bound = -const / slope
                    lo = bound if lo is None else max(lo, bound)
            if not good or (lo is not None and hi is not None and lo > hi):
                continue
            if point is not None and ((lo is not None and point < lo) or
                                      (hi is not None and point > hi)):
                continue
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[3]
    sources = {name: repository / 'services/api/app/section' / name
               for name in ('solver.py', 'independent.py')}
    hashes_before = {name: sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}
    from app.section import solver, independent
    from app.version import SOFTWARE_VERSION

    started = perf_counter()
    nodes = [0.0, 0.5, 1.0]
    rational_rows = [(Fraction(1), Fraction(-1, 2)),
                     (Fraction(1), Fraction(0)),
                     (Fraction(1), Fraction(1, 2))]
    branch_definitions = {
        'no_soil': [],
        'inside_lower': [(1, 10)],
        'outside_lower': [(-1, 10)],
        'complementary_lower': [(1, 10), (-1, 10)],
        'interior_elastic': [(1, 50)],
        'strict_capacity_plateau': [(1, 101), (-1, 9)],
        'complementary_upper': [(1, 100), (-1, 100)],
    }
    checked = closed = open_count = 0
    for kinds in product(branch_definitions, repeat=3):
        for toe in ('free', 'pinned', 'fixed'):
            equalities, inequalities, soils = [], [], []
            prescribed = {}
            if toe != 'free':
                equalities.append(rational_rows[-1])
                prescribed[4] = 0.0
            if toe == 'fixed':
                equalities.append((Fraction(0), Fraction(1)))
                prescribed[5] = 0.0
            for node, kind in enumerate(kinds):
                for sign, trial in branch_definitions[kind]:
                    soils.append({
                        'node': node, 'sign': sign, 'lowerKn': 10.0, 'upperKn': 100.0,
                        'trialPressureKn': float(trial), 'p0Kn': 50.0,
                        'kKnPerM': 1000.0, 'localDisplacementM': 0.0,
                        'previousPlasticDisplacementM': (50.0-trial)/1000.0,
                        'tangentKnPerM': 1000.0 if 10 < trial < 100 else 0.0,
                    })
                    if 10 < trial < 100:
                        equalities.append(rational_rows[node])
                    elif trial in (10, 100):
                        orientation = Fraction(sign * (1 if trial == 10 else -1))
                        inequalities.append(tuple(orientation*v for v in rational_rows[node]))
            expected = not nonzero_feasible(equalities, inequalities)
            try:
                independent._check_final_tangent_restraint(nodes, toe, soils, [])
                independent_pass = True
            except RuntimeError:
                independent_pass = False
            primary_soil = {
                key: np.array([item[field] for item in soils],
                              dtype=int if key in ('node', 'sign') else float)
                for key, field in (
                    ('node', 'node'), ('sign', 'sign'), ('lower', 'lowerKn'),
                    ('upper', 'upperKn'), ('p0', 'p0Kn'), ('k', 'kKnPerM'),
                    ('plastic', 'previousPlasticDisplacementM'))
            }
            evaluation = {'trial': np.array([item['trialPressureKn'] for item in soils]),
                          'local': np.zeros(len(soils))}
            try:
                solver._check_directional_restraint(np.zeros(6), np.array(nodes), primary_soil,
                                                    evaluation, {}, prescribed)
                primary_pass = True
            except RuntimeError:
                primary_pass = False
            assert independent_pass == expected == primary_pass, {
                'nodeBranches': kinds, 'toe': toe, 'expectedStable': expected,
                'primaryStable': primary_pass, 'independentStable': independent_pass,
            }
            checked += 1
            closed += int(expected)
            open_count += int(not expected)
    hashes_after = {name: sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}
    assert hashes_before == hashes_after, 'Source changed during verification; rerun against one source revision'
    result = {
        'verificationOnly': True,
        'verifiedAtUtc': datetime.now(timezone.utc).isoformat(),
        'status': 'passed', 'softwareVersion': SOFTWARE_VERSION,
        'fixScope': 'P2 complementary one-sided restraint: rigid-motion direction cone',
        'method': solver.METHOD, 'independentMethod': independent._METHOD,
        'primarySourceSha256': hashes_before['solver.py'],
        'independentSourceSha256': hashes_before['independent.py'],
        'primaryCheckerSourceSha256': sha256(inspect.getsource(solver._check_directional_restraint).encode()).hexdigest(),
        'independentCheckerSourceSha256': sha256(inspect.getsource(independent._check_final_tangent_restraint).encode()).hexdigest(),
        'sourceUnchangedDuringRun': True,
        'oracle': 'Exact Fraction interval feasibility, fixing a=+/-1 or b=+/-1',
        'nodesM': nodes, 'nodeBranchStates': list(branch_definitions),
        'toeBoundaries': ['free', 'pinned', 'fixed'],
        'configurationCount': checked, 'closedConeCount': closed,
        'nonzeroZeroStiffnessConeCount': open_count, 'mismatchCount': 0,
        'directionTolerance': solver._DIRECTION_TOL,
        'boundRoundoffMultiplier': solver._BOUND_ROUNDOFF / np.finfo(float).eps,
        'elapsedSeconds': perf_counter() - started,
        'scopeLimitations': [
            'Tests classifiers against branch states, not whole-stage equilibrium solutions.',
            'Three rational depths; does not bound displacement errors for nearly singular geometries.',
            'No OpenSees native run or contact-support enumeration in this probe.',
        ],
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + '\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    main()
