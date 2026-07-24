import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { api } from '../api/client';
import { waitForTaskWithHealth } from '../utils/taskPolling';
import type { PitTask, Project, SupportLayoutOptimizationCandidate } from '../types/domain';
import { formatEngineeringValue, withUnitLabel } from '../utils/units';
import { beginGlobalActivity, finishGlobalActivity, updateGlobalActivity } from '../app/GlobalRequestProgress';
import { sanitizeCandidatePlanGeometry } from '../drawing/candidateGeometry';

function letter(rank: number) {
  return String.fromCharCode(64 + Math.max(1, Math.min(26, rank || 1)));
}

function familyLabel(candidate: SupportLayoutOptimizationCandidate) {
  const summary = toRecord(candidate.variableSummary);
  const topologyClass = String(summary.transferTopologyClass ?? '').trim();
  if (topologyClass === 'closed_ring') return '闭合内环梁 + 径向支撑';
  if (topologyClass === 'junction_hub_frame') return '交汇核心框架 + 径向支撑';
  if (topologyClass === 'ring_chord_frame') return '内环弦杆框架 + 径向支撑';
  const explicit = String(summary.schemeLabel ?? '').trim();
  if (explicit) return explicit;
  const family = String(summary.topologyFamily ?? 'direct_grid');
  if (family === 'hybrid_diagonal') return '斜撑 + 短对撑混合';
  if (family === 'bidirectional_grid') return '双向支撑网格';
  if (family === 'ring_radial') return '闭合内环梁 + 径向支撑';
  if (family === 'zoned_direct') return '异形分区墙—墙对撑';
  return '传统直对撑';
}

const reasonLabels: Record<string, string> = {
  'excavation geometry or elevation changed': '基坑轮廓或开挖标高已修改',
  'support system regenerated': '支撑体系已重新生成',
  'diaphragm wall geometry regenerated': '围护墙几何已重新生成',
  candidate_source_hash_mismatch: '候选来源与当前设计输入不一致',
  no_formal_candidate: '当前没有通过硬约束的正式候选',
  insufficient_formal_candidates_for_comparison: '正式候选少于 2 个，不能进行完整比选',
};

const shapeLabels: Record<string, string> = {
  orthogonal_concave_corridor: '正交凹形走廊（L/U/T 等）',
  near_square_quadrilateral: '近方形四边形',
  slender_quadrilateral: '长条形四边形',
  general_concave_polygon: '一般凹多边形',
  slender_stepped_strip: '变宽阶梯形长条基坑',
};

const blockingLabels: Record<string, string> = {
  shape_transfer_system: '凹角/多臂交汇区缺少闭合转接体系',
  wale_support_bay: '围檩直接支点间距超限',
  support_member_screening: '支撑构件初步稳定或承载力筛查未通过',
  support_crossing: '支撑发生非法平面穿越',
  unsupported_internal_endpoint: '支撑端点缺少明确边界支承',
  support_to_support_terminal: '支撑终止于另一支撑中部',
};

function readinessLabel(value: unknown, pending = '未闭合') {
  return value === true ? '通过' : pending;
}

function beamRoleLabel(role: string) {
  if (role === 'transfer_frame_beam') return '转接框架梁';
  if (role === 'transfer_brace') return '内环弦杆';
  if (role === 'transfer_ring_beam') return '闭合转接环梁';
  return '转接构件';
}

function localizedReason(value: unknown) {
  const raw = String(value ?? '方案采用或几何修改');
  return reasonLabels[raw] ?? raw;
}

function toRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {};
}

type XY = { x: number; y: number };
type SchemeGeometry = { outline: XY[]; supports: Record<string, any>[]; columns: Record<string, any>[]; transferBeams: Record<string, any>[]; transferZones: Record<string, any>[]; previewIntegrity: Record<string, any>; hasData: boolean; bounds: { x: number; y: number; width: number; height: number } };

function fittedBounds(minX: number, maxX: number, minY: number, maxY: number, targetAspect = 2.25) {
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const cx = (minX + maxX) / 2;
  const cy = -(minY + maxY) / 2;
  const pad = Math.max(Math.min(spanX, spanY) * 0.10, 1);
  let width = spanX + 2 * pad;
  let height = spanY + 2 * pad;
  const ratio = width / height;
  if (ratio > targetAspect) height = width / targetAspect;
  else width = height * targetAspect;
  return { x: cx - width / 2, y: cy - height / 2, width, height };
}

