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
  const [error, setError] = useState<string>();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([api.getProjectStorageHealth(project.id), api.listProjectArtifacts(project.id)])
      .then(([nextHealth, nextManifest]) => { if (alive) { setHealth(nextHealth); setManifest(nextManifest); } })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt]);

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

  if (error) return <div className="warning">数据工作集状态读取失败：{error}</div>;
  return <section className="dataWorkspacePanel card" aria-label="项目冷热数据工作集">
    <div className="dataWorkspaceSummary">
      <div><strong>数据工作集</strong><span>核心设计常驻 · 大型结果按需加载 · 文件由 Nginx 直传</span></div>
      <div className="dataWorkspaceMetrics">
        <span>工作区 <b>{mb(Number(health?.workspaceBytes ?? 0))}</b></span>
        <span>主快照 <b>{mb(Number(health?.payloadBytes ?? 0))}</b></span>
        <span>外部对象 <b>{mb(Number(health?.externalBytes ?? manifest?.storedBytes ?? 0))}</b></span>
        <span>对象数 <b>{String(health?.artifactCount ?? manifest?.artifactCount ?? 0)}</b></span>
      </div>
      <button className="secondary tiny" onClick={() => setOpen((value) => !value)}>{open ? '收起数据索引' : '查看数据索引'}</button>
    </div>
    {open ? <div className="dataWorkspaceGroups">
      {groups.length ? groups.map(([kind, value]) => <article key={kind}>
        <strong>{kind}</strong><span>{value.count} 个分片</span><em>{mb(value.storedBytes)} / 逻辑 {mb(value.logicalBytes)}</em>
      </article>) : <p>当前项目尚无外部大型数据对象；后续计算、地质和配筋结果会自动分片保存。</p>}
    </div> : null}
  </section>;
}
