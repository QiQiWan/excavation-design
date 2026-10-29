# PitGuard V3.87.26：P1-P4工程标准化与分项验收

日期：2026-10-29；基线：V3.87.25。适用对象：单排灌注桩、一道内支撑的受限工作流程。

## 1. 本轮工作与状态定义

本轮将 P1（水力）、P2（圆桩）、P3（螺屿村3-3）和 P4（同源成果）联成统一的证据验收合同，保留上一版真实数值引擎及签发门禁。新增能力包括：

- P1：不利施工阶段内外水头组合逐个运行原数值渗流问题；固定网格间距进行远场计算域敏感性核对；独立稀疏求解检查原数值问题，不推定现场边界已正确。
- P2：圆截面弯曲边缘拉应力与人工弹性公式对照；适用裂缝等级、组合与轴剪覆盖继续单列，未经可靠方法验证时不产生正式合格结论。
- P3：五阶段全部有符号位移、弯矩、剪力、双侧土压力、支撑反力及安装参考位移按同测点、同符号和同阶段比较。安装前可显式申报无活动支撑，空反力不伪装成零测值。原始数值缺失则阻断 RMSE。
- P4：工程师逐阶段记录原始依据编号、方法版别、独立数值/手算证据、失败反例与复核意见；审核记录一经提交以结果哈希和自身 SHA256 保存。合成验证例可从单一封存结果生成 DOCX/PDF/DXF/CSV 和二级验收索引。

状态说明：数值计算 `analysisStatus=complete` 只说明求解完成；证据审查 `assessmentStatus` 说明对应方法和证据质量；`formalConstructionIssue=false` 始终禁止正式签发。受限工作版附显著声明“不得用于施工”。

## 2. P1 水力：输入、方法、独立验证与发行边界

### 原始输入与量纲

`HydraulicAuditInput` 必须明确项目ID、剖面ID、安全等级、内外水头（m）、帷幕嵌固深度（m）、计算域深度及宽度（m）、各层上下界（m）、水平和竖向渗透系数（m/s）、浮重度（kN/m³）、概念流路、潜在破坏类型和依据登记。施工不利水头以 `adverseHeadScenarios` 分项录入，须有不同场景ID、内外头值和明确理由。其数值结果属于独立工况求解，绝非在原梯度上乘系数。

### 方法与验证

JGJ 120-2012 第4.2.6条、第7章和附录C：先判断均质透水层、承压含水层、止水帷幕和施工降水条件是否适用条文解析式。存在复杂非均质分层时，二维稳态渗流有限体积求解可用于计算调查，并应按破坏模式另行确认土体临界条件、远场水文边界、施工扰动及监测。主数值核与独立 SciPy 稀疏求解针对**同一边界值问题**作代数一致性复核；它不能验证两个求解器共同使用的错误地质边界。新增 48×32 与 72×32 保持等网格间距的远场影响敏感性，以及各水头场景的再求解记录。

### 验收与负例

- 手算/独立证据：相同二维问题的出口梯度、残差和两网格比较；单位、输入哈希、独立实现方法须记录。
- 反例：异常增加坑外水头触发高梯度筛查失败；无现场来源、逆向流超出本算例适用范围、边界敏感性未做或独立复核失败均不能宣称通过。
- 工程师记录：逐项确认抽水试验/水位观测、层间水力联系、帷幕底标高与渗流路径、现场不利水位、内蚀/管涌/流土/承压突涌对应判据。
- 结论：本版 `formalDesignReady=false`。需有项目真实水文模型和经确认的破坏临界条件、完整不利工况及独立专业校核后，另行按适用强制性标准验收。

## 3. P2 圆桩：材料、截面、同步内力与正常使用

### 原始输入

圆桩桩径、混凝土等级及强度取值、纵筋/箍筋等级与强度、配筋直径、根数、轴线位置、箍筋净保护层、连接及锚固条件，均须关联具体勘察或结构设计来源。轴力正负号、弯矩/剪力及控制阶段必须为同组合、同一深度、同一次求解。SLS、ULS 分别冻结。

### 方法与验证

JGJ 120 第4.3.2条及附录B和适用 GB/T 50010 方法，是圆形支护桩强度、构造与正常使用验算的基础。已有圆截面轴弯数值积分、受限 N=0 的抗剪、钢筋笼间距保护层和开裂截面钢筋应力，必须继续实施独立手算/积分与不合格反例。新检查：未开裂圆截面边缘应力 `sigma_t=max(0,|M|r/I - N/A)`（压轴力为正），可用于有明确定义等级和标准/准永久作用的受限拉应力筛查。

