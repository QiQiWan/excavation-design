# Mechanics repair implementation report

## Scope and isolation

Implemented in `/workspace/scratch/fd5d8a28c64e/work/foundation_design`; original audit source unchanged. No commits. Exclusive production changes:

- `services/api/app/calculation/wall_internal_force.py`
- `services/api/app/calculation/global_coupled.py`
- `services/api/app/rules/jgj120_2012/retaining_wall_rules.py`
- `services/api/app/rules/jgj120_2012/stability_rules.py`

Added `services/api/tests/test_calculation_mechanics_reliability.py`. `wale_beam.py` needed no change: its existing projected default spring is now consumed once and consistently. Root and other agents own all other changes visible in the worktree.

## Integration contracts

`analyze_wall_on_elastic_foundation` adds trailing parameters in this order:

```python
pressure_method: str = "active",
water_soil_method: str = "separate",
lateral_restraints: list[dict[str, Any]] | None = None,
pressure_profile: PressureProfile | None = None,
```

A supplied `pressure_profile` bypasses pressure reconstruction completely. Fallback forwards the pressure method as wrapper `mode` and water method unchanged.

Explicit lateral restraints are dictionaries `{id, depth, stiffnessKnPerM}`. `depth` is metres below wall top; stiffness is a discrete full-face normal spring in kN/m, divided once by wall-face length. Optional `levelIndex` defaults to zero. They are never inferred from stage or removed supports inside the FD solver. Recovered records use `restraintSource="explicit_lateral_restraint"`, with `appliedDepth` identifying the actual mesh node. Zero stiffness is valid; negative/nonfinite stiffness and out-of-wall depths raise ValueError.

Global slab properties accept `active: bool` and `activationDepthM: float`. Plain `final` does not activate a slab. Explicit bottom_slab/replacement/support_removal stages can default a missing depth to the current pit bottom with `assumedExcavationBottom=True` and a source diagnostic. Other explicitly active stages require a supplied depth. Both solver branches apply the full-face slab spring exactly once at the nearest wall translation node. The component record reports `applicationCount`, `appliedWallNode`, `appliedDepthM`, and activation depth.

Global displacement/rotation profiles and authoritative DOF values are None after failed solve. `maxWallDisplacement` is None; selected spatial solver fallback/reason propagate outward. Vertical column DOFs stay at the actual solved zero and `loadRepresented=False`; no 3% axial reaction or post-solve DOF overwrite remains. On failed solve vertical reaction is None. Condensed `estimatedVerticalShare` is None. Downstream column design must remain blocked/unavailable, as coordinated with root.

## Repairs and evidence

1. Interior wall support overrides are now included in assembly as well as recovery. One effective stiffness map handles top and interior springs. Explicit overrides are not clamped; default shared helper bounds remain. Tests capture actual matrices at 0, 1e3, and 1e9 kN/m and compare the diagonal increment against reported spring stiffness. Zero reproduces the unrestrained wall response.
2. Previously ignored finite-difference boundary-row restraints (node 1 and the two toe derivative rows) now snap to the nearest assembled interior row; top-node springs retain their head condition. `appliedDepth` exposes the approximation. Tests cover near-head, near-toe and toe locations.
3. Spatial soil-modulus factor reaches actual soil springs. Both branches use full-face wall EI alongside full-face pressure and soil springs. Existing artificial endpoint restraints scale with face width; no new restraint was introduced. A no-support homogeneous face responds identically at 1 m and 10 m lengths.
4. Spatial support projection is applied once because the shared helper already returns projected stiffness. Tests inspect the assembled coupling entry for an inclined support.
5. Actual solved displacement vector is never mutated after equilibrium diagnostics. Authoritative serialized DOFs retain float precision so reconstructing them satisfies the same matrix (relative residual under 1e-8 in the synthetic test).
6. Embedment passive profile uses soil layers sliced at the current pit bottom and local top elevation/water origin, then shifts depth coordinates for moment integration. Dry homogeneous analytic integration matches the corrected resistance. Trace explicitly declares `unverified_screening` and `formalUseAllowed=False`.
7. Overall stability reports the original minimum candidate factor without the optimistic upward calibration. Thresholds remain unchanged. Both stability screens return `manual_review` rather than pass above threshold and fail below threshold; neither certifies the simplified method.
8. FD solve failure preserves None for all displacement profile values and maxima. Global failure null propagation is also covered.

