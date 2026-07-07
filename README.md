
## V2.5.0 Completion Closure

- 完成 issue 多视图定位闭环：workflow / plan / 3D / rebar / result / CAD。
- 完成企业 CAD 模板校验：图框、图层、图号、字体、线型、签审元数据。
- 完成钢筋施工详图深化代理：施工缝、钢筋笼分节、吊装、搭接区、弯折半径、保护层检查和签审清单。
- CAD 图纸包升级到 12 张 DXF + 多张 CSV/JSON 表格。
- 保持规范算法路线，未接入有限元。


## V2.4.0 重点能力

V2.4.0 在继续采用规范算法的前提下，补齐了三个工程化闭环：问题对象高亮定位、企业 CAD 模板可配置、逐根钢筋几何与下料数据。

新增接口：

- `GET /api/projects/{project_id}/cad-template`
- `PUT /api/projects/{project_id}/cad-template`
- `GET /api/projects/{project_id}/rebar/detailing` 返回 `individualBars`

CAD 图纸包新增 `S-08_individual_rebar_geometry.dxf` 和 `individual_bar_geometry.csv`，用于钢筋中心线与下料数据校审。

# PitGuard V2.2.0

V2.2.0 turns the previous delivery closed-loop prototype into a full-maturity design-assist package. The software module ledger reaches 100% for the implemented prototype workflow: data import, geological model, excavation editor, retaining system generation, candidate optimization, complete calculation comparison, calculation traceability, issue center, IFC/CAD/DOCX/JSON exports, task queue and full-delivery bundle.

Important boundary: `systemModuleCompletion=100%` describes software module coverage. `engineeringAcceptanceReadiness` remains project-specific and depends on geological data, calculation checks, quality gates and professional review.

New V2.2.0 endpoints include:

```text
GET /api/projects/{project_id}/calculation/trace
POST /api/projects/{project_id}/tasks  operation=export_trace
POST /api/projects/{project_id}/tasks  operation=export_issue_report
POST /api/projects/{project_id}/tasks  operation=full_delivery
```

## V2.0.14 候选方案多样性、施工图 IFC 可视化与钢筋状态诊断

V2.0.14 修复了候选支撑方案高度近似的核心原因：优化器临时设置的目标分仓未真正传入 `_main_support_count`。现在 3.5-6.0m 分仓会改变主支撑数量和最大分仓，A/B/C 候选优先按支撑数量、立柱数量、最大分仓、最大跨长等结构路径差异排序，线位微调只作为补充候选。前端增加“基准 / 明显差异 / 中等差异 / 高度相似”提示。

本版新增 `construction_visual.ifc`：钢筋几何采用 viewer-safe 代理构件表达，钢筋参数保留在属性集，优先解决详细施工图模型在轻量 Viewer、Revit 或 Navisworks 中不可见的问题。`design_detailed.ifc` 仍用于正式 BIM 语义审查，保留 IfcReinforcingBar。

导出接口新增：

```text
GET/POST /api/projects/{project_id}/export/ifc-construction-visual
GET/POST /api/projects/{project_id}/export/ifc-check?mode=construction_visual
```

当前钢筋状态属于参数化配筋 + 代表性钢筋组阶段；地连墙和支撑具备代表性钢筋实体/可视化代理，围檩、节点附加筋、立柱桩等主要以属性集与局部代理表达，尚未达到逐根钢筋、锚固、搭接、弯钩、保护层和编号出图的完整施工详图深度。

## V2.0.13 计算反馈、候选去重与内力包络可视化

V2.0.13 在 V2.0.12 启动链路修复基础上，继续修复计算与导出缺少过程反馈、候选支撑方案同质化、内力结果缺少直观包络图的问题。

关键变化：

- `services/api/pyproject.toml` 显式声明 setuptools 包发现规则，只打包 `app*`，排除 `exports*` 和 `tests*`，避免 editable 安装时报 `Multiple top-level packages discovered in a flat-layout`。
- `start-windows.ps1` 不再调用 `pip install -e services/api[dev]`；缺少依赖时只安装缺失的第三方包到当前 Python 环境。
- Windows 依赖诊断改为生成 `runtime/check_backend_modules.py` 后执行，避免 PowerShell 多行字符串传参造成 `python -c` 语法截断。
- Linux 脚本同步取消 editable 安装路径，只对缺失第三方依赖执行 `pip install`。
- 启动脚本继续坚持当前环境策略：不创建、不激活 `services/api/.venv`。

