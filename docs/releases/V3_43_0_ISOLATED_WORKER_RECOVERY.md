# PitGuard V3.43.0 隔离计算与后台恢复

## 1. 故障结论

V3.42 在 Windows 本地一键启动场景中只启动 Uvicorn 和前端，任务执行模式默认仍为 `embedded`。候选优化、施工阶段计算、配筋和导出由 API 进程内的线程池执行。重型任务占用大量 Python、NumPy、几何对象和本地数值库内存时，API 的健康检查、任务状态和工作区查询与计算争用同一进程；出现内存不足、本地库阻塞或进程退出后，网页只会持续轮询一个无法完成的任务。

问题还包括：

- Windows 运行时资源监测依赖 Linux `/proc`，可用内存和进程 RSS 可能被报告为 0，资源门禁失效；
- `core_design` 在持有完整项目对象的同时，后续计算阶段又重新载入项目，放大峰值内存；
- 支撑候选搜索会暂存过多完整围护体系副本；
- API 未要求外部 worker 心跳健康即可接收重型任务；
- worker 退出后，队列任务缺少自动中断和明确恢复状态；
- 前端刷新后不会恢复正在执行的任务，且长期轮询缺少 worker 健康判断。

## 2. 新运行架构

默认运行方式调整为：

```text
浏览器
  -> API：轻量查询、项目保存、任务编排
  -> SQLite任务队列
  -> worker supervisor
       -> 新建一次性 worker 进程
       -> 领取一个任务
       -> 执行候选/计算/配筋/导出
       -> 写回项目与任务结果
       -> 进程退出
       -> supervisor 启动干净 worker
```

API 不再执行工程重计算。单个 worker 的内存峰值、第三方本地库状态或异常退出不会带走 API。每个任务结束后进程退出，操作系统直接回收全部堆内存和本地库资源。

## 3. Windows 与 Linux 启动方式

`start-windows.ps1` 和 `start-linux-dev.sh` 默认设置：

```text
PITGUARD_TASK_EXECUTION_MODE=external
PITGUARD_PROCESS_ROLE=api
PITGUARD_WORKER_EXIT_AFTER_TASK=true
PITGUARD_NUMERIC_THREADS=1
```

启动脚本分别启动：

- API；
- worker supervisor；
- 前端。

Windows 本地开发默认关闭 Uvicorn reload，减少额外进程和重复初始化。确需热重载时显式设置：

```powershell
$env:PITGUARD_DEV_RELOAD = "1"
```

日志位置：

```text
runtime/backend.log
runtime/worker.log
runtime/frontend.log
runtime/worker-heartbeat.json
```

## 4. 跨平台资源门禁

新增 `system_resources.py`：

- Linux 读取 `/proc`；
- Windows 通过 Win32 `GlobalMemoryStatusEx` 和 `GetProcessMemoryInfo` 获取真实物理内存与进程 RSS；
- POSIX 系统使用 `sysconf`/`resource` 兜底。

worker 在任务执行期间使用真实 RSS 和系统可用内存实施软、硬门禁。资源不足时任务受控中断，API 保持在线。

## 5. 任务可靠性

外部模式提交任务前必须存在健康 worker 心跳。否则返回 503，并提示检查 `runtime/worker.log`，不会静默退回 API 内嵌执行。

任务恢复规则：

- running 任务心跳超过默认 45 s 未更新：标记 `interrupted`；
- queued 任务超过默认 60 s 且没有健康 worker：标记 `interrupted`；
- API 重启后仍可取消队列中的外部任务；
- 页面刷新后自动恢复当前项目最近的 queued/running 任务；
- 前端长期轮询时同步检查 worker 心跳，避免无限等待。

可配置：

```text
PITGUARD_WORKER_STALE_SECONDS=45
PITGUARD_WORKER_QUEUE_STALE_SECONDS=60
```

## 6. 内存峰值控制

### 6.1 核心设计生命周期

`core_design` 初始检查只读取工作区投影。候选生成、计算和配筋按阶段重新载入所需对象，并在阶段之间：

- 删除上阶段大型对象；
- 执行垃圾回收；
- 在支持的平台执行 allocator trim；
- 记录阶段前后 RSS。

完成的候选和计算结果逐阶段保存。后续阶段失败时，已经完成的结果仍可复用。

### 6.2 候选搜索有界化

Core 模式默认：

- 候选试算最多 12 组；
- 支撑构件默认最多 800 个；
- 候选对象池默认最多保留 9 个；
- 每个拓扑采用一组主线位偏移；
- 最终只保留最多 3 个可解释候选。

Full 模式保留原有更大搜索空间，但不属于默认工程流程。

### 6.3 数值线程限制

Windows 与 Linux 启动脚本将 OpenBLAS、OpenMP、MKL、NumExpr 等线程数默认限制为 1，避免多个线程池在候选和阶段计算中形成过度并行。

## 7. 前端行为

任务覆盖层增加：

- worker 心跳时间；
- 当前阶段与实际进度；
- 取消任务；
- 页面刷新后的任务恢复；
- running 心跳陈旧和 queued 长时间等待诊断；
- 连续读取失败上限；
- 40 分钟轮询硬保护。

前端不会因读取失败重新提交同一计算任务。

## 8. 验证

已完成：

- V3.43 隔离 worker 专项测试：5 项通过；
- V3.27、V3.29、V3.39、V3.40、V3.41 与 V3.43 联合回归：36 项通过；
- 前端测试：19 个测试文件、32 项通过；
- TypeScript 与 Vite 生产构建通过；
- Python 全量语法编译通过；
- Linux 启动脚本语法检查通过；
- 真实 supervisor + 一次性 worker 的核心设计烟雾测试成功。

隔离 smoke 结果：

- 核心任务成功；
- 总耗时约 4.15 s；
- worker RSS 峰值约 350 MB，阶段回收后约 319 MB；
- API 在 worker 执行和退出期间持续在线。

该 smoke 证明了进程隔离、任务队列、阶段保存和内存回收链路。用户实际大型工程数据库未随本次日志上传，仍需在目标机器上用同一启动方式重新执行，以确认该工程的实际候选规模和阶段计算耗时。

## 9. 升级注意事项

升级时保留：

```text
runtime/pitguard.sqlite3
runtime/artifacts/
exports/
```

先关闭旧 API、前端和残留 Python 进程，再使用新的 `start-windows.ps1` 或 `start-linux-dev.sh` 启动。不要只手工启动 Uvicorn后直接提交计算任务；手工部署时必须同时运行 `scripts/run-worker-supervisor.py`。
