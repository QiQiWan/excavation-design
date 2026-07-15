- [V3.32.0 即时交互与全局进度反馈](releases/V3_32_0_FAST_INTERACTION_PROGRESS.md)
- [V3.31.0 外部数据对象与小内存工作集](releases/V3_31_0_EXTERNAL_DATASET_WORKING_SET.md)
# PitGuard 文档中心

现行文档按产品工作流、计算原理、深化设计、成果交付和版本记录组织。发生冲突时，优先级依次为：当前版本源码与自动化测试、V3.24 现行文档、历史版本文档。

## 当前版本 V3.27.0
- [V3.27.0 多平面支撑拓扑与隔离计算进程](releases/V3_27_0_SHAPE_TOPOLOGY_ISOLATED_WORKER.md)
- [V3.25.0 平行角撑体系与登录入口修复](releases/V3_25_0_PARALLEL_CORNER_BRACE_LOGIN_FIX.md)

- [V3.24.1 独立登录页面与受保护路由](releases/V3_24_1_INDEPENDENT_LOGIN_ROUTE.md)
- [V3.24 工业级计算基线与受控交付](releases/V3_24_0_INDUSTRIAL_CALCULATION_CONTROLLED_DELIVERY.md)
- [V3.23 支撑交点、墙长联合变量、IFC/配筋/图纸与登录优化](releases/V3_23_0_JOINT_CLEAN_IFC_REBAR_DRAWING_LOGIN.md)
- [V3.22 P0-P3 工业闭环实施报告](../docs/PitGuard_V3.22.0_P0-P3工业闭环实施报告.md)

- [工业化成熟度审查与整洁支撑拓扑优化](engineering/24_INDUSTRIAL_MATURITY_AND_CLEAN_SUPPORT_OPTIMIZATION.md)
- [V3.21.0 版本说明](releases/V3_21_0_CLEAN_SUPPORT_TOPOLOGY_AND_INDUSTRIAL_MATURITY.md)
- [设计院式围护设计、施工分幅、完整钢筋笼与八阶段成果闭环](engineering/23_DESIGN_INSTITUTE_PIPELINE_SUPPORT_WALL_CAGE.md)
- [V3.20.0 版本说明](releases/V3_20_0_DESIGN_INSTITUTE_PIPELINE_AND_REBAR_CAGE.md)
- [专家式支撑、双向配筋与墙长设计](engineering/22_EXPERT_SUPPORT_REBAR_WALL_LENGTH_DESIGN.md)
- [项目删除、墙—墙角撑与地质设计域闭环](engineering/21_PROJECT_DELETION_CORNER_SUPPORT_AND_GEOLOGY_COVERAGE.md)
- [一般多边形围护设计、计算状态一致性与地质设计域](engineering/20_GENERAL_POLYGON_STATE_AND_GEOLOGY_DOMAIN.md)
- [强度驱动的方案—构件联合设计与计算恢复](engineering/19_STRENGTH_DRIVEN_CALCULATION_RECOVERY.md)
- [工程施工图体系与协同成果导出](engineering/18_ENGINEERING_DRAWINGS_AND_COORDINATED_EXPORT.md)
- [设计流程—规范追溯、钢筋深化包与在线计算文档](engineering/16_STANDARDS_TRACEABILITY_REBAR_PACKAGE_AND_ONLINE_DOCUMENTATION.md)

## 产品、架构与计算

- [产品范围与工作流](architecture/01_PRODUCT_SCOPE_AND_WORKFLOW.md)
- [系统架构、数据与 API](architecture/02_ARCHITECTURE_DATA_AND_API.md)
- [计算方法与工程边界](engineering/03_CALCULATION_METHOD_AND_BOUNDARIES.md)
- [几何、结果与交付一致性](engineering/04_GEOMETRY_RESULT_AND_DELIVERY_CONSISTENCY.md)
- [配筋设计、三维审查与 CAD 成套出图](engineering/07_REBAR_DESIGN_VISUALIZATION_AND_CAD_DRAWING_SET.md)
- [高级工程分析、监测反演与审签](engineering/08_ADVANCED_ENGINEERING_MONITORING_REVIEW.md)
- [可配置出图规则引擎](engineering/09_CONFIGURABLE_DRAWING_RULE_ENGINE.md)

## 运行、质量与交付

- [运行、部署与故障排查](operations/05_OPERATION_DEPLOYMENT_AND_TROUBLESHOOTING.md)
- [测试、质量门禁与发布](operations/06_TESTING_QUALITY_AND_RELEASE.md)
- [钢筋加工深化包使用指南](operations/07_REBAR_DETAILING_PACKAGE_USAGE.md)
- [后端默认端口 8002](releases/V3_7_0_BACKEND_PORT_8002.md)

## 版本记录

- [版本变更记录](releases/CHANGELOG.md)
- `releases/`：V3.0—V3.24 正式版本说明和验证记录；
- `reference/`：标准矩阵、在线文档快照、数据格式、IFC 映射和监测模板；
- `archive/root-notes/`：V1.x—V2.9 历史材料，仅用于追溯。

## V3.24 在线文档逻辑

在线文档和接口统一采用八阶段流程：

1. 设计依据与设计域；
2. 支护体系与施工分幅；
3. 候选方案完整计算与选型；
4. 分阶段计算与规范校核；
5. 构件截面、双向配筋与墙趾优化；
6. 钢筋笼、节点与施工深化；
7. CAD/PDF/IFC/DOCX/XLSX/JSON 成果生成；
8. 设计、校核、审核、批准与正式发行。

项目流水线接口：`GET /api/projects/{project_id}/expert-design/pipeline`。
