# PitGuard V2.4.0 对象高亮定位、企业 CAD 模板与逐根钢筋几何迭代说明

## 迭代边界
本版本继续采用规范算法，不接入有限元。优化重点为：问题对象在二维/三维/CAD/内力图中的定位闭环、企业 CAD 模板可配置、逐根钢筋几何与下料数据、公开算例回归链路保持可运行。

## 1. Issue 定位闭环增强
- 问题清单点击后仍会跳转到对应流程步骤，同时把 locator 传入支撑平面图、工程三维视图、钢筋 IFC 可视化、内力包络图和 CAD 图纸定位预览。
- 支撑平面图对目标支撑/立柱做金色脉冲高亮。
- 工程三维视图对匹配的墙、围檩/冠梁、支撑、节点和立柱使用金色材质、高亮缩放和属性面板标记。
- 钢筋 IFC 可视化对匹配宿主或钢筋组加粗并改为金色显示。
- 计算结果中的支撑轴力包络会对匹配对象进行条形图高亮。
- 导出页新增 CAD 图纸定位预览，显示目标图纸页、对象编号和轻量图纸高亮位置。

## 2. 企业 CAD 模板可配置
新增接口：

- `GET /api/projects/{project_id}/cad-template`
- `PUT /api/projects/{project_id}/cad-template`

模板字段包括企业名称、项目代号、图号前缀、阶段、设计人、校核人、审定人、图框尺寸、图层标准、尺寸规则和 CAD 定位高亮图层。导出的 CAD 包会将当前模板写入 `enterprise_template_manifest.json` 和 `drawing_package_manifest.json`。

## 3. 逐根钢筋几何与下料数据
`GET /api/projects/{project_id}/rebar/detailing` 现在除钢筋编号表外，还返回 `individualBars` 和 `geometrySummary`。每根钢筋包含：

- barId / barMark / subIndex；
- 宿主构件和钢筋组；
- 逐点中心线坐标；
- 分段长度；
- 中心线长度；
- 锚固、搭接、弯钩代理长度；
- 下料长度和重量；
- 构造复核状态。

CAD 包新增：

- `S-08_individual_rebar_geometry.dxf`：逐根钢筋几何索引图；
- `individual_bar_geometry.csv`：逐根钢筋中心线、下料长度和重量表。

## 4. 验证
- `python -m compileall -q services/api/app` 通过。
- `npm run build` 通过。
- `npm test -- --run` 通过，3 passed。
- `test_v2_4_0_locator_template_rebar.py` 通过，3 passed。
- V2.1/V2.2/V2.3/V2.4 组合测试曾执行到多项通过，但公开算例回归链路较长，在当前执行窗口内超时；已对本轮新增接口和前端构建做定向验证。

## 5. 工程边界
当前仍然是规范算法与规则化详图原型。正式工程应用时，锚固、搭接、弯钩半径、施工缝、钢筋笼分节吊装和最终签审仍需工程师复核。
