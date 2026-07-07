import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../types/domain';

export default function ProjectsPage({ onOpen }: { onOpen: (project: Project) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState('新建基坑项目');
  const [location, setLocation] = useState('');
  const [error, setError] = useState<string | undefined>();

  const refresh = () => {
    void api.listProjects().then(setProjects).catch((err) => setError(err.message));
  };
  useEffect(() => { refresh(); }, []);

  async function createProject() {
    try {
      setError(undefined);
      const project = await api.createProject({ name, location });
      setProjects([project, ...projects]);
      onOpen(project);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <main className="page">
      <section className="card">
        <h2>项目列表</h2>
        <div className="toolbar">
          <input aria-label="项目名称" value={name} onChange={(event) => setName(event.target.value)} />
          <input aria-label="项目地点" placeholder="项目地点" value={location} onChange={(event) => setLocation(event.target.value)} />
          <button onClick={createProject}>新建项目</button>
          <button className="secondary" onClick={refresh}>刷新</button>
        </div>
        {error && <div className="error">{error}</div>}
      </section>
      <section className="card">
        <table className="table">
          <thead><tr><th>名称</th><th>地点</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody>
            {projects.map((project) => (
              <tr key={project.id}>
                <td>{project.name}</td>
                <td>{project.location ?? '-'}</td>
                <td>{new Date(project.updatedAt).toLocaleString()}</td>
                <td><button onClick={() => onOpen(project)}>打开</button></td>
              </tr>
            ))}
            {projects.length === 0 && <tr><td colSpan={4}>暂无项目</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}