Windows 推荐启动：

```bat
start-windows.bat
```

如果你需要手动补依赖，使用当前 Conda/系统 Python 执行：

```powershell
python -m pip install fastapi "uvicorn[standard]" pydantic python-multipart numpy shapely python-docx openpyxl matplotlib meshio
```

开发者确实需要 editable 安装时，现在也可以执行：

```powershell
python -m pip install -e services/api --no-deps
```

## V2.0.11 当前环境启动修复与运行诊断

V2.0.11 修复 V2.0.10 一键启动脚本强制创建 `.venv` 导致后端模块不可见的问题。根目录启动脚本现在直接使用当前 shell / Conda / 系统 Python 环境，不再额外创建虚拟环境；如果当前环境缺少后端模块，脚本会把依赖安装到当前 Python 环境中，并在失败时输出明确的缺失模块和日志位置。

关键变化：

- `start-linux.sh`、`start-windows.ps1`、`start-windows.bat` 不再创建 `services/api/.venv`。
- 后端依赖清单补充 `meshio>=5.3`，解决复杂 VTU 解析路径可能出现的 `No module named meshio`。
- 新增后端诊断接口：`/api/system/diagnostics`，返回 Python 解释器、版本、数据库路径和依赖模块可用性。
- 前端顶部新增 API 重检和运行环境提示；后端离线或缺依赖时给出可执行修复路径。
- 启动脚本写入 `runtime/backend.log` 与 `runtime/frontend.log`，后端健康检查失败时自动打印最后日志。

Linux 启动：

```bash
./start-linux.sh
```

Windows 启动：

```bat
start-windows.bat
```

如果你已经确认依赖完整，不希望脚本自动安装依赖：

```bash
PITGUARD_INSTALL_DEPS=0 ./start-linux.sh
```

Windows PowerShell：

```powershell
$env:PITGUARD_INSTALL_DEPS="0"; .\start-windows.ps1
```

手动安装当前环境依赖：

```bash
python -m pip install fastapi "uvicorn[standard]" pydantic python-multipart numpy shapely python-docx openpyxl matplotlib meshio
```

## V2.0.10 功能性、人机交互与一键启动优化

V2.0.10 在 V2.0.9 候选方案差异动画、局部锁定、权重滑块、多候选完整计算和 A/B/C 计算书比选基础上，进一步补齐“操作员决策驾驶舱”和跨平台一键启动能力。

- 工作台新增“设计决策驾驶舱”，集中显示地勘资料、地质模型、基坑轮廓、围护体系、计算结果和成果闸门的完成状态，并可一键跳转到当前关键步骤。
- 集中显示局部锁定数量、候选方案数量、A/B/C 完整比选数量和出图阻断/警告数量，减少用户在多页面、多抽屉、多按钮之间反复确认。
- 根目录新增 Linux 与 Windows 一键启动脚本：`start-linux.sh`、`start-windows.bat`、`start-windows.ps1`。脚本会使用当前 Python 环境、检查并安装缺失的第三方依赖、安装前端依赖、启动 FastAPI 与 Vite，并输出访问地址。

Linux 启动：

```bash
./start-linux.sh
```

Windows 启动：

```bat
start-windows.bat
```

可选环境变量：`PITGUARD_DB_PATH` 指定数据库文件，`PITGUARD_BACKEND_PORT` 指定 API 端口，`PITGUARD_FRONTEND_PORT` 指定前端端口。

## V2.0.8 人机协同支撑候选方案优化

V2.0.8 将支撑优化推进到人机协同阶段：候选方案平面图并排对比、点击候选方案高亮线位变化、支持“采用此方案”写回项目、支持锁定指定支撑线不参与优化、支持按少立柱/低轴力/出土通道等偏好设置目标函数权重，并将候选方案评分图和平面比选图写入计算书首页。

新增后端接口：`/design/optimize-supports` 支持 `objectiveWeights` 和 `preset`；`/design/adopt-support-candidate` 用于采用指定候选方案；`/design/lock-support-lines` 用于锁定或解除锁定支撑线。

## V2.0.7 支撑线约束优化器与操作员效率界面

