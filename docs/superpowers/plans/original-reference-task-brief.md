# Task 1 brief: original conditions and complete-curve exchange

Implement in `services/api/app/section/reference_contract.py` and `reference_curves.py`; tests `test_section_reference.py` and `test_section_reference_curves.py`; example assets under `examples/project-reference/`. Root owns shared models, preprocess, service, routers, UI and exports. Do not modify those without coordination. No nested agents or Git commits while other workers share this branch; root integrates commits.

Read the spec `../specs/2026-10-25-original-project-validation-design.md` and your research `/workspace/scratch/fd5d8a28c64e/work/project_conditions_research.md`.

## Contract

- Public functions: `build_a3_reference_contract(audit: dict) -> dict`, `freeze_reference_contract(value: dict) -> dict`, `validate_reference_contract(contract: dict) -> None`, `assess_reference_contract(contract: dict) -> dict`.
- Stable shape: `schemaVersion`, `caseId`, `sourceRegistry`, `parameters` (list with stable `id`), `criticalConditionIds`, `contractHash`; assessment is derived, not a caller-controlled approval flag.
- Parameters retain original values, conversions/interpretations and evidence. States include `original_fact`, `derived_interpretation`, `unconfirmed`. Missing is null. Historical code and current review basis are different fields. No invented native extension/parser/designer signature.
- Critical conditions cover E/EI, m formula/units/depth/layer/history, width, toe, ground/wall/water datum, crown/support lifecycle, pressure/water/slope/load method, single-pile/line units, combination/design effect/rebar basis.
- Actual original image evidence supports global→wall-top conversion by subtracting1.3m; preserve both values and the explicit derivation. External water becomes−0.8m; do not clip it or discard overburden.
- Do not make completeness true solely by hashing a fake native filename. Record whether evidence is original solver/parameter export, report transcription, digitized curve or synthetic protocol fixture.

## Curve API

- Public functions: `freeze_curve_bundle(value: dict) -> dict`, `validate_curve_bundle(bundle: dict) -> None`, `section_curve_bundle(snapshot, result: dict) -> dict`, `compare_reference_curves(snapshot, result: dict, contract: dict, reference: dict) -> dict`.
- Root will add `reference_contract_hash: str | None` to v2 SectionInput. A claim of same conditions requires this value, reference.contractHash and supplied contract.contractHash to match, and the contract's material conditions to be resolved/matched. Missing conditions must yield incomplete/conditions_mismatch, never pass.
- Design a strict versioned JSON exchange manifest, canonical CSV headers/sample empty templates and documented conversion. Include source kind/hash, actual solver/version where supplied, depth origin/transform, sign conventions, force basis and explicit pile-spacing conversion, characteristic/design basis.
- Each stage contains full u/M/V curves, outside/inside pressure with total/effective/composition identity, supports and installation reference, governing stage evidence, effect factor and reinforcement demand as applicable. Require appropriate coverage before overall pass.
- Retain `above`/`below` samples at jumps; never interpolate across a jump or guess a side. Reject duplicate inconsistent points, nonfinite values, silent sign reversal, mismatched stages, out-of-domain extrapolation and unknown units. Comparing PDF maxima alone cannot pass full-curve acceptance.
- Primary pressure projection must state its Gauss/nodal-lumping meaning. Use matched samples/continuous segments with layer boundaries; missing boundary coverage stays explicit. Root may add v2 station metadata `waterPressureKpa`, `pressureBasis`, `stressMethod`; v1 uses effective soil pressure plus hydrostatic water. Keep compatibility.
- Synthetic round-trip tests may demonstrate protocol logic but must retain synthetic provenance and must not create an original-FRWS-pass claim.

## Required tests and delivery

Write failing tests before production changes. Cover real original datum conversions, missing E/no default, contract tamper, unsupported original methods, hash mismatch, field values and statuses, line/pile explicit conversion, sign identity, jump side, missing stages/coverage, extrema-only invalidity and immutable input. Persist A3-3 contract plus raw-curve empty templates. Keep shared modules unchanged. Report file: `/workspace/scratch/fd5d8a28c64e/work/project_reference_implementation.md` including interfaces, commands/results and remaining limits. Message root early when schema is stable.
