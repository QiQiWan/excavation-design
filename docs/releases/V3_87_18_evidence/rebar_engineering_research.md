# Circular pile engineering reinforcement research / proposed contract

Research date: 2026-10-25. Read-only phase; no product changes. Baseline worktree foundation_section_validation, original-project-validation, c8793e8. Sources below were retrieved directly; formula images for shear and anchorage were independently opened visually. This is an implementation proposal, not a declaration of comprehensive design-code compliance.

## Existing implementation and original-project evidence

Read services/api/app/section/{models,reinforcement,export}.py, legacy gb50010_2024/reinforcement_rules.py and examples/frozen-section/A3-3-reference-audit.md. Current frozen input deliberately allows N=0 only. Capacity is discrete uniform perimeter bars, circular strip quadrature, concrete parabola/plateau with no tension, symmetric elastic-plastic steel and subtraction of displaced concrete. It solves neutral-axis equilibrium at ultimate compressive strain. Required steel changes total area at fixed bar centres; it does not select a constructible cage. Export currently tabulates flexure and explicitly unverified checks.

A3-3 p36 has 16φ18, 59 mm BAR AXIS cover (not concrete clear cover), C30, HRB400, HPB300 φ8. Final provided hoop spacing is absent; 128 mm is computed required two-leg spacing. Original required Asv 783 mm²/m is not provided steel. Existing source audit must remain authoritative about source identity. No SLS combination, exposure assessment, anchor lengths or lap arrangement is established. Therefore absent new inputs cannot become engineering passes.

## Primary-source catalogue and conclusions

1. Ministry official GB55008 announcement, 2021 No167, effective 2022-04-01:
https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2021/art_17339_762454.html
All clauses mandatory; incompatible older-standard provisions yield to it. Official PDF is linked as attachment 0. It is image-only in web extraction and must be visually reviewed for individual requirements before encoding them. A capacity result cannot certify satisfaction of this entire general code.

2. Ministry official 2024 gazette (search indexed No62 at printed p41):
https://www.mohurd.gov.cn/file/2024/20240822/a015dc08-eaf2-474a-81c1-c4454e3b220c.pdf
Direct web fetch failed 502. Secondary reproductions unanimously identify No62, 2024-04-24, effective 2024-08-01, renamed GB/T50010-2010. CABR authoring institution hosts the 2024 text below. Do not claim official revision attachment fully verified yet. CABR front matter still reproduces the old announcement, so its title alone is insufficient revision evidence.

3. Chinese Academy of Building Research (standard authoring institution) 6.3:
https://gf.cabr-fire.com/m/article-12606.htm
6.3.15 expressly permits circular sections to use b=1.76r and h0=1.6r in 6.3.1–14. Thus this particular equivalence IS code-supported, unlike b=D and arbitrary effective depth. For nonprestressed N=0 ordinary bending: Vcs=alpha_cv*ft*b*h0+fyv*Asv*h0/s, ordinary alpha_cv=.7. Concentrated-load independent beams have different alpha_cv; require explicit classification. Section limit for this squat equivalent rectangle is .25*beta_c*fc*b*h0. beta_c=1 through C50, .8 at C80 with linear interpolation. Scope must exclude torsion, prestress, seismic shear and unclassified load type. Formula image verified: https://gf.cabr-fire.com/Web/uploads/allimg/161214/4-16121410042Q22.png . Circular hoop Asv convention must be explicitly recorded; do not silently double a single bar area.

4. CABR 7.1:
https://gf.cabr-fire.com/m/article-12612.htm
7.1.2 lists rectangular/T/inverted-T/I sections. Commentary specifically identifies insufficient research for uniform perimeter steel and requires more reasonable sigma_s and effective tension area. It does NOT establish that generic rectangular crack calculation is valid for these circular piles. 7.1.3 supports planar strains, triangular compression, ignored concrete tension, transformed cracked section for service stress analysis. Deliver stress analysis as useful bounded result; keep crack-width status outside_method_scope absent a verified circular-specific method. Do not use Md/1.25 as silently reconstructed quasi-permanent Ms. SLS action combination and long-term assumptions need their own inputs.

