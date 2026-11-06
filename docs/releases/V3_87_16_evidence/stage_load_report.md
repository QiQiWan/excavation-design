# Unified stage-load and pressure correction report

## Scope and API

Owned changes: `services/api/app/calculation/earth_pressure.py`, `services/api/app/rules/jgj120_2012/earth_pressure_rules.py`, new `services/api/app/calculation/stage_loads.py`, and `services/api/tests/test_stage_load_contract.py`. Engine, reaction iteration, schema, mechanics, and structural solvers were not edited by this agent. No commit was created.

`build_stage_loads(soil_profile, excavation_depth, groundwater_level_outside, groundwater_level_inside, surcharge, top_elevation, calculation_depth, settings, step=0.5) -> StageLoadRecord`

The record is a frozen dataclass storing immutable JSON strings. Exporting a profile or evidence produces a fresh object, preventing mutable nested Pydantic points from invalidating recorded IDs.

Properties:

- `standard_profile`: direct full-surcharge characteristic pressure for deformation/service response.
- `design_profile`: the explicit factored action combination below.
- `permanent_profile`: zero-surcharge net characteristic pressure, retained for inspection; **not** the operand to multiply by gamma G.
- `permanent_action_profile`: zero-surcharge outside soil plus outside water before resistance.
- `resistance_profile`: inside passive soil plus inside water, as positive resistance magnitudes.
- `surcharge_profile`: unclipped lateral surcharge increment K*q.
- `load_id`, `input_hash`: deterministic identity strings.
- `evidence`: JSON-compatible dict; `evidence_json`: equivalent immutable serialized string.

Evidence keys include `contract_version`, `inputs`, `factors` (original DesignSettings names), `methods`, `units`, `provenance`, `profile_ids`, `decomposition`, `components`, and `warnings`. `profile_ids` contains all six profile kinds. Identity incorporates source input values, soil parameters, declared provenance, units, methods, factors, and serialized outputs; no generated UUID/timestamp is used. GeologicalLayer has no source-document hash/confirmation fields, so the evidence explicitly states that document verification cannot be inferred.

## Combination and physical boundary

Let A_G be the zero-surcharge outside soil pressure plus hydrostatic water; R be inside passive soil pressure plus inside water; Q=K*q be the nonnegative uniform surcharge increment. Design pressure is:

`max(0, importance_factor * (load_gamma_g*A_G + load_gamma_q*load_psi*Q - R))`

The favourable resistance factor is 1.0 before applying importance. Thus gamma G does **not** amplify passive soil or inside water credit. Importance multiplies the complete net resultant; every lateral component in the design profile follows that convention. Characteristic vertical stresses remain source geostatic values and are explicitly labeled as such in evidence.

The variable component intentionally bypasses the active-soil tension cutoff and net-pressure clipping. For q>=0, `max(x+K*q,0) <= max(x,0)+K*q`. The action decomposition therefore supplies an upper bound at tension cutoff, and may overestimate characteristic full-q load when combined. `standard_equals_component_sum=False` is recorded explicitly. Standard response uses the direct full-q calculation rather than this upper bound.

This is a bounded effective-stress pressure model with Rankine active or at-rest outside soil, Rankine passive inside soil, hydrostatic water, fully mobilized passive resistance, and nonnegative total net loading. It is not a normative certification, coupled seepage analysis, displacement-dependent passive mobilisation model, or reverse-loading solution. Groundwater and effective strength assumptions still require engineering confirmation. No undrained strength is inferred.

`combined`/total-stress calculations now fail explicitly with an actionable error explaining that independently established total-stress strength and drainage assumptions are absent from the schema. This applies to the calculation wrapper and the compatibility helpers; the previous relabeling/reset of total pressure has been removed.

## Pressure repairs

