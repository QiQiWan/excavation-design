# Frozen section mechanics review

Date: 2026-10-25. Reviewed the new worktree `foundation_section_validation`, principally `services/api/app/section/preprocess.py`, its frozen input contract, result recovery in `service.py`, and the independent adapter. This is an internal mechanics review of the explicitly bounded verification method. It does not certify a geotechnical design or reproduce the A3-3 source project.

## Current final-review status

The complementary-boundary P2 issue has been corrected in both kernels. Current focused verification is **54 passed**, with two complete six-stage same-input OpenSees comparisons and both mesh-halving checks passed. The final-source 1,029-case exact cone oracle has zero mismatches. The definitive correction, tolerances, mixed-precision platform limits and source hashes are in [soil_solver_p2_closure.md](soil_solver_p2_closure.md); current numeric evidence is `soil_direction_cone_verification_current.json`. Earlier review and failure history below are retained for traceability.

## Findings and dispositions

### 1. Independent nodal lumping is a real approximation

The fixed mesh contains every layer boundary, all stage excavation surfaces, all specified water levels, and support depths. Each beam interval has three Gauss material stations; each station is split with positive linear weights between its two adjacent nodes. This preserves the resultant and first moment of a **prescribed** pressure value at that station because the two weights sum to one and their weighted nodal depths equal the station depth.

The two nodal spring branches nevertheless see different nodal displacements and have separate plastic histories. This is not a single Gauss spring driven by interpolated displacement, nor a consistent Hermite foundation stiffness. It is the frozen independent nodal-lumping model.

An exact counterexample makes the limit clear. On an interval with global depths 1–2, choose spring coefficient `k(z)=z` and nodal displacements `y(1)=0`, `y(2)=1`. The lumped model's resultant is 5/6 and its moment about the ground datum is 5/3. A continuous spring law driven by the affine displacement `y(z)=z−1` has the same resultant 5/6 but moment 17/12. The lumped moment is 17.647% larger on this coarse interval. Consequently, exact pressure quadrature must not be described as exact displacement-dependent soil reaction integration.

The parent has corrected the returned `discretisation` description to distinguish prescribed-pressure integration from displacement-dependent nodal lumping. The mandatory mesh comparison is retained. For the complete six-stage verification example, all stages completed for maximum interval sizes 0.25, 0.125 and 0.0625 m. The maximum change among each stage's displacement, moment and shear peaks was 1.4478% for 0.25→0.125 and 0.99934% for 0.125→0.0625. These satisfy the declared 2% fixture criterion; they do not bound errors for arbitrary soils or excavation histories. Full data: `soil_mesh_refinement_centered_beam_current.json`.

### 2. Effective stress and hydrostatic water loading are consistent with the declared assumptions

`effective_vertical_stress` integrates actual layer portions from the current side's surface, splitting at the water level. The submerged contribution is `gamma_sat − gamma_water`. Outside overburden starts at the original ground; inside overburden starts at the current excavation surface. The outside uniform surcharge is added explicitly to the effective vertical stress, while the inside surcharge is zero in this model.

Hand integrations checked the synthetic two-layer example. With outside water at 1.5 m, outside effective vertical stresses at depths 1, 3 and 5 m are respectively 18, 42.285 and 63.665 kPa. With inside excavation at 2.05 m and water at 2.55 m, inside values at 3 and 5 m are 13.5855 and 34.9655 kPa. The implementation agreed within 7.1e-15 kPa in this check.

The only automatically generated `externalForcesKn` are the hydrostatic water differences times actual pile spacing and integration weight. They do not contain soil pressure or a subtracted passive wedge. Soil pressures are applied once through the bounded spring forces. This removes the identified old-engine double counting, where full passive pressure had already been subtracted before adding the m-method spring resistance.

For each stage the water resultant and moment were checked against direct analytic integration of `spacing * gamma_water * ((z−water_out)+ − (z−water_in)+)`. Maximum absolute errors were 2.84e-14 kN in force and 5.68e-14 kN m in moment. Hydrostatic loading is prescribed; this check does not establish seepage, drawdown, hydraulic uplift, or piping safety.

