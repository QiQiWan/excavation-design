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
  projectIdFromPath,
  projectPath,
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

type RuntimeReadiness = {
  status?: 'ready' | 'degraded' | 'not_ready' | string;
  ready?: boolean;
  degraded?: boolean;
  degradedReasons?: string[];
  blockingReasons?: string[];
  tasks?: Record<string, unknown>;
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
  const [readiness, setReadiness] = useState<RuntimeReadiness | undefined>();
  const [selected, setSelected] = useState<Project | undefined>();
  const [restoringProject, setRestoringProject] = useState(false);
  const [projectRestoreError, setProjectRestoreError] = useState<string>();
  const [authChecking, setAuthChecking] = useState(true);
  const [authPolicy, setAuthPolicy] = useState<AuthPolicy | undefined>();
  const [authError, setAuthError] = useState<string>();
  const [identity, setIdentity] = useState<AuthIdentity | undefined>();
  const [authRetryNonce, setAuthRetryNonce] = useState(0);
  const { route, navigate } = useBrowserRoute();
  const routedProjectId = useMemo(() => projectIdFromPath(route.pathname), [route.pathname]);

  const requestedReturnPath = useMemo(() => {
    if (route.pathname === LOGIN_PATH) return returnPathFromLoginSearch(route.search);
    return safeReturnPath(routeHref(route));
  }, [route]);

  const checkApi = useCallback(() => {
    setHealth('checking');
    Promise.all([api.health(), api.diagnostics(), api.systemReadiness()])
      .then(([data, details, runtime]) => {
        setHealth(`${data.status} / ${data.service}`);
        setDiagnostics(details);
        setReadiness(runtime as RuntimeReadiness);
      })
      .catch((err) => {
        setHealth(`offline: ${err.message}`);
        setDiagnostics(undefined);
        setReadiness({ status: 'not_ready', ready: false, blockingReasons: ['API 或数据库就绪检查失败'] });
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


  useEffect(() => {
    if (!identity) return;
    let cancelled = false;
    let timer = 0;
    let failures = 0;
    const schedule = (delay: number) => { window.clearTimeout(timer); timer = window.setTimeout(run, delay); };
    const run = async () => {
      if (cancelled) return;
      if (document.hidden) { schedule(30000); return; }
      try {
        const value = await api.systemReadiness();
        if (cancelled) return;
        failures = 0;
        setReadiness(value as RuntimeReadiness);
        schedule(String((value as RuntimeReadiness).status ?? '') === 'ready' ? 30000 : 12000);
      } catch {
        if (cancelled) return;
        failures += 1;
        if (failures >= 2) setReadiness((previous) => ({ ...(previous ?? {}), status: 'not_ready', ready: false, blockingReasons: ['运行时健康监测连续失败，后台任务状态可能暂时不可读'] }));
        schedule(Math.min(60000, 5000 * (2 ** Math.min(failures, 3))));
      }
    };
    const resume = () => { if (!document.hidden) void run(); };
    const online = () => void run();
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', online);
    void run();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('online', online);
    };
  }, [identity]);

  useEffect(() => {
    if (!identity || !routedProjectId) return;
    if (selected?.id === routedProjectId) return;
    let cancelled = false;
    setRestoringProject(true);
    setProjectRestoreError(undefined);
    api.getProject(routedProjectId)
      .then((project) => {
        if (!cancelled && project?.id) setSelected(project);
      })
      .catch((error) => {
        if (cancelled) return;
        setSelected(undefined);
        setProjectRestoreError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => { if (!cancelled) setRestoringProject(false); });
    return () => { cancelled = true; };
  }, [identity, routedProjectId, selected?.id]);

  const openProject = useCallback((project: Project) => {
    if (!project?.id) return;
    setSelected(project);
    setProjectRestoreError(undefined);
    navigate(projectPath(project.id));
  }, [navigate]);

  const leaveProject = useCallback(() => {
    setSelected(undefined);
    setProjectRestoreError(undefined);
    navigate('/');
  }, [navigate]);

  const updateSelectedProject = useCallback((project: Project) => {
    // A transient empty workspace response must never eject the user from the
    // active project. Keep the current selection unless a valid project object
    // with the same identity is returned.
    if (!project?.id) return;
    setSelected((current) => current && current.id !== project.id ? current : project);
  }, []);

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
  const runtimeStatus = String(readiness?.status ?? (offline ? 'not_ready' : 'ready'));
  const runtimeReasons = [...(readiness?.blockingReasons ?? []), ...(readiness?.degradedReasons ?? [])];
  const missingModules = diagnostics?.missingModules ?? [];
  const isDocs = route.pathname === '/docs';

  if (isDocs) return <><GlobalRequestProgress /><Suspense fallback={<FullPageLoadingFallback label="正在加载文档中心" />}><DocsPage /></Suspense></>;

  if (routedProjectId && restoringProject && !selected) {
    return <><GlobalRequestProgress /><FullPageLoadingFallback label="正在恢复工程工作台" detail="保存设计基准后仍保持当前工程和当前步骤。" /></>;
  }

  return (
    <div className="appShell">
      <GlobalRequestProgress />
      <header className="topBar coreTopBar">
        <div className="pitGuardBrand"><div><h1>PitGuard</h1><p>基坑围护结构设计</p></div><span className="systemVersionBadge">当前系统版本 V{diagnostics?.softwareVersion ?? diagnostics?.version ?? '检测中'}</span></div>
        <div className="apiStatusGroup">
          <span className={health.startsWith('ok') ? 'badge ok' : 'badge warn'}>{health.startsWith('ok') ? '服务正常' : '服务异常'}</span>
          <span className={`badge runtimeHealthBadge ${runtimeStatus}`}>{runtimeStatus === 'ready' ? '流程稳定' : runtimeStatus === 'degraded' ? '流程降级' : '流程阻断'}</span>
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

      {!offline && runtimeStatus !== 'ready' && <section className={`runtimeHealthBanner ${runtimeStatus === 'not_ready' ? 'blocking' : ''}`} role="status">
        <div><strong>{runtimeStatus === 'not_ready' ? '工程流程暂时不可安全执行' : '工程流程处于降级状态'}</strong><span>{runtimeReasons.slice(0, 4).join('；') || '后台资源或计算 worker 状态异常。'}</span></div>
        <button type="button" className="secondary compactButton" onClick={checkApi}>立即复检</button>
      </section>}

      {projectRestoreError ? <section className="apiDiagnosticBanner card compactDiagnostic"><strong>工程工作台恢复失败：{projectRestoreError}</strong><button onClick={() => { setProjectRestoreError(undefined); navigate('/'); }}>返回项目列表</button></section> : null}
      {selected ? <Suspense fallback={<FullPageLoadingFallback label="正在加载工程工作台" detail="正在读取核心工程数据。" />}><ProjectWorkspace project={selected} onBack={leaveProject} onProjectChange={updateSelectedProject} /></Suspense> : routedProjectId && !projectRestoreError ? <FullPageLoadingFallback label="正在恢复工程工作台" detail="正在按工程地址重新读取项目，不会跳回项目列表。" /> : <ProjectsPage onOpen={openProject} />}
    </div>
  );
}
