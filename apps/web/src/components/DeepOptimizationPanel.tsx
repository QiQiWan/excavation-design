import { useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../types/domain';
import { formatEngineeringValue, withUnitLabel } from '../utils/units';

function statusText(status?: string) {
  if (status === 'pass') return '通过';
  if (status === 'warning') return '需协调';
  if (status === 'fail') return '阻断';
  return status ?? '未分析';
}

export default function DeepOptimizationPanel({ project, onChanged }: { project: Project; onChanged: () => void | Promise<void> }) {
  const [coordination, setCoordination] = useState<Record<string, any>>();
  const [submodels, setSubmodels] = useState<Record<string, any>>();
  const [logistics, setLogistics] = useState<Record<string, any>>();
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();

  async function execute(label: string, action: () => Promise<Record<string, any>>, setter: (value: Record<string, any>) => void) {
    setBusy(label); setError(undefined);
    try { setter(await action()); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(undefined); }
  }

  async function apply(issueId: string, candidateId: string) {
    setBusy('应用构造协调方案'); setError(undefined);
    try {
      setCoordination(await api.applyCoordinationCandidate(project.id, issueId, candidateId));
      await onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(undefined); }
  }

  return <section className="deepOptimizationPanel fullWidth">
    <div className="focusSectionHeader">
      <div><span className="sectionKicker">V3.9 智能深化</span><h3>构造协调、节点子模型与吊装物流</h3><p>三项分析按需运行，避免每次进入页面都加载大规模逐根钢筋和吊装数据。</p></div>
      {busy ? <span className="schemeState pending">{busy}…</span> : null}
    </div>
    {error ? <div className="error">{error}</div> : null}
    <div className="deepOptimizationGrid">
      <article className="summaryPanel">
        <div className="panelTitleRow"><div><h4>构造协调候选</h4><p className="small">绕筋、预埋件移位、开孔和局部加筋四类候选。</p></div><button className="secondary" onClick={() => void execute('分析构造协调候选', () => api.getCoordinationOptimization(project.id), setCoordination)}>分析</button></div>
        {coordination ? <>
          <div className="metricLine"><span>问题组</span><strong>{String(coordination.summary?.issueGroupCount ?? 0)}</strong></div>
          <div className="metricLine"><span>应用后阻断 / 复核</span><strong>{String(coordination.summary?.hardFailureAfter ?? 0)} / {String(coordination.summary?.warningAfter ?? 0)}</strong></div>
          <details><summary>查看优先问题与候选</summary>{(coordination.issues ?? []).slice(0, 8).map((issue: Record<string, any>) => {
            const recommended = (issue.candidates ?? []).find((c: Record<string, any>) => c.candidateId === issue.recommendedCandidateId) ?? issue.candidates?.[0];
            return <div className="coordinationIssue" key={issue.issueId}><strong>{issue.hostCode} · {issue.embeddedItemId}</strong><span>{statusText(issue.statusAfterApply)} · {issue.affectedBarGroupCount} 组钢筋</span>{recommended ? <><p>{recommended.title}：{recommended.detail}</p><p className="small">净距 {formatEngineeringValue(issue.minimumActualClearanceM, 'length')} → {formatEngineeringValue(recommended.predictedClearanceM, 'length')}（要求 {formatEngineeringValue(issue.requiredClearanceM, 'length')}）</p><button className="tiny" onClick={() => void apply(issue.issueId, recommended.candidateId)}>应用推荐方案</button></> : null}</div>;
          })}</details>
        </> : <p className="small">点击“分析”后生成可比较的构造协调方案，并重新执行碰撞筛查。</p>}
      </article>
      <article className="summaryPanel">
        <div className="panelTitleRow"><div><h4>高风险节点局部子模型</h4><p className="small">先用六自由度模型筛选，再生成 CalculiX/Abaqus 非线性固体接触输入文件和构造变体。</p></div><button className="secondary" onClick={() => void execute('计算节点局部子模型', () => api.getNodeSubmodels(project.id, 8), setSubmodels)}>计算</button></div>
        {submodels ? <>
          <div className="metricLine"><span>子模型数</span><strong>{String(submodels.summary?.submodelCount ?? 0)}</strong></div>
          <div className="metricLine"><span>最大利用率</span><strong>{String(submodels.summary?.maxUtilization ?? '—')}</strong></div>
          <div className="tableScroll"><table className="table compactTable"><thead><tr><th>节点</th><th>{withUnitLabel('轴力', 'force')}</th><th>{withUnitLabel('接触应力', 'stress')}</th><th>{withUnitLabel('位移', 'displacement')}</th><th>推荐变体</th><th>求解文件</th><th>状态</th></tr></thead><tbody>{(submodels.submodels ?? []).slice(0, 8).map((row: Record<string, any>) => <tr key={String(row.nodeId)}><td>{String(row.nodeCode)}</td><td>{formatEngineeringValue(row.designForceKn, 'force')}</td><td>{formatEngineeringValue(row.results?.maxContactPressureMpa, 'stress')}</td><td>{formatEngineeringValue(row.results?.maxDisplacementMm, 'displacement')}</td><td>{String(row.recommendedVariant?.title ?? '—')}</td><td>{String(row.solverDeckFilename ?? '—')}</td><td>{statusText(row.status)}</td></tr>)}</tbody></table></div>
        </> : <p className="small">优先选择高利用率、滑移或转角较大的节点。该结果用于深化排序，不能替代专项非线性有限元。</p>}
      </article>
      <article className="summaryPanel">
        <div className="panelTitleRow"><div><h4>钢筋笼—吊机—站位联合优化</h4><p className="small">结合重量、作业半径、能力曲线、臂长、接地压力和吊运路径。</p></div><button className="secondary" onClick={() => void execute('优化吊机与站位', () => api.getCraneLogistics(project.id), setLogistics)}>优化</button></div>
        {logistics ? <>
          <div className="metricLine"><span>可行 / 失败工况</span><strong>{String(logistics.summary?.feasibleCount ?? 0)} / {String(logistics.summary?.failCount ?? 0)}</strong></div>
          <div className="metricLine"><span>吊机库 / 站位</span><strong>{String(logistics.summary?.craneLibraryCount ?? 0)} / {String(logistics.summary?.standPointCount ?? 0)}</strong></div>
          <details><summary>查看控制吊装工况</summary><div className="tableScroll"><table className="table compactTable"><thead><tr><th>笼段</th><th>{withUnitLabel('重量', 'weight')}</th><th>推荐吊机 / 站位</th><th>{withUnitLabel('半径', 'length')}</th><th>能力</th><th>地基</th><th>风载</th><th>状态</th></tr></thead><tbody>{(logistics.cases ?? []).slice(0, 12).map((row: Record<string, any>) => <tr key={String(row.segmentId)}><td>{String(row.segmentId)}</td><td>{Number(row.cageWeightT ?? 0).toFixed(2)} t</td><td>{String(row.recommended?.craneName ?? '—')} / {String(row.recommended?.standId ?? '—')}</td><td>{formatEngineeringValue(row.recommended?.workingRadiusM, 'length')}</td><td>{row.recommended ? `${(Number(row.recommended.capacityUtilization ?? 0) * 100).toFixed(1)}%` : '—'}</td><td>{row.recommended ? `${(Number(row.recommended.groundUtilization ?? 0) * 100).toFixed(1)}%` : '—'}</td><td>{row.recommended ? `${(Number(row.recommended.windUtilization ?? 0) * 100).toFixed(1)}%` : '—'}</td><td>{statusText(row.status)}</td></tr>)}</tbody></table></div></details>
        </> : <p className="small">使用通用吊机能力曲线库生成建议。施工前仍需替换为实际设备工况表和现场地基承载参数。</p>}
      </article>
    </div>
  </section>;
}