5. CABR 8.3:
https://gf.cabr-fire.com/m/article-12617.htm
Formula images visually verified: /Web/uploads/allimg/240621/10-2406211112551F.png and 10-240621112044J6.png. Ordinary deformed bar lab=.14*fy/ft*d; concrete tensile strength limited to C60 value. la=max(200, zeta*lab), zeta product not below .6. Diameter >25, epoxy and construction disturbance require increases (1.10,1.25,1.10). Keep reduction for excess area/large cover disabled in first implementation. Straight deformed nonseismic anchorage only, with explicit user case. Available length must be entered per top/bottom or distinct anchorage zone. If cover <=5d, transverse reinforcement requires diameter >=d/4 and spacing <=min(5d,100) for beam/column/brace type members. Node-specific rules remain outside scope. This verifies geometric development length and local confinement only, not actual bond quality.

6. CABR 8.4:
https://gf.cabr-fire.com/m/article-12618.htm
Lap length depends on connection percentage and is at least 300 mm for tension. Same connection zone spans 1.3 lap lengths. Axial/small-eccentric tension excludes tied laps; mechanical/welded joints need separate standards. Implement only explicit nonseismic deformed tension lap case with supplied lap factor and source basis initially, or with table once image verified. Input 'no lap' means explicit full continuous bar arrangement, not missing data. Lap zones must not be inferred from pile length. Local transverse reinforcement and percentage/staggering checks remain separate.

7. CABR cover and beam stirrup details:
https://gf.cabr-fire.com/m/article-12616.htm
https://gf.cabr-fire.com/m/article-12622.htm
Do not blindly apply beam spacing to bored piles. For first release use project-explicit min clear cover/min clear bar spacing/max hoop spacing with documentary basis; classify these as provided-criterion checks, not code-derived requirements. Calculate actual bar geometry exactly. JGJ120 4.3.2 equivalence and JGJ120/JGJ94 pile-specific details were found in third-party reproductions, but official primary text was NOT yet located. Do not encode those secondary statements as verified normative rules.

## Proposed exact integration interface

Root owns Pydantic models/service/export; agent owns app/section/engineering_rebar.py and tests/test_section_engineering_rebar.py. Entry:
`check_engineering_rebar(model: SectionInput, envelope: dict, design: dict | None) -> dict`
Read only model; never mutate envelope or existing reinforcement results. New optional top-level `engineering_design: EngineeringDesignInput | None = None`. Missing object returns per-check missing_input and formalDesignReady=false. Existing fixture compatibility preserved. Do not change axial_force_kn Literal[0] yet; new actions belong to explicit engineering cases until coupled solver axial action is supported.

Suggested nested input names (all optional whole groups; within a supplied group required fields explicit; no affirmative engineering defaults):

- `nm_cases`: tuple of `{id, axial_force_design_kn, moment_design_knm, stage_id: str|None, depth_m:float|None, action_basis:str}`. Compression positive, moment signed. These are supplied section actions, not solver-computed N. First implementation accepts `0 <= N <= N_at_neutral_axis_D`; lower axial tension and compression-only branch outside scope; method-labelled partial N-M curve. Concrete compression block never deeper than D, avoiding unsupported high-compression strain-domain extension. Include N=0 plus 9 equally spaced valid-N diagnostic points, not an asserted full interaction envelope.
- `shear`: `{concrete_tensile_design_strength_mpa, concrete_strength_coefficient_beta_c, stirrup_design_strength_mpa, effective_hoop_area_mm2, hoop_area_basis, loading_case: 'general_bending_nonprestressed_n0', seismic_case:'nonseismic', applicability_basis}`. Take Vd once from signed solver envelope × design_effect_factor. Effective hoop area is explicit because original record uses two-leg interpretation; validate positive and provide supplied hoop diameter/spacing separately. Unknown load type is missing input, other cases outside scope. Return Vc, Vs, Vlimit, required strength Asv/s, provided Asv/s. Do not let adding hoops override section crushing failure.
- `service`: `{cases: tuple[{id, axial_force_service_kn, moment_service_knm, combination_basis}], concrete_elastic_modulus_mpa, long_term_basis}`. First pass N=0 stress solver, explicit other N rejected. Output exact circular cracked-elastic neutral axis, max tension steel stress, compression stress, residuals. Width stays null/outside_method_scope. No substituted rectangular crack width.
- `detailing`: `{minimum_clear_cover_mm, minimum_clear_spacing_mm, maximum_stirrup_spacing_mm, minimum_stirrup_diameter_mm, criterion_basis}`. Derive main bar clear cover=axis_cover-d/2; outer hoop clear cover=axis_cover-d/2-hoop_diameter (only external touching hoop assumed and explicitly named); chord clear spacing=2*(R-axis_cover)*sin(pi/n)-d. Separate criterion provenance and geometric result.
- `anchorage`: `{bar_surface:'deformed', seismic_case:'nonseismic', concrete_tensile_design_strength_mpa_capped_c60, epoxy_coated:bool, construction_disturbed:bool, top_available_length_mm, bottom_available_length_mm, transverse_diameter_mm, transverse_spacing_mm, applicability_basis}`. Require explicit member/node applicability; no bending hooks/mechanical anchors. Keep anchorage concrete tension strength distinct where different joint concrete applies.
- `lap`: `{arrangement:'continuous_no_lap'|'tension_tied_lap', seismic_case:'nonseismic', available_length_mm:float|None, joint_area_ratio:float|None, lap_factor:float|None, factor_basis:str, transverse_diameter_mm:float|None, transverse_spacing_mm:float|None}`. No generic passed lap field without explicit arrangement. May defer normative table validation.

