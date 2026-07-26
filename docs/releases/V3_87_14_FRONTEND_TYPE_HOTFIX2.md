# PitGuard V3.87.14 Frontend Type Hotfix 2

## 现象

生产构建在 `apps/web/src/viewers/ResultViewer.tsx` 中失败，包含：

- `runTask` 回调参数类型不兼容；
- `issueId` 不存在；
- `targetPanel` 不存在（两处）。

## 根因

`ResultViewer` 将后台任务操作名声明为任意 `string`，而调用方使用受控的 `BackendTaskOperation` 联合类型。严格函数参数检查下，受控回调不能赋给接受任意字符串的属性。正式化事项又由后端计划项和本地回退项组成，TypeScript 推断成字段不一致的联合类型。

## 修复

- 新增 `ResultViewerTaskOperation`，只列出该查看器实际调用的操作；
- 新增 `ResultViewerRunTask` 统一三个组件的任务回调签名；
- 新增 `FormalizationIssue`，统一自动计划项与回退项字段；
- 运行目标类型检查及 TSX 转译检查。

## 影响范围

仅影响前端编译类型，不改变运行时计算、配筋、IFC、CAD、数据库和已有结果合同。
