# V2.0.5 支撑自动修复、IFC 双模式导出与支撑评分图出图

## 1. 支撑布置自动修复器

新增 `services/api/app/services/support_layout_repair.py`。

自动修复器在计算前运行，流程为：

1. 调用支撑布置评分器获得修复前问题。
2. 对可修复问题执行重新布置：3-6m 主对撑分仓、扫描线移动避让障碍/出土口、无节点交叉规避、临时立柱和支撑-围檩节点重建。
3. 重新评分并写入 `CalculationResult.supportLayoutRepair` 和 `RetainingSystem.supportLayoutRepair`。
4. 若支撑 ID 改变，自动重建默认施工阶段，保证 `activeSupportIds` 与修复后的支撑一致。

新增接口：

```text
POST /api/projects/{project_id}/design/auto-repair-supports
```

返回 `SupportLayoutRepairSummary`。

## 2. IFC 双模式导出

`export_simplified_ifc()` 新增 `export_mode` 参数。

导出模式：

- `coordination_light`：轻量协调版，省略 `IfcReinforcingBar`、`IfcPlate`、预埋件代理实体；保留参数化钢筋属性和主要构件几何。
- `design_detailed`：施工图详细版，保留代表性钢筋、承压板、预埋件、节点构造和完整属性集。

新增接口：

```text
GET/POST /api/projects/{project_id}/export/ifc-light
GET/POST /api/projects/{project_id}/export/ifc-detailed
GET/POST /api/projects/{project_id}/export/ifc?mode=coordination_light|design_detailed
GET/POST /api/projects/{project_id}/export/ifc-check?mode=coordination_light|design_detailed
```

## 3. Viewer profile 分级优化

IFC 自检新增 `exportMode`。Viewer 风险分级会根据轻量/详细模式调整：

- 轻量协调版对 Revit/Navisworks 默认降低风险。
- 施工图详细版保留对钢筋、承压板、代理构件和属性集导入风险的提示。

## 4. 支撑评分与计算书联动出图

`services/api/app/reports/charts.py` 新增 `support_layout_quality_plan.png`。

计算书首页 `0.1 支撑布置合理性评分` 会自动插入支撑评分平面图，展示：

- 基坑轮廓；
- 主对撑、角撑、环撑；
- 临时立柱；
- 障碍物范围；
- 有质量问题的支撑标签。

## 5. 当前边界

自动修复器不会替代人工方案比选。对于复杂坡道、中心岛、栈桥、支撑洞口或大面积障碍，仍建议工程师在 CAD 编辑器中明确绘制障碍和施工路径，再执行自动修复和计算。
