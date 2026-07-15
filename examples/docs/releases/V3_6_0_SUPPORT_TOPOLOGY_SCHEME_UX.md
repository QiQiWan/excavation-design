# V3.6.0 支撑拓扑、整案比选与交互收束

## 1. 迭代目标

V3.6.0 面向异形基坑方案设计中的五个实际问题：水平支撑轴线贴墙、长跨直对撑过多、优化决策被拆成逐墙操作、换撑刚度以 0 表示造成误判，以及结果页信息过载。本版本将几何、计算、方案优化和界面交互同步调整，保证同一套方案在平面、三维、计算、CAD 和审查台账中使用一致的数据。

## 2. 详细实施清单

### 2.1 支撑轴线与围护墙脱开

| 编号 | 实施项 | 代码位置 | 验收条件 |
|---|---|---|---|
| G-01 | 新增项目级支撑—墙净距参数 | `schemas/domain.py`、`ProjectWorkspace.tsx` | 默认 1.0 m，可配置 |
| G-02 | 支撑端点由墙面连接点向坑内退让 | `services/support_layout.py::_apply_support_wall_clearance` | 中心线不与墙线重合 |
| G-03 | 同时保存墙面连接点与轴线端点 | `SupportElement.start/end_wall_connection` | 计算节点和显示均可追溯 |
| G-04 | 增加短刚臂连接墙面节点与支撑轴线 | `Engineering3DViewer.tsx` | 三维中连接关系可见 |
| G-05 | 围檩和全局矩阵采用原墙面连接点定位 | `wale_beam.py`、`global_coupled.py` | 偏移显示不改变墙面里程 |
| G-06 | 增加最小净距和目标净距质量指标 | `quality/support_layout_quality.py` | 净距不足形成质量问题 |

净距采用以下工程表达：

\[
d_{axis}=\max\left(d_{set},\frac{t_w}{2}+\frac{b_{wale}}{2}+d_c\right)
\]

当前快速布置以项目参数 `supportWallClearanceM` 为控制值，围檩与连接区仍通过刚臂节点传力。该模型用于方案设计，正式节点刚度应结合实际构造复核。

### 2.2 斜撑、短对撑和双向网格候选

| 编号 | 实施项 | 代码位置 | 验收条件 |
|---|---|---|---|
| T-01 | 新增长直对撑阈值 | `DesignSettings.max_direct_strut_span_m` | 默认 24 m |
| T-02 | 新增斜撑触发墙长阈值 | `diagonal_brace_min_wall_length_m` | 默认 18 m |
| T-03 | 长墙凸角生成扩展角撑 | `_corner_diagonal_layout` | 相邻长墙和角度条件满足时生成 |
| T-04 | 混合体系删除靠角超长直撑 | `_hybridize_long_struts` | 由短斜撑承担角部路径 |
| T-05 | 双向网格直接约束长边和回墙 | `_secondary_grid_layout` | 形成主次支撑节点和临时立柱 |
| T-06 | 保留传统直对撑作为对照方案 | `topology_strategy=direct_grid` | A/B/C 至少包含三类体系 |
| T-07 | 超长直对撑计数进入评分 | `excessiveDirectStrutCount` | 候选详情可见 |

当前三类整案候选为：

- 方案 A：斜撑与短对撑混合；
- 方案 B：传统直对撑；
- 方案 C：双向支撑网格。

候选生成同时检查支撑交叉、障碍物、端点有效性、临时立柱位置和换撑路径。斜撑是候选路径之一，不会在缺少中部墙面约束时简单删除全部直对撑。

### 2.3 A/B/C 整体方案比选

| 编号 | 实施项 | 代码位置 | 验收条件 |
|---|---|---|---|
| O-01 | 优化变量增加完整拓扑族 | `support_layout_optimizer.py` | 三类体系分别生成候选 |
| O-02 | 候选包含完整三维几何数据 | `planGeometry.supports/columns/elevations` | 前端无需逐墙拼接 |
| O-03 | 前三候选分别执行完整计算 | `engine.py::_compare_top_support_candidates` | 轴力、位移、墙/围檩内力、稳定和闸门均有结果 |
| O-04 | 完整计算后再次综合排序 | `_rank_full_candidate_calculations` | 输出 `decisionScore` 和 `decisionRank` |
| O-05 | 失败方案不得因评分被推荐 | 完整计算决策评分 | Fail 大于 0 时不能标记推荐 |
| O-06 | 前端以三维整案卡片展示 | `ResultViewer.tsx::CandidateScheme3D` | 一次比较 A/B/C |
| O-07 | 局部墙长优化移入高级详情 | `ResultViewer.tsx` | 默认决策区不再逐墙确认 |
| O-08 | 一键整体采用方案 | `adoptSupportCandidate` | 采用后要求重新计算 |

完整计算决策评分由实际响应和施工性共同构成：

- 支撑轴力、墙体位移、墙体弯矩和剪力；
- 围檩弯矩与挠度；
- 支撑、立柱数量；
- 最大跨度和超长直对撑数量；
- 原约束优化器中的间距、障碍、出土路径和对称性评分；
- Fail、Warning 和人工复核惩罚。

该评分用于方案排序，不替代规范校核，也不能绕过正式发行闸门。

### 2.4 换撑刚度状态和计算来源

