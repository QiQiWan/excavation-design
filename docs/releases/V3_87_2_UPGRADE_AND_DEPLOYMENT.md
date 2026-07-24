# PitGuard V3.87.2 升级与部署说明

## 升级对象

适用于 V3.87.0 和 V3.87.1。V3.87.2 不改变结构计算内核；数据库主 schema 无破坏性迁移。候选预览缓存会由 `candidate-plan-v1/v2` 自动升级为 `candidate-plan-v3`。

## 升级步骤

1. 停止 API、前端和重型 worker；
2. 备份项目数据库、对象存储和运行时配置；
3. 替换程序目录；
4. 删除历史前端构建和不完整依赖；
5. 安装依赖并重新构建；
6. 启动 API 与 worker；
7. 打开典型 L 形项目，确认三个候选均显示闭合转接构件；
8. 运行健康检查和相关测试。

```bash
cd apps/web
rm -rf node_modules dist
npm ci
npm run test
npm run build

cd ../../services/api
python -m pytest -q tests/test_v3_87_2_integrity_hardening.py
```

## 缓存和旧项目

- 旧候选预览首次读取时自动重建；
- 若权威项目快照本身缺少转接构件，系统会标记 `previewIntegrity=incomplete`，需重新运行方案搜索；
- 旧配筋结果中钢筋类型不全时，应重新运行完整计算和配筋，禁止仅依靠新版前端显示；
- GET 设计核心面板不会推进项目 revision。

## 部署要求

- API 与数值 worker 分进程部署；
- worker 设置内存和任务超时；
- 不利工况和钢筋外置阶段建议单 worker 串行或受控并发；
- 生产环境必须保留任务失败正文和 traceback 尾部；
- 禁止复用 V3.87.0/V3.87.1 的历史 `dist`。

## 回滚

回滚程序前恢复数据库和对象存储备份。V3 预览缓存对旧程序无权威作用，可删除后由旧版本重建；已确认参数的来源引用不会丢失。

## 生产发行边界

完成 Vitest、TypeScript 工程编译、Vite 构建、多项目并发、100 次耐久和真实项目回归前，版本保持 `engineering_preview`。
