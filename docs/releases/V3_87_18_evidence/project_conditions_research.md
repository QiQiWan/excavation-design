# Original project reference contract research — A3-3

Research only, 2026-10-25. No product files changed. Baseline worktree: foundation_section_validation, original-project-validation, c8793e8.

## Verified identity and vendor evidence

Directly re-extracted original PDF physical p21 during this research: “计算软件采用同济启明星软件FRWS9.0。” Therefore reference software is Tongji Qimingxing FRWS9.0; it is not Lizheng/理正. PDF SHA256 and page evidence are preserved in examples/frozen-section/A3-3-reference-audit.json. Report cover date is 2020-05-21; exact executable build, run timestamp, and executable hash are unconfirmed. Do not infer those from the cover or a current vendor release.

Primary public sources accessed 2026-10-25:

1. https://www.qimstar.com/h-msgBoard.html (web ref turn57view0): vendor lists FRWS9.0 update dated 2021-04-29 and FRWS8.2 update dated 2020-05-25. This verifies products/releases, not this project's build.
2. https://www.qimstar.com/h-nd-19.html (turn59view1): vendor FRWS9.0 scope includes retaining structure analysis/design and configurable analysis options.
3. https://www.qimstar.com/h-nd-26.html (turn59view2): FRWS10 upgrade, service from 2022-05-31, lists adjusted result display with numeric/graphical pressure and subgrade coefficient, and options for external soil springs under negative displacement. These are later-version statements, not evidence that 9.0's precise algorithms or exports match current versions.
4. https://www.qimstar.com/h-col-124.html (turn59view0): vendor lists FRWS8.2 manual and video concerning multi-section FRWS data for a BSC3D joint-analysis database. Public page extraction does not supply a validated 9.0 native extension or numerical export schema.
5. https://www.qimstar.com/FRWS.html (turn57view1): currently FRWS11; do not treat its modern feature list as the historical project's options.

**Native extension, encoding, file signature, FRWS9.0 CSV/XLS result export capabilities, and exact menu sequence remain unconfirmed.** Do not invent a .frws/.frws9 extension or claim CSV export exists. Accept original files preserving all companion files, original extension, bytes/hash, and observed application version. If a compatible exporter is absent, accept a documented transcription of the original numeric result table with provenance, clearly distinguished from native numeric export. PDF-curve digitization remains derived_interpretation and cannot become original nodal results. No native model was identified in the inspected uploaded material; this is a bounded finding, not an exhaustive claim about all user storage.

## Critical original conditions

Source is the existing field-level PDF audit, physically pp32–37 (original section 4.5 3-3); p37 begins another section and must remain isolated.

|Area|Original fact|Interpretation / unconfirmed|
|---|---|---|
|Geometry|D=.7 m, spacing=.9 m, top=-1.3 m, length=6.6 m, excavation=4.9 m|Model toe depth 7.9 m and embedment 3.0 m derived. Ground-to-pile-top datum transform required.|
|Rigidity|C30 material label|Numerical E, reduction and effective EI unconfirmed; gross I=pi D^4/64 is derived geometry, not original EI.|
|Soil|m=4,2.05,.73,18,20 MPa/m²|Converted m=4000,2050,730,18000,20000 kN/m⁴ derived; formula, depth origin, layer continuity, effective width, caps, unloading and external side unknown.|
|Water|Outside printed .5 m; inside stage depths 2.55,2.55,5.4,5.4,5.4 m; fill separated, other layers combined|Outside stage constancy, water weight and combined-pressure composition unconfirmed.|
|Loads|Surface 0 kPa; finite neighbor 30 kPa, edge 4.3 m, slope-top edge 1.3 m, width 12 m, length 65 m|Finite load projection and slope pressure mapping unconfirmed; no full-plane 30 kPa substitute.|
|Slope|height1.3, width1.3, bench1.7 m|Cannot flatten for same-condition reproduction.|
|Supports|S1 depth1.625,32.3 MN/m²; crown equivalent22.7 MN/m²; T1 depth4.6, axial fixed, rotational0|Crown assembly/location unconfirmed. T1 constrains translation, not rotation. S1/T1 installation-reference displacement inferred from unchanged responses and zero initial reaction.|
|Boundary|No verified toe boundary|Wale “rigid connection” does not establish pile toe fixity.|
|Force identity|Reaction explicitly kN/m; M axis kN·m and V kN|M/V per-pile versus per-width unknown. Neither reaction nor reinforcement Nd may be relabeled stage support N.|
|Rebar|Mk206.7, Md258.4, required As2587.7 mm²,16phi18 provided, axis cover59 mm|Provided As4071.5 mm² derived; shear label Md(kNm)=150.7 conflicts, Vd interpretation unconfirmed; final stirrup spacing unavailable.|

