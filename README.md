# PitGuard V3.87.11 三维全屏与流程稳定性版

V3.87.11 为工程三维模型、项目综合模型、围护墙受力云图和钢筋 IFC 模型统一增加全屏操作，支持浏览器原生全屏、Safari 兼容接口和页面内全屏降级。进入或退出全屏后会主动触发模型尺寸重算，避免画布拉伸、空白或坐标拾取偏移。

本版同时收敛前端运行时稳定性：WebGL 渲染器统一创建、限帧、离屏暂停、上下文丢失恢复和 GPU 资源释放；开发环境默认关闭 React StrictMode 双挂载；写请求不再共享在途响应；临时 HTTP 错误采用有界退避；工程刷新使用世代号隔离旧响应；顶部持续显示数据库、计算 Worker、内存、磁盘与任务队列的流程健康状态。

V3.87.11 不修改结构计算算法、规范规则集和结果管线合同，因此既有 V3.87.10 有效计算结果不会仅因界面与运行时升级被强制判定过期。

详见：`docs/releases/V3_87_11_FULLSCREEN_AND_WORKFLOW_STABILITY.md`。

---

# PitGuard V3.87.10 围护墙钢筋笼平面路径一致性版

V3.87.10 修复配筋三维视图中围护墙钢筋笼平面轮廓与工程输入围护墙轮廓不一致的问题。系统建立单一权威墙路径，按当前基坑分段和设计面解析墙轴线，历史槽段端点仅用于偏差审计；槽段、钢筋笼、IFC、CAD 与配筋工程量统一按当前墙路径里程重建。前端沿完整折线生成纵筋、分布筋和拉结筋，凹角与局部转折不再被首尾弦线切割。

新增墙面几何审计，显示自动重建槽段数量、历史坐标最大偏差和未解析墙面。旧工程在重新应用配筋方案时会自动把槽段几何持久化到当前围护墙路径，并尽量保留槽段编号、接头类型和人工复核属性。

详见：`docs/releases/V3_87_10_CANONICAL_WALL_PATH_REBAR_CAGE_GEOMETRY.md`。

---

# PitGuard V3.87.9 配筋计算合同自动恢复版

V3.87.9 修复“结构计算已经闭合，但配筋深化仍提示当前计算合同与设计快照不一致”的伪阻断。P3 与配筋 Worker 会读取包含外置施工阶段结果的权威工程快照；对可自动恢复的计算合同失效，系统先补算当前方案、持久化计算证据、重新生成配筋，再复核 P3 入口。前端在后台恢复期间显示处理中间状态，并在任务结束后轮询权威配筋合同，避免旧诊断长期停留。

计算合同校核现在会明确列出差异字段，例如 `adoptedDesignSnapshotHash`、`supportTopologyHash`、`caseHash`、算法版本或求解运行时。真实硬失败仍保持阻断；单纯的证据外置、保存时序或快照变化可由主操作自动恢复。

# PitGuard V3.87.7 换撑传力路径自动恢复与闭合版

V3.87.7 针对旧支撑 ID 导致换撑/拆撑阶段无法进入优化搜索的问题，新增语义支撑层谱系、标准自下而上换撑序列重建、当前拓扑审计筛选工况与异常支撑层归并。系统会先恢复可审计的标准传力路径，再执行体系—截面联合搜索；只有用户冻结或专项非标准换撑仍保留人工复核。
V3.87.6 将计算页的阻断修复、当前方案计算、多目标优化和逐项加固合并为一个入口。系统先修复常规施工阶段中的旧支撑引用，再对截面增强、平面支撑加密和增设支撑层候选执行完整计算。无可闭合候选时明确输出 `cannot_close`，并展示剩余控制项。

详见：`docs/releases/V3_87_7_TRANSFER_PATH_AUTO_RECOVERY.md`。

---

# PitGuard V3.87.5 相对加固与一键多目标优化版

V3.87.5 修复连续点击“增厚并复算”后设计值和计算结果不变化的问题。可自动执行的墙厚、墙趾、梁截面和支撑深化措施改为基于数据库当前值的有界相对增量；每次任务记录构件修改前后值、输入快照哈希、结果哈希和配筋刷新状态，避免重复写入历史绝对建议值。

