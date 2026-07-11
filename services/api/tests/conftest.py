from __future__ import annotations

import os
import sys

import pytest

# Keep CI deterministic and prevent nested BLAS thread pools from competing
# with PitGuard's project-level task concurrency.
os.environ["PITGUARD_NUMERIC_THREADS"] = "1"
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_name] = "1"


@pytest.fixture(autouse=True)
def close_test_figures():
    """Release Matplotlib figures without forcing a full cyclic-GC scan.

    Several engineering fixtures contain thousands of Pydantic objects and
    individual rebar polylines. Calling ``gc.collect()`` after every test made
    teardown dominate runtime. The full release gate already isolates heavy
    nodes in separate processes, so process termination provides the stronger
    cleanup boundary.
    """
    yield
    if "matplotlib.pyplot" in sys.modules:
        try:
            sys.modules["matplotlib.pyplot"].close("all")
        except Exception:
            pass
