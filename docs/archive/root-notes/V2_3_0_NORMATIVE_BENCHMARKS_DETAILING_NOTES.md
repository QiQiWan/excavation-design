# PitGuard V2.3.0 - Normative algorithm benchmarks, object localization and drawing/detailing hardening

## Scope

V2.3.0 intentionally keeps the calculation backend on rule-based / code-based algorithms. No finite-element solver is introduced in this iteration.

## Main upgrades

1. Object-level issue localization: issue-center items now carry workflow-step, target-panel, object-type, object-id / object-code, center-point and drawing-sheet locators where available. The web workflow includes a locator banner and issue-row click behavior.
2. Enterprise CAD drawing templates: the construction CAD package adds title blocks, scale/project/stage fields, dimension lines, a drawing register, material schedule, rebar schedule, rebar bending schedule and an enterprise-template manifest.
3. Public-paper-derived benchmark library: five benchmark cases are added for normative-algorithm regression. The source project dimensions are preserved in metadata; plan size and depth may be normalized or capped for repeatable automated runs.
4. Rebar detailing schedule: the backend generates bar marks, shape codes, approximate length and weight, anchorage/lap/hook review flags and a dedicated DXF/CSV bending schedule.
5. Benchmark package export: a ZIP package contains project JSON, issue report, calculation trace, CAD/SVG drawings, DOCX report and construction-visual IFC for each benchmark case.

## Public-paper-derived benchmark cases

- HZ-30M-SOFT-CLAY-9310: Hangzhou soft-clay basement excavation, 30.2 m public depth, about 9310 m2, diaphragm walls and six concrete strut levels.
- SH-ULTRA-LARGE-ZONED-70500: Shanghai ultra-large zoned excavation, about 70,500 m2 and 340 m x 200 m public plan size.
- SH-31P5-PASSAGEWAY-DEWATERING: Shanghai 31.5 m passageway excavation with excavation-dewatering coupling and corner effects.
- SH-56M-CIRCULAR-DOUBLE-WALL: Shanghai 56 m circular excavation with diaphragm wall and cut-off double-wall system; normalized as an octagonal normative-regression model.
- URBAN-TOPDOWN-32M-WALL-5SUPPORT: top-down adjacent-building excavation case with internal supports and six-cut/five-support construction description.

## Engineering boundary

The benchmark suite is for workflow regression and normative-algorithm coverage. It is not a substitute for original design drawings, site-specific geotechnical reports, or registered-engineer review.
