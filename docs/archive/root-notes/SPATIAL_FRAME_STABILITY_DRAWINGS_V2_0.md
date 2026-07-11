# V2.0.0 空间杆系、稳定专项与施工图表达迭代说明

## 1. 空间杆系—墙体耦合内核

V2.0.0 将 V1.9 的平动凝聚矩阵升级为空间杆系代理矩阵。全局自由度包括：

- 墙体节点水平位移 `wall:ux`；
- 墙体梁转角 `wall:theta`；
- 围檩节点水平位移 `wale:ux`；
- 围檩梁转角 `wale:theta`；
- 支撑杆轴向变形 `support:axial`；
- 临时立柱竖向自由度 `column:v`；
- 地下室楼板换撑阶段附加水平刚度 `slabReplacementStiffness`；
- 支撑—围檩节点刚域 `rigidNodeZones`。

主要输出字段：

```text
CalculationResult.stageResults[*].globalCoupledResult.spatialMatrixSize
CalculationResult.stageResults[*].globalCoupledResult.spatialDofSummary
CalculationResult.stageResults[*].globalCoupledResult.wallRotationProfile
CalculationResult.stageResults[*].globalCoupledResult.waleNodeProfile
CalculationResult.stageResults[*].globalCoupledResult.supportAxialDofs
CalculationResult.stageResults[*].globalCoupledResult.columnVerticalDofs
CalculationResult.stageResults[*].globalCoupledResult.rigidNodeZones
```

## 2. 可审查地下水与稳定专项包

`stabilityDetailedResult` 将抗隆起、承压水突涌、抗渗流、整体稳定和软弱下卧层由孤立筛查结果整合为可审查专项包。输出包括：

- 控制剖面自动筛选；
- 圆弧滑动候选搜索数据；
- 渗流路径数据；
- 降水分级过程；
- 降水井建议；
- 承压水减压井建议；
- 坑底加固、增加嵌固、减压降水、增加支撑等方案比选。

该模块仍是设计辅助算法，正式工程需结合详勘、水文试验、地方审图要求和专项软件复核。

## 3. 施工图表达接口

新增 `drawingSheets`，自动输出 SVG 图纸：

```text
exports/detail-sheets/D-01_support_plan.svg
exports/detail-sheets/D-02_wale_node_detail.svg
exports/detail-sheets/D-03_wall_rebar_cage.svg
exports/detail-sheets/D-04_column_pile_detail.svg
```

图纸内容包括支撑平面布置、支撑—围檩节点、地连墙钢筋笼、临时立柱桩。当前为施工图深化接口，后续应接入图框、比例尺、尺寸链、钢筋编号、材料表和审签栏。

## 4. IFC 深化

IFC 导出新增：

- 立柱桩代理构件；
- 承压板 IfcPlate；
- 预埋件/锚固件代理；
- `Pset_ColumnPileDesign`；
- `Pset_BearingPlateDetail`；
- `Pset_PreembeddedAnchorDetail`；
- V2.0 空间杆系、施工阶段和节点刚域属性。

## 5. 当前边界

V2.0.0 仍不是正式商业有限元内核。后续生产级方向：

1. 全三维杆系/壳单元/FEM 联立求解；
2. JGJ 120 与 GB 系列规范逐条映射；
3. 降水井群渗流有限元；
4. 正式施工图图框和钢筋详图；
5. 监测数据反分析与施工期数字孪生。
