# PitGuard V3.88.0：工程 Agent 基础架构与 A5/A6 试点

发布日期：2026-11-06。升级基线：V3.87.28 完整集成源码。软件版本 3.88.0；单排桩求解内核、规范规则版本和原设计成果协议保持现有身份。

## 1. 本版实现范围

V3.88.0 为原有单排桩—一道内支撑工作台增加 **Agent 控制层**：任务拆分、持久化、可恢复执行、模型网关、工程工具白名单、独立候选计算、候选比选、人工工作版采用和事件追溯。原全基坑设计、图纸和 BIM 功能路径保留。

当前只有 **A5 计算与验算 Agent**、**A6 优化闭环 Agent** 具有可运行实现。A1 资料、A2 地质、A3 方案、A4 阶段、A7 构件、A8 成果已登记职责与边界，尚未提供自主执行流程。不能把 8 个角色登记数量理解为 8 个完整智能体均已运行。

A5/A6 的第一条闭环使用单剖面数值求解器的冻结结果，执行：诊断 → 有界变量候选 → 原求解器逐候选完整重算 → 指标比较 → 等待校审角色处理。变更候选不写入原工作快照，不会运行原 `closure-run` 的单系数近似器。工程师采用仅更新独立 Agent 工作版选择，必要时将候选重新载入单排桩工作台。正式图纸发行保持 `false`。

## 2. 模块与职责

| 文件 | 职责 |
|---|---|
| `app/agents/contracts.py` | 严格 API 参数、候选预算、人工决策合同 |
| `app/agents/catalog.py` | 8 个专业角色与 6 个确定性工程工具的允许列表、执行边界 |
| `app/agents/store.py` | SQLite WAL 任务、事件、候选、租约、暂停恢复、审批及工作版选择 CAS |
| `app/agents/engine.py` | 结构化诊断、受限优化规则、候选生成、冻结结果复算、反向校核 |
| `app/agents/model_gateway.py` | 推理/快速/视觉模型配置槽、模型解释 HTTP 适配器、域名允许列表、离线降级 |
| `app/agents/worker.py` | Embedded/External 两种运行方式的后台 Worker，原子领取任务 |
| `app/agents/router.py` | 工程师任务 API、权限隔离、暂停/恢复和人工采用接口 |
| `AgentPilotPanel.tsx` | 单排桩结果页内嵌 Agent 任务、问题、进度、候选与校审面板 |

当前选用自有有限状态机及 SQLite，便于和既有 FastAPI、任务服务共存；没有新增 LangChain、OpenAI SDK 或专用工作流服务依赖。模块内部将 Agent 的规划和执行分离，后续可单独替换模型 Provider 或编排实现。

## 3. 工程安全约束

1. Agent 的计算输入必须来自 `Repository.load()` 校验过的冻结结果。源数据包含 `snapshotHash`、`resultHash`，加载时进行内容哈希和同源验证。
2. A6 目前只允许试算 `strut.stiffness_kn_m`、`pile.inertia_factor`、`pile.length_m`。每项最多生成 5 个候选；禁止修改土层强度、水位、规范安全限值、组合系数、原工程来源文件或其他字段。
3. 候选调整参数后，原来确认该参数的 `sources` 记录自动降为 `candidate`，清除 `approved_by`。必须重新核对变更后的设计依据。实际计算由原 `Repository.compute` 完整进行，失效或越界候选记录为失败，不冒充计算通过。
4. 候选比选至少检查 `analysisStatus`、位移变化、规范检查结果、新增的明确失败项。`engineeringQualified=false`、`formalConstructionIssue=false` 始终保留。旧规范缺项不能被新候选计算掩盖。
5. 人工采用需要 checker 或以上权限，输入当前工作版哈希；在同一个 SQLite 事务内执行 CAS 比较并记录决策。不接受已过期的原始方案，不接受测试验证例直接进入工作版。采用前重新从不可变存储读取原方案和候选，重算允许变更字段、结果和版本一致性；不依赖 Agent 自报的布尔值。
6. “采用工作版”不等于确认材料/支撑刚度的来源，不能修改主全基坑项目、不产生正式校审签章、不触发施工图签发。仍需完成勘察来源、各项规范验算和实际构造复核。
7. 任务通过事务性租约防止多 Worker 重复领取。长计算每 12 s 续约；运行中暂停/取消于当前不可中断的工程计算步骤完成后生效。失败或服务重启后，从最后持久化的执行步骤继续。候选写入后采用不可变 ID 去重；不得自动重放人工采用。

## 4. 模型如何介入

**V3.88.0 模型仅用于解释工程问题，候选调整和力学复算均按确定性代码进行。** 默认模型关闭；没有 API Key 也能完整使用 A5/A6 试点。启用需同时满足：服务端 `PITGUARD_LLM_ENABLE=1`、管理员已配置密钥/模型/主机允许列表、工程师在新建 Agent 任务时选择“启用模型解释”。

网关只向已配置的 Chat Completions 兼容端点发送不超过 15 条结构化问题摘要和项目短标识，不发送勘察报告全文、原始坐标文件或私有数据库。接口使用 HTTPS，允许列表限制可访问主机，本机服务允许明确配置的 HTTP。返回的 JSON 仅作为未验证文字摘要，不能变更数据库、工具参数、数值计算或规范判定。失败自动转为规则解释，不中断计算。

环境变量示例（仅示意，部署时在受控环境配置；不要提交密钥）：

```bash
PITGUARD_LLM_ENABLE=1
PITGUARD_LLM_BASE_URL=https://api.openai.com/v1
PITGUARD_LLM_ALLOWED_HOSTS=api.openai.com
PITGUARD_LLM_MODEL_REASONING=<approved-model-id>
PITGUARD_LLM_MODEL_FAST=<approved-fast-model-id>
PITGUARD_LLM_MODEL_VISION=<approved-vision-model-id>
PITGUARD_LLM_API_KEY=<secret-from-secure-environment>
```

