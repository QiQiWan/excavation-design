# V3.87.3 macOS/Linux 开发启动完整修复

## 问题根因

此前临时补丁误把前端目录假定为 `frontend/`，实际工程前端位于 `apps/web/`；部分补丁脚本只打印启动信息，没有执行 API、计算 worker 和 Vite 前端；原始 `start-linux-dev.sh` 使用 Bash 4 的 `mapfile`，在 macOS 默认 Bash 3.2 下中断。

## 本次修复

- 保留完整 V3.87.3 工程与智能闭环迭代内容；
- 新增 `start-dev.sh` 与 `start-macos-dev.sh`；
- 将 `start-linux-dev.sh` 改为 macOS Bash 3.2/Linux 双兼容实现；
- 正确识别 `services/api` 和 `apps/web`；
- 移除 `mapfile/readarray` 依赖；
- 启动 API、独立计算 worker、Vite 前端；
- 增加后端、worker、前端健康检查；
- 增加 PID、日志、端口占用与旧补丁残留进程治理；
- 新增 `stop-dev.sh` 和 `scripts/diagnose-startup.sh`；
- 支持 `PITGUARD_PREFLIGHT_ONLY=1` 启动前检查。

## 启动

```bash
chmod +x start-dev.sh start-linux-dev.sh start-macos-dev.sh stop-dev.sh scripts/*.sh
./stop-dev.sh
./start-dev.sh
```

服务地址：

- API health: `http://127.0.0.1:8002/health`
- API docs: `http://127.0.0.1:8002/docs`
- Web: `http://127.0.0.1:5173`
