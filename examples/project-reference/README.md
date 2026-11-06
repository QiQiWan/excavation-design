# Original A3-3 conditions and numerical exchange

This package preserves the historical report evidence; it contains **no invented FRWS nodal results** and does not claim original-software reproduction. The report explicitly names 同济启明星 FRWS9.0 (physical PDF p21), not 理正. Its executable build, native file extension and numerical export menus remain unconfirmed.

## Files and APIs

- `A3-3-reference-contract.json`: 337 fields (320 unchanged audit evidence fields plus 17 explicitly identified additions). A source hash and each field's value/unit/status/meaning are included in the condition hash.
- `A3-3-curve-template.json`: empty intake with five separately identified stages; all curve arrays are empty. Unknown units' physical basis, signs and pressure composition remain unconfirmed.
- `curve-samples-template.csv`: header only. The CSV is a documented interchange table, not an assertion that FRWS9.0 exports this format. JSON is the implemented import format.

Python modules are `app.section.reference_contract` and `app.section.reference_curves`. Public functions:

```python
build_a3_reference_contract(audit: dict) -> dict
freeze_reference_contract(value: dict) -> dict
validate_reference_contract(contract: dict) -> None
assess_reference_contract(contract: dict) -> dict
freeze_curve_bundle(value: dict) -> dict
validate_curve_bundle(bundle: dict) -> None
a3_curve_template(contract: dict) -> dict
section_curve_bundle(snapshot, result: dict) -> dict
compare_reference_curves(snapshot, result: dict, contract: dict, reference: dict) -> dict
```

Freeze returns a detached copy with a content hash. Mutated frozen records fail validation. To submit a genuine revision, remove the old hash, preserve the original values/status, include new evidence, then freeze a new record. Hashes establish consistency and change detection; they do not authenticate a vendor executable, prove a supplied source file's contents, or create a designer signature.

## Contract fields and condition checks

The contract stores `schemaVersion`, `caseId`, `sourceRegistry`, `parameters`, `criticalConditionIds`, `stageIds` and `contractHash`. Each parameter includes:

|Field|Meaning|
|---|---|
|`id`|Stable dotted audit path, or explicit added field ID|
|`value`, `unit`, `identity`|Actual recorded value and physical meaning; unknown value is null|
|`status`|`original_fact`, `derived_interpretation` or `unconfirmed`|
|`originalStatus`, `originalValue`|Retained original audit state/value; missing is not zero|
|`sourceRefs`|Registered source ID, SHA256, physical pages and precise locator|
|`derivation`|Explicit calculation/interpretation for derived values|
|`requiresConfirmation`|Original-equivalence meaning remains unresolved without supplied evidence|
|`confirmation`|Optional user-supplied evidence record, not an approval or generated signature|
|`inputPath`|Explicit solver-input binding; absent bindings cannot claim same conditions|
|`note`|Original audit qualification|

`confirmation` requires `providedBy`, `providedAt`, `sourceId`, `sourceSha256`, `locator`, `note`. Sources classified as synthetic fixtures, digitized curves, CAD drawings or application archives cannot confirm original conditions. The software never creates confirmation records. Changes to an original value or promotion to original fact require a supplied confirmation. A new native-looking filename and hash alone do not resolve any parameter.

Critical gates include numerical E and EI factor, m formula/origin/units/layer/history and effective width, head/toe boundary, water/ground/wall datum, mixed water formulation, finite load and slope method, crown-beam assembly, support installation/lifecycle, M/V and line/member force basis, historical code, characteristic combination and rebar design basis. Caller flags such as `approved=true` are rejected. Original A3 critical gates and all five stage identities cannot be removed.

The 17 added fields are:

1. `datum.wall_top_offset_m`
2. `datum.local_excavation_depths_m`
3. `datum.local_support_depths_m`
4. `datum.local_outside_water_m`
5. `datum.local_inside_water_depths_m`
6. `datum.local_layer_interfaces_m`
7. `historical_code_edition`
8. `current_review_basis`
9. `critical.m_depth_origin`
10. `critical.m_layer_transition`
11. `critical.water_combination_definition`
12. `critical.slope_pressure_method`
13. `critical.support_installation_reference`
14. `critical.stage_lifecycle`
15. `critical.characteristic_combination`
16. `critical.rebar_design_basis`
17. `critical.m_units`

