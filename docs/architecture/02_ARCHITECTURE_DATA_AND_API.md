# 系统架构与数据接口

系统由 React/Vite 前端、FastAPI 后端、SQLite 本地存储、计算与导出模块构成。前端工作台和 Three.js 三维模块按需加载。后台长任务通过任务管理器执行，任务记录持久化到 SQLite，同一项目任务串行处理。

项目列表接口 `GET /api/projects` 只返回 `ProjectSummary`，避免传输完整计算历史。项目详情仍由 `GET /api/projects/{id}` 获取。几何一致性接口为 `GET /api/projects/{id}/geometry-consistency`，返回轮廓闭合状态、墙段覆盖关系和几何哈希。

计算结果中的完整墙体采样保存在 `stageResults`。`reportDiagramData.wallForceSamples` 保留为空值以兼容旧客户端，避免重复存储。全局耦合矩阵在阶段结果中保存完整数据，报告图表区只保留摘要。

版本信息集中在 `services/api/app/version.py`：

- `softwareVersion`
- `algorithmVersion`
- `ruleSetVersion`
- `exportSchemaVersion`

正式计算和交付清单应记录输入哈希、几何哈希、版本信息、执行时间和制品 SHA-256。