Common per-check result: `{id, method, status: 'pass'|'fail'|'missing_input'|'outside_method_scope'|'not_calculated', scope, basis:[{standard,clause,url}], inputTrace, demand, capacity, utilization, units, details, limitations}`. Unknown numeric fields null, never 0. A failed numeric check overrides review-required summary, but every pass is explicitly local. Top level `status:'review_required'|'engineering_fail'|'not_calculated'`, `formalDesignReady:false`, `checks`, `unverifiedChecks`. Return interpretation/provenance for load factors and supplied section action cases. Existing N=0 row must continue unchanged.

## Independent calculation tests

1. Geometry fixture D700, 16φ18, axis59, hoop8: steel radius291; chord spacing=2*291*sin(pi/16)-18=95.5425674134mm; main clear50; outer hoop clear42. Test no-hoop yields unknown outer-hoop cover, not 50. Verify geometry failure when cage exceeds radius.
2. Shear synthetic D700, fc14.3, ft1.43, fyv270, beta1, Asv=2*pi*8²/4, s128: b616,h0560; Vc345.305kN; Vs118.752kN; Vlimit1233.232kN (calculate full precision independently). Synthetic Vd150.75 passes strength but may fail explicit spacing/cover. V>Vlimit must fail regardless huge Asv. Factor applied exactly once.
3. Anchorage d18,fy360,ft1.43: lab=.14*360/1.43*18≈634.406mm; uncoated/undisturbed and no reduction -> la same; top600 fails, bottom650 passes. Cover50<=90 means hoop diameter>=4.5 and spacing<=90; existing φ8@128 fails local anchor confinement. Missing availability is unknown.
4. N-M check compare N=0 capacity bitwise/tolerance with existing solver, nonzero N equilibrium residual, independent fine midpoint integration (not shared quadrature), reversed loading for odd bar layouts, N above validated branch refuses capacity. Document finite strain domain; do not accidentally extrapolate to axial compression endpoint.
5. Service stress independent transformed-section integration; compare doubling M doubles stress; no-concrete-tension; each bar stress signed; max stress cannot be replaced by mean tension steel stress; M=0 yields zero stress without neutral-axis division.
6. Missing inputs test returns no false pass, incomplete envelope not_calculated, source fields survive result/export, no nonfinite JSON, fixture snapshots unchanged, explicitly unsupported seismic/axial/crack cases stay unverified.

## Decisions needing root contract approval

A) Implement narrow explicit N-M compression branch, shear, geometry and straight anchorage now. B) Circular cracked-service stress now but no unjustified crack-width number. C) Either defer lap numeric table until visual verification or accept user-supplied criterion as explicitly supplied only. D) Keep project data absent; demonstrate useful checks via synthetic verified cases, never populate A3-3 defaults as confirmed facts.

### Source-access follow-up
Official local construction authority hosts JGJ120-2012: https://jncc.jinan.gov.cn/attach/0/4641b5e9d322401e9beba7ebc163def6.pdf . Search extraction confirms official cover and approval notice; direct retrieval returned403 and targeted clause search did not extract detailing provisions. It is a locator, not yet evidence for encoded clause numbers.