计算阻断解决中心新增“一键优化并复算”。系统在隔离副本中评估当前闭环、经济分区、刚度优先和截面优先四类候选，按计算闭合、结构闭合、失败数量、安全储备、位移和材料代理增量排序，只采用当前搜索边界内的最优可行方案，再在正式工程上重新计算并更新配筋。V3.87.4 的工程路由保持、异步地勘导入和前置阻断修复能力全部保留。

V3.87.3 对 V3.87.2 的界面流程进行收敛。项目工作区只保留“设计基准—工程输入—围护方案—计算验算—配筋深化—成果交付”六阶段主导航；原九个设计核心阶段改为内部质量证据域，通过右上角“质量与追溯”按需打开，并按六个主阶段分组显示。质量中心不再占用主页面、不承担流程导航，关闭后不会改变当前步骤。

V3.87.3 主要变更：

- 删除主页面中常驻的第二套“V3.87 设计主流程”面板；
- 新增按需侧栏式“设计质量与追溯中心”，默认不加载设计核心聚合数据；
- 将九个内部证据域映射到六个主步骤，避免九阶段与六阶段竞争；
- 质量中心可跳转到对应主步骤，但不自行维护另一套流程状态；
- 主页面首屏减少一组聚合请求和大面积卡片内容，降低认知负担；
- 在线文档统一称为“六阶段单一设计主流程”，九个阶段仅作为质量证据域；
- 保留 V3.87.2 的参数来源门禁、候选预览 V3、闭合转接体系显示、工作区证据压缩和候选去重。

当前发行状态仍为 `engineering_preview`。前端完整 Vitest、TypeScript 工程编译和 Vite 生产构建应在可用 npm 环境中执行后再进入生产部署。

V3.87.2 在 V3.87.1 界面与异形支撑预览修复基础上，继续加固设计核心的数据契约、参数来源、候选搜索、工作区摘要和前端并发读取。该补丁不修改 V3.87.0 结构计算内核，重点防止界面残留、旧缓存丢失闭合构件、无效坐标生成幽灵杆件、读取接口推进项目版本、软件建议值被误确认，以及候选完整计算证据在工作区压缩后丢失。

V3.87.2 主要变更：

- 候选预览升级为 `candidate-plan-v3`，记录来源数量、渲染数量、截断状态、无效点、无效构件和转接体系完整性；
- 所有候选平面视图共用坐标清洗器，含缺失或非有限坐标的杆件不会被绘制到原点；
- 设计核心面板改为单次 bundle 读取，并通过请求序号阻止旧项目响应覆盖当前项目；
- 设计核心 GET 接口不再保存项目，避免刷新界面产生版本冲突；
- 正式参数必须具有可接受来源、来源引用、确认状态和正式设计许可；软件建议值、默认值和人工估算值保持阻断；
- 候选物理指纹纳入支撑段、转接梁完整折线及立柱位置，搜索池建立前去除完全重复拓扑；
- 工作区保留候选完整计算摘要、数值健康、结果完整度、关键阶段和根因台账，同时继续外置逐阶段及逐构件大数组；
- 设计核心样式统一进入唯一入口，弃用历史第二样式文件。

当前发行状态仍为 `engineering_preview`。前端完整 Vitest、TypeScript 工程编译和 Vite 生产构建应在可用 npm 环境中执行后再进入生产部署。

V3.77.1–V3.81 进一步完成以下业务重构：

- V3.77.1：拆分设计发行、施工准备和现场放行三套门禁，缺项同时显示责任主体、影响阶段和建议动作；
- V3.78：将含义混杂的施工阶段迁移为设计控制工况，计算引擎只消费设计责任数据；
- V3.79：自动生成基准、延迟支撑、局部超挖、预加轴力、水位、超载、刚度折减和关键支撑异常情景；已批准情景可进入隔离后台正式复算，包络只读取真实完成的计算结果；
- V3.80：施工计划绑定设计控制工况，按 A–E 分级判断是否位于设计允许域、是否需要复算或设计变更；
- V3.81：现场快照记录实际开挖、支撑、水位和监测状态，超出计划或设计允许域时生成可追溯偏差事件和阶段暂停建议。

PitGuard 的默认产品形态已经收敛为基坑围护结构核心设计工作台。主流程采用六个核心步骤：

