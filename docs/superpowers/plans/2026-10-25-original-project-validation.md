# 原项目计算条件与工程检查 Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent modules; root integrates shared models, services and exports. Execute the already authorized plan without intermediate permission prompts.

**Goal:** 固定有来源的原项目条件，实现有依据的方法扩展和全阶段比较，并从同源结果补充工程配筋检查与可复核成果。

**Architecture:** 条件合同与可求解输入分离。独立作用、曲线交换、工程配筋模块分别负责明确的工程语义；共享模型/求解/报告由root集成。v1身份兼容，v2显式启用新选项。

**Tech Stack:** Python/Pydantic/NumPy/OpenSees、FastAPI、React、DOCX/ReportLab/ezdxf。

**Spec:** docs/superpowers/specs/2026-10-25-original-project-validation-design.md

## Global Constraints

- 原资料与旧冻结快照不改写；原值、推导、未确认分离。
- 禁止默认补E、m规则、有效宽度、边界、单位、N、SLS组合及设计人确认。
- 所有计算采用显式单位和适用域；未知/超范围不得变成通过。
- 方法/曲线/配筋/成果必须引用冻结身份；正式发行保持review_required。
- 不改变既有全基坑引擎的土体抗力限制。

## Review Focus

- v1快照/旧成果在新增默认字段或算法版本后被错误判为篡改。
- 原地面与桩顶1.3m转换、水位负局部坐标以及覆土量被静默裁掉。
- 分层土水合算/分算造成水压力漏算或重复。
- 剪力跳跃、相反符号和每米/单桩换算被插值或自动猜测掩盖。
- 纯弯通过、未知SLS或纵筋轴线保护层被误当完整配筋与净保护层通过。

## Task 1 — 原项目条件与全曲线协议

**Owner:** project_conditions；共享原资料只读。
**Files:** app/section/reference_contract.py、reference_curves.py、tests/test_section_reference.py、test_section_reference_curves.py；examples/project-reference。
**Interfaces:** 条件生成/冻结/验证；曲线包冻结、来源/单位/阶段校验、与封存section结果比较；具体公开签名由实施brief固定。
- [x] 原值/推导/未确认、负水位、双标高、单位未确认、来源错配及跳跃插值的失败测试。
- [x] 实现合同与交换协议，生成A3-3条件账本和原程序导出空模板。
- [x] 真实记录缺项；验证协议fixture不得冒充原程序数值导出。
- [x] 独立审查与定向回归。

## Task 2 — 明确适用域的土压力与地面作用

**Owner:** soil_differences。
**Files:** app/section/geotech_actions.py、tests/test_section_geotech_actions.py；方法来源/独立算例。
**Interfaces:** 分层总/有效竖向应力，条带/矩形竖向应力增量，Rankine合算/分算压力，干燥均质折线坡面主动楔体合力。
- [x] 解析条带、独立二维积分、合分算φ=0等值与混层水压计一次、平坡楔体退化的失败测试。
- [x] 实现纯函数与明确方法ID/限制，禁止楔体主动合力隐式变成完整墙体模型。
- [x] 输出独立核验记录并审查。

## Task 3 — 工程配筋检查

**Owner:** rebar_engineering。
**Files:** app/section/engineering_rebar.py、engineering_models.py、对应tests；规范来源/手算。
**Interfaces:** 显式工程检查输入与check_engineering_reinforcement输入/输出，独立于原v1纯弯函数。
- [x] 圆截面N-M、规范允许的圆截面抗剪、净保护层/间距/箍筋、锚固和缺SLS/缺裂缝方法的失败测试。
- [x] 采用原始规范出处实现有依据的检查；未核实规则保持未验证。
- [x] 以独立积分/解析值复核，保存逐项检查依据、作用来源和不足。

## Task 4 — 集成冻结输入、施工状态与结果

**Owner:** root。
**Files:** models.py、preprocess.py、solver.py、independent.py、service.py、benchmarks.py；API/storage、前端入口。
- [x] v1哈希兼容和v2身份变更测试；不允许v1偷偷启用新行为。
- [x] 显式新作用/水土选择/历史选项接入主核与独立核，阶段安装事件仍不可混入其他作用变化。
- [x] 将条件、全曲线比较和工程检查接到冻结结果，保留旧导出校验逻辑。
- [x] API/前端提供条件表、交换包导入/比较和同结果下载；编辑失效及迟到响应检查。

## Task 5 — 同源成果、审查与交付

**Owner:** root/成果与独立审查代理。
**Files:** export.py、render_helper.py、scripts、docs/releases、examples。
- [x] 报告/CSV呈现原值与推导、方法与来源、全阶段曲线、工程检查及缺项；详图仅采用有依据的内容。
- [x] 用最终源码执行新验证例、旧基线、真实OpenSees与导出；独立审查关键数学和状态合同。
- [x] 逐页检查，核对尺寸/比例/公式/包络/配筋/哈希；记录全套测试实际状态。
- [x] 更新说明与复现命令，准备完整源码与成果交付；提交与归档身份见交付外层清单。

## 执行记录

基线c8793e8保持干净；已复用上一轮同提交的248项后端/41项定向前端通过证据。完整前端既有10失败/4异常已记录，不把它们改写成通过。三个研究域使用独立文件所有权并行，root集中处理共享文件，减少串行等待且避免并发编辑冲突。

## 最终执行状态（2026-10-25）

上述勾选表示本轮明确适用域内的实现、失败状态和验证链完成，不代表原FRWS项目或施工成果已验收。最终门槛为367项后端、55项前端、构建及生产包检查通过，真实OpenSees已执行；另有3种历史规则×7阶段混合土水验证全部通过。计算书16页、图册10页已检查。最终快照0c8810fbacd0901852c249ed224fc9965a04e45bfd25f8e22f502e58700c93d4；结果3b83981156de969254ba56c72651392b2d539141a3cc998f798affcf48012f55。

原项目337字段账本仍有25项关键条件未闭合，原曲线模板为0样本。分层含水坡台全压力模型、原软件卸载/重置规则确认、适用圆截面裂缝、扩展轴弯及真实连接构造仍待下一轮；正式工程发行保持false。审查修复与最终证据见docs/releases/V3_87_18_ORIGINAL_PROJECT_REVIEW.md和V3_87_18_VALIDATION.json。