V2.0.7 将支撑优化从“启发式候选参数比选”推进到“支撑线位置变量约束优化”。新增硬约束、软目标、线位调整记录、候选方案导出就绪状态和前端候选方案对比视图。工作台 UI 同步增加操作员摘要面板和下一步建议提示，以减少多按钮流程下的操作干扰。

## V2.0.6 支撑目标函数优化与 IFC 分析模型版

- 支撑自动修复器升级为目标函数候选方案优化器，输出候选方案、目标函数权重和最佳方案。
- IFC 导出扩展为 coordination_light / analysis_model / design_detailed 三模式。
- analysis_model.ifc 保留构件轴线、支撑弹簧、墙体荷载代理和施工阶段信息，不导出实体钢筋。

# PitGuard BIM Designer

## V2.0.2 重点修复

- IFC 输出已将中文等非 ASCII 文本编码为 IFC STEP `\X2\...\X0\`，用于修复部分 BIM 查看器无法打开/无法可视化的问题。
- 水平主对撑自动分仓间距调整为 3-6m，默认目标 5m，避免上一版 16-18m 过稀的布置。
- 基坑轮廓未锁定绝对坐标时，保存后优先与三维地质模型 XY 中心对齐。
- 前端常用按钮保留在主流程，不常用配置移入右侧配置抽屉。
- CAD 编辑器拖动点时显示背景网格和水平/竖向参考线。


PitGuard BIM Designer 是一个面向基坑工程围护结构设计的本地优先工程软件原型。当前版本为 **V2.0.14**，目标是打通从地勘数据、三维地质模型、基坑轮廓、地下连续墙与内支撑初选、施工阶段计算、规范子集校核、IFC 导出到 DOCX 计算书输出的完整辅助设计流程。

本系统属于工程设计辅助软件原型，自动计算结果不能替代注册岩土工程师、结构工程师的专业复核、施工图签审和专家论证。

## 1. 当前能力

V2.0.14 已具备以下能力：

- FastAPI 后端服务与 React/Vite 前端原型。
- SQLite 项目持久化。
- 钻孔 CSV/XLSX 导入、地层参数合并和输入校验。
- IDW 三维地质面生成和代表性剖面提取。
- VTU 非结构网格导入、字段识别和属性映射建议。
- 基坑轮廓输入、面积/周长计算和边段生成。
- 地下连续墙、冠梁、腰梁、短向主对撑、角部斜撑和临时立柱自动初选。
- JGJ 120、GB/T 50010、GB 50007、GB 50009、GB 50017、GB 55003、GB 55008 等规范子集筛查接口。
- 土压力、水压力、支撑轴力、墙体内力和配筋建议计算。
- 立柱基础承载力子集自动扩基设计，避免固定 3m x 3m 基础导致样例流程出现可避免的 GB50007 fail。
- IFC4 STEP 导出，包含 coordination_light、analysis_model、construction_visual 和 design_detailed 四类模型；可输出地连墙、支撑、梁、立柱、代表性钢筋/钢筋可视化代理和属性集。
- DOCX 计算书导出。
- Assurance API，区分软件能力完成度和工程校核状态。

## 2. 目录结构

```text
pitguard-bim-designer/
├─ README.md
├─ CHANGELOG.md
├─ AI_CODING_SPEC.md
├─ DOCS/
│  ├─ product_requirements.md
│  ├─ calculation_method_notes.md
│  ├─ ifc_mapping.md
│  └─ ...
├─ apps/web/                 # React + Vite 前端
├─ services/api/             # FastAPI 后端
├─ packages/sample-data/     # 示例钻孔和 VTU 数据
├─ scripts/                  # 开发、测试、样例流程脚本
└─ sample-output/            # 样例工作流导出目录
```

## 3. 后端启动

推荐使用 Python 3.11 或更高版本。当前版本推荐直接使用已激活的 Conda / 系统 Python 环境。

```bash
python -m pip install fastapi "uvicorn[standard]" pydantic python-multipart numpy shapely python-docx openpyxl matplotlib meshio
cd services/api
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

期望返回：

```json
{"status":"ok","service":"pitguard-api"}
```

如果需要指定数据库文件位置：

```bash
export PITGUARD_DB_PATH=/absolute/path/to/pitguard.sqlite3
```

Windows PowerShell 可使用：

```powershell
$env:PITGUARD_DB_PATH="D:\\pitguard\\pitguard.sqlite3"
```

