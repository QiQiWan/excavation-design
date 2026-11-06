# Calculation reliability implementation plan

> For agentic workers: execute the approved six-task scope with systematic debugging, regression tests, and independent review. The user has authorized implementation; proceed without another approval gate.

**Goal:** repair honest states, frozen-design recalculation, common loads, known mechanics defects, construction events, and final-envelope/member-design consistency.

**Architecture:** Keep the existing API/task/React application. Introduce one immutable calculation boundary and a shared stage load contract, repair source numerical defects, and generate member design only after final stage responses. Preserve strict formal-issue protections.

**Tech Stack:** Python, Pydantic, NumPy, FastAPI, React/TypeScript, pytest, Vitest.

**Spec:** The approved audit is `/workspace/scratch/fd5d8a28c64e/audit/report_content.md`, sections 3, 5, 7, 10.2 and 11. User explicitly selected six work areas in the latest request.

## Global constraints

- Work only in this isolated source copy; the uploaded source remains the audit baseline.
- No fabricated capacities, unknown-to-zero conversions, optimistic stability overrides, or removal of formal-issue gates.
- Frozen recalculation never modifies design geometry, materials, supports, reinforcement input, strata, or construction stages.
- Any derived reinforcement or design suggestions are result data; only an explicit adoption/design operation may change design inputs.
- A section/stiffness change requires a new solve before its forces or reinforcement can be declared current.
- Keep unsupported engineering methods explicit; this iteration does not certify full excavations or implement a general nonlinear geotechnical solver.
- Tests must assert physical/state behavior, not only source strings or function existence.

## Review focus

1. Zero excavation, dry conditions, nonzero water head and pressure-method changes must reach every solver consistently.
2. Changed internal support stiffness must affect displacement and recovered reaction from the same assembled model.
3. Missing or over-limit numerical results cannot become closed/valid through warning labels or null coalescing.
4. Calculation runs must retain their input revision and leave the input design semantically unchanged.
5. Coupling updates and member geometry changes must invalidate/rebuild final envelopes and downstream reinforcement.

## Tasks and ownership

### Task 1 Honest engineering states

Files: `services/api/app/services/intelligent_design_closure.py`, `core_engineering_presentation.py`, `apps/web/src/viewers/RetainingSystemViewer.tsx`, `pages/CoreProjectWorkspace.tsx`, related focused tests.

- [x] Capture failing tests for missing capacity, over-limit warning and unknown/current status.
- [x] Normalize quantitative failure/unknown independently of informational warnings; remove derived capacity fallback.
- [x] Keep engineering/result-version/execution meanings distinct; wire the agreed verify command in the primary calculation button.
- [x] Run focused backend/frontend tests and report changes.

### Task 2 Frozen recalculation boundary

Owner: root. Files: `calculation/engine.py`, calculation API/tasks/services callers, calculation contract helper and focused tests.

- [x] Add a failing test comparing semantic input before/after a run with hand-set wall toe, wale section and stages.
- [x] Add an explicit verification entry point working on isolated inputs and publishing result-derived design separately.
- [x] Avoid hidden auto-embedment, auto-strength resizing, topology replacement and stage regeneration in verification mode.
- [x] Preserve reproducible input/run evidence and ensure task result application cannot overwrite newer inputs.

### Task 3 Common stage loads

Files: `calculation/earth_pressure.py`, `rules/jgj120_2012/earth_pressure_rules.py`, new `calculation/stage_loads.py`; root integrates engine and reaction iteration.

- [x] Fail tests for zero-depth support, pressure setting consumption, water/soil treatment, unit-consistent load combinations.
- [x] Define one typed stage load record with reproducible pressure identity, standard/design component profiles and supported-method restrictions.
- [x] Wire selected active/at-rest and water/soil settings; separate permanent soil/water and surcharge components for combinations.
- [x] Document exact integration signatures for root; compare dry/wet/surcharge analytic values.

### Task 4 Mechanics fixes

Files: `wall_internal_force.py`, `global_coupled.py`, `wale_beam.py`, `retaining_wall_rules.py`, `stability_rules.py`; focused mechanics tests.

- [x] Reproduce audited stiffness, passive-overburden, upward-stability and final-DOF residual failures.
- [x] Consume the exact same spring values in assembly and force recovery, and accept a precomputed pressure profile.
- [x] Apply soil factor consistently; remove post-solve DOF mutation and report meaningful final equilibrium.
- [x] Correct passive overburden origin; remove optimistic stability replacement and mark unvalidated screening appropriately.
- [x] Repair bounded unit-width/projection defects without inventing a verified full spatial solver.

### Task 5 Construction events

Owner: root. Files: `engine.py`, `reaction_iteration.py`, stage validation/helper and schema where necessary.

- [x] Reproduce zero-depth substitution, missing pre-install event and premature slab activation.
- [x] Make generated pre/post installation explicit and preserve user-provided event sequences in verify mode.
- [x] Activate permanent restraints only on explicit transfer/slab conditions; retain design conditions separately from future site execution proof.
- [x] If full path-dependent analysis is unavailable, mark that method limitation rather than claiming implemented history.

### Task 6 Final envelopes and reinforcement

Owner: root. Files: `engine.py`, final result aggregation/helper, reaction iteration and focused tests.

- [x] Capture a meaningful case where coupling changes stage responses and assert all final derived values follow it.
- [x] Finish coupling before unified envelope/member design; regenerate governing checks from final results.
- [x] Ensure immutable runs expose derived design without mutating inputs; changing stiffness never freezes old forces with new sections.
- [x] Run integrated backend tests, frontend tests/build and a real API or service calculation flow; record unresolved method boundaries.

## Verification and delivery

- [x] Collect a baseline and preserve old failures separately from introduced failures.
- [x] Review task diffs for scope and physical meaning, then run whole-project suites within practical execution limits.
- [x] Package a clean update with a reproducible verification command, changed-file inventory, result summary and remaining engineering limits.
- [x] Save the deliverable and return actual test evidence; do not label the package construction-qualified.

## Final checkpoint

V3.87.16 delivered with 145 focused backend tests, 19 focused frontend tests, successful production build and independent review. The full frontend suite retains the same 10 baseline failures; the full backend HTTP suite could not complete because the baseline TestClient/AnyIO bridge hangs in this environment. Model limitations remain blocking. See docs/releases/V3_87_16_CALCULATION_RELIABILITY.md and V3_87_16_VALIDATION.json.
