# V3.34.0 水平支撑深化设计与稳定感知优化

本版本将水平支撑候选评价从几何拓扑扩展到初步结构深化设计。候选必须先满足零非法穿越、墙—墙传力、围檩支点间距、站位净距和角撑独立节点，再评价构件稳定、施工附加效应、节点完整性、轴力均衡、材料量和传力冗余。

主要接口：

- `GET /api/projects/{project_id}/design/support-deep-design`
- `POST /api/projects/{project_id}/design/support-deep-design/optimize`

主要模型：

- `N_eff = N + 0.5 N_pre + N_T + N_gap`
- `N_b,screen = min(0.85 A f, 0.75 pi^2 E I / (K L)^2)`
- `eta = N_eff / N_b,screen + M_e / M_screen`
- `CV_N,l = std(N_l) / mean(N_l)`

该模型用于候选排序和工程诊断。正式设计仍须执行分阶段墙—围檩—支撑耦合计算、规范适用性复核、节点专项设计和独立计算对账。

完整说明见 `V3_34_0_SUPPORT_DEEP_DESIGN_FULL_REPORT.md`。
