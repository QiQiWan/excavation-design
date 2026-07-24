# V3.81 前端构建说明

本代码包已完成 67 个 TypeScript/TSX 源文件的语法转译检查，错误数为 0。

当前执行环境在 `npm ci` 下载依赖时，由内部 npm 代理持续返回 HTTP 503，导致依赖安装未完成，因此未执行 Vitest 和 Vite 生产构建。交付包不包含半安装的 `node_modules`，也不包含历史 `dist`，避免部署旧界面。

部署环境中执行：

```bash
cd apps/web
npm ci --no-audit --no-fund
npm test
npm run build
```

只有上述步骤全部通过后，才应将前端标记为生产构建已验证。
