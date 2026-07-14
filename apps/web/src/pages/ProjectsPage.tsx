import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Project, ProjectSummary } from '../types/domain';

export default function ProjectsPage({ onOpen }: { onOpen: (project: Project) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [name, setName] = useState('新建基坑项目');
  const [location, setLocation] = useState('');
  const [error, setError] = useState<string | undefined>();
  const [deletingId, setDeletingId] = useState<string | undefined>();

  const refresh = () => {
    void api.listProjects().then(setProjects).catch((err) => setError(err.message));
  };
  useEffect(() => { refresh(); }, []);

  async function createProject() {
    try {
      setError(undefined);
      const project = await api.createProject({ name, location });
      refresh();
      onOpen(project);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function deleteProject(project: ProjectSummary) {
    const confirmed = window.confirm(`确认永久删除项目“${project.name}”吗？项目数据、任务记录和已生成的后台成果将一并清理。此操作无法撤销。`);
    if (!confirmed) return;
    try {
      setError(undefined);
      setDeletingId(project.id);
      await api.deleteProject(project.id);
      setProjects((items) => items.filter((item) => item.id !== project.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingId(undefined);
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
                <td>
                  <div className="table-actions">
                    <button disabled={deletingId === project.id} onClick={() => void api.getProject(project.id).then(onOpen).catch((err) => setError(err.message))}>打开</button>
                    <button className="danger" disabled={deletingId === project.id} onClick={() => void deleteProject(project)}>
                      {deletingId === project.id ? '删除中…' : '删除'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {projects.length === 0 && <tr><td colSpan={4}>暂无项目</td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}
