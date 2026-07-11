# PitGuard V1.1 IFC 诊断与基坑工程优化说明

## 1. 用户上传 IFC 的诊断结论

对 `/mnt/data/9c0d5002-7e10-4570-8b49-56790897b149.ifc` 进行 STEP 文本检查：

- `IFCEXTRUDEDAREASOLID` 实体数量：109。
- 其中 `ExtrudedDirection` 写为 `$` 的数量：109。
- 示例：`#53=IFCEXTRUDEDAREASOLID(#48,#52,$,21.);`

该写法会导致部分 BIM Viewer 无法生成扫掠实体，表现为文件打开但模型不可见、部分构件缺失或解析失败。V1.1 已将该字段改为显式 `IFCDIRECTION((0.,0.,1.))` 引用。

## 2. 已完成的代码修复

### 2.1 IFC 几何导出

- 修复 `IfcWriter.rect_swept_shape()`，所有 `IfcExtrudedAreaSolid` 均输出显式挤出方向。
- 增加 `_safe_refdir()`，避免 `IfcAxis2Placement3D` 的 Axis 与 RefDirection 平行。
- IFC GlobalId 改为 22 位压缩兼容 token。
- 墙筋竖向扫掠、支撑筋沿支撑轴线扫掠，降低不同 Viewer 对 `IfcReinforcingBar` 的解析风险。
- 属性集新增：
  - `DesignFaceCode`
  - `DesignLength_m`
  - `FaceSegmentIds`
  - `SupportRole`
  - `LayoutNote`

### 2.2 水平支撑沿短跨布置

- 原逻辑：支撑按层交替沿 X/Y 方向布置，可能出现跨越长边的长支撑。
- 新逻辑：自动识别基坑包络的长短方向，主对撑始终跨越短向，沿长向分布多榀。
- 对 90m × 24m 示例，主支撑长度为 24m，而不是 90m。

### 2.3 大长宽比基坑角部斜撑

- 当长宽比达到阈值时，四个直角附近自动增加 `corner_diagonal` 角部斜撑。
- 斜撑作为独立 `SupportElement` 输出，并在 IFC/前端属性中标记 `SupportRole=corner_diagonal`。

### 2.4 同一面墙统一设计长度

- 新增连续共线边段归组逻辑。
- 中间绘图节点不再导致同一面墙被拆成不同设计长度。
- 每片墙保留原 segment 追溯，同时共享同一 `DesignFaceCode` 和 `DesignLength`。

### 2.5 地质模型自动外扩

- 当基坑轮廓及 10m 缓冲范围超出现有地质模型 XY 范围时，系统自动基于钻孔重新生成外扩 IDW 地质面。
- 自动保留 warning，说明外扩区域为边界钻孔外推，正式工程应补充勘察或人工确认。

### 2.6 前端三维 Viewer

- TypeScript 类型增加 `supportRole`、`layoutNote`、`designFaceCode`、`designLength`、`faceSegmentIds`。
- Three.js Viewer 对角部斜撑使用不同颜色显示。
- 对象拾取面板可显示支撑角色、布置说明、标高、截面等属性。

## 3. 新增样例

- `packages/sample-data/projects/sample_wide_pit_corner_bracing.json`
- `packages/sample-data/projects/sample_wide_pit_corner_bracing.ifc`
- `packages/sample-data/projects/sample_wide_pit_corner_bracing_summary.json`

该样例为 110m × 24m 长矩形深基坑，自动生成：

```json
{
  "supportCount": 24,
  "mainStrutCount": 12,
  "cornerDiagonalCount": 12
}
```

## 4. 真实基坑工程后续仍建议深化的细节

1. 周边环境分区：建筑、道路、管线、地铁、河道等变形控制指标应分区管理。
2. 施工时序：分层分块开挖、换撑、拆撑、栈桥、底板施工阶段应进入工况拓扑。
3. 节点设计：角撑、对撑、围檩、立柱、格构柱、托座、牛腿、预埋件和节点局部承压需独立验算。
4. 降水与承压水：多含水层、水头时程、止水帷幕、降水井和渗流场仍需深化。
5. 地质不确定性：钻孔可信半径、透镜体、夹层、尖灭、参数统计和最不利剖面搜索仍需加强。
6. 施工偏差与温度：超挖、支撑预加力损失、温度应力、墙体垂直度和接缝渗漏需进入风险模型。
7. 监测反馈：位移、轴力、水位、沉降和周边管线监测需导入并与计算包络联动。
8. IFC 深化：钢筋笼可进一步由代表性组实体升级为按间距逐根建模，并补充止水、接头、预埋件和施工阶段状态。
