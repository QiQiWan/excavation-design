# V3.11.0 - Standards traceability, rebar package and online engineering documentation

- Replaced the misleading rebar JSON download with a structured ZIP containing XLSX, CSV, JSON, checks and usage guidance.
- Added project-aware workflow-to-standard traceability APIs and highlighted mandatory standards in every design step.
- Added formulas, assumptions, verification points and standard references to online documentation and DOCX reports.
- Removed hard-coded 100% completion values from delivery manifests and unified assurance check aggregation.
- Added auditable K·u=F residual, symmetry, condition-number and regularization quality gates to the global coupled solver.
- Optimized large reinforcement ZIP generation and added workbook truncation-to-complete-CSV/JSON traceability.

# V3.10.0 - Geometry write-back, parallel schemes, site routing and module assurance

- Added persisted geometry write-back for rebar rerouting, local reinforcement, embedded-item shifting and designed openings.
- Re-runs fabrication, spacing, collision and detailing checks after geometry changes.
- Added A* crane/site route planning with boundaries, roads, exclusion zones and crane footprint clearance.
- Added independent A/B/C full-calculation tasks with stable input hashes, file cache and per-scheme progress.
- Added canonical engineering unit registry shared by backend and generated frontend code.
- Retained the full acceptance matrix and added a twelve-module completion review with evidence, gaps and next actions.
- Rebuilt scheme previews to auto-fit the true geometry bounds and support pan, wheel zoom and reset.
- Added six role-focused workspace modes without hiding closed-loop review or secondary modules.
- Lazy-loaded 3D viewers and added runtime LOD; ProjectWorkspace production chunk reduced to about 161 kB.
- External finite-element execution remains out of scope for this release.

## V3.9.0

- 构造协调增加四类参数化几何候选、净距预测、验证条件和应用后重新筛查。
- 高风险节点增加设计变体和 CalculiX/Abaqus 非线性实体接触输入文件。
- 钢筋笼吊装增加项目吊机库、实际站位、地基、风载、禁入区及运输路径约束。
- 建立统一 SI 工程单位注册表，关键界面和导出台账明确显示单位。
- 将 A/B/C 整案候选和完整计算入口恢复到主工作流。
- 工作台默认采用“当前成果—关键问题—下一步动作”，专业明细按需展开。
- 后端默认端口保持 8002。

## V3.8.0

- 深化设计：节点钢构件、焊缝、锚筋、钢筋笼吊装、套筒、预埋件碰撞和施工顺序闭环。
- 启动诊断：按 pyproject 检查当前 Python 环境，退出时打印缺失依赖安装命令。
- 后端默认端口继续使用 8002。

# V3.7.0 - Professional construction drawing production pipeline

- Replaced hand-written R12 output with validated R2018 DXF, mm model space and paper-space layouts.
- Added native dimensions, Unicode text style, title-block blocks and locked viewports.
- Added real-width wall/wale/support graphics and wall-connection rigid arms.
- Added stock/transport-aware rebar fabrication segmentation, coupler/lap schedules and geometric spacing checks.
- Added drawing completeness, DXF validation and construction issue gates with SHA-256 manifests.
- Added one-sheet-per-page vector PDF publication with Chinese CID text.

# Changelog

## V3.6.0

- 水平支撑轴线与围护墙脱开，并保留墙面连接点、刚臂和净距质量检查。
- 增加斜撑+短对撑混合、双向网格和传统直对撑三类完整拓扑候选。
- A/B/C 候选执行完整计算后二次决策排名，输出推荐理由和不可绕过的工程闸门。
- 换撑刚度采用状态化表达，未激活显示“—”，缺失和无效参数形成阻断。
- 结果页改为三维整案比选，局部优化、矩阵台账和评分分解默认折叠。
- 项目设置增加净距、跨度、斜撑和楼板换撑刚度参数。

## V3.5.0

- 修复 L/T 形基坑凹角回墙缺少直接支撑导致的计算失败。
- 增加旧项目支撑拓扑增量修复和施工工况自动同步。
- 增加计算根因诊断、重复检查归并和修复前后对比。
- 增加 Drawing Intelligence Engine、D-09 大样及图纸质量评分。
- 增加计算诊断与智能出图 CAD 台账，优化宽屏工作台。

## V3.4.0

- 将出图组合、触发、拆图、比例和发行条件抽离为独立规则引擎。
- 核心预设迁移到 `packages/drawing-rules/presets/*.json`，支持企业目录覆盖。
- 增加安全条件 DSL、渲染器白名单、规则校验和决策轨迹。
- 支持按支撑层、按墙幅动态展开及多墙合图。
- 增加保留项目自定义规则的多目标候选优化。
- CAD/PDF 正式发行记录规则集和图纸计划哈希。
- 前端增加规则配置、预览、优化与候选采用工作台。

## V3.3.0