Historical DB33/T1096-2014 and the current review basis are separate fields. No common C30 modulus is substituted for the unknown original E.

### Datum

Physical p32 prints 1.3 m from model ground to pile top, .325 m from pile top to S1, 3.6 m from pile top to final base and 3 m embedment. Physical p33 prints ground-depth event levels and curves spanning about1.3–7.9 m on0–8 m axes. The derived transformation is explicitly:

`z_wall = z_ground - 1.3`

The original2.05/4.9 m excavations become .75/3.6 m; S1/T1 1.625/4.6 become .325/3.3 m. Outside water .5 becomes **−.8 m**; it must not be clipped to zero. Full layer interfaces become −1.3,−.2,.5,5.7,6.3,56.3 m. Do not discard overburden above pile top. This contract does not force these values into a solver that cannot represent their original geometry and pressure conditions.

## Numerical JSON manifest

The curve bundle has schema `pitguard.reference-curves/1`, case and contract hash, source, datum, force basis, signs, effect basis, required stage IDs, stage records, reinforcement rows, tolerances and bundle hash. Each imported source specifies:

- `kind`: `native_numeric_export`, `numeric_table_transcription`, `digitized_curve`, `synthetic_protocol_fixture`, or `pitguard_result`.
- `sha256`: original source bytes' hash, not a fake extension-based identity.
- `software`, `version`: observed identity; null if unavailable. The template's9.0 is explicitly the report label, not an executable build verification.

A synthetic or digitized source can exercise the protocol but cannot pass original-software acceptance. Exact source identity and required original conditions must be supplied independently.

`datum` declares `origin`, `positive`, `offsetToWallTopM` and `evidence`. Canonical depth in metres is `(+1 if down else -1) * source_depth + offsetToWallTopM`. `above`/`below` always denote the physical wall side of a location, independent of numerical axis direction. No data-driven shift or sign fitting is allowed.

`forceBasis` declares `kind`, `pileSpacingM`, `conversion`. Single-pile forces use `identity`. Per-metre forces require `line_to_pile_multiply_spacing` and positive spacing. It applies to M, V, N and horizontal support line reactions only. Canonically named design fields are already single-pile kN/kN·m; they are not multiplied again. Actual support member axial force is a separate field; it is never inferred from a wall reaction without the external structural mapping.

`signs` explicitly defines u, M, V, pressure and reaction; mismatches are reported and not silently reversed. `effectBasis` identifies characteristic/design/unconfirmed response. The implemented comparison currently expects characteristic response. Displacement `m` is explicitly normalized to`mm`; forces use`kN`, `kN*m`, `kN/m`, `kN*m/m`; pressure uses`kPa`. Unknown units are rejected.

Each stage includes `id`, `sourceStageId`, `event`, `domainM`, `boundariesM`, `curves`, `supports`, `design`. The stage IDs must match the contract and the frozen candidate model; the source-stage identity remains recorded. Missing stages remain missing. Original installation of T1 and subsequent removal of S1 are distinct events.

Each curve includes `quantity` (`u`, `M`, `V`, `N`, `pressure_outside`, `pressure_inside`), `unit`, `basis`, `pressureIdentity`, `projection` and `samples`. Each sample is exactly `{z,value,side}`, with side `above`, `below` or `continuous`. Duplicate `(z,side)` identities, conflicting continuous/one-sided representations, nonfinite values and samples outside declared domains are rejected. Empty arrays are permitted for an intake template but cannot pass coverage. A pair of reported min/max values is not a full curve and cannot be placed in substitute samples.

Pressure identity declares stress (`effective/total/mixed/unconfirmed`), composition (`soil_only/water_only/soil_plus_water/layerwise/unconfirmed`) and role (`mobilized/imposed/net/unconfirmed`). These meanings must match before numerical comparison. Native numerical pressure and Gauss/nodal-lumping recovery are different projections; changing a label is not a legitimate conversion.

### Pressure recovery from the PitGuard result

At one material Gauss station, two nodal spring states have the same depth and different linear shape weights. The exported total pressure is:

