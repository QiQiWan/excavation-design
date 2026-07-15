# PitGuard V3.37.0 渐进式设计与自适应资源运行架构

## 1. 改造目标

V3.37 针对三个长期耦合问题实施通用架构改造：

1. 基坑轮廓识别后立即自动生成完整体系，设计意图、施工组织和体系边界缺少逐级确认，复杂工程容易在后期集中暴露问题；
2. 完整项目采用固定 96 MB API 阈值，大型地质、候选、计算、钢筋和导出数据容易触发机械式阻断；
3. A/B/C 候选摘要仍在工作区内，但候选平面几何被压缩或外部化后，前端只显示空白视口。

本版本建立“渐进式决策 + 工作区优先 + 独立 worker + 自适应资源门禁 + 候选预览专用通道”的统一框架。规则由几何资格、工程约束和结构体系目录驱动，不依赖某一种平面分类。

## 2. 渐进式设计过程

### 2.1 八级过程

| 阶段 | 核心决策 | 主要输入 | 输出与门禁 |
|---|---|---|---|
| 1 轮廓、坐标与设计域 | 坐标处理、地质覆盖策略 | 闭合轮廓、控制点、钻孔范围 | 几何资格、坐标审计、设计域证据 |
| 2 工程约束与施工组织 | 顺作、分区、中心岛、逆作或专项组织 | 开挖标高、水位、障碍、出土口、施工分区 | 施工约束合同 |
| 3 围护墙体系与竖向分区 | 墙体家族、统一墙趾、分区墙趾或局部加强 | 地层、稳定控制段、地下室边界 | 围护墙概念体系与竖向策略 |
| 4 支撑结构体系 | 对撑、角撑混合、分区转接、环撑、中心岛、框架等 | 平面形态、施工约束、转接需求 | 体系级设计合同 |
| 5 线位与分仓搜索 | 支撑间距、立柱服务跨、优化主目标、候选数量 | 体系合同、障碍、锁定区 | 多样化候选几何 |
| 6 候选预检 | 穿越、端点、围檩跨、节点拥挤、障碍、冗余 | 候选几何 | 计算资格与体系回退建议 |
| 7 施工阶段完整计算 | 单方案优先或前三方案比选 | 可行候选、施工阶段、资源策略 | 轴力、位移、围檩、稳定性及计算证据 |
| 8 深化与交付 | 工程复核级、施工深化级、正式发行级 | 计算合同、节点、配筋、审签 | CAD/IFC/计算书及发行门禁 |

### 2.2 配置原则

渐进式配置保存在独立 `project_design_sessions` 表，不并入大型项目快照。配置包括：

```json
{
  "schemaVersion": "1.1",
  "currentStage": "support_system_strategy",
  "decisions": {
    "coordinateMode": "confirm_before_formal_issue",
    "geologyPolicy": "expand_with_extrapolation_gate",
    "constructionMethod": "internal_support",
    "retainingWallFamily": "auto",
    "wallVerticalStrategy": "uniform_by_zone",
    "supportSystemFamily": "auto",
    "cornerTreatment": "auto_by_topology",
    "transitionTreatment": "explicit_transfer_zone",
    "objectivePreset": "balanced",
    "candidateCount": 3,
    "fullCalculationCount": 1,
    "calculationMode": "adaptive_safe",
    "detailLevel": "engineering_review"
  },
  "constraints": {
    "supportSpacingMinM": 3.0,
    "supportSpacingMaxM": 6.0,
    "preferredSupportSpacingM": 5.0,
    "columnServiceSpanMaxM": 18.0,
    "preserveMuckPath": true,
    "avoidObstacleBoundaries": true,
    "requireIndependentWallNodes": true,
    "allowSupportToSupportTerminal": false
  },
  "resourcePolicy": {
    "mode": "adaptive",
    "workspaceFirst": true,
    "candidateExecution": "auto_serial_or_parallel",
    "fullProjectHydration": "worker_only"
  }
}
```