## 4. 前端启动

```bash
cd apps/web
npm ci
npm run dev
```

默认 Vite 地址通常为：

```text
http://127.0.0.1:5173
```

前端会调用后端 API。若前后端端口或代理配置变化，请检查 `apps/web/src/api/client.ts` 和 `vite.config.ts`。

## 5. 测试

后端测试：

```bash
cd services/api
pytest -q
```

V1.2.0 当前后端测试基线：

```text
25 passed
```

前端测试：

```bash
cd apps/web
npm ci
npm test
```

如果提示 `vitest: not found`，说明尚未执行 `npm ci` 或依赖没有安装完整。

## 6. 样例工作流

根目录运行：

```bash
python scripts/run_sample_workflow.py
```

该脚本会自动执行以下流程：

1. 创建项目。
2. 导入 `packages/sample-data/boreholes/sample_boreholes.csv`。
3. 生成三维地质模型。
4. 导入 `packages/sample-data/vtu/sample.vtu`。
5. 创建矩形基坑。
6. 自动生成地下连续墙。
7. 自动生成水平支撑和临时立柱。
8. 生成施工工况。
9. 运行计算。
10. 导出 IFC、DOCX 计算书和 JSON。
11. 输出计算检查结果。

## 7. 样例输出位置

样例脚本输出目录为：

```text
sample-output/
```

主要文件包括：

```text
sample-output/full_flow.ifc                 # IFC4 STEP 模型
sample-output/full_flow_report.docx         # DOCX 计算书
sample-output/full_flow_export.json         # 项目导出 JSON
sample-output/full_flow_project.json        # 当前项目完整 JSON
sample-output/checks.json                   # 规范子集检查结果
sample-output/run_log.json                  # 样例流程日志
sample-output/sample-workflow.sqlite3       # 样例流程数据库
```

V1.2.0 样例流程中，立柱基础会根据竖向荷载和承载力特征值自动扩大尺寸。典型结果为临时基础由 3.0m x 3.0m 扩大到约 3.5m x 3.5m，使 GB50007 承载力子集检查通过。

## 8. Assurance API

接口：

```text
GET /api/projects/{project_id}/assurance/gap-analysis
```

V1.2.0 起，Assurance 输出拆成三个核心字段：

```json
{
  "capabilityCompleteness": 100.0,
  "softwareFlowComplete": true,
  "engineeringCheckStatus": "pass",
  "closedLoopComplete": true
}
```

字段含义：

- `capabilityCompleteness`：软件功能和流程覆盖率，不代表工程安全结论。
- `softwareFlowComplete`：项目是否已完成当前版本要求的软件流程。
- `engineeringCheckStatus`：最新计算结果的工程校核状态；只要存在任一 fail，该字段必须为 `fail`。
- `closedLoopComplete`：软件流程完整且不存在硬性 fail 时为 true；manual_review 表示仍需专业复核。

## 9. 工程边界声明

当前系统实现的是规范子集筛查和初步设计辅助，不包含完整施工图设计所需的全部工程判断。正式项目使用前，应补充和复核：

- 经审查的勘察报告和设计参数采用值。
- 地方基坑规范、审图意见和专家论证要求。
- 地下水控制、承压水、降水和抗浮专项设计。
- 周边建筑、道路、管线、轨道交通等环境保护指标。
- 施工组织、换撑拆撑、出土口、坡道和临时荷载。
- 立柱桩、节点、钢筋笼、接头、防水和构造详图。

## 10. 推荐下一步

V1.2.0 修复了闭环可信度和立柱基础 fail 问题。后续应优先推进：

1. 将前端 tab 堆叠改为工程流程向导。
2. 重做基坑轮廓编辑器，实现拖拽、捕捉、缩放、撤销重做和实时校验。
3. 升级三维 Viewer，加入 OrbitControls、图层树、剖切、属性面板和 fail 构件定位。
4. 支撑体系从 bbox 规则升级为多边形拓扑设计。
5. 计算书加入剖面图、内力图、支撑轴力表和校核结论索引。

## 13. V1.3.0 前端流程重构说明

V1.3.0 将项目工作台从原型级 tab 页面重构为工程流程向导。打开项目后，左侧显示 8 个设计步骤，右侧显示当前步骤的操作区、前置条件和数据摘要：

