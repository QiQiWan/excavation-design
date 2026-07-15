# V3.9.0 验证记录

## 1. 后端专项与兼容回归

执行：

```bash
PYTHONPATH=services/api \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
pytest -q \
  services/api/tests/test_v3_9_0_detailing_units_scheme_ux.py \
  services/api/tests/test_v3_8_0_deep_detailing_startup.py \
  services/api/tests/test_v3_6_0_support_topology_scheme_ux.py \
  services/api/tests/test_v3_7_0_professional_drawing_pipeline.py
```

结果：16项通过。

V3.9专项覆盖：

- 单位注册表；
- 四类构造协调候选；
- 参数化几何增量与验证条件；
- 候选应用；
- 高风险节点选择；
- 节点筛选结果与构造变体；
- CalculiX/Abaqus输入文件；
- 吊机能力曲线、地基、风载和路径字段。

## 2. 前端

执行：

```bash
cd apps/web
npm test -- --run
npm run build
```

结果：

- 8个测试文件通过；
- 10项测试通过；
- TypeScript编译通过；
- Vite生产构建通过。

构建块：

| 模块 | 体积 |
|---|---:|
| 入口 | 15.34 kB |
| 工程深化面板 | 约26.45 kB |
| 项目工作台 | 261.20 kB |
| Three.js公共块 | 523.86 kB |

Three.js公共块仍高于500 kB提示线。

## 3. 运行环境与API

- Python依赖检查：通过；
- Linux启动脚本语法：通过；
- 后端默认端口：8002；
- FastAPI路由：110条；
- `/api/system/units`：存在；
- 软件版本：3.9.0；
- 导出结构版本：3.9。

## 4. 基准样例

采用 `URBAN-TOPDOWN-32M-WALL-5SUPPORT`：

| 项目 | 结果 |
|---|---:|
| 构造协调问题组 | 60 |
| 参数化协调候选 | 240 |
| 高风险节点子模型 | 8 |
| 节点最大筛选利用率 | 0.995 |
| 吊装样例工况 | 40 |
| 可行吊装工况 | 30 |
| 失败吊装工况 | 10 |
| 吊机库 | 5 |
| 站位 | 8 |
| 整案候选 | 3 |
| 候选拓扑族 | 斜撑混合、双向网格、传统直对撑 |

A/B/C完整计算为后台重型任务。样例生成仅验证候选恢复和前端入口，完整并行比较仍由“完整计算A/B/C”任务执行。

## 5. CAD/深化包验证

基准算例完整审查包已成功生成：

| 项目 | 数量 |
|---|---:|
| 包内文件 | 113 |
| DXF | 41 |
| CSV | 37 |
| CalculiX/Abaqus `.inp` | 12 |
| 单位注册表 | 1 |
| 构造协调结果 | 1组 JSON/CSV |
| 吊装物流结果 | 1组 JSON/CSV |

节点输入文件、工程单位、协调候选和吊装物流均进入图纸包清单。

## 6. 已知边界

- 构造协调的几何增量尚未完整写回逐根钢筋拓扑；
- 内置节点结果属于降阶筛选，实体输入文件需在正式求解器中运行；
- 吊机默认能力曲线和自动站位不能替代项目实际设备及专项方案；
- 全部候选完整计算在单进程/原生数值库环境中可能耗时较长，应通过独立Worker运行；
- Three.js仍需继续拆包和LOD优化。
