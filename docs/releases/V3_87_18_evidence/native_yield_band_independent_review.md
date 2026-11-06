# Independent read-only review: native ElasticPP yield band

Reviewed `services/api/app/section/independent.py::_check_final_tangent_restraint`, the four new regressions in `test_section_independent.py`, and the recorded 14-point OpenSees 3.8.0 probe in `docs/releases/V3_87_18_evidence/native_yield_band_probe.json`.

No blocking issue found. Strict exterior trial states are discarded before new branch evaluation. Native zero tangent, pressure at the corresponding nearest limit, and inside distance bounded by k*DBL_EPSILON plus 64-epsilon arithmetic scale are required together. The admitted state adds only a one-sided half-plane, never a bilateral equality. Lower/upper orientation signs match their zero-stiffness directions. At exactly one epsilon the probe returns elastic tangent, which the native-zero gate excludes despite the inclusive distance bound.

Interpretation must remain a numerical one-sided rigid-motion cone check, not a claim that OpenSees selected tangent matrix is positive definite. The review found no artificial bilateral restraint or promotion of strictly exterior plastic branches.

The four added tests inspect band classification, incorrect zero tangent deeper inside, strictly exterior mechanism rejection, and pressure at the wrong bound. Initial successful classification test used fixed toe, leaving a small direct mechanism-coverage gap. Root subsequently reported adding a pinned-toe plus single one-sided corner assertion requiring mechanism rejection to the same test. That addition is root-reported and was awaiting the final unified test gate at handoff; this reviewer did not rerun tests. Root later reported the 3x7 method matrix passed without parameter changes; final gate evidence remains root-owned.

No production/test files modified and no calculations run by this reviewer in this review task.
