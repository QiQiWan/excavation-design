# V2.0.12 Windows 启动链路修复说明

## 问题定位

用户日志显示两个独立故障：

1. `pip install -e services/api` 失败，报错为 `Multiple top-level packages discovered in a flat-layout: ['app', 'exports']`。根因是 `services/api` 目录同时存在后端源码包 `app` 和导出目录 `exports`，setuptools 自动包发现无法判断哪些目录应纳入 Python distribution。
2. `start-windows.ps1` 的依赖检查阶段报 `SyntaxError: '(' was never closed`，后续出现 `不能对 Null 值表达式调用方法`。根因是多行 Python here-string 被作为 `python -c` 参数传递时，在 Windows PowerShell/命令行边界下存在截断或转义风险，导致 Python 实际收到的代码不完整。

## 修复内容

- 在 `services/api/pyproject.toml` 中加入 `[build-system]` 和 `[tool.setuptools.packages.find]`，显式包含 `app*`，排除 `exports*` 与 `tests*`。
- Windows/Linux 启动脚本取消 `pip install -e services/api[dev]` 自动安装路径，改为只安装缺失的第三方依赖包。后端以 `PYTHONPATH=services/api` 方式运行，因此普通启动不需要 editable 安装本项目。
- Windows 依赖检查改为生成 `runtime/check_backend_modules.py` 临时诊断脚本并执行，避免 `python -c` 多行传参截断。
- 启动脚本保留“当前环境优先”策略，不创建、不激活 `.venv`。

## 推荐启动命令

Windows：

```bat
start-windows.bat
```

Linux：

```bash
./start-linux.sh
```

手动补依赖：

```bash
python -m pip install fastapi "uvicorn[standard]" pydantic python-multipart numpy shapely python-docx openpyxl matplotlib meshio
```

## 后续优化建议

1. 增加 `doctor` 命令，只检查环境和端口，不启动服务。
2. 增加端口占用检测和自动换端口能力。
3. 增加离线依赖包或内网 wheelhouse 支持，适配工程单位内网部署。
4. 将长耗时计算改成后台任务队列，前端展示计算进度、日志和失败重试入口。
