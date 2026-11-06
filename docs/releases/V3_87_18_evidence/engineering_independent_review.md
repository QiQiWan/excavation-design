# Independent engineering review and authorized SLS coverage repair

Date:2026-10-25. Repository: `/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation`.

## Outcome

One evidenced P2 reporting/completeness defect was found: omitted SLS stages were not identified, so a partial service-stress calculation could be classified as calculated and removed from unverifiedChecks. Root explicitly authorized repairing this specific finding after the initial read-only review. The repair is complete, with tests written and observed failing before code changes. All19 original tests plus2 new regression tests pass. No other reproducible blocking defect was established within the reviewed scope. This is not certification of the original project, full code compliance, or all numerical domains.

Original review scope: engineering_models.py, engineering_rebar.py, test_section_engineering_rebar.py. The reviewer did not author these engineering modules originally. No frontend or export/root integration code changed in this review. No full suite ran. Only the explicitly approved SLS repair changed production code.

## Finding ENG-01 — omitted SLS stage hidden by calculated aggregate (P2, fixed)

Before repair, `_service` iterated only `service.cases`, without comparing their IDs to actual sealed-result stage IDs. It returned calculated for an otherwise valid supplied subset; line199 then excluded the service-stress check from unverifiedChecks. In contrast, `_nm` already detects missing stage cases at lines210–211.

Minimal pre-fix reproduction, from repository root:

```python
import runpy
x = runpy.run_path('services/api/tests/test_section_engineering_rebar.py')
stage_data = x['stages']()
stage_data[1]['points'][0]['momentCharacteristicKnM'] = 1000.0
service = {
    'cases': [{'stage_id': 'S1', 'axial_force_service_kn': 0.0,
               'characteristic_effect_scale': 1.0,
               'combination_basis': 'Explicit S1 service combination only'}],
    'concrete_elastic_modulus_mpa': 30000.0,
    'long_term_basis': 'Elastic instantaneous model only',
    'crack_width_limit_mm': 0.2,
    'limit_basis': 'Explicit project criterion',
}
r = x['api']().check_engineering_rebar(x['model'](), x['envelope'](), {'service': service}, stage_data)
c = x['by_id'](r)['service_stress']
```

Observed pre-fix: `c.status='calculated'`, `c.requiredInputs=[]`, evaluated stage IDs onlyS1, and service_stress absent from unverifiedChecks. The omittedS2's independently invoked service calculation at1000kN·m gives1249.492321040351MPa tensile steel stress, which would be outside the existing model's360MPa steel-domain guard had the stage been evaluated. This does not mean the original code declared overall design approval: formalDesignReady remainsfalse and the service row was calculated, notpass. The defect is that coverage omission was not reported.

Repair at engineering_rebar.py:382–386 and403–406:

- Required stage IDs now come from the actual stage_map, retaining construction order.
- Missing case IDs generate `engineering_design.service.cases[stage_id]` in requiredInputs.
- Existing calculated stage rows remain available.
- Details expose requiredStageIds, missingStageIds and coverageComplete.
- Existing aggregate logic returns missing_input whenever stage coverage is missing; the existing unverifiedChecks aggregation therefore retains service_stress.
- Full coverage continues to return calculated, neverpass. Crack width retains outside_method_scope. Stage N, characteristic-effect scales, and stress algorithms were unchanged.

Regression tests at test_section_engineering_rebar.py:291–319 exercise omission with retained partial values and complete-coverage calculated status. First run:2 failed,19 passed. After repair:21 passed in0.49s. `git diff --check` passed.

## Reviewed engineering behavior

### Same-stage N–M and effect factors

engineering_rebar.py:205–240 binds each user-supplied constant stage designN to that stage's signed minimum/maximum characteristicM. It multiplies M by design_effect_factor once and leaves already-designN unchanged. Demands are not recombined from unrelated global envelopes. Missing stageN, unknown/non-successful stage points, required/unknown second order, axial tension and unsupported high compression do not receive a capacity-pass.

The19 existing tests independently verify theN=300kN branch via angular concrete integration plus Brent root, andN=0 against the established pure-bending function. Additional minimal review computation used an odd7-bar cage: negative-bending capacity equals positive-bending capacity after rotating the bars180°. Differences were0kN·m forN0 and5.68e-14kN·m forN300. This checks sign handling beyond the symmetric16-bar fixture. Explicit unsupported domains (steel rupture strain, global stability, full compression branch, confinement and seismic effects) remain limitations.

### Shear units, effect and applicability