`sum(pressureKn) / (pileSpacingM * sum(weightM)) + sum(externalWaterKpa * weightM) / sum(weightM)`

This is a **weighted Gauss/nodal-lumping recovery**, not an independent continuous pressure field. V2 uses explicit `externalWaterKpa`: it is physical pore pressure for effective separated layers and zero for total combined layers. `waterPressureKpa` remains physical pore pressure and must not be added again for total combined layers. V1 uses effective spring pressure plus hydrostatic water. No values are fabricated at boundaries or in unrepresented excavated portions. The result explicitly reports incomplete pressure boundary/domain coverage.

Service curve samples at Hermite interior extrema (`within`, or `interior` from compatible callers) become `continuous`; their depths and values are retained. Element-end jump limits remain `above/below`.

### Supports, design effects and reinforcement

Supports preserve ID, depth, lifecycle, installation-reference displacement, current displacement, horizontal reaction and optional actual member axial force. A removed support retains its known installation reference and known zero reaction; a not-yet-installed support has unavailable values. Actual member axial force missing on both sides is `not_applicable` for the horizontal section comparison and does not invalidate otherwise complete horizontal support comparison. If one side supplies it, that separate metric remains incomplete.

Design fields contain explicit effect factor, maximum design M/V and combination. Reinforcement rows retain the interval, required and provided areas separately, governing stages and method. Different methods/combinations are identity mismatches even if numbers happen to agree. These checks do not constitute a current-code full engineering approval.

## Comparison semantics

`compare_reference_curves` validates the frozen candidate result and both input documents before comparison. It requires the snapshot's `reference_contract_hash`, supplied contract hash and reference manifest's contract hash to agree. It separately verifies original-condition readiness and explicit input bindings. Unknown methods, signs, spacing, case/stage mismatches and synthetic provenance remain blockers.

Within a compatible channel the comparison uses the union of sampled depths and their physical sides, with explicit linear interpolation only between compatible adjacent sides. It never extrapolates and never interpolates across an unsampled declared boundary. Metrics include maximum absolute error and location/side, signed bias, RMSE, extrema/locations, missing samples, missing boundary depths, coverage fraction and missing length in metres. Tolerance comparison is `absolute_error <= absolute_tolerance + relative_tolerance * abs(reference)`; near-zero responses therefore use the absolute tolerance. Coverage uses missing length versus depth tolerance, not a fraction compared with a length.

Envelope comparison keeps positive/negative extremes and **all** stages tied within the declared rounding/absolute tolerance. It compares stage design effects and support installation references separately. Metadata or coverage gaps cannot be hidden by curve agreement.

Returned statuses distinguish `conditions_mismatch`, `incomplete`, `within_tolerance` and `outside_tolerance`; `numericalStatus` is separate from original-condition acceptance. `coverage.displacementMomentShearComplete` and `coverage.pressureProjectionComplete` independently identify UMV and pressure completeness. `fullCurveAcceptance` requires all mandatory coverage and original-condition checks. `formalDesignReady` remains false.

## CSV conversion

The CSV header is an empty companion template, not a native parser. Supply the JSON manifest with coordinate/sign/force definitions first. Each CSV row maps `stage_id` to a manifest stage, `quantity/unit/basis/pressure_*/projection` to one channel, and `depth_m/side/value` to one sample. Group rows by stage and quantity; reject inconsistent repeated channel metadata rather than silently selecting one row. Add supports, design effects and reinforcement in their separate JSON records. Freeze and validate the resulting JSON with the implemented API. Do not paste PDF extrema into this table as node values.

## Sources

Historical project input: `examples/frozen-section/A3-3-reference-audit.json`, citing the uploaded report and physical PDF pages. Public vendor identity/version context (accessed2026-10-25):

- https://www.qimstar.com/h-msgBoard.html — historical FRWS9.0 update listing.
- https://www.qimstar.com/h-nd-19.html — vendor FRWS9.0 scope.
- https://www.qimstar.com/h-nd-26.html — FRWS10 upgrade; its later features cannot be assumed for historical9.0.
- https://www.qimstar.com/h-col-124.html — listed FRWS8.2 manual and related resources; exact9.0 native format/export remains unconfirmed.
