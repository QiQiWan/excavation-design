import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { api } from '../api/client';
import type { PitTask, Project, SupportLayoutOptimizationCandidate } from '../types/domain';
import { formatEngineeringValue, withUnitLabel } from '../utils/units';

function letter(rank: number) {
  return String.fromCharCode(64 + Math.max(1, Math.min(26, rank || 1)));
}

function familyLabel(candidate: SupportLayoutOptimizationCandidate) {
  const family = String(candidate.variableSummary?.topologyFamily ?? 'direct_grid');
  if (family === 'hybrid_diagonal') return '斜撑 + 短对撑混合';
  if (family === 'bidirectional_grid') return '双向支撑网格';
  return '传统直对撑';
}

function toRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {};
}

type XY = { x: number; y: number };
type SchemeGeometry = { outline: XY[]; supports: Record<string, any>[]; columns: Record<string, any>[]; bounds: { x: number; y: number; width: number; height: number } };

function schemeGeometry(candidate: SupportLayoutOptimizationCandidate): SchemeGeometry {
  const geom = toRecord(candidate.planGeometry);
  const outline = ((geom.outline ?? []) as XY[]).filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
  const supports = (geom.supports ?? []) as Record<string, any>[];
  const columns = (geom.columns ?? []) as Record<string, any>[];
  const xs = [
    ...outline.map((p) => Number(p.x)),
    ...supports.flatMap((s) => [Number(s.start?.x), Number(s.end?.x)]),
    ...columns.map((c) => Number(c.location?.x)),
  ].filter(Number.isFinite);
  const ys = [
    ...outline.map((p) => Number(p.y)),
    ...supports.flatMap((s) => [Number(s.start?.y), Number(s.end?.y)]),
    ...columns.map((c) => Number(c.location?.y)),
  ].filter(Number.isFinite);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 1;
  const minY = ys.length ? Math.min(...ys) : 0;
  const maxY = ys.length ? Math.max(...ys) : 1;
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const padding = Math.max(Math.min(spanX, spanY) * 0.08, 0.8);
  return {
    outline,
    supports,
    columns,
    bounds: { x: minX - padding, y: -(maxY + padding), width: spanX + 2 * padding, height: spanY + 2 * padding },
  };
}

function JunctionMarkers({ candidate }: { candidate: SupportLayoutOptimizationCandidate }) {
  const metrics = toRecord(candidate.metrics);
  const wallNodes = (metrics.wallJunctionPoints ?? []) as Record<string, any>[];
  const internalNodes = ((metrics.junctionPoints ?? []) as Record<string, any>[]).filter((node) => String(node.nodeType ?? '') === 'internal');
  return <>
    {wallNodes.map((node, index) => <g key={`wall-junction-${index}`} className="schemeWallJunction">
      <circle cx={Number(node.point?.x ?? 0)} cy={-Number(node.point?.y ?? 0)} r={Number(node.highDegree) ? 0.72 : 0.54} vectorEffect="non-scaling-stroke" />
      <title>{`墙上汇交：${String((node.supportCodes ?? []).join(' / '))}`}</title>
    </g>)}
    {internalNodes.map((node, index) => <g key={`internal-junction-${index}`} className="schemeInternalJunction">
      <rect x={Number(node.point?.x ?? 0) - 0.42} y={-Number(node.point?.y ?? 0) - 0.42} width="0.84" height="0.84" vectorEffect="non-scaling-stroke" />
      <title>{`内部汇交：${String((node.supportCodes ?? []).join(' / '))}`}</title>
    </g>)}
  </>;
}

