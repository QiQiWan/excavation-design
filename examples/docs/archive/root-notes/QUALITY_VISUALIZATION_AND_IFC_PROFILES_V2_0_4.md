# V2.0.4 支撑评分可视化、IFC Viewer 分级和计算书首页清单

## 1. 支撑布置评分可视化

V2.0.4 将后端支撑布置质量闸门接入前端平面图和三维图。

新增数据：

- `SupportLayoutQualitySummary.highlights`
- `SupportLayoutQualitySummary.crossingPairs`
- `QualityGateIssue.highlightGeometry`
- `QualityGateIssue.relatedObjectIds`
- `QualityGateIssue.displayHint`

高亮规则：

- 红色：同层支撑无节点交叉、分仓严重超限、支撑跨长严重超限、障碍物硬冲突。
- 橙色：分仓偏密、跨长偏大、缺少立柱服务范围、缺少障碍物录入、缺少换撑路径。
- 蓝色：未被质量问题命中的普通支撑。
- 黄色半透明：坡道、出土口、中心岛、保护区等障碍范围。

同层支撑交叉判定：两条支撑线段在平面内相交，且不共享端点，判为 `support_crossing` fail。自动布置阶段也会跳过会造成交叉的候选支撑。

## 2. IFC Viewer 兼容性分级

`IfcCompatibilityCheckResult.viewerProfiles` 给出常用查看器的启发式兼容性风险：

- BlenderBIM / Bonsai
- BIMVision
- Solibri
- Autodesk Revit
- Navisworks

该分级不是厂商认证，只是根据常见 IFC 风险项估算，包括 raw unicode、未定义引用、零尺寸实体、placement、材料关联、空间归属、详细钢筋数量、代理构件数量等。

## 3. 计算书首页审查清单

`FormalReportGate.checklistSections` 固定分为：

1. 计算结果状态
2. 支撑布置合理性
3. IFC 兼容性
4. 成果完整性与专项复核
5. 正式出图阻断项

DOCX 首页会显示每个清单项的 fail、warning、manual_review、pass 数量，并列出关键问题和建议。

## 4. 导出页模型预览

前端导出页在 IFC/DOCX/JSON 下载链接下方新增模型可视化框，复用工程三维视图，并显示支撑质量高亮。该预览用于下载前检查支撑交叉、支撑间距、立柱、节点和 IFC 几何风险。
