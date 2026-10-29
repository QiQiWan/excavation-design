# Geotechnical action implementation handoff

2026-10-25. No Git commit, no nested agents. Owned files only:

- `services/api/app/section/geotech_actions.py`
- `services/api/tests/test_section_geotech_actions.py`
- `docs/releases/V3_87_18_evidence/geotech_actions_sources.md`
- `docs/releases/V3_87_18_evidence/geotech_actions_validation.json`

## Implemented interfaces

`vertical_stress(layers, depth_m, surface_m, water_m, gamma_w)` returns total_vertical_kpa, effective_vertical_kpa, pore_pressure_kpa, method_id. Plain layer dictionaries use existing top_m/bottom_m/gamma_unsat_kn_m3/gamma_sat_kn_m3 fields. Layer flags are intentionally irrelevant to physical column weights and full head. Complete ordered column coverage is required. Water above surface is explicitly supported as ponding: its weight is added to total, and full head is subtracted for effective. This does not reconstruct original terrain water boundaries; root can retain a narrower v2 admissibility guard.

`strip_vertical_increment(q_kpa, near_m, far_m, depth_m)` implements the independently integrated vertical Boussinesq strip influence with explicit surface limits.

`rectangle_vertical_increment(q_kpa, near_m, far_m, along_start_m, along_end_m, depth_m)` uses the signed closed corner primitive and explicit edge/corner surface limits. Along-wall position is never inferred. Neither function is a direct lateral-wall pressure method.

`rankine_pressure_state(total_vertical_kpa,pore_pressure_kpa,surcharge_vertical_kpa,phi_deg,cohesion_kpa,k0,mode)` returns p0_kpa, active_kpa, passive_kpa, external_water_kpa, total_vertical_kpa, effective_vertical_kpa, surcharge_vertical_kpa, pressure_basis, method_id. Raw total/effective fields exclude surcharge. Modes exactly effective_separated/total_combined. No initial-pressure clipping. Negative effective stress is rejected beyond1e-8kPa; tiny roundoff is zeroed. Combined strength-parameter identity belongs to caller validation.

`active_wedge_resultant(surface_vertices_m,height_m,gamma_kn_m3,phi_deg,uniform_surcharge_kpa=0.0,strip_loads=())` uses actual terrain polygon area and strip-overlap load, active equilibrium P=Wtan(α−φ), maximized over geometry/load-edge-partitioned angle intervals. Vertices are (x,upward elevation) from walltop(0,0); monotone rising terrain and horizontal tail. Dry homogeneous c=0 smooth vertical wall only. Returns active_resultant_kn_per_m, critical_angle_deg, contact_x_m, wedge_area_m2, wedge_weight_kn_per_m, surcharge_force_kn_per_m,total_vertical_force_kn_per_m, method identity, input identity and standalone_resultant_only status. No p0/passive/distributed/history inference.

## Tests-first record

Initial command:

`/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest services/api/tests/test_section_geotech_actions.py -q`

Before implementation: ModuleNotFoundError for app.section.geotech_actions. After implementation:13 passed,1 failed due to exact80 comparison against79.99999999999999. Changed test to pytest.approx; formula unchanged. Added ponded-water/column-coverage, mixed-mode water resultant and moment, wedge scaling and far-berm limit, and rectangle small-depth tests. Final command above:18 passed in0.15s.

## Independent numerical evidence

Four rectangle closed-form values compared with separate100×100 Gauss integration of original3qh³/(2πR⁵) kernel, maximum absolute error5.33e-15kPa. q30,near4.3,far16.3,assumed y±32.5,h4.9 gives3.224448523277923kPa. Infinite strip gives3.2318455940550974kPa. These are vertical stress increments and use a centered section assumption; not A3-3 lateral load validation.

Flat wedge H6,γ18,φ30 gives108.00000000000001kN/m, critical angle60° within2e-5°. Uniform q10 gives128kN/m. Dry homogeneous berm fixture with vertices(0,0),(1.7,0),(3,1.3),H6,φ30,γ18,stripq30at4.3–16.3 gives146.5450540766689kN/m. It agrees with separately constructed polygons and Cartesian force equilibrium at20,000 trial angles within2e-7relative tolerance. This fixture is explicitly not original A3-3 layered wet soil.

Mixed soil/water φ0 test gives total80kPa for both modes atz4 withγsat20 andγw10. Alternating pointwise modes reproduces pure-water resultant80kN/m and first moment213.3333333333333kN·m/m over4m. Ponded1m over4m saturated soil gives total90,effective40,pore50kPa.

## Limits and root-owned integration

No original FRWS9 numerical equivalence claimed. No structural solver, v1 data or constitutive state changed. Root owns v2 models/preprocess and independent structural integration. True terrain resultants remain independent until K0/passive/distribution/history coupling is justified. Source register contains primary/institutional URLs, formulas and domain statements.
