# PitGuard V3.87.0 设计核心闭环

## 产品边界

V3.87 将产品主流程聚焦为设计师可控制的九个阶段：规范与参数确认、工程输入、方案搜索、围护结构联合设计、计算核验、配筋深化、施工图、计算书、校审发行。施工计划、现场快照和偏差事件保留只读兼容，退出设计完成度和首次设计发行门禁。

外部施工或现场信息以 `ExternalCollaborationRecord` 接收；只有涉及原设计边界的内容才形成 `DesignReviewRequest`。系统只判断是否需要复算或设计变更，不承担现场事件管理。

## V3.82—V3.87

### V3.82 设计核心收缩
- 九阶段设计工作台。
- 外部资料与设计复核请求。
- 旧施工/现场对象不参与设计主流程。

### V3.83 规范与参数治理
- 参数来源、置信度、确认状态和正式设计资格。
- 条文级规则执行证据。
- 软件建议值与项目批准值分离。

### V3.84 方案与联合设计
- 五级搜索：体系、体系族、拓扑、尺寸、完整计算。
- A/B/C体系多样性检查。
- 采用方案必须具备当前完整计算。

### V3.85 计算与配筋闭环
- 墙、支撑、围檩逐构件包络。
- 每条结果显式单位和控制工况。
- 实际钢筋选择—有效高度/刚度更新—重新验算—构造检查闭环。

### V3.86 设计院级交付
- 15类核心施工图检查。
- 16项计算书章节证据检查。
- DesignSnapshotId统一计算、配筋、图纸、报告和IFC哈希。

### V3.87 生产资格
- 后端、前端、迁移、耐久、并发、外部基准和真实项目试点分项门禁。
- 未完成全部强制验证时标记 `engineering_preview`。

## API

- `GET /api/projects/{id}/design-core`
- `GET/PATCH /api/projects/{id}/design-core/parameters`
- `GET /api/projects/{id}/design-core/rules`
- `GET /api/projects/{id}/design-core/schemes`
- `GET /api/projects/{id}/design-core/member-envelopes`
- `GET /api/projects/{id}/design-core/reinforcement-closure`
- `GET /api/projects/{id}/design-core/delivery-quality`
- `POST /api/projects/{id}/design-core/design-snapshots`
- `GET/POST /api/projects/{id}/design-core/collaboration`
