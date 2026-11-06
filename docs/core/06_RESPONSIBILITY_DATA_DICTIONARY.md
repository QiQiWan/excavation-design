# 设计、施工与现场责任数据字典

## DesignControlStage

设计单位维护的设计成立边界。核心字段：开挖控制标高上下限、必需支撑、允许未激活支撑、水位与荷载限值、预加轴力范围、超挖限值、刚度折减限值、控制点和设计情景。禁止记录实际日期和现场实测值。

## DesignScenario

用于设计包络和鲁棒性筛查的情景。自动情景包括基准、延迟支撑、超挖、预加轴力、水位、超载、刚度折减和构件异常。只有完成正式复算的结果才进入构件包络。

## ConstructionPlanStage

施工单位提交的计划对象，必须绑定一个设计控制工况。计划可以包含日期、计划标高、支撑、预加轴力、水位、荷载和专项方案版本。计划不会修改设计对象。

## FieldExecutionSnapshot

施工、监理或监测责任方提交的时点状态，包括实际开挖标高、实际激活支撑、实测预加轴力、水位、混凝土强度和监测快照。快照采用追加式保存。

## DeviationEvent

系统比较现场、计划和设计允许域后生成的偏差对象。记录设计限值、计划值、实测值、影响工况、影响构件、严重度、暂停建议、复算要求、设计回复要求和闭环状态。

## 数据类型

- `design_value`：设计计算采用的确定值；
- `design_limit`：施工必须满足的设计边界；
- `design_assumption`：设计阶段尚未确认、已纳入不利情景的假定；
- `contractor_plan`：施工单位计划值；
- `field_observation`：现场实测或经核验事实。

不同类型的数据不得在同一字段中相互覆盖。

## 门禁状态补充

- `DesignControlStage.dataStatus`：`draft` 不能进入正式设计发行，`approved` 可计算和校审，`frozen` 用于已批准设计基准；
- `ConstructionPlanStage.approvalStatus`：只有 `approved` 可用于现场阶段放行；
- `FieldExecutionSnapshot.quality`：`provisional` 只参与趋势预警，`verified` 才能作为现场放行证据，`rejected` 不得用于工程判断；
- 施工计划符合性 C 级表示超出直接允许域但可通过增量复算处理，完成复算前属于施工准备阻断项；
- 设计情景 `approvalStatus=approved` 表示已纳入正式包络范围，不代表该情景已经完成结构复算。
