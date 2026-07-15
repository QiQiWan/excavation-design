# V3.21.0 Clean Support Topology and Industrial Maturity Review

## Implemented

- Added legal internal T/Y/X junction counting in addition to illegal crossing detection.
- Added high-degree junction, cross-level projection and plan-intersection complexity metrics.
- Raised illegal crossing and junction complexity to primary optimization objectives.
- Added lexicographic candidate ranking: feasibility, crossings, junction complexity, high-degree nodes, internal nodes, auxiliary members, total members, then aggregate score.
- Added `clean_support_layout` preset and made it the default frontend preference.
- Added crossing/junction metrics to the retaining-system viewer and A/B/C candidate cards.
- Added V3.21 backend regression tests.
- Unified frontend, backend package and runtime version identifiers.
- Added industrial maturity assessment and P0-P3 roadmap.

## Verification

- Targeted backend tests: 9 passed.
- Frontend tests: 13 passed.
- Frontend production build: passed.
- Full backend suite remains affected by pre-existing stale test assumptions and project-geology preconditions; see engineering note 24.