1. 设计基准：工程等级、安全等级、规范体系、荷载组合和材料参数；
2. 工程输入：钻孔/地层、地质摘要、闭合基坑轮廓与标高；
3. 围护方案：围护墙、围檩、水平支撑、角撑和临时立柱；
4. 计算验算：施工阶段、内力变形、强度、刚度和稳定性；
5. 配筋深化：围护墙、围檩、支撑、节点和施工构造；
6. 成果交付：计算书、施工图、IFC 和审查成果包。

V3.52 将基坑验算从少量汇总值升级为 51 项设计阶段完整目录，覆盖强度、刚度、稳定性、水控制和施工性，并保留逐墙、逐工况证据。缺资料项会列明资料名称、提供阶段、责任方、设计阶段是否可提供及补齐动作。配筋深化入口统一计算合同、方案应用、构件配筋、空间深化和 P3 闭环口径，直接输出阻断原因、影响对象和处理顺序。

V3.55 将校核结果接入设计器：按项目储备目标自动执行“验算—定位控制构件—补强—复算”，达到安全边界后闭合，不能安全自动修改的地质、水位和锁定施工顺序转为可交互建议。版本同时补齐冠梁施工阶段内力与五类配筋回写，并修复水平支撑箍筋、侧面构造筋、拉结筋及搭接附加筋在问题过滤后消失的问题。

V3.65.1 以可核验的 V3.62 源码为基线恢复 V3.63–V3.65 的配筋与界面闭环：围檩抗剪计入混凝土和实际多肢箍筋贡献；短回墙通过可追溯角部传力代理生成正式围檩内力；墙筋生成、校核和下料共用锚固/搭接长度合同；1.6m×1.6m 大截面支撑同时满足总纵筋率、单侧纵筋率和 8 肢复合箍筋要求。完整计算会按需恢复外置地质面，安全目标取安全等级、企业模板与项目提高值的最大值，配筋入口可自动执行最多三轮“补强—复算—重新配筋”。

V3.66.0 完成异形基坑水平支撑 P0–P3 几何和工作流闭环：候选与轮廓采用来源哈希绑定，旧候选自动归档；L/U/T/Z/H 等正交凹形平面可生成闭合内环梁—径向支撑候选；分区图记录墙面、凹角和转接路径。

V3.71.0 继续完成异形支撑数值与交付保证：二维框架和墙—围檩体系采用对称刚度尺度化、条件数 A–E 分级、节点刚度比和病态模型自动阻断；节点位置、梁刚度和支撑刚度执行有界敏感性分析；墙—围檩—转接框架反力逐施工阶段迭代；节点三转动自由度子模型计算偏心、扭转、刚域和半刚性效应；环梁形成抗扭筋、局部承压、加腋、锚固和组合弯矩深化证据；OpenSees 自动基准覆盖二维框架/桁架和三维转动节点；真实钻孔、水位、施工资料与注册结构工程师审签通过不可变源文件、SHA-256、受信资格目录和当前拓扑哈希建立门禁。没有真实资料或真实签署时，正式发行保持阻断。

V3.72.0 对整体计算流程进行事务化和可观测性治理：计算只在隔离工作副本中修改墙、支撑、工况和工程证据，失败时完整回滚；六个主要阶段记录耗时、状态、根因和运行时日志；墙—围檩—转接框架反力迭代增加自适应松弛、振荡与停滞诊断。结果侧新增施工阶段矩阵、关键阶段排序、墙/支撑/围檩/转接梁/立柱包络、节点热点、稳定指标语义、阻断根因台账、数值健康、完整度和就绪度。稳定安全系数、风险比值和质量指数按方向分别统计，构件稳定不再误归入岩土稳定。当前运行环境缺少 OpenSeesPy 时明确报告“不可用”，同时保留结构内核版本兼容的外部证书，并执行独立 SciPy 参考模型交叉验证。


## V3.87.1 界面与异形支撑预览修复

V3.87.1 为 V3.87 的补丁版本，不改变结构计算内核。修复内容包括：设计主流程样式入口、L形/凹形方案的转接梁与闭合环梁预览、旧候选预览缓存自动升级、`ResultViewer` 重复函数声明，以及关键工程面板的局部故障隔离。

部署时必须重新执行前端生产构建，不得沿用 V3.87.0 的历史 `dist`。旧项目的 `candidate-plan-v1` 缓存会在首次读取候选预览时自动删除并从项目快照重建。

