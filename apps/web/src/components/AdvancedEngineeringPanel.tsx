import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { AdvancedEngineeringSuite, MonitoringRecord, Project } from '../types/domain';

type PanelGroup = 'design' | 'coordination' | 'monitoring' | 'delivery';

const statusLabel: Record<string, string> = { pass: '通过', warning: '复核', fail: '阻断', manual_review: '人工复核', approved: '已批准', reviewed: '已审核', checked: '已校核', submitted: '已提交', draft: '草稿', rejected: '已退回', stale: '批准已失效' };
function tone(status?: string) { return status === 'pass' || status === 'approved' ? 'pass' : status === 'fail' || status === 'rejected' || status === 'stale' ? 'fail' : 'warn'; }
function num(value: unknown, digits = 2) { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits) : '-'; }

export default function AdvancedEngineeringPanel({ project, onChanged }: { project: Project; onChanged: () => void | Promise<void> }) {
  const [suite, setSuite] = useState<AdvancedEngineeringSuite>();
  const [group, setGroup] = useState<PanelGroup>('design');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const draftKey = `pitguard-monitor-draft-${project.id}`;
  const [monitorDraft, setMonitorDraft] = useState<MonitoringRecord>(() => {
    try { return JSON.parse(localStorage.getItem(draftKey) || '') as MonitoringRecord; } catch { return { recordType: 'wall_displacement', measuredValue: 0, unit: 'mm', quality: 'verified', source: 'manual' }; }
  });
  const [actor, setActor] = useState(() => localStorage.getItem('pitguard-review-actor') || '');
  const [comment, setComment] = useState('');
  const [revisionDescription, setRevisionDescription] = useState('本轮工程闭环复核更新');
  const [monitorFile, setMonitorFile] = useState<File>();

  useEffect(() => { localStorage.setItem(draftKey, JSON.stringify(monitorDraft)); }, [draftKey, monitorDraft]);
  useEffect(() => { localStorage.setItem('pitguard-review-actor', actor); }, [actor]);
  useEffect(() => { void refresh(); }, [project.id, project.updatedAt]);

  async function refresh() {
    setLoading(true); setError(undefined);
    try { setSuite(await api.getAdvancedSuite(project.id)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setLoading(false); }
  }

  async function execute(label: string, action: () => Promise<unknown>, refreshProject = false) {
    setBusy(label); setError(undefined); setNotice(undefined);
    try {
      await action();
      if (refreshProject) await onChanged();
      await refresh();
      setNotice(`${label}已完成。`);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(undefined); }
  }

  async function saveMonitoring() {
    const value = Number(monitorDraft.measuredValue);
    if (!Number.isFinite(value)) { setError('监测值必须为有效数字。'); return; }
    await execute('保存监测记录', () => api.addMonitoringRecords(project.id, [{ ...monitorDraft, measuredValue: value, source: monitorDraft.source || 'manual', quality: monitorDraft.quality || 'verified' }]), true);
  }

  async function importMonitoring() {
    if (!monitorFile) { setError('请选择 UTF-8 编码的监测 CSV 文件。'); return; }
    await execute('批量导入监测数据', () => api.importMonitoringCsv(project.id, monitorFile), true);
    setMonitorFile(undefined);
  }

  const reviewAction = useMemo(() => {
    const status = suite?.review.status;
    if (status === 'draft' || status === 'rejected' || status === 'stale') return { role: 'designer', action: 'submit', label: '提交校核' };
    if (status === 'submitted') return { role: 'checker', action: 'accept', label: '校核通过' };
    if (status === 'checked') return { role: 'reviewer', action: 'accept', label: '审核通过' };
    if (status === 'reviewed') return { role: 'approver', action: 'approve', label: '批准发行' };
    return { role: 'approver', action: 'reopen', label: '重新打开设计' };
  }, [suite?.review.status]);

  async function advanceReview() {
    if (!actor.trim()) { setError('请输入签审人员姓名。'); return; }
    await execute(reviewAction.label, () => api.transitionReview(project.id, { role: reviewAction.role, actor: actor.trim(), action: reviewAction.action, comment: comment.trim() || undefined }), true);
    setComment('');
  }

  const moduleCards = suite ? [
    ['长期与裂缝', suite.serviceability.status, `最大裂缝 ${num(suite.serviceability.summary.maxEstimatedCrackWidthMm, 3)} mm`],
    ['复杂拓扑', suite.topology.status, `${suite.topology.summary.levelCount ?? 0} 层 / ${suite.topology.summary.concaveVertexCount ?? 0} 凹角`],
    ['碰撞与净距', suite.collisions.status, `${suite.collisions.summary.hardCollisionCount ?? 0} 阻断 / ${suite.collisions.summary.warningCount ?? 0} 复核`],
    ['节点局部', suite.nodeLocal.status, `最大利用率 ${num(suite.nodeLocal.summary.maxUtilization, 3)}`],
    ['监测反演', suite.monitoring.recordCount ? 'warning' : 'manual_review', `${suite.monitoring.recordCount} 条记录`],
    ['四级审签', suite.review.approvalValid ? 'approved' : suite.review.status, statusLabel[suite.review.status] ?? suite.review.status],
    ['正式图纸', suite.status === 'fail' ? 'fail' : 'warning', 'CAD + PDF + 修订台账'],
    ['交互辅助', 'pass', '精简模式 / 快捷键 / 草稿恢复'],
  ] : [];

  const engineeringApproved = Boolean(suite && suite.status !== 'fail' && suite.review.approvalValid);
  const constructionRevisionValid = Boolean(suite?.formalDrawings?.constructionRevisionValid);
  const canConstruction = engineeringApproved && constructionRevisionValid;

  return (
    <section className="advancedEngineeringPanel" aria-labelledby="advanced-engineering-title">
      <div className="panelTitleRow">
        <div><h3 id="advanced-engineering-title">工程深化与发行闭环</h3><p className="small">将长期效应、复杂拓扑、碰撞、节点复核、监测反演、审签和正式图纸集中到一个操作面板。</p></div>
        <button className="secondary" onClick={() => void refresh()} disabled={loading || Boolean(busy)} aria-label="刷新工程深化分析">{loading ? '分析中…' : '刷新分析'}</button>
      </div>
      {busy ? <div className="info" role="status" aria-live="polite">{busy}…</div> : null}
      {notice ? <div className="rebarGateMessage pass" role="status">{notice}</div> : null}
      {error ? <div className="error" role="alert">{error}</div> : null}
      {!suite ? <p className="small">正在构建八项工程深化分析…</p> : <>
        <div className="advancedModuleGrid">
          {moduleCards.map(([label, status, detail]) => <article key={label} className={`statusCard ${tone(status)}`}><span>{label}</span><strong>{statusLabel[status] ?? status}</strong><em>{detail}</em></article>)}
        </div>
        <div className="tabBar compactTabs" role="tablist" aria-label="工程深化分组">
          {([['design','设计深化'],['coordination','模型协调'],['monitoring','监测与审签'],['delivery','图纸发行']] as [PanelGroup,string][]).map(([key,label]) => <button key={key} role="tab" aria-selected={group === key} className={group === key ? 'active' : ''} onClick={() => setGroup(key)}>{label}</button>)}
        </div>

        {group === 'design' ? <div className="advancedGroupGrid">
          <article className="summaryPanel">
            <h4>长期效应与裂缝控制</h4>
            <div className="metricLine"><span>裂缝宽度</span><strong>{num(suite.serviceability.summary.maxEstimatedCrackWidthMm, 3)} / {num(suite.serviceability.summary.crackWidthLimitMm, 2)} mm</strong></div>
            <div className="metricLine"><span>长期位移</span><strong>{num(suite.serviceability.summary.longTermDisplacementMm)} / {num(suite.serviceability.summary.displacementLimitMm)} mm</strong></div>
            <div className="metricLine"><span>参数</span><strong>φ={num(suite.serviceability.summary.creepCoefficient, 2)} · 持续比={num(suite.serviceability.summary.sustainedLoadRatio, 2)}</strong></div>
            <details><summary>查看控制分区</summary><table className="table compactTable"><thead><tr><th>墙段/分区</th><th>面</th><th>裂缝</th><th>状态</th><th>操作</th></tr></thead><tbody>{suite.serviceability.wallZoneChecks.filter(x => x.status !== 'pass').slice(0, 15).map((x, i) => <tr key={String(x.objectId ?? i)}><td>{String(x.hostCode ?? x.objectId)}</td><td>{String(x.face)}</td><td>{num(x.estimatedCrackWidthMm, 3)} mm</td><td>{statusLabel[String(x.status)] ?? String(x.status)}</td><td>{String(x.recommendedAction)}</td></tr>)}</tbody></table></details>
            <p className="small boundaryNote">{suite.serviceability.boundary}</p>
          </article>
          <article className="summaryPanel">
            <h4>复杂平面支撑拓扑</h4>
            <div className="metricLine"><span>层数</span><strong>{String(suite.topology.summary.levelCount ?? 0)}</strong></div>
            <div className="metricLine"><span>凹角</span><strong>{String(suite.topology.summary.concaveVertexCount ?? 0)}</strong></div>
            <div className="metricLine"><span>安全候选加撑</span><strong>{String(suite.topology.safeAdditions?.length ?? 0)}</strong></div>
            <table className="table compactTable"><thead><tr><th>层</th><th>连通分量</th><th>双向传力</th><th>冗余度</th><th>状态</th></tr></thead><tbody>{suite.topology.levels.map((x, i) => <tr key={String(x.levelIndex ?? i)}><td>{String(x.levelIndex)}</td><td>{String(x.connectedComponents)}</td><td>{x.directionalCoverage ? '有' : '缺失'}</td><td>{String(x.graphRedundancy)}</td><td>{statusLabel[String(x.status)] ?? String(x.status)}</td></tr>)}</tbody></table>
            {(suite.topology.safeAdditions?.length ?? 0) > 0 ? <button onClick={() => void execute('应用凹角拓扑增强', () => api.applyAdvancedTopology(project.id), true)} disabled={Boolean(busy)}>应用安全候选并清空旧计算</button> : <p className="small">当前未生成可自动应用的凹角加撑候选。</p>}
          </article>
        </div> : null}

        {group === 'coordination' ? <div className="advancedGroupGrid">
          <article className="summaryPanel"><h4>碰撞、净距与钢筋拥挤</h4><div className="metricLine"><span>硬冲突</span><strong>{String(suite.collisions.summary.hardCollisionCount ?? 0)}</strong></div><div className="metricLine"><span>复核项</span><strong>{String(suite.collisions.summary.warningCount ?? 0)}</strong></div><table className="table compactTable"><thead><tr><th>对象 A</th><th>对象 B</th><th>类型</th><th>状态</th><th>建议</th></tr></thead><tbody>{suite.collisions.collisions.slice(0, 20).map((x, i) => <tr key={String(x.id ?? i)}><td>{String(x.objectA ?? '-')}</td><td>{String(x.objectB ?? '-')}</td><td>{String(x.type)}</td><td>{statusLabel[String(x.status)] ?? String(x.status)}</td><td>{String(x.recommendedAction)}</td></tr>)}</tbody></table></article>
          <article className="summaryPanel"><h4>节点局部复核</h4><div className="metricLine"><span>最大利用率</span><strong>{num(suite.nodeLocal.summary.maxUtilization, 3)}</strong></div><div className="metricLine"><span>最大节点滑移</span><strong>{num(suite.nodeLocal.summary.maxLocalSlipMm, 3)} mm</strong></div><table className="table compactTable"><thead><tr><th>节点</th><th>支撑</th><th>承压</th><th>劈裂</th><th>滑移</th><th>状态</th></tr></thead><tbody>{suite.nodeLocal.nodes.filter(x => x.status !== 'pass').slice(0, 20).map((x, i) => <tr key={String(x.nodeId ?? i)}><td>{String(x.nodeCode)}</td><td>{String(x.supportCode)}</td><td>{num(x.bearingUtilization, 3)}</td><td>{num(x.splittingUtilization, 3)}</td><td>{num(x.localSlipMm, 3)} mm</td><td>{statusLabel[String(x.status)] ?? String(x.status)}</td></tr>)}</tbody></table><p className="small boundaryNote">{suite.nodeLocal.boundary}</p></article>
        </div> : null}

        {group === 'monitoring' ? <div className="advancedGroupGrid">
          <article className="summaryPanel"><h4>监测数据与参数反演</h4><p className="small">表单草稿自动保存在本机。应用反演参数会清空旧计算结果并要求重新计算。</p><div className="formGrid compactFormGrid"><label>类型<select value={monitorDraft.recordType} onChange={e => setMonitorDraft({ ...monitorDraft, recordType: e.target.value as MonitoringRecord['recordType'], unit: e.target.value === 'support_axial_force' ? 'kN' : e.target.value === 'groundwater' ? 'm' : 'mm' })}><option value="wall_displacement">墙体位移</option><option value="support_axial_force">支撑轴力</option><option value="groundwater">地下水位</option><option value="settlement">沉降</option></select></label><label>对象编号<input value={monitorDraft.objectCode ?? ''} onChange={e => setMonitorDraft({ ...monitorDraft, objectCode: e.target.value })} placeholder="可选，如 GS-L3-2" /></label><label>监测值<input type="number" value={monitorDraft.measuredValue} onChange={e => setMonitorDraft({ ...monitorDraft, measuredValue: Number(e.target.value) })} /></label><label>单位<input value={monitorDraft.unit} onChange={e => setMonitorDraft({ ...monitorDraft, unit: e.target.value })} /></label><label>标高<input type="number" value={monitorDraft.elevation ?? ''} onChange={e => setMonitorDraft({ ...monitorDraft, elevation: e.target.value === '' ? undefined : Number(e.target.value) })} /></label><label>备注<input value={monitorDraft.note ?? ''} onChange={e => setMonitorDraft({ ...monitorDraft, note: e.target.value })} /></label></div><div className="monitorImportRow"><label>批量 CSV<input type="file" accept=".csv,text/csv" onChange={e => setMonitorFile(e.target.files?.[0])} /></label><button className="secondary" onClick={() => void importMonitoring()} disabled={Boolean(busy) || !monitorFile}>导入 CSV</button><span className="small">支持中英文字段；单文件不超过 5 MB，错误行会单独返回。<a href={api.monitoringTemplateUrl(project.id)}>下载模板</a></span></div><div className="buttonRow"><button onClick={() => void saveMonitoring()} disabled={Boolean(busy)}>保存记录</button><button className="secondary" onClick={() => void execute('预览监测反演', () => api.calibrateMonitoring(project.id, false), true)} disabled={Boolean(busy) || suite.monitoring.recordCount < 1}>预览反演</button><button className="secondary" onClick={() => void execute('应用监测反演参数', () => api.calibrateMonitoring(project.id, true), true)} disabled={Boolean(busy) || suite.monitoring.recordCount < 5}>应用并要求复算</button></div>{suite.monitoring.latestCalibration ? <div className="calibrationSummary"><strong>最近反演：{statusLabel[String(suite.monitoring.latestCalibration.status)] ?? String(suite.monitoring.latestCalibration.status)}</strong><span>土体 {num(suite.monitoring.latestCalibration.soilModulusFactor, 3)} · 墙体 {num(suite.monitoring.latestCalibration.wallStiffnessFactor, 3)} · 支撑 {num(suite.monitoring.latestCalibration.supportStiffnessFactor, 3)} · 置信度 {String(suite.monitoring.latestCalibration.confidence)}</span></div> : null}</article>
          <article className="summaryPanel"><h4>设计—校核—审核—批准</h4><div className={`reviewState ${tone(suite.review.status)}`}><strong>{statusLabel[suite.review.status] ?? suite.review.status}</strong><span>当前角色：{suite.review.currentRole}</span><span>岗位分离：{suite.review.separationOfDutiesValid === false ? '不满足' : '满足'}</span><em>快照：{suite.review.currentSnapshotHash}</em></div>{suite.review.roleActors ? <div className="reviewActorGrid">{Object.entries(suite.review.roleActors).map(([role, name]) => <span key={role}>{role}：<strong>{name}</strong></span>)}</div> : null}<label>签审人员<input value={actor} onChange={e => setActor(e.target.value)} placeholder="请输入姓名" /></label><label>意见<textarea value={comment} onChange={e => setComment(e.target.value)} rows={3} placeholder="可选；退回时建议填写原因" /></label><div className="buttonRow"><button onClick={() => void advanceReview()} disabled={Boolean(busy)}>{reviewAction.label}</button>{!['draft','rejected'].includes(suite.review.status) && suite.review.status !== 'approved' ? <button className="secondary" onClick={() => void execute('退回设计', () => api.transitionReview(project.id, { role: suite.review.currentRole, actor: actor || suite.review.currentRole, action: 'reject', comment: comment || '退回修改' }), true)}>退回修改</button> : null}</div><ol className="reviewTimeline">{suite.review.actions.slice().reverse().slice(0, 8).map((x, i) => <li key={String(x.id ?? i)}><strong>{String(x.actor)}</strong><span>{String(x.role)} · {String(x.action)}</span><em>{String(x.comment ?? '')}</em></li>)}</ol></article>
        </div> : null}

        {group === 'delivery' ? <div className="advancedGroupGrid">
          <article className="summaryPanel"><h4>正式图纸发行包</h4><p>成套成果包含 CAD 总图/分层图/配筋图/节点大样、批量 PDF 发行索引、工程检查表、修订台账和 DWG 转换说明。</p><div className="buttonRow"><a className="buttonLink secondary" href={api.formalDrawingPackageUrl(project.id, 'review')}>下载审查版正式包</a><a className={`buttonLink ${canConstruction ? '' : 'disabledLink'}`} aria-disabled={!canConstruction} href={canConstruction ? api.formalDrawingPackageUrl(project.id, 'construction') : undefined}>下载施工图复核包</a></div>{!canConstruction ? <div className="rebarGateMessage warn">施工图复核包要求无工程阻断、完成四级批准，并创建绑定当前设计快照的施工版修订记录。当前可下载审查版。</div> : null}</article>
          <article className="summaryPanel"><h4>图纸修订记录</h4><label>修订说明<input value={revisionDescription} onChange={e => setRevisionDescription(e.target.value)} /></label><div className="buttonRow"><button onClick={() => void execute('创建图纸修订记录', () => api.addDrawingRevision(project.id, { description: revisionDescription, author: actor || 'AI-DRAFT', issueStatus: engineeringApproved ? 'construction' : 'review' }), true)} disabled={Boolean(busy) || !revisionDescription.trim()}>{engineeringApproved ? '创建施工版修订' : '创建审查版修订'}</button></div><table className="table compactTable"><thead><tr><th>版本</th><th>说明</th><th>编制</th><th>状态</th><th>快照</th></tr></thead><tbody>{(project.drawingRevisions ?? []).slice().reverse().map(x => <tr key={x.id}><td>{x.revision}</td><td>{x.description}</td><td>{x.author}</td><td>{x.issueStatus}</td><td>{x.snapshotHash}</td></tr>)}</tbody></table></article>
        </div> : null}
      </>}
    </section>
  );
}
