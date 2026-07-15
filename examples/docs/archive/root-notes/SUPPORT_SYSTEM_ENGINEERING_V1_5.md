# V1.5.0 支撑体系工程化设计说明

## 1. 目标

V1.5.0 将 V1.4.0 的“水平支撑拓扑布线”推进到“可计算、可追溯、可交付的支撑体系设计原型”。本版本重点覆盖：

1. 支撑与围檩节点建模；
2. 支撑端部局部承压和节点配筋；
3. 临时立柱桩设计；
4. 地下室柱网、坡道、出土口、中心岛等避让；
5. 环撑/中心岛式支撑体系；
6. 分区开挖和换撑路径表达；
7. 按墙面 tributary width 关联支撑轴力；
8. 前端显示支撑分仓、跨长、角撑逻辑和立柱服务范围。

本版本仍属于工程设计辅助原型，不能替代注册岩土/结构工程师的正式设计和审查。

## 2. 支撑布置算法

### 2.1 普通内支撑体系

普通矩形、长条形或凹形基坑采用如下流程：

```text
基坑轮廓去重
→ 识别外包尺度和长短向
→ 沿长向按目标间距布置扫描线
→ 扫描线与基坑多边形求交
→ 得到坑内有效短跨区间
→ 避开坡道、出土口、中心岛、保护区等障碍
→ 生成主对撑
→ 在凸直角处生成角撑
→ 识别支撑端点所在墙面
→ 计算每个支撑端点的墙面 tributary width
```

主对撑不再按同层全局均分轴力，而是按其连接墙面的 tributary width 计算。

### 2.2 障碍避让

`ExcavationModel.obstacles` 支持以下类型：

```text
basement_column_grid
ramp
muck_out_opening
protected_zone
center_island
manual
```

每个障碍可以通过多边形 `outline` 表达，也可以用 `center + width + length + clearance` 表达矩形保护区。支撑线和临时立柱点会避开 active 障碍。

### 2.3 环撑/中心岛体系

当满足以下条件之一时，系统启用中心岛/环撑体系原型：

1. 基坑存在 `center_island` 类型障碍；
2. 基坑平面接近方形，且短边达到大平面阈值。

算法生成内环梁 `ringBeams`，并生成从外围墙面到内环梁的径向支撑 `ring_strut`。

## 3. 支撑-围檩节点

系统为每根支撑两端生成 `SupportWaleNode`：

```text
support_id
support_code
level_index
elevation
location
face_code
wale_beam_code
node_type
bearing_plate
reinforcement
check_status
```

节点类型包括：

```text
strut_to_wale
diagonal_to_wale
ring_strut_to_ring
manual
```

计算阶段根据支撑轴力包络更新节点承压板尺寸、承压应力、承压限值和节点附加钢筋。

## 4. 端部局部承压与节点配筋

节点承压子集计算采用：

```text
sigma_bearing = N_design / A_plate
sigma_bearing <= 0.60 * f_c
```

其中承压板会在支撑截面范围内自动扩大，避免只用固定板尺寸导致不必要 fail。节点配筋按轴力大小自动给出：

1. 节点附加竖向筋；
2. 节点加密箍筋；
3. 高轴力节点增加端部抗裂分布筋。

## 5. 临时立柱桩

V1.5.0 将临时立柱基础从默认扩大基础升级为立柱桩设计。计算阶段对每根临时立柱生成 `FoundationDesign`，其 `foundation_type` 为 `column_pile`。

承载力子集采用：

```text
R = (u * l * qs + Ap * qp) / gamma
N <= R
```

输出字段包括：

```text
pile_diameter
pile_length
pile_count
pile_capacity
pile_utilization
pile_tip_elevation
```

扩大基础设计函数仍保留，用于浅小基坑或桩设计失败后的对比和人工复核。

## 6. 分区开挖和换撑路径

`ConstructionStage` 增加：

```text
stage_type
zone
deactivated_support_ids
replacement_action
```

`RetainingSystem.replacement_path` 给出从底板形成、地下室结构达到强度到自下而上拆撑的建议路径。当前换撑阶段作为路径表达和审查提示，不等价于永久结构完整计算模型。

## 7. 前端展示

前端围护结构页面新增：

1. 支撑角色统计：主对撑、角撑、环撑径向撑；
2. 支撑跨长、分仓间距、连接墙面；
3. 支撑端点 tributary width；
4. 支撑-围檩节点表；
5. 节点承压板、承压应力、承载限值、节点配筋；
6. 临时立柱服务支撑、桩径、桩长、承载力、利用率；
7. 分区开挖与换撑路径；
8. 三维视图中环梁、支撑节点和立柱桩属性拾取。

## 8. 当前边界

1. 节点承压为局部承压子集，不含完整锚固、抗裂扩散、焊缝和预埋件设计。
2. 立柱桩为单桩竖向承载力子集，不含沉降、负摩阻、抗拔、桩身配筋和桩土共同作用。
3. 环撑/中心岛体系为自动布置原型，正式工程仍需结合出土路径、栈桥、分区施工、地下室结构转换复核。
4. 换撑路径已表达，但未把地下室楼板作为真实弹性支点参与墙体变形计算。
5. tributary width 轴力分配已比全局均分更合理，但仍需后续升级为“墙面线荷载—围檩连续梁—支撑节点反力”的体系计算。