function schemeGeometry(candidate: SupportLayoutOptimizationCandidate): SchemeGeometry {
  const sanitized = sanitizeCandidatePlanGeometry(candidate.planGeometry);
  const { outline, supports, columns, transferBeams, transferZones, previewIntegrity } = sanitized;
  const xs = [
    ...outline.map((p) => p.x),
    ...supports.flatMap((item) => [item.start.x, item.end.x]),
    ...columns.map((item) => item.location.x),
    ...transferBeams.flatMap((beam) => (beam.points ?? []).map((point: XY) => point.x)),
  ];
  const ys = [
    ...outline.map((p) => p.y),
    ...supports.flatMap((item) => [item.start.y, item.end.y]),
    ...columns.map((item) => item.location.y),
    ...transferBeams.flatMap((beam) => (beam.points ?? []).map((point: XY) => point.y)),
  ];
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 1;
  const minY = ys.length ? Math.min(...ys) : 0;
  const maxY = ys.length ? Math.max(...ys) : 1;
  return {
    outline, supports, columns, transferBeams, transferZones, previewIntegrity,
    hasData: outline.length >= 3 && (supports.length > 0 || transferBeams.length > 0),
    bounds: fittedBounds(minX, maxX, minY, maxY),
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
    {geometry.transferZones.map((zone, index) => {
      const points = ((zone.outline ?? []) as XY[]).map((point) => `${point.x},${-point.y}`).join(' ');
      return points ? <polygon key={`transfer-zone-${index}`} points={points} className="schemeTransferZone" vectorEffect="non-scaling-stroke"><title>异形闭合转接区</title></polygon> : null;
    })}
    {geometry.transferBeams.map((beam, index) => {
      const points = (beam.points ?? []) as XY[];
      if (points.length < 2) return null;
      const role = String(beam.role ?? 'transfer_ring_beam');
      return <polyline key={String(beam.id ?? index)} points={points.map((point) => `${Number(point.x)},${-Number(point.y)}`).join(' ')} className={`schemeTransferBeam ${role}`} vectorEffect="non-scaling-stroke"><title>{`${String(beam.code ?? '转接构件')} · ${beamRoleLabel(role)}`}</title></polyline>;
    })}
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

function PreviewIntegrityNotice({ candidate }: { candidate: SupportLayoutOptimizationCandidate }) {
  const integrity = schemeGeometry(candidate).previewIntegrity;
  const status = String(integrity.status ?? 'complete');
  if (status === 'complete' && !integrity.truncated) return null;
  const label = status === 'incomplete' ? '预览不完整' : '预览已抽样';
  return <div className={`previewIntegrityNotice ${status}`}><strong>{label}</strong><span>{String(integrity.message ?? '请读取完整拓扑后再判断支撑闭合。')}</span></div>;
}

function SchemePreview({ candidate, loading = false }: { candidate: SupportLayoutOptimizationCandidate; loading?: boolean }) {
  const geometry = useMemo(() => schemeGeometry(candidate), [candidate]);
  const b = geometry.bounds;
  const emptyFontSize = Math.max(b.width, b.height) * 0.08;
  return <svg className="schemeOverviewSvg" viewBox={`${b.x} ${b.y} ${b.width} ${b.height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={`方案 ${letter(candidate.rank)} 支撑平面预览`}>
    {geometry.hasData ? <>
      <SchemeSvgContent geometry={geometry} />
      <JunctionMarkers candidate={candidate} />
    </> : <g className="schemePreviewEmpty"><rect x={b.x} y={b.y} width={b.width} height={b.height} /><text x={b.x + b.width / 2} y={b.y + b.height / 2} fontSize={emptyFontSize}>{loading ? '正在按需读取方案几何…' : '方案几何尚未写入工作区'}</text></g>}
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
  const emptyFontSize = Math.max(b.width, b.height) * 0.055;

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
      {geometry.hasData ? <>
        <SchemeSvgContent geometry={geometry} showLabels={labels} />
        <JunctionMarkers candidate={candidate} />
      </> : <g className="schemePreviewEmpty"><rect x={b.x} y={b.y} width={b.width} height={b.height} /><text x={b.x + b.width / 2} y={b.y + b.height / 2} fontSize={emptyFontSize}>未取得方案几何，请刷新候选预览</text></g>}
    </svg>
    <p className="schemeViewerHint">滚轮缩放 · 拖动平移 · 自动居中</p>
  </div>;
}

export default function SchemeComparisonPanel({
  project,
  onGenerateCandidates,
  onRunComparison,
  onAdopt,
  onRefresh,
  onSelectCandidate,
  compact = false,
}: {
  project: Project;
  onGenerateCandidates?: () => Promise<unknown> | void;
  onRunComparison?: () => Promise<unknown> | void;
  onAdopt?: (candidateId: string) => Promise<unknown> | void;
  onRefresh?: () => Promise<unknown> | void;
  onSelectCandidate?: (candidate: SupportLayoutOptimizationCandidate) => void;
  compact?: boolean;
}) {
  const repair = project.retainingSystem?.supportLayoutRepair;
  const candidateState = toRecord(repair?.comparisonEligibility);
  const rawCandidates = repair?.candidates?.slice(0, 6) ?? [];
  const [previewById, setPreviewById] = useState<Map<string, Record<string, any>>>(new Map());
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string>();
  const [previewRefreshToken, setPreviewRefreshToken] = useState(0);
  const controlledCandidates = rawCandidates.filter((candidate) => String(candidate.variableSummary?.capabilityOutcome ?? '') === 'controlled_block');
  const qualifiedCandidates = rawCandidates.filter((candidate) => Boolean(candidate.hardConstraints?.passed) && candidate.variableSummary?.formalSchemeEligible !== false);
  const controlledBlock = controlledCandidates.length > 0 && qualifiedCandidates.length === 0;
  const controlledCandidate = controlledCandidates[0];
  const formalCandidateCount = Number(candidateState.formalCandidateCount ?? qualifiedCandidates.length);
  const comparisonAllowed = Boolean(candidateState.comparisonAllowed ?? formalCandidateCount >= 2);
  const candidateStateName = String(repair?.candidateState ?? candidateState.state ?? (rawCandidates.length ? 'diagnostic_only' : 'not_generated'));
  // Controlled-block cards may still be shown when their actual geometry is
  // different. They are diagnostic alternatives only and cannot be adopted or
  // sent to formal A/B/C calculation until the hard constraints pass.
  const baseCandidates = controlledBlock
    ? controlledCandidates.slice(0, 3)
    : (qualifiedCandidates.length ? qualifiedCandidates : rawCandidates).slice(0, 3);
  const missingPreviewIds = baseCandidates
    .filter((candidate) => !schemeGeometry(candidate).hasData)
    .map((candidate) => String(candidate.id ?? ''))
    .filter(Boolean);
  useEffect(() => {
    let alive = true;
    if (!missingPreviewIds.length) {
      setPreviewLoading(false);
      setPreviewError(undefined);
      return () => { alive = false; };
    }
    setPreviewLoading(true);
    api.getSupportCandidatePreviews(project.id, Math.max(3, baseCandidates.length))
      .then((bundle) => {
        if (!alive) return;
        const next = new Map<string, Record<string, any>>();
        for (const row of bundle.previews ?? []) {
          if (row.candidateId && row.planGeometry) next.set(String(row.candidateId), row.planGeometry);
        }
        setPreviewById(next);
        setPreviewError(undefined);
      })
      .catch((error) => { if (alive) setPreviewError(error instanceof Error ? error.message : String(error)); })
      .finally(() => { if (alive) setPreviewLoading(false); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, missingPreviewIds.join('|'), previewRefreshToken]);
  const candidates = useMemo(() => baseCandidates.map((candidate) => {
    const preview = previewById.get(String(candidate.id ?? ''));
    return preview ? ({ ...candidate, planGeometry: preview } as SupportLayoutOptimizationCandidate) : candidate;
  }), [baseCandidates, previewById]);
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
  const batchActivityId = useRef<string | undefined>(undefined);
  const selected = candidates.find((candidate) => String(candidate.id) === selectedId) ?? candidates[0];
  const controlledAlternatives = (controlledCandidate?.variableSummary?.alternativeSystemRecommendations ?? []) as string[];
  const shapeDiagnostics = toRecord(controlledCandidate?.variableSummary?.shapeDiagnostics);
  const selectedBlockingCategories = selected ? ((toRecord(selected.hardConstraints).blockingCategories ?? []) as string[]) : [];
  const selectedTransferAudit = selected ? toRecord(toRecord(selected.variableSummary).transferSystemAudit) : {};
  const selectedReadinessRaw = toRecord(selectedTransferAudit.readiness);
  const selectedReadiness = {
    geometryClosed: selectedReadinessRaw.geometryClosed ?? selectedTransferAudit.calculationReady ?? false,
    loadPathClosed: selectedReadinessRaw.loadPathClosed ?? selectedTransferAudit.calculationReady ?? false,
    structuralModelClosed: selectedReadinessRaw.structuralModelClosed ?? selectedTransferAudit.calculationReady ?? false,
    constructionStageClosed: selectedReadinessRaw.constructionStageClosed ?? selectedTransferAudit.formalCalculationReady ?? false,
  };
  const selectedFrameAnalysis = toRecord(selectedTransferAudit.frameAnalysis);
  const selectedSensitivity = toRecord(selectedFrameAnalysis.sensitivity);
  const selectedFull = selected ? toRecord(fullById.get(String(selected.id ?? ''))) : {};
  const selectedScaledCondition = selectedFull.maxTransferFrameScaledConditionNumber ?? selectedFrameAnalysis.maximumScaledConditionNumber;
  const selectedNodeStiffnessRatio = selectedFull.maxTransferNodeStiffnessRatio ?? selectedFrameAnalysis.maximumNodeStiffnessRatio;
  const selectedSensitivityStatus = selectedFull.transferSensitivityStatus ?? selectedSensitivity.status ?? '未运行';
  const selectedSensitivityChange = selectedFull.transferSensitivityMaximumChange ?? selectedSensitivity.maximumRelativeChange;

  useEffect(() => {
    if (!selectedId && candidates[0]?.id) setSelectedId(String(candidates[0].id));
  }, [candidates, selectedId]);

  useEffect(() => {
    if (selected) onSelectCandidate?.(selected);
  }, [selected, onSelectCandidate]);

  useEffect(() => {
    if (batchBusy && !batchActivityId.current) {
      batchActivityId.current = beginGlobalActivity({
        label: '正在完整计算 A/B/C 支撑方案',
        phase: '提交候选方案并建立独立施工阶段任务',
        expectedMs: 180000,
        blocking: true,
        progress: 2,
        path: `local://project/${project.id}/candidate-comparison`,
      });
    }
    if (!batchActivityId.current) return;
    const progress = batchTasks.length
      ? batchTasks.reduce((sum, task) => sum + Number(task.progress || 0), 0) / batchTasks.length
      : 2;
    const activeTask = batchTasks.find((task) => !['success', 'failed', 'cancelled', 'interrupted'].includes(task.status));
    updateGlobalActivity(batchActivityId.current, {
      phase: batchError || activeTask?.currentStep || (batchBusy ? '后台正在并行计算候选方案' : '候选方案计算已结束'),
      progress: Math.max(2, Math.min(100, progress)),
      blocking: true,
    });
    if (batchError) {
      finishGlobalActivity(batchActivityId.current, { ok: false, error: batchError, progress });
      batchActivityId.current = undefined;
    } else if (!batchBusy) {
      finishGlobalActivity(batchActivityId.current, { ok: true, phase: 'A/B/C 完整计算已完成', progress: 100 });
      batchActivityId.current = undefined;
    }
  }, [batchBusy, batchError, batchTasks, project.id]);

  async function adopt() {
    if (!selected?.id || controlledBlock || !Boolean(selected.hardConstraints?.passed) || selected.variableSummary?.formalSchemeEligible === false) return;
    if (onAdopt) await onAdopt(selected.id);
    else await api.adoptSupportCandidate(project.id, selected.id);
  }

  async function runParallelComparison() {
    if (!candidates.length || controlledBlock || !comparisonAllowed) return;
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
      const finished = await Promise.all(created.tasks.map((task) => waitForTaskWithHealth(task, update, { timeoutMs: 35 * 60 * 1000 })));
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
    {requiresRecalculation && <div className="warning schemeStateWarning"><strong>当前支撑拓扑已变更，旧计算结果已归档。</strong><span>原因：{localizedReason(calculationState.reason)}。请重新生成施工工况并计算后再比较或出图。</span></div>}
    {candidateStateName === 'stale' ? <div className="warning schemeStateWarning"><strong>候选方案来源已失效。</strong><span>轮廓、标高、墙体或设计参数发生变化，请重新生成 A/B/C。</span></div> : null}
    <div className="focusSectionHeader">
      <div>
        <span className="sectionKicker">整体方案决策</span>
        <h3>{controlledBlock ? '异形支撑体系诊断' : 'A / B / C 支撑方案比选'}</h3>
        <p>先比较体系与传力路径；完整计算后比较轴力、位移、围檩内力和安全系数。</p>
      </div>
      <div className="schemeHeaderActions">
        <span className={`schemeState ${fullRows.length >= 3 ? 'ready' : 'pending'}`}>{requiresRecalculation ? '旧结果已失效' : fullRows.length >= 3 ? '完整计算已完成' : '待完整计算'}</span>
        {!candidates.length && onGenerateCandidates ? <button onClick={() => void onGenerateCandidates()}>生成 A/B/C 候选</button> : null}
        {candidates.length ? <button className="secondary" disabled={batchBusy || controlledBlock || !comparisonAllowed} onClick={() => void runParallelComparison()}>{controlledBlock ? '诊断试案不可完整计算' : !comparisonAllowed ? '正式候选不足 2 个' : batchBusy ? 'A/B/C 完整计算中…' : '完整计算 A/B/C'}</button> : null}
      </div>
    </div>

    {controlledBlock ? <div className="warning schemeStateWarning controlledTopologyWarning" role="alert">
      <strong>当前平面未形成可正式采用的墙—墙轴压支撑闭环。</strong>
      <span>平面类型：{shapeLabels[String(shapeDiagnostics.classification ?? '')] ?? String(shapeDiagnostics.classification ?? '复杂平面')}；下方保留的是实际几何不同的诊断试案，用于定位分仓、端部支承和转接体系问题。硬约束未通过，不能采用、完整计算或正式出图。</span>
      <span>根因：{Array.from(new Set(controlledCandidates.flatMap((candidate) => ((toRecord(candidate.hardConstraints).blockingCategories ?? []) as string[])))).map((code) => blockingLabels[code] ?? code).join('；') || '当前传力路径未闭合'}。</span>
      {controlledAlternatives.length ? <span>建议结构体系：{controlledAlternatives.join('、')}。</span> : null}
    </div> : null}

    {previewError ? <div className="warning schemePreviewWarning"><strong>方案预览按需读取失败</strong><span>{previewError}</span><button type="button" className="secondary" onClick={() => { setPreviewById(new Map()); setPreviewRefreshToken((value) => value + 1); }}>重新读取</button></div> : null}

    {batchTasks.length ? <div className="schemeBatchProgress" aria-live="polite">
      {batchTasks.map((task, index) => <div key={task.id} className={`schemeTaskRow task-${task.status}`}>
        <span>方案 {letter(index + 1)}</span><progress max={100} value={task.progress} /><strong>{Math.round(task.progress)}%</strong><em>{task.currentStep || task.status}</em>{task.result?.cacheHit ? <b>缓存命中</b> : null}
      </div>)}
      {batchError ? <p className="error">{batchError}</p> : null}
    </div> : null}

    {!candidates.length ? <div className="emptyDecisionState">
      <strong>{controlledBlock ? '当前没有具备正式计算资格的 A/B/C 方案' : '尚未生成整体候选方案'}</strong>
      <p>{controlledBlock ? '诊断候选仅用于说明不同支撑密度和端部处理下的闭合失败。请调整体系、约束或分区后重新生成。' : '先生成候选方案，系统将按已识别平面和结构体系返回完整方案。'}</p>
    </div> : <>
      <div className={`schemeOverviewGrid count-${Math.min(3, candidates.length)}`}>
        {candidates.map((candidate) => {
          const full = requiresRecalculation ? {} : (fullById.get(String(candidate.id ?? '')) ?? toRecord(candidate.fullCalculation));
          const hasFullCalculation = full.maxSupportAxialForce != null && full.maxDisplacement != null;
          const isSelected = String(candidate.id) === String(selected?.id);
          const isRecommended = Boolean(full.recommendedByFullCalculation);
          return <button key={String(candidate.id ?? candidate.rank)} type="button" className={`schemeOverviewCard ${isSelected ? 'selected' : ''} ${isRecommended ? 'recommended' : ''}`} onClick={() => setSelectedId(String(candidate.id ?? ''))}>
            <div className="schemeCardHeader"><strong>{controlledBlock ? '诊断试案' : '方案'} {letter(candidate.rank)}</strong><span>{familyLabel(candidate)}</span>{isRecommended ? <em>推荐</em> : null}</div>
            <SchemePreview candidate={candidate} loading={previewLoading} />
            <div className="schemeKeyMetrics">
              <span><small>支撑 / 立柱 / 转接构件</small><strong>{candidate.supportCount} / {candidate.columnCount} / {schemeGeometry(candidate).transferBeams.length}</strong></span>
              <span><small>拓扑族</small><strong>{String(toRecord(candidate.variableSummary).transferTopologyClass ?? toRecord(candidate.variableSummary).topologyFamily ?? '—')}</strong></span>
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
        <PreviewIntegrityNotice candidate={selected} />
        <InteractiveSchemeViewer candidate={selected} />
        <div className="schemeDecisionBar">
          <div><strong>当前选择：{controlledBlock ? '诊断试案' : '方案'} {letter(selected.rank)} · {familyLabel(selected)}</strong><span>{String(fullById.get(String(selected.id ?? ''))?.decisionReason ?? selected.constructabilityNote ?? '选择后将整体替换支撑、立柱和节点，并要求重新计算。')}</span>{selectedBlockingCategories.length ? <span className="schemeRootCause">根因：{selectedBlockingCategories.map((code) => blockingLabels[code] ?? code).join('；')}</span> : null}{selectedTransferAudit.required ? <div className="schemeTransferReadiness"><span className="schemeTransferStatus">转接体系：{String(selectedTransferAudit.templateLabel ?? '未配置')} · 计算资格 {selectedTransferAudit.calculationReady ? '通过' : '未通过'} · 平面求解 {String(selectedFrameAnalysis.status ?? '待完整计算')} · 正式出图 {selectedTransferAudit.officialIssueReady ? '允许' : '待节点深化/审签'}</span><div className="schemeReadinessGrid"><span className={selectedReadiness.geometryClosed ? 'pass' : 'block'}><small>几何闭合</small><b>{readinessLabel(selectedReadiness.geometryClosed)}</b></span><span className={selectedReadiness.loadPathClosed ? 'pass' : 'block'}><small>传力闭合</small><b>{readinessLabel(selectedReadiness.loadPathClosed)}</b></span><span className={selectedReadiness.structuralModelClosed ? 'pass' : 'block'}><small>平面模型</small><b>{readinessLabel(selectedReadiness.structuralModelClosed, '待求解')}</b></span><span className={selectedReadiness.constructionStageClosed ? 'pass' : 'pending'}><small>施工阶段</small><b>{readinessLabel(selectedReadiness.constructionStageClosed, '待完整计算')}</b></span><span className={selectedTransferAudit.officialIssueReady ? 'pass' : 'pending'}><small>正式出图</small><b>{selectedTransferAudit.officialIssueReady ? '允许' : '待深化审签'}</b></span></div><div className="schemeNumericalEvidence"><span>尺度化条件数：{selectedScaledCondition != null ? Number(selectedScaledCondition).toExponential(2) : '待完整计算'}</span><span>节点最大刚度比：{selectedNodeStiffnessRatio != null ? Number(selectedNodeStiffnessRatio).toExponential(2) : '—'}</span><span>敏感性：{String(selectedSensitivityStatus)} {selectedSensitivityChange != null ? `· 最大变化 ${(Number(selectedSensitivityChange) * 100).toFixed(1)}%` : ''}</span><span>墙—围檩—框架迭代：{String(selectedFull.reactionIterationStatus ?? '待完整计算')}{selectedFull.reactionIterationCount != null ? ` · ${String(selectedFull.reactionIterationCount)} 次` : ''}</span><span>空间节点子模型：{String(selectedFull.transferSpatialStatus ?? '待完整计算')}{selectedFull.maxTransferTorsion != null ? ` · 扭矩 ${formatEngineeringValue(selectedFull.maxTransferTorsion, 'moment')}` : ''}{selectedFull.maxTransferInPlaneEccentricMoment != null ? ` · 偏心弯矩 ${formatEngineeringValue(selectedFull.maxTransferInPlaneEccentricMoment, 'moment')}` : ''}{selectedFull.maxTransferSpatialRotation != null ? ` · 转角 ${Number(selectedFull.maxTransferSpatialRotation).toExponential(2)} rad` : ''}</span></div></div> : null}</div>
          <button onClick={() => void adopt()} disabled={!selected.id || controlledBlock || !Boolean(selected.hardConstraints?.passed) || selected.variableSummary?.formalSchemeEligible === false}>{controlledBlock ? '诊断试案不可采用' : '采用整套方案'}</button>
        </div>
      </> : null}
      {!compact && fullRows.length ? <div className="tableScroll"><table className="table compactTable schemeDecisionTable"><thead><tr><th>方案</th><th>转接拓扑</th><th>Pareto</th><th>综合排名</th><th>得分</th><th>{withUnitLabel('最大轴力', 'force')}</th><th>{withUnitLabel('墙体最大位移', 'displacement')}</th><th>{withUnitLabel('平面框架位移', 'displacement')}</th><th>{withUnitLabel('围檩/转接梁弯矩', 'moment')}</th><th>Fail / Warning</th><th>阶段框架</th><th>尺度化条件数</th><th>节点刚度比</th><th>敏感性</th><th>反力迭代</th><th>空间节点 / 扭矩 / 偏心弯矩</th><th>出图闸门</th></tr></thead><tbody>{fullRows.slice(0, 3).map((row, index) => <tr key={String(row.candidateId ?? index)} className={row.recommendedByFullCalculation ? 'recommendedSchemeRow' : ''}><td>方案 {String(row.schemeLabel ?? letter(index + 1))}</td><td>{String(row.transferTopologyClass ?? row.topologyFamily ?? '—')}</td><td>{row.paretoFront ? `前沿 F${String(row.paretoRank ?? 1)}` : `F${String(row.paretoRank ?? '—')}`}</td><td>{String(row.decisionRank ?? '—')}</td><td>{String(row.decisionScore ?? '—')}</td><td>{formatEngineeringValue(row.maxSupportAxialForce, 'force')}</td><td>{formatEngineeringValue(row.maxDisplacement, 'displacement')}</td><td>{formatEngineeringValue(row.maxTransferFrameDisplacement, 'displacement')}</td><td>{formatEngineeringValue(row.maxWaleMoment, 'moment')}</td><td>{String(row.failCount ?? 0)} / {String(row.warningCount ?? 0)}</td><td>{row.transferFrameStatus === 'pass' ? '通过' : String(row.transferFrameStatus ?? '—')}</td><td>{row.maxTransferFrameScaledConditionNumber != null ? Number(row.maxTransferFrameScaledConditionNumber).toExponential(2) : '—'}</td><td>{row.maxTransferNodeStiffnessRatio != null ? Number(row.maxTransferNodeStiffnessRatio).toExponential(2) : '—'}</td><td>{String(row.transferSensitivityStatus ?? '未运行')}{row.transferSensitivityMaximumChange != null ? ` / ${(Number(row.transferSensitivityMaximumChange) * 100).toFixed(1)}%` : ''}</td><td>{String(row.reactionIterationStatus ?? '—')}{row.reactionIterationCount != null ? ` / ${String(row.reactionIterationCount)} 次` : ''}</td><td>{String(row.transferSpatialStatus ?? '—')}{row.maxTransferTorsion != null ? ` / T ${formatEngineeringValue(row.maxTransferTorsion, 'moment')}` : ''}{row.maxTransferInPlaneEccentricMoment != null ? ` / Mₑ ${formatEngineeringValue(row.maxTransferInPlaneEccentricMoment, 'moment')}` : ''}</td><td>{row.formalGateAllowed ? '允许' : '不允许'}</td></tr>)}</tbody></table></div> : null}
    </>}
  </section>;
}
