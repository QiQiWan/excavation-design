# Task 2 brief: explicit geotechnical actions

Own `services/api/app/section/geotech_actions.py` and `tests/test_section_geotech_actions.py`; method sources and reproducible validation under docs/releases/V3_87_18_evidence. Do not edit models/preprocess/solver/service/independent/UI. No nested agents or Git commits. Root integrates.

Use `/workspace/scratch/fd5d8a28c64e/work/soil_differences_research.md` and design spec. Implement the scoped method proposal now.

## Public interfaces

- `vertical_stress(layers, depth_m: float, surface_m: float, water_m: float, gamma_w: float) -> dict`: total/effective selfweight and pore pressure, explicit units. Take list of plain layer dictionaries with existing layer key names.
- `strip_vertical_increment(q_kpa: float, near_m: float, far_m: float, depth_m: float) -> float`: analytic halfspace vertical increment at wall line, nonnegative finite dimensions. z=0 limits explicit; finite strip at a>0 tends zero.
- `rectangle_vertical_increment(q_kpa: float, near_m: float, far_m: float, along_start_m: float, along_end_m: float, depth_m: float) -> float`: closed corner formula if independently validated, otherwise converged quadrature with explicit errors. Coordinate reference and depth are explicit.
- `rankine_pressure_state(total_vertical_kpa: float, pore_pressure_kpa: float, surcharge_vertical_kpa: float, phi_deg: float, cohesion_kpa: float, k0: float, mode: str) -> dict`: modes `effective_separated` and `total_combined`, output p0/pa/pp soil/conventional constituent plus separately applied water pressure. Return effective/total stress and method trace. Exact return keys sent early to root. No silent p0 clipping; reject materially negative effective stress beyond numerical tolerance.
- `active_wedge_resultant(...) -> dict`: dry, homogeneous, c=0, vertical smooth wall, piecewise-linear terrain with horizontal tail, optional uniform/finite strip q integrated over wedge footprint. Choose a clear typed signature and send to root. Outputs Pa per metre, critical plane, wedge area/weight and input/method identity. Reject invalid polygons/sliding intersections; don't pretend to provide p0/passive/distribution/staged wall solution.

## Physical/tests

Strip equation is the independently derived z-vertical kernel integral. Rectangle compared to independent 2D Boussinesq kernel quadrature. Track wall-line versus pile centre input; do not assume65m strip position. Water total/effective integration is independent of flags in upper layers; only current station mode selects sigma and external water once. Test gamma_sat20,gamma_w10,phi0,z4: two modes total pressure80kPa; mixed-layer mode cannot lose/repeat head.

Wedge test H6,gamma18,phi30,c0,flat: Pa108kN/m, critical60deg; full-width q10: Pa128. Include independently calculated geometry/force-polygon and stepped/bermed geometry. Source constraints and meaningful negative tests.

Root will integrate finite loads and station-mode water treatment into v2 with explicit assumptions. Terrain remains standalone method verification while K0/passive/history are unknown; never silently use height-equivalent surcharge or dPa/dH as original pressure. Stage history strategies handled by root separately.

Save commands/results/sources in `/workspace/scratch/fd5d8a28c64e/work/geotech_actions_implementation.md`. Use tests-first and keep scope honest. Public standards/technical statements require primary sources.
