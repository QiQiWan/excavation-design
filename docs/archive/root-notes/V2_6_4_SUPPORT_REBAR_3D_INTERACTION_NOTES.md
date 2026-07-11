# V2.6.4 Support Rebar and 3D Interaction Closure

## Scope

This iteration closes the remaining operator-facing issues around retaining-wall cloud consistency, support-plan closure, concrete/steel support labeling, concrete support reinforcement visibility, lap/anchorage/node densification annotations, and 3D borehole interaction.

## Changes

- The retaining-wall 3D force/deformation cloud now consumes all wall-force samples and aggregates by wall segment and grouped face segments, preventing missing wall sides in L-shaped pits.
- The support quality plan uses a closed polygon for the excavation boundary.
- Cast-in-place concrete support reinforcement now includes longitudinal bars, closed stirrups, distribution bars, tie/erection bars, and lap-zone additional bars.
- Support rebar visualization uses balanced sampling across walls, beams, supports and nodes so support detailing remains visible.
- The rebar viewer adds support detailing labels for staggered lap zones, end anchorage, closed stirrups, distribution bars, tie/erection bars and lap additional bars.
- The 3D engineering viewer displays a hover badge and keeps the clicked object or borehole layer details in a right-side property card. Borehole clicks show the complete stratum color/name/elevation/depth distribution.

## Engineering Boundary

The reinforcement geometry is a design-assist detailing proxy. Final splice length, hook geometry, construction joints, cage segmentation, lifting plan and node reinforcement must be reviewed against project-specific drawings and construction requirements.
