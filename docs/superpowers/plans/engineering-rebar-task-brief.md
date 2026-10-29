# Task 3 brief: engineering circular-section checks

Own `services/api/app/section/engineering_models.py`, `engineering_rebar.py` and corresponding test files. Root owns models.py/service/export/UI. No nested agents or Git commits. Read design spec and `/workspace/scratch/fd5d8a28c64e/work/rebar_engineering_research.md`.

## Input and API

- Define `EngineeringDesign` as a strict frozen Pydantic input in engineering_models.py, extra forbidden/nonfinite forbidden, no import from models.py (avoid circular dependency). Root adds `engineering_design: EngineeringDesign | None` to v2 SectionInput; absent stays unknown, not defaults.
- Public `check_engineering_rebar(model: dict, envelope: dict, design: dict | None, stages: list[dict] | None = None) -> dict`.
- Use your researched groups nm_cases/shear/detailing/anchorage/lap/service. Each rule records edition/clause/source and scope. Effects must trace the frozen stage/envelope rather than freely overriding M/V.
- `nm_cases` bind stage_id and explicit axial force/source; model assumes N constant along that stage's pile. Take M and both signs from that stage's actual `points`; don't combine max M from one stage with unbound N from another. Stage coverage missing is incomplete. Second-order and tensile/whole-compression cases beyond the validated range remain outside_method_scope. No unknown N→0.
- Shear demands derive from sealed envelope or stage cases with identity. Implement the substantiated circular clause b=1.76r,h0=1.6r and explicit effective hoop area interpretation; N=0/nonseismic applicability must be declared. Per research, don't apply a convenient generic rectangle rule instead.
- Service effects use explicitly declared SLS combination and scale relative to sealed characteristic stage effects, plus supplied N. Circular cracked-elastic steel stress may be computed with mechanics evidence; crack width remains unverified/outside_method_scope if no applicable circular formula/source has been substantiated. No fallback false crack pass.
- Detailing distinguishes bar-axis-to-surface, clear cover to longitudinal bar and clear cover to outer hoop. For current fixture59,18,8: longitudinal clear50mm, outer hoop clear42mm. Circular chord clear spacing for16Φ18 at radius291mm is ~95.5426mm.
- Anchorage and lap use verified formula/clause and explicit corrections, supplied actual length, cover/confinement and other needed conditions. No invented current project exposure/seismic/length.
- Output structured `checks`, `requiredInputs`, `limitations`, source traces, governing cases and quantities; keep `formalDesignReady=false`. A local pass cannot mark the whole engineering design complete when another mandatory check is unknown.

## Verification

First write meaningful red tests: N=0 agrees independent pure-bending, supported N-M equilibrium and out-of-range cases, correct signed/stage demand binding, circular shear hand example, 42mm outer-cover finding, chord-spacing geometry, missing SLS/rule stays unknown, anchorage corrections and provided-length fail. Compare N-M with independently assembled integral/root where feasible; no test mirrors the production calculation as its expected answer.

Send stable schema/return example to root early. Save primary sources plus commands/results in `/workspace/scratch/fd5d8a28c64e/work/engineering_rebar_implementation.md`. Do not alter old pure-bending function or v1 results. If an engineering input is insufficient, list that exact field; continue implementing the supported checks.
