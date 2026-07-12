# 设计流程—规范追溯、钢筋深化包与在线计算文档

## 1. 本轮目标

V3.11.0 将规范对应关系从分散的规则文件、计算检查和说明文字中提取为统一数据模型，并贯穿工作台、在线文档、计算书和交付包。同步修复“钢筋加工深化包下载为项目 JSON”的产品语义错误。

本轮覆盖三个层级：

- P0：交付正确性、完成度口径、人工复核统计和正式发行边界；
- P1：项目级流程—规范矩阵、工作流高亮、计算书追溯和钢筋 ZIP 成套导出；
- P2：在线计算原理、核心公式、假定、验证点、文件用途和机器可读接口。

## 2. 钢筋加工深化包

### 2.1 原问题

旧版任务 `export_rebar_detailing` 将 `build_rebar_detailing()` 的完整对象直接写入 JSON。前端下载按钮又指向项目完整 JSON 导出地址。用户无法直接获得可审阅的钢筋编号表、BBS、接头表、钢筋笼分段表和检查表。

### 2.2 新包结构

```text
<project>_rebar_detailing_package_v3_11_0.zip
├─ rebar_detailing_schedules.xlsx
├─ package_manifest.json
├─ README.txt
├─ 00_machine_data/
│  ├─ rebar_detailing_full.json
│  ├─ individual_rebar_geometry.json
│  └─ rebar_design_scheme.json
├─ 10_schedules/
│  ├─ summary.csv
│  ├─ rebar_mark_schedule.csv
│  ├─ fabrication_bbs.csv
│  ├─ fabrication_segments.csv
│  ├─ splice_schedule.csv
│  ├─ cage_segments.csv
│  ├─ lifting_plan.csv
│  └─ construction_joint_plan.csv
├─ 20_checks/
│  ├─ spacing_checks.csv
│  ├─ cover_conflict_checks.csv
│  ├─ bend_radius_checks.csv
│  └─ signoff_checklist.csv
└─ 90_guidance/README_USAGE.md
```

### 2.3 文件使用边界

- XLSX：人工复核、翻样交接和数量核对的主文件；
- CSV：ERP、翻样系统或加工设备中间层的字段映射文件；
- JSON：保留逐根钢筋中心线、宿主关系、规则结果、套筒和吊装语义的无损交换文件；
- CAD/PDF：可打印、可审签的施工图表达，位于 CAD 图纸包或正式图纸发行包。

导入加工设备前必须确认单位、钢筋级别、弯曲规则、接头工艺、设备字段和企业标准图集。

## 3. 流程—规范矩阵

后端统一服务位于：

- `app/services/standards_matrix.py`
- `GET /api/standards/catalog`
- `GET /api/standards/process-matrix`
- `GET /api/projects/{project_id}/standards/process-matrix`
- `GET /api/documentation`

每个流程节点输出：

- 关键计算步骤；
- 主控规范与规范等级；
- 条文关注点；
- 已实现规则 ID 和 `clauseReference`；
- 项目当前 Pass/Warning/Fail/人工复核状态；
- 实现等级和适用边界；
- 计算或交付输出。

当前覆盖项目设定、勘察资料、三维地质、基坑几何、围护方案、分阶段计算、闭环审查和成果发行八个节点。

## 4. 规范优先级

系统将全文强制性工程建设规范标记为最高级：

1. `GB 55003-2021 建筑与市政地基基础通用规范`；
2. `GB 55008-2021 混凝土结构通用规范`；
3. `JGJ 120-2012 建筑基坑支护技术规程`；
4. `GB 50007-2011 建筑地基基础设计规范`；
5. `GB 50009-2012 建筑结构荷载规范`；
6. `GB 50010-2010（2015年版，2024年局部修订）混凝土结构设计规范`；
7. `GB 50017-2017 钢结构设计标准`；
8. `GB 50068-2018 建筑结构可靠性设计统一标准`；
9. `GB 50497-2019 建筑基坑工程监测技术标准`。

地方标准、勘察报告、专项设计条件、审图意见、专家论证和企业标准通过项目级规则集补充。系统显示的条文映射代表已实现规则子集，不替代标准全文。

## 5. 工作台显著显示

每个 StepHeader 下方增加规范追溯条：

- 红色徽标：全文强制性通用规范；
- 蓝色徽标：专业规程和设计标准；
- 状态色：项目规则聚合状态；
- 展开区：关键计算、条文关注点、输出、规则 ID 和条文引用；
- 完整入口：在线计算与规范文档。

该组件读取项目级矩阵，计算后会呈现与当前项目相关的检查状态。

## 6. 在线文档

`/docs` 页面从单一操作说明升级为四个章节：

1. 操作流程；
2. 计算原理；
3. 流程—规范矩阵；
4. 成果文件使用。

计算原理按模块给出输入、模型、核心公式、假定、输出、验证点和规范依据。在线文档由后端数据生成，版本号、规则集和源码保持同步。

## 7. 计算书增强

DOCX 计算书新增：

- 规范目录与等级；
- 规范优先级；
- 八步骤流程—关键计算—规范—条文—规则—状态—输出矩阵；
- 土压力、墙体、围檩支撑、构件承载力、稳定和钢筋深化的输入—公式—输出—复核点表。

计算书和网页调用同一个 `standards_matrix` 服务，减少口径漂移。

## 8. P0 状态一致性

完整交付包和 CAD 包不再写入硬编码 `softwareModuleCompletion: 100`。清单改为记录：

- `softwareCapabilityCompleteness`；
- `projectModuleCompleteness`；
- `engineeringCheckStatus`；
- `reviewStatus`；
- `officialIssueGateStatus`；
- `officialIssueGateAllowed`。

Assurance 聚合同时读取最新总检查和阶段检查并去重，人工复核数量与计算书、问题中心和交付清单采用同一口径。

## 9. 大型钢筋包导出性能与完整性

大型项目可能包含数万根加工段。V3.11.0 将 XLSX 定位为人工复核主表，每张表最多写入 5,000 行；全部记录仍完整写入 CSV，逐根三维中心线和全部语义完整写入 JSON。`package_manifest.json` 会列出被截断的 Excel 表、总行数、Excel 行数及完整数据源。导出器取消逐单元格样式写入并采用紧凑 JSON，完整示例项目约 12,000 根逐根钢筋、33,844 个加工段的成套包可在约 10 秒量级生成。

## 10. 数值求解质量门禁

墙—围檩—支撑全局矩阵新增以下软件质量证据：

- `K·u=F` 有效矩阵相对残差；
- 原始矩阵相对残差；
- 最大方程残差；
- 矩阵对称误差；
- 矩阵条件数；
- 正则化参数和人工复核状态。

该门禁用于识别约束不足、刚度尺度异常和正则化求解。它与工程规范验算分开显示，不能替代土压力、承载力、变形、稳定和构造检查，也不会虚构规范条文。
