import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../types/domain';

interface ArtifactRef {
  artifactId: string;
  kind: string;
  logicalBytes?: number;
  storedBytes?: number;
  itemCount?: number;
  available?: boolean;
  metadata?: Record<string, unknown>;
}

interface ArtifactManifest {
  projectId: string;
  artifactCount: number;
  storedBytes: number;
  logicalBytes: number;
  artifacts: ArtifactRef[];
}

const mb = (value?: number) => `${((value ?? 0) / 1048576).toFixed((value ?? 0) >= 10485760 ? 1 : 2)} MB`;

export default function ProjectDataWorkspacePanel({ project }: { project: Project }) {
  const [health, setHealth] = useState<Record<string, unknown>>();
  const [manifest, setManifest] = useState<ArtifactManifest>();
  const [healthError, setHealthError] = useState<string>();
  const [manifestError, setManifestError] = useState<string>();
  const [manifestLoading, setManifestLoading] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    setManifest(undefined);
    setManifestError(undefined);
    setHealthError(undefined);
    api.getProjectStorageHealth(project.id)
      .then((nextHealth) => { if (alive) setHealth(nextHealth); })
      .catch((err) => { if (alive) setHealthError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt]);

  useEffect(() => {
    if (!open || manifest) return;
    let alive = true;
    setManifestLoading(true);
    setManifestError(undefined);
    api.listProjectArtifacts(project.id)
      .then((nextManifest) => { if (alive) setManifest(nextManifest); })
      .catch((err) => { if (alive) setManifestError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (alive) setManifestLoading(false); });
    return () => { alive = false; };
  }, [open, manifest, project.id]);

  const resourcePolicy = (health?.resourcePolicy && typeof health.resourcePolicy === 'object' ? health.resourcePolicy : {}) as Record<string, unknown>;
  const workspaceHealthy = Boolean(health?.workspaceLoadAllowed ?? true);
  const fullInteractive = Boolean(health?.fullLoadAllowed ?? true);

  const groups = useMemo(() => {
    const output = new Map<string, { count: number; storedBytes: number; logicalBytes: number }>();
    for (const item of manifest?.artifacts ?? []) {
      const current = output.get(item.kind) ?? { count: 0, storedBytes: 0, logicalBytes: 0 };
      current.count += 1;
      current.storedBytes += item.storedBytes ?? 0;
      current.logicalBytes += item.logicalBytes ?? 0;
      output.set(item.kind, current);
    }
    return [...output.entries()].sort((a, b) => b[1].storedBytes - a[1].storedBytes);
  }, [manifest]);

  return <section className="dataWorkspacePanel card" aria-label="项目冷热数据工作集">
    <div className="dataWorkspaceSummary">
      <div><strong>数据工作集</strong><span>核心设计常驻 · 大型结果按需加载 · 文件由 Nginx 直传</span></div>
      <div className="dataWorkspaceMetrics">
        <span>工作区 <b>{mb(Number(health?.workspaceBytes ?? 0))}</b> / 动态预算 {mb(Number(health?.workspaceLimitBytes ?? 0))}</span>
        <span>主快照 <b>{mb(Number(health?.payloadBytes ?? 0))}</b> / API动态预算 {mb(Number(health?.apiFullLoadLimitBytes ?? 0))}</span>
        <span>外部对象 <b>{mb(Number(health?.externalBytes ?? manifest?.storedBytes ?? 0))}</b></span>
        <span>运行模式 <b>{workspaceHealthy ? (fullInteractive ? '标准' : '工作区优先') : '工作区需优化'}</b></span>
      </div>
      <button className="secondary tiny" onClick={() => setOpen((value) => !value)}>{open ? '收起数据索引' : '查看数据索引'}</button>
    </div>
    {healthError ? <div className="warning">数据工作集状态读取失败：{healthError}</div> : null}
    {open ? <>
      <div className="dataWorkspacePolicy">
        <span>有效可用内存 <b>{mb(Number(resourcePolicy.effectiveAvailableBytes ?? 0))}</b></span>
        <span>系统保留 <b>{mb(Number(resourcePolicy.reserveBytes ?? 0))}</b></span>
        <span>worker 软/硬预算 <b>{mb(Number(resourcePolicy.workerSoftLimitBytes ?? 0))} / {mb(Number(resourcePolicy.workerHardLimitBytes ?? 0))}</b></span>
        <span>重型任务并发 <b>{String(resourcePolicy.recommendedHeavyConcurrency ?? '—')}</b></span>
        <p>{String(resourcePolicy.policyExplanation ?? '网页使用轻量工作区，重型对象由后台任务按需读取。')}</p>
      </div>
      <div className="dataWorkspaceGroups">
      {manifestLoading ? <p>正在按需读取外部对象索引…</p> : manifestError ? <p className="error">外部对象索引读取失败：{manifestError}</p> : groups.length ? groups.map(([kind, value]) => <article key={kind}>
        <strong>{kind}</strong><span>{value.count} 个分片</span><em>{mb(value.storedBytes)} / 逻辑 {mb(value.logicalBytes)}</em>
      </article>) : <p>当前项目尚无外部大型数据对象；后续计算、地质和配筋结果会自动分片保存。</p>}
      </div>
    </> : null}
  </section>;
}
