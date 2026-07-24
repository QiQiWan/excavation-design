## V3.87.11 - 三维全屏与流程稳定性

- 四类实际 WebGL 模型统一增加原生/降级全屏按钮和退出全屏尺寸恢复。
- 统一 WebGL 创建、限帧、离屏暂停、上下文丢失恢复及 GPU 资源释放。
- 默认关闭开发环境 React StrictMode 双挂载，避免同一模型短时间创建两套 WebGL 上下文。
- GET 请求允许安全去重，上传、保存、计算等写请求不再共享在途响应。
- 临时网络错误增加有界指数退避、`Retry-After` 支持和写后读缓存失效。
- 工程工作台刷新增加世代号，迟到的旧响应不能覆盖最新工程快照。
- 增加系统 readiness 周期监测和顶部流程健康状态，提前暴露数据库、Worker、内存、磁盘与任务队列问题。
- 结构算法版本与结果管线版本保持 V3.87.10，避免无工程计算变更时误使既有结果失效。

## V3.87.10 - 围护墙钢筋笼平面路径一致性

- 新增围护墙单一权威平面路径解析与折线里程工具。
- 修复钢筋笼将多段墙轴线压缩为首尾弦线的问题。
- 历史槽段端点改为审计数据，按当前墙轴线自动重投影和连续化。
- 三维预览、配筋深化、IFC、CAD 和槽段追溯统一使用规范化路径。
- 增加墙体与槽段几何偏差、自动修复数量和未解析墙面诊断。

## 3.87.9 - Reinforcement calculation-contract auto-heal

- Hydrate externalized stage-result evidence before P3 reinforcement-deepening readiness is evaluated.
- Distinguish true structural failures from a transient mismatch between the persisted design snapshot and the latest calculation contract.
- Report the exact mismatched contract fields instead of the generic “calculation result missing or expired” message.
- Mark stale calculation evidence as an automatically recoverable blocker.
- Let P3 automatically recalculate, persist the authoritative result, rebuild reinforcement and re-evaluate the entry gate before stopping.
- Rebuild the final reinforcement gate from the saved project and latest calculation chunks so a pre-save diagnosis cannot remain on screen.
- Force reinforcement application actions to request recalculation when the current evidence is stale.
- Poll the authoritative reinforcement scheme after the background task and suppress the transient red blocker while recovery is still running.

## 3.87.7 - Transfer-path semantic recovery and auditable closure

- Preserve support-level semantics across topology regeneration instead of relying only on unstable support IDs.
- Rebuild software-managed bottom-up replacement sequences when support topology or support-level count changes.
- Keep frozen or specialist transfer paths under explicit engineering review while still evaluating structural screening candidates.
- Remove the zero-candidate early return caused by stale replacement/removal references.
- Group construction stages by semantic support level rather than raw member elevation.
- Cluster and cap pathological legacy support depths before optimization to prevent stage-count explosion.
- Distinguish formal closure, calculated screening pending transfer review, and bounded-search non-closure.
- Expose transfer-path recovery evidence, reconstructed stages and remaining manual decisions in the calculation workspace.

## 3.87.6 - Unified adaptive calculation closure

- Merge calculation, blocker recovery, optimization and recalculation into one primary action.
- Repair stale support IDs in regular design-control stages after topology regeneration.
- Keep replacement/removal transfer paths under explicit engineering review.
- Expand optimization from section-only portfolios to support spacing and additional support-level candidates.
- Add monotonic backend/frontend progress and duplicate-submission protection.
- Return explicit `closed`, `cannot_close` or `needs_manual_input` outcomes.

## 3.87.5 - Persistent relative interventions and bounded multi-objective optimization

- Replaces stale absolute wall/beam/toe proposals with bounded increments from the current persisted design value.
- Records before/after values, design and result hashes, governing-value changes and reinforcement regeneration for each manual closure action.
- Regenerates reinforcement after an intervention and recalculates when reinforcement-driven section changes require it.
- Adds `calculation_optimize_search`, evaluating isolated balanced, economic-zoned, stiffness-first and section-first portfolios.
- Ranks candidates by calculation closure, structural closure, failures, reserve deficit, displacement and material-growth proxy.
- Applies only the best bounded feasible retaining-system candidate, then performs a canonical recalculation and reinforcement update.
- Adds one-click optimization controls, candidate ranking, selected strategy and material-proxy evidence to the calculation workspace.
- Preserves manual control over loads, strata, groundwater, survey coordinates and locked construction stages.

## 3.87.4 - Resilient workspace, asynchronous geology import and calculation recovery

