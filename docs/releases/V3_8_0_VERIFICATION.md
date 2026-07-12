# V3.8.0 验证记录

## 1. 后端专项回归

执行：

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
PYTHONPATH=. pytest -q \
  tests/test_v3_8_0_deep_detailing_startup.py \
  tests/test_v3_7_0_professional_drawing_pipeline.py
```

结果：7项通过，约3.2秒。

覆盖：

- 节点承压板、加劲板、焊缝和锚筋生成；
- 钢筋笼吊装和机械连接生成；
- D-10 R2018 DXF合法性；
- 缺失依赖精确安装命令；
- V3.7专业CAD、纸空间、钢筋加工和发行门禁兼容性。

## 2. 前端验证

执行：

```bash
npm test -- --run
npm run build
```

结果：

- 6个测试文件通过；
- 8项测试通过；
- TypeScript编译通过；
- Vite生产构建通过；
- Three.js公共块523.86 kB，仍高于500 kB提示线。

## 3. 基准工程验证

案例：`URBAN-TOPDOWN-32M-WALL-5SUPPORT`。

| 指标 | 结果 |
|---|---:|
| 基准工程构建 | 约11.7 s |
| 配筋与深化模型 | 约1.9 s |
| 承压板/节点硬件 | 230组 |
| 钢筋笼分节 | 138段 |
| 吊装筛查失败 | 0 |
| 机械连接套筒 | 21,886个 |
| 预埋件协调检查 | 1,054项 |
| 深化设计硬失败 | 0 |
| 深化设计警告 | 1,054项 |
| 深化状态 | warning |

## 4. CAD样例验证

- 图纸数量：41张DXF；
- CSV台账：34个；
- D-10、R-10、R-11、Q-04均已生成；
- DXF版本：R2018/AC1032；
- 独立DXF校验：41/41通过；
- 图纸完整性：warning，0个硬阻断；
- 施工版发行：未开放，基准项目仍受工程计算和正式审签条件约束；
- 审查版CAD包生成成功。

## 5. 启动环境验证

- `python scripts/check-python-env.py --format text`：当前环境通过；
- 人工构造缺失依赖：退出码2；
- JSON输出包含`missingRequirements`、`installCommand`和`editableInstallCommand`；
- Linux脚本语法检查通过；
- Windows脚本具有同等缺包检测和退出提示逻辑；
- 后端默认端口保持8002。

## 6. 已知边界

- 全量逐根钢筋仍采用后端完整数据、前端抽样显示的分层策略；
- 1,054项构造协调需项目节点大样进一步处理；
- 正式DWG和CTB/STB批量出版依赖企业AutoCAD、BricsCAD或ODA环境；
- 原生数值库长期连续测试仍建议采用独立进程门禁。
