# V2.8.0 围护墙设计长度冗余优化修复闭环

## 工程边界
- 围护墙厚度按项目统一厚度策略控制；本版本不再把单面墙厚作为独立优化变量。
- 优化对象为设计面长度、槽段分幅长度和局部加强段长度。
- 采纳候选不会改变基坑外轮廓、围护墙轴线和项目统一墙厚。
- 采纳候选后必须重新运行规范算法计算，才能刷新冗余指标和正式交付状态。

## 新增能力
1. 冗余优化结果进入问题清单中心。
2. 对严重冗余、偏保守、接近下限和采纳后未复算状态生成对象级 issue。
3. 每个设计面输出保守、均衡、经济方向的修复动作说明。
4. 采纳候选后写入 `wallLengthOptimizationHistory`，保留修改前后、目标带、设计面和复算状态。
5. 运行计算后自动清除 `wallLengthOptimizationRecomputeRequired`，形成闭环。
6. 新增冗余优化报告导出任务和导出接口。
7. 完整交付包增加围护墙设计长度冗余优化报告。

## 新增/强化接口
- `GET /api/projects/{project_id}/wall-optimization/length-redundancy`
- `POST /api/projects/{project_id}/wall-optimization/apply-length-candidate`
- `GET|POST /api/projects/{project_id}/export/wall-length-redundancy`
- 任务：`export_wall_length_redundancy`

## 验证
- 后端 `compileall` 通过。
- 后端 V2.8.0 定向测试通过。
- 前端 `npm run build` 通过。
- 前端 `vitest` 通过。
