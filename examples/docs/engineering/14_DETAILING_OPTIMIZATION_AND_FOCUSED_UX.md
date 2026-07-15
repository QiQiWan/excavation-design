# 深化优化、单位治理与聚焦型交互技术说明

## 1. 模块边界

V3.9 将深化能力拆分为四个相互独立、通过稳定数据契约连接的模块：

```text
逐根钢筋/预埋件几何
  ├─ 构造协调优化器
  ├─ 节点局部子模型生成器
  ├─ 钢筋笼吊装物流优化器
  └─ 工程单位注册表
```

前端只消费结构化结果，不在组件内部推导工程单位或重新计算结构指标。

## 2. 构造协调数据契约

一个问题组至少包含：

```text
issueId
embeddedItemId
hostCode
barGroupId
minimumActualClearanceM
requiredClearanceM
sourceCheckIds[]
candidates[]
```

一个候选至少包含：

```text
candidateId
action
predictedClearanceGainM
predictedClearanceM
geometryDelta
verification
structuralPenalty
constructabilityScore
score
```

候选应用后保存到：

```text
project.advancedEngineering.detailingOverrides
```

重新生成深化包时按原碰撞检查 ID 对应到候选，计算预测净距和残余缺口。没有验证条件或净距不足时不能判为 pass。

## 3. 节点子模型数据契约

每个节点输出：

- 六自由度筛选结果；
- 节点几何和材料规格；
- 接触参数；
- 设计变体；
- 推荐变体；
- 求解器输入文件路径；
- 工程边界说明。

输入文件采用 N-mm-MPa，支持 CalculiX/Abaqus 语法子集。系统不在 API 进程内调用商业求解器，以避免许可、资源和任务恢复问题。正式求解应由独立 Worker 执行并回传结果摘要、日志和制品校验和。

## 4. 吊装物流项目配置

示例：

```json
{
  "advancedEngineering": {
    "craneSitePlan": {
      "designWindSpeedMps": 8.0,
      "siteGate": {"x": -20.0, "y": -10.0},
      "standPoints": [
        {"id": "SP-A", "x": 12.0, "y": -8.0, "groundCapacityKpa": 180.0, "accessWidthM": 7.0}
      ],
      "exclusionZones": [
        {"id": "HV-LINE", "outline": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 20}, {"x": 0, "y": 20}]}
      ]
    },
    "craneLibrary": [
      {
        "id": "PROJECT-CRANE-01",
        "name": "项目实际履带吊",
        "maxBoomLengthM": 72,
        "groundPressureKpa": 108,
        "maxWindSpeedMps": 10,
        "capacityCurve": [[8, 60], [12, 42], [16, 30], [20, 22]]
      }
    ]
  }
}
```

只有明确的保护区或项目吊装禁入区作为硬性吊装禁区。地下室柱网、未来坡道等施工阶段对象不会无条件阻断地连墙钢筋笼吊装。

## 5. 单位治理

后端 `unit_registry.py` 是单位定义源，前端 `utils/units.ts` 是显示适配层。下一阶段应把单位元数据直接写入 OpenAPI schema，通过代码生成同步到 TypeScript，进一步消除双份维护。

字段命名仍保留单位后缀，如：

```text
designForceKn
workingRadiusM
maxDisplacementMm
maxContactPressureMpa
groundPressureKpa
```

这样即使下游不读取单位注册表，也能避免基本量纲误用。

## 6. 界面信息架构

### 精简模式

适用于方案设计和常规校审：

```text
状态摘要
→ 整案 A/B/C 比选
→ 控制问题
→ 下一步动作
```

### 专业模式

适用于模型诊断和高级复核：

- 全量施工阶段；
- 墙体/支撑/围檩逐构件结果；
- 全局矩阵；
- 候选评分分解；
- 构造协调候选；
- 节点子模型；
- 吊装物流；
- 出图规则与发行台账。

同一数据只保留一个权威视图，其他位置使用摘要和跳转，避免重复面板。

## 7. 性能策略

- 三项深化分析按需调用；
- API 默认限制问题组、节点和吊装工况数量；
- 钢筋笼工况按宿主墙幅轮询抽样；
- 大型 JSON 结果不随项目列表返回；
- 三维模型和工程深化面板继续懒加载；
- 求解器输入文件作为制品下载，不嵌入前端状态。

## 8. 后续技术路线

1. 将协调几何增量写回逐根钢筋拓扑并生成变更云线；
2. 独立 CalculiX/Abaqus Worker 自动运行、收敛诊断和结果回写；
3. 吊机站位与场地道路采用可见性图/A*，替代直线路径筛选；
4. 接入 BIM 场地模型、地下管线和高压线安全区；
5. 单位元数据写入 Pydantic/OpenAPI，实现前后端自动生成；
6. A/B/C 计算改为持久化并行 Worker，支持单方案先返回和结果缓存。
