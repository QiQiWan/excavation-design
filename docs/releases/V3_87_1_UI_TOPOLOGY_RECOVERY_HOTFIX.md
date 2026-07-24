# PitGuard V3.87.1：界面、异形支撑预览与恢复模式修复

## 1. 适用范围

本版本是 V3.87.0 的补丁版本，结构计算内核、规范规则集和导出数据格式保持兼容。修复聚焦于界面样式接入、候选预览数据契约、异形支撑闭合显示和前端故障隔离。

## 2. 问题与根因

### 2.1 设计主流程排版退化

`DesignCoreWorkflowPanel` 的样式定义位于 `apps/web/src/styles.css`，原入口只加载 `apps/web/src/app/styles.css`。组件结构已经渲染，但卡片、页签和表格样式没有进入生产包。

### 2.2 L形方案只显示短支撑段

异形方案由径向撑与 `transferBeams` 共同形成闭合传力体系。原 `candidate-plan-v1` 压缩契约只保存 `supports` 和 `columns`，并且部分预览组件只绘制 `supports`，导致闭合环梁、中心汇交框架和内环弦杆在界面中消失。

### 2.3 页面进入安全恢复模式

`ResultViewer.tsx` 同一模块重复声明 `statusText`，浏览器加载结果模块时出现 `Identifier 'statusText' has already been declared`。根级错误边界随后接管整个应用。

## 3. 实施修复

1. 前端入口按顺序加载基础样式和 V3.87 补充样式。
2. 将优化状态函数重命名为 `optimizationStatusText`，计算健康状态保留独立 `statusText`。
3. 候选预览升级为 `candidate-plan-v2`，保留：
   - `supports`；
   - `columns`；
   - `transferBeams`；
   - `transferZones`；
   - `obstacles`；
   - 支撑标高和源对象数量。
4. 读取到 V1 缓存时自动删除项目对应预览缓存，并从权威项目快照重建。
5. 在以下视图统一绘制转接体系：
   - A/B/C 方案比选；
   - 计算结果候选预览；
   - 核心工作台精简预览；
   - 围护结构设计模型；
   - 支撑评分平面。
6. 多点转接梁改用 `polyline` 绘制，避免仅连接首尾点而丢失中间折点。
7. 新增 `PanelErrorBoundary`，设计主流程、方案比选和结果可视化出现运行时异常时只隔离当前面板。

## 4. 部署要求

```bash
cd apps/web
rm -rf node_modules dist
npm ci
npm run build
```

随后部署新的 `dist`。浏览器需强制刷新。生产部署脚本必须在 `npm ci` 或 `npm run build` 失败时终止，禁止回退到旧 `dist`。

## 5. 验收清单

- 设计主流程以响应式卡片网格显示；
- L形三候选均显示径向撑和转接构件；
- A/B/C 小预览、选中方案大预览和围护评分平面一致；
- 旧项目首次打开后预览缓存升级到 `candidate-plan-v2`；
- 结果页不再出现重复 `statusText` 错误；
- 单一面板故障不再触发整站恢复页；
- 施工图、IFC 和计算书仍使用原结构模型中的 `ringBeams`，本补丁不修改计算结果。

## 6. 验证边界

当前环境的 npm 内部镜像返回 HTTP 503，未能执行完整 `npm ci`、Vitest 和 Vite 生产构建。已完成 TypeScript 69 个源文件语法转译检查、Python 编译、65 项相关后端回归和 L形三体系真实候选几何验证。
