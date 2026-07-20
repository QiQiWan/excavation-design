# PitGuard V3.60.0

PitGuard 的默认产品形态已经收敛为基坑围护结构核心设计工作台。V3.60 默认只向工程师显示三个工作阶段：

1. 快速方案：确认四项设计意图，录入闭合轮廓和最终开挖深度，生成 A/B/C 方案；
2. 计算与优化：只补地勘、水位等高影响资料，自动生成施工阶段并执行校核—补强—复算；
3. 配筋与交付：基于有效内力包络完成配筋、节点深化、校审和成果输出。

六个技术步骤和完整参数仍保留在“专业流程”中，用于审计、专业覆盖和人工改写；缺少后续专项资料不再错误阻断前面的快速方案。

V3.52 将基坑验算从少量汇总值升级为 51 项设计阶段完整目录，覆盖强度、刚度、稳定性、水控制和施工性，并保留逐墙、逐工况证据。缺资料项会列明资料名称、提供阶段、责任方、设计阶段是否可提供及补齐动作。配筋深化入口统一计算合同、方案应用、构件配筋、空间深化和 P3 闭环口径，直接输出阻断原因、影响对象和处理顺序。

V3.55 将校核结果接入设计器：按项目储备目标自动执行“验算—定位控制构件—补强—复算”，达到安全边界后闭合，不能安全自动修改的地质、水位和锁定施工顺序转为可交互建议。版本同时补齐冠梁施工阶段内力与五类配筋回写，并修复水平支撑箍筋、侧面构造筋、拉结筋及搭接附加筋在问题过滤后消失的问题。

V3.59 将设计入口改为“最小设计任务书”：工程师只确认成果目标、周边保护要求、设计取向和结构使用阶段，系统带来源地推荐规范组合、首轮材料、分析模型与施工阶段。轮廓和最终开挖深度齐全即可先生成方案；地勘只在正式计算前拦截；承载力与专项资料只在相关验算或正式出图前要求。

V3.60 修复配筋入口的循环阻断：当前计算有效时不再把地质建模错误置为首要动作；配筋任务会自动识别只有几何、没有设计结果的冠梁/围檩。围檩没有直接节点反力时，系统仅使用同一道支撑已计算梁的最不利施工阶段包络进行保守补齐，并保留专业复核标识；随后写入墙、梁、支撑五类钢筋，必要时自动复算。P3 入口以“为什么—怎么处理—影响对象—一键修复”展示。

## 启动

Windows：

```powershell
.\start-windows.ps1
```

Linux：

```bash
./start-linux-dev.sh
```

API 默认端口为 `8002`，前端默认端口为 `5173`。

## 核心运行原则

- 网页只读取轻量工作区投影；
- 完整计算和导出通过后台任务执行；
- 默认生成具有体系、间距或立柱布置差异的三个候选，只自动计算当前采用方案；
- 逐根钢筋、完整地质网格、计算阶段数组和导出文件存入外部对象；
- 项目主快照只保留当前设计状态和必要摘要；
- 每次修改方案后，计算和配筋状态按依赖关系失效。

## 文档

- [V3.60 配筋入口自愈与梁设计证据补齐](docs/releases/V3_60_0_REBAR_ENTRY_SELF_HEALING.md)
- [V3.59 最小设计任务书与渐进式资料门禁](docs/releases/V3_59_0_INTENT_DRIVEN_DESIGN.md)
- [V3.55 智能设计闭环与完整配筋合同](docs/releases/V3_55_0_INTELLIGENT_DESIGN_CLOSURE.md)
- [V3.53 施工阶段数据与计算证据闭环](docs/releases/V3_53_0_CONSTRUCTION_STAGE_EVIDENCE_CLOSURE.md)
- [V3.52 完整验算与配筋深化闭环](docs/releases/V3_52_0_VERIFICATION_AND_DEEPENING_CLOSURE.md)
- [核心流程](docs/core/01_CORE_WORKFLOW.md)
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
