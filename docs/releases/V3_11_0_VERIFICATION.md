# V3.11.0 Verification

## Scope

This verification covers the rebar ZIP delivery fix, project-aware standards traceability, online engineering documentation, completion-status consistency, numerical matrix quality gates and large-package export performance.

## Automated tests

- `tests/test_v3_11_0_standards_rebar_docs.py`: 7 passed.
- Affected regression group (`V3.10 P0-P2`, `V2.5 closure`, `V3.11`): 15 passed.
- Task/health/project smoke group: 3 passed.
- Frontend Vitest: 8 files, 10 tests passed.
- Frontend TypeScript and Vite production build: passed.
- Python `compileall`: passed.

The complete isolated fast gate was started. The V3.11, V3.6 and V3.5 groups completed with 18 passing tests before the 360-second execution window ended while the drawing-rule group was running. No assertion failure was reported before the time limit. The affected and newly modified modules were then verified separately as listed above.

## Full workflow regression

The rectangular-pit sample workflow completed all 16 steps, including geology, support design, staged calculation, IFC, DOCX and JSON outputs.

- Coupled-system cases: 28.
- Matrix condition number range: 182.086 to 29,800.592.
- Maximum relative equation residual: approximately `2.315e-13`.
- Numerical equilibrium status: all pass.
- Check summary: pass 237, warning 12, fail 0, manual review 1.

## Rebar package regression

A full-flow project generated:

- 12,000 individual bars in the geometry export;
- 33,844 fabrication pieces;
- XLSX, complete CSV schedules, complete JSON geometry, check tables and usage guidance;
- ZIP size approximately 4.06 MB;
- export time approximately 9.4 seconds in the verification container.

The workbook is capped at 5,000 rows per sheet for interactive use. The manifest identifies every truncated workbook table and points to the complete CSV/JSON source. Engineering failures and omitted-bar counts are retained in the package and prevent treating a successful download as fabrication approval.

## Remaining engineering boundary

- The standards matrix traces implemented rules and project checks. It does not reproduce or replace the complete standards.
- Local standards, review comments, expert-review conditions and enterprise details remain project-level inputs.
- External three-dimensional finite-element execution remains outside this release.
- Formal issue remains controlled by the project gate, professional review, revision and signoff status.
