# Original-reference frontend implementation

2026-10-25. Worktree: `/workspace/scratch/fd5d8a28c64e/work/foundation_section_validation`. No Git commit, no dependencies added, no backend/geotechnical files changed in this frontend task.

## Owned files

Modified:
- apps/web/src/components/FrozenSectionPanel.tsx
- apps/web/src/components/FrozenSectionPanel.css
- apps/web/src/components/FrozenSectionPanel.test.tsx
- apps/web/src/api/client.ts
- apps/web/src/api/client.section.test.ts

Added dedicated children:
- apps/web/src/components/FrozenSectionReferenceReview.tsx
- apps/web/src/components/FrozenSectionReviewDetails.tsx

## Behavior

Input import/editor now accepts frozen-section/1 and /2. Freeze response must match submitted schema and section identity, and its input schema must agree. Existing v1 workflow, explicit restore, package download and request-generation guards remain. V2 example has its own button. Method details are collapsed: current-layer stress basis, finite strip/rectangle coordinates and their declared basis, stage active loads and plastic-history actions. Soil parameter headings no longer label total-stress values as effective c′/φ′. Depth wording follows actual pile-top coordinate convention.

Engineering checks display per-item demand, capacity, units, localized status, scope, missing inputs and limitations; detailed calculations remain collapsible. pass/fail/missing_input/outside_method_scope/calculated/not_calculated remain distinct. A calculated service-stress result is not labeled passed. engineering_fail gets an explicit failed-check notice while formal overall review remains pending. Pure-bending success does not promote total engineering approval.

Reference panel is closed initially and does not fetch automatically. It loads the audited A3-3 contract/assessment/empty curve template on explicit request. Parameters show original value, current/derived value and evidence status, initially up to12 critical fields; a control expands all. Missing conditions are available separately. Editable contract download omits only the content hash; original values/evidence and unknown nulls are retained. Upload routes send exact parsed user JSON for server validation and sealing. Uploading another contract clears the prior curve choice and comparison.

Binding writes only reference_contract_hash into an already-v2 input via the existing edit() path, immediately invalidating snapshot/results. V1 cannot be silently converted; missing model parameters are not filled.

Comparison is separate from the immutable result. Following root's finalized API contract, its outer schema is pitguard.reference-comparison-record/1; the embedded report schema is pitguard.reference-comparison/1. Before display, the UI validates outer schema, snapshotHash, resultHash, contractHash, curveBundleHash, nonempty comparisonHash and report. Display additionally binds to the parent's request generation. Editing, freezing, recovering or rerunning invalidates the previous comparison even if a later analysis returns the identical resultHash. Late comparison responses cannot revive it. Contract/curve uploads and unmount also invalidate their pending local request.

Comparison JSON downloads the independent sealed record. Current result curve download verifies bundleHash and source.sha256===current resultHash before offering the JSON. Both downloads suppress late responses after parent input/result generation changes. Same-source ZIP uses the unchanged endpoint; root backend owns attaching the latest stored comparison.

## API methods

- getSectionExampleV2 → GET /api/sections/example-v2
- getA3Reference → GET /api/sections/reference/a3-3
- saveSectionReferenceContract → POST /api/sections/reference/contracts, exact JSON object
- saveSectionReferenceCurves → POST /api/sections/reference/curves, exact JSON object
- compareSectionReference → POST /api/sections/results/{encoded resultHash}/compare-reference, {contract_hash,curve_bundle_hash}
- getSectionCurves → GET /api/sections/results/{encoded resultHash}/curves

All reuse authenticated section request options, no retries, no cached/deduplicated reference state.

## Verification

Tests-first run before implementation:5 new tests failed because v2/reference APIs and UI were absent;21 existing tests passed. Final focused test command:

`npm test --prefix apps/web -- src/components/FrozenSectionPanel.test.tsx src/api/client.section.test.ts`

Result:35 passed in2 files. Covers existing13 panel cases and8 client cases plus v2 acceptance/schema mismatch, binding invalidation without parameter fabrication, exact endpoint/payload mapping, all engineering statuses, five independent response-identity mismatches, successful comparison download, result curve source identity, editable contract/empty-template downloads and contract upload, input-edit late-response rejection, and same-hash recomputation late-response rejection.

`npm run build --prefix apps/web` succeeded: TypeScript project build and Vite production bundle. Existing >500kB Three.js chunk warning remains; the frozen-section bundle is about32.55kB (9.70kB gzip). No browser initialization was used per task instruction.

`git diff --check --` on modified owned files passed.

Dependency runtime: existing dependencies were reused via symlink `apps/web/node_modules -> /workspace/scratch/fd5d8a28c64e/work/foundation_design/apps/web/node_modules`. This is an untracked workspace runtime link, not a source/dependency change; do not include it in a Git commit. npm generated the standard engineeringUnits file without a tracked content change.
