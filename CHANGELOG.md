
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
- JGJ120、GB/T50010、GB50007、GB50009、GB50017、GB55003、GB55008 规范子集筛查。
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