每次配置变更都会生成 `configurationTraceHash`，记录历史和最早失效阶段。例如修改坐标策略会使阶段 1 之后全部结果失效；只修改完整计算数量，仅使阶段 7 之后失效。系统保留已确认的上游阶段，避免每次修改都全流程重算。

### 2.3 通用性边界

渐进式过程基于：

- 轮廓闭合、主轴、凸凹性、可见墙对、设计分区和障碍边界；
- 支撑体系目录及其前提、硬边界和计算模型；
- 施工组织、地质覆盖、坐标和人工锁定；
- 结构硬约束和候选质量目标。

因此同一过程可用于规则矩形、旋转平面、凸多边形、L/T/U/C/Z/H 形、台阶形、圆形/多边形井筒、局部坑和含障碍工程。尚未实现计算内核的体系只允许“定义体系模型”，不会退化为普通直撑。

## 3. 自适应资源策略

### 3.1 固定阈值的问题

JSON 文档的安全加载尺寸取决于序列化放大、Pydantic 对象、求解器数组、当前进程 RSS、容器限制和并发任务。固定 96 MB 无法代表真实安全边界：高内存服务器被过早阻断，小内存容器又可能在阈值以下发生 OOM。

### 3.2 动态预算

系统读取：

- 主机 `MemTotal`、`MemAvailable`；
- cgroup v1/v2 内存限制和当前占用；
- API/worker 当前 RSS；
- CPU 核数与 1/5/15 分钟负载；
- SQLite/对象存储所在磁盘总量、空闲量；
- 管理员设置的硬上限。

核心预算为：

```text
有效可用内存 = min(主机可用内存, cgroup剩余内存)
系统保留量   = clamp(有效总内存 × 16%, 256 MB, 8 GB)
可用余量     = max(0, 有效可用内存 - 系统保留量)
API全量预算  = min(硬上限, 可用余量 × 42% / JSON放大系数)
工作区预算   = min(工作区硬上限, 有效可用内存 × 8%)
worker硬上限 = 受总内存、当前RSS、可用余量和systemd/cgroup共同约束
```

默认 JSON 放大系数为 5.5，可通过环境变量调整。旧的 `PITGUARD_API_FULL_PROJECT_LIMIT_MB=96` 在自适应模式下不再生效；需要行政硬限制时使用 `PITGUARD_API_FULL_PROJECT_HARD_CAP_MB`。

### 3.3 CPU、磁盘与并发

重型任务并发同时受内存和 CPU 负载控制：

- CPU 1 分钟负载率高于 85% 时，新重型任务按 1 个并发准入；
- 负载率为 65%～85% 时最多 2 个；
- 内存余量不足时自动降为串行；
- 每次任务启动前重新计算，而非仅在服务启动时计算一次。

对象外部化前还会检查磁盘安全余量。磁盘不足时只用 SQLite JSON1 重建轻量工作区，暂缓写入外部对象，避免数据库和对象目录同时耗尽磁盘。

## 4. 前端、API、worker 和存储职责

### 前端

只承担：

- 工作区摘要显示；
- 渐进式配置和人工确认；
- 候选轻量预览、局部缩放和状态反馈；
- 后台任务提交、轮询和结果刷新。

前端不持有完整施工阶段数组、原始 IDW 网格、逐根钢筋、IFC 实体缓存和全部历史计算。

### API 进程

只承担：

- 认证、轻量查询和配置写入；
- 工作区 JSON 直接返回；
- 候选预览缓存；
- 任务编排和资源状态接口。

API 提交 A/B/C 任务时只读取工作区中的候选 ID，不展开完整项目。GET 请求不得在 API 中重建重型计算对象。

### 独立 worker

承担：

- 完整项目按需重组；
- 支撑候选优化、完整施工阶段计算、配筋和导出；
- 资源预检、动态并发准入、超限监视和阶段性内存释放；
- 计算完成后写回一个不可变修订和轻量摘要。

