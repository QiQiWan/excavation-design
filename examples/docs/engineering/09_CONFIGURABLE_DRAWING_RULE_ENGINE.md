# 可配置、可优化出图规则引擎

## 1. 模块边界

V3.4.0 将出图体系拆分为五个相互独立的层级：

1. **工程模型层**：基坑轮廓、围护墙、支撑层、配筋区、节点、计算和审查结果。
2. **规则包层**：决定需要哪些图、触发条件、拆图粒度、比例策略、优先级和发行约束。
3. **渲染器层**：白名单绘图函数，将某一类图纸规则转换为 DXF/PDF 几何。
4. **企业模板层**：图框、图层、字体、线型、签审栏、CTB/STB 和企业编号规则。
5. **发行闸门层**：工程检查、当前快照审签、修订绑定、施工版或审查版标识。

规则包不能执行 Python、JavaScript 或系统命令。配置只能调用服务端白名单渲染器，因此企业可以调整出图组合，同时不会扩大代码执行面。

## 2. 规则包目录

核心规则包位于：

```text
packages/drawing-rules/
├─ manifest.json
├─ presets/
│  ├─ compact.json
│  ├─ balanced.json
│  ├─ construction.json
│  └─ enterprise-minimal.json
├─ schema/drawing-rule-set.schema.json
└─ examples/project-override.json
```

可通过环境变量加载企业规则包：

```bash
export PITGUARD_DRAWING_RULE_DIR=/opt/company/pitguard-drawing-rules
```

企业目录中的同名预设会覆盖核心预设，也可以增加新的 `presets/*.json`。规则集来源记录为稳定的 `sourcePackageId` 和相对 `sourceFile`，不会把服务器绝对路径写入项目成果。

## 3. 配置模型

规则集的主要字段如下：

| 字段 | 作用 |
|---|---|
| `schemaVersion` | 配置结构版本 |
| `id/name/version` | 规则集身份和版本 |
| `modules` | 总图、配筋、节点大样、质量复核和监测反演五个图纸模块 |
| `parameters` | 图幅、有效图幅比例、最大图纸数、逐墙合图数量等全局参数 |
| `objectiveWeights` | 自动优化的目标权重 |
| `issuePolicy` | 审查版和施工版的发行约束 |
| `sheetRules` | 图纸触发、编号、文件路径、渲染器、比例和动态展开规则 |
| `ruleSetHash` | 规范化配置的稳定哈希 |

其中总图和配筋为工程必需模块，项目配置不能关闭；节点大样、质量复核和监测反演可按项目阶段启停。

单张图纸规则包括：

```json
{
  "id": "S02",
  "enabled": true,
  "sheetNo": "S-02-L{level:02d}",
  "title": "第{level}道支撑平面布置图",
  "scope": "general",
  "renderer": "support_level_plan",
  "file": "10_plans/S-02-L{level:02d}_support_level_plan.dxf",
  "expansion": "per_level",
  "trigger": {"path": "facts.supportLevelCount", "op": "gt", "value": 0},
  "scalePolicy": {"kind": "plan", "extent": "project", "preferred": 150},
  "priority": 90
}
```

## 4. 安全条件 DSL

触发条件使用有限表达式，不调用解释器。支持：

- 逻辑：`all`、`any`、`not`；
- 比较：`eq`、`neq`、`gt`、`gte`、`lt`、`lte`；
- 集合：`in`、`contains`；
- 状态：`exists`、`truthy`、`always`。

可访问的上下文由系统生成，例如：

```text
facts.wallCount
facts.supportLevelCount
facts.cornerBraceCount
facts.nodeWarningCount
facts.isDeepExcavation
parameters.includePerWallElevations
parameters.includeEmptyQualitySheets
```

未知操作符、未知渲染器、绝对路径和 `..` 路径会在保存前被拒绝。

## 5. 动态拆图

当前支持三种展开模式：

- `single`：一条规则生成一张图；
- `per_level`：按支撑层动态生成；
- `per_wall`：按墙幅动态生成。

`wallSheetsPerDrawing` 控制每张墙体立面容纳的墙幅数量。取值为 1 时每幅墙独立出图；取值为 2 时相邻两幅墙合并到同一张图，墙体 ID、墙段 ID 和配筋区仍分别绑定，图纸数量约减半。

