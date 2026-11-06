# PitGuard V3.87.28 集成实施与验收记录

基线：用户上传 `PitGuard_3.87.27_Runtime_Source(1).zip`（428 个归档条目）。本版直接修改其运行代码与工作台，保留原有设计计算内核和已封存结果身份算法。日期：2026-11-06。

## 1. 工程目标与边界

重点把 P1（现场水文）、P2（圆桩）、P3（螺屿村原生数值）、P4（工作版档案）证据接入真实程序入口，分别保存“数据完整性”“数值方法”“工程适用性”“成果签发”四类结论。软件测试通过不能替代勘察资料复核、GB 55003/JGJ 120 等适用条文验算或设计院校审。所有新增响应明确 `formalDesignReady=false` / `formalConstructionIssue=false`。

本版新增的外部数据均为提交者提供或在本机独立校验的文件字节，不做原文件签发人身份、现场观测真实性、FRWS 计算参数或第三方项目条件的自动认证。

## 2. P1：现场水位配对和抽水试验

### 2.1 现场水位配对

API：`POST /api/engineering/one-strut/assurance/hydraulic/observations/validate`

输入中每条记录必须含 `project_id`、`section_id`、`stage_id`、`well_id`、`side`、带时区的 `observed_at`、水头 `head_m`（m）、`source_sha256`、`source_row`、`quality`。通过 `max_pair_gap_minutes` 限制配对时差；同一阶段内坑内、坑外一对一最优匹配，优先最大化可用配对数，再最小化总时差。多井记录未明确选择时输出 `well_selection_required`。失效和超时记录不参与最大水头差计算。

输出 `maxObservedPairedDeltaHeadM` 是指定观测集内的坑外减坑内最大配对水头差，不能用于直接声称设计不利水位已定。监测位置、仪器精度、场地边界、观测窗口及原始文件 SHA-256 均需独立验证。

### 2.2 承压含水层 Thiem 解释

API：`POST /api/engineering/one-strut/assurance/hydraulic/pumping-test/interpret`

只在承压、准稳态、均质各向同性、抽水井充分贯入、井损可忽略且两观测井半径/降深方向符合条件时计算：

`T = Q ln(r_far/r_near) / [2π(s_near-s_far)]`，`K = T/b`。

`Q` 为 m³/s，`r`、`s`、`b` 为 m，输出 `T` 为 m²/s、`K` 为 m/s。任一物理条件缺失返回 `outside_Thiem_method_scope` 且无 `K`。该计算不推断竖向 K、储水系数或设计最不利水头。工作台提供输入 Schema，数据错误被明确拒绝。

## 3. P2：圆桩裂缝双报告与欧拉参考

### 3.1 双份裂缝报告

API：`POST /api/engineering/one-strut/assurance/circular/crack-crosscheck`

要求同一冻结 `result_hash`、`snapshot_hash`、SLS 组合、阶段、深度、节点侧、限值；两份报告必须由不同复核人提交且原始文件散列不同。两类明确容差 `absolute_tolerance_mm` 与 `relative_tolerance` 同时满足，且两份报告均不超裂缝限值，才返回 `numerically_agreed_method_unqualified`。超过限值、数值不一致分别返回可处理的失败状态。结果不会改变原系统圆桩裂缝规范适用性门禁。

### 3.2 二阶效应参考

API：`POST /api/engineering/one-strut/assurance/circular/second-order/reference-screen`

`Ncr = π²EI / Le²`，输入 `EI` 为 kN·m²、`Le` 为 m、轴压 `N` 为 kN。仅检查 `N/Ncr` 对理想弹性柱的参考状态。实际受土体弹簧、分阶段开挖、支点约束影响的构件，必须使用经确认的结构计算方法；即使本参考筛查低于临界值，也不能宣布实际二阶验算通过。

## 4. P3：螺屿村 3-3 五阶段原生数值

API：

- `GET /api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/blank-template`
- `POST /api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/validate`（multipart 字段 `upload`）
- `POST /api/engineering/one-strut/assurance/luoyu-3-3/native-numeric/compare`（multipart 字段 `upload`、`candidate_json`）

ZIP 严格包含 `metadata.json`、`stages.json`、`curve.csv`、`pressure.csv`、`brace.csv` 五件。必须完整覆盖 S1—S5；每阶段 `activeSupportIds` 明确支撑状态，零活动支撑必须使用空反力数组。CSV 字段及单位：

| 文件 | 必需表头（顺序固定） | 数值口径 |
| --- | --- | --- |
| `curve.csv` | `stageId,depthSiteM,side,displacementMm,momentKnM,shearKn` | 原地面向下 m；u mm，M kN·m，V kN；节点侧 above/below |
| `pressure.csv` | `stageId,depthSiteM,side,pressureKind,pressureKpa` | 原地面向下 m；坑内/坑外 kPa |
| `brace.csv` | `stageId,id,reactionKnPerPile,referenceMm` | 单桩反力 kN/桩；安装参考位移 mm |