- 增加长期效应与裂缝分区筛查、设计寿命及环境参数。
- 支撑拓扑增加关节点、桥接杆件、墙面覆盖率、凹角和正交传力候选。
- 碰撞检查增加跨层实体包络竖向净距。
- 节点局部模型升级为三自由度凝聚刚度、偏心转动和特征值稳定性筛查。
- 监测 CSV 支持中英文字段与单位归一，修复同一标高误选早期施工阶段位移的问题。
- 监测反演系数进入墙体与全局联立计算，应用后自动要求复算。
- 四级审签增加岗位分离、快照失效、失效重提和退回意见要求。
- 正式施工版发行增加当前快照施工修订记录闸门，修订号支持 AA 及后续编号。
- 前端增加精简/专业模式、工程深化分组、快捷命令、批量监测导入和无障碍交互。

## V3.2.0

- 修复支撑方案重建后施工阶段引用旧 ID 导致的无支撑计算。
- 深大长方形基坑增加正交次对撑及主次网格立柱节点。
- 修复配筋轴力重复放大、高弯矩配筋平台化和承压板取整假失败。
- 增加原因分组、截面升级、自动复算和施工图出图闸门。
- 配筋工作台改为四步引导，默认仅显示问题项。
- CAD 增加 D-08 主次支撑网格节点及设计诊断 JSON/CSV。

## V3.1.0 - 分区配筋、三维审查与 CAD 成套出图

- 地下连续墙按内力包络和构造位置划分墙顶、支撑节点、开挖转换、墙趾和一般区，分别生成坑内/坑外侧配筋。
- 钢筋混凝土支撑增加端部加密区、跨中区、错开搭接和拥挤诊断；围檩及节点增加局部附加筋建议。
- 配筋方案与三维钢筋、墙体分区立面、节点大样和钢筋表建立统一图号追溯。
- 三维查看器增加状态/类型/宿主着色、三向剖切、透明度、显示倍率、构造标注和宿主隔离。
- CAD 图纸包新增总平面、逐层支撑平面、全部墙幅及逐幅分区立面、D-01～D-07 节点大样。
- 支持完整、总图、配筋和大样四类独立导出，图纸与表格按专业目录组织。
- 标题栏根据图元边界自动放置，并保留 V2.x 下游脚本所需的兼容 CSV。

## V3.0.0 - 几何、计算拓扑、性能与交付收束

- 墙体云图统一采用实际高程，支持单阶段、全阶段控制包络、有符号值和绝对值显示。
- 墙体求解按墙段过滤实际连接支撑，支撑弹簧按构件 EA/L、方向投影和构件角色逐根计算。
- 支撑候选优化使用不可变配置，消除并行任务修改模块全局参数的风险。
- 项目列表改为轻量摘要，项目详情和计算历史默认限流；删除计算结果中的重复全局矩阵和墙体采样副本。
- 新增闭合轮廓、墙段覆盖和几何哈希检查。
- 后台任务增加 SQLite 持久化、项目级串行锁、服务重启中断标识和成果 SHA-256。
- 前端工作台、文档页和 Three.js 资源按需加载，依赖版本锁定。
- 根目录历史说明迁入 `docs/archive/root-notes/`，现行说明集中于 `docs/`。

## V2.9.0 方案快照与交付驾驶舱

- 新增项目交付驾驶舱 `/api/projects/{project_id}/dashboard`，集中展示交付闸门、规范状态、墙长闭环和下一步动作。
- 新增方案快照台账 `/api/projects/{project_id}/design-scheme-ledger`，记录采纳历史、复算状态、长度变化和当前方案 KPI。
- 完整交付包增加 `designSchemeLedger`，交付包版本升级为 `v2_9_0`。
- 高级导出新增“方案快照与交付台账”，用于归档优化过程和复核边界。
- 问题中心模块清单新增 M16，标记方案快照、复算状态和交付闸门台账已闭环。



## V2.8.0 - 围护墙设计长度冗余优化修复闭环
- 将围护墙设计长度冗余分析接入问题清单中心。
- 新增严重冗余、偏保守、接近下限和采纳后未复算 issue。
- 采纳候选后保存 wallLengthOptimizationHistory，并标记 recomputeRequired。
- 重新运行计算后自动闭合复算状态。
- 新增冗余优化报告导出接口、任务和完整交付包记录。

## V2.8.0 - Wall design-length redundancy optimization
- Added project-unified diaphragm wall thickness policy: wall thickness is treated as a project-level value, not a per-wall optimization variable.
- Added retaining wall design-length redundancy optimizer for design-face zoning, panel length and local strengthening length.
- Added API endpoints: `/api/projects/{project_id}/wall-optimization/length-redundancy` and `/apply-length-candidate`.
- Added ResultViewer panel for target redundancy band, face-level Rmin/Rmax, design length recommendations and candidate adoption.

## V2.6.4

