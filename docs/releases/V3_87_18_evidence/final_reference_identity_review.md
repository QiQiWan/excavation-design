# 最终原项目参考身份核对

核对时间：2026-10-25。仅读取最终文件并校验内容哈希、字段分类和交叉引用；未修改源码、未运行求解或重新计算曲线比较。

## 核对对象与结论

- 源例：`examples/project-reference/A3-3-reference-contract.json`
- 最终成果目录：`/workspace/scratch/fd5d8a28c64e/work/v3_87_18_delivery_build/section_VERIFY-METHODS-02_0c8810fbac_3b83981156_2lhy_hg0`
- 同时读取最终目录的`reference_comparison.json`、`reference_contract.json`、`reference_curves.json`、`frozen_section.json`、`section_result.json`。

**未发现计数、内容哈希或文件绑定身份不一致。** 最终合同与examples合同逐对象相同；合同与曲线通过其结构和哈希校验；比较记录去除comparisonHash后的规范JSON哈希与所载comparisonHash一致。未重算比较算法。

## 最终数量

|项目|数量|
|---|---:|
|参数总数|337|
|original_fact 原文事实|198|
|derived_interpretation 显式推导|51|
|unconfirmed 未确认|88|
|关键条件|31|
|关键条件待补或待确认|25|
|其中原条件未提供|23|
|其中推导仍需来源确认|2|

两项待确认推导是模型坐标约定、坑外水位各阶段保持不变的解释。25并非全部88未知字段的数量，而是31个关键条件中尚不能闭合的数量。

## 身份与状态

|身份|完整值|
|---|---|
|caseId|PROJECT_A_SECTION_4_5_3_3_LONG_PILE|
|contractHash|76b1bd8e73a79e513dad9c4dbdf57ef21ef09dea5cd65123d3279ada01b715eb|
|curveBundleHash|12e65d9a533bb7f031450a8a5f163301cbd9e13d4c23bbc6fcd5f5313ab5f4fb|
|comparisonHash|7f85fb030107954bd58212bab9efc9b805eb46257b1a41448bb44701f87e5d42|
|snapshotHash|0c8810fbacd0901852c249ed224fc9965a04e45bfd25f8e22f502e58700c93d4|
|resultHash|3b83981156de969254ba56c72651392b2d539141a3cc998f798affcf48012f55|
|analysisHash|dfcb4353e1af427a9fc8244fc1f25793a8e28eca3f4c6bfb32d2e6a2d5d6c9f5|
|candidateBundleHash|2da30e801df2642115753cfbc4bfbfe2f13751bdbf4d47d8755c43b67aa9442e|

快照输入中的reference_contract_hash、比较外层及report中的合同身份一致。比较外层curveBundleHash和report.referenceBundleHash均对应最终reference_curves.json。比较绑定的resultHash和snapshotHash与最终求解结果及冻结快照一致。

原曲线包具有S1–S5五阶段结构，但**数值样本总数为0**。所以missingStages=[]和missingChannels=[]只表明模板结构存在，不表明原节点数据已提供。最终标志正确保持：

- status=`conditions_mismatch`
- numericalStatus=`incomplete`
- sameConditionsVerified=`false`
- formalDesignReady=`false`
- fullCurveAcceptance=`false`
- displacementMomentShearComplete=`false`
- pressureProjectionComplete=`false`

阶段domain mismatch是已声明合成验证桩长10m与原参考桩长6.6m的条件差异，和candidate_is_synthetic共同阻断原项目认证；这不是最终文件串包或哈希错误。

## 用户下一步最少提供什么

最有效的是一次提供**原FRWS9.0计算工程完整打包 + 同次计算的参数/选项与数值结果导出**，无需重新整理现有PDF。

1. **原生工程及软件身份。** 提供生成该历史A3-3报告的可打开工程，保留原文件名、所有伴随文件及目录；提供关于窗口/版本号和build截图。不要仅上传DWG或重新输出PDF，也无需按任何猜测的扩展名改名。FRWS9.0原生扩展名尚未确认。
2. **原设置中未打印的选项。** 若不能完整导出，可用参数页/选项页截图，至少覆盖：桩E、EI折减及桩头/桩端边界；m算法、深度起点、分层接续、有效宽度、被动上限/脱开与卸载历史；分合算压力组成、水重度及逐阶段内外水位；放坡/台宽和有限邻载侧压力方法；冠梁22.7与主撑32.3的作用位置/组合；支撑预力、安装参考位移、换撑安装与拆撑之间的状态继承；M/V单桩或每延米口径、线反力到实体支撑轴力映射；原特征组合、设计系数及材料强度/配筋选项。历史规范版本应随原设置保留，不替换为现行值。
3. **五阶段完整数值结果。** 给出同一原工程同次运行的深度—坑内外压力—u—M—V表，以及支点水平反力和安装参考位移；若已有实体支撑N也一并提供并注明口径。保留节点左右侧、深度原点、符号、压力总/有效与组成、单桩/单位宽度、特征/设计口径，以及包络/控制阶段和配筋需求。可接受原软件数值表导出或带出处的数值转录；只有图示峰值不足以补全曲线。

若暂时只有原生工程，先提供完整工程和版本截图即可；之后可据实际界面定位必须补导出的选项。没有用户提供的证据，不自动补E、自动确认原算法或生成设计人签认。

## 验证范围说明

本次只读身份核对没有新增测试或计算。root提供的最终门槛结果为367项后端、55项前端及build全部通过；本记录不把该软件门槛解释成原FRWS同条件通过。