### 当前方法缺口

GB/T 50010 常规裂缝宽度公式的适用截面不能直接推定包括任意圆形桩截面；圆桩 III 级裂缝宽度继续标记 `width_method_required`。明显轴力下的斜截面承载力不能默认为 N=0 的方法已完成。抗剪、裂缝、连接和构造按独立项目分别报告。工程师确认的企业经验值只能作为候选，不能替代规范设计和审核。

### 反例与复核

高弯矩触发拉应力不满足；钢筋净保护层、锚固长度和箍筋间距可各自失败；缺持续作用或轴力来源时为待补证据；所有检算应有相应计算版本和材料来源。正式施工图签发继续关闭。

## 4. P3 螺屿村3-3：原文事实、缺项和全通道验收

### 原始计算书可确认的记录（物理页码）

原文件 `1-文本，2-计算书.pdf` 已按原始文件字节实际计算 SHA256，完整摘要和页码在独立验证包 `P3_luoyu_original_pdf_byte_receipt_and_facts.json`。哈希仅证明取用文件字节一致，不确认工程解释。

| 属性 | 原记录/工程解释 | PDF物理页 | 备注 |
|---|---:|---:|---|
| 桩径、桩距、桩长 | 0.70m、0.90m、6.60m | 32 | 原文 |
| 桩顶低于原地面 | 1.30m | 32 | 原文 |
| 初挖、最终开挖 | 2.05m、4.90m | 33 | 施工五工况 |
| 临时支撑与换撑标高深度 | 1.625m、4.60m（原地面向下） | 33 | 坐标须核实 |
| 冠梁等效刚度 | 22.7 MN/m² | 33 | 对单桩支点刚度换算未知 |
| 支撑等效刚度 | 32.3 MN/m² | 33 | 原资料单位及分担口径需确认 |
| 3、5阶段弯矩极值 | 206.7、176.9 kN·m | 34 | 图示峰值，不能生成逐点数组 |
| 设计弯矩 | 258.4 kN·m | 36 | 206.7×1.25四舍五入；仅算术核对 |

### 仍需工程资料，按优先级补充

1. 桩底边界：冠梁连接、桩趾嵌固/自由/弹簧，结构实际约束及土的等效刚度来源。
2. 冠梁：`22.7 MN/m²` 的原始定义、沿墙参与宽度、每根桩影响宽度和实际等效弹簧单位。
3. 对侧墙：双方 EI、位移历程、土弹簧/端部约束；杆件 EA/L、长度、角度、预加力、安装间隙及安装位移参考。
4. 坡台、有限邻载与混合土水：坡顶坐标和高度、各层强度和重度、水位/土水计算口径，以及坡台侧压力采用的已核准方法。
5. **真实 S1-S5 逐点结果文件**：统一原地面向下 `depthSiteM`（m），位移 `displacementMm`（mm），每根桩的 `momentKnM`（kN·m）和 `shearKn`（kN），含支点两侧标签 `above/below`，坑内外 `pressureKpa`（kPa）以及每一支撑的 `reactionKnPerPile`（kN）、`referenceMm`（mm）。阶段无活动支撑时须显式记录 `activeSupportIds: []` 和 `strutReactions: []`。

### 同条件五阶段比较

只对共同测点、相同符号、相同单位和相同节点侧的数组逐值算 RMSE 与最大差；缺任一阶段、压力一侧、支撑安装参考/反力、重复测点或假定来源则拒绝误差评价。原 PDF 提供极值和图线，但无完整原始节点数组。当前可计算的条件化模型不能被标为原工程重现；即便合成数组恰好一致也仅能达到 `all_channels_compared_source_peer_review_pending`，还需要对原始字节和两种方法的独立审核。

## 5. P4 计算书、图纸、复核签认

先保证单一冻结 `snapshotHash`、单一 `resultHash`、单一预审记录哈希。只有 `checker/admin` 可提交阶段校审和下载加上验证索引的工作版成果。阶段记录包含 `methodRevision`、`declaredInputSourceReferences`、`declaredIndependentVerificationReference`、`declaredNegativeCaseReference`、`notes`、审查账户与事件时间。来源声明在字节真实性未确认之前保持 `sourceReferencesAuthenticated:false`。P1—P4 每阶段可选择退回修改或确认工作版限制，二者均无正式发行许可。