function SchemeSvgContent({ geometry, showLabels = false }: { geometry: SchemeGeometry; showLabels?: boolean }) {
  const outlinePoints = geometry.outline.map((p) => `${p.x},${-p.y}`).join(' ');
  return <>
    {outlinePoints ? <polygon points={outlinePoints} className="schemePitOutline" /> : null}
    {geometry.supports.map((support, index) => {
      const a = { x: Number(support.start?.x ?? 0), y: -Number(support.start?.y ?? 0) };
      const b = { x: Number(support.end?.x ?? 0), y: -Number(support.end?.y ?? 0) };
      const role = String(support.role ?? support.supportRole ?? 'main_strut');
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      return <g key={String(support.id ?? index)}>
        <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`schemeLine ${role}`} vectorEffect="non-scaling-stroke" />
        {showLabels && index < 50 ? <text x={mx} y={my} className="schemeSupportLabel" vectorEffect="non-scaling-stroke">{String(support.code ?? support.id ?? index + 1)}</text> : null}
      </g>;
    })}
    {geometry.columns.map((column, index) => {
      const x = Number(column.location?.x ?? 0);
      const y = -Number(column.location?.y ?? 0);
      return <circle key={String(column.id ?? index)} cx={x} cy={y} r="0.42" className="schemeColumnPoint" vectorEffect="non-scaling-stroke" />;
    })}
  </>;
}

