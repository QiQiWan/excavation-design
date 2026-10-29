## V3.87.26 新增统一验收与声明式阶段校审

新增 `assurance/quality_protocol.py`、`dossier.py`、`source_bytes.py` 与 `real_project/complete_curve_protocol.py`。`hydraulic.py` 新增分场景水头与远场边界敏感性；`repository.py` 增加逐阶段不可变哈希记录；`AssuranceGatePanel.tsx` 在原工作台提供 P1-P4 审核入口。所有证据声明在实际来源字节和专业方法验证之前均不表示正式合格。详见 `STANDARDIZATION_AND_ACCEPTANCE_V3_87_26.md`。

# PitGuard V3.87.23 - 模块目录和交付说明

## 入口与启动

- `start-windows.bat` / `start-windows.ps1`：Windows 启动；`start-linux.sh`：Linux 启动。
- `services/api/app/main.py`：FastAPI 入口、鉴权、公共路由组装。
- `apps/web/src/`：工程师工作台；`apps/web/src/features/single-strut/`：单排桩一道支撑的独立前端。
- `scripts/build-production.sh`、`scripts/run-worker-supervisor.py`：部署和任务工作进程。

## 后端业务模块及文件职责

| 路径 | 实际作用 |
| --- | --- |
| `services/api/app/engineering/single_braced_pile/` | **新增纵切模块**：单排非咬合圆桩＋一道无预力内支撑 |
| `…/contracts.py` | 强类型输入、单位与施工事件合同；缺参数直接报错 |
| `…/preflight.py` | 勘察来源、适用范围、缺项、地方标准风险识别 |
| `…/pressure.py` | 土压力、有效应力、土反力宽度公式 |
| `…/analysis.py` | 施工阶段离散模型、结构求解、控制包络、超限诊断 |
| `…/repository.py` | 不可变输入/结果/校审哈希存储，阻止覆盖与篡改 |
| `…/router.py` | 输入审查、计算、结果读取、校审及成果 API |
| `…/artifacts.py` | DOCX、A3 PDF、DXF、包络 PDF、配筋 CSV 同源导出 |
| `…/assets/verification_example.json` | **小型内置演示模板**，必须标明数值验证，不属于工程勘察 |
| `services/api/app/section/` | 既有可验证梁土弹簧求解核心及历史阶段方法；新增模块调用其 `solver.py` |
| `services/api/app/section/norms/` | JGJ120 单项公式计算工具和规范参数候选 |
| `services/api/app/geometry/` / `geology/` | 工程几何、地层与水文地质数据 |
| `services/api/app/calculation/` | 其他结构作用及主系统计算 |
| `services/api/app/drawings/` / `reports/` | 全项目原有 CAD、计算书、成果生成 |
| `services/api/app/compliance/` / `rules/` | 规范、适用性与工程条件检查 |
| `services/api/app/models/` / `storage/` | 数据模型、持久化与主项目版本 |
| `services/api/app/tasks/` / `services/` | 任务调度和通用后台服务 |
| `services/api/app/routers/` | 主系统 HTTP 路由；后续逐渐归入对应业务模块 |
| `apps/web/src/features/single-strut/` | 前端输入、来源、阶段结果、人工复核、成果下载 |

该版本采取**新功能纵向封装、历史功能保持导入路径稳定**的增量重构办法。
既有 `routers` / `services` 下还有部分跨域模块，未进行大规模迁移；后续应按依赖统计、接口测试逐批收敛，避免盲目挪文件导致历史设计链中断。

## 一条工程师可操作的最小流程

1. 在计算工作台打开“单排桩·一道内支撑”；录入单排桩几何、土层天然/饱和重度、c′、φ′、m、水位、工况以及给定钢筋。
2. 在“逐参数依据与确认”处填写勘察报告/材料证明的文档章节、单位及确认人；也可导入结构化 JSON。演示输入仅用于数值冒烟测试。
3. “检查缺项”：几何和工况无效直接阻断；项目来源待确认会单列，允许受限计算但无法称为工程输入已齐全。
4. “冻结并计算”：逐阶段求解，检查零预力安装、最终包络、位移限值及土反力合力，保留每个控制点来源。异常情况下不得生成虚假的钢筋需求。
5. 查看“超限与未覆盖”及候选修改；系统只给变更建议，不擅自加深桩长/更改钢筋。修改后生成新快照和新结果。
6. 具有 **checker** 权限的校审人员逐类确认遗留项和工作版限制后，才能导出 ZIP；不会变成正式施工发行状态。
7. 同一已封存结果生成 `calculation.docx`、`section_A3.pdf`、`section.dxf`、`stage_envelopes.pdf`、`rebar_demand.csv` 和 `manifest.json`。