### 3. Pile spacing and stiffness width have different explicit roles

The beam rigidity is for one circular pile: `EI = E * pi * D^4 / 64 * inertia_factor`. The pressure baseline and active/passive bounds use actual pile spacing times the integration weight. Spring stiffness uses `m * depth_below_current_surface * soil_effective_width * integration_weight`.

The units are consistent: `m` is kN/m^4, the horizontal subgrade coefficient `m*d` is kN/m^3 (kPa/m), and the nodal spring stiffness is kN/m. If an equivalent pressure–displacement slope is displayed, it equals `m*d*(soil_effective_width / pile_spacing)`. The width is therefore a **stiffness effective width** under this contract. Capacity remains based on pile spacing. The name must not imply a verified three-dimensional passive capacity, soil arching, or group-pile model. The fixture deliberately uses equal width and spacing, both 0.9 m.

### 4. Initial stress and excavation path are internally defined, with a bounded constitutive scope

Initially equal effective vertical stress and water level on both sides give equal positive `K0*sigma_v` baseline pressures. Their signed wall forces cancel naturally at zero wall displacement; no zero-curve override is used. The zero-excavation analytical test verifies this.

At excavation, springs above the new inside surface disappear. Surviving IDs retain their plastic displacement while inside `p0`, bounds and `k=m*(z−H)` are recalculated for the remaining overburden. Outside IDs retain their locations and change pressure parameters only according to explicit stage inputs. This is a declared independent-spring parameter path, not an assertion that the surrounding continuum's unloading stress path has been reproduced. The soil material's return mapping is committed only after a stage converges and passes equilibrium and restraint checks.

The Rankine cohesion term creates a zero-active-pressure clipping point when cohesion is nonzero. That internal kink is not an extra mesh breakpoint in the current preprocessor, so three-point Gauss quadrature is not analytically exact across that kink. The published verification fixture has zero cohesion. General cohesive/tension-crack cases need explicit limitations and their own mesh study; a successful bounded-spring solution must not become a passed tension-crack, short-term undrained, or groundwater safety check.

### 5. Force recovery and boundary signs agree with the discrete model

Depth increases downwards, positive displacement is into the pit, and rotation is `dy/dz`. For an element with work-conjugate end forces `[Vi, Mi, Vj, Mj]`, the recovered internal fields are `V=Vi` and `M(x)=−Mi+Vi*x`. The analytical cantilever test checks this convention, including toe shear and moment reactions. The service preserves both sides of nodal force jumps and searches cubic-Hermite displacement extrema.

All loads are nodal after lumping, so an element has no distributed load and its internal shear is constant. A free head or toe means no prescribed restraint; a nonzero load lumped to an end node can still produce nonzero shear immediately inside the adjacent element. This is consistent with the discrete model and must not be described as the exact continuum end-shear boundary of a wall under smooth pressure.

The force balance uses the actual nodal depths of the lumped reactions, matching the solver's virtual-work definition. Prescribed pressure first moments are preserved by the positive weights; displacement-dependent spring moments carry the approximation described in finding 1.

## Numerically confirmed defects repaired during review

### A. Fine-mesh beam force cancellation — historical first repair

The 0.0625 m / 163-node fixture initially failed S5 after 100 iterations even though displacement and the active spring set had stabilized. The old global `K*d` calculation combined absolute row terms of approximately 7.873e8. Machine epsilon times that scale is 1.748e-7 kN, larger than the unchanged 4.234e-8 kN nonlinear convergence threshold. Evaluating the same converged displacement with old global multiplication versus centered element forces differed by 6.554e-8 kN.

A first-failing full-history regression test reproduced the failure. The repaired primary solver removes rigid translation and chord rotation before evaluating each element: `a=theta_i−(y_j−y_i)/L`, `b=theta_j−(y_j−y_i)/L`, `Mi=2EI/L*(2a+b)`, `Mj=2EI/L*(a+2b)`, `Vi=(Mi+Mj)/L`. The same forces and associated strain energy now drive residual calculation, line search and output recovery. The tangent stiffness and physical model are unchanged. No tolerance was loosened and no stiffness was added.

