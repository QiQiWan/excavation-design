# V2.0.11 当前环境启动修复与人机交互优化说明

## 1. 问题诊断

V2.0.10 的一键启动脚本默认在 `services/api/.venv` 下创建或复用虚拟环境。该策略在干净机器上可用，但在用户已经配置好 Conda、系统 Python 或 IDE 解释器的场景下，会导致以下问题：

1. 启动脚本进入 `.venv` 后，无法读取当前环境中已经安装的 FastAPI、uvicorn、numpy、shapely、python-docx 等模块。
2. 若 `.venv` 创建或安装依赖失败，后端会表现为模块缺失或无法启动。
3. 用户难以判断后端到底使用了哪个 Python 解释器，也难以定位数据库路径和日志。
4. 后端依赖清单未声明 `meshio`。虽然轻量 VTU XML 解析可以在未安装 meshio 时运行，但复杂 appended / compressed VTU 文件需要 meshio 支持，用户环境缺少该模块时容易误判为后端模块缺失。

## 2. 已完成修改

### 2.1 启动脚本

根目录脚本已改为当前环境优先：

- `start-linux.sh`
- `start-windows.ps1`
- `start-windows.bat`

脚本不再创建 `services/api/.venv`，而是使用当前 shell 中的 Python。Linux 默认使用 `python`，不存在时回退到 `python3`；Windows 默认使用当前 `python`，仅当不存在时回退到 `py`。

脚本会执行后端依赖预检查。若缺少模块且 `PITGUARD_INSTALL_DEPS` 未设置为 `0`，会执行：

```bash
python -m pip install -e "services/api[dev]"
```

该命令会安装到当前 Python 环境，不会创建新虚拟环境。

### 2.2 依赖清单

`services/api/pyproject.toml` 增加：

```toml
"meshio>=5.3"
```

用于增强 VTU 文件解析能力。

### 2.3 后端诊断接口

新增：

```text
GET /api/system/diagnostics
```

返回内容包括：

- API 版本；
- Python 解释器路径；
- Python 版本；
- 当前工作目录；
- 数据库路径；
- 后端依赖模块可用性；
- 缺失模块清单。

### 2.4 前端运行状态提示

前端顶部新增 API 重检按钮和运行环境提示。后端离线或依赖缺失时，页面会提示使用根目录一键脚本启动，并给出手动安装命令。

## 3. 进一步功能性优化空间

当前系统已经形成“资料导入—地质建模—基坑轮廓—围护结构—计算校核—闭环审查—成果导出”的完整链条，但仍有以下优化空间：

1. 启动与部署：下一步可增加 Docker Compose、离线依赖包和端口占用自动切换，降低工程现场部署门槛。
2. 长耗时任务：候选方案完整计算、IFC 导出和 DOCX 计算书生成仍是同步调用，后续应升级为任务队列、进度条、可取消任务和结果缓存。
3. 交互状态恢复：前端可记录最近打开项目、当前流程步骤、侧栏展开状态和候选方案选中状态，刷新页面后恢复操作现场。
4. 错误诊断：后端异常应统一返回错误码、模块名、对象 ID 和建议操作，前端按“输入错误 / 模型缺项 / 计算失败 / 导出失败”分类展示。
5. 工程审查闭环：当前已有正式化闸门，但还可以把阻断项直接绑定到三维对象和二维平面图定位，形成“点击问题—定位构件—修改参数—复算”的闭环。
6. 生产级计算：多候选完整计算已经接入，但核心土-结构耦合求解仍偏工程筛查，应继续引入更完整的施工阶段非线性、接触、开挖释放和支撑预加轴力求解。

## 4. 验证结果

已验证：

- 后端 `compileall` 通过；
- 后端健康接口可启动；
- 新增 `/api/system/diagnostics` 可返回诊断信息；
- 前端 `vitest` 通过：3 个测试通过；
- 前端生产构建通过；
- 后端定向测试通过：`test_health`、`test_v2_0_11_system_diagnostics_endpoint`、`test_calculation_result_schema`、`test_v2_0_8_report_contains_candidate_score_chart`。

注意：当前验证环境未安装 `meshio`，诊断接口能正确识别缺失模块。用户实际运行一键脚本时，默认会将 `meshio` 安装到当前 Python 环境；如设置 `PITGUARD_INSTALL_DEPS=0`，脚本会停止并提示手动安装命令。
