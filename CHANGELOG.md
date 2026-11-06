## V3.88.0 (2026-11-06)

- 新增统一工程 Agent 控制面，登记 A1-A8 角色；A5/A6 提供实际单排桩任务执行，其他角色保留明确待开发状态。
- 新增事务性 SQLite 任务、执行租约、取消/暂停恢复、不可变候选、事件回放、重复任务幂等请求和人工工作版选择 CAS。
- 在冻结单排桩模型上进行有界参数候选生成与完整阶段求解，计算和工程规范结论独立记录；原工程参数来源随候选修改降级为待复核。
- 新增仅供解释的可选模型网关，默认关闭，域名固定白名单，模型输出不能调用工程修改或审批工具。
- 新增 A5/A6 单排桩结果页内嵌面板和手动/后台运行方式；补充 API 权限与回归测试。
- 保留 P1-P4 水文、圆桩、螺屿村原生数字导入和工作版验签模块，正式发行始终关闭。

## V3.87.28 - 原始证据输入、独立核验及双清单验签

- P1 水位记录按时区/井号/施工阶段单次配对与缺项诊断，适用范围内 Thiem 渗透系数候选。
- P2 双份同一 SLS 截面裂缝计算一致性与危险差异检测，新增受限欧拉柱理论筛查。
- P3 原生五阶段签名与单位严格合同、ZIP/CSV 哈希及有符号曲线全通道比较。
- P4 成果包双层 Manifest、冻结输入和计算结果及阶段校审的内部哈希核验，Ed25519 离线可信公钥验证。
- 专项前端控件和 API；源代码完整交付附自动化测试。工程正式签发仍禁止。

## V3.87.27 - 定向闭合规范轴压抗剪、P1 物理不变量与 P3 逐点曲线接入

- 圆形截面偏心受压抗剪按 GB/T 50010 6.3.15 转换等效宽、高，依据 6.3.12 和 6.3.1 计算抗剪承载力及截面限制。轴力与剪力取同一设计组合、阶段和截面。受拉和未确认荷载类型保持阻断。
- 非均质渗流增加水头平移、全域渗透率倍率和水头差倍率的独立物理不变量试验，仍不将数值解视为现场边界已审核。
- 螺屿村研究求解器 soilPoints -> pressureStations 转换，保留双侧压力的力积分；新增桩端自由/铰接/固结、冠梁及支撑刚度对照，拒绝以峰值反推原工程边界。
- 更新规范方法目录及研究 API；继续按 V3.87.26 的冻结计算与受控 P1-P4 校审生成工作版成果。
- 原生五阶段曲线、冠梁映射、对侧墙边界、正式圆桩裂缝和现场复杂地下水破坏判据未确认；formalConstructionIssue=false。

## V3.87.26 - P1-P4 五项证据、同源校审与阶段全通道对照

- 将 P1（水力）、P2（圆桩）、P3（螺屿村）、P4（工作版成果）纳入一个只读、按冻结结果验收的模块。
- P1：施工不利水位逐例重新求解；固定网格大小的远场边界敏感性；同域独立稀疏求解；现场证据缺失仍阻断。
- P2：圆截面弯曲边缘拉应力手算对照及超限负例；正式圆桩裂缝宽度和轴剪方法未完成，不宣称通过。
- P3：五阶段位移/弯矩/剪力、两侧压力、支撑反力与安装参考位移全通道逐点一致性核对；明确空支撑阶段身份；缺原工程原始数组不计算 RMSE。
- P4：新增分阶段校审的五项来源声明、哈希不可变记录、工作版计算书与 CAD/PDF/CSV 同源成果及二级验收索引；checker/admin 权限控制。
- 源码限于必要运行文件，测试、真实工程资料、研究与生成物不随运行包分发。

已完成的回归和验证数据随本次独立验证包交付。P1 至 P3 仍有规范与原工程证据缺口，正式施工签发状态保持关闭。

具体接口和方法见 `STANDARDIZATION_AND_ACCEPTANCE_V3_87_26.md`。

---

## V3.87.23 - 分项规范验算、独立研究对照与结果真实性