- Keeps the active project and six-stage workspace route after saving the design basis; transient save responses can no longer eject the user to the project list.
- Adds `/projects/:id` route-backed project restoration for refresh, save and browser history.
- Moves CSV/XLSX/XLSM borehole parsing into the isolated task worker with streamed upload, SHA-256 verification, progress, cancellation and a 50 MB configurable limit.
- Invalidates stale geological models and calculation evidence when borehole or stratum source data changes.
- Adds bounded pre-calculation recovery for closed-outline wall mapping, missing baseline support generation, geological design-domain rebuilding and support-topology candidate regeneration.
- Adds a calculation blocker resolution center and an `automatic diagnose, repair and recalculate` task that forces the verification-strengthening-recalculation loop.
- Preserves unresolved coordinate or specialist-system decisions as explicit manual actions; the software does not silently translate survey coordinates.
- Retains the latest calculation recovery state in aggressively compacted workspaces.
- Preserves the exact scroll position and re-reads the canonical project if a compacted save response is incomplete.
- Bounds Excel/CSV rows and columns, closes read-only workbooks deterministically, cleans stale staging files and gives borehole imports a dedicated worker timeout.
- Adds a second automatic intervention phase for safe monotonic actions such as unlocked wall-toe deepening, local member strengthening and support-section optimization, followed by another full verification cycle.

## 3.87.3 - Single primary design flow and on-demand assurance center

- Removes the always-visible second design workflow from the project workspace.
- Keeps one six-stage primary navigation: basis, input, scheme, calculation, reinforcement and deliverables.
- Repositions the nine detailed design-core stages as internal evidence domains rather than navigation stages.
- Adds an on-demand right-side quality and traceability center, grouped one-to-one with the six primary stages.
- Defers the design-core bundle request until the assurance center is opened.
- Adds drawer close, Escape handling, background-scroll locking and stage-return actions.
- Updates online documentation and API metadata to identify the evidence dashboard as quality assurance.

## 3.87.2 - Design-core integrity, preview contract and workspace hardening

- Consolidates the design-core dashboard CSS into the single active application stylesheet and deprecates the unused secondary stylesheet.
- Adds a shared candidate-geometry sanitizer across scheme comparison, result, core-workspace and retaining-system views.
- Upgrades compact candidate previews to `candidate-plan-v3` with finite-coordinate validation, truncation disclosure and transfer-system integrity metadata.
- Invalidates legacy preview caches and retains supports, transfer beams, transfer zones, obstacles and columns in bounded preview rows.
- Adds one-hydration `/design-core/bundle` loading and stale-response suppression in the frontend.
- Makes design-core read routes side-effect free so dashboard refreshes cannot advance project revisions.
- Requires an eligible parameter source and non-empty source reference before a confirmed parameter may control formal design.
- Deduplicates exact physical candidates before bounded ranking; fingerprints include all transfer-beam polyline segments and temporary-column positions.
- Preserves bounded candidate full-calculation, numerical-health, completeness and critical-ledger evidence in compact workspaces.
- Extends runtime-diagnostic summarization for byte-based memory fields, prefixed duplicate reasons and task error messages.

## 3.87.1 - UI, transfer-topology preview and recovery hotfix

- Loads the V3.87 design-core stylesheet from the application entry point.
- Removes the duplicate `statusText` declaration that forced the frontend into root recovery mode.
- Introduces `candidate-plan-v2`; compact project workspaces and preview caches now retain transfer beams, transfer zones and obstacles.
- Automatically invalidates and rebuilds legacy `candidate-plan-v1` preview rows.
- Renders transfer rings, hub frames and chord frames in all candidate, core-workspace and retaining-system previews.
- Adds panel-level error boundaries so a visualization failure does not terminate the complete project workspace.
- Adds backend regression tests and a source-level TypeScript syntax contract for the hotfix.


## 3.87.0 - Design-core closure

- V3.82: construction plans, field snapshots and deviation events were removed from the primary designer workflow; external information now enters as reference records and design-review requests.
- V3.83: parameter provenance, formal-design eligibility, clause-level rule evidence and parameter impact governance.
- V3.84: five-level scheme search assurance, family diversity checks and full-calculation selection gates.
- V3.85: per-member result envelopes, explicit units, reinforcement feedback closure and formal proxy-result isolation.
- V3.86: design-institute drawing/report completeness checks and unified design snapshot hashes across calculation, reinforcement, drawings, report and IFC.
- V3.87: nine-stage design dashboard, conservative production-release qualification and updated online documentation.
