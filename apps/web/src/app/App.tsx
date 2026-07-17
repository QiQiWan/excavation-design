import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { api, type AuthIdentity } from '../api/client';
import ProjectsPage from '../pages/ProjectsPage';
const ProjectWorkspace = lazy(() => import('../pages/CoreProjectWorkspace'));
const DocsPage = lazy(() => import('../pages/DocsPage'));
import type { Project } from '../types/domain';
import LoginPage from '../pages/LoginPage';
import { FullPageLoadingFallback, GlobalRequestProgress } from './GlobalRequestProgress';
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
    Promise.all([api.health(), api.diagnostics()])
      .then(([data, details]) => {
        setHealth(`${data.status} / ${data.service}`);
        setDiagnostics(details);
      })
      .catch((err) => {
        setHealth(`offline: ${err.message}`);
        setDiagnostics(undefined);
      });
  }, []);

  useEffect(() => {
    let active = true;
    setAuthChecking(true);
    setAuthError(undefined);
    api.authBootstrap()
      .then((status) => {
        if (!active) return;
        setAuthPolicy(status);
        if (!status.loginRequired) {
          setIdentity(status.identity ?? { actor: 'local-development', role: 'admin', authenticated: false, authMode: 'local' });
          return;
        }
        setIdentity(status.authenticated ? status.identity : undefined);
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
    return <><GlobalRequestProgress /><FullPageLoadingFallback label="正在验证登录状态" detail="超过 5 秒将进入可重试的离线登录页，不会无限等待。" /></>;
  }

  if (!identity) {
    return (
      <><GlobalRequestProgress /><LoginPage
        onAuthenticated={authenticated}
        returnTo={requestedReturnPath}
        notice={loginReasonMessage(route.search)}
        serviceError={authError}
        onRetryService={() => setAuthRetryNonce((value) => value + 1)}
      /></>
    );
  }

  const offline = !health.startsWith('ok');
  const missingModules = diagnostics?.missingModules ?? [];
  const isDocs = route.pathname === '/docs';

  if (isDocs) return <><GlobalRequestProgress /><Suspense fallback={<FullPageLoadingFallback label="正在加载文档中心" />}><DocsPage /></Suspense></>;

  return (
    <div className="appShell">
      <GlobalRequestProgress />
      <header className="topBar coreTopBar">
        <div className="pitGuardBrand"><div><h1>PitGuard</h1><p>基坑围护结构设计</p></div><span className="systemVersionBadge">当前系统版本 V{diagnostics?.softwareVersion ?? diagnostics?.version ?? '检测中'}</span></div>
        <div className="apiStatusGroup">
          <span className={health.startsWith('ok') ? 'badge ok' : 'badge warn'}>{health.startsWith('ok') ? '服务正常' : '服务异常'}</span>
          <details className="coreSystemMenu"><summary>{identity.username ?? identity.actor}</summary><div>
            <span>角色：{identity.role}</span>
            {diagnostics ? <span>版本：{diagnostics.version}</span> : null}
            <a href="/docs">设计说明</a>
            <button className="secondary compactButton" onClick={checkApi}>检查后端</button>
            <button className="secondary compactButton" onClick={() => void logout()}>退出</button>
          </div></details>
        </div>
      </header>

      {(offline || missingModules.length > 0) && <section className="apiDiagnosticBanner card compactDiagnostic">
        <strong>{offline ? '后端未连接' : `缺少依赖：${missingModules.join('、')}`}</strong>
        <button onClick={checkApi}>重新检测</button>
      </section>}

      {selected ? <Suspense fallback={<FullPageLoadingFallback label="正在加载工程工作台" detail="正在读取核心工程数据。" />}><ProjectWorkspace project={selected} onBack={() => setSelected(undefined)} onProjectChange={setSelected} /></Suspense> : <ProjectsPage onOpen={setSelected} />}
    </div>
  );
}
