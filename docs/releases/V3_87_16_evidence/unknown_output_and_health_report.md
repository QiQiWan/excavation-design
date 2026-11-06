# Unknown calculation result propagation

## Changes and verification

Edited only `services/api/app/calculation/result_enrichment.py` for the downstream task and added `services/api/tests/test_unknown_calculation_outputs.py`. Tests construct actual FD/global failure outputs through controlled matrix-solver failure injection, validate them into the real domain result classes, and run public `enrich_calculation_result`.

- Unknown wall displacement remains None in stage matrices and wall envelopes. A partial envelope remains unknown; `maximumKnownDisplacementMm` exposes available information separately with `displacementStatus=partial/unavailable/complete`.
- Missing wall objects are retained as missing envelope contributors. No governing displacement stage is invented for an incomplete envelope. True zero displacement remains a valid known result.
- Stage rows cannot claim pass for unknown displacement or fallback solves.
- Numerical residual and condition maxima remain None when required evidence is missing; missing/fallback global systems cannot produce a numerical pass. `unknownSystemCount` provides availability evidence.
- Wall-result completeness requires finite moment, shear, and displacement, rather than merely the existence of a result object.
- Critical-stage ranking identifies unknown outputs explicitly.
- Missing stage elevation and unavailable stability values serialize as JSON null, avoiding NaN serialization failure.

Initial meaningful red run: **6 failures** (`null_result_red.txt`). Additional missing-wall-envelope regression failed before repair. Final focused run: **35 passed** (8 unknown-output tests plus 27 mechanics tests), recorded in `null_result_green.txt`. Scoped git diff check clean. No full project suite was run.

## Concurrent mechanics API follow-up

At root request, wall FD now accepts optional trailing `long_term_stiffness_factor=1.0` before `pressure_profile`. Wall stiffness factor uses [0.25,4] and long-term uses [0.20,1] independently, matching the global solver. A .25 × .6 product therefore remains .15. A regression failed on the missing API and passed after implementation. This is the 27th focused mechanics test.

## Root-owned assurance corrections identified

`app/services/calculation_assurance.py` was inspected but not edited:

- Around 454-455, `max_wall_displacement or 0.0` and `max_displacement or 0.0` manufacture equal-zero independent checks from missing values. Only compare known finite results; record unavailable pairs and prevent an independent-check pass.
- `_finite_numbers` around 390 filters None out, so missing fields can still yield a finite-success conclusion. Required output availability needs separate validation.
- `finite_stages` around 516 substitutes 0 for missing wall results. This does not prove required stage outputs exist.

## Adjacent consumers reported, not modified

These were outside this task's assigned write ownership; findings sent to root:

- `services/serviceability_service.py:64-70`: missing governing displacement becomes 0 and can produce a displacement pass.
- `services/calculation_diagnostics.py:60`: wall diagnostic envelopes turn missing displacement into 0.
- `calculation/nonlinear_geotechnical.py:163,175`: projected displacement cases become 0 from unknown governing displacement.
- `services/monitoring_calibration.py:77,119,307`: unknown model displacement is converted into zero prediction.
- `services/calculation_trace.py:191,227`: missing global displacement becomes 0 for an invented comparison capacity; stage demand itself remains None.

These are concrete follow-up consumers rather than claims that all downstream null propagation has been fully repaired.

## Assigned adjacent consumer repairs completed

Root subsequently assigned ownership of the adjacent main/quality consumers. The following are now repaired (superseding the deferred list above except monitoring):

- `services/serviceability_service.py`: absent/nonfinite governing displacement stays None for short- and long-term values; displacement status is manual_review and counts prevent an overall pass. A genuine zero remains computable.
- `services/calculation_diagnostics.py`: missing and partial displacement envelopes stay unknown, with known maximum reported separately. Missing wall objects remain contributors. Unknown displacement prevents a stiffness or overall diagnostics pass.
- `calculation/nonlinear_geotechnical.py`: unknown point displacement does not become zero mobilization/full tangent stiffness. Point and envelope ratios are None; displacement sensitivity projections remain None when base displacement is unknown. Incomplete displacement evidence prevents formalUseAllowed and status pass.
- `services/calculation_trace.py`: when stage or governing displacement is unknown, the wall-deformation trace does not manufacture capacity=1; capacity and utilization remain None and the entry requires manual review. The known-input legacy capacity convention was outside this narrow null repair.

Four new consumer tests failed before repairs (`null_result_followup_red.txt`). A further public-diagnostics test exposed an unearned stiffness/overall pass and also followed red→green. Final run: **41 passed in 0.52s** = 13 unknown-result regressions + 27 mechanics regressions + one existing structured-trace regression. `null_result_green.txt` records the final run; scoped diff check is clean.

No changes to root-owned assurance/engine/schema files. **Remaining deferred null consumer:** separately enabled `services/monitoring_calibration.py` still converts unknown model displacement to zero predictions (lines approximately 77,119,307). Root explicitly deferred that path; it is not represented as fixed.

## Final packaging review: standard/design availability

A strictly scoped final review confirmed that `_numerical_health` and `_stage_rows` only inspected the standard global result directly, despite publishing generic status. Explicit design global or design FD failures could therefore be hidden behind successful standard results when no separate check had already marked the row.

Repaired only `result_enrichment.py` and its focused test file:

- Added per-basis availability summaries for standard/design global solves and `coupled_system_result.solveEvidence.wallSolvers` evidence.
- Generic numerical-health and stage-row statuses now include the worst status across both bases and both solver families.
- Existing condition/residual/count summary values retain their established standard-basis meaning; `summaryBasis="standard"` makes that explicit.
- Added `basisAvailability` with expected stage-segment count, present/unavailable/fallback/blocked global counts, unavailable wall-solver count and basis status.
- Missing legacy design/wall-solver evidence remains unavailable/manual review rather than proving pass.

Two real-domain regressions reproduced design-only failures (2 failed / 13 passed before implementation). Final focused verification: **15 unknown-output tests passed in 0.28s**, including a healthy-two-basis control proving the new availability guard does not block complete solved evidence. Scoped `git diff --check` clean. No monitoring or root-owned modules were touched.

## R11 reviewer follow-up: numerical gate status

The reviewer's warning-only counterexample reproduced for both standard and design bases: finite solved responses, conditionGrade=pass, numericalGate.status=warning/blocked=false/regularized=true, residual 1e-7. Both parametrized regression cases failed before the one-line fix. `_basis_availability` now includes the solver's numerical_gate.status in worst-status aggregation; no new threshold was introduced. Final focused run: 17 unknown-output tests passed; scoped diff check clean.
