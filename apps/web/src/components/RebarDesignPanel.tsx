import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { DrawingSetManifest, Project, RebarDesignScheme } from '../types/domain';
import { SupportRebarPreview, WallZoneElevationPreview } from './RebarDrawingPreview';

type RebarMode = 'conservative' | 'balanced' | 'economic';
type ActiveGroup = 'issues' | 'walls' | 'supports' | 'beams' | 'drawings';
type StatusFilter = 'problems' | 'all' | 'fail' | 'warning' | 'pass';

const statusText: Record<string, string> = {
  pass: '通过', warning: '需复核', manual_review: '人工复核', preliminary: '初步方案', fail: '阻断',
};
const categoryText: Record<string, string> = {
  wall_reinforcement: '墙体配筋', support_reinforcement: '支撑配筋', beam_reinforcement: '围檩配筋',
  node_congestion: '节点承压与拥挤', calculation: '计算有效性', other: '其他',
};
const reasonText: Record<string, string> = {
  WALL_REBAR_CATALOG_EXHAUSTED: '墙体钢筋组合达到上限',
  WALL_SECTION_CAPACITY: '墙体截面承载力不足',
  SUPPORT_REBAR_CAPACITY: '支撑截面或配筋不足',
  SUPPORT_TOPOLOGY_UPGRADE: '支撑传力体系需调整',
  NODE_BEARING_CAPACITY: '节点承压不足',
  NODE_BEARING_HIGH_UTILIZATION: '节点承压利用率较高',
};

function n(value: unknown, digits = 2): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '-';
}
function statusTone(status: unknown): string {
  const text = String(status ?? 'manual_review');
  if (text === 'pass') return 'pass';
  if (text === 'fail') return 'fail';
  return 'warn';
}
function localizedStatus(status: unknown): string { return statusText[String(status ?? '')] ?? String(status ?? '-'); }
function record(value: unknown): Record<string, any> { return (value && typeof value === 'object' ? value : {}) as Record<string, any>; }
function searchable(row: Record<string, any>): string { return JSON.stringify(row).toLowerCase(); }
function rowStatus(row: Record<string, any>): string { return String(row.status ?? row.checkStatus ?? 'manual_review'); }
function passesStatus(row: Record<string, any>, filter: StatusFilter): boolean {
  const status = rowStatus(row);
  if (filter === 'all') return true;
  if (filter === 'problems') return status !== 'pass';
  if (filter === 'warning') return ['warning', 'manual_review', 'preliminary'].includes(status);
  return status === filter;
}

