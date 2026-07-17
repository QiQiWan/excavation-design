import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Project, ProjectSummary } from '../types/domain';

const PROJECT_CACHE_KEY = 'pitguard-project-summaries-v332';

type PendingCreate = ProjectSummary & { optimistic?: boolean };

function readCachedProjects(): ProjectSummary[] {
  try {
    const raw = window.sessionStorage.getItem(PROJECT_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { savedAt?: number; projects?: ProjectSummary[] };
    if (!Array.isArray(parsed.projects)) return [];
    return parsed.projects;
  } catch { return []; }
}

function writeCachedProjects(projects: ProjectSummary[]) {
  try { window.sessionStorage.setItem(PROJECT_CACHE_KEY, JSON.stringify({ savedAt: Date.now(), projects })); } catch { /* Storage can be disabled. */ }
}

function summaryFromProject(project: Project): ProjectSummary {
  return {
    id: project.id,
    revision: 1,
    name: project.name,
    location: project.location,
    createdAt: project.createdAt,
    updatedAt: project.updatedAt,
    hasExcavation: Boolean(project.excavation),
    hasRetainingSystem: Boolean(project.retainingSystem),
    calculationCaseCount: project.calculationCases?.length ?? 0,
    calculationResultCount: project.calculationResults?.length ?? 0,
    payloadBytes: 0,
    workspaceBytes: 0,
    externalBytes: 0,
    artifactCount: 0,
    storageStatus: 'normal',
  } as ProjectSummary;
}

export default function ProjectsPage({ onOpen }: { onOpen: (project: Project) => void }) {
  const initial = useMemo(() => readCachedProjects(), []);
  const [projects, setProjects] = useState<PendingCreate[]>(initial);
  const [name, setName] = useState('新建基坑项目');
  const [location, setLocation] = useState('');
  const [error, setError] = useState<string | undefined>();
  const [deletingId, setDeletingId] = useState<string | undefined>();
  const [openingId, setOpeningId] = useState<string | undefined>();
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(initial.length === 0);
  const [openProgress, setOpenProgress] = useState(0);

  async function refresh(force = false) {
    setRefreshing(true);
    setError(undefined);
    try {
      const items = await api.listProjects(force);
      setProjects(items);
      writeCachedProjects(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setRefreshing(false); }
  }
  useEffect(() => { void refresh(false); }, []);

  async function createProject() {
    if (creating || !name.trim()) return;
    const temporaryId = `pending-${Date.now()}`;
    const now = new Date().toISOString();
    const optimistic: PendingCreate = {
      id: temporaryId,
      name: name.trim(),
      location: location.trim() || undefined,
      createdAt: now,
      updatedAt: now,
      revision: 0,
      hasExcavation: false,
      hasRetainingSystem: false,
      calculationCaseCount: 0,
      calculationResultCount: 0,
      storageStatus: 'normal',
      optimistic: true,
    } as PendingCreate;
    setCreating(true);
    setError(undefined);
    setProjects((items) => [optimistic, ...items]);
    try {
      const project = await api.createProject({ name: name.trim(), location: location.trim() || undefined });
      const summary = summaryFromProject(project);
      setProjects((items) => {
        const next = items.map((item) => item.id === temporaryId ? summary : item);
        writeCachedProjects(next);
        return next;
      });
      onOpen(project);
    } catch (err) {
      setProjects((items) => items.filter((item) => item.id !== temporaryId));
      setError(err instanceof Error ? err.message : String(err));
    } finally { setCreating(false); }
  }

  async function openProject(project: PendingCreate) {
    if (openingId || project.optimistic) return;
    let timer: number | undefined;
    try {
      setError(undefined);
      setOpeningId(project.id);
      setOpenProgress(8);
      timer = window.setInterval(() => setOpenProgress((value) => Math.min(92, value + Math.max(1, Math.round((94 - value) / 9)))), 180);
      const loaded = await api.getProject(project.id);
      setOpenProgress(100);
      onOpen(loaded);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (timer) window.clearInterval(timer);
      window.setTimeout(() => { setOpeningId(undefined); setOpenProgress(0); }, 180);
    }
  }

  async function deleteProject(project: PendingCreate) {
    const confirmed = window.confirm(`确认永久删除项目“${project.name}”吗？项目数据、任务记录和已生成的后台成果将一并清理。此操作无法撤销。`);
    if (!confirmed || deletingId) return;
    const previous = projects;
    try {
      setError(undefined);
      setDeletingId(project.id);
      setProjects((items) => items.filter((item) => item.id !== project.id));
      await api.deleteProject(project.id);
      writeCachedProjects(previous.filter((item) => item.id !== project.id));
    } catch (err) {
      setProjects(previous);
      setError(err instanceof Error ? err.message : String(err));
    } finally { setDeletingId(undefined); }
  }

  return (
    <main className="page" aria-busy={refreshing || creating || Boolean(openingId)}>
      <section className="card">
        <div className="panelTitleRow"><div><h2>项目列表</h2><p className="small">列表优先使用本地摘要即时显示，服务器数据在后台刷新。</p></div>{refreshing ? <span className="inlineBusy"><i />同步中</span> : <span className="statusTag pass">已同步</span>}</div>
        <div className="toolbar">
          <input aria-label="项目名称" value={name} onChange={(event) => setName(event.target.value)} disabled={creating} />
          <input aria-label="项目地点" placeholder="项目地点" value={location} onChange={(event) => setLocation(event.target.value)} disabled={creating} />
          <button onClick={() => void createProject()} disabled={creating || !name.trim()}>{creating ? <><span className="buttonSpinner" />创建中</> : '新建项目'}</button>
          <button className="secondary" onClick={() => void refresh(true)} disabled={refreshing}>{refreshing ? '刷新中…' : '刷新'}</button>
        </div>
        {creating ? <div className="inlineOperationProgress"><span style={{ width: '62%' }} /><small>正在写入项目核心数据和初始修订，请勿重复点击。</small></div> : null}
        {error && <div className="error">{error}</div>}
      </section>
      <section className="card">
        <table className="table">
          <thead><tr><th>名称</th><th>地点</th><th>存储</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody>
            {projects.map((project) => (
              <tr key={project.id} className={project.optimistic ? 'optimisticRow' : undefined}>
                <td>{project.name}{project.optimistic ? <small className="pendingLabel">正在创建</small> : null}</td>
                <td>{project.location ?? '-'}</td>
                <td><span className={`statusTag ${project.storageStatus === 'workspace_only' || project.storageStatus === 'large' ? 'warning' : project.storageStatus === 'elevated' ? 'info' : 'pass'}`} title={project.storageStatus === 'workspace_only' ? '完整快照由后台 worker 按需读取，网页仅加载轻量工作区。' : undefined}>{project.optimistic ? '准备中' : project.storageStatus === 'workspace_only' ? `工作区模式 · 核心 ${((project.payloadBytes ?? 0) / 1048576).toFixed(1)} MB` : project.payloadBytes ? `核心 ${(project.payloadBytes / 1048576).toFixed(1)} MB${project.externalBytes ? ` · 外部 ${(project.externalBytes / 1048576).toFixed(1)} MB` : ''}` : '常规'}</span></td>
                <td>{new Date(project.updatedAt).toLocaleString()}</td>
                <td>
                  <div className="table-actions">
                    <button disabled={Boolean(deletingId || openingId || project.optimistic)} onClick={() => void openProject(project)}>{openingId === project.id ? <><span className="buttonSpinner" />安全加载 {openProgress}%</> : '打开'}</button>
                    <button className="danger" disabled={Boolean(deletingId || openingId || project.optimistic)} onClick={() => void deleteProject(project)}>{deletingId === project.id ? <><span className="buttonSpinner" />删除中</> : '删除'}</button>
                  </div>
                  {openingId === project.id ? <div className="rowProgress"><span style={{ width: `${openProgress}%` }} /></div> : null}
                </td>
              </tr>
            ))}
            {projects.length === 0 && !refreshing && <tr><td colSpan={5}>暂无项目</td></tr>}
            {projects.length === 0 && refreshing && <tr><td colSpan={5}><span className="inlineBusy"><i />正在加载项目列表</span></td></tr>}
          </tbody>
        </table>
      </section>
    </main>
  );
}
