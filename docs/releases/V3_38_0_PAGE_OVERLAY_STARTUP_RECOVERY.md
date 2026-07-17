# PitGuard V3.38.0 页面覆盖式进度与启动恢复

## 1. 版本目标

V3.38.0 处理两个直接影响基本可用性的问题：

1. 原全局加载反馈集中在页面顶部，在长页面、滚动页面和专业工作台中容易脱离用户视野；
2. V3.37 自适应资源策略新增 `workspace_only` 存储状态后，项目摘要模型仍使用旧三态约束，导致初始项目列表接口返回 500，系统进入首页即报错。

本版本将读取、保存、后台任务、候选比选、计算和导出统一接入页面覆盖式进度层，同时建立共享的存储状态契约和项目摘要降级恢复机制。

## 2. 初始界面报错根因与修复

### 2.1 根因

运行日志显示健康检查、认证初始化和系统诊断均正常，错误集中在 `GET /api/projects`。数据库摘要返回：

```text
storage_status = workspace_only
```

而 `ProjectSummary.storage_status` 仅允许：

```text
normal | elevated | large
```

Pydantic 因此抛出 `ValidationError`，项目列表接口返回 500。该问题属于运行时资源分类器和 API 数据契约之间的版本漂移，不是数据库损坏，也不需要删除现有大型项目。

### 2.2 共享状态契约

新增：

```text
services/api/app/contracts/storage_status.py
```

统一定义：

```text
normal | elevated | large | workspace_only
```

以下模块改为使用同一契约：

- 自适应资源策略；
- 项目摘要 Pydantic 模型；
- 项目列表接口；
- 前端领域类型；
- 项目列表状态展示。

这样可以避免分类器新增状态后，接口模型和前端仍停留在旧枚举。

### 2.3 历史状态兼容

项目摘要在模型校验前执行兼容归一：

| 历史或外部状态 | 归一结果 |
|---|---|
| `workspace-only`、`workspace`、`oversized` | `workspace_only` |
| `high` | `large` |
| `warning` | `elevated` |
| 未知值 | `elevated` |

未知值采用保守的 `elevated`，不会因为单个旧项目或外部工具写入的新状态使首页整体不可用。

### 2.4 项目列表逐条恢复

`ProjectRepository.list_summaries()` 改为逐项目验证：

- 正常摘要直接返回；
- 单条摘要校验失败时写入警告日志；
- 构造最小可用项目摘要并标记为 `elevated`；
- 继续返回其他项目。

单个异常记录不再拖垮全部项目列表。

### 2.5 前端大型项目状态

项目列表新增“工作区模式”展示：

```text
工作区模式 · 核心 <workspace size> MB
```

其含义为：浏览器加载轻量工作区，完整快照由独立 worker 按需读取。该状态属于大型项目的正常运行模式，不再显示为启动错误。

## 3. 页面覆盖式加载进度

### 3.1 统一进度事件总线

`GlobalRequestProgress` 由顶部窄条重构为全视口覆盖层，并继续复用现有请求活动事件。新增可编程接口：

```ts
beginGlobalActivity(...)
updateGlobalActivity(...)
finishGlobalActivity(...)
```

进度源包括：

- API 请求；
- 项目工作台多步骤流程；
- 后台任务轮询；
- 已存在任务恢复；
- A/B/C 并行完整计算；
- 文件导出和下载；
- 登录、文档、工作台和查看器懒加载。

### 3.2 两类覆盖行为

#### 被动读取覆盖

适用于 GET、预览和一般查询：

- 半透明覆盖整个页面；
- 显示读取阶段和估算进度；
- `pointer-events: none`，允许用户继续浏览现有内容；
- 不会让页面看起来卡死。

#### 阻断式任务覆盖

适用于保存、生成、计算、采用方案和导出：

- 覆盖整个页面并阻止重复交互；
- 显示真实任务百分比；
- 显示当前阶段、并行任务队列和失败状态；
- 任务完成后自动退出。

这一区分避免了所有读取都强制锁屏，同时对会改变工程状态的操作提供明确保护。

### 3.3 后台任务真实进度

项目工作台中的 `operation` 状态已映射到全局覆盖层，覆盖以下过程：

- 设计流程迭代；
- 完整计算；
- 方案采用；
- 存储压缩；
- IFC、CAD、PDF 等导出；
- 页面刷新后恢复中的任务。

A/B/C 并行计算按各任务实际进度计算平均值，并在覆盖层中显示当前正在执行的方案和步骤。

### 3.4 懒加载一致性

新增 `FullPageLoadingFallback`，用于：

- 认证初始化；
- 在线文档；
- 项目工作台；
- 专业视图组件。

用户不会再看到局部空白区或只在页面顶部出现一个加载提示。

## 4. 关键文件

### 后端

- `services/api/app/contracts/storage_status.py`
- `services/api/app/schemas/domain.py`
- `services/api/app/services/runtime_resource_policy.py`
- `services/api/app/storage/repository.py`
- `services/api/tests/test_v3_38_0_page_overlay_startup_recovery.py`

### 前端

- `apps/web/src/app/GlobalRequestProgress.tsx`
- `apps/web/src/app/GlobalRequestProgress.test.tsx`
- `apps/web/src/app/App.tsx`
- `apps/web/src/app/styles.css`
- `apps/web/src/pages/ProjectWorkspace.tsx`
- `apps/web/src/pages/ProjectsPage.tsx`
- `apps/web/src/components/SchemeComparisonPanel.tsx`
- `apps/web/src/types/domain.ts`

## 5. 回归验证

### 后端

V3.29—V3.38 大项目加载、外部对象、工作区投影、IDW、支撑深化、设计资格、自适应资源和启动恢复定向回归：

```text
53 passed
```

其中 V3.38 专项覆盖：

- 510 MB 项目在 96 MB 交互预算下分类为 `workspace_only`；
- `ProjectSummary` 正常接受该状态；
- 历史别名和未知状态可兼容归一；
- `GET /api/projects` 返回 200，不再因大型项目状态导致初始界面 500。

### 前端

```text
16 test files passed
27 tests passed
```

覆盖：

- 页面覆盖式阻断进度；
- 被动读取覆盖；
- 显式任务百分比更新；
- 工作区模式项目卡；
- A/B/C 轻量预览加载；
- 工作台、认证和导航回归。

### 构建

- TypeScript 编译通过；
- Vite 生产构建通过；
- npm 安装审计为 0 个已知漏洞；
- Three.js 独立包约 524 kB，仍有非阻断拆包提示，后续可按查看器继续拆分。

## 6. 部署与升级

该修复不要求删除或重新导入项目，也不要求直接修改 SQLite 数据。

推荐步骤：

1. 停止旧前端和 API；
2. 使用 V3.38 代码覆盖部署目录；
3. 重新构建前端；
4. 重启 API 和 worker；
5. 打开首页，确认 `/api/projects` 返回 200；
6. 打开大型项目，确认项目卡显示“工作区模式”；
7. 执行一次读取和一次后台计算，确认全页面进度层分别进入被动和阻断模式。

## 7. 工程边界

页面覆盖式进度用于反馈和防止重复操作，不代替后台任务幂等、资源准入和任务恢复。大型项目完整计算仍由独立 worker 执行；浏览器和 API 进程继续使用轻量工作区，以控制内存峰值并保证服务可用性。
