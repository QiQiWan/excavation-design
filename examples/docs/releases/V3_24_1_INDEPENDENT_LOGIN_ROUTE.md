# PitGuard V3.24.1：独立登录页面与受保护路由

## 1. 问题

V3.24.0 已包含登录组件和会话认证，但登录组件由根应用条件渲染，浏览器地址没有切换到 `/login`。用户直接访问 `/docs` 或其他业务地址时，页面虽然可能显示登录表单，仍缺少明确的独立登录路由、登录后回跳和会话失效导航。

## 2. 新路由行为

- 未登录访问 `/`：跳转到 `/login`；
- 未登录访问 `/docs`：跳转到 `/login?redirect=%2Fdocs`；
- 登录成功：返回 `redirect` 指定的同源安全地址；
- 已登录访问 `/login`：自动返回首页或原访问地址；
- API 返回 401：清空当前工程状态，跳转 `/login?reason=expired`；
- 主动退出：跳转 `/login?reason=logout`；
- 登录状态服务不可用：显示独立登录页和后端连接错误。

重定向地址经过同源校验，拒绝外部 URL、协议相对 URL和登录页自循环。

## 3. 登录页面

登录页采用全屏双栏工程平台布局，包含：

- PitGuard 平台定位和关键能力；
- 用户名、密码输入；
- 密码显隐；
- 登录中状态；
- 会话过期、退出和服务异常提示；
- 登录后返回地址提示；
- HttpOnly Cookie、角色权限和操作审计说明。

## 4. 生产部署

`sudo bash start-linux.sh` 会自动：

1. 生成或读取网页账号；
2. 生成 PBKDF2-SHA256 密码哈希；
3. 生成会话签名密钥；
4. 写入 `PITGUARD_USERS` 和 `PITGUARD_SESSION_SECRET`；
5. 构建同域 `/api` 前端；
6. 使用 Nginx 托管 `/login` 和业务 SPA 路由；
7. 不配置 Nginx Basic Auth 弹窗。

登录凭据位于 `/etc/pitguard/web-credentials.txt`，文件权限为 `600`。

## 5. 验证

- 前端 12 个测试文件、19 项测试通过；
- 覆盖未登录自动跳转、原地址回跳、会话失效跳转、密码显隐和应用登录表单；
- TypeScript 和 Vite 生产构建通过；
- `/login` 生产路由由 Nginx SPA 回退支持。
