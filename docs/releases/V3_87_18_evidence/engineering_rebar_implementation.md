# Engineering reinforcement implementation handoff

2026-10-25. No Git commit. Ownership limited to engineering_models.py, engineering_rebar.py, tests/test_section_engineering_rebar.py. Existing pure bending function and v1 results untouched by this agent.

## Delivered interface

`EngineeringDesign` in services/api/app/section/engineering_models.py is frozen, forbids extra/nonfinite data, and has no dependency on section.models. Groups: nm_cases/shear/detailing/anchorage/lap/service, each nullable, default absence is not confirmation. No user-supplied override of M or V exists. Cases reference stage_id. Root imports this under v2 top-level engineering_design.

`check_engineering_rebar(model: dict, envelope: dict, design: dict|None, stages: list[dict]|None=None) -> dict` is pure. Caller validates result sealing before invocation. Output checks list has stable ids axial_flexural_interaction, shear, minimum_cover, bar_spacing, stirrup_detailing, anchorage, anchorage_confinement, lap, service_stress, crack_width. Common fields: method/status/demand/capacity/utilization/units/details/requiredInputs/basis/scope/limitations/governingCase. Status values: pass/fail/missing_input/outside_method_scope/not_calculated; service_stress additionally calculated because it has no acceptance criterion. Top status review_required/engineering_fail/not_calculated. formalDesignReady always false.

Full typed schema is inspectable via EngineeringDesign.model_json_schema(). Small valid group examples are in the test functions nm_cases(), shear(), detailing(), anchorage(); service/lap cases in named tests. These are SYNTHETIC VERIFICATION inputs, not adopted A3-3 project facts.

## Numeric methods and limits

N-M checks N>=0 only, neutral-axis depth<=D, with original frozen concrete/steel curves and exact circular quadrature/discrete bars/displaced concrete. Constant explicit N per stage binds to actual signed stage minimum/maximum M, scaled by design effect factor once. Missing stage coverage is incomplete. Tension, whole compression, required/unknown second-order assessment produce outside_method_scope, with no capacity number. This is a numerical section resistance branch, not a complete code interaction diagram or member stability check.

Circular shear follows verified 6.3.15 equivalent b=1.76r/h0=1.6r, general nonprestressed N=0/nonseismic loading only. Effective Asv is supplied with explanation and limited to the two-leg geometric area of the given single hoop. Axial stage cases with any nonzero N invalidate the all-stage N=0 shear assumption. If axial cases absent, trace explicitly says independent_explicit_zero_axial_assessment and axialStageCoverageConfirmed=false. Section crushing and Vc+Vs both limit resistance. This result does NOT confirm minimum hoop ratio, closure or local confinement.

Detailing computes chord clear gap and cover to main bars/outer tangent hoop; compares explicit project criteria, without fabricating environmental or bored-pile limits. Given D700/16phi18/axis59/phi8 gives clear main50, outer hoop42, chord95.542567mm.

Anchorage calculates nonseismic deformed straight-bar tension lab, corrections for d>25/epoxy/disturbance, no beneficial reductions, and minimum200. Explicit joint ft capped at C60 input; top/bottom available lengths are actual lengths from anchorage start sections, not pile length or embedment. Confinement is separate and uses anchored longitudinal-bar clear cover for the 5d condition. With phi18 and clear50 the transverse minimum d=4.5 and maximum spacing90; phi8@128 fails local confinement despite possible global shear pass.

Lap calculates 8.4.4 length factor (1.2/1.4/1.6 at <=25/50/100%, interpolation) and local transverse dimensions when explicit tied-lap context and anchorage group exist. It deliberately remains outside_method_scope even if numeric lengths pass because longitudinal splice positions, member classification/percentage limits and prohibited axial/small-eccentric tension applicability are unverified. Numeric deficient length/confinement returns fail. Explicit continuous_no_lap gives a scoped arrangement declaration pass only. Mechanical/welded cases remain unsupported.

Service stress is N=0 cracked circular elastic analysis, actual signed characteristic stage M times explicit SLS scale. It returns tensile steel stresses and equilibrium residuals; if tensile stress reaches supplied steel design strength, linear model is marked outside scope. Crack width remains null/outside_method_scope because general rectangular GB formula is not substantiated for this perimeter-uniform circular cage. Environment/SLS limit inputs are recorded, not inferred.

## Primary source evidence

- GB55008 official mandatory/preference announcement: https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2021/art_17339_762454.html
- CABR authoring institution GB/T50010 2024 shear clauses: https://gf.cabr-fire.com/m/article-12606.htm ; formula image visually verified https://gf.cabr-fire.com/Web/uploads/allimg/161214/4-16121410042Q22.png
- Cracking scope and service stress assumptions: https://gf.cabr-fire.com/m/article-12612.htm
- Anchorage 2024 text: https://gf.cabr-fire.com/m/article-12617.htm ; formula images visually verified https://gf.cabr-fire.com/Web/uploads/allimg/240621/10-2406211112551F.png and https://gf.cabr-fire.com/Web/uploads/allimg/240621/10-240621112044J6.png
- Lap: https://gf.cabr-fire.com/m/article-12618.htm ; table visually verified https://gf.cabr-fire.com/Web/uploads/allimg/161214/4-161214140HV09.png
- JGJ120 official local authority PDF locator: https://jncc.jinan.gov.cn/attach/0/4641b5e9d322401e9beba7ebc163def6.pdf . Retrieval403; no unviewed pile-detail clause encoded as verified.
- Full research memo /workspace/scratch/fd5d8a28c64e/work/rebar_engineering_research.md records remaining edition/primary-access gaps. Do not equate this local implementation with comprehensive national/local standards compliance.

## Test evidence

Initial red run: 12 failures, intentionally reporting missing new modules. First implementation:12 passed. Effective-hoop-given-geometry and independent-axial-source tests then observed red (2 failed/14 passed); implemented correction,16 passed. Lap table test observed red (1 failed/18 passed); numeric bounded lap implementation,19 passed.

Latest dedicated command, repository root:
`PYTHONPATH=services/api /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest services/api/tests/test_section_engineering_rebar.py -q`
Result:19 passed in1.18s (exit0).

Independent evidence includes hard numeric shear/anchor values, N=0 agreement with old verified solver, independent adaptive angular integration+Brent root for nonzero N, and independent fine-strip transformed cracked-section root for service stresses. State tests cover sign/stage coupling, unsupported N/second order, incomplete coverage/envelope, strict input extra/nonfinite rejection, absent SLS/hoops, and geometry-source incompatibility.

A full backend `python -m pytest -q` was started, then explicitly interrupted on root instruction because shared integration files remain in progress and old broad baselines may block in sandbox. Exit130, partial dots only; NOT claimed as a passed full suite. Root owns final section suite,145 targeted regression and necessary API gates.
