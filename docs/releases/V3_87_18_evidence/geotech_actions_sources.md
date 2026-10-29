# Explicit geotechnical actions: sources and scope

Method implementation is `services/api/app/section/geotech_actions.py`. Validation is `services/api/tests/test_section_geotech_actions.py`, with numerical results in `geotech_actions_validation.json`. All dimensions are SI; wedge forces are per metre of wall, not per pile. These methods are not a reproduction of FRWS9.0.

## Primary sources

1. NPTEL, *Advanced Geotechnical Engineering*, rectangular loaded area, elemental vertical stress kernel and equation71. Institutional host: https://vidyamitra.inflibnet.ac.in/data-server/eacharya-documents/55da9cb2e41301e54e2caeb7_INFIEP_206/20/ET/206-20-ET-V1-S1__20.pdf . Boussinesq kernel is 3q h³/[2π(x²+y²+h²)^(5/2)]. Production uses its signed corner primitive; tests independently integrate the original kernel with tensor Gauss quadrature.
2. Caltrans, *Trenching and Shoring Manual*, AppendixC, July2025, ppC16–17: https://dot.ca.gov/-/media/dot-media/programs/engineering/documents/structureconstruction/ts/ts-app-c-a11y.pdf . Distinguishes vertical Boussinesq stress combined with an earth-pressure coefficient from the empirical Teng rigid-wall lateral strip formula. The module implements the former vertical-stress influence, without the latter's factor of two.
3. Caltrans, same manual, Chapter4, §4-5.01, eq4-5-5: https://dot.ca.gov/-/media/dot-media/programs/engineering/documents/structureconstruction/ts/ts-chpt-4-a11y.pdf . Active trial wedges maximize the equilibrium resultant. Our explicit specialization sets cohesion, wall friction and wall adhesion to zero: P=W tan(α−φ), including actual terrain polygon weight and footprint surcharge. Source is a method reference, not a governing Zhejiang design code.
4. USACE, EM1110-2-2504, 31March1994, p3-4 paragraph8: https://www.publications.usace.army.mil/Portals/76/Publications/EngineerManuals/EM_1110-2-2504.pdf?ver=qHEac96DMIScmfwrQ21Cmg%3D%3D . Total-stress analysis and effective-stress analysis require corresponding strength parameters. Our pointwise mode convention is explicitly declared; it does not establish FRWS's mixed-layer convention.

Retrieved 2026-10-25. No source establishes the original project's finite-load transform, topographic pressure distribution, unloading/reset policy, or FRWS9.0 soil spring implementation.

## Formulas and independent checks

With R=√(x²+y²+h²), the signed rectangular corner primitive used is

F(x,y,h) = [atan(xy/(hR)) + xyh/R (1/(x²+h²)+1/(y²+h²))]/(2π).

Signed inclusion/exclusion at the four rectangle corners produces Δσv/q. The implementation uses atan2 and normalized terms. Independent tests integrate the elemental kernel, not this primitive. Strip integration gives q/π[atan(b/h)−atan(a/h)+bh/(b²+h²)−ah/(a²+h²)]. At a=0,b=h the ratio is 0.40915494309189535. Surface edge/corner limiting values are explicit.

The retained-halfplane load influence cannot exceed q/2 at the wall line. A load described as an all-ground uniform surcharge is a distinct input, not the limit of a unilateral strip interpreted as a whole-plane load. Horizontal offset refers to the supplied evaluation line; there is no implicit pile-radius correction. Along-wall position is supplied explicitly; 65m is not automatically centered or infinite.

Water selfweight integration ignores layer analysis flags. Total stress includes ponded-water weight if water lies above the soil surface. Effective stress subtracts the complete local hydrostatic head. At each station separated mode adds external water once; combined mode designates its computed pressure as total and adds no external water. For φ=0,γsat20,γw10,z4, both formulations give80kPa total pressure. Mixed mode hydrostatic resultant and first moment are checked analytically.

## Wedge method boundaries

Only dry, homogeneous, cohesionless soil; smooth vertical wall; piecewise-linear nondecreasing surface with a horizontal tail; two-dimensional loading. Terrain vertices use upward elevations from wall top, and cannot represent vertical steps. Actual polygon area and actual load overlap enter the equilibrium. Geometry and load-edge rays partition the angle search into smooth intervals, each searched by deterministic golden-section maximization. Independent tests use a separately constructed polygon and Cartesian force equilibrium over20,000 angles.

The output is a resultant with its critical wedge, not a wall pressure distribution. It contains no K0, passive state, local nonlinear spring law, pore-pressure solution, stage-history model or soil slope stability safety factor. It must not be inserted directly into dual-EPP structural analysis. No height-equivalent surcharge or derivative of the resultant is substituted for a sourced slope-pressure law.

A3-3 has mixed layers, water, cohesion and a finite-length load, so it is outside this dry homogeneous wedge domain. Its known geometry may inform an explicitly artificial method fixture; it cannot make that fixture an equivalent historical-project analysis.

## Reproduction

From repository root:

```sh
/workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest services/api/tests/test_section_geotech_actions.py -q
```

Tests were written before the module: collection failed because the new module did not exist. After implementation, one test's exact floating-point equality was replaced by a numerical tolerance; no engineering equation was changed to satisfy that assertion. Final targeted run:18 passed.