## 范围及严格限制

- 已实施 JGJ120-2012 第 3.4、4.1 节中适用本剖面的主动土压力、m 法坑内反力、圆桩反力宽度和嵌固段土反力合力审查。
- 数值阶段求解采用既有 Euler–Bernoulli 梁/独立土弹簧模块。
- **尚未**完成 GB55003-2021 要求的全部极限状态；整体稳定、抗隆起、抗倾覆、渗流、完整荷载组合及支撑杆件校验仍是正式发行硬阻断项。
- 钢筋只提供带来源的设计效应和几何需求信息，尚未给出轴弯抗剪裂缝、锚固构造的合格证明。
- 当前单排非咬合圆桩、一道无预力弹性支撑、水平地面、有效应力分算、无渗流；不得把近似模型结论推广到双排桩或其他支护体系。
- **用户输入的位移限值、m、EI 折减与设计作用系数均需项目依据核定**。

## 轻量运行包组成与排除项目

运行包只保留 API 源码及其真实 JSON 规则、Web 源码、锁文件、启动/构建/必要运维脚本、必要的规范/图层配置与本文件。
排除 `tests/`、大体量 `examples/`、案例数据、历史验证报告、历史备份、缓存、数据库、临时产物、编译文件、字体二进制、`node_modules/` 和旧的 `dist/`。
运行时安装 Python、Node/npm 的实际依赖后再启动；包内不包含前端编译结果和任何第三方字体文件。

## 验证与上线前检查

- 已跑定向后端计算/合同/复核/导出及历史规范、阶段求解相关测试；完整数量见交付验证清单。
- 校审权限依赖主应用 RBAC 的 checker 或 admin 角色；本地无登录配置时按历史策略进入 local-development admin，**生产环境必须配置会话和用户**。
- 稳定性与工程设计范围暂不满足正式工程设计交付，图册标记“不得用于施工”。
- 运行包须在目标部署机器执行 `scripts/verify-release-consistency.py`、安装依赖和生产构建，并复查系统原有流程。

## V3.87.22 规范专项目录

`app/engineering/single_braced_pile/normative/` 已分为 `stability.py`（4.2.2/4.2.4/4.2.5适用性/4.2.7）、`groundwater.py`（附录C）、`actions.py`（标准与设计效应、不利工况重算）、`support.py`（单桩反力→真实杆件及节点）、`reinforcement.py`（复用圆截面承载力与构造工具）、`reference_review.py`（原项目资料与内在算术审查）。入口 `engineering_checks.py` 仅承担原路径的编排门面，不能重复实现公式。迁移计划见独立 `PitGuard_3.87.22_Modularization_Plan.md`。

新增规范方法仅在自身说明的几何、水土、材料和工况范围内允许给出局部分项结果。软土圆弧、实际支撑体系、完整荷载组合和圆截面裂缝均保留正式发行阻断。

## V3.87.23 实际目录职责与不跨越边界

- `single_braced_pile/normative/soft_soil_slip.py`: JGJ 120 第 4.2.5 条土条法，独立计算与证据返回；不得调用前端/成果引擎。
- `single_braced_pile/normative/seepage_fd.py`: 非均质分层二维稳态渗流数值调查；返回水量守恒，严格保留筛查结论。
- `single_braced_pile/normative/groundwater.py`: 附录 C 均质公式及数值专项方法选择。
- `single_braced_pile/normative/actions.py`: 明确组合系数、各作用分解、单独求解和阶段控制来源。
- `single_braced_pile/normative/support.py`: ULS 反力传递、支撑杆件和对端受力；缺三维平面节点时不能宣称完成。
- `single_braced_pile/normative/reinforcement.py`: 阶段 N-M-V 同源检查，并复用 `app/section/engineering_rebar.py` 材料积分和构造接口。
- `single_braced_pile/real_project/luoyu_three_three.py`: 研究用历史资料条件化算例；不进入工程正式计算主流程。
- `router.py` 中新增 `GET /api/engineering/one-strut/research/luoyu-3-3`，所有数据明确 `sameConditionsAsOriginal=false`。

### 下一轮历史巨型模块拆分路线

1. 为 `services/support_layout.py`、`calculation/engine.py`、`tasks/manager.py`、`drawings/cad_export.py` 建立行为基线和 API 契约。
2. 按设计域抽出不可变输入、稳定验算、内力与配筋、成果几何、图纸模板、工作任务；只在旧入口保留轻量兼容转发。
3. 对每次移动按接口和工程案例回归，并对比冻结结果哈希与 PDF/DXF 图元；不可一次性删除旧服务。
4. 严禁任何验算代码直接写入 CAD 或任务数据库。成果只消费冻结快照，任务只管调度。