- `docs/releases/V3_87_1_UI_TOPOLOGY_RECOVERY_HOTFIX.md`
- `docs/releases/V3_87_1_RELEASE_VALIDATION.json`
- `docs/releases/V3_87_1_COMPLETE_FIX_REPORT.md`

## V3.87 设计核心

V3.87 将主流程聚焦为设计依据、工程输入、方案搜索、联合设计、计算核验、配筋深化、施工图、计算书和校审发行九个阶段。施工计划、现场快照和偏差事件保留兼容读取，退出首次设计发行门禁。

相关文档：

- `docs/releases/V3_87_0_DESIGN_CORE_CLOSURE.md`
- `docs/releases/V3_87_0_IMPLEMENTATION_AND_EVALUATION.md`
- `docs/releases/V3_87_0_UPGRADE_AND_DEPLOYMENT.md`
- `docs/releases/V3_87_0_RELEASE_VALIDATION.json`

## 启动

Windows：

```powershell
.\start-windows.ps1
```

macOS / Linux 开发环境：

```bash
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./start-dev.sh
```

启动器会使用 `services/api` 作为后端目录、`apps/web` 作为前端目录，自动检查当前 Python/Node 环境、安装缺失依赖、启动 API、计算 worker 与 Vite 前端，并进行健康检查。

停止服务：

```bash
./stop-dev.sh
```

仅执行启动前检查：

```bash
PITGUARD_PREFLIGHT_ONLY=1 ./start-dev.sh
```

诊断启动问题：

```bash
./scripts/diagnose-startup.sh
```

API 默认端口为 `8002`，前端默认端口为 `5173`。

## 核心运行原则

- 网页只读取轻量工作区投影；
- 完整计算和导出通过后台任务执行；
- 显式优化时生成最多三个具有体系、间距或立柱布置差异的候选；未满足硬约束的候选仅用于诊断，计算只作用于当前采用方案；
- 逐根钢筋、完整地质网格、计算阶段数组和导出文件存入外部对象；
- 项目主快照只保留当前设计状态和必要摘要；
- 每次修改方案后，计算和配筋状态按依赖关系失效。

## 文档

- [V3.77.1—V3.81 设计责任边界与施工现场协同闭环](docs/releases/V3_81_0_RESPONSIBILITY_WORKFLOW.md)