export default function RebarDesignPanel({ project, onApplied }: { project: Project; onApplied: () => void | Promise<void> }) {
  const [mode, setMode] = useState<RebarMode>('balanced');
  const [scheme, setScheme] = useState<RebarDesignScheme>();
  const [manifest, setManifest] = useState<DrawingSetManifest>();
  const [deepDetailing, setDeepDetailing] = useState<Record<string, any>>();
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [activeGroup, setActiveGroup] = useState<ActiveGroup>('issues');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('problems');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(undefined); setNotice(undefined);
    Promise.all([api.getRebarDesignScheme(project.id, mode), api.getDrawingSetManifest(project.id), api.getDeepDetailing(project.id, mode)])
      .then(([schemeData, manifestData, deepData]) => { if (alive) { setScheme(schemeData); setManifest(manifestData); setDeepDetailing(record(deepData.deepDetailing)); } })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, project.calculationResults.length, mode]);

  const diagnostics = scheme?.diagnostics;
  const summary = scheme?.summary ?? {};
  const q = query.trim().toLowerCase();
  const filterRows = (rows: Record<string, unknown>[] | undefined) => (rows ?? []).map(record).filter((row) => passesStatus(row, statusFilter) && (!q || searchable(row).includes(q)));
  const wallRows = useMemo(() => filterRows(scheme?.wallZones), [scheme, statusFilter, q]);
  const supportRows = useMemo(() => filterRows(scheme?.supportSchemes), [scheme, statusFilter, q]);
  const beamRows = useMemo(() => filterRows(scheme?.beamNodeSchemes), [scheme, statusFilter, q]);
  const issueRows = useMemo(() => filterRows(scheme?.checks), [scheme, statusFilter, q]);
  const issueMode: 'review' | 'construction' = diagnostics?.canIssueConstructionDrawings ? 'construction' : 'review';
  const primaryDownloadText = issueMode === 'construction' ? '下载施工图包' : '下载审查版图纸';
  const canApply = Boolean(diagnostics?.canApply ?? project.retainingSystem);

  async function applyScheme() {
    try {
      setApplying(true); setError(undefined); setNotice(undefined);
      const result = await api.applyRebarDesignScheme(project.id, mode, true);
      setScheme(result.scheme);
      setNotice(result.recalculated ? '截面优化已应用并完成重新计算，配筋方案已按新内力更新。' : '配筋方案已应用到当前构件。');
      await onApplied();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setApplying(false); }
  }

  const steps = [
    { label: '1 校验计算', status: diagnostics?.calculation.status ?? 'warning', note: diagnostics?.calculation.valid ? '施工阶段和支撑拓扑有效' : '需要先重新计算' },
    { label: '2 选择策略', status: 'pass', note: mode === 'conservative' ? '较低目标利用率' : mode === 'economic' ? '较高目标利用率' : '承载与施工性平衡' },
    { label: '3 处理问题', status: Number(summary.failCount ?? 0) ? 'fail' : Number(summary.warningCount ?? 0) ? 'warning' : 'pass', note: `${summary.failCount ?? 0} 个阻断，${summary.warningCount ?? 0} 个复核` },
    { label: '4 应用与出图', status: diagnostics?.canIssueConstructionDrawings ? 'pass' : 'warning', note: diagnostics?.canIssueConstructionDrawings ? '可输出施工图复核包' : '当前仅输出审查版' },
  ];

  return (
    <section className="rebarDesignPanel summaryPanel">
      <div className="panelTitleRow">
        <div><h3>配筋设计与施工图</h3><p className="small">按“计算有效性—传力体系—构件配筋—节点构造—图纸闸门”逐级检查，仅展示需要处理的信息。</p></div>
        <span className={`statusPill ${statusTone(scheme?.status)}`}><strong>{localizedStatus(scheme?.status ?? '未计算')}</strong></span>
      </div>

      <div className={`rebarHeadline ${statusTone(diagnostics?.canIssueConstructionDrawings ? 'pass' : diagnostics?.canApply ? 'warning' : 'fail')}`}>
        <strong>{diagnostics?.headline ?? '正在生成设计诊断…'}</strong>
        <span>{diagnostics?.supportTopology.message ?? '将自动检查支撑拓扑、配筋承载力和节点承压。'}</span>
      </div>

      <div className="rebarWorkflowSteps">
        {steps.map((step) => <div key={step.label} className={`workflowStep ${statusTone(step.status)}`}><strong>{step.label}</strong><span>{step.note}</span></div>)}
      </div>

      <div className="rebarPrimaryBar">
        <label>配筋策略<select value={mode} onChange={(event) => setMode(event.target.value as RebarMode)}><option value="conservative">保守</option><option value="balanced">均衡</option><option value="economic">经济</option></select></label>
        <button onClick={applyScheme} disabled={applying || loading || !canApply}>{applying ? '正在应用并复算…' : diagnostics?.sectionChangeCount ? `应用 ${diagnostics.sectionChangeCount} 项截面优化并复算` : '应用配筋方案'}</button>
        <a className={`buttonLink ${issueMode === 'review' ? 'secondary' : ''}`} href={api.cadPackageUrl(project.id, 'full', mode, issueMode)}>{primaryDownloadText}</a>
      </div>
      {!canApply ? <div className="rebarGateMessage fail">计算结果或支撑拓扑尚未通过，已禁用配筋应用。先执行重新计算或优化支撑体系。</div> : null}
      {issueMode === 'review' ? <div className="rebarGateMessage warn">图纸包将带“审查版”标识；消除阻断项后才能切换为施工图复核包。</div> : null}
      {notice ? <div className="rebarGateMessage pass">{notice}</div> : null}
      {error ? <div className="error">{error}</div> : null}
      {loading ? <p className="small">正在检查计算有效性、配筋组合、节点承压和图纸闸门…</p> : null}

      <div className="deepDetailingSummary" aria-label="深化设计摘要">
        <div className="panelTitleRow"><div><h4>深化设计闭环</h4><p className="small">节点钢构件、钢筋笼吊装、机械连接、预埋件碰撞和施工顺序。</p></div><span className={`statusPill ${statusTone(record(deepDetailing?.summary).status)}`}>{localizedStatus(record(deepDetailing?.summary).status ?? 'warning')}</span></div>
        <div className="maturityGrid rebarSummaryGrid">
          <div className={`statusCard ${statusTone(record(deepDetailing?.summary).hardFailureCount ? 'fail' : 'pass')}`}><span>深化阻断</span><strong>{String(record(deepDetailing?.summary).hardFailureCount ?? 0)}</strong><em>节点、吊装或预埋件碰撞</em></div>
          <div className="statusCard"><span>节点硬件</span><strong>{String(record(deepDetailing?.summary).bearingPlateCount ?? 0)}</strong><em>承压板/加劲板/焊缝/锚筋</em></div>
          <div className="statusCard"><span>吊装工况</span><strong>{String(record(deepDetailing?.summary).cageHoistingCaseCount ?? 0)}</strong><em>分节、吊点、索力与临时加强</em></div>
          <div className="statusCard"><span>机械连接</span><strong>{String(record(deepDetailing?.summary).couplerCount ?? 0)}</strong><em>套筒、丝头、错开组和抽检</em></div>
        </div>
      </div>

      <div className="maturityGrid rebarSummaryGrid">
        <div className={`statusCard ${statusTone(diagnostics?.calculation.status)}`}><span>计算有效性</span><strong>{localizedStatus(diagnostics?.calculation.status ?? 'warning')}</strong><em>{diagnostics?.calculation.messages?.[0] ?? '等待检查'}</em></div>
        <div className={`statusCard ${statusTone(diagnostics?.supportTopology.status)}`}><span>支撑传力体系</span><strong>{localizedStatus(diagnostics?.supportTopology.status ?? 'warning')}</strong><em>正交次对撑 {diagnostics?.supportTopology.secondaryGridSupportCount ?? 0} 根；角撑最大分担 {n(diagnostics?.supportTopology.maxCornerTributaryWidthM, 1)} m</em></div>
        <div className={`statusCard ${Number(summary.failCount ?? 0) ? 'fail' : 'pass'}`}><span>阻断项</span><strong>{String(summary.failCount ?? 0)}</strong><em>截面、配筋、节点或拓扑不足</em></div>
        <div className={`statusCard ${Number(summary.warningCount ?? 0) ? 'warn' : 'pass'}`}><span>复核项</span><strong>{String(summary.warningCount ?? 0)}</strong><em>锚固、裂缝、拥挤和施工偏差</em></div>
        <div className={`statusCard ${diagnostics?.canIssueConstructionDrawings ? 'pass' : 'review'}`}><span>出图状态</span><strong>{diagnostics?.canIssueConstructionDrawings ? '施工图复核' : '审查版'}</strong><em>{manifest?.sheetCount ?? 0} 张计划图纸</em></div>
      </div>

      {(diagnostics?.actions?.length ?? 0) > 0 ? <div className="rebarActionList"><strong>建议处理顺序</strong>{diagnostics?.actions.map((action) => <div key={action.id}><span className="actionPriority">P{action.priority}</span><b>{action.label}</b><span>{action.description}</span></div>)}</div> : null}

      <div className="rebarFilterBar">
        <label>显示<select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}><option value="problems">仅问题项</option><option value="fail">仅阻断项</option><option value="warning">仅复核项</option><option value="pass">仅通过项</option><option value="all">全部</option></select></label>
        <label>搜索<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="构件编号、原因或图号" /></label>
        <span className="small">当前 {activeGroup === 'issues' ? issueRows.length : activeGroup === 'walls' ? wallRows.length : activeGroup === 'supports' ? supportRows.length : activeGroup === 'beams' ? beamRows.length : manifest?.sheetCount ?? 0} 项</span>
      </div>

      <div className="tabBar compactTabs">
        <button className={activeGroup === 'issues' ? 'active' : ''} onClick={() => setActiveGroup('issues')}>问题中心</button>
        <button className={activeGroup === 'walls' ? 'active' : ''} onClick={() => setActiveGroup('walls')}>墙体分区</button>
        <button className={activeGroup === 'supports' ? 'active' : ''} onClick={() => setActiveGroup('supports')}>支撑配筋</button>
        <button className={activeGroup === 'beams' ? 'active' : ''} onClick={() => setActiveGroup('beams')}>围檩与节点</button>
        <button className={activeGroup === 'drawings' ? 'active' : ''} onClick={() => setActiveGroup('drawings')}>图纸目录</button>
      </div>

      {activeGroup === 'issues' ? <div className="issueReasonGrid">
        {Object.entries(diagnostics?.failureReasons ?? {}).length ? Object.entries(diagnostics?.failureReasons ?? {}).map(([code, item]) => <article key={code} className="issueReasonCard"><strong>{reasonText[code] ?? code}</strong><span>{item.count} 项：{item.objects?.join('、') || '-'}</span><p>{item.recommendedAction ?? '按构件详情处理后重新计算。'}</p></article>) : <div className="emptyState">没有阻断原因。可继续检查复核项和施工构造。</div>}
        {issueRows.length ? <table className="table compactTable"><thead><tr><th>对象</th><th>类别</th><th>状态</th><th>说明</th><th>建议操作</th></tr></thead><tbody>{issueRows.map((row, index) => <tr key={String(row.checkId ?? index)}><td>{String(row.hostCode ?? row.hostId ?? '-')}</td><td>{categoryText[String(row.category)] ?? String(row.category ?? '-')}</td><td><span className={`inlineStatus ${statusTone(row.status)}`}>{localizedStatus(row.status)}</span></td><td>{String(row.message ?? row.failureReasonCode ?? '-')}</td><td>{String(row.recommendedAction ?? '-')}</td></tr>)}</tbody></table> : null}
      </div> : null}

      {activeGroup === 'walls' ? <><WallZoneElevationPreview zones={wallRows} /><table className="table compactTable"><thead><tr><th>区段</th><th>墙段</th><th>类型</th><th>标高</th><th>坑内侧</th><th>坑外侧</th><th>布置</th><th>状态</th><th>图号</th></tr></thead><tbody>{wallRows.map((row) => { const faces = (row.faces ?? []) as Record<string, any>[]; const inner = record(faces.find((item) => item.face === 'inner')); const outer = record(faces.find((item) => item.face === 'outer')); return <tr key={String(row.zoneId)}><td>{String(row.zoneId)}</td><td>{String(row.hostCode)}</td><td>{String(row.zoneType)}</td><td>{n(row.topElevation)}～{n(row.bottomElevation)}</td><td>{String(inner.token ?? '-')}</td><td>{String(outer.token ?? '-')}</td><td>{String(inner.arrangementType ?? outer.arrangementType ?? '单层')}</td><td>{localizedStatus(row.status)}</td><td>{(row.drawingRefs ?? []).join(' / ')}</td></tr>; })}</tbody></table></> : null}
      {activeGroup === 'supports' ? <><SupportRebarPreview rows={supportRows} /><table className="table compactTable"><thead><tr><th>支撑</th><th>角色</th><th>轴力</th><th>现状/建议截面</th><th>纵筋</th><th>利用率</th><th>结论</th><th>建议</th></tr></thead><tbody>{supportRows.map((row) => { const section = record(row.section); const existing = record(row.existingSection); return <tr key={String(row.hostId)}><td>{String(row.hostCode)}</td><td>{String(row.supportRole ?? '-')}</td><td>{n(row.axialForceDesignKn)} kN</td><td>{existing.name ?? '-'}{row.sectionChanged ? ` → ${section.name ?? '-'}` : ''}</td><td>{String(record(row.longitudinal).token ?? '-')}</td><td>{n(row.utilization, 3)}</td><td>{localizedStatus(row.status)}</td><td>{String(row.recommendedAction ?? '-')}</td></tr>; })}</tbody></table></> : null}
      {activeGroup === 'beams' ? <table className="table compactTable"><thead><tr><th>对象</th><th>类型</th><th>主筋/U筋</th><th>箍筋/约束</th><th>承压利用率</th><th>状态</th><th>建议</th><th>图号</th></tr></thead><tbody>{beamRows.map((row) => <tr key={String(row.hostId)}><td>{String(row.hostCode)}</td><td>{String(row.hostType)}</td><td>{String(record(row.mainBars).token ?? record(row.additionalUBars).token ?? '-')}</td><td>{String(record(row.stirrups).token ?? `D${record(row.confinement).stirrupDiameterMm ?? '-'}@${record(row.confinement).spacingMm ?? '-'}`)}</td><td>{n(row.bearingUtilization, 3)}</td><td>{localizedStatus(row.status)}</td><td>{String(row.recommendedAction ?? row.nodeAdditional ?? '-')}</td><td>{(row.drawingRefs ?? []).join(' / ')}</td></tr>)}</tbody></table> : null}
      {activeGroup === 'drawings' ? <><table className="table compactTable"><thead><tr><th>图号</th><th>图名</th><th>类别</th><th>比例</th><th>文件</th></tr></thead><tbody>{manifest?.sheets.map((item) => <tr key={item.sheetNo}><td>{item.sheetNo}</td><td>{item.title}</td><td>{item.category}</td><td>{item.scale}</td><td>{item.file}</td></tr>)}</tbody></table><details className="secondaryDownloads"><summary>分专业下载</summary><div><a className="buttonLink secondary" href={api.cadPackageUrl(project.id, 'general', mode, issueMode)}>总图与剖面</a><a className="buttonLink secondary" href={api.cadPackageUrl(project.id, 'rebar', mode, issueMode)}>配筋图</a><a className="buttonLink secondary" href={api.cadPackageUrl(project.id, 'details', mode, issueMode)}>节点大样</a></div></details></> : null}
      <p className="small boundaryNote">{manifest?.issueBoundary}</p>
    </section>
  );
}
