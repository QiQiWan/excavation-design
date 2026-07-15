# V2.0.15 钢筋级 IFC 可视化与 CAD 出图优化

## 已实现

1. 新增后端钢筋级 IFC 可视化数据接口：`GET /api/projects/{project_id}/export/ifc-rebar-visualization`。接口从地连墙、冠梁/围檩、水平支撑、节点附加筋等钢筋组生成浏览器可渲染的采样钢筋段，并返回 IFC 类映射、钢筋组摘要、估算完整钢筋数量和状态分布。

2. 新增前端 `RebarIfcViewer` 模块，并插入到 Step 8「BIM 与计算书」中。支持宿主筛选、钢筋类型筛选、透明宿主显示、X 向剖切、点击钢筋查看 IFC 类/宿主/钢筋组/直径/间距/状态。

3. 新增施工 CAD 图纸包导出：`GET /api/projects/{project_id}/export/drawings-cad`。输出 ZIP，包含 R12 DXF 支撑平面图、地连墙钢筋笼示意图、支撑—围檩节点详图和钢筋表 CSV。

4. 新增施工图 SVG 图纸包导出：`GET /api/projects/{project_id}/export/drawings-svg`。用于汇报、校审和文档插图。

## 设计结论

当前系统可以导出施工 CAD 图纸的交换文件。推荐路线是后端生成 DXF/SVG，前端通过导出卡片下载 ZIP 包。DXF 使用 CAD 图层承载墙、围檩、支撑、立柱、钢筋、障碍、文字和尺寸信息；CSV 承载钢筋组参数。正式施工图级 CAD 仍需补充图框签审、详图编号、钢筋弯曲形状、锚固搭接、保护层、剖面索引、尺寸链和下料表。

## 钢筋级状态

本版本实现的是“钢筋级浏览器可视化 + IFC 代表性钢筋映射 + CAD 交换图纸包”。对于按间距布置的密集钢筋，浏览器端采用采样显示，并在 `estimatedFullBarCount` 中保留完整估算数量，避免大项目一次渲染数万根钢筋导致卡顿。