```text
01 项目设置
02 地勘资料
03 三维地质模型
04 基坑轮廓
05 围护结构
06 计算校核
07 闭环审查
08 BIM 与计算书
```

每个步骤会根据当前项目数据自动给出状态：

```text
done      已完成
ready     可以执行
blocked   前置数据不足
warning   已完成但存在警告或人工复核项
error     存在 fail 或硬性错误
```

建议使用顺序：

1. 进入项目后先查看“项目设置”。
2. 在“地勘资料”导入钻孔 CSV/XLSX。
3. 在“三维地质模型”生成 IDW 地层面，必要时导入 VTU。
4. 在“基坑轮廓”录入或调整轮廓点并保存边段。
5. 在“围护结构”依次生成地连墙和支撑/立柱。
6. 在“计算校核”先生成工况，再运行计算。
7. 在“闭环审查”查看功能完成度、工程校核状态和出图闸门。
8. 在“BIM 与计算书”导出 IFC、DOCX 和 JSON。

前端工作台目前重点解决流程混乱和按钮堆叠问题。下一阶段应继续增强基坑轮廓 CAD-like 编辑器和三维审查 Viewer。

## 14. V1.4.0 水平支撑拓扑化布置说明

V1.4.0 将水平支撑自动设计从“外包矩形画线”升级为“基坑平面拓扑驱动”的布置算法：

1. 自动识别基坑长向和短向，主对撑沿短跨方向布置。
2. 沿长向按约 18m 目标间距分仓生成主对撑。
3. 对 L 形、凹形基坑，采用扫描线与多边形求交，避免支撑穿越坑外空区。
4. 对凸直角自动生成角撑，凹角不自动跨越。
5. 根据主对撑跨长自动生成临时立柱候选点。
6. 支撑轴力估算按支撑角色加权分配，主对撑和角撑不再获得完全相同的轴力。

详细算法见根目录 `SUPPORT_LAYOUT_ALGORITHM.md`。


## V1.5.0 支撑体系工程化设计

V1.5.0 在 V1.4.0 支撑拓扑布置基础上继续深化：

1. 自动生成支撑-围檩节点，并在计算阶段更新承压板、局部承压应力和节点附加配筋。
2. 临时立柱采用立柱桩设计，输出桩径、桩长、承载力、利用率和桩端标高。
3. 基坑轮廓可携带 `obstacles`，用于表达地下室柱网、坡道、出土口、保护区和中心岛，支撑与立柱会自动避让。
4. 大平面或中心岛场景可生成环梁和径向环撑。
5. 支撑轴力按支撑端点所在墙面的 tributary width 与土压力积分关联，不再按同层全局平均。
6. 前端围护结构页面显示支撑角色、跨长、分仓、连接墙面、tributary width、节点承压、节点配筋和立柱桩服务范围。

样例工作流输出中，`governingCheckStatus` 可能为 `warning`，原因通常是抗渗流/承压水专项提示。只要 `checkSummary.fail = 0`，说明当前软件子集没有硬性 fail；正式设计仍需专项复核。


## 16. V1.7.0 围檩本体设计、施工效应与工程审查增强

V1.7.0 在 V1.6.0 的围檩连续梁反力模型基础上，继续输出围檩本体的弯矩、剪力、挠度、正截面配筋、斜截面抗剪和节点区附加筋协调结果。计算结果通过 `WaleBeamInternalForceResult` 写入阶段结果，通过 `WaleBeamDesignResult` 写入围檩构件。

支撑体系新增预加轴力、温度约束、节点间隙闭合和施工偏心/偏差影响。支撑设计轴力不再只等于围檩节点反力包络，而是记录 `effectiveAxialForceStandard`、`preload`、`thermalAxialForce`、`gapClosureForce` 和 `eccentricityMoment`，并在 IFC 与前端表格中展示。

CAD-like 编辑器新增 DXF 导入、尺寸标注、坐标命令行、选中边偏移、多段线闭合/断开、图层管理、坡道/出土口/中心岛障碍物绘制。三维 Viewer 新增支撑轴力云图、节点颜色、剖切轴和剖切滑块、测距、中文属性表，以及最大轴力支撑和最大弯矩墙段定位入口。

详见 `WALE_BEAM_DESIGN_AND_UI_V1_7.md`。

