## V2.0.8 Addendum - Human-in-the-loop support optimization

Support optimization candidates must carry deterministic IDs, objective weights, hard-constraint results, soft-objective scores, line-adjustment records, plan geometry for UI comparison, and export-readiness flags. Candidate adoption must regenerate the candidate from its target spacing, column span, and line-position pattern instead of storing a shadow model. Locked support lines must be preserved during optimization and displayed in candidate plan geometry.

## V2.0.6 支撑目标函数优化与 IFC 分析模型版

- 支撑自动修复器升级为目标函数候选方案优化器，输出候选方案、目标函数权重和最佳方案。
- IFC 导出扩展为 coordination_light / analysis_model / design_detailed 三模式。
- analysis_model.ifc 保留构件轴线、支撑弹簧、墙体荷载代理和施工阶段信息，不导出实体钢筋。

# AI Coding Spec：PitGuard BIM Designer

版本：V2.0.0 P0-P5 综合工程化迭代基线  
目标：将当前可运行工程原型继续迭代为可交互、可审查、可扩展的基坑围护结构智能设计软件。

## 1. 总指令

你是本项目的高级工程软件开发助手。后续所有代码修改必须保持“可运行、可测试、可追溯”。不要一次性重写整个系统；每次只实现一个明确阶段，并补测试、文档和变更记录。

必须遵守：

1. 数据模型优先，界面和计算都必须围绕领域模型展开。
2. 规范公式、限值、构造要求放入 `services/api/app/rules/`，不要散落在前端组件或路由中。
3. 工程计算结果必须可追溯到输入参数、施工工况、规范规则编号和计算时间。
4. 暂未完整实现的规范项必须标记为 `manual_review`，不能伪装成完整通过。
5. 不能通过删除检查项规避 fail。应修正设计逻辑；如仍不能满足，保留 fail 并给出工程建议。
6. 前端不得伪造后端计算结果。
7. 每次迭代必须运行后端 pytest；涉及前端时必须运行 npm test 和 npm run build。
8. 每次迭代必须更新 `CHANGELOG.md`；若修改启动、测试、工作流或输出位置，必须同步更新 `README.md`。

## 2. 当前技术栈

```text
Frontend: React + TypeScript + Vite
3D Viewer: Three.js
State: Zustand
Backend: Python FastAPI
Data Models: Pydantic
Database: SQLite JSON store
Geometry/Math: numpy, shapely
Reports: python-docx
IFC Export: internal STEP writer / IFC4-oriented exporter
VTU Parsing: internal lightweight parser
Testing: pytest + vitest
```

## 3. 当前项目结构

```text
pitguard-bim-designer/
├─ README.md
├─ CHANGELOG.md
├─ AI_CODING_SPEC.md
├─ DOCS/
├─ apps/web/
├─ services/api/
├─ packages/sample-data/
├─ scripts/
└─ sample-output/
```

核心后端目录：

```text
services/api/app/
├─ calculation/      # 土压力、支撑轴力、墙体内力、施工阶段计算
├─ compliance/       # assurance/gap-analysis
├─ geology/          # IDW、地质模型、剖面提取
├─ ifc/              # IFC 导出
├─ reports/          # DOCX 计算书
├─ routers/          # FastAPI routes
├─ rules/            # 规范规则库
├─ schemas/          # Pydantic 领域模型
├─ services/         # 地勘导入、围护设计、配筋、VTU导入
└─ storage/          # SQLite 项目存储
```

## 4. V1.2.0 已完成基线

V1.2.0 已完成：

1. 根目录文档补齐。
2. 样例工作流 `scripts/run_sample_workflow.py` 可完整运行。
3. 自动扩建立柱基础，修复 GB50007 承载力子集 fail。
4. Assurance 输出拆分为 `capabilityCompleteness`、`softwareFlowComplete`、`engineeringCheckStatus`、`closedLoopComplete`。
5. 新增 `FoundationDesign` 领域模型。
6. 后端测试达到 `25 passed`。

后续开发必须以 V1.2.0 为基线，不要回退到固定 3.0m x 3.0m 基础逻辑。

## 5. 后续阶段路线

### Phase A：工程流程向导

目标：将当前 tab 堆叠式页面升级为工程流程工作台。

页面步骤：

