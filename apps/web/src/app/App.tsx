import { useEffect, useState } from 'react';
import { api } from '../api/client';
import ProjectsPage from '../pages/ProjectsPage';
import ProjectWorkspace from '../pages/ProjectWorkspace';
import type { Project } from '../types/domain';

type Diagnostics = {
  version: string;
  pythonExecutable: string;
  pythonVersion: string;
  databasePath?: string;
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

  return (
    <div className="appShell">
      <header className="topBar">
        <div>
          <h1>PitGuard BIM Designer</h1>
          <p>基坑围护结构设计 MVP · 结果需注册岩土/结构工程师复核</p>
        </div>
        <div className="apiStatusGroup">
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
          <span>Python {diagnostics.pythonVersion}</span>
          <span title={diagnostics.pythonExecutable}>当前解释器：{diagnostics.pythonExecutable}</span>
          <span title={diagnostics.databasePath}>数据库：{diagnostics.databasePath ?? '-'}</span>
        </section>
      )}

      {selected ? <ProjectWorkspace project={selected} onBack={() => setSelected(undefined)} onProjectChange={setSelected} /> : <ProjectsPage onOpen={setSelected} />}
    </div>
  );
}
