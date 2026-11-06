# Independent calculation-reliability review

Repository: `/workspace/scratch/fd5d8a28c64e/work/foundation_design`  
Branch: `calculation-reliability`  
Baseline: `76295ab`  
Review scope: approved six-area calculation reliability plan; uncommitted worktree.
Final reviewed checkpoint: 2026-10-25, version 3.87.16, after the owner declared the R11 backend and UI corrections frozen.

## Review status

The reviewer found nine concrete integration defects. All nine have been corrected and independently checked by focused regressions, direct real-solver reproductions, or the explicitly identified static call-site check. The owner then identified two adjacent issues: R10, involving failure of only the design-load wall solve, and R11, involving numerical-health aggregation and display. The reviewer confirmed both and found a downstream null-demand sizing crash under R10 and omitted solver-warning propagation under R11. All eleven findings, including these adjacent paths, are now corrected and independently verified.

**Final outcome: 145 focused backend tests passed in 10.60 seconds, 19 related frontend component tests passed in 2.08 seconds, and `git diff --check` passed. No unresolved Critical or Important counterexample was found in the approved review scope.** Real repeated verification also preserved manually supplied inputs and an existing result history. No further production change is requested by this review. The prior 141-test result remains in the report as R10 checkpoint evidence.

The original defects and their evidence remain below as an audit trail. The resolution paragraphs describe the corrected worktree, not the original baseline. This is a bounded code and regression review, not certification of the engineering model.

No production or test files were edited by this reviewer. This report is the only reviewer-authored file. The review does not request a full nonlinear solver or treat the bounded model as construction-qualified.

## Findings

### R1 — Important: failed FD solve crashed while converting fallback points

Location at discovery: `services/api/app/calculation/stage_solver.py:22–26,108`; producer `wall_internal_force.py:47–74,263–267`.

The real FD failure path returns the simplified span estimate with `depth`, `moment`, `shear`, and null displacement, but without an `elevation` key. The new `_wall_points` adapter indexed `p["elevation"]` directly. A singular or nonfinite wall solve therefore raised `KeyError('elevation')` before the pipeline could publish its intended unavailable/partial evidence.

Reproduction: use `test_stage_solver_consistency.fixture()`, wrap only the stage wall solver call, and temporarily replace `wall_internal_force.np.linalg.solve` with a function raising `numpy.linalg.LinAlgError`. Run the real wall function under that injection. The trace ended at `_wall_points`, reached through `solve_stage_response` line 108. The global solver was not injected and remained real.

Required correction: derive fallback elevation from the actual wall-top elevation and depth, retain null displacement and fallback method, and do not label this a successful FD response.

Resolution verified: `_wall_points` derives missing elevation from wall-top elevation minus depth, while preserving unknown displacement. `test_fd_fallback_without_elevation_preserves_unknown_displacement` passes in the independent targeted run.

### R2 — Important: a consumed transfer execution flag was absent from input identity

Location at discovery: `services/api/app/services/calculation_assurance.py:22–38,148–171`; consuming branch `services/api/app/calculation/engine.py:2121–2122`.

`calculation_input_payload` recursively removed all `layoutSummary`, but the engine used `retaining_system.layout_summary["transferSystem"]["required"]` to decide whether to run reaction iteration and transfer-frame analysis. Changing that flag could change the solver path while preserving all declared calculation identities.

Reproduction on `test_calculation_pipeline_reliability.project_fixture()`: set the flag to false, capture the full-design hash, solver-configuration hash and calculation-contract input hash, then set it to true. All three equality comparisons returned true.

Required correction: include the consumed execution flag in the frozen payload or derive execution solely from physical inputs that already belong to identity.

Resolution verified: the consumed transfer execution flag is explicitly retained in the frozen calculation payload. `test_transfer_execution_flag_is_part_of_frozen_input_identity` passes and checks all three identities.

### R3 — Important: displacement convergence used metre values with an inappropriate 1.0 floor and mislabeled absolute units

Location at discovery: `services/api/app/calculation/reaction_iteration.py:35–44,87–90,193,213–228,234`.

