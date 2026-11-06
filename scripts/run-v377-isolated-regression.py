#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = [
    "services/api/tests/test_v3_70_0_planar_transfer_delivery.py::test_v368_full_stage_frame_analysis_populates_transfer_beam_design",
    "services/api/tests/test_v3_70_0_planar_transfer_delivery.py::test_v371_formal_delivery_blocks_missing_real_data_and_credential",
    "services/api/tests/test_v3_71_0_numerical_coupling.py::test_scaled_solver_reduces_condition_number_and_preserves_solution",
    "services/api/tests/test_v3_71_0_numerical_coupling.py::test_scaled_solver_blocks_rank_deficient_stiffness_matrix",
    "services/api/tests/test_v3_71_0_numerical_coupling.py::test_v371_full_chain_has_conditioning_coupling_sensitivity_and_spatial_detailing",
    "services/api/tests/test_v3_72_0_workflow_stability_results.py::test_calculation_transaction_rolls_back_trial_mutations",
    "services/api/tests/test_v3_72_0_workflow_stability_results.py::test_full_result_contains_execution_health_completeness_and_catalog",
    "services/api/tests/test_v3_77_0_accuracy_compliance_workflow.py",
]


def main() -> int:
    targets = sys.argv[1:] or DEFAULT_TARGETS
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "services" / "api")
    rows = []
    for target in targets:
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", target],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=240,
        )
        rows.append({
            "target": target,
            "returnCode": proc.returncode,
            "durationSeconds": round(time.perf_counter() - started, 3),
            "output": proc.stdout[-4000:],
        })
        print(("PASS" if proc.returncode == 0 else "FAIL"), target)
    report = {
        "schema": "pitguard-isolated-regression-v1",
        "status": "pass" if all(row["returnCode"] == 0 for row in rows) else "fail",
        "targetCount": len(rows),
        "passCount": sum(row["returnCode"] == 0 for row in rows),
        "rows": rows,
    }
    out = ROOT / "docs" / "releases" / "V3_77_0_ISOLATED_REGRESSION.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
