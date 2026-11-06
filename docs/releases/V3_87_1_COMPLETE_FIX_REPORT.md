# PitGuard V3.87.1 完整修复清单与实施报告

## 一、修复结论

本次已针对截图中的三项问题完成代码级修复：

1. V3.87 设计主流程排版退化；
2. L形/凹形支撑方案只显示短支撑段，闭合转接体系缺失；
3. `ResultViewer.tsx` 重复声明 `statusText`，页面直接进入安全恢复模式。

补丁版本为 **V3.87.1**。该版本只调整前端样式、候选预览数据契约、可视化与故障隔离，不改变 V3.87.0 的结构计算内核、规范规则集和既有计算结果。

## 二、完整修复清单

### P0-1 前端恢复模式根因

| 项目 | 内容 |
|---|---|
| 根因 | `ResultViewer.tsx` 同一模块存在两个 `statusText` 函数声明 |
| 修复 | 优化模块函数更名为 `optimizationStatusText`，计算健康状态继续使用独立 `statusText` |
| 防复发 | 新增源代码契约测试，要求 `ResultViewer.tsx` 仅存在一个 `function statusText(` |
| 降级保护 | 新增 `PanelErrorBoundary`，设计主流程、方案比选和结果可视化发生运行时异常时只隔离当前面板 |

### P0-2 设计主流程排版

| 项目 | 内容 |
|---|---|
| 根因 | `DesignCoreWorkflowPanel` 样式位于 `apps/web/src/styles.css`，主入口仅加载 `app/styles.css` |
| 修复 | `main.tsx` 在基础样式后显式加载 `./styles.css` |
| 效果 | 阶段卡片、页签、表格、状态色和响应式布局重新进入生产构建 |
| 验收 | 页面不再出现裸文本纵向堆积和异常大面积空白 |

### P0-3 异形支撑闭合预览

| 项目 | 内容 |
|---|---|
| 根因1 | `candidate-plan-v1` 压缩预览只保存 `supports` 和 `columns`，丢弃 `transferBeams` 与 `transferZones` |
| 根因2 | `ResultViewer`、核心精简预览和围护评分平面只绘制支撑段 |
| 根因3 | 多点转接梁部分视图只连首尾点 |
| 修复 | 预览契约升级为 `candidate-plan-v2`，保留转接梁、转接区、障碍物、支撑标高和源对象计数 |
| 缓存迁移 | 读取到 V1 缓存后自动删除项目预览缓存，并从权威项目快照重建 |
| 绘制修复 | A/B/C 比选、结果候选图、核心工作台小图、围护结构平面、支撑评分平面均绘制转接构件 |
| 几何修复 | 转接梁统一使用 `polyline`，完整保留中间折点 |

## 三、修改文件范围

### 前端

- `apps/web/src/main.tsx`
- `apps/web/src/styles.css`
- `apps/web/src/app/styles.css`
- `apps/web/src/app/PanelErrorBoundary.tsx`
- `apps/web/src/viewers/ResultViewer.tsx`
- `apps/web/src/viewers/RetainingSystemViewer.tsx`
- `apps/web/src/components/SchemeComparisonPanel.tsx`
- `apps/web/src/components/CoreEngineeringVisuals.tsx`
- `apps/web/src/pages/CoreProjectWorkspace.tsx`
- `apps/web/src/pages/ProjectWorkspace.tsx`
- `apps/web/src/pages/DocsPage.tsx`
- `apps/web/src/types/domain.ts`

### 后端与缓存

- `services/api/app/storage/database.py`
- `services/api/app/version.py`
- `services/api/app/main.py`
- `services/api/app/services/standards_matrix.py`

### 测试、文档与评估

- `services/api/tests/test_v3_87_1_frontend_topology_recovery.py`
- `services/api/tests/test_v3_87_1_frontend_source_contract.py`
- `scripts/evaluate-v3871-ui-topology.py`
- `docs/releases/V3_87_1_UI_TOPOLOGY_RECOVERY_HOTFIX.md`
- `docs/releases/V3_87_1_UI_TOPOLOGY_EVALUATION.json`
- `docs/releases/V3_87_1_RELEASE_VALIDATION.json`
- `README.md`、`CHANGELOG.md`、`docs/README.md`

## 四、实际验证结果

### 1. L形三方案预览

| 模板 | 支撑数 | 转接构件数 | 转接区 | 压缩后保留 |
|---|---:|---:|---:|---|
| compact_elbow_ring | 72 | 18 | 1 | 完整保留 |
| junction_hub_frame | 72 | 30 | 1 | 完整保留 |
| ring_chord_frame | 72 | 24 | 1 | 完整保留 |

三个候选的压缩预览均为 `candidate-plan-v2`，转接构件数量与原始候选一致。

### 2. 测试

- 相关后端回归：**65 passed**；
- TypeScript/TSX 源文件语法转译：**69 个文件，0 错误**；
- Python 编译检查：通过；
- API 导入：通过，173 条路由；
- L形候选真实生成与预览压缩评估：通过。

当前环境 npm 内部镜像在依赖下载时返回 HTTP 503，因此未声明 Vitest 和 Vite 生产构建通过。

## 五、部署步骤

```bash
cd PitGuard_V3.87.1_ui_topology_recovery/apps/web
rm -rf node_modules dist
npm ci
npm run build
```

然后替换服务器上的前端 `dist`，重启后端和 Nginx，并执行浏览器强制刷新。

旧项目无需手工修改数据库。首次读取候选预览时，后端会识别 `candidate-plan-v1`，删除旧缓存并自动重建 V2 预览。

## 六、验收标准

1. V3.87 设计主流程显示为卡片网格，页签与表格样式正常；
2. L形 A/B/C 方案同时显示径向撑、闭合环梁/转接框架/内环弦杆和立柱；
3. 小预览、大预览、围护结构平面和评分平面构件数量一致；
4. 结果页不再出现 `Identifier 'statusText' has already been declared`；
5. 单个可视化面板异常时显示局部重试卡片，项目工作区保持可用；
6. 旧项目首次打开后，候选预览返回 `candidate-plan-v2`；
7. 施工图、IFC、计算书仍使用原结构模型，不因本补丁改变设计内力或配筋结果。