```text
01 项目设置
02 地勘资料
03 三维地质模型
04 基坑轮廓
05 围护结构
06 施工工况
07 计算校核
08 BIM 与计算书导出
```

每一步必须显示：状态、完成条件、主要操作、输入摘要、问题列表、下一步按钮。

验收：用户可以不读说明就按步骤完成 sample workflow。

### Phase B：基坑轮廓 CAD-like 编辑器

目标：替换当前坐标表格为可交互平面编辑器。

必须实现：

1. 点拖拽。
2. 点击边插入点。
3. 删除点。
4. 滚轮缩放。
5. 画布平移。
6. 网格和坐标轴。
7. 端点、网格点、中点吸附。
8. Shift 正交约束。
9. 撤销/重做。
10. 自交、短边、未闭合、标高错误实时提示。
11. 保存后显示边段编号和长度。

### Phase C：三维 Viewer 升级

目标：把 Three.js viewer 从展示骨架升级为工程审查工具。

必须实现：

1. OrbitControls。
2. 俯视、前视、侧视、等轴测、Fit All。
3. 图层树。
4. 点击对象高亮。
5. 中文属性面板，不直接展示 JSON。
6. X/Y/Z 剖切面。
7. 支撑轴力、墙体位移、校核状态颜色映射。
8. fail 构件定位。
9. 图例和基础测量工具。

### Phase D：支撑拓扑设计

目标：支撑布置从 bbox 规则升级为多边形拓扑设计。

新增模型建议：

```text
DesignFace
SupportTopology
SupportNode
ColumnLayout
FoundationDesign
```

要求：

1. 从 polygon 提取连续设计面。
2. 建立对边关系。
3. 矩形/近似矩形生成短向对撑。
4. L 形、凹多边形不得生成穿越坑外支撑。
5. 长宽比较大时生成角撑。
6. 支撑端点吸附到围檩。
7. 支撑交点和长支撑跨中自动布置立柱。
8. 每根立柱基础按荷载自动设计。

### Phase E：计算书增强

目标：让 DOCX 更接近工程计算书。

必须增加：

1. 基坑平面轮廓图。
2. 代表性地质剖面图。
3. 土压力和水压力曲线。
4. 墙体弯矩、剪力、位移包络图。
5. 支撑轴力汇总表。
6. 立柱基础设计表。
7. fail/warning/manual_review 索引。
8. 公式和规则编号追踪表。

## 6. 推荐下一条 Coding Prompt

```text
请实现 Phase A：工程流程向导。

目标：将当前 ProjectWorkspace 的 tab 堆叠式界面改造成 8 步工程流程工作台。

必须完成：
1. 新增 WorkflowStep 类型，包含 id、title、status、completionCriteria、primaryActions、issues。
2. 根据当前 project 数据自动计算每一步状态：pending / ready / done / error / needs_recalculation。
3. ProjectWorkspace 左侧显示步骤导航，中间显示当前步骤主内容，右侧显示该步骤输入摘要和问题列表。
4. 不删除现有功能组件，而是将 BoreholeImport、VtuImport、ExcavationEditor、Engineering3DViewer 等嵌入对应步骤。
5. 下一步按钮只有在当前步骤满足基础条件时可用。
6. calculation checks 中的 fail 必须在计算校核步骤红色显示，并能跳转到对象或结果表。
7. 更新前端测试。
8. 更新 README 和 CHANGELOG。

验收：
1. 用户可以按步骤完成项目创建、导入地勘、生成地质模型、创建基坑、自动设计、运行计算、导出成果。
2. 前端 npm test 通过。
3. 后端 pytest 仍通过。
```

## 7. 禁止事项

1. 禁止删除工程规则以通过测试。
2. 禁止把 fail 降级为 warning 来伪造闭环。
3. 禁止前端生成设计结果替代后端计算。
4. 禁止引入无法安装或没有必要的大型依赖。
5. 禁止把所有逻辑堆到单个 React 组件或单个 Python 文件。
6. 禁止修改 API 返回结构时不保留兼容字段。

---

# V1.3.0 Frontend Workflow Refactor Addendum

## Goal

The front-end workspace must be workflow-driven rather than tab-driven. The user should see a clear engineering sequence from project settings to export, with status, prerequisites and next actions at each step.

## Implemented workflow steps

