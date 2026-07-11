# PitGuard 文档中心

现行文档按主题维护，历史版本说明统一归档，避免根目录堆积大量迭代记录。

## 现行文档

### 当前版本

- [V3.3.0 八项工程深化闭环](releases/V3_3_0_EIGHT_TRACK_ENGINEERING_CLOSURE.md)
- [V3.3.0 验证记录](releases/V3_3_0_VERIFICATION.md)
- [版本记录](releases/CHANGELOG.md)

### 架构与工程说明

- [产品范围与工作流](architecture/01_PRODUCT_SCOPE_AND_WORKFLOW.md)
- [系统架构与数据接口](architecture/02_ARCHITECTURE_DATA_AND_API.md)
- [计算方法与工程边界](engineering/03_CALCULATION_METHOD_AND_BOUNDARIES.md)
- [几何、云图与交付一致性](engineering/04_GEOMETRY_RESULT_AND_DELIVERY_CONSISTENCY.md)
- [配筋设计、三维审查与 CAD 成套出图](engineering/07_REBAR_DESIGN_VISUALIZATION_AND_CAD_DRAWING_SET.md)
- [高级工程分析、监测反演与审签](engineering/08_ADVANCED_ENGINEERING_MONITORING_REVIEW.md)
- [运行、部署与故障排查](operations/05_OPERATION_DEPLOYMENT_AND_TROUBLESHOOTING.md)
- [测试、质量门禁与发布](operations/06_TESTING_QUALITY_AND_RELEASE.md)

### 历史正式版本

- [V3.2.0 配筋失败诊断、支撑拓扑与交互迭代](releases/V3_2_0_REBAR_DIAGNOSTICS_UX.md)
- [V3.2.0 验证记录](releases/V3_2_0_VERIFICATION.md)
- [V3.1.0 配筋与 CAD 迭代说明](releases/V3_1_0_REBAR_CAD_ITERATION.md)
- [V3.1.0 验证记录](releases/V3_1_0_VERIFICATION.md)
- [V3.0.0 集成迭代说明](releases/V3_0_0_INTEGRATED_ITERATION.md)
- [V3.0.0 验证记录](releases/V3_0_0_VERIFICATION.md)

## 参考与历史资料

- `reference/`：数据格式、监测 CSV 模板、标准调研、IFC 映射、历史产品蓝图和既有产品需求等参考资料。
- `archive/root-notes/`：V1.x—V2.9.0 的阶段性迭代说明，仅用于追溯，不再作为现行实现依据。

发生文档冲突时，优先级依次为：V3.3.0 现行文档、源码及自动化测试、历史归档文档。
