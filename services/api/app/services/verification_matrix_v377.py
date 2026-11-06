from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from app.calculation.opensees_benchmark import (
    run_independent_reference_benchmark_suite,
    run_opensees_planar_benchmark_suite,
)
from app.calculation.spatial_frame_6dof import beam_local_stiffness_3d
from app.rules.gb50017.steel_support_rules import steel_pipe_buckling_curve
from app.version import ALGORITHM_VERSION, SOFTWARE_VERSION


def _beam_kernel_case() -> dict[str, Any]:
    k = beam_local_stiffness_3d(2.06e8, 7.92e7, 0.025, 8.5e-5, 7.8e-5, 1.5e-4, 6.0)
    symmetry = float(np.linalg.norm(k - k.T) / max(np.linalg.norm(k), 1.0))
    eig = np.linalg.eigvalsh((k + k.T) * 0.5)
    rigid_modes = int(np.sum(np.abs(eig) <= max(np.max(np.abs(eig)), 1.0) * 1.0e-10))
    status = "pass" if symmetry <= 1.0e-12 and rigid_modes >= 6 and np.min(eig) >= -max(np.max(np.abs(eig)), 1.0) * 1.0e-9 else "fail"
    return {
        "caseId": "six_dof_beam_kernel",
        "status": status,
        "symmetryError": symmetry,
        "rigidBodyModeCount": rigid_modes,
        "minimumEigenvalue": float(np.min(eig)),
        "maximumEigenvalue": float(np.max(eig)),
    }


def _steel_curve_case() -> dict[str, Any]:
    lengths = [3.0, 6.0, 9.0, 12.0]
    rows = [steel_pipe_buckling_curve(outer_diameter_m=0.609, wall_thickness_m=0.016, length_m=l) for l in lengths]
    factors = [float(row.get("stabilityReductionFactor") or 0.0) for row in rows]
    monotonic = all(a >= b for a, b in zip(factors, factors[1:]))
    return {
        "caseId": "steel_buckling_curve_monotonicity",
        "status": "pass" if monotonic and all(row.get("status") == "ok" for row in rows) else "fail",
        "lengthsM": lengths,
        "stabilityReductionFactors": factors,
    }


@lru_cache(maxsize=1)
def run_v377_verification_matrix() -> dict[str, Any]:
    independent = run_independent_reference_benchmark_suite()
    external = run_opensees_planar_benchmark_suite()
    cases = [_beam_kernel_case(), _steel_curve_case()]
    internal_pass = independent.get("status") == "pass" and all(row["status"] == "pass" for row in cases)
    external_status = str(external.get("status") or "unavailable")
    overall = "fail" if not internal_pass or external_status == "fail" else "warning" if external_status in {"unavailable", "partial"} else "pass"
    return {
        "schema": "pitguard-v3.77-verification-matrix-v1",
        "softwareVersion": SOFTWARE_VERSION,
        "algorithmVersion": ALGORITHM_VERSION,
        "status": overall,
        "internalReferenceStatus": independent.get("status"),
        "externalReferenceStatus": external_status,
        "formalExternalBenchmarkReady": external_status == "pass",
        "caseCount": len(cases) + int(independent.get("caseCount") or 0) + int(external.get("caseCount") or 0),
        "kernelCases": cases,
        "independentReference": independent,
        "externalReference": external,
        "scope": "线弹性单元、组装、稳定曲线和参考求解交叉验证；不覆盖土体非线性、接触、固结、动力和施工误差。",
    }


def runtime_verification_summary() -> dict[str, Any]:
    matrix = run_v377_verification_matrix()
    return {
        "schema": matrix["schema"],
        "softwareVersion": matrix["softwareVersion"],
        "status": matrix["status"],
        "internalReferenceStatus": matrix["internalReferenceStatus"],
        "externalReferenceStatus": matrix["externalReferenceStatus"],
        "formalExternalBenchmarkReady": matrix["formalExternalBenchmarkReady"],
        "caseCount": matrix["caseCount"],
        "scope": matrix["scope"],
    }
