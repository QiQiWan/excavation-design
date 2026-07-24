import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { DrawingSetManifest, Project, RebarDesignScheme } from '../types/domain';
import { SupportRebarPreview, WallZoneElevationPreview } from './RebarDrawingPreview';
import { waitForTaskWithHealth } from '../utils/taskPolling';

type RebarMode = 'conservative' | 'balanced' | 'economic';
type ActiveGroup = 'issues' | 'walls' | 'supports' | 'beams' | 'drawings';
type StatusFilter = 'problems' | 'all' | 'fail' | 'warning' | 'pass';
type ExpandGroup = 'gateBlockers' | 'gateWarnings' | 'failureReasons' | 'issues' | 'walls' | 'supports' | 'beams' | 'drawings';
const DEFAULT_VISIBLE_ROWS = 12;

const statusText: Record<string, string> = {
  pass: '通过', warning: '需复核', manual_review: '人工复核', preliminary: '初步方案', fail: '阻断',
};
const categoryText: Record<string, string> = {
  wall_reinforcement: '墙体配筋', support_reinforcement: '支撑配筋', beam_reinforcement: '围檩配筋',
  support_stirrup_reinforcement: '支撑箍筋', support_longitudinal_ratio: '支撑总纵筋率',
  support_single_side_longitudinal_ratio: '支撑单侧纵筋率', anchorage: '锚固长度', lap_splice: '搭接长度',
  mechanical_coupler: '机械连接', rebar_congestion: '钢筋拥挤与净距',
  node_congestion: '节点承压与拥挤', calculation: '计算有效性', other: '其他',
};
const zoneTypeText: Record<string, string> = { field_zone: '一般墙段', support_node_zone: '支撑节点区', corner_zone: '转角区', lap_zone: '搭接区', end_zones: '端部加密区', middle_zone: '跨中普通区' };
const supportRoleText: Record<string, string> = { primary_strut: '主对撑', secondary_strut: '次对撑', corner_diagonal: '角撑', ring_beam: '环梁', transferred_support: '换撑' };
const hostTypeText: Record<string, string> = { wale_or_crown_beam: '围檩/冠梁', support_node: '支撑节点', support_wale_node: '支撑—围檩节点', internal_support: '水平支撑', diaphragm_wall: '地下连续墙' };
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
function sleep(ms: number): Promise<void> { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
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
  const [expandedGroups, setExpandedGroups] = useState<Partial<Record<ExpandGroup, boolean>>>({});

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(undefined); setNotice(undefined);
    Promise.all([api.getRebarDesignScheme(project.id, mode), api.getDrawingSetManifest(project.id), api.getDeepDetailing(project.id, mode)])
      .then(([schemeData, manifestData, deepData]) => { if (alive) { setScheme(schemeData); setManifest(manifestData); setDeepDetailing(record(deepData.deepDetailing)); } })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, project.calculationResults.length, mode]);

  useEffect(() => { setExpandedGroups({}); }, [project.id, mode, activeGroup, statusFilter, query]);

  const diagnostics = scheme?.diagnostics;
  const deepeningGate = record(diagnostics?.deepeningGate);
  const gateBlockers = (Array.isArray(deepeningGate.blockers) ? deepeningGate.blockers : []).map(record);
  const gateWarnings = (Array.isArray(deepeningGate.warnings) ? deepeningGate.warnings : []).map(record);
  const failureReasonRows = Object.entries(diagnostics?.failureReasons ?? {});
  const summary = scheme?.summary ?? {};
  const q = query.trim().toLowerCase();
  const filterRows = (rows: Record<string, unknown>[] | undefined) => (rows ?? []).map(record).filter((row) => passesStatus(row, statusFilter) && (!q || searchable(row).includes(q)));
  const wallRows = useMemo(() => filterRows(scheme?.wallZones), [scheme, statusFilter, q]);
  const supportRows = useMemo(() => filterRows(scheme?.supportSchemes), [scheme, statusFilter, q]);
  // The drawing preview is a design record, not a problem list.  Using the
  // filtered table rows here made all passing stirrups/construction bars vanish
  // whenever the default filter was "problems".
  const allSupportRows = useMemo(() => (scheme?.supportSchemes ?? []).map(record).filter((row) => !q || searchable(row).includes(q)), [scheme, q]);
  const beamRows = useMemo(() => filterRows(scheme?.beamNodeSchemes), [scheme, statusFilter, q]);
  const issueRows = useMemo(() => filterRows(scheme?.checks), [scheme, statusFilter, q]);
  const visibleRows = <T,>(rows: T[], group: ExpandGroup): T[] => expandedGroups[group] ? rows : rows.slice(0, DEFAULT_VISIBLE_ROWS);
  const expandControl = (rows: unknown[], group: ExpandGroup) => rows.length > DEFAULT_VISIBLE_ROWS ? <div className="progressiveListFooter"><button type="button" className="secondary expandRowsButton" onClick={() => setExpandedGroups((current) => ({ ...current, [group]: !current[group] }))}>{expandedGroups[group] ? '收起' : `展开全部（${rows.length}）`}</button><span>默认显示前 {DEFAULT_VISIBLE_ROWS} 项</span></div> : null;
  const issueMode: 'review' | 'construction' = diagnostics?.canIssueConstructionDrawings ? 'construction' : 'review';
  const primaryDownloadText = issueMode === 'construction' ? '下载施工图包' : '下载审查版图纸';
  const canAutoClose = Boolean(project.designSettings.designBasisConfirmed && project.retainingSystem);
  const canApply = Boolean(diagnostics?.canApply || canAutoClose);
  const calculationAutoHealing = applying && gateBlockers.some((row) => String(row.reasonCode ?? '') === 'CALCULATION_NOT_CURRENT');

  async function applyScheme(recalculate = true) {
    try {
      setApplying(true); setError(undefined); setNotice(undefined);
      const runOnce = async () => {
        const created = await api.createTask(project.id, 'rebar_design', { mode, apply: true, recalculate });
        return waitForTaskWithHealth(created, () => undefined, { timeoutMs: 65 * 60 * 1000 });
      };
      let finished = await runOnce();
      const interruptionText = String(finished.error ?? finished.currentStep ?? '').toLowerCase();
      if (finished.status === 'interrupted' && (interruptionText.includes('worker') || interruptionText.includes('工作进程'))) {
        setNotice('检测到独立配筋工作进程异常退出，正在使用全新工作进程自动重试一次。');
        finished = await runOnce();
      }
      if (finished.status !== 'success') throw new Error(finished.error || `配筋任务未完成：${finished.status}`);
      setNotice(finished.result?.requiresRecalculation ? '配筋草案已生成，但二次复算后仍存在截面调整需求；当前保持审查版并列出控制构件。' : finished.result?.recalculatedAfterSectionChange ? '配筋引起的截面调整已自动复算，并按更新后的内力包络重新完成配筋。' : '配筋草案已应用到当前构件，并完成构造与出图资格检查。');
      await onApplied();
      // The worker persists the calculation, external stage chunks and final
      // reinforcement contract in separate bounded writes.  Poll the
      // authoritative scheme until the transient CALCULATION_NOT_CURRENT gate
      // disappears, instead of rendering the pre-save diagnosis indefinitely.
      let next = await api.getRebarDesignScheme(project.id, mode);
      for (let attempt = 0; attempt < 6; attempt += 1) {
        const blockers = (Array.isArray(record(next.diagnostics?.deepeningGate).blockers)
          ? record(next.diagnostics?.deepeningGate).blockers
          : []).map(record);
        const transientCalculationBlocker = blockers.some((row: Record<string, unknown>) => String(row.reasonCode ?? '') === 'CALCULATION_NOT_CURRENT');
        if (!transientCalculationBlocker) break;
        await sleep(350 * (attempt + 1));
        next = await api.getRebarDesignScheme(project.id, mode);
      }
      setScheme(next);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setApplying(false); }
  }

  const gateSteps = (Array.isArray(deepeningGate.steps) ? deepeningGate.steps : []).map(record);
  const structuralClosure = record(deepeningGate.structuralClosure);
  const steps = gateSteps.length ? gateSteps.map((step, index) => ({ label: `${index + 1} ${String(step.label)}`, status: String(step.status), note: String(step.message ?? '') })) : [
    { label: '1 校验计算', status: diagnostics?.calculation.status ?? 'warning', note: diagnostics?.calculation.valid ? '施工阶段和支撑拓扑有效' : '需要先重新计算' },
    { label: '2 选择策略', status: 'pass', note: mode === 'conservative' ? '较低目标利用率' : mode === 'economic' ? '较高目标利用率' : '承载与施工性平衡' },
    { label: '3 处理问题', status: Number(summary.failCount ?? 0) ? 'fail' : Number(summary.warningCount ?? 0) ? 'warning' : 'pass', note: `${summary.failCount ?? 0} 个阻断，${summary.warningCount ?? 0} 个复核` },
    { label: '4 应用与出图', status: diagnostics?.canIssueConstructionDrawings ? 'pass' : 'warning', note: diagnostics?.canIssueConstructionDrawings ? '可输出施工图复核包' : '当前仅输出审查版' },
  ];

  return (
    <section className="rebarDesignPanel summaryPanel">
      <div className="panelTitleRow">
        <div><h3>配筋设计与施工图</h3><p className="small">构件配筋、节点构造与出图资格。</p></div>
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
        <button onClick={() => applyScheme(true)} disabled={applying || loading || !canApply}>{applying ? '后台闭合配筋…' : '一键修复并闭合配筋'}</button>
        <button className="secondary" onClick={() => applyScheme(false)} disabled={applying || loading || !diagnostics?.canApply}>仅更新配筋</button>
        <a className={`buttonLink ${issueMode === 'review' ? 'secondary' : ''}`} href={api.cadPackageUrl(project.id, 'full', mode, issueMode)}>{primaryDownloadText}</a>
      </div>
      {calculationAutoHealing ? <div className="rebarGateMessage warn">正在自动恢复计算合同、补算当前方案并重新生成配筋。此处为处理中间状态，不代表最终阻断。</div> : null}
      {!calculationAutoHealing && !canApply ? <div className="rebarGateMessage fail">当前不能启动配筋闭合：{gateBlockers[0]?.message ?? diagnostics?.calculation.messages?.[0] ?? '请先确认设计基准并生成围护方案。'} {gateBlockers[0]?.requiredAction ?? ''}</div> : null}
      {!calculationAutoHealing && canAutoClose && !diagnostics?.canApply ? <div className="rebarGateMessage warn">当前计算证据缺失或已失效；主操作会自动补算当前方案，再执行最多三轮“补强—复算—重新配筋”。</div> : null}
      {issueMode === 'review' ? <div className="rebarGateMessage warn">图纸包将带“审查版”标识；消除阻断项后才能切换为施工图复核包。</div> : null}
      {notice ? <div className="rebarGateMessage pass">{notice}</div> : null}
      {error ? <div className="error">{error}</div> : null}
      {loading ? <p className="small">正在检查计算有效性、配筋组合、节点承压和图纸闸门…</p> : null}

      <div className="deepDetailingSummary" aria-label="深化设计摘要">
        <div className="panelTitleRow"><div><h4>深化设计闭环</h4><p className="small">节点钢构件、钢筋笼吊装、机械连接、预埋件碰撞和施工顺序。</p></div><span className={`statusPill ${statusTone(record(deepDetailing?.summary).status)}`}>{localizedStatus(record(deepDetailing?.summary).status ?? 'warning')}</span></div>
        <div className="maturityGrid rebarSummaryGrid">
          <div className={`statusCard ${statusTone(record(deepDetailing?.summary).hardFailureCount ? 'fail' : 'pass')}`}><span>空间深化阻断</span><strong>{String(record(deepDetailing?.summary).hardFailureCount ?? 0)}</strong><em>仅指节点、吊装或预埋件碰撞</em></div>
          <div className="statusCard"><span>节点硬件</span><strong>{String(record(deepDetailing?.summary).bearingPlateCount ?? 0)}</strong><em>承压板/加劲板/焊缝/锚筋</em></div>
          <div className="statusCard"><span>吊装工况</span><strong>{String(record(deepDetailing?.summary).cageHoistingCaseCount ?? 0)}</strong><em>分节、吊点、索力与临时加强</em></div>
          <div className="statusCard"><span>机械连接</span><strong>{String(record(deepDetailing?.summary).couplerCount ?? 0)}</strong><em>套筒、丝头、错开组和抽检</em></div>
        </div>
      </div>

      <div className="maturityGrid rebarSummaryGrid">
        <div className={`statusCard ${statusTone(diagnostics?.calculation.status)}`}><span>计算有效性</span><strong>{localizedStatus(diagnostics?.calculation.status ?? 'warning')}</strong><em>{diagnostics?.calculation.messages?.[0] ?? '等待检查'}</em></div>
        <div className={`statusCard ${statusTone(diagnostics?.supportTopology.status)}`}><span>支撑传力体系</span><strong>{localizedStatus(diagnostics?.supportTopology.status ?? 'warning')}</strong><em>正交次对撑 {diagnostics?.supportTopology.secondaryGridSupportCount ?? 0} 根；角撑最大分担 {n(diagnostics?.supportTopology.maxCornerTributaryWidthM, 1)} m</em></div>
        <div className={`statusCard ${Number(deepeningGate.blockerCount ?? summary.failCount ?? 0) ? 'fail' : 'pass'}`}><span>深化入口总阻断</span><strong>{String(deepeningGate.blockerCount ?? summary.failCount ?? 0)}</strong><em>计算、方案、构件与配筋的统一口径</em></div>
        <div className={`statusCard ${Number(summary.warningCount ?? 0) ? 'warn' : 'pass'}`}><span>复核项</span><strong>{String(summary.warningCount ?? 0)}</strong><em>锚固、裂缝、拥挤和施工偏差</em></div>
        <div className={`statusCard ${diagnostics?.canIssueConstructionDrawings ? 'pass' : 'review'}`}><span>出图状态</span><strong>{diagnostics?.canIssueConstructionDrawings ? '施工图复核' : '审查版'}</strong><em>{manifest?.sheetCount ?? 0} 张计划图纸</em></div>
      </div>

      {(gateBlockers.length || gateWarnings.length) ? <section className="deepeningEntryDiagnostics" aria-label="配筋深化入口诊断">
        <div className="panelTitleRow"><div><h4>配筋深化入口诊断</h4><p className="small">逐项说明哪里缺失、影响哪些对象、由哪个阶段补齐。</p></div><span className={`statusPill ${gateBlockers.length ? 'fail' : 'warn'}`}>{gateBlockers.length ? `${String(deepeningGate.blockerCount ?? gateBlockers.length)} 个阻断` : `${String(deepeningGate.warningCount ?? gateWarnings.length)} 个复核`}</span></div>
        {gateBlockers.length ? <><div className="deepeningIssueGrid">{visibleRows(gateBlockers, 'gateBlockers').map((issue) => <article key={String(issue.id ?? issue.reasonCode)} className="fail"><header><strong>{String(issue.title ?? issue.reasonCode)}</strong><span>{String(issue.count ?? 1)} 项</span></header><p>{String(issue.message ?? '')}</p>{Array.isArray(issue.objects) && issue.objects.length ? <small>影响对象：{issue.objects.slice(0, expandedGroups.gateBlockers ? issue.objects.length : 8).map(String).join('、')}{!expandedGroups.gateBlockers && issue.objects.length > 8 ? ` 等 ${issue.objects.length} 项` : ''}</small> : null}<div><b>如何补齐</b><span>{String(issue.requiredAction ?? '调整设计并重新运行校核。')}</span></div><footer><span>目标阶段：{String(issue.targetStage ?? '配筋深化')}</span><em>{issue.canResolveAtDesignStage === false ? '需施工/专项资料' : '设计阶段可处理'}</em></footer></article>)}</div>{expandControl(gateBlockers, 'gateBlockers')}</> : <div className="rebarGateMessage pass">配筋深化入口无硬阻断，可继续运行 P3；正式出图仍需关闭复核项。</div>}
        {gateWarnings.length ? <details className="deepeningWarningRegister"><summary>查看 {String(deepeningGate.warningCount ?? gateWarnings.length)} 个复核项及处理方法</summary><div>{visibleRows(gateWarnings, 'gateWarnings').map((issue) => <article key={String(issue.id ?? issue.reasonCode)}><strong>{String(issue.title ?? issue.reasonCode)} · {String(issue.count ?? 1)} 项</strong><span>{String(issue.message ?? '')}</span><em>{String(issue.requiredAction ?? '')}</em></article>)}</div>{expandControl(gateWarnings, 'gateWarnings')}</details> : null}
      </section> : null}

      {structuralClosure.status ? <div className={`rebarGateMessage ${structuralClosure.closed ? 'pass' : 'warn'}`}>
        <strong>{structuralClosure.closed ? '结构计算与配筋合同已闭合' : '结构闭合尚未完成'}</strong>
        <span>{String(structuralClosure.message ?? '')}</span>
        {structuralClosure.closed && gateWarnings.length ? <em>剩余 {gateWarnings.length} 组项目属于锚固、接头、碰撞或审签复核，可进入 P3 处理。</em> : null}
      </div> : null}

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
        {failureReasonRows.length ? visibleRows(failureReasonRows, 'failureReasons').map(([code, item]) => <article key={code} className="issueReasonCard"><strong>{reasonText[code] ?? code}</strong><span>{item.count} 项：{item.objects?.slice(0, expandedGroups.failureReasons ? item.objects?.length : 8).join('、') || '-'}{!expandedGroups.failureReasons && (item.objects?.length ?? 0) > 8 ? ` 等 ${item.objects?.length} 项` : ''}</span><p>{item.recommendedAction ?? '按构件详情处理后重新计算。'}</p></article>) : <div className="emptyState">没有阻断原因。可继续检查复核项和施工构造。</div>}
        {expandControl(failureReasonRows, 'failureReasons')}
        {issueRows.length ? <table className="table compactTable"><thead><tr><th>对象</th><th>类别</th><th>状态</th><th>说明</th><th>建议操作</th></tr></thead><tbody>{visibleRows(issueRows, 'issues').map((row, index) => <tr key={String(row.checkId ?? index)}><td>{String(row.hostCode ?? row.hostId ?? '-')}</td><td>{categoryText[String(row.category)] ?? String(row.category ?? '-')}</td><td><span className={`inlineStatus ${statusTone(row.status)}`}>{localizedStatus(row.status)}</span></td><td>{String(row.message ?? row.failureReasonCode ?? '-')}</td><td>{String(row.recommendedAction ?? '-')}</td></tr>)}</tbody></table> : null}{expandControl(issueRows, 'issues')}
      </div> : null}

      {activeGroup === 'walls' ? <><WallZoneElevationPreview zones={visibleRows(wallRows, 'walls')} /><table className="table compactTable"><thead><tr><th>区段</th><th>墙段</th><th>类型</th><th>标高</th><th>坑内侧</th><th>坑外侧</th><th>布置</th><th>状态</th><th>图号</th></tr></thead><tbody>{visibleRows(wallRows, 'walls').map((row) => { const faces = (row.faces ?? []) as Record<string, any>[]; const inner = record(faces.find((item) => item.face === 'inner')); const outer = record(faces.find((item) => item.face === 'outer')); return <tr key={String(row.zoneId)}><td>{String(row.zoneId)}</td><td>{String(row.hostCode)}</td><td>{zoneTypeText[String(row.zoneType)] ?? '墙体设计区'}</td><td>{n(row.topElevation)}～{n(row.bottomElevation)}</td><td>{String(inner.token ?? '-')}</td><td>{String(outer.token ?? '-')}</td><td>{String(inner.arrangementType ?? outer.arrangementType ?? '单层')}</td><td>{localizedStatus(row.status)}</td><td>{(row.drawingRefs ?? []).join(' / ')}</td></tr>; })}</tbody></table>{expandControl(wallRows, 'walls')}</> : null}
      {activeGroup === 'supports' ? <><SupportRebarPreview rows={allSupportRows} />{supportRows.length ? <table className="table compactTable supportRebarTable"><thead><tr><th>支撑</th><th>角色 / 轴力</th><th>现状 / 建议截面</th><th>纵向主筋</th><th>端部 / 跨中箍筋</th><th>侧面构造筋</th><th>拉结 / 附加筋</th><th>五类钢筋合同</th><th>结论与建议</th></tr></thead><tbody>{visibleRows(supportRows, 'supports').map((row) => { const section = record(row.section); const existing = record(row.existingSection); const contract = record(row.rebarContract); return <tr key={String(row.hostId)}><td><strong>{String(row.hostCode)}</strong></td><td>{supportRoleText[String(row.supportRole)] ?? '水平支撑'}<small>{n(row.axialForceDesignKn)} kN</small></td><td>{existing.name ?? '-'}{row.sectionChanged ? ` → ${section.name ?? '-'}` : ''}</td><td>{String(record(row.longitudinal).token ?? '-')}</td><td>{String(record(row.endZones).token ?? '-')} / {String(record(row.middleZone).token ?? '-')}</td><td>{String(record(row.distributionBars).token ?? '-')}</td><td>{String(record(row.tieBars).token ?? '-')} / {String(record(row.lapAdditionalBars).token ?? '-')}</td><td><span className={`inlineStatus ${contract.status === 'complete' ? 'pass' : 'fail'}`}>{contract.status === 'complete' ? '完整' : `缺 ${Array.isArray(contract.missingBarTypes) ? contract.missingBarTypes.join('、') : '数据'}`}</span></td><td>{localizedStatus(row.status)}<small>{String(row.recommendedAction ?? '-')}</small></td></tr>; })}</tbody></table> : <div className="emptyState">当前筛选条件下没有问题支撑；上方仍展示全部支撑的纵筋、箍筋、构造筋、拉结筋和附加筋。切换“全部”可查看完整明细。</div>}{expandControl(supportRows, 'supports')}</> : null}
      {activeGroup === 'beams' ? <><table className="table compactTable"><thead><tr><th>对象</th><th>类型</th><th>主筋/U筋</th><th>箍筋/约束</th><th>承压利用率</th><th>状态</th><th>建议</th><th>图号</th></tr></thead><tbody>{visibleRows(beamRows, 'beams').map((row) => <tr key={String(row.hostId)}><td>{String(row.hostCode)}</td><td>{hostTypeText[String(row.hostType)] ?? '梁/节点构件'}</td><td>{String(record(row.mainBars).token ?? record(row.additionalUBars).token ?? '-')}</td><td>{String(record(row.stirrups).token ?? `D${record(row.confinement).stirrupDiameterMm ?? '-'}@${record(row.confinement).spacingMm ?? '-'}`)}</td><td>{n(row.bearingUtilization, 3)}</td><td>{localizedStatus(row.status)}</td><td>{String(row.recommendedAction ?? row.nodeAdditional ?? '-')}</td><td>{(row.drawingRefs ?? []).join(' / ')}</td></tr>)}</tbody></table>{expandControl(beamRows, 'beams')}</> : null}
      {activeGroup === 'drawings' ? <><table className="table compactTable"><thead><tr><th>图号</th><th>图名</th><th>类别</th><th>比例</th><th>文件</th></tr></thead><tbody>{visibleRows(manifest?.sheets ?? [], 'drawings').map((item) => <tr key={item.sheetNo}><td>{item.sheetNo}</td><td>{item.title}</td><td>{item.category}</td><td>{item.scale}</td><td>{item.file}</td></tr>)}</tbody></table>{expandControl(manifest?.sheets ?? [], 'drawings')}<details className="secondaryDownloads"><summary>分专业下载</summary><div><a className="buttonLink secondary" href={api.cadPackageUrl(project.id, 'general', mode, issueMode)}>总图与剖面</a><a className="buttonLink secondary" href={api.cadPackageUrl(project.id, 'rebar', mode, issueMode)}>配筋图</a><a className="buttonLink secondary" href={api.cadPackageUrl(project.id, 'details', mode, issueMode)}>节点大样</a></div></details></> : null}
      <p className="small boundaryNote">{manifest?.issueBoundary}</p>
    </section>
  );
}