The same relative-difference helper served force and displacement. Its denominator was `max(abs(previous), 1.0)`, while global displacement was in metres. As a result, the nominal 0.01 displacement tolerance allowed up to 10 mm of change whenever the previous displacement was below 1 m. The absolute difference was also serialized under an `...Mm` key without conversion from metres.

Exact example: 0.001 m to 0.009 m returned relative delta 0.008 and passed tolerance 0.01, despite an actual relative change of 8.0. It published absolute delta 0.008 mm although the change was 8 mm. Force convergence can independently pass, so this is not merely a display defect.

Required correction: a displacement-specific convergence calculation with declared dimensional floor/tolerance and conversion of absolute values to mm.

Resolution verified: `_displacement_deltas` converts metre-valued translations to mm and applies a 1 mm dimensional floor. `test_iteration_displacement_units_cannot_hide_large_relative_change` passes: the example returns relative change 8.0 and absolute change 8.0 mm.

### R4 — Important: absent current wale/transfer results retained historical member designs

Location at discovery: `services/api/app/calculation/engine.py:624–626`.

`_design_wale_beams` immediately continued when a beam had no results in the current solve. The isolated trial began as a copy of the prior retaining system, so an old `design_result`, `design_moment`, `design_shear`, and `internal_force_results` could survive and then be published within the new `derivedDesign` proposal. This matters for members inactive throughout the saved case or for missing current transfer-member evidence.

Reproduction: place a prior `WaleBeamDesignResult(max_moment_design=999, check_status="pass")` and `design_moment=999` on a fixture wale, then call `_design_wale_beams(project, [], 1.0)`. It returned no checks and preserved the passed 999 kNm demand.

Required correction: clear stale derived force/design fields for members without current contributors and publish explicit unavailable/not-active evidence. Keep supplied reinforcement input distinguishable from a new proposal.

Resolution verified: a member without current contributing results has its prior force/design output cleared and receives an explicit manual-review check. `test_wale_without_current_stage_result_cannot_retain_old_pass` passes; the stale 999 kNm passed result no longer survives.

### R5 — Important: design-load solver failure could still produce passing calculation assurance and eligibility

Location at discovery: `services/api/app/services/calculation_assurance.py:454–490,537–565,624–665`.

The assurance collector inspected `row.global_coupled_result` only. It did not require or inspect the new `design_global_coupled_result`. Thus a complete set of good standard-load outputs could mask unavailable/fallback design-load solves in `RESULT-NUMERICAL-QUALITY`, `RESULT-FINITE`, and eligibility fields.

Reproduction: create all expected stage/segment rows with finite standard wall outputs, standard global displacement 0.001 m, scaled condition 1 and residual 0.0. Give every design global result `fallback=True` and `max_wall_displacement=None`. Supply a truthful input-audit stub and one complete trace row explicitly marked fail with `loadBasis="design"`. `assess_calculation_result` returned `status="pass"`, `eligibleForEngineeringUse=True`, and `eligibleForOfficialIssue=True`; all five assurance issues passed.

The separate formal gate can still block from other checks. That does not make the quality/eligibility object accurate, and existing consumers read those eligibility fields directly.

Required correction: require finite, usable numerical evidence for both standard and design load bases; preserve failed/missing design evidence and disallow successful eligibility.

Resolution verified: assurance now requires per-row condition and residual evidence for both standard and design global solves; missing/fallback design evidence fails numerical assurance. Engineering failures/review states affect overall assurance and successful eligibility is restricted to a passing overall result. `test_assurance_rejects_failed_design_solve_and_engineering_failure` passes.

An independent follow-up reproduction also removed only design condition numbers from 16 otherwise finite rows, leaving valid standard conditions, both residuals and displacements, and no engineering failure to mask the outcome. It reported 16 missing-evidence entries, overall fail, and both eligibility flags false. Missing condition evidence can no longer be supplied by another row or load basis.

### R6 — Important: post-iteration compatibility summaries still exposed initial reactions