1. Project settings
2. Borehole and strata data
3. 3D geological model
4. Excavation outline
5. Retaining structure
6. Calculation and checks
7. Assurance and output gate
8. BIM and report export

## Rules for future front-end work

- Do not add more global buttons to the workspace header.
- Put actions inside the current workflow step.
- Each step must expose status: done, ready, blocked, warning or error.
- Keep `capabilityCompleteness`, `softwareFlowComplete`, `engineeringCheckStatus` and `closedLoopComplete` visually separated.
- Never represent software workflow completion as engineering approval.
- Future CAD-like editing and 3D review tools should be embedded inside the relevant workflow step rather than added as separate unrelated pages.

# V1.4.0 Support Layout Algorithm Addendum

后续开发水平支撑相关功能时，必须以 `services/api/app/services/support_layout.py` 作为唯一自动布置入口。禁止重新在 UI 或其他服务中用外包矩形直接生成支撑。

当前支撑自动布置的核心约束：

1. 主对撑沿短跨方向布置。
2. 沿长向分仓，默认目标间距为 18m。
3. 凹形基坑必须通过扫描线-多边形求交得到有效支撑区间。
4. 凸直角可生成角撑，凹角不得自动跨越布撑。
5. 临时立柱应从主对撑跨长和支撑平面拓扑生成。
6. 支撑轴力估算应区分 main_strut 与 corner_diagonal 的角色权重。
7. 所有自动布置结果都必须带 `layoutNote`，并保留人工复核提示。

下一步增强方向：

- 环撑和中心岛式支撑拓扑。
- 坡道、出土口、塔吊、地下室柱网避让。
- 支撑节点和围檩连接节点建模。
- 立柱桩与格构柱设计。
- 根据墙面 tributary width 而不是全局同层支撑进行支撑轴力分配。


# V1.5.0 Support System Engineering Addendum

后续 coding agent 必须保留以下设计边界：

1. `SupportWaleNode` 是支撑与围檩节点的一等公民，不能只在前端显示几何线。
2. 支撑端部承压必须在计算阶段由支撑轴力包络更新，不能长期使用固定承压板。
3. 临时立柱优先采用 `column_pile`，扩大基础只作为浅小基坑或桩设计失败时的备选。
4. `ConstructionObstacle` 必须参与支撑线和立柱点避让。
5. 环撑体系使用 `ringBeams` + `ring_strut` 表达，后续可扩展为中心岛、栈桥和分区施工模型。
6. 支撑轴力应按墙面 tributary width 计算，禁止回退到同层全局均分。
7. 前端必须展示支撑角色、跨长、分仓、连接墙面、tributary width、节点承压、立柱桩和换撑路径。


# V1.6.0 Wale Beam Reaction Addendum

Coding agents must preserve the V1.6.0 support-force path: wall pressure band -> continuous wale beam -> elastic support-node reaction -> support axial force. Do not replace it with same-level averaging. Tributary width may remain as an explanatory fallback and UI reference. The excavation editor must remain CAD-like: point dragging, edge insertion, delete selected point, undo/redo, pan/zoom, snap, orthogonal constraint, and real-time geometry validation.

# V1.7.0 Wale Beam Design and Engineering UI Addendum

Coding agents must preserve the V1.7 data path: wall pressure band -> continuous wale beam -> internal-force envelope -> RC wale design -> support construction effects -> IFC/report/UI export. Do not remove the wale flexure/shear/node coordination checks to hide failures. If a preliminary wale section fails, first attempt section auto-sizing and retain warnings that final detailing requires professional review.

The excavation editor must keep DXF import, coordinate command line, dimension annotations, selected-edge offset, closed/open polyline toggling, layer controls and obstacle drawing for ramps, muck-out openings and center islands.

# V2.0.0 P0-P5 Engineering Addendum

Coding agents must preserve the V2.0 data path: wall pressure and wall elastic-foundation beam -> continuous wale beam -> multi-stage wale envelope -> wale section optimization -> support lifecycle effects -> report diagram data -> review-oriented UI. Do not remove warning/manual_review checks to make a project look passed. When a production-grade formula is missing, add a traceable screening result and a professional-review boundary.

Required V2.0 objects and fields:

