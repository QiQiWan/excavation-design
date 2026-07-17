# PitGuard V3.44.0 候选搜索内存治理与运行诊断

## 1. 故障现象

在 20 段围护墙、约 168 根支撑的长条台阶形工程中，候选方案搜索期间出现两个 Python 进程分别占用约 9.9 GB 和 3.7 GB；任务结束后单个 Python 进程仍可能达到约 18.7 GB。系统内存占用超过 90%，页面虽然还能显示 A/B/C 卡片，但采用方案、继续计算和其他桌面操作均无法正常完成。

该现象并非结构计算矩阵本身需要十几 GB。主要内存来自候选搜索和项目状态管理中的重复对象复制。

## 2. 根因

### 2.1 每个试算方案深复制完整历史工程

旧候选生成使用 `project.retaining_system.model_copy(deep=True)`。被复制对象同时包含：

- 当前围护墙、围檩、支撑和立柱；
- 历史 `supportLayoutRepair`；
- `layoutSummary.autoRepair`；
- `layoutSummary.supportOptimizationCandidates`；
- 计算摘要、配筋方案、审计和预览数据。

当完整项目快照约 138.8 MB、工作区约 93.6 MB 时，9～36 个试算组合会生成大量 Python 字典、列表和 Pydantic 对象。JSON 文件大小不能直接代表内存大小，模型水化、深复制和临时序列化会产生数倍放大。

### 2.2 同一候选集在项目中保存三份

候选同时存在于：

1. `retainingSystem.supportLayoutRepair`；
2. `retainingSystem.layoutSummary.autoRepair`；
3. `retainingSystem.layoutSummary.supportOptimizationCandidates`。

这导致候选几何、检查结果和预览数据重复进入完整快照和工作区。

### 2.3 点击“采用方案”再次执行完整搜索

旧采用接口会再次调用候选优化器，并在 API 进程内同步运行。即使搜索 worker 已退出，采用按钮仍可能让 API 再次深复制全部候选，使常驻 API 进程膨胀到 18 GB，之后无法释放给其他操作。

### 2.4 API 缓存长期持有大型 Pydantic 工程图

旧工作区缓存最多保留 16 个模型、120 秒，没有字节上限。约 93.6 MB 的 JSON 工作区水化后可能占用数百 MB，多个修订或项目会长期驻留在 API 进程。

### 2.5 Windows 内存门禁只看工作集

Windows 任务管理器中最关键的增长可能来自 Private Bytes/提交内存。旧门禁只检查 RSS/工作集，对私有提交量识别不足，也缺少操作系统级硬限制。

## 3. 修复方案

### 3.1 候选试算使用干净种子

候选搜索只复制：

- 围护墙；
- 冠梁；
- 设计设置；
- 局部锁定信息。

候选种子明确清除：

- 历史支撑和立柱；
- 旧围檩、节点和环梁；
- 历史计算结果和施工阶段；
- 旧候选、配筋、审计和预览缓存。

因此候选试算的内存由“当前轮廓和构件数量”决定，不再由“项目迭代历史”决定。

### 3.2 搜索空间和候选池严格有界

Core 模式默认：

| 项目 | 默认值 |
|---|---:|
| 最大试算组合 | 9 |
| 完整候选对象池 | 6 |
| 最终 A/B/C | 最多 3 |
| 单候选最大支撑构件 | 800 |
| 重型任务并发 | 1 |

候选池超过上限后，只保留各结构体系代表和评分靠前方案。每两个试算执行一次垃圾回收；超构件候选在进入质量图和预览序列化前即被拒绝。

### 3.3 候选只保留一个权威副本

权威候选集只保存在：

```text
retainingSystem.supportLayoutRepair
```

`layoutSummary` 只保留候选数量、推荐 ID、采用 ID 和评分等小型摘要。启动迁移通过 SQLite JSON1 直接删除旧项目中的两份重复候选，不需要在 API 中加载 100 MB 以上的项目对象。

### 3.4 方案采用改为 O(1) 查找和一次重建

