# V3.87.13 变形云图成果路由与按需恢复

## 根因

生产部署固定使用 `PITGUARD_PRODUCT_MODE=core`，但计算结果分片路由此前仅在 `full` 模式注册。前端打开变形云图时请求 `/api/projects/{project_id}/calculation-results/{result_id}/stage-chunks`，因此返回 404 `Not Found`。

## 修复

1. 将成果分片路由纳入 core 主流程。
2. 云图读取请求改为 quiet，不触发全屏加载遮罩。
3. 分片读取失败时回退到 `/calculation/latest-evidence`。
4. 按需加载最多8个分片，避免浏览器堆内存峰值。
5. 云图沿围护墙完整折线路径渲染。
6. 数据未载入时明确标记为几何占位。

## 验收

- core模式下两个 stage-chunks 路由存在。
- 打开“查看控制云图与内力包络”不再显示 Not Found。
- 读取失败只在模块内显示警告，项目页面保持可操作。
- 折线墙和转角墙云图与当前围护墙轮廓一致。
