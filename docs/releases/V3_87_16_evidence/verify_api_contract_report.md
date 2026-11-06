# Verification API/task contract report

## Scope

Implemented the delegated verification boundary in:

- `services/api/app/tasks/manager.py::_run_calculation_full`, plus explicit design-mode payloads at its internal optimization callers.
- `services/api/app/routers/calculation.py::run`.
- `services/api/app/services/construction_stages.py::select_calculation_case_for_verification`.
- Authorized follow-up: `services/api/app/storage/repository.py::get_for_calculation/require_for_calculation` keyword `migrate`.
- New tests: `services/api/tests/test_verify_api_contract.py`.

No engine, schema, or assurance production edits were made in this task. Root owns the underlying `run_calculation(mode='verify'|'design')` and assurance implementation.

## Public behavior

`POST /api/projects/{project_id}/calculation/run` accepts `mode: Literal['verify','design']`, default `verify`. The callable also explicitly rejects unknown values. Task operation `calculation_full` takes payload mode with the same default and supported values. Verification requires `topN=0` (or omitted); nonzero candidate requests produce an actionable error directing callers to explicit design mode.

Verify performs these steps:

1. Capture the repository revision before loading project content. Capturing before load also prevents the load-to-revision race from accepting an unseen concurrent edit.
2. Hydrate with `require_for_calculation(..., migrate=False)`. No version migration, design-default adoption, or stale-case regeneration occurs during this load.
3. Retain the existing `design_basis_confirmed` prerequisite. Validate saved stages and solver/event inputs; do not add documentation-only pre-execution formal gates.
4. Select the existing saved case through the read-only helper.
5. Task execution retains the read-only memory/resource safety budget check and cancellation checks.
6. Call `run_calculation(project, case, auto_repair=False, mode='verify')`.
7. Attach construction-stage selection metadata, then reapply calculation assurance so the published result hash includes that metadata.
8. Append result and mark derived calculation-state metadata current. Do not call `mark_wall_length_recalculated`, because it writes retaining-system layout input/history.
9. Save with `expected_revision` from step 1. Concurrent changes reject the save with the repository's 409 conflict behavior.

Verification does not enter repair-capable `_assert_calculation_qualified`, support-reference repair, default case generation, semantic synchronization, intelligent closure, automatic interventions, or candidate comparison. Payload flags such as `forceClosure` and `autoInterventions` cannot override verification mode.

## Case selection contract

`select_calculation_case_for_verification(project, case_id=None) -> (CalculationCase, evidence_dict)` returns the actual existing saved object without modification.

Priority: latest explicitly user-defined locked case; otherwise requested saved ID; otherwise last saved case. Missing cases and stale references fail with instructions to confirm/save the stage input explicitly. Existing auto-generated cases are preserved rather than regenerated.

The helper calls existing case validation and the nonmutating stage-state builder. This checks stage sequencing, removed-support reuse, newly activated support exposure, and slab activation conditions. If semantic design controls exist, it compares the saved stage count/order/source identity, excavation elevation, stage type, active/removed supports, zone, replacement conditions, effective groundwater inputs, and surcharge. Disagreement fails with the affected fields; no synchronization occurs.

The selection evidence includes original source, `preserved=True`, selected/requested IDs, stage count, validation output, `selectionMode='verify_saved_case'`, whether semantic controls were checked, and `synchronized=False`.

## Explicit design operations retained

The ordinary design branch continues to support its existing qualification/recovery/closure behavior. Its direct engine invocation now explicitly uses `mode='design'`. Internal core-design, rebar-design, recovery, and industrial-closure calls into `_run_calculation_full` now pass design mode explicitly. Formalization uses design mode only when it is invoking structural auto-closure; a pure recalculation uses verify. Full-delivery forwards its caller's payload/mode.

Construction-stage selection and final comparison metadata in the design branch are also followed by assurance refresh before persistence.

## Load-time migration defect found and fixed

Repository `require` and `require_for_calculation` previously invoked `_migrate_loaded_project`. That method calls design-default migration and can call `invalidate_calculation_state(..., rebuild_cases=True)` when the latest results or topology are stale. Therefore a nominally read-only case selector could receive already-regenerated stages.

The new keyword `migrate: bool=True` preserves all prior callers by default. Both verification entry points explicitly use `migrate=False`. Artifact hydration remains available without those design/stage mutations. Missing/stale stages are reported rather than implicitly adopted.

## Verification evidence

Initial behavior run after writing endpoint/task/selection tests: **10 failed**. Failures demonstrated default route case regeneration, task repair-capable qualification, absent revision protection/selector, and missing mode support.

After implementation: **10 passed**. Additional regression coverage grew to **15 passed in 0.62s** with:

- Default route and task preserve retaining-system content and saved cases.
- Forbidden repair/closure/synchronization/candidate functions are never reached in verify, including when force flags are supplied.
- Both route and task save the initial revision and reject a simulated concurrent edit.
- Nonzero verify candidate count is rejected before saving.
- Saved default and locked-user priority selection work without regeneration.
- Missing/stale/semantically mismatched cases fail without mutation.
- Matching semantic controls remain unchanged and never invoke synchronization.
- Explicit design task reaches engine design mode.
- Both entry points execute two sequential verification saves with expected revisions 7 then 8, using real assurance; each contract is current, and reapplying assurance leaves the result hash unchanged.
- Repository nonmigrating calculation load preserves settings and saved stages and never calls migration.

Command: `/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest tests/test_verify_api_contract.py -q` from `services/api`.

`git diff --check` and focused Python compilation of the four modified production modules passed. Full-suite execution belongs to root.

## Test harness limitation

The initial HTTP TestClient attempt hung inside AnyIO/Starlette thread-portal dispatch before the endpoint ran; faulthandler located `run_sync_from_thread`/`start_task_soon`. Those runs were interrupted. The delivered tests invoke the actual registered route function directly, using a repository double and numerical-engine boundary mock, and invoke the real task orchestration method. Separate tests use real assurance and repository loading. HTTP transport behavior itself was not certified by this test file; endpoint/type declarations are unchanged FastAPI mechanisms apart from the new Literal mode parameter.

These tests verify mutation/persistence boundaries. They do not validate the engineering solver or override its model limitations/formal gates.
