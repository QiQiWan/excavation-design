# PitGuard V3.87.3 升级与部署说明

## 升级范围

V3.87.3 可从 V3.87.0、V3.87.1 或 V3.87.2 直接升级。数据库主 schema、结构计算内核和候选预览 schema 均无破坏性变化。

## 前端构建

旧版静态文件仍包含常驻的第二套流程面板，必须重新构建前端：

```bash
cd apps/web
rm -rf node_modules dist
npm ci
npm run test
npm run build
```

部署新的 `dist` 后执行浏览器强制刷新，确认顶部版本为 V3.87.3。

## 后端验证

```bash
cd services/api
PYTHONPATH=. pytest -q \
  tests/test_v3_87_2_integrity_hardening.py \
  tests/test_v3_87_3_single_primary_flow.py
```

## 升级后检查

1. 项目页面只显示六个主步骤；
2. 页面不再常驻“V3.87 设计主流程”卡片；
3. 右上角存在“质量与追溯”按钮；
4. 打开后显示“设计质量与追溯中心”；
5. 六组证据卡与六个主步骤对应；
6. 点击证据卡的“进入该步骤”可以返回相应主步骤；
7. Escape、遮罩和关闭按钮均可关闭抽屉；
8. 方案、计算和成果数据不发生变化。

## 回滚

本版本没有数据库迁移。回滚时恢复 V3.87.2 后端和前端静态文件即可。V3.87.3 新增的 API 返回字段为兼容性扩展，不会修改项目数据。
