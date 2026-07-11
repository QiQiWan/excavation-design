"""PitGuard API package bootstrap.

Small and medium engineering matrices are solved concurrently by the task
manager. Limiting each BLAS kernel to one thread avoids nested thread pools,
large resident-memory growth and unstable latency when several projects run in
parallel. Set PITGUARD_NUMERIC_THREADS before startup to override the default.
"""
from __future__ import annotations

import os

try:
    _numeric_threads_value = max(1, int(os.getenv("PITGUARD_NUMERIC_THREADS", "1")))
except (TypeError, ValueError):
    _numeric_threads_value = 1
_numeric_threads = str(_numeric_threads_value)
for _variable in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_variable] = _numeric_threads