## 15. V1.6.0 围檩连续梁与 CAD-like 编辑器

V1.6.0 将水平支撑轴力计算从墙面 tributary width 估算升级为“围檩连续梁—弹性支撑节点反力”模型。计算流程为：墙面压力带积分得到线荷载，围檩按一维连续梁离散，支撑端部按 EA/L 和法向投影作为弹性支座，求解节点反力并换算为支撑轴力。

基坑轮廓编辑器同步升级为 CAD-like 交互组件，支持拖拽点、插点、删除点、撤销/重做、滚轮缩放、画布平移、网格吸附、正交约束和前端实时几何校验。

详见 `WALE_BEAM_AND_CAD_EDITOR_V1_6.md`。

## 17. V2.0.0 P0-P5 综合工程化迭代

V2.0.0 在 V1.7.0 基础上完成 P0-P5 的整体推进：

- **P0 围檩工程化**：新增 `WaleBeamEnvelopeResult`，输出围檩多工况 M+、M-、|V|、|δ| 包络；`WaleBeamDesignResult` 增加挠度限值、截面优化历史、承压扩散尺寸和围檩-地连墙连接说明。
- **P1 支撑生命周期**：支撑对象记录预加轴力阶段、拆撑阶段和生命周期说明，新增 `JGJ120-SUPPORT-LIFECYCLE-PATH-SUBSET` 检查。
- **P2 CAD 工程图层**：前端基坑编辑器支持 DXF 导出、支护轴线偏距、地下室外墙线偏距、工程图层保存，以及 `AXIS_OFFSET`、`BASEMENT_OFFSET`、`CLOSE`、`OPEN` 命令。
- **P3 三维审查**：三维 Viewer 增加 fail/warning/manual_review 审查定位按钮，可直接查看规则、对象和说明。
- **P4 计算书图表数据**：计算结果输出 `reportDiagramData`，DOCX 增加 V2.0 摘要、墙-围檩-支撑耦合摘要和围檩包络表。
- **P5 计算扩展接口**：阶段结果新增 `coupledSystemResult`，集中记录墙体、围檩和支撑之间的耦合摘要，为后续全局联立矩阵求解预留接口。

详见 `P0_P5_ENGINEERING_ITERATION_V1_8.md`。

## 18. V2.0.0 全局联立刚度与专项复核

V2.0.0 在 V1.8.0 的基础上继续推进四类生产化能力。

### 18.1 墙-围檩-支撑全局联立刚度模型

后端新增 `services/api/app/calculation/global_coupled.py`。每个施工阶段和基坑边段建立一个全局矩阵，统一表达：

- 墙体节点水平位移；
- 围檩节点水平位移；
- 支撑杆轴向弹簧刚度；
- 立柱竖向支承代理；
- 施工阶段支撑激活/失活。

结果写入：

```text
data.calculationResults[-1].stageResults[*].globalCoupledResult
```

### 18.2 计算书图表化

后端新增 `services/api/app/reports/charts.py`，DOCX 计算书会自动插入：

- 墙体土压力图；
- 墙体位移图；
- 墙体弯矩图；
- 墙体剪力图；
- 围檩弯矩包络图；
- 围檩剪力包络图；
- 支撑轴力柱状图；
- 校核结果汇总图。

图表默认生成在：

```text
services/api/exports/report-charts/
```

### 18.3 CAD 几何内核升级

前端 `ExcavationEditor` 已加入：

- 精确闭合多段线 offset；
- 支护轴线 / 地下室外墙线偏移；
- `FILLET`、`CHAMFER`、`REPAIR` 命令；
- 倒角、圆角、自交修复；
- DXF 导入/导出和工程图层。

### 18.4 地下水与稳定性专项

新增规则：

```text
JGJ120-2012-DEWATERING-STAGE-SUBSET
JGJ120-2012-LAYERED-SEEPAGE-GRADIENT-SUBSET
JGJ120-2012-WEAK-UNDERLYING-LAYER-SUBSET
```

并继续保留承压水突涌、抗隆起、抗渗流和整体稳定圆弧搜索。

### 18.5 强度、刚度、稳定性复核汇总

`CalculationResult.designReviewSummary` 会按类别输出：

- `strengthStatus`；
- `stiffnessStatus`；
- `stabilityStatus`；
- fail / warning 统计；
- 强度利用率、刚度利用率、最小稳定安全系数。