- [V3.72.0 事务化流程、稳定性治理与丰富结果](docs/releases/V3_72_0_WORKFLOW_STABILITY_RICH_RESULTS.md)
- [V3.71.0 数值耦合、空间节点、工程数据与审签闭环](docs/releases/V3_71_0_NUMERICAL_COUPLING_SPATIAL_ASSURANCE.md)
- [V3.70.0 异形支撑平面框架与交付闭环](docs/releases/V3_70_0_PLANAR_TRANSFER_AND_DELIVERY.md)
- [V3.66.0 异形基坑水平支撑 P0–P3 闭环](docs/releases/V3_66_0_CONCAVE_SUPPORT_P0_P3.md)
- [V3.65.1 恢复重建、配筋与界面闭环说明](docs/releases/V3_65_1_RECOVERY_BEAM_STIRRUP_UI_CLOSURE.md)
- [V3.62 完整性与稳定性发布说明](docs/releases/V3_62_0_SYSTEM_INTEGRITY_AND_STABILITY.md)
- [V3.55 智能设计闭环与完整配筋合同](docs/releases/V3_55_0_INTELLIGENT_DESIGN_CLOSURE.md)
- [V3.53 施工阶段数据与计算证据闭环](docs/releases/V3_53_0_CONSTRUCTION_STAGE_EVIDENCE_CLOSURE.md)
- [V3.52 完整验算与配筋深化闭环](docs/releases/V3_52_0_VERIFICATION_AND_DEEPENING_CLOSURE.md)
- [核心流程](docs/core/01_CORE_WORKFLOW.md)
- [V3.81 升级与数据迁移指南](docs/releases/V3_81_0_MIGRATION_GUIDE.md)
- [责任数据字典](docs/core/06_RESPONSIBILITY_DATA_DICTIONARY.md)
- [工程方法与边界](docs/core/02_ENGINEERING_METHODS_AND_BOUNDARIES.md)
- [运行时与存储](docs/core/03_RUNTIME_STORAGE_AND_DEPLOYMENT.md)
- [测试与发行](docs/core/04_TEST_RELEASE_AND_LIMITATIONS.md)
- [功能审计与扩展目录](docs/core/05_FUNCTION_AUDIT_AND_EXTENSION_CATALOG.md)
- [V3.51 自适应拓扑搜索与门禁闭合](docs/releases/V3_51_0_ADAPTIVE_TOPOLOGY_RECOVERY.md)
- [V3.50 候选视图与计算门禁恢复](docs/releases/V3_50_0_CANDIDATE_VIEW_CALCULATION_GATE.md)
- [V3.49 设计基准静默交互与台阶形支撑闭合修复](docs/releases/V3_49_0_SILENT_BASIS_TOPOLOGY_RECOVERY.md)
- [V3.48 候选与支撑配筋完整性](docs/releases/V3_48_0_CANDIDATE_REBAR_INTEGRITY.md)
- [V3.46 P0–P2 工程闭环](docs/releases/V3_46_0_P0_P2_ENGINEERING_CLOSURE.md)
- [V3.44 候选搜索内存治理](docs/releases/V3_44_0_BOUNDED_CANDIDATE_MEMORY.md)
- [V3.43 隔离计算与后台恢复](docs/releases/V3_43_0_ISOLATED_WORKER_RECOVERY.md)
- [V3.42 专业可视化与设计校核恢复](docs/releases/V3_42_0_PROFESSIONAL_VISUALIZATION_RESTORATION.md)
- [V3.41 工程可视化与多方案设计](docs/releases/V3_41_0_ENGINEERING_VISIBILITY_AND_MULTI_SCHEME.md)
- [V3.40 重构报告](docs/releases/V3_40_0_CORE_REINTEGRATION.md)
- [功能、文档与界面整合审计](docs/releases/V3_40_0_FUNCTION_DOCUMENT_AUDIT.md)

V3.0–V3.39 的迭代文档已经归档到 `docs/archive/legacy_iteration_docs_v3_0_v3_39.zip`，不再占用主文档目录。

## 核心链路自检

```bash
python scripts/evaluate-v381-responsibility-workflow.py
python scripts/evaluate-v377-accuracy-compliance-workflow.py
python scripts/evaluate-v372-workflow-stability-results.py
python scripts/evaluate-v371-numerical-coupling.py
python scripts/benchmark-v371-opensees.py
python scripts/evaluate-v370-planar-transfer.py
python scripts/evaluate-v366-concave-support.py --full-calculation
python scripts/smoke-core-workflow.py
python scripts/smoke-v353-construction-stage-evidence.py
python scripts/smoke-v352-verification-deepening-closure.py
python scripts/smoke-candidate-memory.py
python scripts/smoke-v351-adaptive-topology-recovery.py
python scripts/smoke-v350-calculation-recovery.py
python scripts/smoke-v350-full-calculation.py
```

该脚本在临时数据库中端到端执行项目创建、钻孔导入、轮廓录入、候选生成、当前方案计算和配筋，不修改现有工程。

## 内存诊断

候选搜索、方案采用、项目保存和一次性 worker 的结构化日志位于 `runtime/diagnostics`。发生内存异常时执行：

```bash
python scripts/summarize-runtime-diagnostics.py --runtime runtime
python scripts/audit-design-rebar-integrity.py --project-id <PROJECT_ID>
python scripts/repair-v349-support-bearings.py --project-id <PROJECT_ID>
```

230 m 台阶形样例的有界内存自检：

```bash
python scripts/smoke-candidate-memory.py --keep-runtime
```
- [V3.73–V3.77 深化实施与评估](docs/releases/V3_73_TO_V3_77_IMPLEMENTATION_AND_EVALUATION.md)


## V3.87 设计核心工作台

V3.87 的默认工作流按九个设计阶段组织。施工计划、现场快照和偏差事件不再进入设计完成度或首次设计发行门禁。外部资料通过“外部资料”页登记，涉及原设计边界时自动形成设计复核请求。

后端接口 `/api/projects/{projectId}/design-core` 返回九阶段状态、参数来源、方案搜索完整度、逐构件包络、配筋闭环、交付质量及生产发布资格。
