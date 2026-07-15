# PitGuard V3.30.0：大型项目安全打开与 API 内存隔离

## 1. 故障现象

服务器日志表明，项目列表、登录和健康检查能够正常响应，但用户点击“打开项目”后 API 进程会失去响应或被 systemd 重新拉起。现场状态中计算 worker 处于空闲，RSS 仅约 51 MB；API 进程 `MemoryCurrent` 约 2.23 GB，已经超过 `MemoryHigh=2 GB`，接近 `MemoryMax=4 GB`。这说明故障发生在项目读取链路，不在隔离计算 worker。

## 2. 根因

V3.29 及以前的项目打开流程为：

```text
SQLite projects.data 完整 TEXT
→ sqlite3 将完整字符串复制到 Python
→ json.loads 生成完整 dict/list 对象树
→ Project.model_validate 再构造 Pydantic 对象树
→ 旧项目迁移和结果压缩检查
→ model_dump/model_dump_json 再生成响应对象或字符串
→ FastAPI response_model 再执行一次验证与序列化
```

大型项目可能同时包含：

- 全施工阶段墙体内力和位移离散点；
- 全局耦合自由度、支撑反力与矩阵诊断；
- A/B/C候选完整计算；
- 候选平面几何和差异动画；
- 原始VTU网格；
- 逐根钢筋和加工数据；
- 深化设计、工业资格与图纸缓存；
- 多个历史计算结果。

Python字符串、字典、列表、Pydantic对象和JSON响应会同时存在，峰值内存通常为磁盘JSON体积的数倍。旧版本还会在读取过程中自动迁移并写入项目，从而产生额外的完整序列化和不可变修订副本。

## 3. 新存储模型

`projects` 表增加：

```text
workspace_data   受控工作区JSON
payload_bytes    完整快照字节数
workspace_bytes  工作区投影字节数
```

完整快照继续保留在：

```text
projects.data
project_revisions.data
```

完整快照只允许隔离worker或显式全量接口读取。网页打开项目默认读取 `workspace_data`。

## 4. 工作区投影内容

工作区保留：

- 项目基本信息、单位和坐标；
- 钻孔、土层和关键地质摘要；
- 开挖轮廓和施工障碍；
- 围护墙、围檩、支撑和立柱；
- 支撑候选指标及有限预览；
- 施工阶段；
- 最近一次计算的控制值、校核汇总和发行状态；
- 审批和图纸修订摘要。

工作区排除或压缩：

- 全部施工阶段结果矩阵和离散点；
- 原始VTU网格；
- 候选完整计算；
- 大型工业深化缓存；
- 逐根钢筋完整几何；
- IFC实体缓存；
- 计算结果历史全文；
- 过量监测记录。

地质网格最大降采样为64×64。普通工作区上限默认为24 MB；超过上限时进一步移除候选预览几何和地质面网格，只保留工程对象与指标。

## 5. 零复制项目打开

`GET /api/projects/{id}?profile=workspace` 直接返回 SQLite 中的 `workspace_data` 字符串：

```text
SQLite workspace_data
→ FastAPI Response
→ 浏览器
```

该路径不执行：

- `json.loads`；
- Pydantic项目构造；
- 历史迁移；
- 项目写回；
- response_model二次验证。

响应头包含完整快照和工作区大小，便于运维判断。

## 6. 完整项目加载硬门禁

API进程默认：

```text
PITGUARD_PROCESS_ROLE=api
PITGUARD_API_FULL_PROJECT_LIMIT_MB=96
```

完整快照超过96 MB时，API在读取 `data` 字段之前返回HTTP 413：

```text
PROJECT_FULL_LOAD_BLOCKED
```

worker进程设置：

```text
PITGUARD_PROCESS_ROLE=worker
```

worker仍可读取完整快照执行计算和交付。这样即使某个旧接口误用全量加载，也只会得到受控错误，不会冲破API的systemd内存上限。

## 7. 读取操作无副作用

项目打开、版本列表、审计列表和修订号查询不再触发完整项目迁移或保存。旧结果失效、算法版本迁移和历史压缩由明确的维护或worker操作执行。

## 8. 旧数据库迁移

一键部署在启动API之前：

1. 创建SQLite在线一致性备份；
2. 增加工作区字段；
3. 使用SQLite JSON1生成工作区投影；
4. 移除计算结果、VTU、工业缓存和候选完整计算；
5. 计算完整/工作区字节数；
6. 输出最大项目体积；
7. 启动API与worker。

迁移由独立短生命周期Python进程执行。迁移期间即使SQLite处理大型JSON产生临时内存，进程结束后也会由操作系统完整回收，不会污染常驻API。

## 9. 前端变化

- 项目打开显式请求 `profile=workspace`；
- 20秒超时后显示存储诊断提示；
- 项目列表显示完整快照体积；
- 打开按钮显示“安全加载中”；
- 工作区中的重型结果继续按需读取；
- 后台任务完成后刷新工作区，不再拉取完整项目。

## 10. 运维接口

```text
GET /api/projects/{id}/storage-health
```

返回：

- 完整快照体积；
- 工作区体积；
- 压缩比例；
- API全量加载限制；
- 当前进程角色；
- 是否允许全量读取。

`status-production.sh` 同时输出体积最大的十个项目。

## 11. 验证

- V3.30专项测试：7项通过；
- V3.20—V3.29关键后端回归：66项通过；
- 前端12个测试文件、20项测试通过；
- TypeScript和Vite生产构建通过；
- Python compileall通过；
- Shell语法检查通过。

64 MB合成项目基准中：

- 工作区响应约4 KB；
- 工作区读取进程最大RSS约288 MB；
- 旧式完整模型读取与响应进程最大RSS约542 MB。

该基准的Python运行环境自身基线约280 MB，因此工作区打开增加的内存接近可忽略；完整路径额外增加约260 MB。真实项目包含大量小对象时，Pydantic对象树的放大比通常更高。

## 12. 使用边界

V3.30解决项目打开导致API进程内存失控的问题。完整计算、IFC、钢筋加工和大型结果查询仍应通过隔离worker完成。若单个完整项目快照长期超过数百MB，建议进一步迁移为分表/对象存储架构，将地质网格、计算结果、候选结果和加工数据从项目主文档中彻底拆分。