Original five events must remain distinct: excavate2.05; installS1; excavate4.9; installT1 while S1 remains; removeS1. Stages 3/4 tie for printed M/V maxima, stages3/4/5 tie for printed u maximum. Retain all tied controlling stages and use rounding-aware comparison. Original plotted envelope minimum displacement0 versus stage minima≥.6 has unresolved initial-zero inclusion; do not silently normalize it away.

## Proposed ReferenceContract v1

This is a separate immutable reference/evidence object; it must not force unknown values through SectionInput defaults.

- schema_version, reference_id, section_id, revision; source_registry with source_id, kind, sha256, filename, physical-page count; software product/version observed, executable/build/run identity if available.
- fields: ordered records of path, value, unit, status in {original_fact, derived_interpretation, unconfirmed}, original_status, semantic_identity, source_refs, derivation, unresolved_reason. Map audit missing to unconfirmed with null and preserve original_status=missing. Status original_fact requires traceable source; derived requires formula/input field references; unconfirmed must remain null or explicitly preserve raw text separately from unresolved semantic value.
- stages: stable IDs, original ordinal/event/depth, interpreted cumulative excavation, support install/remove identity, water levels; interpretation status per field.
- force_basis and datum: reference depth origin/direction, displacement direction, M/V sign, per-pile/per-width/member basis, spacing/tributary width and support projection if applicable. Each has evidence status.
- required_conditions: machine-identifiable gates for E/EI, soil law/width/origin, boundary, load/slope mapping, mixed water rules, crown assembly, event history/reference displacement, result force basis, software/build identity, full results. Each lists field paths and block reason.
- report_extrema and reinforcement_reference retain original printed values and labels, precision, and page provenance, never fabricate curves.
- imported_results: immutable canonical result-bundle hash and source hash. Imported metadata cannot automatically confirm original field meanings or close gates.
- assessment: separate readiness (blocked/partial/ready), comparison scope (report_extrema/full_curves), result (not_comparable/partial/within_tolerance/outside_tolerance); no single boolean “verified”.

Existing SectionInput supports only a specific synthetic two-sided EPP model, uniform effective pressure law, flat uniform surcharge, and no crown component. It currently cannot faithfully express the original mixed layer water rules and finite neighbor/slope mapping. Contract freeze may be completed now; same-condition solver generation must expose these unsupported differences. Numerical agreement alone cannot close evidence gates.

## Full-result import protocol

Accept a neutral PitGuard JSON interchange (explicitly not claimed to be a FRWS-native format), optionally CSV plus mandatory JSON manifest. Validate strict finite numbers, size/row caps, hashes, duplicate stage/component/channel keys, stage identity, source status, and mandatory units/basis before activation. Unconfirmed means missing, never zero. Preserve raw files and canonical parsed bundle separately.

Manifest: schema, source software/product/version, source document/file hash, extraction mode {native_numeric_export, numeric_table_transcription, digitized_curve, synthetic_fixture}, section identity, coordinate/sign conventions, component and stage mappings, unit/basis per channel, datum transform with evidence, declared coverage and precision. A synthetic fixture always stays synthetic even if named A3-3. Native source declarations alone do not authenticate executable identity.

Per curve: stage_id, component_id, quantity (p_soil_out,p_water_out,p_soil_in,p_water_in,p_net,u,M,V,pile_N), unit, basis, depth origin/direction, sign convention, samples [{z,value,side}], provenance. side={above,below,continuous} distinguishes jump limits. Pressure definitions must state whether this is imposed, mobilized, net external or net including soil reaction; unsupported decompositions remain unavailable. Add supports table with stage/support IDs, activation status, reference_displacement, displacement, line reaction and actual member axial force as separate channels; conversion to member N requires tributary spacing, angle, grouping and source-backed convention.

Comparison sequence:
1. Assess condition gates and explicit stage/geometry/basis maps; incompatible or unknown identities block affected channels.
2. Normalize units only with declared transforms. No auto M/V multiplication by .9, no datum/sign fitting to minimize errors.
3. Require complete five-stage coverage per claimed quantity; report coverage matrix including missing pressure and N. Never interpret absent stages as unchanged.
4. Compare on union depth breakpoints within common domain, split at layer boundaries, load endpoints and support discontinuities. Piecewise linear resampling is an explicit comparison operation; do not smooth shear jumps or extrapolate missing coverage.
5. Metrics per channel/stage: signed bias, max abs error with depth/side, RMSE or depth-weighted L2, positive/negative extrema and locations, coverage fraction; relative error uses explicit nonzero scale and absolute tolerance near zero.
6. Compare envelope separately, preserving signed extrema and all ties. Compare controlling stages under a declared rounding tolerance rather than selecting first maximum.
7. Compare support installation reference displacement and reactions separately from member N, and reported Mk/Vk→Md/Vd and required/provided rebar separately from structural response.
8. Preserve tolerances, rounding intervals, normalization transformations and missing blockers in result provenance. “Within tolerance” is scoped to compared channels; a partial match cannot become same-condition reproduction.

