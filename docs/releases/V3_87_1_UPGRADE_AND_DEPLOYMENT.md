# PitGuard V3.87.1 升级与部署说明

## 1. 升级性质

V3.87.1 是 V3.87.0 的兼容补丁。结构计算内核、规范规则集、项目数据模型和正式成果格式保持兼容，主要修复前端样式、候选预览缓存和错误隔离。

## 2. 升级步骤

```bash
cd /opt/excavation-design
unzip PitGuard_V3.87.1_ui_topology_recovery.zip
cd PitGuard_V3.87.1_ui_topology_recovery
bash scripts/build-production.sh
```

生产部署也可以使用：

```bash
bash scripts/build-and-start-production.sh
```

构建脚本必须完成 `npm ci` 和 `npm run build`。任一步失败时应终止部署，禁止继续使用旧版 `dist`。

## 3. 旧项目预览缓存

旧项目可能存在 `candidate-plan-v1` 预览缓存。V3.87.1 在读取候选预览时会：

1. 检查 `previewSchema`；
2. 发现 V1 后删除该项目的旧预览缓存；
3. 从权威项目快照提取完整候选几何；
4. 写入 `candidate-plan-v2`；
5. 保留支撑、转接梁、转接区、立柱和障碍物。

不需要人工修改数据库。

## 4. 浏览器缓存

部署新 `dist` 后，应执行浏览器强制刷新。Nginx 应对 `index.html` 使用不缓存或短缓存策略，对带哈希的静态资源使用长期缓存。

## 5. 验收

- 设计主流程显示为卡片网格；
- L形 A/B/C 方案显示完整转接体系；
- 结果页不再进入根级恢复模式；
- 浏览器开发者工具中无重复标识符错误；
- 旧项目候选预览接口返回 `candidate-plan-v2`；
- 单个面板故障只显示局部重试卡片。

## 6. 回滚

V3.87.1 没有破坏性数据库迁移。回滚前保留数据库备份，恢复 V3.87.0 代码和对应前端 `dist` 即可。V2 预览缓存包含 V1 的全部字段，旧后端读取时会忽略新增字段。