Location at discovery: `services/api/app/calculation/engine.py:2032–2060`; `reaction_iteration.py:167–187`; `final_checks.py:107–110`. User-visible consumer: `apps/web/src/viewers/ResultViewer.tsx:832`.

The reaction loop replaced the authoritative global and wall objects, but updated only `solveEvidence`, restraint audit, iteration data, and selected wall summary values in `coupled_system_result`. Initial values for `globalMaxSupportAxialForce`, `globalMaxWallDisplacement`, and `globalEquilibriumDiagnostics` remained. The result viewer prefers the copied support-force field over the final global object's value.

Real reproduction on `test_v3_71_0_numerical_coupling._project()`: wrap the real iteration function to compare summaries against returned authoritative objects. The iteration passed in two steps, yet there were 108 mismatches across those three copied fields. One support-force summary stayed at 68.635 kN while the final value was 39.623 kN; another stayed at 560.025 kN versus 381.543 kN. A corresponding displacement stayed at 0.00014027 m versus 0.00020403 m.

Required correction: rebuild all copied solver summaries from final objects after the last solve. Current solve IDs must not accompany old numerical summaries.

Resolution verified: `refresh_final_stage_checks` rebuilds copied global summaries, wale summaries, and the wall placeholder from final response objects. The real transfer-case regression passes and checks the copied support force, displacement, equilibrium diagnostics, wall demand, and solve/load identity against authoritative final objects.

### R7 — Important: actual wale stiffness was absent from the shared solve identity

Location at discovery: `services/api/app/calculation/stage_solver.py:47–55,98–103,148–156`.

The shared `stiffnessHash` included walls, supports, settings, segment geometry and soil, but not the actual wale beams consumed by `estimate_support_axial_forces`. Returned wale results received the same solve ID despite physical changes to their own model.

Real reproduction: add a `WL-1-F1` wale with width/height 0.8 m to the stage fixture. Solve, change its height to 1.2 m, and solve again. Standard wale deflection changed from 0.003028 m to 0.000897 m while both `solve_id` and `stiffnessHash` stayed identical.

Required correction: include the consumed wale geometry, material and section in the shared solve identity, or give the independent wale model a separate complete identity.

Resolution verified by a fresh direct real-solver rerun: actual wale beams are now part of the stiffness input. Changing the same beam height from 0.8 m to 1.2 m changes both `stiffnessHash` and `solve_id`, alongside the expected deflection change from 0.003028 m to 0.000897 m. This was a direct reviewer assertion, rather than a newly authored repository test.

### R8 — Important integration gap: synchronous industrial closure retained an implicit design-mode call

Location: `services/api/app/services/industrial_readiness.py:341`, `execute_full_industrial_closure`.

This explicit closure operation still called `run_calculation(project, None, auto_repair=True)` without specifying design mode. With the new verify default, a project without saved cases now raises instead of performing the caller's authorized design workflow; on existing cases `auto_repair=True` is overridden. The background closure callers were updated, but this synchronous counterpart remained implicit.

Required correction: explicitly select design mode for this authorized design operation, or intentionally document/remove the operation. Do not change the ordinary verification default.

Resolution verified by static call-chain inspection: `execute_full_industrial_closure` now supplies `mode="design"` explicitly. The default ordinary verification path remains `verify`. This one-line integration correction was inspected, not independently exercised as a full industrial-closure runtime scenario.

### R9 — Important: minimum furnished steel area did not imply minimum capacity across different grades

Location: `services/api/app/calculation/final_checks.py::supplied_wall_rebar`, and the newly added `supplied_beam_checks`.

Both helpers selected the supplied face/zone with minimum steel area and then calculated capacity using that group's grade. This is only conservative when the steel grades are the same. The supported input model permits different grades.

Reproduction using the repository's own capacity function for a 0.8 m C35 wall: D20 at 200 mm spacing, HRB500, was selected and gave 484.828 kNm/m; the other supplied face, D20 at 180 mm spacing, HRB335, gave only 374.019 kNm/m. A demand of 400 therefore passed the selected group while exceeding the other supplied face's capacity.