生成包（`P1_P4_engineering_review.pdf`、`P1_P4_gate_index.json`、`P1_P4_phase_reviews.json`、`calculation.docx`、`section_A3.pdf`、`section.dxf`、`stage_envelopes.pdf`、`rebar_demand.csv`、结果 JSON、哈希清单）均只消费封存结果，导出不修改工程方案。原提交的正常工作版输出接口保持兼容。

## 6. API合同与示例

- `GET /api/engineering/one-strut/assurance/acceptance/methods`：P1-P4 依据及必要条件。
- `POST /api/engineering/one-strut/assurance/acceptance/plan`：输入 `UnifiedAuditInput`，输出每阶段 `assessmentStatus`、`outstanding`、五项证明声明与正式门禁。
- `POST /api/engineering/one-strut/assurance/acceptance/review-phase`：`checker/admin`，输入结果身份、阶段、依据、反例、独立验算和意见，输出不可修改 `phaseReviewHash`。
- `POST /api/engineering/one-strut/assurance/acceptance/dossier`：`checker/admin` 提供预审及阶段记录身份，在验证后返回工作版 ZIP，HTTP 头 `X-PitGuard-Formal-Issue:false`。
- `POST /api/engineering/one-strut/assurance/luoyu-3-3`：原项目同条件准入和完整通道核对。
- `POST /api/engineering/one-strut/assurance/hydraulic`：水力输入、证据、不利水头及独立核验。

输入由 Pydantic JSON Schema 强校验，内部单位明确；不存在的来源以缺项列出。具体 Schema、负例和样例 JSON 位于独立验证包，不随运行程序发布。

## 7. 模块依赖与部署

```text
services/api/app/engineering/single_braced_pile/
  contracts.py / analysis.py / repository.py / router.py / artifacts.py
  normative/                 # 规范公式与受限适用域
  real_project/
    luoyu_three_three.py     # 条件化研究模型
    complete_curve_protocol.py  # 五阶段全通道严格比较
  assurance/
    model.py                 # 证据字段合同
    hydraulic.py             # P1水力证据、场景和边界敏感性
    circular.py              # P2圆桩适用性审查
    luoyu.py                 # P3原项目条件审核
    quality_protocol.py      # P1-P4统一发行门禁
    source_bytes.py          # 原始文件字节与安全路径验证
    dossier.py               # P4工作版同源成果
    router.py                # 方法目录和受控审查API
apps/web/src/features/single-strut/
  OneStrutWorkbench.tsx
  AssuranceGatePanel.tsx      # P1-P4校审入口
```

部署使用 `start-windows.bat` 或 `start-linux.sh`，端口按系统实际部署设置。运行源码包含 `packages/units/engineering-units.json`、Python 和 npm 锁文件、后端配置与启动脚本。不随包分发测试、原工程、勘察文件、数据库、日志、字体、第三方依赖或历史文档附录；详细测试另存独立验证包。

## 8. 当前能力及正式验收限制

| 阶段 | 本轮技术进展 | 正式验收情况 |
|---|---|---|
| P1 | 场景水头再求解、远场敏感性、同域独立稀疏求解 | 原场地水文边界与破坏判据未完成验证，未通过正式验收 |
| P2 | 圆截面拉应力核算、分项证据及负例 | 圆桩III级宽度和非零轴力正式抗剪方法未闭合 |
| P3 | 五阶段双侧压力、u/M/V、反力和安装参考严格比较 | 原工程完整数值数组及边界未确认，不能得出同条件误差 |
| P4 | 校审角色控制、阶段不可变记录、同源工作版成果 | 可以供内部工程复核，正式施工图发行始终为false |

本版验收说明只证明**明确数据和方法范围内的程序行为**。项目正式设计须继续完成所有适用的勘察、结构、渗流、施工与校审要求。

## 9. 复现实验与生产安装

后端相关及专项测试在解压 `PitGuard_3.87.26_Validation_And_Luoyu_Evidence.zip` 后运行：

```bash
# 在 PitGuard_3.87.26 目录
PITGUARD_SOURCE_ROOT="$PWD" PYTHONPATH=services/api pytest -q verification/tests
python scripts/verify-release-consistency.py
```

测试包与源包放置为相邻目录时，需将 `verification/` 复制到源码目录旁边，或按实际位置设置路径。具体出包后脚本会提供精确运行命令。生产先安装锁定的 Python 和 npm 依赖并完成 `npm run build`、HTTP/GUI 端到端测试；本轮仅完成源码 TSX 语法转译，完整前端构建未在当前缺依赖环境完成。暂不允许把数值回归通过替代真实工程审核。
