# IFC 映射（engineering-v0.2）

本版本将 MVP 的轻量 proxy 导出升级为工程级 IFC4 STEP 文本导出器。当前环境未强制依赖 IfcOpenShell，因此导出器直接生成 IFC4 STEP；后续可在相同服务接口下替换为 IfcOpenShell 后端。

## 空间结构

- `IfcProject`：项目根对象，包含单位、几何上下文和项目级属性集。
- `IfcSite`：场地。
- `IfcBuilding`：基坑支护模型容器。
- `IfcBuildingStorey`：临时支撑阶段模型。
- `IfcRelAggregates` 和 `IfcRelContainedInSpatialStructure`：建立空间聚合与构件容纳关系。

## 构件实体

- `IfcWall`：地下连续墙，采用 `IfcExtrudedAreaSolid` 矩形扫掠体表达墙长、墙厚、墙深。
- `IfcBeam`：冠梁、腰梁、钢筋混凝土支撑。
- `IfcColumn`：立柱/临时立柱。
- `IfcMaterial` 与 `IfcRelAssociatesMaterial`：混凝土、钢筋、结构钢材料关联。

## 属性集

### Pset_RetainingWallDesign

- WallType
- Thickness
- TopElevation
- BottomElevation
- EmbedmentDepth
- ConcreteGrade
- RebarGrade
- MaxMoment_kNm_per_m
- MaxShear_kN_per_m
- MaxDisplacement_mm
- MomentDesign_kNm_per_m
- ShearDesign_kN_per_m
- RequiredAs_mm2_per_m
- ProvidedAs_mm2_per_m
- MomentCapacity_kNm_per_m
- ShearCapacity_kN_per_m
- RebarDiameter_mm
- RebarSpacing_mm
- CheckStatus
- ProfessionalReviewRequired

### Pset_InternalSupportDesign

- LevelIndex
- Elevation
- SectionType
- SectionSize
- Material
- DesignAxialForce
- Preload
- CheckStatus
- ProfessionalReviewRequired

### Pset_ReinforcementGroups

MVP/engineering-v0.2 采用参数化钢筋组属性输出，而非逐根 `IfcReinforcingBar` 实体。属性包括钢筋组名称、直径、间距、根数、等级、位置描述和校核状态。

## 后续增强

1. 接入 IfcOpenShell 并生成更完整的 `IfcWallStandardCase`/`IfcMember`/`IfcStructuralAnalysisModel`。
2. 地连墙分幅、槽段接头、接头箱、止水和钢筋笼实体化。
3. `IfcReinforcingBar` / `IfcReinforcingMesh` 详细钢筋模型可选导出。
4. 工程量统计、构件编码、施工阶段 4D 属性和模型校验报告。
