# PitGuard V2.1.0 交付闭环优化说明

## 本版目标

V2.1.0 将系统从“功能串联原型”推进到“工程交付闭环原型”。本版重点不是继续堆叠零散按钮，而是补齐长耗时任务、问题清单、完成度评估和成果生成日志，使用户能够理解系统当前卡点、后台正在做什么、成果是否可用于正式交付。

## 关键新增能力

### 1. 后端任务队列

新增统一任务系统：

- `POST /api/projects/{project_id}/tasks`
- `GET /api/projects/{project_id}/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/cancel`
- `GET /api/tasks/{task_id}/download`

支持任务：

- `calculation_full`：一键计算校核；
- `candidate_comparison`：候选方案 A/B/C 完整比选；
- `export_ifc_light`；
- `export_ifc_analysis`；
- `export_ifc_construction_visual`；
- `export_ifc_detailed`；
- `export_drawings_cad`；
- `export_drawings_svg`；
- `export_report`；
- `export_json`；
- `full_delivery`：完整交付包任务。

任务返回真实状态、进度、当前步骤、日志、错误和可下载结果文件。

### 2. 问题清单中心

新增：

- `GET /api/projects/{project_id}/issues`

该接口汇总数据缺项、地质模型问题、基坑轮廓问题、围护体系问题、计算规范筛查问题、IFC 兼容性问题、正式出图闸门问题、钢筋深化边界和候选方案差异性问题。

前端 Step 7 “闭环审查”新增“问题清单中心”，显示：

- 总体完成度；
- 数据建模完成度；
- 设计计算完成度；
- BIM/CAD 交付完成度；
- 交互闭环完成度；
- 正式出图准备度；
- 阻断项、警告项、人工复核项；
- 优先处理动作；
- 当前系统边界。

### 3. 一键计算与导出任务化

Step 6 “一键计算校核”改为后端任务队列执行，前端轮询任务状态，显示真实进度和任务日志。

Step 8 导出 IFC、CAD、SVG、DOCX、JSON 时，也优先走后端任务队列。生成成功后从 `/api/tasks/{task_id}/download` 下载成果文件。

### 4. 完整交付包入口

Step 8 新增“一键生成完整交付包”，按顺序执行：

1. 完整计算；
2. 施工图可视化 IFC；
3. CAD 图纸包；
4. SVG 图纸包；
5. DOCX 计算书。

该能力用于演示完整交付闭环，后续可升级为 ZIP 总包。

## 完成度评估

当前 V2.1.0 的系统完成度评估以项目实际数据为准，由 `/api/projects/{project_id}/issues` 动态返回。一般状态下：

- 软件流程闭环：已基本完成；
- 后台任务反馈：已完成原型级闭环；
- BIM/CAD 交付：已达到可校审交换级；
- 钢筋 IFC：已达到参数化钢筋组可视化级；
- 施工图 CAD：已达到 DXF/SVG 交换图纸包级；
- 正式施工图：仍需图框、尺寸链、详图索引、钢筋大样、材料表、签审栏和审图规则深化；
- 计算可信度：仍需工程师复核规范适用性、参数来源、施工阶段和公式条件。

## 后续优化方向

下一阶段建议优先推进：

1. 候选方案族和完整模型快照，使 A/B/C 真正成为工程方案族比选；
2. 计算链追溯，从最大值结果升级为“工况—构件—截面—公式—规范条文”可追溯；
3. 正式 CAD 图纸集，包括图框、图号、比例、剖面索引、节点详图和钢筋料表；
4. 钢筋逐根几何和下料表，包括保护层、锚固、搭接、弯钩、弯折段；
5. IFC 兼容性矩阵，针对 Revit、Navisworks、Bonsai、Solibri、BIMVision 输出不同模型策略；
6. 任务系统持久化和并行调度，当前任务队列为进程内原型，服务重启后任务记录会丢失。