After the fix the fine-grid S3 took four iterations and S5 five. S5's nodal residual was 3.440e-8 kN; its global relative force and moment residuals were 9.151e-12 and 4.209e-12. The complete grid comparison then passed the previously declared 2% criterion.

### B. Independent OpenSees accepted a nonunique plastic plateau

A real OpenSees probe exposed a distinct problem: in a free two-node beam, each node has opposed soils with `p0=50`, `lower=20`, `upper=100` kN and `k=1000` kN/m. Applying 80 kN at each node exactly balances both ultimate pressure differences. The adapter returned success with zero residual at a rigid displacement of 0.05 m, although any greater inward rigid translation remains balanced. A pinned-toe variant retains an unconstrained rigid rotation. A zero residual alone does not establish a unique displacement.

Two first-failing plateau regression cases and one mixed installation/removal contract case were added. The initial rank-only repair correctly rejected these platforms but subsequently proved too conservative at complementary one-sided corners. The final implementation uses the actual independent material branch states and a normalized direction-cone test, preserving corner half-planes rather than deleting them. It uses no primary stiffness matrix and adds no restraints. A nonzero zero-stiffness direction returns failed, prevents transfer of that stage state, and leaves curves null. The final proof and regression evidence are in `soil_solver_p2_closure.md`.

The independent adapter also now rejects installation and removal within the same event, matching the primary kernel and frozen input contract. Native ElasticPP `eps0` remains the history initialization mechanism; it is not replaced with the InitStrain wrapper that the earlier probe showed can spuriously commit a plastic offset.

## Verification evidence and remaining boundaries

Before the final complementary-boundary review, the focused run was **35 passed in 0.58 s**. This is a historical milestone; the final run after the P2 and fine-grid refinement corrections is **54 passed in 1.31 s**, comprising 27 independent and 27 primary tests. Source hashes, the red/green history, current six-stage results and limitations are recorded in `soil_solver_verification_current.json`. Root owns the final integrated API, export, full comparison and repository verification.

The foundational public method review, URLs and precise manual sections are in `soil_method_research_primary_sources.md`: USACE EM 1110-2-2504 chapter 7 for bounded soil–structure springs, Hong Kong GEO Publication 1/2023 §6.6.3 for independent spring capabilities and limitations, and OpenSees official material/element documentation and source for the independent implementation. The staged committed-history extension is this subsystem's explicit modeling assumption, not a claim of certification by those manuals.

Unresolved engineering coverage remains explicit: original A3-3 model parameters and native file, actual project method equivalence, continuum ground deformation, global stability, basal heave, seepage, strut system geometry and axial allocation, and formal structural detailing. None is passed by the successful numerical fixture.

## Repository evidence index and handoff

- `soil_method_research_primary_sources.md`: original method alternatives, selected constitutive contract, exact primary-source references, and actual OpenSees initialization probes.
- `soil_solver_verification_current.json`: focused 35-test result, current primary/independent source hashes at this review handoff, current six-stage primary response, and clearly labeled historical test attempts.
- `soil_mesh_refinement_centered_beam_current.json`: complete runs and all stage-wise peak comparisons for the centered beam-response implementation at 0.25, 0.125 and 0.0625 m.
- `soil_solver_pre_centering_failure_history.json`: explicitly historical pre-fix failure and first-failing regression; it must not supply final design curves.

The 0.125 m current fixture in the mesh record has snapshot hash `2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd`. Root is re-running the final actual OpenSees comparator and recording its own final source hashes after this handoff. Those final run records govern integrated acceptance; this review does not pre-claim them. Primary and independent source editing is stopped at this handoff.

The earlier all-corner-branches-released gate was found to reject valid complementary constraints and has been superseded by the normalized two-dimensional direction-cone test. It preserves the real one-sided corner forces, rejects true zero-stiffness directions, and adds no averaged constitutive resistance. See `soil_solver_p2_closure.md` for the proof, exact-oracle review and final-source evidence.

Current canonical evidence: `soil_direction_cone_verification_current.json`. Superseded pre-cone and float64-only records are explicitly labeled `_history.json`.
