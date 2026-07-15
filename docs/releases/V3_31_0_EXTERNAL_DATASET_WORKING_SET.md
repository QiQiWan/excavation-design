# PitGuard V3.31.0 外部数据对象与小内存工作集

## 1. 目标

V3.31.0 将在线系统从“完整工程对象长期保存在项目 JSON 中”调整为三层数据结构：

1. **项目核心快照**：几何、材料、采用方案、施工阶段、计算摘要、审签状态和外部数据引用；
2. **网页工作区投影**：打开项目和日常交互所需的有限数据；
3. **外部工程数据对象**：完整施工阶段结果、地质网格、候选完整计算、逐根钢筋和工业深化缓存。

API 不再承担大型数据文件的读取和转发。隔离 worker 在执行计算或导出时按引用重组完整项目；浏览器按页面、结果和分片读取；下载由 Nginx `X-Accel-Redirect` 直接发送。

## 2. 存储结构

默认目录：

```text
runtime/
├── pitguard.sqlite3
├── artifacts/
│   └── <project-id>/
│       ├── calculation-stage-results/
│       ├── calculation-result-details/
│       ├── geology-vtu-mesh/
│       ├── geology-surfaces/
│       ├── support-candidate-calculations/
│       ├── rebar-geometry/
│       ├── monitoring-records/
│       └── advanced-engineering-heavy/
└── backups/
```

每个对象采用规范化 JSON、SHA-256 内容寻址和 gzip 压缩。相同内容不会重复写入同一路径。项目快照通过 `advancedEngineering.artifactStorage.artifacts` 保存不可变引用。

## 3. 计算结果分片

`CalculationResult.stageResults` 按默认 100 条记录分片：

```text
calculation:<result-id>:stages:0
calculation:<result-id>:stages:1
...
```

项目核心快照只保留控制值、校核汇总、计算合同、发行闸门和空的 `stageResults`。worker 读取完整项目时自动按分片顺序重组。浏览器展开“控制云图与内力包络”后，只读取首个受控分片，避免一次加载全部施工阶段结果。

## 4. API

新增：

```text
GET /api/projects/{project_id}/artifacts
GET /api/projects/{project_id}/artifacts/{artifact_id}
GET /api/projects/{project_id}/artifacts/{artifact_id}/download
GET /api/projects/{project_id}/calculation-results/{result_id}/stage-chunks
GET /api/projects/{project_id}/calculation-results/{result_id}/stage-chunks/{chunk_index}
```

下载接口通过认证后返回：

```http
X-Accel-Redirect: /protected-artifacts/<relative-path>
```

Nginx 使用 `sendfile` 传输文件，Python API 不创建完整文件字节副本。

## 5. 数据迁移

一键部署会依次执行：

```text
数据库在线备份
→ V3.30 工作区列检查
→ 当前项目与保留修订外部化
→ 更新工作区投影和存储统计
→ 清理无修订引用的孤立对象
→ 启动 API 与 worker
```

维护脚本：

```bash
PYTHONPATH=services/api python scripts/prepare-project-artifact-storage.py \
  --database runtime/pitguard.sqlite3

PYTHONPATH=services/api python scripts/garbage-collect-artifacts.py \
  --database runtime/pitguard.sqlite3 --delete
```

## 6. 备份

数据库备份必须与外部对象清单共同保存：

```bash
bash backup-production.sh
```

默认输出 SQLite 一致性备份和对象 SHA-256 清单。需要把对象文件一并打包时：

```bash
PITGUARD_BACKUP_ARTIFACT_FILES=1 bash backup-production.sh
```

## 7. 内存边界

V3.31.0显著降低以下路径的内存占用：

- 打开项目；
- 保存普通设计修改；
- 返回项目列表；
- 读取最近计算摘要；
- 下载 IFC、图纸和数据对象；
- 浏览计算结果页面。

计算 worker 在单次求解期间仍需保留当前求解器要求的活动矩阵和阶段工作集。V3.31.0在任务完成后外部化结果并退出 worker，尚未把求解器内部所有中间矩阵改为 out-of-core 算法。超大有限元或高密度网格仍应采用专用求解服务、稀疏矩阵和分区/分布式计算。

## 8. 生产配置

```text
PITGUARD_ARTIFACT_ROOT=/opt/excavation-design/runtime/artifacts
PITGUARD_ARTIFACT_THRESHOLD_MB=1
PITGUARD_STAGE_RESULT_CHUNK_SIZE=100
```

首次升级必须运行：

```bash
sudo bash start-linux.sh
```

`restart-production.sh` 不执行数据迁移和前端重建，不能用于 V3.30 → V3.31 的首次升级。
