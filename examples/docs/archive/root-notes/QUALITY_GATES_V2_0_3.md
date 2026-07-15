# V2.0.3 质量闸门：支撑布置、IFC 兼容性与计算书正式化检查

## 1. 闭环检查含义

V2.0.3 将原来的“闭环检查”拆成四个互相独立的状态，避免把软件功能路径、工程校核和正式出图混在一起。

- `capabilityCompleteness`：软件功能路径覆盖率。缺 VTU、缺计算结果、缺 IFC/计算书路径时会低于 100%。
- `softwareFlowComplete`：软件流程是否完整。它只说明资料、建模、围护、计算、成果这条链是否跑通。
- `engineeringCheckStatus`：工程校核状态。只要存在任一 `fail`，它必须为 `fail`；没有 `fail` 但存在 `warning` 时为 `warning`。
- `officialIssueGateStatus`：正式出图质量闸门。综合支撑布置评分、IFC 兼容性、稳定专项、计算书数据完整性和 fail/warning/manual_review。
- `closedLoopComplete`：设计辅助闭环。V2.0.3 中它表示“软件流程完整且无硬性 fail”。正式出图仍由 `officialIssueGateStatus` 单独控制。

因此，`closedLoopComplete=false` 不一定表示存在工程 `fail`。常见原因包括：未导入 VTU、未完成 IFC 自检、缺障碍物信息、缺稳定专项、报告图表数据不完整等。

## 2. 支撑布置合理性评分

新增模块：

```text
services/api/app/quality/support_layout_quality.py
```

自动检查：

1. 主对撑分仓间距是否在 3-6m。
2. 支撑跨长是否超过 30m warning / 45m fail。
3. 深基坑角撑数量是否明显不足。
4. 长支撑是否有临时立柱服务范围。
5. 支撑是否与坡道、出土口、保护区、中心岛等障碍物相交。
6. 是否定义换撑/拆撑路径。

输出字段：

```text
CalculationResult.supportLayoutQuality
CalculationResult.reportDiagramData.supportLayoutQuality
AssuranceResult.supportLayoutQuality
```

## 3. IFC 兼容性自检器

新增模块：

```text
services/api/app/quality/ifc_compatibility.py
```

导出前模型级检查：

1. 是否存在地连墙、支撑、梁、立柱、节点等核心实体。
2. 是否存在零长度、零截面、零高度构件。
3. 是否缺少材料等级。
4. 是否缺少参数化钢筋组。

导出后文件级检查：

1. 是否存在 raw unicode。
2. 是否存在未定义 STEP 引用。
3. 是否存在零尺寸 `IFCRECTANGLEPROFILEDEF` / `IFCEXTRUDEDAREASOLID`。
4. 是否存在缺少 `IFCLOCALPLACEMENT` 的风险。
5. 是否有产品缺少 `IFCRELASSOCIATESMATERIAL`。
6. 是否有产品缺少 `IFCRELCONTAINEDINSPATIALSTRUCTURE`。

新增接口：

```text
GET/POST /api/projects/{project_id}/export/ifc-check
```

导出 IFC 时会同步生成：

```text
*.ifc_check.json
```

## 4. 计算书正式化检查

新增模块：

```text
services/api/app/quality/formal_gate.py
```

正式化检查会汇总：

1. `fail / warning / manual_review` 数量。
2. 支撑布置评分与问题清单。
3. IFC 兼容性评分与问题清单。
4. 稳定专项是否存在。
5. 施工图详图清单是否存在。
6. 计算书图表数据是否存在。

输出字段：

```text
CalculationResult.formalReportGate
CalculationResult.reportDiagramData.formalReportGate
AssuranceResult.officialIssueGateStatus
AssuranceResult.officialIssueBlockingItems
AssuranceResult.officialIssueWarningItems
AssuranceResult.officialIssueMissingItems
```

DOCX 计算书首页新增“正式化检查与出图质量闸门”章节，集中列出阻断项、警告项和缺项。

## 5. 当前边界

V2.0.3 的支撑布置评分和 IFC 兼容性检查属于工程质量闸门，不替代人工审图。障碍物避让依赖用户是否录入坡道、出土口、中心岛、保护区等信息；如果未录入，系统会给出 warning。IFC 自检可发现常见 STEP/几何/关联风险，但不能保证所有第三方 BIM Viewer 100% 一致显示。