## Initial implementable deliverables

1. Strict ReferenceContract loader/builder from unchanged audit, canonical hash, explicit source/status validation and machine-readable blockers.
2. JSON import schema/validator and a blank reference template with no invented nodal numbers. An internally generated fixture may test imports but must be marked synthetic and excluded from reference certification.
3. Per-stage coverage and report-extrema/rebar comparison for existing audited values, guarded by identity readiness; full-curve comparator executable against compatible imported bundles.
4. Snapshot/export inclusion of original facts/interpretations/unconfirmed fields, condition gates, immutable import hash, comparison metrics and missing coverage.
5. Essential tests: unknown E stays null; false all-confirmed declaration rejected; synthetic cannot certify original; unknown force basis blocked; missing stage not filled; mismatched hashes/NaN/duplicates rejected; distinct one-sided V samples maintained; ties preserved; shear label conflict retained.

No product edits or reference confirmation were performed in this research.

## Datum verification addendum (fresh image inspection)

New extraction and image rendering of physical pp32,33 were performed during this research; images /tmp/reference-page-32.png and /tmp/reference-page-33.png are intermediate evidence.

- p32 section drawing directly prints dimension chain: original model ground to pile top 1.3 m; pile top to S1 0.325 m; S1 to excavation base 3.275 m; pile top to base 3.6 m; base to toe 3 m. Original text explicitly says wall top elevation -1.3 m, pile length6.6 m and design excavation4.9 m.
- p33 original response vertical axes run0–8, while plotted wall response begins around1.3 and ends7.9. This independently supports model-ground depth, rather than pile-top depth, for original curve coordinates.
- p33 stage table/event drawings print excavation2.05/4.9 and supports1.625/4.6. They align with p32 dimension chain (1.3+.325=1.625;1.3+3.6=4.9). Ground-origin interpretation is strongly supported but retain coordinate mapping as derived_interpretation, with source references to both drawing and table; no silent rewrite of original printed values.

|Quantity|Original model-ground depth m|Derived pile-top depth m|Evidence status|
|---|---:|---:|---|
|Pile top|1.3|0|Top elevation printed; depth/sign conversion derived|
|Pile toe|7.9|6.6|Original length6.6; ground toe depth derived|
|Excavation stage1/2|2.05|.75|2.05 original, minus1.3 explicit derived|
|Excavation stage3/4/5|4.9|3.6|Both geometric4.9/3.6 supported p32; stage inheritance derived|
|S1|1.625|.325|Both numbers independently printed in table/dimension chain|
|T1|4.6|3.3|4.6 original, minus1.3 derived|
|Outside water|.5|-.8|.5 original; negative pile-local depth derived, means above top|
|Inside water stage1/2|2.55|1.25|Original stage values, explicit derived transform|
|Inside water stage3/4/5|5.4|4.1|Original stage values, explicit derived transform|
|Original layer interfaces|0,1.1,1.8,7,7.6,57.6|-1.3,-.2,.5,5.7,6.3,56.3|Cumulative thickness and transform derived|

Current SectionStage water depth ge=0 cannot represent original outside water at pile-local -.8. Current SoilLayer top_m ge=0 plus continuous top0 also cannot retain original overburden above pile top. Simply cutting the top1.3 m off layers loses overburden pressure and slope/bench geometry. This is a modeling capability gap, not a datum-only fix. Freeze geometry in ground coordinates and derive explicit solver projection once implementation can support it.

Architectural datum is distinct: report pp7–8 connects ±0.000 to national elevation5.000 m and leveled ground to relative-1.000 m. Candidate project-relative elevation=-1.0-ground_depth remains a derived mapping, not a reason to label original wall top-1.3 as architectural relative elevation. Native project's internal coordinate export still needs observed axis metadata.

## Normative editions, hashing and confirmation

Add separate historical_code_edition (source fact DB33/T1096-2014, p33), historical_design_expression (p36 factor1.25×1), and current_review_basis (unconfirmed until explicitly supplied with edition and scope). Current material/design values must never overwrite historical inputs. A C30 label does not confirm E=30GPa or any effective EI.

Reference freeze should include canonical condition hash plus independent source-file hashes and reference-result-bundle hash. Hash conditions with status, units, meaning and transformations, not values alone. A confirmation is a sourced record supplied by a user/engineer: who supplied the evidence, time, source hash/page or native-option locator, fields clarified, old/new status, rationale. It is not an automatic status promotion, a generated designer signature, or a design approval. Preserve absence of confirmation as absence. If supporting files are unavailable, an asserted value may support an explicit reconstruction assumption, not original_fact. The module should not synthesize sign-off identities.
