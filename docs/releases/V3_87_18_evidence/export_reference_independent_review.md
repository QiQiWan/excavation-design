# Reference/export independent review and authorized follow-up fixes

2026-10-25. Initial read-only review covered reference_workflow.py, storage.py, routers/sections.py, review_export.py, export.py and relevant reference_curves/contract validation paths. Root subsequently authorized two narrow fixes in reference_workflow.py/storage.py with tests in test_section_reference_workflow.py. No other product files were modified in this review task; no Git commit.

## Findings and disposition

### 1. Uncompared curve attachment bypassed validation — fixed

Previously reference_supplement validated a curve bundle only indirectly when comparison was supplied. With comparison=None and a contract bound to the snapshot, a malformed bundle or a separately sealed bundle naming a different contract/case was accepted and given a supplementHash. build_minimum_package recomputes the supplement as its export gate, but repeated that same bypass, so the direct export API could attach mixed/invalid evidence. Normal SectionStore generation did not usually produce this combination, but the public export helper supported it.

Fix: every non-null curves attachment now passes validate_curve_bundle and must match both contractHash and caseId of its accompanying contract, before comparison/no-comparison branching. A valid uncompared curve attachment remains possible, with comparison=None and unchanged incomplete contract assessment; it never claims comparison acceptance.

Red tests observed all three corruptions accepted before fix: invalid_bundle, contract_mismatch and case_mismatch. All now rejected with ValueError.

### 2. Lost saved comparison dependencies silently became contract-only export — fixed

Previously SectionStore.reference_supplement caught FileNotFoundError from load_latest_reference_comparison regardless of where it arose. The same exception represented an absent comparison index and a saved comparison whose record/result/snapshot/contract/curve dependency had disappeared. The latter silently returned a supplement without the prior comparison/curve evidence.

Fix: load_latest_reference_comparison still raises FileNotFoundError when no index exists. Once an index is found, FileNotFoundError/KeyError/TypeError while recovering the record or dependencies becomes a ValueError explicitly identifying missing/damaged dependency evidence. The package router already maps ValueError to422. Missing evidence is not silently discarded. Valid no-comparison workflow remains unchanged.

Red tests used real saved snapshot/result/contract/curves/comparison, then separately deleted the curve file or comparison-record file. Both formerly fell back; both now raise. Test-owned files only were removed.

### 3. Hard-coded A3 coordinate facts in generic reference export — assigned to root

append_reference_document unconditionally emitted the statements that original ground-to-pile-top offset is1.3m and local outside water depth is−0.8m, even for a generic imported contract. The parameter-table rendering is data-driven, but the adjacent prose was not. Root confirmed ownership of the fix in review_export.py. This agent did not modify that file and does not claim the fix verified. Recommended behavior: render values/derivation from validated contract fields, or restrict historical A3 narrative to its actual verified scope and values.

## Identity-chain review

- snapshot: content hash and analysis hash validated on load through FrozenSectionSnapshot; v1 serialized form excludes v2 keys.
- result: store reloads the associated snapshot, validates result digest and internal contract. Routes select server-stored result by resultHash; package generation receives that result and source snapshot, not caller-supplied numeric results.
- contract: stored by contractHash with structural/source-reference validation; uncompared supplement requires it to match snapshot.reference_contract_hash.
- curves: stored by bundleHash; source and curve semantics validated. section_curve_bundle validates source result before deriving characteristic M/V/u and weighted total-pressure output; source.sha256 is the source resultHash. New attachment validation closes the no-comparison path.
- comparison: record binds snapshot/result/contract/curve hashes plus deterministic comparison report and comparisonHash. Storage rederives the comparison report from those immutable inputs, so rehashing forged acceptance booleans does not validate them. This rederivation is a comparison check, not a structural solve or reinforcement redesign.
- supplement/package: supplement includes detached evidence and its own hash; export validates result and supplement before output-directory creation, deep-copies inputs, and checks the sealed result has not changed. Package filenames are allocated uniquely; manifest and checksum listing identify emitted bytes. Manifest keeps formalConstructionIssue=false and engineeringStatus=review_required.

Comparisons with incomplete original facts or empty template curves remain incomplete/conditions_mismatch, sameConditionsVerified=false and fullCurveAcceptance=false. Per-channel numeric matches can remain visible alongside global blockers. I did not find a path in the inspected normal stored-comparison workflow that converts an empty original curve template to a verified original-project match.

## Numerical table/drawing scope

- engineering_checks.csv carries each result's hashes, local status, demand/capacity/unit, governing case, required fields, method scope and sources. It does not select new bars.
- reinforcement.csv remains explicitly labelled N=0 pure-bending reference and keeps engineering status separate.
- soil_pressure_states.csv retains both nodal-lumped states at a common Gauss depth. Unit conversion divides by spacing×station weight; external water is added once to obtain total pressure. Physical pore pressure and separately applied external water are distinct columns.
- stage_pressures.pdf combines the two nodal-lumped values at each same-layer/same-depth station by weights, adds water once, breaks curves by soil layer and does not extrapolate boundary values. The notes disclose this projection rather than calling it an original-software sample.
- rebar_geometry.pdf/dxf derives only the given cage and explicit external tangent hoop arrangement. It states geometry-only scope, carries snapshot/result identities and does not invent anchorage/node details. Existing failure results remain failure statuses in engineering tables.
- _inspect_outputs' pass means file/dimension/equation/result-preservation consistency; output also explicitly says visual review is not automatically certified and engineering remains review_required. This is not an engineering-pass gate.

## Verification evidence

Executed only the authorized targeted file, repository-root:
`PYTHONPATH=services/api /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest services/api/tests/test_section_reference_workflow.py -q`

Before fixes:5 failed,6 passed in10.60s. The five failures matched the identified bypass/fallback defects.
After fixes:11 passed in10.63s, exit0.

No full-suite run and no rendering-heavy export test was started in this follow-up; root owns final combined workflow/export/v1 regression gate. The existing v2 export tests were inspected for preservation of failures, missing crack values, reference gaps, source identities and mixed-result rejection.

## Root follow-up on finding3

Root reported replacing the hard-coded1.3/−0.8 datum prose with the current contract field table and a generic coordinate-consistency note; root reports a dedicated red-to-green test. This agent did not edit or independently re-run that test. The original finding remains above as the audit trail. Root is preparing the unified regression after SLS coverage and font-embedding work.