- Fixed retaining-wall 3D cloud consistency for grouped/closed wall segments.
- Closed excavation boundary in support quality plan.
- Expanded concrete support detailing groups and balanced rebar sampling so support reinforcement remains visible.
- Added explicit 3D labels for lap, anchorage, stirrup, distribution and tie bars in the rebar viewer.
- Added 3D hover badge and right-side borehole/object property card.

## V2.6.2 Operator UI, 3D Wall Cloud and Rebar Detailing Fixes

- Fixed white/invisible audit-locator labels in the engineering 3D viewer.
- Added hover highlighting and borehole click detail panel with stratum colors, names and elevation ranges.
- Replaced JSON-like replacement-path and check-summary displays with operator-facing text/tags.
- Added frontend `/docs` operation documentation page.
- Improved formula rendering by replacing long internal parameter names with engineering symbols.
- Reworked module ledger spacing and wrapping so `pass` and evidence text do not run together or overflow.
- Added fallback display of wall moment/capacity and support axial force from latest calculation results when component-level design fields are not persisted.
- Added support reinforcement groups for cast-in-place RC struts and improved staggered lap/closed stirrup visualization.
- Added 3D wall deformation/moment/shear cloud viewer and removed development-version wording from result panels.


## V2.5.0 Completion Closure

- 完成 issue 多视图定位闭环：workflow / plan / 3D / rebar / result / CAD。
- 完成企业 CAD 模板校验：图框、图层、图号、字体、线型、签审元数据。
- 完成钢筋施工详图深化代理：施工缝、钢筋笼分节、吊装、搭接区、弯折半径、保护层检查和签审清单。
- CAD 图纸包升级到 12 张 DXF + 多张 CSV/JSON 表格。
- 保持规范算法路线，未接入有限元。


## V2.4.0 - 对象高亮定位、企业 CAD 模板、逐根钢筋几何

- 问题清单 locator 贯通二维支撑平面、工程三维视图、钢筋 IFC 可视化、内力包络图和 CAD 图纸定位预览。
- 新增 CAD 企业模板接口，支持图号前缀、签审栏、阶段、图层标准和高亮图层配置。
- CAD 图纸包新增 S-08 逐根钢筋几何索引图和 individual_bar_geometry.csv。
- 钢筋详图接口新增 individualBars，包含中心线点、分段、锚固/搭接/弯钩代理长度、下料长度和重量。
- 新增 V2.4.0 定向测试，覆盖 CAD 模板、逐根钢筋几何和 CAD 包内容。

# V2.2.0 - Full maturity delivery closure

- Added calculation traceability endpoint `/api/projects/{project_id}/calculation/trace`, mapping governing values to stage, object, formula, code reference and result path.
- Upgraded Issue Center to include locator metadata and a 100% software-module acceptance ledger, while keeping project-specific official issue readiness dynamic.
- Upgraded CAD package to a formal drawing-set interface with six DXF sheets, drawing register, material schedule, rebar schedule, delivery consistency matrix and JSON manifest.
- Upgraded full-delivery background task to produce one downloadable ZIP bundle containing IFC, CAD, SVG, DOCX, JSON, trace and issue-center reports.
- Added frontend Calculation Trace panel and V2.2.0 module ledger visualization.



## V2.0.15 - Rebar-level IFC visualization and CAD drawing export

- Added `/export/ifc-rebar-visualization` for browser-safe sampled rebar geometry generated from IFC reinforcement groups.
- Added a Step 8 Rebar IFC viewer with host/bar filters, transparent host overlay, clipping and property picking.
- Added `/export/drawings-cad` ZIP export with R12 DXF support plan, wall rebar cage, support-wale node detail and rebar schedule CSV.
- Added `/export/drawings-svg` ZIP export for construction detail SVG sheets.
- Added CAD export cards and improved download filename fallback for ZIP drawing packages.

## V2.0.14 - Candidate diversity repair, construction-visual IFC and reinforcement diagnostics

- Fixed a Python default-argument bug that prevented target support spacing values from taking effect during candidate optimization.
- Re-ranked support candidates by structural diversity first: support count, column count, maximum bay spacing and maximum span length now drive A/B/C alternatives before cosmetic line shifts.
- Added front-end labels for candidate difference level: baseline, obvious difference, medium difference and highly similar.
- Added construction_visual IFC export and `/ifc-construction-visual`; representative reinforcement is exported as viewer-safe proxy geometry while retaining reinforcement property sets.
- Added UI export card for construction-visual IFC and updated IFC compatibility mode selection.
- Documented current reinforcement completeness: parameterized reinforcement plus representative groups, not full bar-by-bar construction detailing.

# V2.0.13 - 计算反馈、候选去重与内力包络可视化

