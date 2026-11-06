# PitGuard V3.87.11 前端构建热修复 1

## 修复问题

服务器执行 `npm run build` 时，TypeScript 在 `RebarDesignPanel.tsx` 报错：

```text
TS7006: Parameter 'row' implicitly has an 'any' type.
```

问题位于配筋计算合同自动恢复的轮询逻辑。`blockers` 由运行时诊断对象转换而来，条件表达式没有为 `Array.prototype.some` 的回调参数提供稳定的上下文类型。在启用 `noImplicitAny` 的生产构建中，回调参数 `row` 因此被判定为隐式 `any`。

## 修复方法

为回调参数增加显式结构类型：

```ts
blockers.some((row: Record<string, unknown>) =>
  String(row.reasonCode ?? '') === 'CALCULATION_NOT_CURRENT'
)
```

该修改只修复 TypeScript 类型检查，不改变配筋计算、计算合同恢复、前端交互或后端算法版本。

## 部署验证

在 `apps/web` 目录执行：

```bash
npm ci
npm run build
```

预期不再出现 `RebarDesignPanel.tsx:133:60 TS7006`。