- Excavation depth zero is valid when calculation depth is positive; negative/invalid depths are rejected without final-depth or epsilon substitution.
- Selected active/at-rest mode enters the actual coefficient formula; explicit k0 and Jaky fallback both operate.
- `active_earth_pressure` and `passive_earth_pressure` now mean soil-only components. `earth_pressure` is signed net soil; `water_pressure` is signed outside-minus-inside hydrostatic pressure; `total_pressure=max(0,earth_pressure+water_pressure)`.
- Soil tension is cut off before adding water, so cohesive tension does not erase hydrostatic pressure.
- Inside water above the pit base is included on the exposed wall.
- Vertical stress is integrated exactly between layer and groundwater boundaries instead of midpoint slicing. Ponded water weight at the current soil surface is included; submerged effective inside stress therefore remains physically consistent when water stands above the pit base.
- Passive-only profiles consistently use outside hydrostatic water and soil-only passive fields.
- Invalid/nonfinite pressure inputs, malformed soil thickness, nonfinite soil parameters, negative cohesion/unit weights, invalid friction angles/k0, unrecognised methods, and invalid/nonfinite combination coefficients are rejected.

Existing default parameter fallbacks are retained, with warnings/evidence: missing c=0 kPa, phi=30 degrees, gamma=18 kN/m3, missing saturated gamma uses natural gamma, missing k0 uses Jaky. Existing profile extension uses the final supplied layer beyond coverage. These are assumptions, not verified project parameters.

## Verification evidence

Python: `/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python`.

1. First pressure test run before production edits: **13 failed, 3 passed**. Failures reproduced mislabeled water/soil components, omitted inside water, hydrostatic loss at active tension cutoff, zero excavation rejection, combined/unknown method acceptance, invalid input acceptance, and groundwater-boundary integration error.
2. Pressure correction run: **16 passed**.
3. New contract behavior tests before introducing the module: **14 failed, 18 passed**, with the missing contract explicitly asserted.
4. Implemented contract: **32 passed**.
5. Focused final command: `python -m pytest tests/test_stage_load_contract.py tests/test_mvp.py::test_jgj120_rankine_pressure_formula_subset -q`: **33 passed, 1 warning** in 1.44s. The warning is the existing Starlette/AnyIO BlockingPortal deprecation, not a failed numerical check.

Tests include independent hand values for dry active/at-rest/passive, explicit k0, saturated soil, water above pit, signed water difference, ponded inside water, below-base resistance, each selected factor, passive resistance not amplified by gamma G, surcharge conservatism across tension cutoff, deterministic IDs, defensive immutability, invalid values, and baseline Rankine compatibility. `git diff --check` passed during final focused verification. Full-suite/integration execution remains with root by delegation scope.

## Read-only integration observations sent to root

At the time of review, water/heave/overall checks were passed final-pit embedment while using the current-stage pit elevation. They need `max(0, top-stage_depth-wall_bottom)` consistently. Zero-depth/no-surcharge heave used an epsilon driving force and generated an enormous pass margin; no-head water used a 999 pass. Explicit no-driving/not-applicable outcomes are preferable. Heave/water are independent simplified screens and do not consume the lateral pressure method; they must not be described as having the same lateral load contract. Embedment rebuilt active pressures irrespective of the project's at-rest setting. Root and mechanics owners were informed; these files were not edited here.

## Additional delegated pipeline regression

On root's request, `tests/test_stage_solver_consistency.py` was added using actual global, FD, and wale solves with a nonunit support stiffness factor. Its first run had 2 passes and 1 failing long-term EI regression (factor0.6 left EI unchanged). After root's fix, all **3 tests passed in 0.34s**. Tests verify global-to-wall effective spring consistency, exact standard/design pressure handoff without a second response factor, and long-term wall EI sensitivity. Review details are in `work/pipeline_peer_review.md`.

**Substantive remaining physical limitation:** the pressure model subtracts fully mobilized passive soil while the structural models additionally use below-pit elastic soil springs. Their constitutive compatibility is unvalidated and may double-credit inside soil. The API has intentionally not been altered to imply a one-line constitutive repair. Root is retaining formal gates and adding explicit blocking applicability evidence; these load-contract tests do not certify the coupled physical model.