function SchemePreview({ candidate }: { candidate: SupportLayoutOptimizationCandidate }) {
  const geometry = useMemo(() => schemeGeometry(candidate), [candidate]);
  const b = geometry.bounds;
  return <svg className="schemeOverviewSvg" viewBox={`${b.x} ${b.y} ${b.width} ${b.height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={`方案 ${letter(candidate.rank)} 支撑平面预览`}>
    <SchemeSvgContent geometry={geometry} />
    <JunctionMarkers candidate={candidate} />
  </svg>;
}

function InteractiveSchemeViewer({ candidate }: { candidate: SupportLayoutOptimizationCandidate }) {
  const geometry = useMemo(() => schemeGeometry(candidate), [candidate]);
  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [drag, setDrag] = useState<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const [labels, setLabels] = useState(false);

  useEffect(() => { setZoom(1); setPan({ x: 0, y: 0 }); }, [candidate.id]);
  const b = geometry.bounds;
  const viewWidth = b.width / zoom;
  const viewHeight = b.height / zoom;
  const viewBox = `${b.x + pan.x + (b.width - viewWidth) / 2} ${b.y + pan.y + (b.height - viewHeight) / 2} ${viewWidth} ${viewHeight}`;

  function zoomBy(factor: number) {
    setZoom((value) => Math.max(0.8, Math.min(8, value * factor)));
  }
  function reset() { setZoom(1); setPan({ x: 0, y: 0 }); }
  function pointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drag || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const dx = (event.clientX - drag.x) * (viewWidth / Math.max(rect.width, 1));
    const dy = (event.clientY - drag.y) * (viewHeight / Math.max(rect.height, 1));
    setPan({ x: drag.panX - dx, y: drag.panY - dy });
  }

  return <div className="schemeInteractiveViewer">
    <div className="schemeViewerToolbar">
      <strong>方案 {letter(candidate.rank)} · {familyLabel(candidate)}</strong>
      <div>
        <button type="button" className="secondary" onClick={() => zoomBy(1.25)} aria-label="放大方案预览">＋</button>
        <button type="button" className="secondary" onClick={() => zoomBy(0.8)} aria-label="缩小方案预览">－</button>
        <button type="button" className="secondary" onClick={reset}>适应窗口</button>
        <button type="button" className="secondary" onClick={() => setLabels((value) => !value)}>{labels ? '隐藏编号' : '显示编号'}</button>
      </div>
    </div>
    <svg
      ref={svgRef}
      className={`schemeDetailSvg ${drag ? 'dragging' : ''}`}
      viewBox={viewBox}
      preserveAspectRatio="xMidYMid meet"
      onWheel={(event) => { event.preventDefault(); zoomBy(event.deltaY < 0 ? 1.12 : 0.89); }}
      onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); setDrag({ x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y }); }}
      onPointerMove={pointerMove}
      onPointerUp={() => setDrag(null)}
      onPointerCancel={() => setDrag(null)}
      role="img"
      aria-label={`方案 ${letter(candidate.rank)} 可缩放平移支撑总平面`}
    >
      <SchemeSvgContent geometry={geometry} showLabels={labels} />
      <JunctionMarkers candidate={candidate} />
    </svg>
    <p className="schemeViewerHint">滚轮缩放，按住拖动平移；模型已按实际外包范围自动居中并最大化利用视口。</p>
  </div>;
}

async function waitForTask(task: PitTask, onUpdate: (task: PitTask) => void): Promise<PitTask> {
  let current = task;
  const started = Date.now();
  while (!['success', 'failed', 'cancelled'].includes(current.status)) {
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    current = await api.getTask(current.id);
    onUpdate(current);
    if (Date.now() - started > 12 * 60 * 1000) throw new Error(`方案任务 ${current.title} 超时。`);
  }
  return current;
}

export default function SchemeComparisonPanel({
  project,
  onGenerateCandidates,
  onRunComparison,
  onAdopt,
  onRefresh,
  compact = false,
}: {
  project: Project;
  onGenerateCandidates?: () => Promise<unknown> | void;
  onRunComparison?: () => Promise<unknown> | void;
  onAdopt?: (candidateId: string) => Promise<unknown> | void;
  onRefresh?: () => Promise<unknown> | void;
  compact?: boolean;
}) {
  const candidates = project.retainingSystem?.supportLayoutRepair?.candidates?.slice(0, 3) ?? [];
  const calculationState = toRecord(project.advancedEngineering?.calculationState);
  const requiresRecalculation = Boolean(calculationState.requiresRecalculation);
  const latest = requiresRecalculation || !project.calculationResults.length
    ? undefined
    : project.calculationResults[project.calculationResults.length - 1];
  const storedFullRows = ((latest?.supportLayoutRepair?.candidateFullCalculations
    ?? (latest?.reportDiagramData?.candidateFullCalculationComparison as Record<string, unknown>[] | undefined)
    ?? []) as Record<string, any>[]);
  // A candidate's full calculation is valid only for the topology snapshot that
  // produced it.  Once geometry/topology changes, show current proxy metrics and
  // require a fresh comparison instead of silently falling back to stale rows.
  const fullRows = requiresRecalculation ? [] : storedFullRows;
  const fullById = useMemo(() => new Map(fullRows.map((row) => [String(row.candidateId ?? ''), row])), [fullRows]);
  const recommended = fullRows.find((row) => row.recommendedByFullCalculation) ?? [...fullRows].sort((a, b) => Number(a.decisionRank ?? 99) - Number(b.decisionRank ?? 99))[0];
  const initial = String(recommended?.candidateId ?? project.retainingSystem?.supportLayoutRepair?.selectedCandidateId ?? candidates[0]?.id ?? '');
  const [selectedId, setSelectedId] = useState(initial);
  const [batchTasks, setBatchTasks] = useState<PitTask[]>([]);
  const [batchError, setBatchError] = useState<string>();
  const [batchBusy, setBatchBusy] = useState(false);
  const selected = candidates.find((candidate) => String(candidate.id) === selectedId) ?? candidates[0];

  useEffect(() => {
    if (!selectedId && candidates[0]?.id) setSelectedId(String(candidates[0].id));
  }, [candidates, selectedId]);

  async function adopt() {
    if (!selected?.id) return;
    if (onAdopt) await onAdopt(selected.id);
    else await api.adoptSupportCandidate(project.id, selected.id);
  }

  async function runParallelComparison() {
    if (!candidates.length) return;
    if (!onRefresh) {
      if (onRunComparison) await onRunComparison();
      return;
    }
    setBatchBusy(true);
    setBatchError(undefined);
    try {
      const created = await api.createCandidateComparisonBatch(project.id, Math.min(3, candidates.length), true);
      setBatchTasks(created.tasks);
      const update = (updated: PitTask) => setBatchTasks((items) => items.map((item) => item.id === updated.id ? updated : item));
      const finished = await Promise.all(created.tasks.map((task) => waitForTask(task, update)));
      const failed = finished.filter((task) => task.status !== 'success');
      if (failed.length) throw new Error(failed.map((task) => task.error || `${task.title}：${task.status}`).join('；'));
      await onRefresh();
    } catch (error) {
      setBatchError(error instanceof Error ? error.message : String(error));
    } finally {
      setBatchBusy(false);
    }
  }

  return <section className={`schemeComparisonPanel ${compact ? 'compact' : ''}`}>
    {requiresRecalculation && <div className="warning schemeStateWarning"><strong>当前支撑拓扑已变更，旧计算结果已归档。</strong><span>原因：{String(calculationState.reason ?? '方案采用或几何修改')}。请重新生成施工工况并计算后再比较或出图。</span></div>}
    <div className="focusSectionHeader">
      <div>
        <span className="sectionKicker">整体方案决策</span>
        <h3>A / B / C 支撑方案比选</h3>
        <p>候选卡片先展示几何拓扑预检；只有完成独立施工阶段计算后，才显示轴力、位移、围檩内力和工程排名。几何代理值不作为设计内力。</p>
      </div>
      <div className="schemeHeaderActions">
        <span className={`schemeState ${fullRows.length >= 3 ? 'ready' : 'pending'}`}>{requiresRecalculation ? '旧结果已失效' : fullRows.length >= 3 ? '完整计算已完成' : '待完整计算'}</span>
        {!candidates.length && onGenerateCandidates ? <button onClick={() => void onGenerateCandidates()}>生成 A/B/C 候选</button> : null}
        {candidates.length ? <button className="secondary" disabled={batchBusy} onClick={() => void runParallelComparison()}>{batchBusy ? 'A/B/C 完整计算中…' : '完整计算 A/B/C'}</button> : null}
      </div>
    </div>

    {batchTasks.length ? <div className="schemeBatchProgress" aria-live="polite">
      {batchTasks.map((task, index) => <div key={task.id} className={`schemeTaskRow task-${task.status}`}>
        <span>方案 {letter(index + 1)}</span><progress max={100} value={task.progress} /><strong>{Math.round(task.progress)}%</strong><em>{task.currentStep || task.status}</em>{task.result?.cacheHit ? <b>缓存命中</b> : null}
      </div>)}
      {batchError ? <p className="error">{batchError}</p> : null}
    </div> : null}

    {!candidates.length ? <div className="emptyDecisionState">
      <strong>尚未生成整体候选方案</strong>
      <p>先生成候选方案，系统将返回斜撑混合、传统直对撑和双向网格等完整方案。</p>
    </div> : <>
      <div className="schemeOverviewGrid">
        {candidates.map((candidate) => {
          const full = requiresRecalculation ? {} : (fullById.get(String(candidate.id ?? '')) ?? toRecord(candidate.fullCalculation));
          const hasFullCalculation = full.maxSupportAxialForce != null && full.maxDisplacement != null;
          const isSelected = String(candidate.id) === String(selected?.id);
          const isRecommended = Boolean(full.recommendedByFullCalculation);
          return <button key={String(candidate.id ?? candidate.rank)} type="button" className={`schemeOverviewCard ${isSelected ? 'selected' : ''} ${isRecommended ? 'recommended' : ''}`} onClick={() => setSelectedId(String(candidate.id ?? ''))}>
            <div className="schemeCardHeader"><strong>方案 {letter(candidate.rank)}</strong><span>{familyLabel(candidate)}</span>{isRecommended ? <em>推荐</em> : null}</div>
            <SchemePreview candidate={candidate} />
            <div className="schemeKeyMetrics">
              <span><small>支撑 / 立柱</small><strong>{candidate.supportCount} / {candidate.columnCount}</strong></span>
              <span><small>非法穿越 / 墙上汇交 / 内部汇交</small><strong>{candidate.crossingCount ?? 0} / {candidate.wallJunctionCount ?? Number(candidate.metrics?.wallJunctionCount ?? 0)} / {Number(candidate.metrics?.internalJunctionCount ?? candidate.junctionCount ?? 0)}</strong></span>
              <span><small>角撑扇形异常 / 墙节点拥挤</small><strong>{Number(candidate.metrics?.cornerBraceParallelismIssueCount ?? 0)} / {Number(candidate.metrics?.cornerBraceEndpointCongestionCount ?? 0)}</strong></span>
              <span><small>{withUnitLabel('最长跨度', 'length')}</small><strong>{formatEngineeringValue(candidate.maxSpanLength, 'length')}</strong></span>
              <span><small>{hasFullCalculation ? withUnitLabel('最大轴力', 'force') : '完整计算状态'}</small><strong>{hasFullCalculation ? formatEngineeringValue(full.maxSupportAxialForce, 'force') : '待计算'}</strong></span>
              <span><small>{hasFullCalculation ? withUnitLabel('最大位移', 'displacement') : '几何拓扑评分'}</small><strong>{hasFullCalculation ? formatEngineeringValue(full.maxDisplacement, 'displacement') : String(candidate.score ?? '—')}</strong></span>
            </div>
            <div className="schemeRiskLine"><span className={Number(full.failCount ?? candidate.failCount ?? 0) > 0 ? 'riskFail' : 'riskPass'}>{hasFullCalculation ? '计算 Fail' : '拓扑 Fail'} {String(full.failCount ?? candidate.failCount ?? 0)}</span><span>{hasFullCalculation ? '计算 Warning' : '拓扑 Warning'} {String(full.warningCount ?? candidate.warningCount ?? 0)}</span><span>{hasFullCalculation ? `综合得分 ${String(full.decisionScore ?? '—')}` : '未生成设计内力'}</span></div>
          </button>;
        })}
      </div>
      {selected ? <>
        <InteractiveSchemeViewer candidate={selected} />
        <div className="schemeDecisionBar">
          <div><strong>当前选择：方案 {letter(selected.rank)} · {familyLabel(selected)}</strong><span>{String(fullById.get(String(selected.id ?? ''))?.decisionReason ?? selected.constructabilityNote ?? '选择后将整体替换支撑、立柱和节点，并要求重新计算。')}</span></div>
          <button onClick={() => void adopt()} disabled={!selected.id}>采用整套方案</button>
        </div>
      </> : null}
      {!compact && fullRows.length ? <div className="tableScroll"><table className="table compactTable schemeDecisionTable"><thead><tr><th>方案</th><th>完整排名</th><th>得分</th><th>{withUnitLabel('最大轴力', 'force')}</th><th>{withUnitLabel('最大位移', 'displacement')}</th><th>{withUnitLabel('围檩弯矩', 'moment')}</th><th>Fail / Warning</th><th>出图闸门</th></tr></thead><tbody>{fullRows.slice(0, 3).map((row, index) => <tr key={String(row.candidateId ?? index)} className={row.recommendedByFullCalculation ? 'recommendedSchemeRow' : ''}><td>方案 {String(row.schemeLabel ?? letter(index + 1))}</td><td>{String(row.decisionRank ?? '—')}</td><td>{String(row.decisionScore ?? '—')}</td><td>{formatEngineeringValue(row.maxSupportAxialForce, 'force')}</td><td>{formatEngineeringValue(row.maxDisplacement, 'displacement')}</td><td>{formatEngineeringValue(row.maxWaleMoment, 'moment')}</td><td>{String(row.failCount ?? 0)} / {String(row.warningCount ?? 0)}</td><td>{row.formalGateAllowed ? '允许' : '不允许'}</td></tr>)}</tbody></table></div> : null}
    </>}
  </section>;
}