## 19. V2.0.0 空间杆系内核、稳定专项和施工图表达

V2.0.0 在 V1.9.0 基础上完成三类深度推进：

1. **空间杆系—墙体耦合内核**：全局矩阵新增墙体梁转角自由度、围檩梁转角自由度、支撑轴向变形自由度、支撑空间方向刚度、立柱竖向自由度、支撑节点刚域和地下室楼板换撑刚度。计算结果输出 `spatialMatrixSize`、`spatialDofSummary`、`wallRotationProfile`、`waleNodeProfile`、`supportAxialDofs`、`columnVerticalDofs` 和 `rigidNodeZones`。
2. **可审查地下水与稳定专项包**：新增 `stabilityDetailedResult`，包括控制剖面、圆弧滑动候选、渗流路径、降水过程、降水井建议、承压水减压井建议和坑底加固/嵌固优化方案。
3. **施工图和 IFC 深化接口**：新增 `drawingSheets`，自动生成支撑平面布置、支撑—围檩节点、地下连续墙钢筋笼和临时立柱桩 SVG 图纸；IFC 增加立柱桩代理、承压板、预埋件/锚固件代理和 V2.0 属性集。

运行样例后主要输出包括：

```text
sample-output/full_flow.ifc
sample-output/full_flow_report.docx
sample-output/full_flow_export.json
exports/detail-sheets/D-01_support_plan.svg
exports/detail-sheets/D-02_wale_node_detail.svg
exports/detail-sheets/D-03_wall_rebar_cage.svg
exports/detail-sheets/D-04_column_pile_detail.svg
services/api/exports/report-charts/*.png
```

## NPM 安装修复说明

如果前端执行 `npm ci` 时访问 `packages.applied-caas-gateway1.internal.api.openai.org` 并出现 `ETIMEDOUT`，说明旧版 `package-lock.json` 中保留了内部 npm 代理地址。V2.0.1 已将 `apps/web/package-lock.json` 的 `resolved` 地址改为 `https://registry.npmjs.org/`，并新增 `apps/web/.npmrc`。

在 `apps/web` 目录执行：

```bash
npm cache clean --force
npm config set registry https://registry.npmjs.org/
npm ci --registry=https://registry.npmjs.org/
npm test -- --run
npm run build
```

如果出现 `vitest 不是内部或外部命令` 或 `tsc 不是内部或外部命令`，原因是 `npm ci` 没有成功完成，`node_modules/.bin` 尚未安装。

## 21. V2.0.3 质量闸门、IFC 自检和计算书正式化检查

V2.0.3 新增三个正式成果质量门控模块：

1. `supportLayoutQuality`：检查主对撑 3-6m 分仓间距、支撑跨长、角撑、立柱服务范围、障碍物/出土口避让和换撑路径。
2. `ifcCompatibility`：导出前/导出后检查 raw unicode、未定义引用、零尺寸几何、placement、材料关联和空间归属。
3. `formalReportGate`：把 fail/warning/manual_review、支撑布置异常、稳定性专项缺项、IFC 风险统一放到计算书首页。

新增接口：

```bash
curl -X POST http://127.0.0.1:8000/api/projects/<project_id>/export/ifc-check
```

运行样例后会生成：

```text
sample-output/full_flow_ifc_check.json
services/api/exports/<project_id>_engineering.ifc_check.json
```

闭环字段说明：

- `capabilityCompleteness`：功能路径覆盖率。
- `softwareFlowComplete`：软件流程是否完整。
- `engineeringCheckStatus`：工程校核状态；存在 fail 必须为 fail。
- `officialIssueGateStatus`：正式出图质量闸门。
- `closedLoopComplete`：软件流程完整且无硬性 fail。它不等于正式出图许可。

如果界面显示 `功能完成度 92.3%`，常见原因是缺少一个流程项，例如未导入 VTU。点击闭环审查页中的“软件流程缺项”可查看具体缺失内容。

## V2.0.4 支撑高亮、IFC Viewer 分级与计算书首页清单

V2.0.4 在 V2.0.3 质量闸门基础上增加四项改进：