- 增加 JGJ 120-2012 第 4.2.5 条以最下层支点为圆心、经墙趾的分层圆弧土条法；合力、土条及假定可追溯。缺少土体强度依据、软弱下卧层或混合应力口径时保持阻断。
- 增加水平分层非均质含水层二维有限体积渗流调查，输出水头边界、控制出口梯度、迭代残差和边界水量守恒。该数值计算仅为专项筛查，不能代替复杂水力稳定正式验算。
- 分拆土自重、永久/可变地面超载及水压力，按显式 ULS/SLS 组合逐阶段重新求解；不利组合反力进入杆件传力，不再统一放大旧反力。
- 圆形桩承载力按相同组合、施工阶段、深度配对 N-M-V；支持有依据的 N-M 与 N=0 非抗震圆桩抗剪。另增加 N-M 使用阶段开裂弹性应力，正式裂缝宽度仍不提供未经验证的规范结论。
- 隔离增加螺屿村 3-3 五阶段条件化研究计算接口（含桩顶标高转换、坡台等效应力、有限邻载、土水分合算、临时撑及换撑）。原报告缺失逐点数值、支点刚度映射与准确方法，明确不属于同条件复现。
- 接口遵循现有独立工程模块，测试、原工程结果和图形复核附件另外交付，不进入精简运行包。正式发行闸门仍为关闭状态。

## 3.87.20 - One-strut section design workflow and lightweight delivery (2026-10-26)

- 独立工程纵切模块：勘察来源审查、规范范围内阶段梁土弹簧求解、超限及缺项、修改建议、校审、同源工作版成果。
- 校审/成果 API 加入 checker 角色控制，绑定实际账户身份；输入和封存结果以 SHA256 保护。
- 修正水位以上天然重度字段和单根支撑刚度语义；优化复核计算书与问题去重显示。
- 维护清晰的模块目录和独立的精简运行包，去除历史案例、测试和非运行数据。
- 已知范围缺口维持正式发行阻断。

## V3.87.19 (2026-10-26) - 规范计算工具与受控智能体

- 按 GB55003、GB55001、GB55008、JGJ120、GB/T50010、GB50497 和浙江地方标准整理计算方法目录与适用性状态。
- 增加规范单项公式服务、逐项候选值与出处、量纲强校验、有限来源确认、受控任务编排和历史冻结剖面只读审计。
- 新增规范计算可折叠面板与一致的计算单位注册，保留未实施模块的阻断。
- 新增可运行示例、一次性验证脚本和 119 项通过的后端定向及兼容回归；完整前端构建受当前环境依赖安装限制未完成。
- 原冻结剖面双侧弹塑性模型仍为验证用途，正式工程完整JGJ120支点法和全套稳定/配筋验算尚在后续阶段。

# V3.87.17 — 冻结剖面阶段分析与同源成果

- 冻结输入、单位、土体方法、桩端边界、阶段事件和显式设计效应系数，保存内容寻址快照与结果；禁止篡改、错配和缺阶段假包络。
- 新增单桩梁与双侧弹塑性土弹簧，承接已提交塑性位移及支撑安装参考位移；拆撑移除真实约束，刚性换撑使用精确位移约束。
- 新增解析手算子问题、实际冻结剖面的压力/水力/平衡核对、真实 OpenSees 独立求解和网格减半比较；原 A3-3 同条件比较缺项保持 incomplete。
- 修复土压力上下界互补时的刚体稳定性误判；以局部中心化梁公式和混合精度迭代改进解决细网格残差舍入，不放宽验收阈值。
- 圆形截面采用显式应变协调与离散纵筋纯弯检算，配筋需求和给定配筋分别保留；独立角度积分复核后提高积分阶次。
- 从同一封存快照/结果生成含原生公式的 DOCX、真实比例剖面 PDF/DXF、标准/设计内力包络、逐阶段 CSV 和配筋表，并附文件哈希清单。
- 计算页增加轻量“剖面结果验证”入口、二进制成果下载、身份核对和超时后的已保存结果恢复。

本版不解除原全基坑引擎的土体抗力方法门禁，不把合成算例通过标记为原项目复现或施工成果通过。详见 `docs/releases/V3_87_17_FROZEN_SECTION.md`。

# V3.87.16 — 计算可信性与不可变复算

