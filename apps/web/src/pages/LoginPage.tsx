import { FormEvent, useState } from 'react';
import { api, type AuthIdentity } from '../api/client';

export default function LoginPage({ onAuthenticated }: { onAuthenticated: (identity: AuthIdentity) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(undefined);
    try {
      const result = await api.login(username.trim(), password);
      onAuthenticated(result.identity);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return <main className="loginPage">
    <section className="loginBrandPanel">
      <div className="loginBrandMark">PG</div>
      <p className="sectionKicker">PitGuard BIM Designer</p>
      <h1>基坑围护结构智能设计平台</h1>
      <p>支撑拓扑、围护墙设计、分阶段计算、配筋深化、IFC 与施工图交付的一体化工程工作台。</p>
      <div className="loginFeatureGrid">
        <span>洁净支撑拓扑</span><span>墙长联合优化</span><span>钢筋笼深化</span><span>成果发行闸门</span>
      </div>
      <small>工程成果必须由具备相应资质的岩土与结构专业人员复核、审签。</small>
    </section>
    <section className="loginCard">
      <div><p className="sectionKicker">安全访问</p><h2>登录 PitGuard</h2><p>使用服务器部署时配置的工程账号进入系统。</p></div>
      <form onSubmit={submit}>
        <label>用户名<input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="请输入用户名" /></label>
        <label>密码<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="请输入密码" /></label>
        {error ? <div className="loginError">{error}</div> : null}
        <button type="submit" disabled={busy || !username.trim() || !password}>{busy ? '正在验证…' : '登录'}</button>
      </form>
      <p className="loginHint">会话采用 HttpOnly 安全 Cookie，账号角色决定查看、设计、校核和审批权限。</p>
    </section>
  </main>;
}
