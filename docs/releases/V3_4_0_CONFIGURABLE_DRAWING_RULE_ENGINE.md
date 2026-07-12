# V3.4.0 可配置出图规则引擎

## 迭代目标

V3.4.0 将图纸选择、触发条件、按层/按墙拆图、自动比例、图纸优先级和发行约束从 CAD 导出函数中抽离，建立独立的规则包、规则引擎、渲染器注册表和优化器。

## 已完成内容

- 新增 `packages/drawing-rules/`，核心预设改为版本化 JSON 规则包。
- 增加总图、配筋、节点大样、质量复核和监测反演五个图纸模块，其中必需模块不可关闭。
- 支持 `PITGUARD_DRAWING_RULE_DIR` 企业规则目录和同名预设覆盖。
- 新增安全条件 DSL，不执行任意代码。
- 图纸渲染器采用服务端白名单。
- 增加配置结构版本、上下文路径、输出路径、重复图号和重复文件路径校验。
- 支持 `single`、`per_level`、`per_wall` 动态展开。
- 支持图纸级图幅和横向、纵向、自动方向比例适配。
- `wallSheetsPerDrawing` 支持多幅墙合并到同一立面图。
- 平面、剖面和详图比例按图幅及项目范围自动选择。
- CAD 导出由统一渲染器注册表驱动，删除固定图纸组合分支。
- 新增规则校验、预览、预设应用、候选优化和候选采用 API。
- 优化器保留项目自定义图纸规则，并枚举图幅和逐墙合图参数。
- 施工版规则只能加严工程闸门，不能关闭项目级安全条件。
- 正式 CAD/PDF 包记录规则集哈希、图纸计划哈希和决策轨迹。
- 前端新增规则预设、图幅、最大图数、逐墙合图、质量图、兼容图、JSON 编辑和候选评分界面。

## API

```text
GET  /api/drawing-rules/presets
GET  /api/projects/{id}/drawing-rules
PUT  /api/projects/{id}/drawing-rules
POST /api/projects/{id}/drawing-rules/validate
GET  /api/projects/{id}/drawing-rules/preview
POST /api/projects/{id}/drawing-rules/optimize
POST /api/projects/{id}/drawing-rules/apply-preset/{preset}
POST /api/projects/{id}/drawing-rules/apply-candidate
GET  /api/drawing-rules/capabilities
```

## 兼容性

默认 `balanced` 规则包保持 V3.3.0 图纸组合和旧版 CSV 兼容接口。未配置项目规则集时自动加载核心平衡型预设。企业模板继续负责图层、图框和签审栏，现有企业 CAD 模板不需要迁移。
