# PitGuard V3.22.0 服务器一键构建与启动补丁

## 目标

面向 `/opt/excavation-design` 的 Linux 生产服务器，将依赖安装、前端生产构建、systemd 后端服务、Nginx HTTPS 静态托管、API 反向代理、访问密钥和健康检查合并为单条命令。

## 执行

```bash
sudo bash start-linux.sh
```

生产入口不启动 Vite，不使用或检查 5173/5174。Nginx 直接提供 `apps/web/dist`，FastAPI 仅监听 `127.0.0.1:8002`。

## 新增脚本

- `scripts/build-production.sh`：安装依赖并构建生产前端，不改系统服务。
- `scripts/build-and-start-production.sh`：完整的一键构建、服务配置与启动入口。
- `start-linux.sh`：生产环境快捷入口。
- `start-linux-dev.sh`：保留原开发模式。
- `restart-production.sh`：重启后端并重载 Nginx。
- `status-production.sh`：显示服务、健康检查和生产端口。

## 默认配置

- 域名：`designer.eatrice.cn`
- TLS 证书：`/usr/crt/fullchain.pem`
- TLS 私钥：`/usr/crt/privkey.pem`
- 后端：`127.0.0.1:8002`
- systemd：`pitguard-api.service`
- Nginx：`/etc/nginx/conf.d/designer.eatrice.cn.conf`
- 运行数据库：项目根目录下 `runtime/pitguard.sqlite3`

首次执行会生成 Nginx 到后端的 API Key，并默认启用 Basic Auth。网页登录凭据保存在 `/etc/pitguard/web-credentials.txt`，权限为 600。