| 编号 | 实施项 | 代码位置 | 验收条件 |
|---|---|---|---|
| R-01 | 将未激活与零刚度分离 | `global_coupled.py::_replacement_slab_state` | 未激活显示 `—` |
| R-02 | 增加 active/missing/invalid 状态 | `GlobalCoupledSystemResult` | 状态可序列化 |
| R-03 | 按 EA/L 计算楼板等效刚度 | `solve_global_coupled_system` | 正刚度且来源可追溯 |
| R-04 | 引入有效宽度、厚度、弹模和连接折减 | `DesignSettings` | 参数可在项目设置修改 |
| R-05 | 应激活但缺失时设置阻断项 | `engine.py` | 不再静默写 0 |
| R-06 | 表格显示状态、数值、来源和组成 | `ResultViewer.tsx` | 设计人员可判断 0 的含义 |

快速模型采用：

\[
k_{rep}=\eta_c\frac{E_s b_{eff}t_s}{L_t}
\]

其中 `L_t` 为当前墙段等效传力长度。V3.6 样例中，未激活阶段显示“—”；换撑阶段不同墙段得到 585000～1950000 kN/m 的等效刚度。后续仍需以板壳—框架协同模型替代单弹簧代理。

### 2.5 操作流程和信息收束

| 编号 | 实施项 | 代码位置 | 验收条件 |
|---|---|---|---|
| U-01 | 项目默认精简模式 | `defaultWorkspaceMode` | 新项目先显示核心成果 |
| U-02 | 设置页集中支撑与换撑关键参数 | `ProjectWorkspace.tsx` | 无需进入底层代码 |
| U-03 | 候选区先显示三张整案卡片 | `ResultViewer.tsx` | 3D、关键 KPI、推荐状态同屏 |
| U-04 | 评分分解和动画折叠 | `<details className="engineeringDetails">` | 默认页面减少表格 |
| U-05 | 全局矩阵与换撑台账折叠 | `ResultViewer.tsx` | 专业人员按需展开 |
| U-06 | 内力、云图和局部墙优化折叠 | `ResultViewer.tsx` | 决策信息优先 |
| U-07 | 三维对象属性增加净距与拓扑族 | `Engineering3DViewer.tsx` | 点击构件可核查 |

## 3. 数据结构变化

新增或扩展字段包括：

```text
DesignSettings
  supportWallClearanceM
  maxDirectStrutSpanM
  diagonalBraceMinWallLengthM
  preferDiagonalBraces
  replacementSlabEffectiveWidthM
  replacementSlabThicknessM
  replacementSlabElasticModulusMpa
  replacementConnectionReduction
  defaultWorkspaceMode

SupportElement
  startWallConnection / endWallConnection
  centerlineOffsetM
  startWallClearanceM / endWallClearanceM
  topologyFamily

GlobalCoupledSystemResult
  slabReplacementStatus
  slabReplacementSource
  slabReplacementRequired
  slabReplacementComponents
```

## 4. 兼容策略

- 旧项目缺少新增参数时采用安全默认值；
- 原支撑端点没有墙面连接点时仍可加载；
- 计算、IFC、CAD 和围檩节点优先使用墙面连接点，缺失时回退到原端点；
- 原候选方案字段保持不变，新增完整计算决策字段为可选字段；
- 未执行完整候选计算时，界面明确标注“预筛评分”，不伪装为最终推荐。

## 5. 工程边界

V3.6 的支撑内偏移采用轴线和刚臂代理；斜撑候选仍是规则和离散搜索；换撑楼板采用 EA/L 等效弹簧。正式工程应进一步复核节点偏心、围檩扭转、楼板开洞、梁柱协同、施工缝、支撑安装偏差和分区施工顺序。

## 6. 下一步优化空间

### P0：工程可信度

1. **真实围檩偏置几何内核**：当前按端点沿杆轴退让，下一步应对闭合多边形生成完整内偏移围檩线，处理凹角、圆角、交点裁切和自交。
2. **换撑楼板板壳模型**：用板壳、框架梁柱和连接滑移替代单一 EA/L 弹簧，支持开洞、后浇带和分区形成顺序。
3. **混合拓扑联合截面优化**：当前先选拓扑再计算截面，下一步使用图优化或混合整数规划同时决定支撑类型、节点、立柱、截面和预加轴力。
4. **节点偏心与围檩扭转**：内偏移支撑会引入节点偏心，应增加围檩扭矩、承压板偏心和节点局部有限元复核。

### P1：设计效率

5. **候选方案并行任务化**：将 A/B/C 完整计算迁移到独立 Worker，流式回传每个方案进度和结果，支持取消、失败重试和缓存。
6. **可视化方案差异**：在同一三维视口中显示 A→B 支撑增删、移动、截面变化和施工通道影响，替代多个静态卡片之间的肉眼比较。
7. **决策权重项目化**：把完整计算决策权重配置为企业规则集，保存权重版本、推荐结果和人工覆盖原因。
8. **一键问题闭环**：将“超长直撑、净距不足、换撑缺失”映射到具体自动修复动作，并在复算后自动关闭对应问题。

### P2：性能与交付

9. **Three.js 继续分包和 LOD**：按工程三维、钢筋三维、云图三维拆分渲染器，增加实例化和视锥裁剪，将公共块控制到 500 kB 以下。
10. **CAD/IFC 一致性增强**：在 CAD 和 IFC 中输出墙面连接点、刚臂、支撑偏心和换撑状态，生成几何哈希对账。
11. **概率与鲁棒优化**：对土体刚度、水位、支撑安装误差和换撑刚度进行不确定性传播，比较候选方案在参数扰动下的失效概率。
12. **独立基准验证**：增加与手算、公开算例和成熟有限元软件的对照库，形成可审查的误差范围和适用边界。