1. 支撑布置评分前端可视化：围护结构页面新增平面高亮图，红色显示交叉、严重跨长或分仓超限支撑，橙色显示警告支撑，障碍区以半透明面表达；三维视图同步按质量问题高亮支撑。
2. IFC Viewer 兼容性分级：IFC 自检结果增加 `viewerProfiles`，按 BlenderBIM/Bonsai、BIMVision、Solibri、Autodesk Revit、Navisworks 给出启发式风险评分、风险项和建议。
3. 导出页模型预览：在 IFC、DOCX、JSON 下载入口下方增加模型可视化框，方便下载前检查支撑、节点、立柱和质量高亮。
4. 计算书首页审查清单模板化：DOCX 首页新增“审图式首页清单”，集中列出阻断项、警告项、缺项、人工复核项和 pass 项。

运行方式不变：

```bash
cd services/api
python -m pytest tests/test_mvp.py -q -k "v2_0_4 or v2_0_3 or v2_0_2"
```

前端仍按：

```bash
cd apps/web
npm ci --registry=https://registry.npmjs.org/
npm run dev
```

## V2.0.5 支撑自动修复、IFC 双模式与支撑评分出图

V2.0.5 增加三个工程交付能力：

1. 支撑布置自动修复器：计算前自动重新分仓、移动支撑线、规避无节点交叉、重建立柱服务范围和支撑节点。
2. IFC 双模式导出：
   - `coordination_light.ifc`：轻量协调版，适合 Revit/Navisworks 协调浏览；
   - `design_detailed.ifc`：施工图详细版，保留钢筋、承压板、预埋件和节点构造。
3. 支撑评分与计算书联动：自动生成 `support_layout_quality_plan.png`，并插入 DOCX 计算书首页。

接口示例：

```bash
curl -X POST http://127.0.0.1:8000/api/projects/<project_id>/design/auto-repair-supports
curl -X POST http://127.0.0.1:8000/api/projects/<project_id>/export/ifc-light -o coordination_light.ifc
curl -X POST http://127.0.0.1:8000/api/projects/<project_id>/export/ifc-detailed -o design_detailed.ifc
curl -X POST 'http://127.0.0.1:8000/api/projects/<project_id>/export/ifc-check?mode=coordination_light'
```


### V2.0.15 钢筋级 IFC 可视化与 CAD 出图

Step 8「BIM 与计算书」新增钢筋级 IFC 可视化模块，可直接在浏览器中查看地连墙、围檩/冠梁、水平支撑和节点附加筋的采样钢筋段。模块对应后端接口：

```text
GET /api/projects/{project_id}/export/ifc-rebar-visualization
```

施工 CAD 图纸包可通过以下接口或前端导出卡片下载：

```text
GET /api/projects/{project_id}/export/drawings-cad
GET /api/projects/{project_id}/export/drawings-svg
```

CAD 包采用 R12 DXF + 钢筋表 CSV，面向 AutoCAD、中望 CAD、浩辰 CAD 等继续深化。当前为设计辅助级施工图交换成果，正式施工图仍需补充锚固、搭接、弯钩、保护层、图框签审和下料表。


## V2.2.0 - 交付闭环、任务队列与问题清单中心

- 新增后端任务队列：计算、候选比选、IFC、CAD、SVG、DOCX、JSON 和完整交付包都可后台执行。
- 新增 `/api/projects/{project_id}/issues` 问题清单中心，动态评估完成度、阻断项、警告项和下一步动作。
- Step 6 一键计算校核改为任务化执行，前端显示真实进度、当前步骤和任务日志。
- Step 8 导出操作改为任务化生成并从任务结果下载。
- 新增完整交付包入口，串联计算、IFC、CAD、SVG 和 DOCX。
- 版本更新为 2.2.0。

## V2.4.0 normative benchmark and detailing workflow

V2.4.0 keeps the calculation workflow on normative / rule-based algorithms and adds a public-paper-derived benchmark library.

Key endpoints:

```text
GET  /api/benchmarks
POST /api/benchmarks/run?caseId=<case-id>&persist=false
GET  /api/benchmarks/export-package
GET  /api/projects/{project_id}/rebar/detailing
GET  /api/projects/{project_id}/issues
GET  /api/projects/{project_id}/calculation/trace
```

The benchmark export package contains project JSON, issue report, calculation trace, CAD/SVG drawings, DOCX report and construction-visual IFC for each benchmark. These cases are regression cases derived from public case-study metadata. Original project drawings and site-specific reports are not bundled.
