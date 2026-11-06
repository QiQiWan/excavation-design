from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ConditionThresholds:
    pass_limit: float = 1.0e8
    attention_limit: float = 1.0e10
    warning_limit: float = 1.0e12
    block_limit: float = 1.0e14
    rank_tolerance: float = 1.0e-12


def symmetric_diagonal_scaling(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return D K D and the diagonal vector of D.

    Stiffness matrices mix translational and rotational DOFs whose numerical
    magnitudes can differ by many orders.  Symmetric Jacobi scaling preserves
    symmetry and transforms the unknown through ``u = D y``.
    """
    K = np.asarray(matrix, dtype=float)
    if K.ndim != 2 or K.shape[0] != K.shape[1]:
        raise ValueError("stiffness_matrix_must_be_square")
    diag = np.abs(np.diag(K))
    finite_positive = diag[np.isfinite(diag) & (diag > 0.0)]
    reference = float(np.median(finite_positive)) if finite_positive.size else 1.0
    floor = max(reference * 1.0e-15, 1.0e-18)
    scale = 1.0 / np.sqrt(np.maximum(diag, floor))
    scaled = (scale[:, None] * K) * scale[None, :]
    return scaled, scale


def _condition_number_symmetric(matrix: np.ndarray) -> tuple[float | None, str, float | None, float | None, int]:
    K = np.asarray(matrix, dtype=float)
    n = int(K.shape[0]) if K.ndim == 2 else 0
    if n == 0:
        return None, "unavailable", None, None, 0
    try:
        if n <= 900:
            values = np.linalg.eigvalsh(0.5 * (K + K.T))
            absolute = np.abs(values)
            vmax = float(np.max(absolute)) if absolute.size else 0.0
            threshold = max(vmax * 1.0e-14, 1.0e-14)
            nonzero = absolute[absolute > threshold]
            rank = int(nonzero.size)
            signed_min = float(np.min(values)) if values.size else None
            if not nonzero.size:
                return None, "symmetric_eigenvalue_ratio", signed_min, vmax, rank
            vmin_abs = float(np.min(nonzero))
            return float(vmax / max(vmin_abs, 1.0e-30)), "symmetric_eigenvalue_ratio", signed_min, vmax, rank
        # Bounded-cost proxy for larger matrices.
        diag = np.abs(np.diag(K))
        row_offdiag = np.sum(np.abs(K), axis=1) - diag
        lower = np.maximum(diag - row_offdiag, 1.0e-18)
        upper = diag + row_offdiag
        valid = np.isfinite(lower) & np.isfinite(upper) & (upper > 0.0)
        if not np.any(valid):
            return None, "gershgorin_estimate", None, None, 0
        cond = float(np.max(upper[valid]) / max(float(np.min(lower[valid])), 1.0e-30))
        return cond, "gershgorin_estimate", float(np.min(lower[valid])), float(np.max(upper[valid])), n
    except (np.linalg.LinAlgError, ValueError, FloatingPointError):
        return None, "failed", None, None, 0


def condition_grade(value: float | None, thresholds: ConditionThresholds | None = None) -> dict[str, Any]:
    limits = thresholds or ConditionThresholds()
    if value is None or not math.isfinite(float(value)):
        return {
            "grade": "blocked",
            "status": "fail",
            "label": "不可用",
            "blocked": True,
            "message": "未获得有限条件数，矩阵数值质量不可判定。",
        }
    value = float(value)
    if value <= limits.pass_limit:
        grade, status, label, blocked = "A", "pass", "稳定", False
    elif value <= limits.attention_limit:
        grade, status, label, blocked = "B", "warning", "关注", False
    elif value <= limits.warning_limit:
        grade, status, label, blocked = "C", "warning", "警告", False
    elif value <= limits.block_limit:
        grade, status, label, blocked = "D", "fail", "严重病态", True
    else:
        grade, status, label, blocked = "E", "fail", "极端病态", True
    return {
        "grade": grade,
        "status": status,
        "label": label,
        "blocked": blocked,
        "message": f"尺度化矩阵条件数为 {value:.3e}，数值等级 {grade}（{label}）。",
    }


def solve_scaled_symmetric(
    matrix: np.ndarray,
    load: np.ndarray,
    *,
    thresholds: ConditionThresholds | None = None,
    allow_screening_regularization: bool = False,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Scale and solve a symmetric stiffness system with an explicit hard gate."""
    K = np.asarray(matrix, dtype=float)
    F = np.asarray(load, dtype=float)
    limits = thresholds or ConditionThresholds()
    raw_cond, raw_method, raw_min, raw_max, raw_rank = _condition_number_symmetric(K)
    Ks, scale = symmetric_diagonal_scaling(K)
    Fs = scale * F
    scaled_cond, scaled_method, scaled_min, scaled_max, scaled_rank = _condition_number_symmetric(Ks)
    grade = condition_grade(scaled_cond, limits)
    rank_deficient = scaled_rank < K.shape[0]
    positive_definite = bool(scaled_min is not None and scaled_min > -limits.rank_tolerance)
    blocked = bool(grade["blocked"] or rank_deficient or not positive_definite)
    regularized = False
    regularization = 0.0
    solve_method = "scaled_direct"
    if blocked and not allow_screening_regularization:
        return None, {
            "status": "blocked",
            "blocked": True,
            "conditionGrade": grade,
            "rawConditionNumber": raw_cond,
            "scaledConditionNumber": scaled_cond,
            "rawConditionMethod": raw_method,
            "scaledConditionMethod": scaled_method,
            "rawEigenMin": raw_min,
            "rawEigenMax": raw_max,
            "scaledEigenMin": scaled_min,
            "scaledEigenMax": scaled_max,
            "matrixRank": scaled_rank,
            "matrixSize": int(K.shape[0]),
            "rankDeficient": rank_deficient,
            "positiveDefinite": positive_definite,
            "regularized": False,
            "regularization": 0.0,
            "solveMethod": "blocked_before_solve",
            "message": "尺度化后矩阵仍病态、秩亏或非正定，已自动阻断，不使用正则化结果作为设计依据。",
        }
    Ksolve = Ks
    if blocked and allow_screening_regularization:
        regularization = max(float(np.max(np.abs(np.diag(Ks)))) * 1.0e-10, 1.0e-12)
        Ksolve = Ks + np.eye(Ks.shape[0]) * regularization
        regularized = True
        solve_method = "scaled_regularized_screening"
    try:
        y = np.linalg.solve(Ksolve, Fs)
    except np.linalg.LinAlgError:
        if not allow_screening_regularization:
            return None, {
                "status": "blocked",
                "blocked": True,
                "conditionGrade": grade,
                "rawConditionNumber": raw_cond,
                "scaledConditionNumber": scaled_cond,
                "rawConditionMethod": raw_method,
                "scaledConditionMethod": scaled_method,
                "matrixRank": scaled_rank,
                "matrixSize": int(K.shape[0]),
                "rankDeficient": True,
                "positiveDefinite": positive_definite,
                "regularized": regularized,
                "regularization": regularization,
                "solveMethod": "direct_solve_failed",
                "message": "尺度化刚度矩阵直接求解失败，已自动阻断。",
            }
        y = np.linalg.lstsq(Ksolve, Fs, rcond=1.0e-12)[0]
        regularized = True
        solve_method = "scaled_regularized_least_squares_screening"
    displacement = scale * y
    residual = K @ displacement - F
    residual_norm = float(np.linalg.norm(residual))
    reference = max(float(np.linalg.norm(F)), 1.0)
    relative_residual = residual_norm / reference
    finite = bool(np.all(np.isfinite(displacement)) and math.isfinite(relative_residual))
    status = "pass"
    if not finite or relative_residual > 1.0e-5:
        status = "fail"
        blocked = True
    elif grade["status"] == "warning" or relative_residual > 1.0e-8 or regularized:
        status = "warning"
    return displacement if finite else None, {
        "status": status,
        "blocked": blocked,
        "conditionGrade": grade,
        "rawConditionNumber": raw_cond,
        "scaledConditionNumber": scaled_cond,
        "rawConditionMethod": raw_method,
        "scaledConditionMethod": scaled_method,
        "rawEigenMin": raw_min,
        "rawEigenMax": raw_max,
        "scaledEigenMin": scaled_min,
        "scaledEigenMax": scaled_max,
        "matrixRank": scaled_rank,
        "matrixSize": int(K.shape[0]),
        "rankDeficient": rank_deficient,
        "positiveDefinite": positive_definite,
        "regularized": regularized,
        "regularization": regularization,
        "solveMethod": solve_method,
        "relativeResidual": relative_residual,
        "residualNorm": residual_norm,
        "scalingMin": float(np.min(scale)) if scale.size else 1.0,
        "scalingMax": float(np.max(scale)) if scale.size else 1.0,
        "message": (
            "尺度化求解通过。" if status == "pass"
            else "尺度化求解完成，但条件等级、残差或筛查正则化需要复核。" if status == "warning"
            else "尺度化求解未满足数值质量门禁。"
        ),
    }