Required correction: choose the minimum calculated capacity across supplied faces/zones and grades, or reject ambiguous applicability with manual review. This does not require a complete section analysis.

Resolution verified: supplied wall and beam groups are selected by minimum calculated flexural capacity using each group's actual steel grade. `test_supplied_reinforcement_selects_lowest_capacity_across_different_grades` passes for the wall.

The reviewer separately exercised `supplied_beam_checks` with a 0.8 m by 0.8 m C35 beam and the same two supplied groups. Candidate capacities were 387.8624 and 299.2152 kNm. A 320 kNm demand correctly selected 299.2152 and failed. With no supplied stirrup evidence, shear remained manual review with null capacity.

### R10 — Important, owner-found: design-only wall fallback lost its failure identity

Location at confirmation: `services/api/app/calculation/stage_solver.py:105–116`; `services/api/app/services/calculation_assurance.py:454–570`.

The independent global and wall solves can fail separately. When only the design-load FD wall solve failed, `design_raw` supplied its simplified fallback moment/shear and points directly to the official design fields. `WallInternalForceResult.method` described only the standard wall method, and the raw design-wall response was not retained in `StageCalculationResult`. Assurance inspected both global solves but its FD displacement comparison only covered the standard solve. A successful design global solve therefore did not establish that the reported wall-design demand came from a successful FD wall solve.

The owner identified this adjacent failure path at the final checkpoint; the reviewer independently confirmed the data-flow omission. Required behavior is explicit per-basis wall evidence and unavailable official design demand when the design wall solve fails. Merely retaining a warning string does not establish usable design demand.

Resolution verified: `wallSolvers` records standard and design wall method, availability, fallback and warnings separately. An unavailable design wall solve yields null official design moment/shear and no design points. Final checks, assurance, and reaction iteration inspect wall-solve availability as well as global evidence. A missing final wall envelope clears stale wall-design results, retains input bars, publishes failure evidence, and skips sizing.

Independent red/green evidence: the stage-only injected failure test first failed with a published design moment of 1243.62 kNm/m, then passed after per-basis availability and null design-demand propagation were added. The reviewer then injected the same design-only failures into the complete real rectangular-project pipeline. That exposed a downstream `abs(None)` at `engine.py:2490` through `design_rectangular_flexural_reinforcement`, because the final design envelope correctly became null but sizing still ran. The owner added the complete-path guard and full-pipeline regression. Both `test_design_fd_failure_cannot_publish_simplified_envelope_as_solved_design` and `test_failed_design_wall_solve_is_published_as_incomplete_without_rebar_proposal` passed in the independent R10 141-test run and the final 145-test run. The full-pipeline test verifies immutable supplied members, null design moments, partial wall placeholders, absent wall-design proposals, a failed final-envelope check, failed numerical assurance, and engineering ineligibility.

### R11 — Important, owner-found: numerical-health summary hid design-basis failure and rendered unknown metrics as zero

Locations: `services/api/app/calculation/result_enrichment.py::_numerical_health` and `_stage_rows`; `apps/web/src/viewers/ResultViewer.tsx::CalculationHealthPanel`.

The generic numerical-health summary inspected only standard global results, so successful standard evidence could mask an unavailable design global or design wall solve in the health badge. Separately, the result viewer formatted null condition numbers and residuals through a zero-default helper, displaying apparent numerical perfection for an unavailable quantity. Missing fallback/block counts and reaction-iteration count were also defaulted to zero in the same panel.

Required correction: aggregate availability and numerical state for standard and design global/wall solves; explicitly identify retained numeric summary fields as the standard basis; render null, missing and nonfinite health metrics as unavailable while retaining genuine numeric zero.

The reviewer found a further state-propagation omission in the initial correction: `condition_grade.status` and blocked flags were propagated, but `numerical_gate.status` was omitted. Direct reproduction with finite standard/design outputs, solved walls, a passing condition grade, and design `numerical_gate={status: warning, blocked: false, regularized: true}` plus residual `1e-7` still returned passing design-basis and overall numerical health. This requires propagation of the solver's existing warning, not an additional threshold or numerical model.

