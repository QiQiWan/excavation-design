# V2.0.8 人机协同支撑优化器

本版本在 V2.0.7 约束优化器基础上增加交互式方案选型能力。

## 1. 候选方案平面图并排对比

每个 `SupportLayoutOptimizationCandidate` 输出 `planGeometry`，包含基坑轮廓、支撑线、立柱、障碍物和支撑线是否被移动/锁定的信息。前端据此绘制 3-5 个候选方案的平面图。

## 2. 点击候选方案高亮线位变化

前端维护 `selectedCandidateId`，点击平面图或候选表行后高亮对应方案。候选图中红色虚线表示优化器移动过的支撑线，黑色粗线表示锁定支撑线。

## 3. 采用此方案

`POST /api/projects/{project_id}/design/adopt-support-candidate` 接受 `candidateId`，后端按候选方案的目标分仓、立柱服务跨和线位变量策略重新生成支撑体系，并写回项目。

## 4. 锁定支撑线

`POST /api/projects/{project_id}/design/lock-support-lines` 接受 `supportIds`、`locked`、`reason`。锁定支撑在后续优化中保持线位，不被自动平移或替换。

## 5. 优化权重设置

`POST /design/optimize-supports` 支持：

```json
{
  "preset": "low_axial_force",
  "objectiveWeights": {"axialPeakProxy": 35, "columnCount": 3}
}
```

可选 preset：`balanced`、`fewer_columns`、`low_axial_force`、`muck_path_priority`。

## 6. 计算书联动

计算书首页新增“支撑优化候选方案评分图”和“支撑优化候选方案平面比选图”，用于在审图首页对比候选方案质量。
