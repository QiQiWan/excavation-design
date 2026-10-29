#!/usr/bin/env python3
"""Reproduce the bounded V3.87.16 calculation-reliability regression gates.

Run in a Python environment with the API dev dependencies installed, and run
npm ci in apps/web first. This is a focused regression gate, not engineering
certification or a replacement for the full project test suites.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND_FILES = [
    "test_calculation_pipeline_reliability.py",
    "test_stage_solver_consistency.py",
    "test_stage_load_contract.py",
    "test_calculation_mechanics_reliability.py",
    "test_unknown_calculation_outputs.py",
    "test_engineering_state_truth.py",
    "test_verify_api_contract.py",
    "test_v3_55_0_intelligent_design_closure.py",
    "test_v3_87_5_optimization_search.py",
    "test_v3_87_6_unified_auto_closure.py",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web", action="store_true", help="Run only release metadata, Python compilation, and backend regressions.")
    args = parser.parse_args()
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    steps: list[tuple[str, list[str], Path]] = [
        ("release metadata", [sys.executable, "scripts/verify-release-consistency.py"], ROOT),
        ("Python compilation", [sys.executable, "-m", "compileall", "-q", "services/api/app"], ROOT),
        ("focused backend regressions", [sys.executable, "-m", "pytest",
            *[f"services/api/tests/{name}" for name in BACKEND_FILES], "-q", "--tb=short"], ROOT),
    ]
    if not args.skip_web:
        npm = shutil.which("npm")
        node = shutil.which("node")
        if not npm or not node:
            print("Node.js and npm are required; install dependencies or explicitly use --skip-web.", file=sys.stderr)
            return 2
        steps.extend([
            ("focused frontend regressions", [npm, "test", "--", "src/pages/CoreProjectWorkspace.test.tsx",
                "src/viewers/RetainingSystemViewer.test.tsx", "src/viewers/ResultViewer.test.tsx"], ROOT / "apps/web"),
            ("production frontend build", [npm, "run", "build"], ROOT / "apps/web"),
            ("production bundle API checks", [node, "scripts/verify-production-build.mjs", "apps/web/dist"], ROOT),
        ])
    for name, command, cwd in steps:
        print(f"[PitGuard reliability] {name}", flush=True)
        completed = subprocess.run(command, cwd=cwd, env=env, check=False)
        if completed.returncode:
            print(f"[PitGuard reliability] Failed: {name} (exit {completed.returncode})", file=sys.stderr)
            return completed.returncode
    print("[PitGuard reliability] Selected gates passed. Full-suite and engineering-method limitations remain as documented.")
    if args.skip_web:
        print("[PitGuard reliability] Frontend tests/build were explicitly skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