目前推理槽供 A5 解释使用，fast/vision 只登记配置以供后续 A1/A3 接入。本版未对真实供应商进行付费 API 联调。模型输出无工程安全签发权。

## 5. 主要 API

所有路径经主 FastAPI 身份校验。查询他人任务需 checker 及以上权限。

| HTTP | API | 作用 |
|---|---|---|
| GET | `/api/agents/catalog` | 查看专业角色及可调用工具 |
| GET | `/api/agents/model/configuration` | 查看模型启用状态（不返回密钥） |
| POST | `/api/agents/runs` | 根据已保存 `result_hash` 创建试点任务；可设 `max_candidates`、`model_mode` 和幂等请求 ID |
| GET | `/api/agents/runs?project_id=...` | 获取授权范围内任务 |
| GET | `/api/agents/runs/{id}` | 任务状态、阶段和已有诊断 |
| GET | `/api/agents/runs/{id}/overview` | 一次返回状态、候选、事件、工作选择（前端轮询） |
| GET | `/api/agents/runs/{id}/events` | 持久化事件日志 |
| GET | `/api/agents/runs/{id}/candidates` | 已计算候选、调整字段、比较指标 |
| GET | `/api/agents/runs/{id}/selection` | 当前 Agent 工作版选择与版本号 |
| POST | `/api/agents/runs/{id}/advance` | 无 Worker 时手动推进一个步骤 |
| POST | `/api/agents/runs/{id}/pause` | 暂停后续步骤；当前计算完成后停下 |
| POST | `/api/agents/runs/{id}/resume` | 恢复暂停/失败/过期租约任务 |
| POST | `/api/agents/runs/{id}/cancel` | 取消后续步骤 |
| POST | `/api/agents/runs/{id}/decision` | checker 人工采用单剖面工作版或拒绝，必须提交预期当前快照和说明 |

`POST /api/agents/runs` 请求示例：

```json
{"result_hash":"<verified-sha256>","max_candidates":3,"model_mode":"off","client_request_id":"manual-20261106-001"}
```

`decision` 只接受 `adopt_for_working_copy` 和 `reject`，且要求 `acknowledgement=true`、至少 12 字符的 `comment`、`expected_active_snapshot_hash`。不提供任何由 Agent 或大模型调用的正式签发端点。

## 6. 部署和操作

### Windows

解压 V3.88.0 到新目录，先安装原项目 Python/npm 依赖，再执行：

```bat
RUN_V3_88_0_VERIFY.bat
RUN_V3_88_0_DEMO.bat
```

正常产品启动使用原 `start-windows.bat`。Agent 默认以 `PITGUARD_AGENT_WORKER=embedded` 在 FastAPI 生命周期中启动；服务器关闭后，未完成任务保存在独立 `pitguard_agent_v388.sqlite3` 中。采用单独服务运行时，先在 API 服务环境设置 `PITGUARD_AGENT_WORKER=external`，再启动 `RUN_V3_88_0_AGENT_WORKER.bat`，避免与嵌入式 Worker 重复占用计算资源。主项目其他耗时任务仍继续由旧 TaskManager 管理。

### Linux/macOS

```bash
sh RUN_V3_88_0_VERIFY.sh
python3 scripts/run-v388-agent-demo.py
# 如选用独立 Worker：
PITGUARD_AGENT_WORKER=external PYTHONPATH=services/api python3 -m app.agents.worker --loop
```

默认 Agent 数据库路径在原 `PITGUARD_DB_PATH` 所在目录旁，亦可通过 `PITGUARD_AGENT_DB_PATH` 指定到可写数据盘。此文件不应随软件发行包覆盖。后台服务读写权限和备份策略应与工程数据库一致。

前端离线编译需具备完整 npm 依赖缓存，之后执行 `cd apps/web && npm ci && npm run build && npm test`。在当前封闭验证环境中缺少部分 npm 安装包，本版仅验证全量 `.ts/.tsx` 文件的语法转译；上线前必须完成浏览器实际交互与生产构建验收。

### 合成演示记录

`RUN_V3_88_0_DEMO.bat` 从内置数值验证例独立计算 3 个候选，生成 `verification-output/agent-v388-demo/agent-demo-result.json`。本例只检验程序流程，不声称工程参数真实。输入为水平地面、单排桩、单道无预力支撑；不能替代螺屿村原项目现场数据。

## 7. 分项完成与后续

| 事项 | V3.88.0 状态 |
|---|---|
| Agent 角色登记/职责隔离 | 完成；A5/A6 可执行，其余待实现 |
| 不可变输入、候选创建与完整复算 | 完成，复用 V3.87.28 原计算 |
| 持久化、任务租约、暂停/恢复、事件追踪 | 完成专项测试，仍须进行多机长时运行实测 |
| L0/L1 工具白名单与 L2 人工选择 | 完成，可在单剖面 Agent 工作选择中使用 |
| LLM 网关 | 已实现 opt-in 结构化解释和失败回退；未验证付费供应商实际 API |
| 前端 Agent 面板 | 已接入单排桩工作台；待完整 npm 构建及人工 GUI 验收 |
| 全坑 Agent 多专业联动 | 未完成 |
| 规范全项自动闭合及正式施工图发行 | 未完成；正式发行保持禁止 |

建议 V3.88.1 优先引入有证据的参数重新确认、失败约束自动定位与构件级候选工具；同时建立 30–50 个真实/独立数值校核算例的评估集。取得现场水文、圆桩裂缝规范适用方法、FRWS 原生五阶段数组之后再实施各专项真实工程对照。
