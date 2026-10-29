# Independent read-only review of v2 method/state integration

2026-10-25. Reviewed method_models.py, models.py, preprocess.py, geotech_actions.py, relevant solver.py/independent.py state construction and constitutive formulas, benchmarks.py same_snapshot_hand_checks/_independent_surface_integral, and targeted existing v2/history/action tests. No shared product files changed. This is a bounded code/math review, not a replacement for root's final integration suite.

## Findings

### Low priority: two solver validators differ on a fixed-pressure spring

Primary solver._validate accepts kKnPerM=0 if lowerKn=p0Kn=upperKn. Independent._validate requires soil kKnPerM strictly positive unconditionally. Therefore a mathematically valid primary fixed-pressure station cannot be passed to the independent comparator under the same discrete contract. This behavior predates the new branch machinery and does not affect present preprocess output (m positive; all retained stations strictly below each side surface), so it is not a blocker for the described v2 path.

Minimal reproduction: take test_section_history_options.fixture('carry_plastic_history_v1'); for every stage set its sole soil spring to kKnPerM=0,p0Kn=lowerKn=upperKn=50. Primary _validate accepts; independent._validate raises ValueError('soil stiffness must be positive'). Observed directly. If general discrete-model parity is in scope, permit nonnegative k in independent validation only when all pressure bounds equal; its existing upper==lower path already treats pressure as a fixed external action and skips division by k. A regression should validate the resulting fixed-tip beam displacement/pressure in both solvers.

### No new blocking sign/state/math defect identified in inspected v2 path

The following conclusions are limited to inspected code and direct checks, rather than a claim that all domain assumptions are generally valid.

- v1 serializer explicitly omits three v2 keys, even when caller passes null; v1 rejects active v2 choices and soil_reset. Historical record re-read retained snapshot hash2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd. v2 requires complete methods and new pressure-method identity; analysis hash includes those choices and distinct algorithm version.
- Source layer flags select pressure interpretation at current station; they do not alter overburden integration through overlying layers. vertical_stress first integrates complete total weight and head, then subtracts pore pressure. Combined mode adds no separate water; separated mode adds exactly one external water term.
- Positive wall displacement is toward excavation. Soil pressure force on wall is -sign*p. Thus outside sign=-1 produces positive wall load, inside sign=+1 negative. In preprocess external water is also -sign*u; this agrees with residual convention. No duplicate separate water was found in combined layers.
- Finite q increments are initialized afresh for each side and material station, added only to outside surcharge, and then mapped through that station's K0/Ka/Kp. They are not additionally inserted into externalForcesKn. This remains a declared elastic vertical-stress-to-Rankine mapping, not a proven exact lateral-wall or original-FRWS method.
- Carry uses committed plastic offset, reset uses zero, rebase uses sign*u_previous so at the previous displacement its new soil pressure equals current p0. None of these modifies the beam displacement reference or support installation reference.
- Support installation stores previous converged displacement before solving its own zero-force installation event. Soil-reset events cannot coincide with support install/remove; all stage shifts use prior converged states. Primary commits plastic state after successful equilibrium/stability/equilibrium-resultant checks. Independent rebuilds from independently committed offsets; it does not consume primary state.
- OpenSees soil uses p=lower+ElasticPP(sign*u - eps0), eps0=plastic−(p0−lower)/k, with its lower pressure applied once as adjusted nodal load. This reconstructs the primary trial law p0+k(sign*u−plastic) with clipping. Native ElasticPP eps0 avoids the historical InitStrainMaterial plastic-initialization issue; support wrapper is reversible elastic contact only.
- The same-snapshot hand audit integrates water by actual separated-layer intervals; combined intervals contribute no external water. Its pressure worked rows use current active finite loads and actual layer stress basis, and it independently integrates the Boussinesq kernel after an angular substitution with48/96 convergence check. It checks representative stations rather than all pressures, but explicitly describes its limited hand-auditable scope; full nonlinear response is separately compared.

## Direct read-only evidence

Command: repository-root PYTHONPATH=services/api:services/api/tests with work/.venv/bin/python. Loaded historical record, constructed equivalent v1 and all-effective-separated v2 physical models, compared all stage external forces and every spring p0/k/lower/upper, and solved the closed-form one-tip fixture under each history policy.

Observed:

|Check|Result|
|---|---:|
|v1 historical hash|2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd|
|v1 vs explicit v2 max physical force/parameter difference|3.552713678800501e-15|
|carry: u/p/prior offset|0.09m /90kN /.05m|
|reset zero: u/p/prior offset|0.065m /115kN /0m|
|rebase: u/p/prior offset|0.14m /40kN /.15m|

Independent hand equation for all three is1000u+clip(50+1000(u−offset),20,150)=180 after first F300 stage committed u=.15,p150,plastic=.05. Values above follow directly from this equation.

Final gate responsibility remains root: section full suite, prior145 targeted regressions and required API tests. Agent's abandoned full backend run exited130 at root request and is not counted as successful verification.

## Root follow-up resolution (reported by root; original finding retained)

Root reported correction of the fixed-pressure validator mismatch after this review: independent validation now accepts k>=0 and requires lower=p0=upper when k=0, consistent with the primary physical contract. Existing native fixed-pressure branch performs no division by k. Root reports red-to-green verification with two force directions and invalid pressure-range coverage (three new tests). This agent has not rerun those new tests independently; root's final unified regression remains the final gate. The earlier observed mismatch is retained above as the audit trail of the issue and reproduction.
