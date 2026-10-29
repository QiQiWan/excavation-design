# V3.87.17 证据索引

本目录同时保存最终验证和发现问题时的历史记录。应按下列身份读取，不能把开发中间结果混入最终成果。

## 最终计算结果

- 冻结输入：`2223478185afda316bc8fc9585a4998cf4900f88108b94337ef064f9f8a989fd`。
- 封存结果：`69f2e1a3a784947b6a1f2e13b31d6bd704a71597effc217a6659d2def8dbe8f9`。
- 完整输入/结果：`../../../examples/frozen-section/validated-snapshot.json`、`validated-result.json`。
- 汇总状态、交付文件哈希及验证范围：`../V3_87_17_VALIDATION.json`。

## 当前验收证据

| 文件 | 验证内容 |
|---|---|
| final_gates/focused_gate_results.json 及配套日志/XML | 103 项剖面后端 + 145 项保留基线回归，41 项定向前端，类型/生产构建和生产资源检查 |
| full_frontend_results.json、full_frontend_tests.log | 完整前端实际运行：68 通过、10 失败、4 异常 |
| full_frontend_baseline_comparison.json | 失败用例身份及异常消息与 3.87.16 基线逐项一致；不声称全应用全绿 |
| soil_direction_cone_verification_current.json | 最终两内核、默认及 K0=Ka 完整历史、粗细网格、混合精度及源码哈希 |
| soil_direction_cone_probe_result.json | 两个方向锥检查器与独立 Fraction 精确方法，1,029 配置，0 差异 |
| verify_soil_direction_cone.py | 上述独立精确检查的可运行脚本；不进入生产计算 |
| soil_solver_p2_closure.md、section_final_review.md | 力学判据/超时恢复的审查发现、修复和窄范围复核 |
| independent_circular_section_check_current.json | 绑定最终封存结果的圆截面独立积分与求根，12 项检查 |
| independent_circular_section_check.py | 独立圆截面审查脚本，依赖 SciPy，不调用产品承载力值作为参考答案 |
| delivery_review.json | 最终成果的哈希、页数、字体、尺寸、比例、同源数值及视觉检查 |

## 开发中间与历史记录

`frontend_http_verification.json` 和 `frontend_http_recovery_verification.json` 是开发阶段真实完整应用 HTTPS 调用记录，保留各自当时的 resultHash：认证、输入校验、冻结、计算、保存、恢复和二进制 ZIP 下载均已执行。它们不冒充最终 69f2… 算例的文件哈希。最终源码的 API/storage 回归另见 `final_gates`。

名称包含 `_history` 的记录明确保留失败和改进前状态。`soil_mesh_refinement_centered_beam_current.json`、`soil_solver_verification_current.json` 是各自修复阶段的检查记录；最终主/独立内核身份以 `soil_direction_cone_verification_current.json` 和最终结果内源码哈希为准。`soil_solver_review.md` 保留检查演进。

`legacy_backend_regression_incomplete_history.log` 是未完整留存摘要的早期运行，不作为通过证据；完整的 145 项回归结果已包含在 `final_gates/focused_backend.xml` 和完整日志中。

`soil_method_research_primary_sources.md` 记录方法原文与限制，`frontend_review.md` 记录界面及 API 审查。原 A3-3 的事实/解释/缺项见 `examples/frozen-section/A3-3-reference-audit.json/.md`，不得将参数缺失解释为原项目验算失败或通过。
