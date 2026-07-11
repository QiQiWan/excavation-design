import { lazy, Suspense, useEffect, useState } from 'react';
import { api } from '../api/client';
import ProjectsPage from '../pages/ProjectsPage';
const ProjectWorkspace = lazy(() => import('../pages/ProjectWorkspace'));
const DocsPage = lazy(() => import('../pages/DocsPage'));
import type { Project } from '../types/domain';

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

export default function App() {
  const [health, setHealth] = useState('checking');
  const [diagnostics, setDiagnostics] = useState<Diagnostics | undefined>();
  const [selected, setSelected] = useState<Project | undefined>();

  const checkApi = () => {
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
  };

  useEffect(() => { checkApi(); }, []);

  const offline = !health.startsWith('ok');
  const missingModules = diagnostics?.missingModules ?? [];
  const isDocs = window.location.pathname === '/docs';

  if (isDocs) return <Suspense fallback={<main className="page">正在加载文档…</main>}><DocsPage /></Suspense>;

  return (
    <div className="appShell">
      <header className="topBar">
        <div>
          <h1>PitGuard BIM Designer</h1>
          <p>基坑围护结构设计 MVP · 结果需注册岩土/结构工程师复核</p>
        </div>
        <div className="apiStatusGroup">
          <a className="topLink" href="/docs">操作文档</a>
          <span className={health.startsWith('ok') ? 'badge ok' : 'badge warn'}>API {health}</span>
          <button className="secondary compactButton" onClick={checkApi}>重检后端</button>
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