动态展开后的图号、文件名、标题和模型绑定会进入 `drawing_set_manifest.json`，每张图可以追溯到实际支撑层、墙幅和设计快照。

## 6. 自动比例选择

平面图和剖面图根据模型范围与有效图幅计算所需比例分母：

\[
D_{req}=\max\left(\frac{L_x\times1000}{W_p\eta},\frac{L_y\times1000}{H_p\eta}\right)
\]

其中：

- `Lx/Ly` 为模型范围，单位 m；
- `Wp/Hp` 为图幅尺寸，单位 mm；
- `η` 为有效图幅比例；
- `Dreq` 为可容纳模型所需的最小比例分母。

引擎从允许比例表中选取不小于 `Dreq` 的最接近比例，并在图纸计划中记录：

- 所需分母；
- 实际分母；
- 图幅；
- 有效图幅比例；
- 模型范围；
- 固定比例或自动适配模式。

详图采用允许详图比例中的最近值，避免任意比例进入正式图纸。

## 7. 规则集优化

优化器包含两类候选：

1. **项目当前规则候选**：保留企业或项目已经修改的 `sheetRules`，仅枚举图幅、逐墙合图数量等布局参数。
2. **标准预设候选**：比较紧凑审查型、平衡型、施工深化型和企业最小发行集。

目标函数为：

\[
S=100\frac{\sum_i w_i m_i}{\sum_i w_i}
\]

指标包括：

| 指标 | 含义 |
|---|---|
| 覆盖度 | 已触发规则和必需图纸的覆盖情况 |
| 可读性 | 自动比例的适配程度 |
| 施工深化 | 配筋、节点和质量图的完整程度 |
| 紧凑性 | 图纸数量相对上限的控制程度 |
| 一致性 | 是否存在超限裁剪和规则冲突 |

优化结果默认只返回规则集元数据、评分分解、图纸分类、图纸数、超限数量和计划哈希，避免为每个候选重复传输完整规则集。采用候选时，服务端依据同一项目规则重新生成完整候选并再次执行白名单校验，客户端不能绕过验证直接保存。需要离线分析时可显式请求 `includeRuleSets=true`。

## 8. 图纸计划与决策追溯

每次导出均生成：

```text
drawing_rule_set.json
 drawing_set_manifest.json
90_schedules/drawing_rule_decisions.csv
 drawing_package_manifest.json
```

其中决策表记录每条规则：

- 是否启用；
- 条件是否触发；
- 导出范围是否匹配；
- 渲染器是否存在；
- 是否进入图纸包；
- 条件求值轨迹。

正式发行包根目录同时保存 `drawing_rule_set.json` 和 `drawing_plan.json`。PDF 页脚、发布清单和发行清单记录规则集哈希及计划哈希，便于复现某一版成果。

## 9. 发行安全边界

规则配置可以加严施工版条件，不能绕过工程底线。下列条件始终优先：

- 计算或配筋存在硬阻断时禁止施工版；
- 项目设置要求正式批准时，规则集不能关闭该要求；
- 项目设置要求当前修订绑定时，规则集不能关闭该要求；
- 设计输入或规则集改变后，设计快照哈希改变，原批准和施工版修订自动失效。

规则集属于设计输入的一部分。修改图纸触发、拆图或发行条件会形成新的设计快照。

## 10. 企业扩展流程

建议企业按以下顺序建立规则包：

1. 复制 `balanced.json` 作为企业基线；
2. 固定企业图幅、允许比例和发行图纸上限；
3. 根据项目类型增加触发条件；
4. 禁用不需要的兼容图，保留必需图；
5. 设置审查效率或施工深化目标权重；
6. 在样例项目上运行预览和优化；
7. 通过规则校验、CAD 回归、PDF 发行和审签回归；
8. 冻结规则包版本并记录 `sourcePackageId`；
9. 项目仅引用已批准的规则包版本。

新增全新图种时，需要开发并注册新的白名单渲染器，再由规则包引用。仅调整触发、拆图、比例和发行组合时不需要修改后端代码。
