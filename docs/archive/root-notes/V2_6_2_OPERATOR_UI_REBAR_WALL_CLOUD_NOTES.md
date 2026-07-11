# V2.6.2 Operator UI, Rebar and Wall Cloud Fixes

This iteration keeps the normative-design algorithm route and focuses on fixing user-facing issues observed in the current project.

## Fixed

1. Audit-locator buttons in the 3D viewer now use explicit dark text and visible hover states.
2. Retaining-wall and support design tables show latest calculated values when component objects do not yet persist design fields.
3. Replacement-path and check-summary sections no longer display raw JSON.
4. Development/version wording is filtered from result panels.
5. `/docs` is added as an operation-documentation page in the front-end application.
6. Compliance formulas are rendered with engineering symbols and wrapped text instead of long internal parameter names.
7. Module ledger cards separate status and evidence text and support line wrapping.
8. Cast-in-place concrete supports are assigned preliminary longitudinal bars and closed stirrups. Steel supports remain explicitly marked as steel, without RC reinforcement.
9. Rebar visualization now shows staggered lap polylines and closed stirrup hoops for supports/beam cages.
10. Wall deformation, bending moment and shear can be inspected in a 3D wall-cloud viewer.
11. The engineering 3D viewer supports hover highlighting and borehole-click stratum details.

## Boundary

The system still uses normative algorithms and rule-based detailing. Formal construction documents require enterprise review and registered engineer signoff.