Resolution verified: `_basis_availability` now evaluates both standard and design global-system availability, blocked/fallback state, condition grade and numerical gate status, along with explicit wall-solver evidence. Both stage status and generic numerical-health status take the worst applicable state. The retained numerical summary fields are explicitly labeled `summaryBasis="standard"`; a successful standard result can no longer erase a design failure or either solver's warning.

The viewer's health block now renders null, missing and nonfinite quantities as `未计算`; finite zero remains numeric zero. The standard-action summary label is shown together with the statement that status includes both standard and design solves. Independent tests include the two design-unavailability cases, the parameterized standard/design numerical-gate warning cases, five unknown-value component cases (null, undefined, NaN, positive infinity and negative infinity), actual-zero rendering, and the basis label. All pass in the final 145-backend / 19-frontend focused runs.

## Additional check resolved without a new finding

The initial transfer-frame review suspected that the full retaining system might retain inactive radial supports. Inspection of `planar_transfer_frame._build_level_model` showed that radial supports are filtered by the force-map keys, and the reaction loop rebuilds those maps from active stage responses. That suspected defect is not present in the reviewed path.

The real transfer fixture also exposed the end-of-run solver-configuration guard rejecting changes to derived ring-beam analysis status/torsion fields and node spatial detailing. The owner independently identified the same generated fields and adjusted the solver-only projection. This is tracked as an owner correction, not a duplicate review finding; source-input immutability must remain protected.

A zero design reaction is now retained as zero when building transfer design force maps; it is no longer replaced by a truthiness fallback to the standard reaction. The final standard/design transfer envelopes remain separate.

## Snapshot and persistence boundary review

The reviewed wrapper deep-copies geometry, geology, materials/settings, retaining members, saved cases, and design control stages. Verify publishes proposal/result evidence without adopting retaining-system or case mutations. Required missing saved cases fail instead of triggering hidden generation. The model limitation for simultaneous passive pressure and elastic soil springs is an explicit blocking fail, and independent linear stage-history limitations remain manual-review evidence.

The API/task verification path captures revision before canonical load, uses `require_for_calculation(..., migrate=False)`, bypasses repair-capable qualification and closure, attaches metadata before the final assurance hash, and saves using the captured revision. SQLite checks the revision under `BEGIN IMMEDIATE`, so a concurrent edit cannot be overwritten by a stale verification save. `mark_calculation_state_current` modifies derived metadata only.

The case selector preserves saved cases, validates stage events, compares semantic controls without synchronization, and implements the expressly tested priority for a locked user case. The reviewed repository save path performs no load-time migration.

The reviewer also ran the actual numerical pipeline twice on the same project after manually changing wall-toe elevation, supplied wall reinforcement to HRB335 D20 at 180 mm, wale width, a locked user-defined case, and final-stage surcharge to 33 kPa. Each run returned 16 stage/segment rows. A deep JSON comparison of the entire caller project, excluding only derived `advanced_engineering`, result history, and `updated_at`, remained identical after both runs. The full input identity and each result's before/after hashes remained identical. Final support-force/displacement summaries and solve/load consumer identities agreed. Both runs retained failed assurance and disallowed official issue, as required by the stated model limitations.

At the R10 frozen checkpoint, a separate real-pipeline check retained the first result in `project.calculation_results` before running verify again. This comparison excluded only `advanced_engineering` and `updated_at`, so it included the complete existing result history. The entire remaining project remained deeply equal, the prior result was still the same object with unchanged contents, and all frozen input hashes remained identical. Thus the shallow sharing of read-only result histories did not mutate the retained history on this exercised path.

## Verification evidence