- 一键计算校核改为分阶段进度反馈，显示生成工况、运行计算、A/B/C 候选完整比选的执行状态。
- Step Header 的 required 标签改为真实完成状态，避免未计算时显示“已运行计算”。
- 导出 IFC / DOCX / JSON 改为受控下载按钮，生成过程显示进度和当前操作。
- 支撑候选优化器新增几何指纹和几何差异分数，自动隐藏几何重复候选。
- 支撑线变量策略新增 global shift、center gap、alternating escape，减少候选方案同质化。
- 计算结果页新增墙体、围檩和支撑轴力关键内力包络可视化。
- 更新前后端版本号至 2.0.13。

# V2.0.12 - Windows 启动链路与包发现修复

- 修复 `pip install -e services/api` 报 `Multiple top-level packages discovered in a flat-layout: ['app', 'exports']` 的问题：在 `pyproject.toml` 中显式声明只发现 `app*` 包，排除 `exports*` 和 `tests*`。
- Windows 启动脚本不再通过 editable 安装后端项目；缺依赖时仅安装缺失的第三方包到当前 Python 环境，避免触发 setuptools 包发现路径。
- 修复 PowerShell 多行 Python 代码传入 `python -c` 后被截断导致的 `SyntaxError: '(' was never closed`：改为写入 `runtime/check_backend_modules.py` 后执行。
- Linux 启动脚本同步取消 editable 安装逻辑，保持与 Windows 一致的当前环境启动策略。
- 启动脚本增加本地 `.venv` 检测提示，但不会主动创建或激活 `.venv`。
- 后端诊断模块补充 `python-multipart` 检查。
- 更新前后端版本号至 2.0.12。

# V2.0.11 - 当前环境启动修复与运行诊断

- 修复根目录启动脚本强制创建 `.venv` 导致后端无法读取当前 Conda/系统环境模块的问题。
- Linux/Windows 启动脚本改为直接使用当前 Python 环境；缺依赖时安装到当前环境，支持 `PITGUARD_INSTALL_DEPS=0` 禁止自动安装。
- 后端依赖补充 `meshio>=5.3`，覆盖复杂 VTU 文件解析路径，避免用户环境中出现 `No module named meshio`。
- 新增 `/api/system/diagnostics`，输出 Python 解释器、Python 版本、数据库路径、依赖模块可用性和缺失模块清单。
- 前端顶部新增 API 重检按钮和运行环境提示；后端离线或缺依赖时展示明确修复命令。
- 启动脚本增加后端健康检查、启动失败日志回显、`runtime/backend.log` 与 `runtime/frontend.log`。
- 更新前后端版本号至 2.0.11。

# V2.0.10 - 功能性、人机交互与一键启动优化

- 新增设计决策驾驶舱：把流程完整性、局部锁定、候选方案、A/B/C 完整计算比选和出图风险统一显示。
- 优化工作台导航：关键状态卡片可直接跳转到对应步骤，降低流程化设计中的认知负担。
- 新增根目录跨平台启动脚本：`start-linux.sh`、`start-windows.bat`、`start-windows.ps1`。
- 更新前后端版本号至 2.0.10。

## V2.0.8 - Interactive support candidate selection and weighted constrained optimization

- Added deterministic support optimization candidate IDs and plan geometry payloads for side-by-side comparison.
- Added candidate adoption endpoint to write a selected scheme back to the retaining system.
- Added support-line locking endpoint and `optimizationLocked` support fields; locked supports keep their line positions during optimization.
- Added weighted objective presets for fewer columns, lower axial peak proxy and muck-out-path priority.
- Added candidate plan and score charts for DOCX report front-page review.
- Updated front-end result viewer with candidate plan comparison, click-to-highlight, radar bars and adopt button.

## V2.0.7 - Constrained support-line optimizer and operator-efficiency UI

- Upgraded support layout optimization from parameter enumeration to constrained support-line position optimization.
- Added hard-constraint labels, soft-objective labels, variable summaries, line-adjustment records and export-readiness status to candidate results.
- Added 3-5 ranked candidate plans for front-end comparison.
- Added operator dashboard and next-action panel to the main workflow UI.
- Added candidate comparison cards with soft-objective bars in the calculation result page.

## V2.0.6 支撑目标函数优化与 IFC 分析模型版

- 支撑自动修复器升级为目标函数候选方案优化器，输出候选方案、目标函数权重和最佳方案。
- IFC 导出扩展为 coordination_light / analysis_model / design_detailed 三模式。
- analysis_model.ifc 保留构件轴线、支撑弹簧、墙体荷载代理和施工阶段信息，不导出实体钢筋。

# Changelog

## V3.4.0

- 将出图组合、触发、拆图、比例和发行条件抽离为独立规则引擎。
- 核心预设迁移到 `packages/drawing-rules/presets/*.json`，支持企业目录覆盖。
- 增加安全条件 DSL、渲染器白名单、规则校验和决策轨迹。
- 支持按支撑层、按墙幅动态展开及多墙合图。
- 增加保留项目自定义规则的多目标候选优化。
- CAD/PDF 正式发行记录规则集和图纸计划哈希。
- 前端增加规则配置、预览、优化与候选采用工作台。