- `WaleBeamEnvelopeResult` for M+/M-/|V|/|δ| envelopes.
- `WaleBeamDesignResult.optimizationHistory`, `deflectionLimit`, `wallConnectionNote`, and `envelope`.
- `SupportElement.lifecycleNote`, `preloadStageId`, `removalStageId`.
- `StageCalculationResult.coupledSystemResult`.
- `CalculationResult.designIterationSummary`, `optimizationActions`, and `reportDiagramData`.

Next production target: replace coupled-summary storage with a global wall-wale-support stiffness solver and generate report figures from `reportDiagramData`.

# V2.0.0 Global Coupled Matrix Addendum

The next coding phases must treat the V2.0.0 implementation as the new baseline.

Required preserved capabilities:

1. `StageCalculationResult.globalCoupledResult` must remain populated after calculation when retaining-system supports are available.
2. Support forces should keep continuous-wale / tributary-width trace fields while the governing force is updated from the global coupled matrix when available.
3. `CalculationResult.designReviewSummary` must classify checks into strength, stiffness and stability.
4. DOCX reports must include generated chart images from `services/api/app/reports/charts.py`.
5. Excavation editor geometry operations must preserve closed-polyline validity and should not silently generate self-intersecting offset lines.
6. Groundwater and stability checks must include dewatering-stage, layered-seepage and weak-underlying-layer screening.
7. All global-matrix outputs remain design-assist results; production work must still implement a full 3D frame/FEM or formally verified 2D staged solver before claiming construction-document readiness.

# V2.0.0 Spatial Frame, Stability and Drawing Addendum

Coding agents must preserve the V2.0 data path: pressure profile -> spatial wall-wale-support-column-slab matrix -> support reactions and wall/wale rotations -> reviewable stability package -> construction detail sheets -> detailed IFC/export/report. Do not downgrade `stabilityDetailedResult`, `drawingSheets`, or V2.0 global matrix fields to string-only summaries.

Required V2.0 outputs:
- `GlobalCoupledSystemResult.spatialMatrixSize`, `spatialDofSummary`, `wallRotationProfile`, `waleNodeProfile`, `supportAxialDofs`, `columnVerticalDofs`, `rigidNodeZones`.
- `CalculationResult.stabilityDetailedResult` with circular slip candidates, seepage paths, drawdown process, dewatering wells, depressurization wells and improvement options.
- `CalculationResult.drawingSheets` with support plan, wale-node detail, wall rebar cage and column pile detail sheet references.
- IFC exports must retain detailed reinforcement, bearing plate, embedded anchor/preembedded proxy and column pile properties.

# V2.0.3 Quality Gate Addendum

新增质量闸门要求：

1. 支撑布置合理性评分必须检查主对撑 3-6m 分仓间距、跨长、角撑、临时立柱服务范围、障碍物/出土口避让和换撑路径。
2. IFC 导出必须执行模型级和文件级兼容性自检，至少覆盖 raw unicode、未定义引用、零尺寸构件、placement、材料关联和空间归属。
3. 计算书首页必须集中显示正式化检查：fail/warning/manual_review、支撑布置风险、稳定专项缺项、IFC 风险和报告图表完整性。
4. `closedLoopComplete` 只表达软件设计闭环，正式出图许可由 `officialIssueGateStatus` 和 `allowedForOfficialIssue` 表达。不要用“不可闭环”笼统替代具体缺项。

# V2.0.4 Quality Visualization Addendum

后续 coding agent 必须保持以下约束：

1. 支撑中心线同层无节点交叉必须作为 quality gate fail，不得只在界面隐藏。
2. `SupportLayoutQualitySummary.highlights` 是前端平面和三维高亮的数据源，新增支撑布置规则时必须同步生成 highlight geometry。
3. IFC 兼容性检查必须输出 viewerProfiles，不得只给单一 pass/warning/fail。
4. 正式计算书首页必须包含 checklistSections，且阻断项、警告项、缺项、人工复核项必须在首页出现。
5. 导出页必须在下载链接下方提供模型预览，便于下载前做几何和质量闸门复核。

# V2.0.5 Addendum

- Support layout repair must run before staged calculation when possible, because regenerated support IDs must be synchronized with construction stages.
- IFC export must support `coordination_light` and `design_detailed` modes. Do not remove parameterized reinforcement properties from light mode; only omit heavy explicit entities.
- Report chart generation must include `support_layout_quality_plan.png` when retaining-system supports are available.
- Formal reports should present support repair and support layout quality on the first quality-gate page.