点击“采用方案”现在执行：

```text
候选 ID 查找
→ 按已保存参数重建一次支撑体系
→ 保存当前方案
→ 失效旧计算结果
```

不会重新运行候选搜索。采用操作也通过独立 worker 执行。方案存在但工程校核仍有 Fail/Warning 时，任务会正常完成并保留工程结论，不再把“设计不通过”误当成“采用操作失败”。

### 3.5 API 工作区缓存按字节约束

默认缓存：

- 最多 4 个工作区；
- TTL 30 秒；
- 单项原始 JSON 最大 24 MB；
- 原始 JSON 总量最大 96 MB。

超过 24 MB 的工作区不进入 Pydantic 常驻缓存。网页项目接口直接返回 SQLite 中的轻量 JSON，不在 API 中重新建模。

### 3.6 Windows 使用 Private Bytes 和 Job Object 双重限制

worker 监测指标包括：

- RSS/工作集；
- Peak Working Set；
- Private Bytes；
- Pagefile Usage；
- 系统可用内存。

有效内存取 RSS 与 Private Bytes 的较大值。默认 worker 硬上限不超过 6 GB，监测周期由 3 秒缩短为 1 秒。

Windows supervisor 还为每个一次性 worker 安装 Job Object：

```text
JOB_OBJECT_LIMIT_PROCESS_MEMORY
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
```

即使出现瞬时分配峰值，操作系统也会在达到上限时终止计算子进程，API 和网页继续运行。

## 4. 运行诊断日志

默认写入 `runtime/diagnostics`，采用 JSONL 轮转格式，每个文件约 16 MB，保留 3 份历史：

| 文件 | 内容 |
|---|---|
| `task-lifecycle.jsonl` | 任务开始、阶段、结束、内存基线与增量 |
| `worker-memory.jsonl` | 每秒 RSS、Private Bytes、峰值、可用内存和当前步骤 |
| `candidate-search.jsonl` | 每个试算参数、候选池规模、支撑/立柱数量和内存 |
| `candidate-adoption.jsonl` | 候选查找、重建和采用阶段内存 |
| `project-storage.jsonl` | 完整快照、工作区、外部对象大小和序列化耗时 |
| `worker-supervisor.jsonl` | supervisor/worker PID、退出码、Job Object 安装状态 |

汇总命令：

```bash
python scripts/summarize-runtime-diagnostics.py --runtime runtime
```

针对 230 m 台阶形案例执行内存自检：

```bash
python scripts/smoke-candidate-memory.py --keep-runtime
```

## 5. 性能复测

使用项目自带的 24 钻孔、20 点闭合轮廓、230 m 长台阶形样例，执行候选搜索和候选采用：

| 指标 | V3.44 复测 |
|---|---:|
| 支撑数量 | 208 |
| 立柱数量 | 40 |
| 候选搜索与采用总耗时 | 15.27 s |
| 峰值 RSS | 428 MB |
| 完整项目快照 | 1.99 MB |
| 网页工作区 | 1.73 MB |

同一版本的完整核心设计、计算和配筋自检峰值 RSS 为约 388 MB，项目快照约 1.37 MB。

复测在 Linux 容器中进行；Windows 的内存统计口径不同，但候选深复制、重复持久化和同步采用三个主要放大源已经移除，并增加 Private Bytes 和 Job Object 保护。

## 6. 升级注意事项

保留现有：

```text
runtime/pitguard.sqlite3
runtime/artifacts/
exports/
```

替换程序后必须完全关闭旧版 API、worker、supervisor 和前端，再使用 `start-windows.bat` 或 `start-windows.ps1` 启动。启动时数据库会自动清除当前项目中的历史候选重复副本，历史 revision 只用于审计，不在网页和候选搜索中加载。

如果任务被资源闸门终止，先查看：

```text
runtime/worker.log
runtime/diagnostics/worker-memory.jsonl
runtime/diagnostics/candidate-search.jsonl
```

再执行汇总脚本定位峰值发生在哪个候选和阶段。