### 工程校验边界

单项数值公式和五阶段合成研究曲线并不等于完整的专项设计验证。有效应力孔压沿弧分布、圆桩完整裂缝宽度、杆件温差和节点三维传力、原项目全条件数值数组仍须补齐。


# PitGuard V3.87.24 - 新增工程研究接口与核验范围

## 新增/修改目录

- `services/api/app/engineering/single_braced_pile/normative/hydraulic_assurance.py`：非均质渗流的双网格、渗流出逸和水量守恒证据；不执行正式异质地层合格认定。
- `.../normative/crack_scope.py`：圆截面在已确认开裂等级与SLS组合下的未开裂拉应力核算。III类宽度保留未计算。
- `.../normative/groundwater.py`：复用既有附录C均质核验；非均质另返回 assuranceStatus；既有status合同不变。
- `.../normative/reinforcement.py`：带有轴压的零轴力保守抗剪下界参考，保留范围外标记。
- `.../real_project/terrain_pressure.py`：坡台折线填土竖向附加应力高斯积分，远侧无限平台解析积分。不是规范坡面主动楔体方法。
- `.../real_project/bracing_compatibility.py`：对侧墙弹簧/支撑EA-L的独立双端相容性核算，未建完整三维支撑体系。
- `.../real_project/boundary_evidence.py`：原项目证据与完整曲线的严格协议、非有限和重复节点拒绝；未取得原生数值数据时不计算RMSE。
- `.../real_project/luoyu_three_three.py`：保留显式桩端、冠梁映射、分层饱和重度假定；五阶段条件化研究模型。
- `apps/web/src/features/single-strut/OneStrutWorkbench.tsx`：裂缝等级与SLS轴力动作编辑、原项目边界记录入口。

## 本版审查门禁

- 非均质稳态渗流对比与保守筛查可计算；正式规范合格状态 **禁止** 由筛查升级。
- 圆形桩I/II类裂缝仅在规范允许的组合及适用前提下进行未开裂截面拉应力校核；III类宽度不自动填值。
- 带轴压的抗剪仅能给出 N=0 保守下界筛查，不能证明圆桩轴剪承载力满足规范。
- 螺屿村坡台侧压力采用弹性竖向附加应力投影，严格分层强度口径；尚需规范可比的土压力计算模型。
- 冠梁由 22.7 MN/m² 映射单桩刚度需确认；桩底约束和两端墙位移/刚度不明，不得宣称同条件复现。
- 原项目 PDF 含图像与峰值，未给原生节点数组，完全阶段曲线比较保留阻断。
- 运行包只包含前后端源码、配置、工具和必要静态资产，研究验证数据独立打包。

## 接口边界

- GET `/api/engineering/one-strut/research/luoyu-3-3/boundary-register`
- POST `/api/engineering/one-strut/research/luoyu-3-3/compare-curves`
- 前者返回原报告证据、待确认参数、来源页码；后者要求逐点数字数组的单位、桩宽口径、符号和坐标基准一致，无法一致时拒绝RMSE。

## 运行说明

```bash
cd services/api
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

运行环境必须安装 `services/api/pyproject.toml` 指定的依赖；本次不包含数据缓存、用户项目数据库、测试资料及字体文件。


## V3.87.25 统一证据审查模块

`services/api/app/engineering/single_braced_pile/assurance/` 负责严格证据登记、量纲/参数匹配、现场水文与数值双算法复核、圆桩裂缝及构造准入、螺屿村 3-3 原始曲线准入、统一只读审查；原有 `normative/` 保留数值公式，`real_project/` 保留研究模型，`repository.py` 仍负责不可变封存。

该目录不得反向导入到 `normative/` 或 `real_project/`，审查结果不得修改冻结设计输入。GET `/api/engineering/one-strut/assurance/catalog` 可查询实现状态和适用边界，`/schemas` 可读取严格数据合同。数值独立复核可选 SciPy，缺少 SciPy 时明示 `dependency_unavailable`，不宣布通过。原项目没有逐点数据时禁止进行误差统计。

部署应保留 `assurance/assets/method_catalog.json` 和 `packages/units/engineering-units.json`；不在运行包内捆绑测试、用户工程、原勘察资料和旧结果。详见 `STANDARDIZATION_AND_ACCEPTANCE_V3_87_25.md`。