`metadata.json.fileDigests` 必须有上述 4 个数据文件的实际 SHA256；ZIP 自身 SHA256 由服务端重新计算。上传端强制限制 16 MB ZIP、48 MB 解压预算、40000 行/单 CSV、重复测点/缺侧别/错单位/非有限值拒绝。可执行命令：

```bash
python scripts/pack-luoyu-native.py --source ./native_filled --output ./native_review_001.zip
```

CLI 负责重新计算文件散列、重新打包、执行同一严格检查。空白模板没有数值，必须填写真实原生数值后才能通过，不能自动制造原 FRWS 工程数据。

P3 比较调用原有 `complete_curve_protocol.py`，逐阶段核对 u/M/V、双侧压力及支点安装反力/位移。比较即使所有数值相同，仍返回原工程方法和来源审核未完成。缺原工程桩底、冠梁刚度、对侧墙刚度、坡台与混合土水边界时不得通过真实同条件验收。

## 5. P4：内部成果清单 + 可信公钥签署

API：`POST /api/engineering/one-strut/assurance/acceptance/verify-package`，multipart 字段为 `upload`，HTTP 上传限制 50 MB。内部校验使用标准库、无私钥、无需外部网络。

验证清单：

1. 检查 ZIP 目录、条目数量、重复路径、解压预算、文件字节、外层 `manifest.json` 和 `original_export_manifest.json` 的完整成员集合。
2. 从 `frozen_input.json` 重新计算规范化 SHA256，必须与 `snapshotHash` 一致；检查 `sealed_result.json` 内嵌快照及剔除 `resultHash` 后的结果规范化摘要；核对 `review_record.json` 的限定校审范围与规范化摘要。
3. 对 `P1_P4_phase_reviews.json` 每份校审记录重新计算摘要，要求与 `P1_P4_gate_index.json` 的阶段记录索引精确一致；禁止任意阶段声称正式签发。
4. 离线签署使用 Ed25519，签名记录包含整个 ZIP 的 SHA256、大小、输入/结果/校审身份，验证方必须提供通过独立渠道获得的可信公钥；没有可信公钥的签名不合格。

Windows 使用：

```bat
set PITGUARD_SIGNING_PASSWORD=请替换为仅本机使用的长口令
RUN_P4_DOSSIER.bat audit D:\review\P1_P4_workcopy.zip
RUN_P4_DOSSIER.bat keygen --private-key D:\keys\reviewer_private.pem --public-key D:\keys\reviewer_public.pem
RUN_P4_DOSSIER.bat sign D:\review\P1_P4_workcopy.zip --private-key D:\keys\reviewer_private.pem
RUN_P4_DOSSIER.bat verify D:\review\P1_P4_workcopy.zip --trusted-public-key D:\keys\reviewer_public.pem
```

Linux/macOS 使用：`python services/api/app/engineering/single_braced_pile/assurance/dossier_signature.py <action> ...`。密钥签署依赖 `python -m pip install 'cryptography>=44,<47'`（离线 ZIP 完整性检查可不安装）。私钥始终离线保存；对外只提供 ZIP、分离式 `.sig.json` 和由验签者预先信任的公钥。口令应通过受控本机环境变量或密钥管理工具提供，不能写在仓库或提交文件中。

独立数字签名能够提供归档字节及持有密钥者的证明，但无法直接证明原始勘察文件签章、工程师法定签字或规范符合性。正式工程发行许可继续关闭。

## 6. 运行、兼容性和测试

新增模块只处理证据，**没有修改 V3.87.27 的求解方程、施工阶段推进、支点反力或配筋效应算法**。原计算、冻结结果、工作版成果 API 路径保持可用。新版新增 API 自动进入主应用既有鉴权中间件。

```bash
python scripts/verify-release-consistency.py
PYTHONPATH=services/api python -m pytest -q verification/tests
python -m compileall -q services/api/app
uv lock --check --offline --project services/api
```

Windows 一键：`RUN_V3_87_28_VERIFY.bat`；Linux 一键：`sh RUN_V3_87_28_VERIFY.sh`。前端构建应在能访问 npm 仓库或预装完整依赖的目标机上执行 `cd apps/web && npm ci && npm run build && npm test`。当前验证环境离线 npm 缺失 Vite/React/Three 等锁定 tarball，因此只完成全 55 个 `.ts/.tsx` 文件 TypeScript 语法转译，**未宣称前端生产构建通过**。

基线缺少完整真实勘察、FRWS 原生五阶段数据、所有适用规范验算、独立设计校审记录，必须继续补齐。版本标记为工程验收工作版，不得用于直接出具正式施工图。
