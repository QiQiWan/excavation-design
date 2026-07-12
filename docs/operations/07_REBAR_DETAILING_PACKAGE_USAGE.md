# 钢筋加工深化包使用指南

## 下载方式

在项目工作台“成果导出”中选择“钢筋加工深化包”，下载结果应为 ZIP。也可调用：

```text
GET /api/projects/{project_id}/export/rebar-detailing-package?mode=balanced
```

配筋模式可选 `conservative`、`balanced`、`economic`。

## 人工复核流程

1. 打开 `rebar_detailing_schedules.xlsx` 的“钢筋包汇总”和“钢筋编号表”；
2. 核对构件编号、直径、级别、数量、单根长度和总重量；
3. 核对 BBS、加工分段、接头/套筒、钢筋笼分段和吊装计划；
4. 处理净距、保护层、弯曲半径和签审检查表；
5. 对照 CAD/PDF 钢筋施工图确认分区边界、剖面、节点和施工缝；
6. 完成企业标准、设备字段和现场施工条件确认后，方可导入后续加工系统。

## JSON 的用途

JSON 用于保存 Excel 难以完整表达的逐根三维中心线、构件宿主、检查证据、几何关系和机器接口字段。它适合：

- BIM 联动；
- 二次开发；
- 数字化加工接口；
- 设计变更差异比较；
- 完整追溯和归档。

现场交底和人工审阅应优先使用 XLSX、CAD/PDF 和签审后的图纸目录。

## 大型项目说明

为控制浏览器下载和 Excel 打开时间，XLSX 每张表最多展示 5,000 行。CSV 文件保持完整，JSON 保持全部逐根几何和语义。应先查看 `package_manifest.json` 中的 `workbookTruncation`：存在记录时，Excel 仅用于快速复核，数量统计和设备导入应以对应完整 CSV/JSON 为准。

钢筋包内若存在 `fabricationHardFailureCount`、`geometricSpacingFailureCount`、`manualReviewCount` 或 `omittedBarCount` 大于 0，代表当前包未达到直接加工条件。需要在系统中处理净距、分段、几何上限、节点碰撞和人工复核项后重新生成。