engineering_rebar.py:243–277 uses radius inmm, b=1.76r, h0=1.6r, strength inMPa=N/mm². Concrete and hoop terms are convertedN→kN by /1000. Area-per-length conversion multiplies mm²/mm by1000 to mm²/m. The separate conversion of demandkN toN before calculating requiredAsv/s is dimensionally correct. EnvelopeV is multiplied by design_effect_factor once. Section crushing limit caps concrete+hoop capacity.

Fixture hand values independently checked: Vd150.75kN, b616mm,h0560mm,Vc345.30496kN,Vs118.75220230569417kN,total capacity464.0571623056941kN,section cap1233.232kN. Provided effective area per metre785.3981633974482mm²/m. Given demand belowVc, strength-only required hoop area is0; code explicitly does not call that a minimum-hoop/detailing pass.

Nonzero declaredN, conflicting nonzero stageN, seismic/unknown seismic and unsupported load case return outside_method_scope. Missing hoops remain missing_input. Supplied effective area larger than two given geometric legs is rejected. The exact circular-hoop effective-area interpretation is explicitly user-supplied; I verified formula units and its provenance field, but did not establish from primary evidence that every possible two-leg interpretation is correct for every detailing arrangement. This is an existing declared applicability boundary, not an additional proven defect or a blanket validation of the area mapping.

Primary clause check: CABR-hosted GB/T50010-2010(2024),6.3.15 specifies the1.76r/1.6r substitutions and refers to6.3.1–6.3.14. Section6.3.4 distinguishes general bending from concentrated loading. Source: https://gf.cabr-fire.com/m/article-12606.htm (retrieved2026-10-25; tool references turn85view0,turn86view1). No source-based statement that the original historical project used this implementation is made.

### Anchorage, local confinement, cover and laps

engineering_rebar.py:305–332 applies the declared deformed-bar/nonseismic straight-tension scope,0.14fy/ft×d basic length, explicit diameter/epoxy/disturbance multipliers, and the200mm floor. For fixturefy360MPa,ft1.43MPa,d18mm, basic length634.4055944055945mm; top600 fails and bottom650 passes. Combined epoxy/disturbance correction gives872.3076923076924mm. No favorable excess-rebar or large-cover reduction is inferred.

Longitudinal-bar cover50mm is used for the5d anchorage confinement condition, while the separate cover check uses the outside-hoop cover42mm. Local transverse input is independent of full-length stirrup input. At d18, required local spacing≤90mm;128mm correctly fails despite full-length spacing criterion150mm passing. Missing detailing arrangement keeps local confinement unknown.

The circular cover/geometric checks use outer-hoop clearance and chord bar spacing, not arc spacing. The declared hoop arrangement is required for hoop-derived cover/detail checks.

Lap length and local confinement can be numerically checked, but a passing partial length check remains outside_method_scope because layout/location and category constraints are absent. Existing fixture ratio0.5 gives factor1.4 and required888.167832167832mm. This conservative status split is appropriate for the implemented scope.

CABR-hosted8.3 supports the correction factors, minimum anchorage and local transverse provisions;8.4 separately sets lap location/percentage and transverse requirements. Sources: https://gf.cabr-fire.com/m/article-12617.htm and https://gf.cabr-fire.com/m/article-12618.htm (retrieved2026-10-25; tool references turn85view1,turn85view2). Joint geometry and actual site bar anchorage lengths remain supplied engineering facts; this review did not authenticate them.

### Cracks and calculated status

engineering_rebar.py:371–407 preserves crack_width outside_method_scope with no fabricated width/capacity. Service stress uses explicitSLS scale times same-stage characteristicM, not theULS factor. Its result remains calculated and is not promoted topass. ExplicitN!=0 or elastic tensile stress reaching the supplied steel design-strength boundary remains outside_method_scope. Overall formalDesignReady is alwaysfalse. After ENG-01, omittedSLS stages are visibly incomplete and retained in unverifiedChecks.

The implementation's crack citation URL could not be fetched through the web tool during this review; I did not independently authenticate its complete7.1.2 text. The reviewed safety property is implementation-level: no unsupported circular crack width or crack pass is produced. Linear service stress alone remains an intermediate quantity, not a demonstrated concrete-service limit or crack-compliance result.

## Test execution and exclusions

Executed only:

`/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest services/api/tests/test_section_engineering_rebar.py -q`

Original suite:19 passed in0.87s. Tests-first repair run:2 failed,19 passed. Final scoped run:21 passed in0.49s. Additional read-only scalar calculations are recorded above. No full-suite, project analysis, frontend or package-generation run was performed for this review.

No further blockers were demonstrated. Remaining stated method domains and source-parameter identity requirements should stay visible in exports and review UI; this report does not expand the implemented engineering applicability.
