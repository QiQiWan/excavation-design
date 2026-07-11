# V2.0.6 支撑目标函数优化与 IFC 分析模型版

## 1. 支撑布置目标函数优化器

本版本将支撑自动修复器从单一规则修复升级为小规模启发式候选方案优化。系统自动枚举不同的主对撑目标分仓和临时立柱服务跨，并对每个候选方案进行质量评分。

### 1.1 候选方案变量

- 主对撑目标分仓：3.5m、4.0m、4.5m、5.0m、5.5m、6.0m。
- 临时立柱最大服务跨：12m、15m、18m、21m。

### 1.2 目标函数

目标函数包含以下项：

- 支撑间距偏差最小。
- 支撑跨长最小。
- 障碍冲突为 0。
- 支撑无节点交叉为 0。
- 立柱数量不过多。
- 出土路径连续性。
- 支撑轴力峰值代理项不过大。
- 支撑体系平面对称性较好。

优化结果写入：

- `CalculationResult.supportLayoutRepair.candidates`
- `CalculationResult.supportLayoutRepair.objectiveWeights`
- `CalculationResult.reportDiagramData.supportLayoutRepair`
- `RetainingSystem.layoutSummary.supportOptimizationCandidates`

## 2. 新接口

```text
POST /api/projects/{project_id}/design/optimize-supports
```

该接口返回 `SupportLayoutRepairSummary`，并将最佳候选方案写回项目的 `retainingSystem`。

## 3. IFC 三模式导出

V2.0.6 将 IFC 从双模式扩展为三模式：

```text
coordination_light.ifc  协调浏览版
analysis_model.ifc      计算模型交换版
design_detailed.ifc     施工图深化版
```

### 3.1 coordination_light.ifc

用于 Revit/Navisworks/普通 Viewer 协调浏览。保留主要构件几何和属性，省略详细钢筋、承压板和预埋件。

### 3.2 analysis_model.ifc

用于计算模型交换。保留构件轴线、支撑弹簧、墙体侧向荷载代理和施工阶段激活信息，不输出实体钢筋。

新增属性集：

- `Pset_AnalysisSupportSpring`
- `Pset_AnalysisLateralLoad`
- `Pset_AnalysisConstructionStage`

### 3.3 design_detailed.ifc

用于施工图深化审查。保留代表性钢筋、承压板、节点构造和预埋件代理。

## 4. 新导出接口

```text
GET/POST /api/projects/{project_id}/export/ifc-light
GET/POST /api/projects/{project_id}/export/ifc-analysis
GET/POST /api/projects/{project_id}/export/ifc-detailed
GET/POST /api/projects/{project_id}/export/ifc?mode=coordination_light
GET/POST /api/projects/{project_id}/export/ifc?mode=analysis_model
GET/POST /api/projects/{project_id}/export/ifc?mode=design_detailed
```

IFC 自检也支持三模式：

```text
GET/POST /api/projects/{project_id}/export/ifc-check?mode=analysis_model
```

## 5. 工程边界

优化器采用启发式评分，不代替工程师进行方案判定。分析模型版 IFC 是计算模型交换接口，不等价于完整 FEM 输入文件；后续仍需要进一步扩展节点自由度、荷载组合、边界条件和阶段激活/失活的标准化映射。
