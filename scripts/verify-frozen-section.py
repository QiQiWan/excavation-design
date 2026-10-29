#!/usr/bin/env python3
"""Reproduce the focused frozen-section software gates and retain complete output.

This is a bounded regression gate. The full application suites and original
project/model comparison have separate, explicitly recorded statuses.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SECTION_TESTS = [
    'snapshot', 'preprocess', 'solver', 'independent', 'reinforcement',
    'service', 'benchmarks', 'api', 'export',
    'v2', 'history_options', 'geotech_actions', 'engineering_rebar',
    'reference', 'reference_curves', 'reference_workflow', 'v2_export',
    'docx_fonts',
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-web', action='store_true')
    parser.add_argument('--require-independent', action='store_true',
                        help='Fail before running if OpenSeesPy is unavailable; do not accept skipped comparisons.')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification-output')
    args = parser.parse_args()
    if args.require_independent and importlib.util.find_spec('openseespy') is None:
        print('OpenSeesPy is required. Install the benchmark extra with Python >= 3.12.', file=sys.stderr)
        return 2
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    prior = runpy.run_path(str(ROOT / 'scripts/verify-calculation-reliability.py'))['BACKEND_FILES']
    files = [f'services/api/tests/test_section_{name}.py' for name in SECTION_TESTS]
    files.extend(f'services/api/tests/{name}' for name in prior)
    steps = [
        ('release_metadata', [sys.executable, 'scripts/verify-release-consistency.py'], ROOT),
        ('python_compilation', [sys.executable, '-m', 'compileall', '-q', 'services/api/app'], ROOT),
        ('focused_backend', [sys.executable, '-m', 'pytest', *files, '-q', '--tb=short',
                             f'--junitxml={output / "focused_backend.xml"}'], ROOT),
    ]
    if not args.skip_web:
        npm, node = shutil.which('npm'), shutil.which('node')
        if not npm or not node:
            print('Node and npm are required unless --skip-web is explicit.', file=sys.stderr)
            return 2
        steps.extend([
            ('focused_frontend', [npm, 'test', '--', 'src/components/FrozenSectionPanel.test.tsx',
                                  'src/api/client.section.test.ts', 'src/pages/CoreProjectWorkspace.test.tsx',
                                  'src/viewers/RetainingSystemViewer.test.tsx', 'src/viewers/ResultViewer.test.tsx'], ROOT / 'apps/web'),
            ('frontend_build', [npm, 'run', 'build'], ROOT / 'apps/web'),
            ('production_bundle', [node, 'scripts/verify-production-build.mjs', 'apps/web/dist'], ROOT),
        ])
    reports = []
    for name, command, cwd in steps:
        print(f'[PitGuard section gate] {name}', flush=True)
        started = time.monotonic()
        try:
            completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace', timeout=900, check=False)
            exit_code = completed.returncode
            log = completed.stdout + '\n--- stderr ---\n' + completed.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            def as_text(value):
                return value.decode('utf-8', 'replace') if isinstance(value, bytes) else value or ''
            log = as_text(exc.stdout) + '\n--- stderr ---\n' + as_text(exc.stderr) + '\nTIMEOUT after 900 seconds\n'
        (output / f'{name}.log').write_text(log, encoding='utf-8')
        reports.append({'step': name, 'command': command, 'cwd': str(cwd), 'exitCode': exit_code,
                        'durationSeconds': round(time.monotonic() - started, 3), 'log': f'{name}.log'})
        print('\n'.join(log.splitlines()[-14:]), flush=True)
        print(f'[PitGuard section gate] {name}: exit {exit_code}', flush=True)
    passed = all(row['exitCode'] == 0 for row in reports)
    version = runpy.run_path(str(ROOT / 'services/api/app/version.py'))['SOFTWARE_VERSION']
    record = {'softwareVersion': version, 'status': 'passed' if passed else 'failed',
              'scope': 'New section tests plus preserved V3.87.16 focused regression gate; not the entire application suite.',
              'independentRequired': args.require_independent, 'frontendSkipped': args.skip_web,
              'pythonVersion': sys.version, 'steps': reports}
    (output / 'focused_gate_results.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[PitGuard section gate] Evidence: {output / "focused_gate_results.json"}', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
