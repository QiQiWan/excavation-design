import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { api, type AuthIdentity } from '../api/client';
import ProjectsPage from '../pages/ProjectsPage';
const ProjectWorkspace = lazy(() => import('../pages/ProjectWorkspace'));
const DocsPage = lazy(() => import('../pages/DocsPage'));
import type { Project } from '../types/domain';
import LoginPage from '../pages/LoginPage';
import {
  buildLoginHref,
  LOGIN_PATH,
  loginReasonMessage,
  readBrowserRoute,
  returnPathFromLoginSearch,
  routeHref,
  safeReturnPath,
  type BrowserRoute,
} from './navigation';

type Diagnostics = {
  version: string;
  softwareVersion?: string;
  algorithmVersion?: string;
  ruleSetVersion?: string;
  exportSchemaVersion?: string;
  pythonVersion: string;
  databaseConfigured?: boolean;
  missingModules: string[];
  modules: { importName: string; packageName: string; available: boolean; version?: string }[];
};

type AuthPolicy = {
  loginRequired: boolean;
  mode: string;
  sessionTtlSeconds: number;
};

function useBrowserRoute() {
  const [route, setRoute] = useState<BrowserRoute>(() => readBrowserRoute());

  useEffect(() => {
    const update = () => setRoute(readBrowserRoute());
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);

  const navigate = useCallback((href: string, replace = false) => {
    if (replace) window.history.replaceState({}, '', href);
    else window.history.pushState({}, '', href);
    setRoute(readBrowserRoute());
  }, []);

  return { route, navigate };
}

export default function App() {
  const [health, setHealth] = useState('checking');
  const [diagnostics, setDiagnostics] = useState<Diagnostics | undefined>();
  const [selected, setSelected] = useState<Project | undefined>();
  const [authChecking, setAuthChecking] = useState(true);
  const [authPolicy, setAuthPolicy] = useState<AuthPolicy | undefined>();
  const [authError, setAuthError] = useState<string>();
  const [identity, setIdentity] = useState<AuthIdentity | undefined>();
  const [authRetryNonce, setAuthRetryNonce] = useState(0);
  const { route, navigate } = useBrowserRoute();

  const requestedReturnPath = useMemo(() => {
    if (route.pathname === LOGIN_PATH) return returnPathFromLoginSearch(route.search);
    return safeReturnPath(routeHref(route));
  }, [route]);

  const checkApi = useCallback(() => {
    setHealth('checking');
    api.health()
      .then((data) => {
        setHealth(`${data.status} / ${data.service}`);
        return api.diagnostics();
      })
      .then(setDiagnostics)
      .catch((err) => {
        setHealth(`offline: ${err.message}`);
        setDiagnostics(undefined);
      });
  }, []);

  useEffect(() => {
    let active = true;
    setAuthChecking(true);
    setAuthError(undefined);
    api.authStatus()
      .then(async (status) => {
        if (!active) return;
        setAuthPolicy(status);
        if (!status.loginRequired) {
          setIdentity({ actor: 'local-development', role: 'admin', authenticated: false, authMode: 'local' });
          return;
        }
        try {
          const current = await api.me();
          if (active) setIdentity(current.identity);
        } catch {
          if (active) setIdentity(undefined);
        }
      })
      .catch((error) => {
        if (!active) return;
        setIdentity(undefined);
        setAuthPolicy((value) => value ?? { loginRequired: true, mode: 'session', sessionTtlSeconds: 0 });
        setAuthError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setAuthChecking(false);
      });
    return () => { active = false; };
  }, [authRetryNonce]);

  useEffect(() => {
    if (!authError || identity) return;
    const timer = window.setTimeout(() => setAuthRetryNonce((value) => value + 1), 15000);
    return () => window.clearTimeout(timer);
  }, [authError, identity]);

  useEffect(() => {
    const unauthorized = () => {
      const current = readBrowserRoute();
      const returnTo = current.pathname === LOGIN_PATH
        ? returnPathFromLoginSearch(current.search)
        : safeReturnPath(routeHref(current));
      setSelected(undefined);
      setIdentity(undefined);
      setAuthPolicy((value) => value ?? { loginRequired: true, mode: 'session', sessionTtlSeconds: 0 });
      navigate(buildLoginHref(returnTo, 'expired'), true);
    };
    window.addEventListener('pitguard:unauthorized', unauthorized);
    return () => window.removeEventListener('pitguard:unauthorized', unauthorized);
  }, [navigate]);

  useEffect(() => {
    if (authChecking) return;
    if (identity) {
      if (route.pathname === LOGIN_PATH) navigate(returnPathFromLoginSearch(route.search), true);
      return;
    }
    if ((authPolicy?.loginRequired || authError) && route.pathname !== LOGIN_PATH) {
      navigate(buildLoginHref(routeHref(route), authError ? 'offline' : 'required'), true);
    }
  }, [authChecking, authError, authPolicy, identity, navigate, route]);

  useEffect(() => { if (identity) checkApi(); }, [checkApi, identity]);

  async function logout() {
    try { await api.logout(); } finally {
      setSelected(undefined);
      setIdentity(undefined);
      setAuthPolicy((value) => value ?? { loginRequired: true, mode: 'session', sessionTtlSeconds: 0 });
      navigate(buildLoginHref('/', 'logout'), true);
    }
  }

  function authenticated(value: AuthIdentity) {
    const returnTo = requestedReturnPath;
    setIdentity(value);
    setAuthError(undefined);
    navigate(returnTo, true);
  }

  if (authChecking) {
    return <main className="loginLoading" aria-live="polite"><div className="loginBrandMark">PG</div><p>正在验证登录状态…</p><small>超过 5 秒将进入可重试的离线登录页，不会无限等待。</small></main>;
  }

  if (!identity) {
    return (
      <LoginPage
        onAuthenticated={authenticated}
        returnTo={requestedReturnPath}
        notice={loginReasonMessage(route.search)}
        serviceError={authError}
        onRetryService={() => setAuthRetryNonce((value) => value + 1)}
      />
    );
  }

  const offline = !health.startsWith('ok');
  const missingModules = diagnostics?.missingModules ?? [];
  const isDocs = route.pathname === '/docs';

  if (isDocs) return <Suspense fallback={<main className="page">正在加载文档…</main>}><DocsPage /></Suspense>;

  return (
    <div className="appShell">
      <header className="topBar">
        <div>
          <h1>PitGuard BIM Designer</h1>
          <p>基坑围护结构设计 MVP · 结果需注册岩土/结构工程师复核</p>
        </div>
        <div className="apiStatusGroup">
          <a className="topLink" href="/docs">设计与计算文档</a>
          <span className={health.startsWith('ok') ? 'badge ok' : 'badge warn'}>API {health}</span>
          <span className="userBadge">{identity.username ?? identity.actor} · {identity.role}</span>
          <button className="secondary compactButton" onClick={checkApi}>重检后端</button>
          <button className="secondary compactButton" onClick={() => void logout()}>退出登录</button>
        </div>
      </header>

      {(offline || missingModules.length > 0) && (
        <section className="apiDiagnosticBanner card">
          <div>
            <strong>{offline ? '后端未连接' : '后端依赖不完整'}</strong>
            <p>
              {offline
                ? '请先运行根目录 start-linux.sh 或 start-windows.bat。启动脚本现在使用当前 Python 环境，不再创建额外虚拟环境。'
                : `缺失模块：${missingModules.join('、')}。建议重新运行一键启动脚本，或手动执行 python -m pip install -e services/api[dev]。`}
            </p>
          </div>
          <button onClick={checkApi}>重新检测</button>
        </section>
      )}

      {diagnostics && (
        <section className="runtimeRibbon">
          <span>API v{diagnostics.version}</span>
          <span>算法 {diagnostics.algorithmVersion ?? diagnostics.version}</span>
          <span>规则集 {diagnostics.ruleSetVersion ?? '-'}</span>
          <span>Python {diagnostics.pythonVersion}</span>
          <span>数据库：{diagnostics.databaseConfigured ? '已配置' : '默认本地库'}</span>
        </section>
      )}

      {selected ? <Suspense fallback={<main className="page"><section className="card">正在加载工程工作台…</section></main>}><ProjectWorkspace project={selected} onBack={() => setSelected(undefined)} onProjectChange={setSelected} /></Suspense> : <ProjectsPage onOpen={setSelected} />}
    </div>
  );
}
