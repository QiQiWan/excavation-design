import { FormEvent, useEffect, useState } from 'react';
import { api, type AuthIdentity } from '../api/client';

export default function LoginPage({
  onAuthenticated,
  returnTo = '/',
  notice,
  serviceError,
  onRetryService,
}: {
  onAuthenticated: (identity: AuthIdentity) => void;
  returnTo?: string;
  notice?: string;
  serviceError?: string;
  onRetryService?: () => void;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const previousTitle = document.title;
    document.title = '登录 · PitGuard BIM Designer';
    return () => { document.title = previousTitle; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const result = await api.login(username.trim(), password);
      onAuthenticated(result.identity);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const returnLabel = returnTo !== '/' ? returnTo : undefined;

  return (
    <main className="loginPage">
      <section className="loginBrandPanel" aria-label="PitGuard平台介绍">
        <div className="loginBrandLine">
          <div className="loginBrandMark">PG</div>
          <div><strong>PitGuard</strong><span>BIM Designer</span></div>
        </div>
        <p className="sectionKicker">GEOTECHNICAL DESIGN CONTROL</p>
        <h1>基坑围护结构智能设计与受控交付平台</h1>
        <p>统一完成支撑拓扑、围护墙设计、分阶段计算、配筋深化、IFC建模和施工图交付，并保留计算合同与审计链。</p>
        <div className="loginFeatureGrid">
          <span><b>01</b> 洁净支撑拓扑</span>
          <span><b>02</b> 工业计算合同</span>
          <span><b>03</b> 钢筋笼与IFC</span>
          <span><b>04</b> 受控发行闸门</span>
        </div>
        <small>系统输出属于工程设计辅助成果，正式成果须由具备相应资格的岩土与结构专业人员复核、审核和批准。</small>
      </section>

      <section className="loginFormPanel">
        <div className="loginCard">
          <div className="loginCardHeader">
            <p className="sectionKicker">SECURE ACCESS</p>
            <h2>登录系统</h2>
            <p>输入服务器部署时配置的工程账号。</p>
          </div>

          {notice ? <div className="loginNotice" role="status">{notice}</div> : null}
          {serviceError ? <div className="loginServiceError" role="alert">
            <strong>登录服务暂不可用</strong>
            <span>{serviceError}</span>
            <button type="button" className="secondary" onClick={onRetryService}>重新检测服务</button>
          </div> : null}

          <form onSubmit={submit}>
            <label htmlFor="pitguard-username">用户名</label>
            <input
              id="pitguard-username"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="请输入用户名"
              disabled={busy}
            />

            <label htmlFor="pitguard-password">密码</label>
            <div className="passwordField">
              <input
                id="pitguard-password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="请输入密码"
                disabled={busy}
              />
              <button type="button" className="passwordToggle" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? '隐藏密码' : '显示密码'}>
                {showPassword ? '隐藏' : '显示'}
              </button>
            </div>

            {error ? <div className="loginError" role="alert" aria-live="polite">{error}</div> : null}
            <button className="loginSubmit" type="submit" disabled={busy || !username.trim() || !password}>
              {busy ? <><span className="loginSpinner" />正在验证</> : '登录并进入工作台'}
            </button>
          </form>

          {returnLabel ? <p className="loginReturnHint">验证成功后返回：<code>{returnLabel}</code></p> : null}
          <div className="loginSecurityLine"><span aria-hidden="true">●</span> HttpOnly 会话 · 角色权限控制 · 操作审计</div>
        </div>
        <footer className="loginFooter">PitGuard V3.31.0 · designer.eatrice.cn</footer>
      </section>
    </main>
  );
}
