# V2.9.0 方案快照与交付驾驶舱

## 目标

在 V2.8.0 的围护墙设计长度冗余修复闭环基础上，进一步补齐“采纳方案之后如何追溯、如何对比、如何判断是否可交付”的使用闭环。

## 新增能力

1. 项目交付驾驶舱：后端提供 `/api/projects/{project_id}/dashboard`，前端在工作台顶部集中显示交付闸门、规范状态、墙长闭环、方案快照和下一步动作。
2. 方案快照台账：后端提供 `/api/projects/{project_id}/design-scheme-ledger`，记录当前方案 KPI、设计面状态、墙长优化历史、待复算状态和交付阻断项。
3. 完整交付包联动：`full_delivery` 增加 `designSchemeLedger` 输出，并将交付包命名升级到 `v2_9_0`。
4. 高级导出：新增 `design-scheme-ledger` 导出接口和后台任务 `export_design_scheme_ledger`。
5. 问题中心模块清单：新增 M16“方案快照、复算状态与交付闸门台账”。

## 工程边界

- 墙厚继续采用项目统一厚度策略。
- 方案快照只记录设计面长度、分幅长度、局部加强段等优化动作。
- 不改变基坑外轮廓、围护墙轴线或正式签审边界。
- 交付闸门为软件辅助判断；正式出图仍需注册工程师和企业签审流程。

## 验证

- `python -m compileall -q services/api/app` 通过。
- `pytest -q test_v2_9_0_design_scheme_ledger.py test_v2_8_0_wall_length_closed_loop.py` 通过。
- `pytest -q test_v2_1_0_tasks_issues.py test_v2_2_0_trace_and_cad.py` 通过。
- `npm run build` 通过。
- `npm test -- --run` 通过。