## V2.0.3 - Quality gates for support layout, IFC compatibility and official report readiness

- Added support layout quality scoring for spacing, span length, corner diagonals, temporary columns, obstacles, muck-out openings and replacement path.
- Added IFC compatibility precheck and file-level checker for raw unicode, missing references, zero-size geometry, invalid placement risk, missing material association and missing spatial containment.
- Added `/api/projects/{project_id}/export/ifc-check` and IFC sidecar `*.ifc_check.json`.
- Added formal report gate with blocking/warning/missing items.
- Added DOCX front-page officialization gate section.
- Clarified `closedLoopComplete`: it now means a no-fail software design loop, while `officialIssueGateStatus` controls official drawing/report issue readiness.
- Updated front-end assurance panel to display exact missing items instead of only `不可闭环`.


## V2.0.2 - IFC visualization, practical support spacing and UI simplification

- Fixed IFC STEP text escaping: non-ASCII names and Chinese engineering notes are exported with IFC `\X2\...\X0\` encoding to improve compatibility with strict BIM viewers.
- Changed automatic main-strut bay spacing from sparse engineering-screening layout to practical 3-6m layout with 5m target spacing.
- Added automatic excavation-to-geology center alignment when the user has not locked absolute placement.
- Preserved `supportAxisOffset`, `basementWallOffset`, `drawingLayers` and `explicitPlacement` from the CAD editor payload.
- Simplified main workflow buttons: frequent operations stay visible; less frequent operations move to side drawers.
- Enhanced CAD dragging with always-on background grid and blue alignment guide lines for horizontal/vertical coordination.
- Added regression tests for dense support spacing, IFC text escaping and geology-centered excavation placement.

## V2.0.0 - P0-P5 综合工程化迭代

- P0 围檩工程化：新增围檩多工况包络数据 `WaleBeamEnvelopeResult`，输出 M+、M-、|V|、|δ| 包络，补充挠度限值、截面自动优化历史、承压扩散尺寸和围檩-地连墙连接构造说明。
- P1 支撑生命周期：为支撑写入预加轴力阶段、拆撑阶段、生命周期说明和 `JGJ120-SUPPORT-LIFECYCLE-PATH-SUBSET` 检查；继续保留预加轴力、温度、间隙和偏心效应。
- P2 CAD 工程制图：基坑编辑器新增 DXF 导出、支护轴线偏距、地下室外墙线偏距、工程图层保存和命令行 `AXIS_OFFSET` / `BASEMENT_OFFSET` / `CLOSE` / `OPEN`。
- P3 三维审查：三维 Viewer 增加 fail/warning/manual_review 审查定位按钮，可直接查看规则、对象和说明；保留支撑轴力云图、节点颜色、剖切和测距。
- P4 计算书图表数据：计算结果新增 `reportDiagramData`，DOCX 计算书增加 V2.0 摘要、耦合摘要和围檩包络表，为后续自动出弯矩图/剪力图/挠度图提供数据。
- P5 核心计算接口：阶段结果新增 `coupledSystemResult`，集中记录墙体弹性地基梁、围檩连续梁和支撑弹性反力摘要；保留全局联立矩阵、三维有限元和监测反分析为后续生产级内核。
- 验证：后端 pytest 34 passed；前端测试 3 passed；前端生产构建通过；样例工作流无 fail。

## V1.7.0 - 围檩本体设计、施工效应、CAD/Viewer/计算书深化

- 新增 `WaleBeamInternalForceResult` 和 `WaleBeamDesignResult`，输出围檩弯矩、剪力、挠度、设计内力、正截面配筋、斜截面抗剪、节点区附加筋协调。
- 围檩构件自动按内力包络调整截面并生成主筋、箍筋、节点附加抗裂筋，避免样例工程出现可通过扩截面解决的硬性 fail。
- 支撑轴力叠加预加轴力、温度约束、节点间隙闭合和施工偏心/偏差筛查效应，并写入支撑、阶段支撑力、IFC 属性和前端表格。
- CAD-like 编辑器新增 DXF 导入、坐标命令行、尺寸标注、选中边偏移、多段线闭合/断开、图层管理和障碍物绘制。
- 三维 Viewer 新增支撑轴力云图、节点颜色、剖切轴/剖切滑块、测距、中文属性表、最大轴力支撑/最大弯矩墙段定位入口。
- DOCX 计算书新增围檩连续梁节点反力表、支撑施工效应表、围檩本体设计表和节点附加筋协调说明。
- 新增 `WALE_BEAM_DESIGN_AND_UI_V1_7.md`。

验证：后端 `33 passed`，前端 `3 passed`，前端构建通过，样例工作流 `fail=0`。

## V1.6.0 - 围檩连续梁反力分配与 CAD-like 基坑轮廓编辑器

- 将支撑轴力分配从 V1.5 的墙面 tributary width 估算升级为围檩连续梁—弹性支撑节点反力模型。
- 新增 `services/api/app/calculation/wale_beam.py`，按墙面、支撑层、支撑节点建立一维围檩梁单元模型。
- `SupportForceResult` 新增 faceCode、supportEndpoint、waleBeamCode、waleChainage、continuousBeamReaction、elasticSupportStiffness、normalProjectionFactor、beamNodeCount、distributionMethod 等字段。
- `run_calculation` 已将每个墙面段的支撑轴力计算切换为连续围檩梁模型；条件不足时自动退化为 tributary width fallback。
- 前端 `ResultViewer` 新增围檩连续梁—支撑节点反力表。
- 前端 `ExcavationEditor` 升级为 CAD-like 编辑器，支持拖拽点、边中点插入、删除选中点、撤销/重做、滚轮缩放、Alt/中键平移、网格吸附、正交约束、自交/短边/标高校验和 Fit view。
- 新增 `WALE_BEAM_AND_CAD_EDITOR_V1_6.md` 记录算法与交互边界。
- 后端测试：32 passed；前端测试：3 passed；前端构建成功；样例工作流 `checkSummary.fail = 0`，`governingCheckStatus = warning`。

## V1.5.0 - 支撑体系工程化设计

- 新增支撑-围檩节点 `SupportWaleNode` 和节点承压板 `BearingPlateDesign`。
- 新增节点局部承压子集校核 `GB50010-NODE-BEARING-SUBSET`，并自动生成节点附加筋、加密箍筋和高轴力端部抗裂筋。
- 临时立柱基础由默认扩大基础升级为立柱桩设计，新增 `GB50007-2011-COLUMN-PILE-CAPACITY-SUBSET`。
- 基坑轮廓支持障碍物 `ConstructionObstacle`，用于地下室柱网、坡道、出土口、中心岛和保护区避让。
- 新增环撑/中心岛式支撑体系原型：生成 `ringBeams` 与 `ring_strut`。
- 支撑轴力由“同层全局均分”升级为“支撑端点墙面 tributary width 关联”。
- 施工工况增加 `stageType`、`zone`、`deactivatedSupportIds` 和 `replacementAction`，并输出换撑路径。
- 前端围护结构页面显示支撑分仓、跨长、角撑/环撑逻辑、节点承压、节点配筋、立柱桩和立柱服务范围。
- IFC 导出增加环梁、支撑节点、支撑端 tributary width、立柱桩属性。
- 新增 `SUPPORT_SYSTEM_ENGINEERING_V1_5.md`。
- 验证：后端 pytest 30 passed；前端 vitest 2 passed；前端 build 成功；样例工作流 fail=0，governingCheckStatus=warning。

## V1.4.0 - 水平支撑拓扑化布置算法

### Added

- 新增 `services/api/app/services/support_layout.py`，将水平支撑布置从外包矩形简化逻辑升级为拓扑化算法。
- 新增主对撑扫描线算法：识别基坑长短边，主对撑沿短跨方向布置，沿长向按目标间距分仓。
- 新增凹形基坑处理：通过扫描线与多边形求交生成有效支撑区间，避免支撑穿越坑外空区。
- 新增凸直角角撑生成：仅在凸直角生成角撑，凹角不自动跨越。
- 新增临时立柱自动布置：根据主对撑跨长在跨中或分跨点生成 `ColumnElement`。
- `SupportElement` 新增 `spanLength`、`baySpacing`、`startFaceCode`、`endFaceCode` 字段。
- `ColumnElement` 新增 `supportCodes` 字段，记录立柱服务的支撑编号。
- IFC 导出增加支撑跨长、分仓间距、立柱关联支撑编号等属性。
- 新增 `SUPPORT_LAYOUT_ALGORITHM.md`，说明水平支撑拓扑化算法。

### Changed

- 支撑轴力估算由同层支撑均分升级为按角色加权分配：主对撑权重 1.0，角撑权重 0.35。
- 自动支撑布置说明文本更新为“拓扑化自动建议”，明确仍需围檩节点、立柱桩、换撑和施工空间复核。
- 后端、前端版本号更新为 `1.4.0`。

### Tests

- 后端测试更新为 27 项，新增凹形基坑避空测试和立柱由支撑跨长生成测试。
- 验证 `python scripts/run_sample_workflow.py` 可运行，样例工程 `checkSummary.fail = 0`。

## V1.2.0 - 闭环可信度与立柱基础修复

### Added

- 根目录新增 `README.md`，说明后端启动、前端启动、测试命令、样例工作流和导出文件位置。
- 根目录新增 `CHANGELOG.md`，记录当前版本能力和修复内容。
- 根目录新增 `AI_CODING_SPEC.md`，作为后续 vibe coding 和增量开发依据。
- 新增 `FoundationDesign` 领域模型，用于保存临时立柱基础初选结果。
- `ColumnElement` 新增 `foundationDesign` 字段，记录基础类型、尺寸、面积、自重、竖向荷载、fa、平均压力、最大压力和状态。
- 新增 `design_column_foundation()`，按竖向荷载和 GB50007 承载力子集自动扩大立柱基础尺寸。
- Assurance API 新增 `capabilityCompleteness`、`softwareFlowComplete`、`engineeringCheckStatus` 字段。
- 新增后端测试：立柱基础自动扩基检查和 `run_sample_workflow.py` 等效 API 闭环检查。

### Changed

- 立柱基础不再固定使用 3.0m x 3.0m。
- 立柱基础承载力子集检查保留 GB50007 规则，不通过删除检查项规避 fail。
- 当基础压力超过 `fa` 或偏心最大压力超过 `1.2fa` 时，系统自动按 0.25m 模数扩大基础边长。
- 若达到最大自动扩基尺寸后仍不满足，基础设计保持 fail/manual_review，并在校核结果中给出修改建议。
- Assurance 逻辑由单一 `completionPercent` 改为“功能完成度 + 工程校核状态 + 闭环状态”分离表达。
- `completionPercent` 保留为兼容字段，与 `capabilityCompleteness` 同值。

### Fixed

- 修复 `scripts/run_sample_workflow.py` 样例流程出现 `GB50007-2011-BEARING-SUBSET` fail 的问题。
- 修复“软件功能完成”和“工程设计通过”混用导致的闭环可信度问题。
- 样例项目的临时立柱基础会根据计算荷载从 3.0m x 3.0m 自动扩大到满足承载力子集的尺寸，典型为约 3.5m x 3.5m。

### Verification

- 后端测试：`25 passed`。
- 样例工作流：`python scripts/run_sample_workflow.py` 可完成 JSON、IFC、DOCX 和 checks 导出。
- 样例计算结果：`checkSummary.fail = 0`，`governingCheckStatus = pass`。
- Assurance 结果：`capabilityCompleteness = 100.0`，`engineeringCheckStatus = pass`，`closedLoopComplete = true`。

## V1.1.0 - 可运行工程原型

### Current capability before V1.2.0

- FastAPI 后端与 React/Vite 前端原型。
- 项目 CRUD 与 SQLite JSON 存储。
- 钻孔 CSV/XLSX 导入、地层参数合并和输入校验。
- IDW 三维地质模型和 VTU 导入。
- 基坑轮廓、边段生成、地连墙和支撑自动初选。
- 土压力、水压力、支撑轴力、墙体内力和配筋建议。
- JGJ120、GB50010、GB50007、GB50009、GB50017、GB55003、GB55008 规范子集筛查。
- IFC4 STEP 导出和 DOCX 计算书导出。

### Known problems before V1.2.0

- 根目录缺少 README、CHANGELOG 和 AI_CODING_SPEC。
- 样例脚本运行后会出现 `GB50007-2011-BEARING-SUBSET` fail。
- 立柱基础尺寸硬编码为 3.0m x 3.0m，不能根据荷载自动调整。
- Assurance 将功能完成度和工程校核状态混合表达，容易误导用户。
- 前端仍为原型级 tab 交互，工程流程引导和三维交互精细度不足。

## V1.3.0 - 前端工程流程重构

### Added

- 前端 `ProjectWorkspace` 从原来的横向 tab 页面重构为 8 步工程流程向导：项目设置、地勘资料、三维地质模型、基坑轮廓、围护结构、计算校核、闭环审查、BIM 与计算书。
- 新增流程 Stepper，按当前项目数据自动显示 `done / ready / blocked / warning / error` 状态。
- 新增工作台顶部状态条，集中显示流程完成度、fail 数、warning 数和人工复核项数量。
- 新增每个步骤的前置条件、当前状态、关键摘要和下一步按钮。
- 新增闭环审查卡片，分别展示 `capabilityCompleteness`、`softwareFlowComplete`、`engineeringCheckStatus` 和 `closedLoopComplete`。
- 新增导出卡片，将 IFC、DOCX 和 JSON 导出入口独立展示，并在存在 fail 或缺少计算时提示风险。
- 新增 V1.3.0 前端工作台样式，降低按钮堆叠感并强化工程流程语义。

### Changed

- 前端不再让用户在 overview/geology/excavation/design/calculation 等英文 tab 间自行判断流程顺序。
- 保留原有后端 API 和核心功能组件，外层交互重构为“工程步骤驱动”。
- 地质建模、围护设计和计算操作改为当前步骤内的 action strip，减少主页面按钮噪声。
- Assurance 前端显示由单一完成度描述改为功能完成度、软件流程、工程校核和出图闸门分离表达。
- 前端版本号更新为 `1.3.0`；后端 API 版本和 Assurance `softwareVersion` 同步更新为 `1.3.0`。

### Known limitations

- 基坑轮廓编辑器仍为点表 + SVG 显示，尚未实现完整 CAD-like 交互。
- 三维 Viewer 已可显示工程对象，但 OrbitControls、剖切滑块、测量、构件属性中文化和 fail 构件定位仍需下一阶段增强。
- 项目设置页当前以摘要显示为主，尚未实现规范版本和设计控制参数的前端编辑表单。

## V2.0.1 - Frontend npm install fix

- Fixed `apps/web/package-lock.json` tarball URLs that pointed to an internal OpenAI npm proxy.
- Added `apps/web/.npmrc` to force the public npm registry.
- Added `NPM_INSTALL_FIX.md` with local Windows/Linux recovery commands.
- Bumped frontend package version to `2.0.1`.

## V2.0.4 - Support layout visualization, IFC viewer profiles, report checklist and crossing control

- Added support-layout quality highlights for front-end plan and 3D visualization.
- Added support crossing detection; same-level supports that cross without a modeled node are quality-gate fail items.
- Added automatic support-layout cleanup to skip candidate braces that would cross existing supports.
- Added IFC viewer profile risk grading for BlenderBIM/Bonsai, BIMVision, Solibri, Autodesk Revit and Navisworks.
- Added model preview frame below export download links.
- Added formal report homepage checklist sections for calculation status, support layout, IFC compatibility, output completeness and blocking items.
- Updated DOCX report to include the fixed homepage checklist and viewer compatibility matrix.

## V2.0.5 - Support auto-repair, dual IFC export and report-linked layout QA

- Added support layout auto-repairer: dense re-bay, support-line shifting around obstacles, crossing avoidance, temporary column/node regeneration.
- Added `/design/auto-repair-supports` endpoint.
- Added dual IFC export modes: `coordination_light` and `design_detailed`.
- Added `/export/ifc-light`, `/export/ifc-detailed`, and mode-aware `/export/ifc-check`.
- Reduced IFC Revit/Navisworks risk for `coordination_light` mode by omitting detailed rebar, plates and embedded-anchor entities.
- Added support layout quality plan PNG and inserted it on the DOCX report front quality page.
- Added V2.0.5 regression tests and sample workflow dual IFC outputs.


## V2.2.0 - 交付闭环、任务队列与问题清单中心

- 新增后端任务队列：计算、候选比选、IFC、CAD、SVG、DOCX、JSON 和完整交付包都可后台执行。
- 新增 `/api/projects/{project_id}/issues` 问题清单中心，动态评估完成度、阻断项、警告项和下一步动作。
- Step 6 一键计算校核改为任务化执行，前端显示真实进度、当前步骤和任务日志。
- Step 8 导出操作改为任务化生成并从任务结果下载。
- 新增完整交付包入口，串联计算、IFC、CAD、SVG 和 DOCX。
- 版本更新为 2.2.0。

## V2.4.0 - Normative benchmark regression, object localization and drawing/detailing hardening

- Added object-level issue locators for workflow step, target panel, object type, object id/code, plan coordinate and CAD sheet.
- Added rebar detailing endpoint and frontend panel with bar marks, shape codes, quantity, length, weight and manual review flags.
- Upgraded CAD package with enterprise title blocks, dimension lines, drawing register, material schedule and S-07 rebar bending schedule.
- Added public-paper-derived benchmark catalog and benchmark export package.
- Added five normative-regression benchmark cases derived from public excavation case-study metadata.
- Added V2.4.0 backend tests for benchmark catalog, benchmark flow and rebar detailing schedule.
- Kept finite-element integration out of scope; all benchmark cases use normative/rule-based workflow.


## V2.6.0

- 修复上传案例一键复核计算卡在支撑候选深拷贝的问题。
- 计算追溯链公式改为数学化显示。
- 简化导出入口，常用成果优先，调试成果收纳到更多导出。
- 钢筋可视化改为折线/闭合箍筋形态，补充搭接和弯钩表达。
- 结果页新增围护墙变形、弯矩、剪力云图。

## V2.6.1

- 修复前端 KaTeX CSS 依赖未安装时 Vite 无法解析 `katex/dist/katex.min.css` 的问题。公式显示改为内置轻量数学表达组件，不再依赖外部 CSS 文件。
- Windows/Linux 一键启动脚本会检查关键前端模块；当 `node_modules` 存在但依赖不完整时自动执行 `npm install` 更新当前前端环境。
- 计算追溯链升级为条文对比表，按合规、不合规、预警、复核汇总，并显示需求值、限值、利用率、数学公式和规范条文。
- 进一步压缩界面研发性说明文字，保留设计使用所需状态、对象和建议。
