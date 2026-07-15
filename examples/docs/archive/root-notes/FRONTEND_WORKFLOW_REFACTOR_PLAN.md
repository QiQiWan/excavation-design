# PitGuard V1.3.0 前端流程重构方案

## 1. 当前问题

V1.2.0 的后端闭环已经可用，但前端仍是原型级 tab 工作台。主要问题包括：

1. 用户需要自己判断操作顺序，容易跳步。
2. 按钮分散在多个 tab 中，当前步骤、前置条件和下一步不清楚。
3. 完成度、工程校核状态和导出闸门没有形成强视觉分离。
4. 项目数据摘要、fail/warning/manual_review 状态不够集中。
5. 后续 CAD-like 轮廓编辑器和三维审查 Viewer 缺少明确的挂载位置。

## 2. V1.3.0 重构目标

前端工作台从“功能 tab 集合”改为“工程流程向导”。核心目标是让用户按基坑设计流程推进：

```text
项目设置 → 地勘资料 → 三维地质模型 → 基坑轮廓 → 围护结构 → 计算校核 → 闭环审查 → BIM 与计算书
```

每一步都显示：

- 当前状态：done / ready / blocked / warning / error。
- 前置条件。
- 当前数据摘要。
- 当前步骤的主要操作按钮。
- 上一步 / 下一步导航。

## 3. 已完成改造

### 3.1 ProjectWorkspace 重构

`apps/web/src/pages/ProjectWorkspace.tsx` 已重构为 8 步流程工作台：

1. 项目设置
2. 地勘资料
3. 三维地质模型
4. 基坑轮廓
5. 围护结构
6. 计算校核
7. 闭环审查
8. BIM 与计算书

### 3.2 流程状态判断

新增 `buildWorkflowSteps(project)`，根据当前项目数据自动判断每一步状态。

- `done`：步骤已完成。
- `ready`：可执行。
- `blocked`：前置数据不足。
- `warning`：已完成但存在 warning 或 manual_review。
- `error`：存在 fail 或硬性错误。

### 3.3 顶部状态条

新增工作台顶部状态摘要：

- 流程完成步骤数。
- Fail 数量。
- Warning 数量。
- 人工复核项数量。

### 3.4 闭环审查重构

Assurance 页面拆分显示：

- `capabilityCompleteness`：功能完成度。
- `softwareFlowComplete`：软件流程是否完整。
- `engineeringCheckStatus`：工程校核状态。
- `closedLoopComplete`：是否允许作为闭环结果输出。

避免把“软件功能跑通”误解为“工程设计已通过”。

### 3.5 导出步骤重构

导出入口改为卡片式：

- IFC BIM 模型。
- DOCX 计算书。
- 完整 JSON。

如果缺少计算结果或存在 fail，导出区会显示风险提示。

### 3.6 样式增强

在 `apps/web/src/app/styles.css` 中新增 V1.3.0 workflow 样式：

- 左侧流程 stepper。
- 右侧步骤主工作区。
- 状态卡片。
- 导出卡片。
- 响应式布局。

### 3.7 测试

新增前端测试：

```text
apps/web/src/pages/ProjectWorkspace.test.tsx
```

验证工程流程向导能够正常渲染。

## 4. 验证结果

后端测试：

```text
25 passed
```

前端测试：

```text
2 passed
```

前端构建：

```text
npm run build 成功
```

样例流程：

```text
python scripts/run_sample_workflow.py 成功
```

结果：

```text
checkSummary.fail = 0
governingCheckStatus = pass
```

## 5. 后续建议

下一阶段不建议继续堆 API，建议优先做三个前端工程体验模块：

### P1：基坑轮廓 CAD-like 编辑器

- 点拖拽。
- 边上插点。
- 删除点。
- 网格吸附。
- 正交约束。
- 滚轮缩放和平移。
- 自交检测和短边检测实时提示。
- 边段编号和尺寸标注。

### P2：三维 Viewer 工程审查化

- OrbitControls。
- 俯视、侧视、等轴测和 Fit All。
- 剖切滑块。
- 构件拾取高亮。
- 中文属性面板。
- fail/warning 构件颜色定位。
- 支撑轴力和墙体位移图例。

### P3：项目设置表单

- 地下水位、坑内水位、超载、规则集、安全等级可编辑。
- 参数修改后自动标记“需重算”。
- 规则集版本和规范子集说明可查看。

