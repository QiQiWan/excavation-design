# 运行、部署与故障排查

## 本地启动

Linux/macOS：`bash start-linux.sh`。Windows：运行 `start-windows.bat`。后端默认端口 8002，前端默认端口 5173。

前端依赖使用确定版本，安装命令为 `npm ci`。Python 包可在 `services/api` 下执行 `python -m pip install -e .[dev]`。

## 环境变量

- `PITGUARD_BACKEND_PORT`：后端 API 端口，默认 `8002`。启动脚本会同步注入前端 `VITE_API_BASE_URL`。
- `PITGUARD_DB_PATH`：SQLite 数据库路径。
- `PITGUARD_BACKUP_DIR`：在线一致性备份目录，默认位于数据库同级 `backups`。
- `PITGUARD_BACKUP_RETENTION`：本地备份保留数量，默认 `20`。
- `PITGUARD_API_KEYS`：可选 API Key 与角色映射。JSON 示例：`{"designer-secret":{"role":"designer","actor":"design-a"},"admin-secret":{"role":"admin","actor":"ops"}}`。配置后客户端通过 `X-PitGuard-Key` 或 Bearer Token 访问。
- `PITGUARD_CORS_ORIGINS`：逗号分隔的前端来源，默认仅允许本机 5173 端口。
- `PITGUARD_NUMERIC_THREADS`：单个数值内核的线程数，默认 `1`。任务并发由后台任务管理器控制，通常不建议同时放大两级并发。

## 常见问题

前端白屏时先执行 `npm ci && npm run build`。后端导入失败时检查当前 Python 解释器和依赖。项目列表缓慢时确认客户端使用新的摘要接口。任务在服务重启后显示 `interrupted` 属于预期行为，应重新提交任务。同一项目的计算与导出会串行执行，不同项目可并行。交付文件可用任务结果中的 `sha256` 校验完整性。若计算过程中 CPU 长时间满载或延迟波动，先确认 BLAS 线程变量未覆盖 `PITGUARD_NUMERIC_THREADS`。


## 生产部署最低检查

1. 在反向代理终止 TLS，仅开放 443，并限制后端 8002 仅允许内网访问。
2. 配置 `PITGUARD_API_KEYS`，至少分别设置设计、审查和管理员身份，密钥不得写入仓库。
3. 调用 `GET /api/system/readiness` 检查数据库、任务、访问控制和备份配置。
4. 管理员定期调用 `POST /api/system/backup`，并在独立环境执行恢复演练。
5. 将数据库、备份和导出成果目录纳入异机或对象存储备份；本地保留策略不能替代灾备。
6. 使用 `bash scripts/test-backend.sh full-isolated` 和 `bash scripts/test-frontend.sh` 执行发布门禁。
