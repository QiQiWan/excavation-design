# PitGuard V2.0.9 Optimization Notes

## Main upgrade scope

This iteration upgrades the support-layout candidate workflow from static candidate display to an interactive design-decision loop:

1. Support candidate delta animation
   - Frontend candidate plan SVG now overlays original support lines and candidate support lines.
   - When a candidate is selected, SVG animation shows the motion from the original line to the candidate line.

2. Local candidate locks
   - Backend schema now stores support whole-line locks, endpoint locks, support-layer locks, and obstacle/haul-road boundary locks.
   - Optimizer preserves locked supports, locked endpoints, locked levels, and locked obstacle boundary constraints.
   - Frontend exposes local lock controls for support line/start/end, level index, and obstacle boundary.

3. Weight visualization
   - Objective weights are exposed as sliders in the retaining-system workflow.
   - Frontend computes a local ranking preview immediately when weights change.
   - Backend still receives objectiveWeights for authoritative candidate generation.

4. Multi-candidate full calculation comparison
   - Added `/api/projects/{project_id}/calculation/run-candidate-comparison?top_n=3`.
   - The calculation workflow can run complete calculation for the top candidate schemes A/B/C and summarize axial force, displacement, wale internal force, stability, IFC risk, and formal quality-gate state.

5. DOCX report candidate comparison chapter
   - DOCX report now adds `方案 A/B/C 完整计算比选` when candidate full-calculation results are available.
   - The section includes support count, column count, max axial force, max displacement, wale moment/shear/deflection, min stability factor, IFC risk, and formal-gate status.

## Validation performed

- Python syntax check passed for modified backend modules.
- Frontend `npm run build` passed under `apps/web`.
- Targeted backend tests passed:
  - `test_calculation_result_schema`
  - `test_support_layout_spans_short_direction_and_adds_corner_diagonals`
  - V2.0.6/V2.0.7/V2.0.8 candidate optimization tests
  - V2.0.8 report candidate score chart test
- Smoke workflow passed for local locks + candidate optimization + top-1 candidate full-calculation comparison.

## Known remaining engineering work

- Full pytest suite was not run to completion in this session because it is long-running; targeted tests and smoke workflow passed.
- Frontend bundle warns that one chunk exceeds 500 kB. This does not block compilation, but later can be optimized with route-level dynamic imports.
- Candidate full calculation currently runs sequentially inside a thread-pool executor interface. It is safe for the current in-memory/data-store pattern; production deployment should connect this to a persistent job queue when calculations become heavier.
