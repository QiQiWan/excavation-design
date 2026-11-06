# P2 complementary-boundary correction and final kernel verification

This note supersedes the earlier all-boundary-branches-released stability gate. Product changes are limited to `app/section/solver.py`, `app/section/independent.py` and their two test files. The UI, exports, pressure preprocessor and constitutive parameters were not changed by this correction.

## Finding and physical criterion

The original release gate wrongly rejected a stable state when opposed springs were both exactly on a pressure limit. For two springs with `p0=lower=10 kN`, `upper=100 kN`, `k=1000 kN/m`, opposite orientations produce an aggregate restoring force of −1/0/+1 kN at wall displacements −0.001/0/+0.001 m. They therefore provide positive restoring stiffness in both directions even though neither individual branch has bilateral stiffness at the corner. The analogous opposed upper-bound case is also stable. Simply deleting both corner branches is incorrect; treating both as full bilateral springs is also incorrect.

A connected Euler–Bernoulli beam with positive EI has zero bending energy only for an affine rigid displacement `v(z)=a+b*xi`, where `xi=(z−mid_depth)/beam_length`. Every nodal row `[1,xi]` and every candidate direction is normalized. For a direction to have zero incremental restoring energy:

| Actual branch at the converged trial state | Required zero-stiffness condition |
|---|---|
| Strict interior elastic soil or bilateral elastic support | `v=0` |
| Exact displacement constraint | `v=0` (and fixed toe also `b=0`) |
| Lower soil limit with side orientation `s` | `s*v<=0` |
| Upper soil limit with side orientation `s` | `s*v>=0` |
| Just-touching compression-only support | `v<=0` |
| Strictly exceeded pressure bound or open contact | No small-displacement constraint |

These equalities and half-planes describe directions with **zero** stiffness, not an averaged force law. At equilibrium the linear work term vanishes. Every bending, interior elastic, or engaged one-sided contribution to incremental energy is nonnegative; hence a nonzero feasible direction gives a locally flat potential and a nonunique displacement, while a cone containing only zero excludes such a mechanism in this discrete incremental model.

If the bilateral equalities have rank two, there is no nonzero rigid direction. At rank one only the two signs of its null vector need checking. With no bilateral equality, a nonzero closed polyhedral cone in two dimensions contains a boundary ray of one of its half-planes (or it is the whole plane when no half-plane exists), so the two perpendicular directions of every half-plane normal provide a complete candidate set. This test catches both complementary restraints and genuinely unconstrained plastic platforms.

The primary implementation uses NumPy arrays; the independent implementation uses its own material states and pure two-dimensional scalar geometry. The independent adapter imports no primary stability helper, beam matrix or solved response.

## Tolerances and scope

The normalized direction feasibility tolerance is `1e-12`. Near-bound classification uses `64 * float64_epsilon * local_force_scale`, with the scale including pressure bounds, p0 and elastic-force terms. This tolerance acts only from the interval's interior: a strictly exceeded bound is never promoted into a one-sided corner. The same conservative interior-side rule applies to a tiny positive contact closure. Degenerate or unresolved cases continue to fail explicitly. These are numerical classification rules for the stated discrete spring model, not continuum geotechnical stability criteria.

The exact-oracle review is separately reproducible with `verify_soil_direction_cone.py`. Its oracle uses Fraction interval feasibility with `a=+/-1` or `b=+/-1`, not the production perpendicular-ray procedure. The final-source rerun checked **1,029** configurations at three rational depths and free/pinned/fixed toe conditions: **743 closed cones, 286 cones with a nonzero direction, zero mismatches**. Both whole-file and checker-specific hashes are recorded in `soil_direction_cone_probe_result.json`. This probe does not enumerate compression-only support configurations or establish conditioning bounds for arbitrarily close depths.

## Actual OpenSees corner solve

In OpenSees 3.8.0, complementary upper-bound materials can all select a zero current tangent at the initial point, making the first linear solve singular despite the stable one-sided model. Real native probes found that `Newton -initialThenCurrent` still failed on the first iteration in this case. The adapter therefore retains NewtonLineSearch for its normal solve, and if that attempt fails it wipes the native model and rebuilds solely from its own previous committed plastic offsets and installation references. A single full-load-step retry uses `ModifiedNewton -initial`.