## Red/green history

- Initial baseline: **12 intended failures**, recorded in `mechanics_red.txt`.
- After initial repairs: **12 passed**.
- Further audit: serialized rounded DOFs, global failed-solve zero output, and condensed slab omission: **3 failures / 15 passes**, recorded in `mechanics_red_second.txt`.
- After repairs: **18 passed**.
- FD slab integration, three boundary-row restraint cases, and retained assumed slab-depth metadata: **5 failures / 18 passes**, recorded in `mechanics_red_third.txt`.
- Final focused mechanics file: **23 passed**.
- Final run including three existing numerical conditioning/spatial-node regressions: **26 passed in 0.70s**, recorded in `mechanics_green.txt` (actual output is authoritative for elapsed time).
- Scoped `git diff --check`: clean.

Command from `services/api`:

```bash
/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest tests/test_calculation_mechanics_reliability.py tests/test_v3_71_0_numerical_coupling.py::test_scaled_solver_reduces_condition_number_and_preserves_solution tests/test_v3_71_0_numerical_coupling.py::test_scaled_solver_blocks_rank_deficient_stiffness_matrix tests/test_v3_71_0_numerical_coupling.py::test_reduced_spatial_node_solver_recovers_applied_moment -q
```

Root runs whole-project integration; this subtask intentionally did not run the expensive full suite.

## Limitations retained openly

The global model remains a spatial/condensed proxy, with empirical endpoint restraints, approximate wale stiffness and far-end restraint. These repairs establish internal consistency, not validated three-dimensional structural behaviour. No physical vertical load path exists. The finite-difference wall uses nearest-node support placement, with explicitly recorded boundary snapping. Overall/embedded stability screens remain unverified methods; raw candidate values are not normative certified factors. Existing common stiffness sensitivity-factor bounds remain. No target was relaxed and no new automatic slab activation was introduced.

## Follow-up integration: shared embedment pressures and zero-depth applicability

At root request, `check_embedment_stability` now adds trailing `pressure_profile: PressureProfile | None = None`, `pressure_method: str = "active"`, `water_soil_method: str = "separate"`. A supplied decomposed profile is consumed without pressure reconstruction. Driving above pit bottom uses active soil plus outside water minus inside water; embedded resistance uses passive soil plus inside water minus outside soil/water. The clipped net profile total is not misused as outside pressure. Default construction uses the current excavation depth (not wall depth) and forwards at-rest selection and explicit inside groundwater to the shared pressure helper. Legacy profiles lacking component fields retain the local pit-bottom passive slice fallback.

H=0 and zero driving moment no longer manufacture factor 999: check status manual_review, calculated_value=None, trace applicability=not_applicable. No heave/water changes made.

Two added tests failed before implementation (`mechanics_red_fourth.txt`) and then passed. Updated final focused result: **25 mechanics tests passed**; including three existing numerical regressions: **28 passed**, recorded in `mechanics_green.txt`. Earlier counts above describe intermediate runs. Scoped diff check remains clean.

## Final effective override contract

Peer integration identified that global reaction stiffness already contains support/projection/series effects. FD `support_stiffness_overrides_kn_per_m` values now mean **final effective full-face normal stiffness**: they receive neither support_stiffness_factor nor transfer_stiffness_factor again, only face-length distribution. Non-overridden springs retain their normal factors. A new nonunit support factor 2.0 / transfer factor 1.5 regression first failed (300000 vs expected 100000) and then passed. Final run: **26 mechanics tests + 3 existing numerical regressions = 29 passed in 0.69s**, with clean scoped diff check.

## Separate long-term FD factor

At root request, `long_term_stiffness_factor=1.0` is now an independent optional FD keyword immediately before `pressure_profile`; its [0.20,1] bound is separate from wall factor [0.25,4]. This matches global EI scaling and preserves a .25*.6=.15 product. New regression red→green. Current mechanics file contains 27 passing tests; combined with 8 null-result enrichment tests, 35 tests passed in the latest scoped run.
