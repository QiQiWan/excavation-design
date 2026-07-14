import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { Project, SupportLayoutOptimizationCandidate, CalculationResult } from '../types/domain';
import WallCloud3DViewer from './WallCloud3DViewer';
import { formatEngineeringValue, withUnitLabel } from '../utils/units';

function conclusion(status?: string) {
  if (status === 'fail') return '存在 fail 项，自动方案不得进入施工图或正式报审。';
  if (status === 'warning' || status === 'manual_review') return '未形成施工图级结论，需按规范原文和项目条件复核。';
  if (status === 'pass') return '软件子集校核未发现 fail，仍需注册工程师复核。';
  return '尚未运行计算。';
}

function DeferredResultDetails({ summary, className = "engineeringDetails", children }: { summary: string; className?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <details className={className} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{summary}</summary>
    {open ? children : null}
  </details>;
}



function toNumber(value: unknown, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function polylinePoints(rows: Record<string, unknown>[], xKey: string, yKey: string, width: number, height: number, pad = 24) {
  const xs = rows.map((r) => toNumber(r[xKey])).filter(Number.isFinite);
  const ys = rows.map((r) => toNumber(r[yKey])).filter(Number.isFinite);
  if (!xs.length || !ys.length) return '';
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const xSpan = Math.max(1e-9, maxX - minX);
  const ySpan = Math.max(1e-9, maxY - minY);
  return rows.map((r) => {
    const x = pad + ((toNumber(r[xKey]) - minX) / xSpan) * (width - pad * 2);
    const y = height - pad - ((toNumber(r[yKey]) - minY) / ySpan) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

function MiniLineChart({ title, xLabel, yLabel, rows, xKey, series }: { title: string; xLabel: string; yLabel: string; rows: Record<string, unknown>[]; xKey: string; series: { key: string; label: string; className: string }[] }) {
  const width = 360;
  const height = 210;
  if (!rows.length) return <div className="envelopeEmpty">{title}：暂无曲线数据</div>;
  return (
    <div className="envelopeChartCard">
      <div className="chartTitle"><strong>{title}</strong><span>{xLabel} / {yLabel}</span></div>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="envelopeSvg">
        <line x1="24" y1="186" x2="340" y2="186" className="chartAxis" />
        <line x1="24" y1="18" x2="24" y2="186" className="chartAxis" />
        {[0.25, 0.5, 0.75].map((v) => <line key={v} x1="24" x2="340" y1={18 + v * 168} y2={18 + v * 168} className="chartGrid" />)}
        {series.map((item) => <polyline key={item.key} points={polylinePoints(rows, xKey, item.key, width, height)} className={`chartLine ${item.className}`} />)}
      </svg>
      <div className="chartLegend">{series.map((item) => <span key={item.key} className={item.className}>{item.label}</span>)}</div>
    </div>
  );
}

function SupportAxialBarChart({ rows, highlightLocator }: { rows: Record<string, unknown>[]; highlightLocator?: Record<string, unknown> }) {
  const data = rows.slice(0, 18).map((r, index) => ({
    label: String(r.supportId ?? r.stageId ?? `S${index + 1}`),
    value: Math.abs(toNumber(r.axialForceDesign ?? r.effectiveAxialForce ?? r.axialForce))
  })).filter((r) => r.value > 0);
  const maxValue = Math.max(1, ...data.map((r) => r.value));
  const targetId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');
  if (!data.length) return <div className="envelopeEmpty">支撑轴力包络：暂无支撑轴力数据</div>;
  return <div className="envelopeChartCard wide"><div className="chartTitle"><strong>支撑轴力包络</strong><span>按前 18 条控制支撑显示，单位 kN</span></div><div className="barEnvelope">{data.map((row) => <div key={row.label} className={`barRow ${targetId && row.label.includes(targetId) ? 'locatorBarHighlight' : ''}`}><span>{row.label}</span><div><em style={{ width: `${Math.max(3, row.value / maxValue * 100)}%` }} /></div><strong>{row.value.toFixed(0)}</strong></div>)}</div></div>;
}


function metricLabel(metric: string) {
  if (metric === 'moment') return '弯矩 kN·m/m';
  if (metric === 'shear') return '剪力 kN/m';
  return '变形 mm';
}

function metricValue(point: Record<string, unknown>, metric: string) {
  if (metric === 'moment') return Math.abs(toNumber(point.moment));
  if (metric === 'shear') return Math.abs(toNumber(point.shear));
  return Math.abs(toNumber(point.displacement));
}

function heatColor(ratio: number) {
  const r = Math.max(0, Math.min(1, ratio || 0));
  const hue = 220 - 220 * r;
  return `hsl(${hue.toFixed(0)} 82% 52%)`;
}

function WallContourMap({ project, latest, highlightLocator }: { project: Project; latest: CalculationResult; highlightLocator?: Record<string, unknown> }) {
  const [metric, setMetric] = useState<'moment' | 'shear' | 'displacement'>('displacement');
  const samples = (((latest.reportDiagramData ?? {}).wallForceSamples as any[]) ?? latest.stageResults.map((r) => r.wallInternalForce).filter(Boolean) ?? []) as any[];
  const walls = project.retainingSystem?.diaphragmWalls ?? [];
  const bySegment = new Map<string, any[]>();
  samples.forEach((sample) => {
    const key = String(sample?.segmentId ?? '');
    if (!key) return;
    bySegment.set(key, [...(bySegment.get(key) ?? []), sample]);
  });
  const rows = walls.map((wall) => {
    const wallSamples = bySegment.get(wall.segmentId) ?? [];
    const points = wallSamples.flatMap((sample) => sample?.points ?? []) as Record<string, unknown>[];
    const value = Math.max(0, ...points.map((pt) => metricValue(pt, metric)));
    return { wall, points, value };
  }).filter((row) => row.points.length);
  if (!rows.length) return null;
  const maxValue = Math.max(1, ...rows.map((row) => row.value));
  const pts = walls.flatMap((wall) => wall.axis.points ?? []);
  const xs = pts.map((pt) => pt.x); const ys = pts.map((pt) => pt.y);
  const minX = Math.min(...xs, 0); const maxX = Math.max(...xs, 60);
  const minY = Math.min(...ys, 0); const maxY = Math.max(...ys, 40);
  const pad = Math.max(3, Math.max(maxX - minX, maxY - minY) * 0.08);
  const viewBox = `${minX - pad} ${minY - pad} ${Math.max(1, maxX - minX + pad * 2)} ${Math.max(1, maxY - minY + pad * 2)}`;
  const targetId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');
  return (
    <section className="wallCloudPanel">
      <div className="sectionLead"><h4>围护墙变形与内力云图</h4><div className="segmentedControls"><button className={metric === 'displacement' ? 'active' : ''} onClick={() => setMetric('displacement')}>变形</button><button className={metric === 'moment' ? 'active' : ''} onClick={() => setMetric('moment')}>弯矩</button><button className={metric === 'shear' ? 'active' : ''} onClick={() => setMetric('shear')}>剪力</button></div></div>
      <div className="wallCloudGrid">
        <div className="wallCloudCard">
          <strong>平面控制云图 · {metricLabel(metric)}</strong>
          <svg viewBox={viewBox} preserveAspectRatio="xMidYMid meet" className="wallCloudSvg">
            {walls.map((wall) => {
              const row = rows.find((item) => item.wall.id === wall.id || item.wall.segmentId === wall.segmentId);
              const a = wall.axis.points[0]; const b = wall.axis.points[wall.axis.points.length - 1];
              if (!a || !b) return null;
              const highlighted = Boolean(targetId && (targetId === wall.id || targetId === wall.panelCode || targetId === wall.segmentId));
              const color = row ? heatColor(row.value / maxValue) : '#cbd5e1';
              return <g key={wall.id}><line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={highlighted ? '#eab308' : color} strokeWidth={highlighted ? 2.4 : 1.6} className="wallCloudLine" /><text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2} className="wallCloudLabel">{wall.panelCode}</text></g>;
            })}
          </svg>
          <div className="heatLegend"><span>低</span><em /><span>高：{maxValue.toFixed(metric === 'displacement' ? 1 : 0)}</span></div>
        </div>
        <div className="wallCloudCard">
          <strong>墙身深度云图 · 前 6 面墙</strong>
          <div className="wallStripList">
            {rows.slice(0, 6).map((row) => {
              const sorted = [...row.points].sort((a, b) => toNumber(a.depth) - toNumber(b.depth));
              const top = Math.min(...sorted.map((pt) => toNumber(pt.depth)));
              const bottom = Math.max(...sorted.map((pt) => toNumber(pt.depth)));
              return <div className="wallStrip" key={row.wall.id}><span>{row.wall.panelCode}</span><svg viewBox="0 0 160 28" preserveAspectRatio="none">{sorted.slice(0, 24).map((pt, idx) => { const x = ((toNumber(pt.depth) - top) / Math.max(0.01, bottom - top)) * 152 + 4; const v = metricValue(pt, metric); return <rect key={idx} x={x - 3} y="4" width="6" height="20" fill={heatColor(v / maxValue)} />; })}</svg><strong>{row.value.toFixed(metric === 'displacement' ? 1 : 0)}</strong></div>;
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

function InternalForceVisualization({ latest, highlightLocator }: { latest: CalculationResult; highlightLocator?: Record<string, unknown> }) {
  const diagram = latest.reportDiagramData ?? {};
  const wallSamples = ((diagram.wallForceSamples as any[]) ?? latest.stageResults.map((r) => r.wallInternalForce).filter(Boolean) ?? []) as any[];
  const wall = wallSamples.find((item) => item?.points?.length) ?? wallSamples[0];
  const wallRows = ((wall?.points ?? []) as Record<string, unknown>[]).map((p) => ({ ...p, absMoment: Math.abs(toNumber((p as any).moment)), absShear: Math.abs(toNumber((p as any).shear)), displacementValue: Math.abs(toNumber((p as any).displacement)) }));
  const wales = ((diagram.waleEnvelopes as any[]) ?? []) as any[];
  const wale = wales.find((item) => item?.points?.length) ?? wales[0];
  const waleRows = ((wale?.points ?? []) as Record<string, unknown>[]).map((p) => ({ ...p, momentPositive: toNumber((p as any).maxPositiveMoment), momentNegative: -Math.abs(toNumber((p as any).maxNegativeMoment)), shearAbs: toNumber((p as any).maxAbsShear), deflectionAbs: toNumber((p as any).maxAbsDeflection) }));
  const supportRows = ((diagram.supportAxialSummary as any[]) ?? latest.stageResults.flatMap((r) => r.supportForces ?? []) ?? []) as Record<string, unknown>[];
  if (!wallRows.length && !waleRows.length && !supportRows.length) return null;
  return (
    <section className="envelopeVisualization">
      <div className="sectionLead"><h4>关键部件内力包络</h4></div>
      <div className="envelopeChartGrid">
        <MiniLineChart title={`围护墙包络 ${wall?.segmentId ?? ''}`} xLabel="深度 m" yLabel="内力 / 位移" rows={wallRows} xKey="depth" series={[{ key: 'absMoment', label: '|M| kN·m/m', className: 'moment' }, { key: 'absShear', label: '|V| kN/m', className: 'shear' }, { key: 'displacementValue', label: '|δ| mm', className: 'deflection' }]} />
        <MiniLineChart title={`围檩包络 ${wale?.waleBeamCode ?? ''}`} xLabel="里程 m" yLabel="内力 / 挠度" rows={waleRows} xKey="chainage" series={[{ key: 'momentPositive', label: 'M+ kN·m', className: 'moment' }, { key: 'momentNegative', label: 'M- kN·m', className: 'momentNeg' }, { key: 'shearAbs', label: '|V| kN', className: 'shear' }, { key: 'deflectionAbs', label: '|δ|', className: 'deflection' }]} />
        <SupportAxialBarChart rows={supportRows} highlightLocator={highlightLocator} />
      </div>
    </section>
  );
}

function candidateDifferenceLabel(candidate: SupportLayoutOptimizationCandidate) {
  const score = Number(candidate.variableSummary?.geometryDifferenceScore ?? 0);
  const moved = Number(candidate.variableSummary?.adjustedLineCount ?? 0);
  if (candidate.rank === 1 && score <= 0) return '基准';
  if (score >= 0.18 || moved >= 8) return '明显差异';
  if (score >= 0.08 || moved >= 3) return '中等差异';
  return '高度相似';
}

function CandidateDiversityNotice({ candidates }: { candidates: SupportLayoutOptimizationCandidate[] }) {
  if (candidates.length <= 1) return <div className="warning">当前几何约束下只形成 1 个可区分候选方案；系统已隐藏重复方案，不再要求用户在相同布置之间选择。</div>;
  const structural = new Set(candidates.map((c) => `${c.supportCount}-${c.columnCount}-${c.maxBaySpacing}-${c.maxSpanLength}`)).size;
  const weak = candidates.filter((c) => candidateDifferenceLabel(c) === '高度相似').length;
  if (structural <= 1 || weak >= Math.max(2, candidates.length - 1)) {
    return <div className="warning">当前候选方案在支撑数量、立柱数量和最大分仓上仍接近。系统会优先推荐结构路径不同的方案；若 A/B/C 完整计算结果完全相同，说明这些候选只属于线位微调，不应作为正式方案比选依据。</div>;
  }
  return <div className="success">当前候选已按支撑数量、立柱数量、分仓间距和线位几何进行去重，A/B/C 比选优先展示结构路径可区分的方案。</div>;
}

function RadarBar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100;
  return <div className="radarBar"><span>{label}</span><div><em style={{ width: `${pct}%` }} /></div><strong>{pct.toFixed(0)}</strong></div>;
}

function CandidatePlanSvg({ candidate, selected = false, onClick }: { candidate: SupportLayoutOptimizationCandidate; selected?: boolean; onClick?: () => void }) {
  const geom = (candidate.planGeometry ?? {}) as Record<string, any>;
  const outline = (geom.outline ?? []) as { x: number; y: number }[];
  const supports = (geom.supports ?? []) as Record<string, any>[];
  const columns = (geom.columns ?? []) as Record<string, any>[];
  const obstacles = (geom.obstacles ?? []) as Record<string, any>[];
  const adjustments = (candidate.lineAdjustments ?? []) as Record<string, any>[];
  const adjustmentPts = adjustments.flatMap((a) => [a.before?.start, a.before?.end, a.after?.start, a.after?.end]).filter(Boolean) as { x: number; y: number }[];
  const xs = [...outline.map((p) => Number(p.x)), ...supports.flatMap((s) => [Number(s.start?.x), Number(s.end?.x)]), ...columns.map((c) => Number(c.location?.x)), ...adjustmentPts.map((p) => Number(p.x))].filter(Number.isFinite);
  const ys = [...outline.map((p) => Number(p.y)), ...supports.flatMap((s) => [Number(s.start?.y), Number(s.end?.y)]), ...columns.map((c) => Number(c.location?.y)), ...adjustmentPts.map((p) => Number(p.y))].filter(Number.isFinite);
  const minX = Math.min(...xs, 0);
  const maxX = Math.max(...xs, 100);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 100);
  const pad = Math.max(2, Math.max(maxX - minX, maxY - minY) * 0.08);
  const viewBox = `${minX - pad} ${minY - pad} ${Math.max(1, maxX - minX + pad * 2)} ${Math.max(1, maxY - minY + pad * 2)}`;
  const outlinePts = outline.map((p) => `${p.x},${p.y}`).join(' ');
  const motionKey = `${candidate.id ?? candidate.rank}-${selected ? 'motion' : 'static'}`;
  return (
    <button type="button" className={`candidatePlan ${selected ? 'selected' : ''}`} onClick={onClick}>
      <div className="candidatePlanHeader"><strong>方案 {candidate.rank}</strong><span>{candidate.score} 分</span></div>
      <svg key={motionKey} viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
        {outlinePts && <polygon points={outlinePts} className="candidateOutline" />}
        {obstacles.map((obs, idx) => {
          const pts = ((obs.points ?? []) as { x: number; y: number }[]).map((p) => `${p.x},${p.y}`).join(' ');
          return pts ? <polygon key={`obs-${idx}`} points={pts} className="candidateObstacle" /> : null;
        })}
        {selected && adjustments.slice(0, 24).map((a, idx) => {
          const before = a.before ?? {};
          const after = a.after ?? {};
          if (!before.start || !before.end || !after.start || !after.end) return null;
          return <g key={`motion-${idx}`}>
            <line x1={before.start.x ?? 0} y1={before.start.y ?? 0} x2={before.end.x ?? 0} y2={before.end.y ?? 0} className="candidateSupportBefore" />
            <line x1={before.start.x ?? 0} y1={before.start.y ?? 0} x2={before.end.x ?? 0} y2={before.end.y ?? 0} className="candidateSupportMoving">
              <animate attributeName="x1" from={before.start.x ?? 0} to={after.start.x ?? 0} dur="1.1s" fill="freeze" />
              <animate attributeName="y1" from={before.start.y ?? 0} to={after.start.y ?? 0} dur="1.1s" fill="freeze" />
              <animate attributeName="x2" from={before.end.x ?? 0} to={after.end.x ?? 0} dur="1.1s" fill="freeze" />
              <animate attributeName="y2" from={before.end.y ?? 0} to={after.end.y ?? 0} dur="1.1s" fill="freeze" />
            </line>
          </g>;
        })}
        {supports.map((s, idx) => {
          const changed = Boolean(s.changed);
          const lockState = (s.lockState ?? {}) as Record<string, unknown>;
          const locked = Boolean(s.locked || lockState.line || lockState.start || lockState.end);
          return <line key={`${s.id ?? idx}`} x1={s.start?.x ?? 0} y1={s.start?.y ?? 0} x2={s.end?.x ?? 0} y2={s.end?.y ?? 0} className={`candidateSupport ${changed ? 'changed' : ''} ${locked ? 'locked' : ''}`} />;
        })}
        {columns.map((c, idx) => <rect key={`col-${idx}`} x={(c.location?.x ?? 0) - 0.5} y={(c.location?.y ?? 0) - 0.5} width="1" height="1" className="candidateColumn" />)}
      </svg>
      <p className="small">{String(candidate.variableSummary?.positionPattern ?? '-')} · 位移线 {String((candidate.deltaGeometry?.changedSupportCount as number | string | undefined) ?? adjustments.length ?? 0)} · 交叉 {candidate.crossingCount ?? 0} · 障碍 {candidate.obstacleConflictCount ?? 0}</p>
    </button>
  );
}




function schemeLetter(rank: number) {
  return String.fromCharCode(64 + Math.max(1, Math.min(26, rank || 1)));
}

function candidateSchemeName(candidate: SupportLayoutOptimizationCandidate) {
  return String(candidate.variableSummary?.schemeLabel ?? candidate.variableSummary?.topologyFamily ?? `方案 ${schemeLetter(candidate.rank)}`);
}

function CandidateScheme3D({ candidate, selected = false, onClick, fullCalculation }: { candidate: SupportLayoutOptimizationCandidate; selected?: boolean; onClick?: () => void; fullCalculation?: Record<string, unknown> }) {
  const geom = (candidate.planGeometry ?? {}) as Record<string, any>;
  const outline = (geom.outline ?? []) as { x: number; y: number }[];
  const supports = (geom.supports ?? []) as Record<string, any>[];
  const columns = (geom.columns ?? []) as Record<string, any>[];
  const allX = [...outline.map((p) => Number(p.x)), ...supports.flatMap((item) => [Number(item.start?.x), Number(item.end?.x)])].filter(Number.isFinite);
  const allY = [...outline.map((p) => Number(p.y)), ...supports.flatMap((item) => [Number(item.start?.y), Number(item.end?.y)])].filter(Number.isFinite);
  const minX = Math.min(...allX, 0); const maxX = Math.max(...allX, 1);
  const minY = Math.min(...allY, 0); const maxY = Math.max(...allY, 1);
  const cx = (minX + maxX) / 2; const cy = (minY + maxY) / 2;
  const planSpan = Math.max(1, maxX - minX, maxY - minY);
  const elevations = supports.map((item) => Number(item.elevation ?? 0)).filter(Number.isFinite);
  const topZ = Math.max(...elevations, 0); const bottomZ = Math.min(...elevations, -1);
  const zSpan = Math.max(1, topZ - bottomZ);
  const project = (x: number, y: number, z = 0) => ({
    x: 160 + ((x - cx) - (y - cy)) * (112 / planSpan),
    y: 118 + ((x - cx) + (y - cy)) * (46 / planSpan) - (z - bottomZ) * (72 / zSpan),
  });
  const outlineProjected = outline.map((point) => project(point.x, point.y, topZ)).map((point) => `${point.x},${point.y}`).join(' ');
  const family = String(candidate.variableSummary?.topologyFamily ?? 'direct_grid');
  const schemeName = candidateSchemeName(candidate);
  const letter = schemeLetter(candidate.rank);
  const decisionScore = fullCalculation?.decisionScore;
  const recommended = Boolean(fullCalculation?.recommendedByFullCalculation);
  return (
    <button type="button" className={`candidateScheme3d ${selected ? 'selected' : ''} ${recommended ? 'recommended' : ''}`} onClick={onClick} aria-pressed={selected}>
      <div className="candidatePlanHeader"><strong>方案 {letter} · {schemeName}</strong><span>{decisionScore != null ? `决策 ${String(decisionScore)} 分` : `预筛 ${candidate.score} 分`}</span></div>
      {recommended && <div className="schemeRecommendationBadge">完整计算推荐</div>}
      <svg viewBox="0 0 320 220" role="img" aria-label={`方案 ${letter} 三维支撑模型`}>
        <defs><linearGradient id={`pit-${candidate.id ?? candidate.rank}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#dbeafe" stopOpacity=".9"/><stop offset="1" stopColor="#eff6ff" stopOpacity=".35"/></linearGradient></defs>
        {outlineProjected && <polygon points={outlineProjected} fill={`url(#pit-${candidate.id ?? candidate.rank})`} stroke="#475569" strokeWidth="1.5" />}
        {supports.map((item, index) => {
          const a = project(Number(item.start?.x ?? 0), Number(item.start?.y ?? 0), Number(item.elevation ?? bottomZ));
          const b = project(Number(item.end?.x ?? 0), Number(item.end?.y ?? 0), Number(item.elevation ?? bottomZ));
          const role = String(item.role ?? item.supportRole ?? 'main_strut');
          return <line key={`${item.id ?? index}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`scheme3dSupport ${role}`} />;
        })}
        {columns.map((item, index) => {
          const top = project(Number(item.location?.x ?? 0), Number(item.location?.y ?? 0), topZ);
          const bottom = project(Number(item.location?.x ?? 0), Number(item.location?.y ?? 0), bottomZ);
          return <line key={`column-${index}`} x1={top.x} y1={top.y} x2={bottom.x} y2={bottom.y} className="scheme3dColumn" />;
        })}
      </svg>
      <div className="schemeMetricStrip"><span>{candidate.supportCount} 支撑</span><span>{candidate.columnCount} 立柱</span><span>最长 {candidate.maxSpanLength ?? '-'}m</span><span>{family === 'hybrid_diagonal' ? '斜撑混合' : family === 'bidirectional_grid' ? '双向网格' : '直对撑'}</span></div>
      <div className="schemeOutcomeRow"><span>轴力 {String(fullCalculation?.maxSupportAxialForce ?? candidate.axialPeakProxy ?? '-')}</span><span>位移 {String(fullCalculation?.maxDisplacement ?? '-')}</span><span className={Number(fullCalculation?.failCount ?? candidate.failCount ?? 0) > 0 ? 'bad' : 'good'}>Fail {String(fullCalculation?.failCount ?? candidate.failCount ?? 0)}</span></div>
      {Boolean(fullCalculation?.decisionReason) && <p className="schemeDecisionReason">{String(fullCalculation?.decisionReason)}</p>}
    </button>
  );
}

function statusText(status?: string) {
  const map: Record<string, string> = {
    applied_pending_recalculation: '已采纳，待复算',
    candidate_ready: '可优化',
    manual_review_required: '需复核',
    closed_after_recalculation: '已闭环',
    analysis_complete: '已分析',
  };
  return map[status ?? ''] ?? (status ?? '-');
}

function redundancyFaceStatusText(status?: string) {
  const map: Record<string, string> = {
    fail: '不满足',
    near_limit: '接近下限',
    target: '目标带内',
    conservative: '偏保守',
    over_redundant: '严重冗余',
    manual_review: '需复核',
  };
  return map[status ?? ''] ?? (status ?? '-');
}

function ExpertDesignPanel({ project, runStep }: { project: Project; runStep?: (label: string, step: () => Promise<unknown>) => Promise<void> }) {
  const [mode, setMode] = useState('balanced');
  const [data, setData] = useState<Record<string, any> | undefined>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const latestId = project.calculationResults[project.calculationResults.length - 1]?.id ?? 'none';
  const load = () => {
    setLoading(true); setError(undefined);
    api.getExpertDesignReview(project.id, mode)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { if (project.retainingSystem?.diaphragmWalls?.length) load(); }, [project.id, latestId, mode, project.retainingSystem?.diaphragmWalls?.length]);
  if (!project.retainingSystem?.diaphragmWalls?.length) return null;
  const support = (data?.supportSystem ?? {}) as Record<string, any>;
  const rebar = (data?.wallReinforcement ?? {}) as Record<string, any>;
  const vertical = (data?.wallVerticalLength ?? {}) as Record<string, any>;
  const candidates = (vertical.candidates ?? []) as Record<string, any>[];
  const apply = async (candidateId: string) => {
    const action = async () => { await api.applyExpertVerticalWallLength(project.id, candidateId, mode, true); load(); };
    return runStep ? runStep('正在应用围护墙竖向长度方案并重新计算', action) : action();
  };
  return <section className="expertDesignPanel">
    <div className="sectionLead">
      <div><h4>设计院专家式联合设计</h4><p className="small">支撑体系、施工阶段、墙体双向配筋、墙趾长度和施工可实施性统一审查；候选几何代理不替代完整计算。</p></div>
      <div className="segmentedControls"><button className={mode === 'conservative' ? 'active' : ''} onClick={() => setMode('conservative')}>保守</button><button className={mode === 'balanced' ? 'active' : ''} onClick={() => setMode('balanced')}>均衡</button><button className={mode === 'economic' ? 'active' : ''} onClick={() => setMode('economic')}>经济</button></div>
    </div>
    {loading && <div className="infoBox">正在执行支撑—配筋—墙趾联合审查…</div>}
    {error && <div className="error">{error}</div>}
    {data && <>
      <div className="expertDesignStatusGrid">
        <div><span>支撑体系</span><strong className={`status-${String(support.status)}`}>{String(support.status ?? '未评估')}</strong><em>{String(support.rationale ?? '')}</em></div>
        <div><span>墙体配筋</span><strong className={`status-${String(rebar.status)}`}>{String(rebar.status ?? '未评估')}</strong><em>长墙 {String(rebar.longWallCount ?? 0)} 面；密度异常 {String(rebar.sparseLongWallCount ?? 0)} 面</em></div>
        <div><span>竖向墙长</span><strong className={`status-${String(vertical.status)}`}>{String(vertical.status ?? '未评估')}</strong><em>墙趾候选 {String(candidates.length)} 个；导入/人工锁定墙不自动缩短</em></div>
      </div>
      <div className="expertRuleStrip"><strong>推荐体系：{String(support.preferredTopology ?? '-')}</strong><span>墙体沿深度和沿平面双向分区；转角区、支撑节点区、坑底转换区和墙趾区分别表达附加筋。</span></div>
      {candidates.length > 0 && <div className="wallLengthCandidateGrid">{candidates.map((candidate) => <div className="candidateCard wallLengthCandidate" key={String(candidate.candidateId)}><h5>{String(candidate.label)}</h5><div className="metricGrid compact"><div><strong>{String(candidate.zoneCount)}</strong><span>墙趾分区</span></div><div><strong>{String(candidate.optimizedConcreteVolumeM3)} m³</strong><span>估算混凝土</span></div><div><strong>{String(candidate.estimatedConcreteSavingM3)} m³</strong><span>估算节省</span></div><div><strong>{String(candidate.minimumScreeningFactor)}</strong><span>最小筛查系数</span></div></div><p className="small">状态：{String(candidate.status)}；施工复杂度罚值 {String(candidate.constructabilityPenalty ?? 0)}。分区墙趾必须在墙幅接头或转角处过渡。</p>{candidate.status === 'candidate' && <button onClick={() => void apply(String(candidate.candidateId))}>采用并重新计算</button>}</div>)}</div>}
    </>}
  </section>;
}

function WallLengthRedundancyPanel({ project, runStep, runTask }: { project: Project; runStep?: (label: string, step: () => Promise<unknown>) => Promise<void>; runTask?: (title: string, operationName: 'export_wall_length_redundancy' | 'calculation_full', payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void> }) {
  const [mode, setMode] = useState('balanced');
  const [data, setData] = useState<Record<string, any> | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const latestId = project.calculationResults[project.calculationResults.length - 1]?.id ?? 'none';
  const load = () => {
    setLoading(true);
    setError(undefined);
    api.getWallLengthRedundancy(project.id, mode)
      .then((result) => setData(result as Record<string, any>))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { if (project.retainingSystem?.diaphragmWalls?.length) load(); }, [project.id, latestId, mode, project.retainingSystem?.diaphragmWalls?.length, project.retainingSystem?.layoutSummary?.wallLengthOptimizationRecomputeRequired]);
  if (!project.retainingSystem?.diaphragmWalls?.length) return null;
  const faces = (data?.faces ?? []) as Record<string, any>[];
  const candidates = (data?.candidates ?? []) as Record<string, any>[];
  const suggestions = (data?.issueSuggestions ?? []) as Record<string, any>[];
  const band = data?.targetBand as Record<string, unknown> | undefined;
  const thickness = data?.uniformThickness as Record<string, unknown> | undefined;
  const closed = (data?.closedLoopStatus ?? {}) as Record<string, any>;
  const history = ((data?.historySummary?.latest ? [data.historySummary.latest] : []) as Record<string, any>[]);
  const statusClass = closed.severity === 'pass' ? 'success' : closed.severity === 'fail' ? 'error' : closed.severity === 'warning' ? 'warning' : 'infoBox';
  const applyCandidate = (candidateId: string) => {
    const action = () => api.applyWallLengthCandidate(project.id, candidateId, mode).then(() => load());
    return runStep ? runStep('正在写入围护墙设计长度优化建议', action) : action();
  };
  const applyAndRecompute = (candidateId: string) => {
    const action = async () => {
      await api.applyWallLengthCandidate(project.id, candidateId, mode);
      await api.runCalculation(project.id);
      await load();
    };
    return runStep ? runStep('正在采纳设计长度建议并重新计算', action) : action();
  };
  const exportReport = () => {
    if (runTask) return runTask('正在导出围护墙设计长度冗余优化报告', 'export_wall_length_redundancy', { mode }, true);
    window.open(api.wallLengthRedundancyReportUrl(project.id, mode), '_blank');
    return Promise.resolve();
  };
  return (
    <section className="wallLengthOptimizationPanel">
      <div className="sectionLead">
        <div>
          <h4>围护墙平面设计段、分幅与冗余均衡</h4>
          <p className="small">本模块优化平面设计段和槽段分幅；竖向墙长/墙趾标高由上方专家联合设计模块单独控制。</p>
        </div>
        <div className="segmentedControls"><button className={mode === 'conservative' ? 'active' : ''} onClick={() => setMode('conservative')}>保守</button><button className={mode === 'balanced' ? 'active' : ''} onClick={() => setMode('balanced')}>均衡</button><button className={mode === 'economic' ? 'active' : ''} onClick={() => setMode('economic')}>经济</button></div>
      </div>
      {error && <div className="error">{error}</div>}
      {loading && <div className="operationPanel compactOperation"><div className="operationHeader"><strong>正在分析设计长度冗余</strong><span>读取计算追溯链、墙段分组和设计面长度。</span></div><div className="operationBar"><em style={{ width: '54%' }} /></div></div>}
      {data && <>
        <div className={`redundancyClosedLoop ${statusClass}`}><strong>{statusText(String(closed.status ?? ''))}</strong><span>{String(closed.message ?? '')}</span><em>{String(closed.nextAction ?? '')}</em></div>
        <div className="metricGrid compact">
          <div><strong>{String(thickness?.value ?? '-')} m</strong><span>项目统一墙厚</span></div>
          <div><strong>{String(band?.low ?? '-')}–{String(band?.high ?? '-')}</strong><span>目标冗余带 R</span></div>
          <div><strong>{String(data.summary?.faceCount ?? '-')}</strong><span>设计面</span></div>
          <div><strong>{String(data.summary?.overRedundantFaceCount ?? 0)}</strong><span>严重冗余面</span></div>
          <div><strong>{String(data.summary?.nearLimitFaceCount ?? 0)}</strong><span>接近下限面</span></div>
          <div><strong>{String(data.summary?.repairActionCount ?? 0)}</strong><span>修复建议</span></div>
        </div>
        {thickness?.isUniform === false && <div className="warning">当前墙厚存在多个取值：{String((thickness?.allThicknesses as unknown[])?.join(' / '))}；建议先统一项目墙厚策略。</div>}
        {suggestions.length > 0 && <div className="repairSuggestionList"><h5>冗余修复建议</h5>{suggestions.slice(0, 5).map((item) => <div className={`repairSuggestion ${String(item.severity)}`} key={String(item.id)}><strong>{String(item.title ?? item.faceCode)}</strong><span>{String(item.message ?? '')}</span><em>{String(item.recommendation ?? '')}</em></div>)}</div>}
        <table className="table compactTable redundancyTable"><thead><tr><th>设计面</th><th>当前设计长度</th><th>槽段均长</th><th>Rmin</th><th>Rmax</th><th>状态</th><th>推荐动作</th></tr></thead><tbody>
          {faces.map((face) => <tr key={String(face.faceCode)}><td>{String(face.faceCode)}</td><td>{Number(face.physicalLength ?? 0).toFixed(2)} m</td><td>{Number(face.currentPanelLength ?? 0).toFixed(2)} m</td><td>{String(face.rMin ?? '-')}</td><td>{String(face.rMax ?? '-')}</td><td><span className={`redundancyStatus ${String(face.status)}`}>{redundancyFaceStatusText(String(face.status ?? '-'))}</span></td><td>{String(face.recommendation?.reason ?? '-')}</td></tr>)}
        </tbody></table>
        <div className="wallLengthCandidateGrid">
          {candidates.map((candidate) => <div className="candidateCard wallLengthCandidate" key={String(candidate.candidateId)}><h5>{String(candidate.faceCode)} · {redundancyFaceStatusText(String(candidate.action)) || String(candidate.action)}</h5><p className="small">{String(candidate.reason)}</p><div className="metricGrid compact"><div><strong>{String(candidate.before?.designLength ?? '-')} m</strong><span>原设计面长度</span></div><div><strong>{String(candidate.after?.designSectionLength ?? '-')} m</strong><span>推荐设计段</span></div><div><strong>{String(candidate.after?.panelLength ?? '-')} m</strong><span>分幅长度</span></div><div><strong>{String(candidate.after?.localStrengtheningLength ?? '-')} m</strong><span>局部加强段</span></div><div><strong>{String(candidate.after?.estimatedRMax ?? '-')}</strong><span>估算 Rmax</span></div></div>
            {Array.isArray(candidate.repairActions) && candidate.repairActions.length > 0 && <ul className="small repairActionBullets">{candidate.repairActions.slice(0, 3).map((action: Record<string, unknown>) => <li key={String(action.actionId)}><strong>{String(action.label)}</strong>：{String(action.description)}</li>)}</ul>}
            {candidate.status === 'candidate' && <div className="buttonRow"><button onClick={() => applyCandidate(String(candidate.candidateId))}>采纳长度建议</button><button className="secondary" onClick={() => applyAndRecompute(String(candidate.candidateId))}>采纳并重新计算</button></div>}
          </div>)}
        </div>
        <div className="wallLengthHistory"><div><strong>优化历史</strong><span>{String(data.historySummary?.count ?? 0)} 次；{data.historySummary?.recomputeRequired ? '最近一次待复算' : '无待复算项'}</span></div>{history.map((item) => <p className="small" key={String(item.appliedAt)}>{String(item.appliedAt)} · {String(item.candidateId)} · 设计面 {String((item.changedFaces ?? []).join('、'))}</p>)}<button className="secondary" onClick={exportReport}>导出冗余优化记录</button></div>
      </>}
    </section>
  );
}

function CheckSummaryPills({ summary }: { summary?: Record<string, unknown> }) {
  const pass = Number(summary?.pass ?? 0);
  const fail = Number(summary?.fail ?? 0);
  const warning = Number(summary?.warning ?? 0);
  const manual = Number(summary?.manualReview ?? summary?.manual_review ?? 0);
  return <div className="checkSummary"><span className="checkTag pass">合规 {pass}</span><span className="checkTag fail">不合规 {fail}</span><span className="checkTag warning">预警 {warning}</span><span className="checkTag manual_review">复核 {manual}</span></div>;
}

export default function ResultViewer({ project, runStep, runTask, highlightLocator, density = 'professional' }: { project: Project; runStep?: (label: string, step: () => Promise<unknown>) => Promise<void>; runTask?: (title: string, operationName: 'export_wall_length_redundancy' | 'calculation_full', payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; highlightLocator?: Record<string, unknown>; density?: 'compact' | 'professional' }) {
  const calculationState = (project.advancedEngineering?.calculationState ?? {}) as Record<string, unknown>;
  const requiresRecalculation = Boolean(calculationState.requiresRecalculation);
  const latest = requiresRecalculation || !project.calculationResults.length
    ? undefined
    : project.calculationResults[project.calculationResults.length - 1];
  const checks = latest?.checks ?? [];
  const candidates = (project.retainingSystem?.supportLayoutRepair?.candidates ?? latest?.supportLayoutRepair?.candidates ?? []).slice(0, 5);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | undefined>(latest?.supportLayoutRepair?.selectedCandidateId ?? latest?.supportLayoutRepair?.bestCandidateId ?? candidates[0]?.id);
  const selectedCandidate = useMemo(() => candidates.find((c) => c.id === selectedCandidateId) ?? candidates[0], [candidates, selectedCandidateId]);
  if (density === 'compact') {
    return <div className="viewer compactResultViewer">
      <div className="focusSectionHeader"><div><span className="sectionKicker">计算核心成果</span><h3>计算结果与规范复核</h3><p>专注模式仅展示控制指标和出图闸门。完整矩阵、逐阶段内力和原始台账请切换专业模式。</p></div></div>
      {!latest ? <div className="emptyDecisionState"><strong>{requiresRecalculation ? '原计算结果已失效' : '尚未运行计算'}</strong><p>{requiresRecalculation ? `原因：${String(calculationState.reason ?? '围护几何或支撑拓扑已变化')}。请重建施工工况并重新计算。` : '完成围护结构后执行“一键计算校核”。'}</p></div> : <>
        <div className="resultCards focusResultCards">
          <div><strong>{formatEngineeringValue(latest.governingValues.maxTotalPressure, 'pressure')}</strong><span>最大合成侧压力</span></div>
          <div><strong>{formatEngineeringValue(latest.governingValues.maxSupportAxialForce, 'force')}</strong><span>最大支撑轴力</span></div>
          <div><strong>{formatEngineeringValue(latest.governingValues.maxWallMoment, 'wallMoment')}</strong><span>最大墙体弯矩</span></div>
          <div><strong>{formatEngineeringValue(latest.governingValues.maxDisplacement, 'displacement')}</strong><span>最大墙体位移</span></div>
        </div>
        <div className={latest.governingValues.governingCheckStatus === 'fail' ? 'error' : 'warning'}>{conclusion(latest.governingValues.governingCheckStatus)}</div>
        <div className="compactDecisionGrid">
          <section className="summaryPanel"><h4>校核数量</h4><CheckSummaryPills summary={latest.checkSummary} /></section>
          <section className="summaryPanel"><h4>施工图闸门</h4><div className="metricLine"><span>状态</span><strong>{latest.formalReportGate?.status ?? '未评估'}</strong></div><div className="metricLine"><span>正式发行</span><strong>{latest.formalReportGate?.allowedForOfficialIssue ? '允许' : '暂不允许'}</strong></div><p className="small">{latest.formalReportGate?.headline ?? '完成计算后自动评估。'}</p></section>
        </div>
        <DeferredResultDetails className="focusDetails" summary="查看控制云图与内力包络"><InternalForceVisualization latest={latest} highlightLocator={highlightLocator} /><WallCloud3DViewer project={project} latest={latest} highlightLocator={highlightLocator} /></DeferredResultDetails>
      </>}
    </div>;
  }
  return (
    <div className="viewer">
      <h3>计算结果与规范复核</h3>
      {!latest && <div className={requiresRecalculation ? 'warning' : ''}>{requiresRecalculation ? `原计算结果已失效：${String(calculationState.reason ?? '围护几何或支撑拓扑已变化')}。请重新计算后再查看内力、方案排名和出图闸门。` : '尚未运行计算。'}</div>}
      {latest && (
        <>
          <div className="resultCards">
            <div><strong>{latest.governingValues.maxTotalPressure}</strong><span>最大合成侧向压力 kPa</span></div>
            <div><strong>{latest.governingValues.maxSupportAxialForce}</strong><span>最大支撑轴力 kN</span></div>
            <div><strong>{latest.governingValues.maxWallMoment ?? '-'}</strong><span>最大设计弯矩 kN·m/m</span></div>
            <div><strong>{latest.governingValues.maxWallShear ?? '-'}</strong><span>最大设计剪力 kN/m</span></div>
            <div><strong>{latest.governingValues.maxDisplacement ?? '-'}</strong><span>最大位移 mm</span></div>
          </div>
          <div className={latest.governingValues.governingCheckStatus === 'fail' ? 'error' : 'warning'}>{conclusion(latest.governingValues.governingCheckStatus)}</div>
          <DeferredResultDetails summary="查看内力包络、墙体云图与设计面局部优化">
            <InternalForceVisualization latest={latest} highlightLocator={highlightLocator} />
            <WallCloud3DViewer project={project} latest={latest} highlightLocator={highlightLocator} />
            <ExpertDesignPanel project={project} runStep={runStep} />
            <WallLengthRedundancyPanel project={project} runStep={runStep} runTask={runTask} />
          </DeferredResultDetails>
          <p>专业复核：{latest.professionalReviewRequired ? '需要' : '否'}</p>
          <h4>校核汇总</h4>
          <CheckSummaryPills summary={latest.checkSummary} />
          {latest.formalReportGate && (
            <>
              <h4>计算书正式化检查与出图闸门</h4>
              <div className="metricGrid compact">
                <div><strong>{latest.formalReportGate.status}</strong><span>闸门状态</span></div>
                <div><strong>{latest.formalReportGate.allowedForOfficialIssue ? '允许' : '不允许'}</strong><span>正式出图</span></div>
                <div><strong>{latest.formalReportGate.blockingItems?.length ?? 0}</strong><span>阻断项</span></div>
                <div><strong>{latest.formalReportGate.warningItems?.length ?? 0}</strong><span>警告项</span></div>
                <div><strong>{latest.formalReportGate.missingItems?.length ?? 0}</strong><span>缺项</span></div>
              </div>
              <div className={latest.formalReportGate.status === 'fail' ? 'error' : 'warning'}>{latest.formalReportGate.headline}</div>
              {latest.formalReportGate.checklistSections?.length ? <table className="table compactTable"><thead><tr><th>首页清单项</th><th>状态</th><th>Fail</th><th>Warning</th><th>人工复核</th><th>Pass</th></tr></thead><tbody>
                {latest.formalReportGate.checklistSections.map((section, idx) => {
                  const counts = (section.counts ?? {}) as Record<string, number>;
                  return <tr key={idx}><td>{String(section.title ?? '-')}</td><td>{String(section.status ?? '-')}</td><td>{counts.fail ?? 0}</td><td>{counts.warning ?? 0}</td><td>{counts.manual_review ?? 0}</td><td>{counts.pass ?? 0}</td></tr>;
                })}
              </tbody></table> : null}
            </>
          )}
          {latest.supportLayoutQuality && (
            <>
              <h4>支撑布置合理性评分</h4>
              <div className="metricGrid compact">
                <div><strong>{latest.supportLayoutQuality.score}</strong><span>评分</span></div>
                <div><strong>{latest.supportLayoutQuality.status}</strong><span>状态</span></div>
                <div><strong>{String(latest.supportLayoutQuality.metrics?.mainSupportCount ?? '-')}</strong><span>主对撑数量</span></div>
                <div><strong>{String(latest.supportLayoutQuality.metrics?.maxBaySpacing ?? '-')}</strong><span>最大分仓间距</span></div>
                <div><strong>{String(latest.supportLayoutQuality.metrics?.maxSpanLength ?? '-')}</strong><span>最大跨长</span></div>
              </div>
              <p className="small">{latest.supportLayoutQuality.summary}</p>
              {candidates.length ? (
                <>
                  <h4>整体支撑方案 A/B/C 比选</h4>
                  <CandidateDiversityNotice candidates={candidates.slice(0, 3)} />
                  <p className="small">每个卡片代表一套完整支撑体系。先比较三维传力路径、支撑跨度、立柱数量和完整计算指标，再整体采用；单墙长度微调已收纳到高级详情。</p>
                  {(() => {
                    const fullRows = ((latest.supportLayoutRepair?.candidateFullCalculations ?? (latest.reportDiagramData?.candidateFullCalculationComparison as any[]) ?? []) as Record<string, unknown>[]);
                    const byId = new Map(fullRows.map((row) => [String(row.candidateId ?? ''), row]));
                    return <>
                      <div className="candidateSchemeGrid">
                        {candidates.slice(0, 3).map((candidate) => <CandidateScheme3D key={candidate.id ?? candidate.rank} candidate={candidate} fullCalculation={byId.get(String(candidate.id ?? '')) ?? candidate.fullCalculation} selected={(selectedCandidate?.id ?? selectedCandidateId) === candidate.id} onClick={() => setSelectedCandidateId(candidate.id)} />)}
                      </div>
                      {selectedCandidate && <div className="candidateSelectionPanel schemeSelectionPanel"><div><strong>当前选中：方案 {schemeLetter(selectedCandidate.rank)} · {candidateSchemeName(selectedCandidate)}</strong><p className="small">{selectedCandidate.constructabilityNote}</p><div className="schemeSelectedMetrics"><span>目标分仓 {selectedCandidate.targetSpacing}m</span><span>立柱服务跨 {selectedCandidate.columnMaxSpan}m</span><span>最大支撑跨 {selectedCandidate.maxSpanLength ?? '-'}m</span><span>硬约束 {Boolean(selectedCandidate.hardConstraints?.passed) ? '满足' : '未满足'}</span></div></div>{runStep && selectedCandidate.id && <button onClick={() => runStep('正在整体采用支撑优化方案', () => api.adoptSupportCandidate(project.id, selectedCandidate.id!))}>整体采用方案 {schemeLetter(selectedCandidate.rank)}</button>}</div>}
                      {fullRows.length ? <table className="table compactTable schemeComparisonTable"><thead><tr><th>整体方案</th><th>完整排名/得分</th><th>体系</th><th>支撑/立柱</th><th>最长跨/超长直撑</th><th>最大轴力</th><th>最大位移</th><th>围檩弯矩</th><th>Fail/Warning</th><th>正式闸门</th></tr></thead><tbody>{fullRows.slice(0, 3).map((item, index) => <tr className={item.recommendedByFullCalculation ? 'recommendedSchemeRow' : ''} key={String(item.candidateId ?? index)}><td>方案 {String(item.schemeLabel ?? schemeLetter(index + 1))}{item.recommendedByFullCalculation ? ' · 推荐' : ''}</td><td>{String(item.decisionRank ?? '-')} / {String(item.decisionScore ?? '-')}</td><td>{String(item.schemeName ?? item.topologyFamily ?? '-')}</td><td>{String(item.supportCount ?? '-')} / {String(item.columnCount ?? '-')}</td><td>{String(item.maxSpanLength ?? '-')} / {String(item.excessiveDirectStrutCount ?? 0)}</td><td>{String(item.maxSupportAxialForce ?? '-')}</td><td>{String(item.maxDisplacement ?? '-')}</td><td>{String(item.maxWaleMoment ?? '-')}</td><td>{String(item.failCount ?? 0)} / {String(item.warningCount ?? 0)}</td><td>{item.formalGateAllowed ? '允许' : '不允许'}</td></tr>)}</tbody></table> : <div className="warning">尚未执行 A/B/C 完整并行计算；当前卡片使用代理评分。请运行候选方案完整计算后再正式定案。</div>}
                      <details className="engineeringDetails compactDetails"><summary>查看评分分解、原方案动画和完整候选参数</summary>
                        <div className="candidatePlanGrid">{candidates.slice(0, 3).map((candidate) => <CandidatePlanSvg key={`plan-${candidate.id ?? candidate.rank}`} candidate={candidate} selected={(selectedCandidate?.id ?? selectedCandidateId) === candidate.id} onClick={() => setSelectedCandidateId(candidate.id)} />)}</div>
                        <table className="table compactTable"><thead><tr><th>方案</th><th>评分</th><th>拓扑</th><th>分仓</th><th>立柱跨</th><th>支撑数</th><th>最长跨</th><th>交叉/障碍</th></tr></thead><tbody>{candidates.slice(0, 3).map((candidate) => <tr key={`detail-${candidate.id ?? candidate.rank}`}><td>{schemeLetter(candidate.rank)}</td><td>{candidate.score}</td><td>{candidateSchemeName(candidate)}</td><td>{candidate.targetSpacing}m</td><td>{candidate.columnMaxSpan}m</td><td>{candidate.supportCount}</td><td>{candidate.maxSpanLength ?? '-'}m</td><td>{candidate.crossingCount ?? 0}/{candidate.obstacleConflictCount ?? 0}</td></tr>)}</tbody></table>
                        <div className="candidateCompareGrid">{candidates.slice(0, 3).map((candidate) => <div className={`candidateCard ${(selectedCandidate?.id ?? selectedCandidateId) === candidate.id ? 'selected' : ''}`} key={`card-${candidate.id ?? candidate.rank}`}><h5>方案 {schemeLetter(candidate.rank)} · {candidate.score} 分</h5><div className="radarBars"><RadarBar label="间距" value={Number(candidate.softObjectives?.spacingCloseTo3To6m ?? 0)} /><RadarBar label="短跨" value={Number(candidate.softObjectives?.shortSpanLength ?? 0)} /><RadarBar label="立柱" value={Number(candidate.softObjectives?.reasonableColumnCount ?? 0)} /><RadarBar label="轴力" value={Number(candidate.softObjectives?.lowAxialPeakProxy ?? 0)} /><RadarBar label="出土" value={Number(candidate.softObjectives?.continuousMuckPath ?? 0)} /><RadarBar label="对称" value={Number(candidate.softObjectives?.planSymmetry ?? 0)} /></div></div>)}</div>
                      </details>
                    </>;
                  })()}
                </>
              ) : null}
            </>
          )}
          {latest.ifcCompatibility && (
            <>
              <h4>IFC 兼容性自检</h4>
              <div className="metricGrid compact">
                <div><strong>{latest.ifcCompatibility.score}</strong><span>评分</span></div>
                <div><strong>{latest.ifcCompatibility.status}</strong><span>状态</span></div>
                <div><strong>{String(latest.ifcCompatibility.rawUnicodeFound)}</strong><span>raw unicode</span></div>
                <div><strong>{latest.ifcCompatibility.zeroDimensionCount ?? 0}</strong><span>零尺寸</span></div>
                <div><strong>{latest.ifcCompatibility.missingMaterialAssociationCount ?? 0}</strong><span>材料缺失</span></div>
              </div>
              <p className="small">{latest.ifcCompatibility.summary}</p>
              {latest.ifcCompatibility.viewerProfiles?.length ? <table className="table compactTable"><thead><tr><th>Viewer</th><th>状态</th><th>风险</th><th>评分</th><th>风险项</th><th>建议</th></tr></thead><tbody>
                {latest.ifcCompatibility.viewerProfiles.map((profile) => <tr key={profile.viewer}><td>{profile.viewer}</td><td>{profile.status}</td><td>{profile.riskLevel}</td><td>{profile.score}</td><td>{profile.riskItems?.join('；') || '-'}</td><td>{profile.recommendation ?? '-'}</td></tr>)}
              </tbody></table> : null}
            </>
          )}
          {latest.designReviewSummary && (
            <>
              <h4>强度 / 刚度 / 稳定性复核汇总</h4>
              <div className="metricGrid compact">
                <div><strong>{latest.designReviewSummary.strengthStatus}</strong><span>强度状态</span></div>
                <div><strong>{latest.designReviewSummary.stiffnessStatus}</strong><span>刚度状态</span></div>
                <div><strong>{latest.designReviewSummary.stabilityStatus}</strong><span>稳定性状态</span></div>
                <div><strong>{latest.designReviewSummary.maxStrengthUtilization ?? '-'}</strong><span>最大强度利用率</span></div>
                <div><strong>{latest.designReviewSummary.maxStiffnessUtilization ?? '-'}</strong><span>最大刚度利用率</span></div>
                <div><strong>{latest.designReviewSummary.minStabilitySafetyFactor ?? '-'}</strong><span>最小稳定安全系数</span></div>
              </div>
            </>
          )}

          {latest.optimizationActions?.length ? (
            <>
              <h4>自动优化动作</h4>
              <table className="table"><thead><tr><th>对象</th><th>动作</th><th>数量</th></tr></thead><tbody>
                {latest.optimizationActions.map((item, index) => <tr key={index}><td>{String(item.target ?? '-')}</td><td>{String(item.action ?? '-')}</td><td>{String(item.count ?? '-')}</td></tr>)}
              </tbody></table>
            </>
          ) : null}

          {latest.stageResults.some((r) => r.coupledSystemResult && Object.keys(r.coupledSystemResult).length) && (
            <details className="engineeringDetails">
              <summary>墙—围檩—支撑全局矩阵与换撑刚度明细</summary>
              <p className="small">未进入换撑阶段显示“未激活 / —”；进入换撑阶段后按楼板 EA/L、有效宽度和连接折减计算。应激活但参数缺失会显示“缺失”并进入阻断项。</p>
              <table className="table">
                <thead><tr><th>阶段</th><th>边段</th><th>空间矩阵</th><th>墙平动/转角</th><th>围檩平动/转角</th><th>立柱竖向</th><th>换撑状态</th><th>{withUnitLabel('楼板换撑刚度', 'stiffness')}</th><th>刚度来源</th><th>{withUnitLabel('全局轴力', 'force')}</th><th>说明</th></tr></thead>
                <tbody>{latest.stageResults.filter((r) => r.coupledSystemResult).slice(0, 24).map((r) => <tr key={`${r.stageId}-${r.segmentId}`}>
                  <td>{r.stageId}</td><td>{r.segmentId}</td><td>{String(r.coupledSystemResult?.globalSpatialMatrixSize ?? r.globalCoupledResult?.spatialMatrixSize ?? r.globalCoupledResult?.matrixSize ?? '-')}</td>
                  <td>{String((r.coupledSystemResult?.globalSpatialDofSummary as any)?.wallHorizontal ?? '-')} / {String((r.coupledSystemResult?.globalSpatialDofSummary as any)?.wallRotation ?? '-')}</td>
                  <td>{String((r.coupledSystemResult?.globalSpatialDofSummary as any)?.waleHorizontal ?? '-')} / {String((r.coupledSystemResult?.globalSpatialDofSummary as any)?.waleRotation ?? '-')}</td>
                  <td>{String((r.coupledSystemResult?.globalSpatialDofSummary as any)?.columnVertical ?? r.globalCoupledResult?.columnVerticalDofs?.length ?? '-')}</td>
                  <td>{String(r.coupledSystemResult?.slabReplacementStatus ?? r.globalCoupledResult?.slabReplacementStatus ?? 'not_active') === 'not_active' ? '未激活' : String(r.coupledSystemResult?.slabReplacementStatus ?? r.globalCoupledResult?.slabReplacementStatus ?? '-')}</td>
                  <td>{String(r.coupledSystemResult?.slabReplacementStatus ?? r.globalCoupledResult?.slabReplacementStatus ?? 'not_active') === 'not_active' ? '—' : formatEngineeringValue(r.coupledSystemResult?.slabReplacementStiffness ?? r.globalCoupledResult?.slabReplacementStiffness, 'stiffness')}</td>
                  <td className="small">{String(r.coupledSystemResult?.slabReplacementSource ?? r.globalCoupledResult?.slabReplacementSource ?? '—')}</td>
                  <td>{formatEngineeringValue(r.coupledSystemResult?.globalMaxSupportAxialForce ?? r.globalCoupledResult?.maxSupportAxialForce, 'force')}</td><td className="small">{String(r.coupledSystemResult?.note ?? '')}</td>
                </tr>)}</tbody>
              </table>
            </details>
          )}

          {latest.stageResults.some((r) => r.supportForces.some((f) => (f.distributionMethod?.includes('continuous_wale_beam') || f.distributionMethod?.includes('global')))) && (
            <>
              <h4>围檩连续梁—支撑节点反力</h4>
              <table className="table">
                <thead><tr><th>支撑ID</th><th>墙面</th><th>端点</th><th>围檩里程</th><th>节点反力</th><th>轴力设计值</th><th>弹簧刚度</th><th>有效标准轴力</th><th>模型</th></tr></thead>
                <tbody>
                  {latest.stageResults.flatMap((r) => r.supportForces).filter((f) => (f.distributionMethod?.includes('continuous_wale_beam') || f.distributionMethod?.includes('global'))).slice(0, 24).map((force, idx) => (
                    <tr key={`${force.supportId}-${idx}`}>
                      <td>{force.supportId ?? '-'}</td><td>{force.faceCode ?? '-'}</td><td>{force.supportEndpoint ?? '-'}</td><td>{force.waleChainage ?? '-'}</td>
                      <td>{force.continuousBeamReaction ?? '-'} kN</td><td>{force.axialForceDesign ?? force.axialForce} kN</td><td>{force.elasticSupportStiffness ?? '-'} kN/m</td><td>{force.effectiveAxialForce ?? '-'}</td><td>{force.distributionMethod}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h4>围檩本体内力包络</h4>
              <table className="table">
                <thead><tr><th>围檩</th><th>墙面</th><th>层号</th><th>线荷载</th><th>最大弯矩</th><th>最大剪力</th><th>最大挠度</th><th>节点数</th></tr></thead>
                <tbody>
                  {latest.stageResults.flatMap((r) => r.waleBeamResults ?? []).slice(0, 32).map((wale, idx) => (
                    <tr key={`${wale.waleBeamCode}-${idx}`}>
                      <td>{wale.waleBeamCode}</td><td>{wale.faceCode}</td><td>{wale.levelIndex}</td><td>{wale.pressureLineLoad} kN/m</td><td>{wale.maxMoment} kN·m</td><td>{wale.maxShear} kN</td><td>{wale.maxDeflection}</td><td>{wale.supportNodeCount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h4>围檩多工况弯矩/剪力/挠度包络</h4>
              <table className="table">
                <thead><tr><th>围檩</th><th>墙面</th><th>控制阶段数</th><th>M+</th><th>M-</th><th>|V|max</th><th>|δ|max</th><th>点数</th></tr></thead>
                <tbody>{(latest.reportDiagramData?.waleEnvelopes as any[] | undefined)?.slice(0, 24).map((env, idx) => (
                  <tr key={`${env.waleBeamCode}-${idx}`}><td>{env.waleBeamCode}</td><td>{env.faceCode ?? '-'}</td><td>{env.governingStageIds?.length ?? 0}</td><td>{env.maxPositiveMoment}</td><td>{env.maxNegativeMoment}</td><td>{env.maxAbsShear}</td><td>{env.maxAbsDeflection}</td><td>{env.points?.length ?? 0}</td></tr>
                )) ?? <tr><td colSpan={8}>暂无包络数据</td></tr>}</tbody>
              </table>
            </>
          )}
          {latest.stabilityDetailedResult && (
            <>
              <h4>可审查地下水与稳定专项包</h4>
              <div className="metricGrid compact">
                <div><strong>{latest.stabilityDetailedResult.controllingSectionName ?? latest.stabilityDetailedResult.controllingSectionId ?? '-'}</strong><span>控制剖面</span></div>
                <div><strong>{latest.stabilityDetailedResult.controllingMode ?? '-'}</strong><span>控制模式</span></div>
                <div><strong>{latest.stabilityDetailedResult.minSafetyFactor ?? '-'}</strong><span>最小安全指标</span></div>
                <div><strong>{latest.stabilityDetailedResult.circularSlipSurfaces?.length ?? 0}</strong><span>圆弧候选</span></div>
                <div><strong>{latest.stabilityDetailedResult.seepagePaths?.length ?? 0}</strong><span>渗流路径</span></div>
                <div><strong>{latest.stabilityDetailedResult.dewateringWells?.length ?? 0}</strong><span>降水井建议</span></div>
              </div>
              <table className="table"><thead><tr><th>圆弧</th><th>中心X</th><th>中心标高</th><th>半径</th><th>安全系数</th><th>控制</th></tr></thead><tbody>
                {(latest.stabilityDetailedResult.circularSlipSurfaces ?? []).slice(0, 8).map((item: any) => <tr key={String(item.id)}><td>{String(item.id)}</td><td>{String(item.centerX)}</td><td>{String(item.centerElevation)}</td><td>{String(item.radius)}</td><td>{String(item.safetyFactor)}</td><td>{String(item.governing)}</td></tr>)}
              </tbody></table>
            </>
          )}

          {latest.drawingSheets?.length ? (
            <>
              <h4>施工图级成果表达接口</h4>
              <table className="table"><thead><tr><th>图号</th><th>图名</th><th>比例</th><th>类型</th><th>文件</th></tr></thead><tbody>
                {latest.drawingSheets.map((sheet) => <tr key={sheet.sheetId}><td>{sheet.sheetId}</td><td>{sheet.title}</td><td>{sheet.scale}</td><td>{sheet.sheetType}</td><td className="small">{sheet.filePath}</td></tr>)}
              </tbody></table>
            </>
          ) : null}

          <table className="table">
            <thead><tr><th>规则</th><th>对象</th><th>状态</th><th>计算值</th><th>限值</th><th>说明</th></tr></thead>
            <tbody>
              {checks.slice(0, 40).map((check, index) => <tr key={`${check.ruleId}-${index}`}><td>{check.ruleId}</td><td>{check.objectType}</td><td>{check.status}</td><td>{check.calculatedValue ?? '-'}</td><td>{check.limitValue ?? '-'}</td><td>{check.message}</td></tr>)}
              {checks.length === 0 && <tr><td colSpan={6}>暂无校核结果</td></tr>}
            </tbody>
          </table>
          {latest.warnings.filter((item) => !/V\d|本版本|迭代/.test(item)).map((item) => <div key={item} className="warning">{item}</div>)}
        </>
      )}
    </div>
  );
}