### SQLite 与对象存储

采用四层数据：

1. `projects.data`：逻辑完整快照或外部对象引用；
2. `projects.workspace_data`：网页使用的有界投影；
3. `project_candidate_previews`：A/B/C 平面专用缓存；
4. `runtime/artifacts`：施工阶段、地质曲面、钢筋和导出重型对象。

## 5. A/B/C 空白预览修复

空白预览由候选计数/评分保留而 `planGeometry` 在工作区压缩时被清除造成。V3.37 实施三层保障：

1. 工作区压缩保留有上限的轮廓、支撑线、立柱点和节点指标；
2. 每次项目保存同步写入 `project_candidate_previews`；
3. 老项目缺少缓存时，通过 SQLite JSON1 从完整快照中只提取候选几何，缓存后按需返回，不加载整个 510 MB 项目。

前端切换 A/B/C 时使用候选 ID 合并预览。加载失败会显示明确诊断和重试按钮，不再显示无说明的空白区域。

## 6. 大项目完整链路

对于 510 MB 等大型项目，推荐运行路径为：

1. 网页打开 `workspace_data`；
2. 轮廓解析和渐进式配置通过轻量接口完成；
3. 候选生成提交到 worker；
4. 候选预览从专用缓存返回；
5. A/B/C 完整计算由 worker 根据实时资源串行或有限并发执行；
6. 计算、配筋、IFC 和图纸重型数据进入对象存储；
7. 网页刷新最新摘要和交付门禁。

“完整快照超过 API 全量预算”只表示 API 不直接展开该文档，不再阻断网页设计和任务编排。若 worker 也缺少安全余量，任务会进入资源等待或要求先完成外部化，系统不会以提高固定阈值的方式冒险运行。

## 7. 主要接口

- `GET /api/projects/{id}/design/progressive`
- `PUT /api/projects/{id}/design/progressive`
- `GET /api/projects/{id}/design/candidate-previews`
- `GET /api/system/resource-policy`
- `POST /api/projects/{id}/tasks/candidate-comparison-batch`
- `POST /api/projects/{id}/tasks`，操作 `storage_compaction`

## 8. 验收标准

- 大项目打开、渐进式配置、体系选择和候选预览不调用 API 全量加载；
- 旧 96 MB 环境变量在自适应模式下不形成机械上限；
- 候选预览缺失时可以按候选 ID 恢复；
- 配置变更只使依赖阶段失效，并产生追踪哈希；
- A/B/C 任务提交只读取工作区；
- worker 重型任务并发随内存、CPU 负载和磁盘余量动态调整；
- 低内存维护过程不执行完整 JSON hydration；
- 计算资格、正式发行资格继续执行工程门禁。

## 9. 当前边界

- 极大型旧快照在内存和磁盘均不足时只重建工作区，完整外部化延后执行；该策略优先保证服务不崩溃。
- 中心岛、环桁架、显式空间框架等体系若尚未接入完整计算内核，仍需先建立专项模型。
- Three.js 公共包约 524 kB，生产构建仍提示大分块，可继续按查看器功能拆分。
- 渐进式配置负责决策合同与失效传播，具体结构参数最终仍需通过计算、规范审查和专业校审。

## 10. 本轮验证

- V3.29—V3.37 大项目、工作区、外部对象、IDW、支撑深化、证据门禁和渐进式运行回归：50 项通过；
- V3.22、V3.25—V3.28 工业闭环、内存稳定、隔离 worker 和异形平面回归：43 项通过；
- 前端 Vitest：15 个测试文件、25 项通过；
- TypeScript 编译与 Vite 生产构建通过；
- Linux 生产启动脚本 shell 语法和存储迁移脚本 Python 语法检查通过；
- 当前执行环境未安装 `ezdxf`，依赖施工图 DXF 模块的 V3.23/V3.24 测试未执行，不计入通过数量。