- 默认计算改为 verify：冻结给定方案与已保存工况，结果建议单独存储；API 和任务保存使用原修订，冲突拒绝覆盖。
- 消除虚构承载力、超限定量项被 warning 掩盖、未知结果补零以及旧设计结果沿用。
- 新增不可变阶段荷载记录；标准作用与设计作用分别求解，墙、支撑、围檩及反力迭代共用来源。
- 修复支撑刚度组装/恢复不一致、重复投影与系数、整墙 EI 口径、空间土刚度因子、被动覆土起点及稳定指标乐观上调。
- 保留真实零开挖，显式区分安装前后、楼板投入、支撑拆除；不再由 final 名称自动激活楼板或恢复已拆支撑刚度。
- 耦合完成后统一重建结果摘要、阶段检查、最终包络和配筋需求；给定配筋验算与新建议分离。
- 标准/设计墙体求解及全局求解分别记录可用性；设计墙体失败不得将简化回退作为设计内力，包络缺失时不生成墙配筋需求。
- 明确未验证土体抗力共同作用、未实现路径历史及立柱竖向荷载的工程边界，保持正式发行阻断。
- 本轮专项后端回归 145 项通过，相关前端 19 项通过，前端生产构建通过；全量前端仍有与基线一致的 10 项失败，完整后端 HTTP 回归未宣称通过。

详见 `docs/releases/V3_87_16_CALCULATION_RELIABILITY.md`。

# V3.87.15

- Fixed the false deployment failure caused by bundled documentation text containing a loopback health-check URL.
- Moved the development API origin to `.env.development`; production now defaults to same-origin requests.
- Normalized `/api` configuration to prevent duplicate `/api/api/...` request paths.
- Added production bundle verification with exact file and context diagnostics.
- Added release-version consistency checks before dependency installation and build.
- Synchronized frontend package, lockfile, backend package and runtime version to 3.87.15.
- Added `scripts/diagnose-production-api.sh` for server-side diagnosis.

## V3.87.14 Hotfix 2 - ResultViewer 严格类型构建修复

- 将 ResultViewer 的后台任务回调从任意字符串收紧为实际使用的任务操作联合类型，兼容 ProjectWorkspace 的 BackendTaskOperation。
- 为正式化阻断列表增加统一 FormalizationIssue 类型，补齐 issueId、targetPanel 等可选字段。
- 修复 TypeScript strict/noImplicitAny 构建中的 TS2322 与 TS2339 共 4 项错误。
- 本热修复不改变结构计算算法、结果管线、数据库结构或工程数据。

## V3.87.14 - 云端加载流式化与分阶段工作区

- 项目首屏改用 shell 投影，移除数千条配筋检查的大 JSON 传输。
- 配筋方案按 summary/full/checks 分级读取，完整复核列表按需加载。
- core-status 按当前阶段构建证据，减少重复计算与返回体。
- 浏览器显示真实响应字节进度并检测数据流停滞。
- 普通 GET 读取不再覆盖页面，旧响应和卸载请求可取消。
- FastAPI/Nginx 启用 gzip 和代理缓冲，增加 HTTP 慢请求诊断。

## V3.87.13 - 变形云图成果路由与按需恢复

- 修复生产core模式未注册计算阶段成果分片路由导致云图请求404。
- 云图分片读取改为静默、限量、可回退，不再以全屏遮罩阻断工作区。
- 最多按需载入8个结果分片，并在分片缺失时回退到最新权威计算证据。
- 围护墙云图沿完整折线轴线逐段渲染，避免转角墙被首尾弦线替代。
- 无变形样本时明确显示几何占位提示，避免将零值占位误认为真实结果。

## V3.87.12 - 正式化闭合与在线文档

- 正式化阻断分类、自动证据恢复与人工责任边界。
- `/docs`任务导向说明中心、故障检索和部署维护说明。
- 反力迭代`not_required`状态显示修复。

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

## V3.87.22
- 独立 `normative` 稳定、水力、作用组合、实际支撑和圆桩工程检查工具；将局部结果及未覆盖项贯通到封存计算及可追溯成果。
- 加强软弱下卧层和水位改变后的适用性审查；杆件初筛及连接超限明确返回失败。
- 螺屿村3-3原报告五阶段事实与坐标/系数算术审计，不生成虚构曲线。
- 工程师界面补充土类、强度口径、水文、构件传力与不利工况输入；附精简运行源码和独立 QA 验证包。