- Initial reviewer command: `../.venv/bin/python -m pytest services/api/tests/test_calculation_pipeline_reliability.py services/api/tests/test_stage_solver_consistency.py -q` from repository root. It observed **12 passed, 3 failed in 6.32s** while owner red regressions were being added. Two failures were the deliberately captured R1/R2 regressions; the third was the transfer generated-field configuration guard described above.
- Stable API boundary command: `../.venv/bin/python -m pytest services/api/tests/test_verify_api_contract.py -q --tb=short`. Result: **15 passed in 0.65s**.
- Independent integrated retest after the nine reviewer fixes: `../.venv/bin/python -m pytest services/api/tests/test_calculation_pipeline_reliability.py services/api/tests/test_stage_solver_consistency.py services/api/tests/test_verify_api_contract.py -q --tb=short`. Result: **34 passed in 8.11s**.
- Expanded new-test run: `../.venv/bin/python -m pytest services/api/tests/test_calculation_mechanics_reliability.py services/api/tests/test_calculation_pipeline_reliability.py services/api/tests/test_engineering_state_truth.py services/api/tests/test_stage_load_contract.py services/api/tests/test_stage_solver_consistency.py services/api/tests/test_unknown_calculation_outputs.py services/api/tests/test_verify_api_contract.py -q --tb=short`. Result at the first frozen checkpoint: **122 passed, 2 failed in 8.56s**. Both failures were `SimpleNamespace` test fixtures missing the `wale_beam_results` field now read while rebuilding final summaries; the real `StageCalculationResult` declares that field with an empty-list default. These fixtures were corrected to actual domain types without relaxing production behavior and pass in the final run. The related crown fixture now supplies independent design points and adds a missing-design-evidence regression that clears historical passed output.
- Direct real-solver reproductions established R1, R6, and R7. Direct deterministic identity/helper reproductions established R2/R3. Direct member and assurance reproductions established R4/R5.
- Direct post-fix assertions independently covered repeated real verify input/history immutability, R7 physical-stiffness identity, R5 missing per-basis condition evidence, and R9 mixed-grade beam capacity selection.
- `git diff --check` completed successfully at the reviewed checkpoint.
- No full pytest suite was run. The owner identified a baseline TestClient/AnyIO hang and instructed focused testing. The API tests call the registered route and real task orchestration directly; they do not certify HTTP transport.

### R10 stable-checkpoint command

Executed independently from the repository root after the owner froze the R10 correction:

```bash
../.venv/bin/python -m pytest \
  services/api/tests/test_calculation_mechanics_reliability.py \
  services/api/tests/test_calculation_pipeline_reliability.py \
  services/api/tests/test_engineering_state_truth.py \
  services/api/tests/test_stage_load_contract.py \
  services/api/tests/test_stage_solver_consistency.py \
  services/api/tests/test_unknown_calculation_outputs.py \
  services/api/tests/test_verify_api_contract.py \
  services/api/tests/test_v3_55_0_intelligent_design_closure.py \
  services/api/tests/test_v3_87_5_optimization_search.py \
  services/api/tests/test_v3_87_6_unified_auto_closure.py \
  -q --tb=short
```

Result: **141 passed in 9.18s**. The same checkpoint's `git diff --check` returned exit code 0 with no output.

### Final R11 stable-checkpoint verification

The same ten-file backend command above was independently repeated after both R11 implementations were declared frozen. Result: **145 passed in 10.60s**. The four additional cases cover design global failure, design wall unavailability, and standard/design solver-warning propagation.

From `apps/web`, the reviewer also independently ran:

```bash
npm test -- \
  src/viewers/ResultViewer.test.tsx \
  src/pages/CoreProjectWorkspace.test.tsx \
  src/viewers/RetainingSystemViewer.test.tsx
```

Result: **3 test files passed, 19 tests passed in 2.08s**, including the seven new result-health component cases. Final `git diff --check` returned exit code 0 with no output. No additional blocking finding emerged from the narrow R11 code review.

No full-backend-suite, full-frontend-suite, or HTTP-transport pass is claimed. The passive-pressure/elastic-spring compatibility block, independent linear stage-history limitation, absent vertical-load path, and other explicitly unavailable engineering evidence remain limitations of the model and are not converted into certification by this review.