This uses the native initial material tangent only as the search matrix. Every residual remains the actual bounded ElasticPP force, and every final result must satisfy equilibrium and the direction-cone check. No spring, artificial support, averaged pressure force, load substep or trial history transfer is added. Zero load at the complementary upper corner converged in one native iteration; +/-1 kN examples converged in 26 iterations. Their displacement error was 1.49e-11 m, within the existing force criterion's 4e-11 m implication for the analytical 1000 kN/m aggregate stiffness. The new assertion uses 5e-11 m; the production force criterion was not loosened.

Primary API source: [OpenSees Modified Newton Algorithm, §3.2.5.4](https://opensees.github.io/OpenSeesDocumentation/user/manual/analysis/algorithm/ModifiedNewton.html). The runtime probes, rather than documentation assumptions about implementation details, determine which native option is actually used.

## Fine-grid residual refinement

The newly accepted public `K0=Ka` input exposed one further numerical issue at 0.0625 m maximum element size. S0–S4 completed, but S5's active set and displacement stopped changing while its residual remained 5.2991121269e-8 kN, above the unchanged 3.0908672268e-8 kN criterion. Float64 could no longer retain the small correction in the total displacement. A new complete-history test first reproduced this failure.

The matrix and linear solves remain float64. The running displacement and centered beam-curvature evaluation now use `numpy.longdouble`, retaining the small correction in mixed-precision iterative refinement. Published JSON numbers remain regular float64. The force law, pressure bounds, EI, mesh, construction history, iteration limit and acceptance criteria did not change.

For this tested runtime, `np.finfo(float).nmant=52` and `np.finfo(np.longdouble).nmant=63`; these exclude the implicit leading significand bit. `longdouble` is platform dependent and may offer no extra precision on some platforms. This evidence makes no cross-platform precision guarantee; inability to meet the unchanged residual gate remains an explicit failed analysis.

## Final necessary verification

The final focused command, with the real OpenSees backend, was:

```bash
PYTHONPATH=/tmp/pitguard-soil-opensees:. python -m pytest -q tests/test_section_independent.py tests/test_section_solver.py
```

Result: **54 passed in 1.31 s**, comprising 27 primary and 27 independent tests. New tests cover both pressure-limit directions, zero and signed small loads, a three-station cone closed without co-located spring pairs, complete public `K0=Ka` history, and the corresponding fine mesh. Exact-capacity free/pinned platforms continue to fail and retain null curves.

Both complete six-stage fixtures were then solved once by the primary and actual independent solvers and checked with the unchanged per-value 0.1% comparison rule and 2% mesh-halving rule:

| Fixture | Snapshot prefix | Primary / independent stages | Independent comparison | 0.125 to 0.0625 m mesh | Largest relative peak change |
|---|---|---|---|---|---|
| Default verification example | `2223478185af` | All six complete | Passed | Passed | 0.9993404% |
| Public `K0=Ka`, zero cohesion | `37e09839b4f8` | All six complete | Passed | Passed | 1.0059110% |

The coarse-grid primary S5 nodal residuals were respectively 7.1751e-12 and 3.9608e-12 kN. All comparisons retain absolute floors alongside the stated relative tolerances. The underlying whole-stage results, mesh metrics, material history comparison, support reactions and current source hashes are in `soil_direction_cone_verification_current.json`. The force/moment global gate remains 1e-6 relative.

Final primary source SHA256: `5cf9f3820862259adb68c4c00736c0a75377187671f6020745eb2307580e35e1`.

Final independent source SHA256: `ae66d24601a1bac63cfd813572722032e24ab1f1b745f47c7fa743e257c2bb7b`.

The kernel sources are frozen at this handoff. Root owns the subsequent final integrated regression, frozen example and package. Historical failed or superseded records have names ending in `_history.json` and must not supply final curves or acceptance claims.
