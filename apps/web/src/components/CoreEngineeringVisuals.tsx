import { useMemo, useState } from 'react';
import type { Project, SupportLayoutOptimizationCandidate, WallInternalForcePoint } from '../types/domain';
import { formatEngineeringValue } from '../utils/units';
import { sanitizeCandidatePlanGeometry } from '../drawing/candidateGeometry';

type StandardRef = {
  id?: string;
  code?: string;
  name?: string;
  level?: string;
  levelLabel?: string;
  focus?: string;
  implementedScope?: string;
  boundary?: string;
  sourceUrl?: string;
};

const CORE_STATUS_LABELS: Record<string, string> = { pass: '通过', warning: '需复核', manual_review: '人工复核', preliminary: '初步结果', fail: '不通过' };
const REBAR_CHECK_LABELS: Record<string, string> = { anchorage: '锚固长度', lap_splice: '搭接长度', mechanical_coupler: '机械连接', rebar_congestion: '钢筋拥挤与净距', support_stirrup_reinforcement: '支撑箍筋', support_longitudinal_ratio: '支撑总纵筋率', support_single_side_longitudinal_ratio: '支撑单侧纵筋率' };
const SCENARIO_LABELS: Record<string, string> = { groundwater_failure: '降水失效', over_excavation: '超挖', temperature: '温差作用', installation_deviation: '安装偏差', confined_water_rise: '承压水位抬升', local_seepage: '局部渗流' };
const PARAMETER_LABELS: Record<string, string> = { groundwaterOffsetM: '坑外水位抬升', overExcavationM: '超挖深度', temperatureDeltaC: '温差', installationDeviationMm: '安装偏差', surchargeKpa: '地面附加荷载' };

function readableEngineeringText(value: unknown): string {
  if (value == null) return '';
  if (typeof value !== 'object') return String(value);
  return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${PARAMETER_LABELS[key] ?? key} ${String(item)}`)
    .join('；');
}

export function CoreStandardGuidance({ standards }: { standards: StandardRef[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!standards?.length) return null;
  return <section className="coreStandards" aria-label="本步骤规范依据">
    <div className="coreStandardsHeader">
      <strong>规范依据</strong>
      <div>{standards.slice(0, 3).map((item) => <span key={String(item.id ?? item.code)}>{item.code}</span>)}</div>
      <button type="button" className="secondary compactButton" onClick={() => setExpanded((value) => !value)}>{expanded ? '收起' : '查看对应关系'}</button>
    </div>
    {expanded ? <div className="coreStandardList">{standards.map((item) => <article key={String(item.id ?? item.code)}>
      <div><strong>{item.code}</strong><span>{item.name}</span></div>
      <p>{item.focus || item.implementedScope}</p>
      {item.boundary ? <small>软件边界：{item.boundary}</small> : null}
      {item.sourceUrl ? <a href={item.sourceUrl} target="_blank" rel="noreferrer">查看官方来源</a> : null}
    </article>)}</div> : null}
  </section>;
}

function numeric(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function boundsFromPoints(points: { x: number; y: number }[]) {
  if (!points.length) return { minX: 0, maxX: 1, minY: 0, maxY: 1, width: 1, height: 1 };
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  return { minX, maxX, minY, maxY, width: Math.max(maxX - minX, 1), height: Math.max(maxY - minY, 1) };
}

export function GeologySectionVisual({ project }: { project: Project }) {
  const data = useMemo(() => {
    const previews = project.geologicalModel?.surfacePreviews?.length
      ? project.geologicalModel.surfacePreviews
      : project.geologicalModel?.surfaces ?? [];
    const sectionSurfaces = previews.slice(0, 10).map((surface) => {
      const grid = surface.grid;
      const rowIndex = Math.max(0, Math.min((grid.zValues?.length ?? 1) - 1, Math.floor((grid.zValues?.length ?? 1) / 2)));
      const row = grid.zValues?.[rowIndex] ?? [];
      const xValues = grid.xValues?.length === row.length ? grid.xValues : row.map((_, index) => index);
      return {
        code: surface.stratumCode,
        type: surface.surfaceType,
        confidence: surface.confidence,
        points: row.map((z, index) => ({ x: Number(xValues[index] ?? index), z: Number(z) })).filter((point) => Number.isFinite(point.x) && Number.isFinite(point.z)),
      };
    }).filter((surface) => surface.points.length > 1);
    const boreholes = (project.boreholes ?? []).map((borehole) => ({ x: borehole.x, top: borehole.collarElevation, bottom: borehole.collarElevation - borehole.depth, code: borehole.code }));
    const all = [
      ...sectionSurfaces.flatMap((surface) => surface.points.map((point) => ({ x: point.x, y: point.z }))),
      ...boreholes.flatMap((borehole) => [{ x: borehole.x, y: borehole.top }, { x: borehole.x, y: borehole.bottom }]),
    ];
    return { sectionSurfaces, boreholes, bounds: boundsFromPoints(all) };
  }, [project]);

  const { minX, maxY, width, height } = data.bounds;
  const padX = Math.max(width * 0.04, 1);
  const padY = Math.max(height * 0.08, 1);
  const viewBox = `${minX - padX} ${-(maxY + padY)} ${width + 2 * padX} ${height + 2 * padY}`;
  const audit = project.geologicalModel?.coverageAudit;

  if (!data.sectionSurfaces.length && !data.boreholes.length) return <div className="coreVisualEmpty">导入钻孔或生成地质模型后显示地层剖面。</div>;
  return <section className="coreVisualModule">
    <header><div><strong>地质模型剖面</strong><span>轻量地层面与钻孔控制范围</span></div><em className={audit?.status ?? 'manual_review'}>{audit?.designDomainCovered ? '覆盖设计域' : audit?.status === 'fail' ? '覆盖不足' : '待复核'}</em></header>
    <svg className="coreEngineeringSvg geology" viewBox={viewBox} preserveAspectRatio="xMidYMid meet" role="img" aria-label="地质模型剖面预览">
      {data.sectionSurfaces.map((surface, index) => <polyline key={`${surface.code}-${surface.type}-${index}`} points={surface.points.map((point) => `${point.x},${-point.z}`).join(' ')} className={`coreGeoSurface surface-${index % 6} ${surface.type}`} vectorEffect="non-scaling-stroke"><title>{surface.code} {surface.type} · 置信度 {surface.confidence}</title></polyline>)}
      {data.boreholes.map((borehole) => <g key={borehole.code} className="coreBorehole"><line x1={borehole.x} x2={borehole.x} y1={-borehole.top} y2={-borehole.bottom} vectorEffect="non-scaling-stroke" /><circle cx={borehole.x} cy={-borehole.top} r={Math.max(width * .003, .25)} vectorEffect="non-scaling-stroke" /><title>{borehole.code}</title></g>)}
    </svg>
    <footer><span>钻孔 {data.boreholes.length}</span><span>预览地层面 {data.sectionSurfaces.length}</span>{audit?.maximumExtrapolationDistanceM != null ? <span>最大外推 {audit.maximumExtrapolationDistanceM.toFixed(1)} m</span> : null}</footer>
  </section>;
}

function planGeometry(project: Project, candidate?: SupportLayoutOptimizationCandidate) {
  const rawGeometry = candidate?.planGeometry ?? {
    outline: project.excavation?.outline.points ?? [],
    supports: project.retainingSystem?.supports ?? [],
    columns: project.retainingSystem?.columns ?? [],
    transferBeams: (project.retainingSystem?.ringBeams ?? []).filter((beam: any) => String(beam.beamRole ?? '').startsWith('transfer_') || ['TR-', 'TF-', 'TB-'].some((prefix) => String(beam.code ?? '').startsWith(prefix))),
    transferZones: [],
  };
  const geometry = sanitizeCandidatePlanGeometry(rawGeometry);
  const walls = project.retainingSystem?.diaphragmWalls ?? [];
  const points = [
    ...geometry.outline,
    ...geometry.supports.flatMap((support) => [support.start, support.end]),
    ...geometry.columns.map((column) => column.location),
    ...geometry.transferBeams.flatMap((beam) => beam.points ?? []),
    ...geometry.transferZones.flatMap((zone) => zone.outline ?? []),
  ];
  return { ...geometry, walls, bounds: boundsFromPoints(points) };
}

export function RetainingPlanVisual({ project, candidate }: { project: Project; candidate?: SupportLayoutOptimizationCandidate }) {
  const geometry = useMemo(() => planGeometry(project, candidate), [project, candidate]);
  if (!geometry.outline.length) return <div className="coreVisualEmpty">录入闭合轮廓后显示围护结构平面。</div>;
  const b = geometry.bounds;
  const pad = Math.max(Math.min(b.width, b.height) * .08, 1);
  const viewBox = `${b.minX - pad} ${-(b.maxY + pad)} ${b.width + 2 * pad} ${b.height + 2 * pad}`;
  const polygon = geometry.outline.map((point) => `${point.x},${-point.y}`).join(' ');
  return <section className="coreVisualModule">
    <header><div><strong>围护结构设计模型</strong><span>{candidate ? `候选方案 ${candidate.rank || ''}` : '当前采用方案'} · 平面传力路径</span></div><em>{geometry.supports.length} 支撑</em></header>
    <svg className="coreEngineeringSvg retaining" viewBox={viewBox} preserveAspectRatio="xMidYMid meet" role="img" aria-label="围护结构设计模型平面图">
      <polygon points={polygon} className="corePitFill" />
      <polyline points={`${polygon} ${geometry.outline[0]?.x},${-geometry.outline[0]?.y}`} className="coreWallLine" vectorEffect="non-scaling-stroke" />
      {geometry.transferZones.map((zone: any, index: number) => {
        const points = (zone.outline ?? []).map((point: any) => `${Number(point.x)},${-Number(point.y)}`).join(' ');
        return points ? <polygon key={`core-transfer-zone-${index}`} points={points} className="coreTransferZone" vectorEffect="non-scaling-stroke"><title>异形闭合转接区</title></polygon> : null;
      })}
      {geometry.transferBeams.map((beam: any, index: number) => {
        const points = (beam.points ?? beam.axis?.points ?? []).filter((point: any) => Number.isFinite(Number(point?.x)) && Number.isFinite(Number(point?.y)));
        if (points.length < 2) return null;
        const role = String(beam.role ?? beam.beamRole ?? 'transfer_ring_beam');
        return <polyline key={String(beam.id ?? index)} points={points.map((point: any) => `${Number(point.x)},${-Number(point.y)}`).join(' ')} className={`coreTransferBeam ${role}`} vectorEffect="non-scaling-stroke"><title>{String(beam.code ?? beam.id ?? '转接构件')}</title></polyline>;
      })}
      {geometry.supports.slice(0, 1200).map((support: any, index: number) => <line key={String(support.id ?? index)} x1={Number(support.start?.x ?? 0)} y1={-Number(support.start?.y ?? 0)} x2={Number(support.end?.x ?? 0)} y2={-Number(support.end?.y ?? 0)} className={String(support.role ?? support.supportRole ?? '').includes('corner') ? 'coreBraceLine' : 'coreSupportLine'} vectorEffect="non-scaling-stroke"><title>{String(support.code ?? support.id ?? `S${index + 1}`)}</title></line>)}
      {geometry.columns.slice(0, 500).map((column: any, index: number) => <circle key={String(column.id ?? index)} cx={Number(column.location?.x ?? 0)} cy={-Number(column.location?.y ?? 0)} r={Math.max(Math.min(b.width, b.height) * .008, .25)} className="coreColumnMark" vectorEffect="non-scaling-stroke"><title>{String(column.code ?? column.id ?? `C${index + 1}`)}</title></circle>)}
    </svg>
    <footer><span>围护墙 {geometry.walls.length}</span><span>支撑 {geometry.supports.length}</span><span>转接构件 {geometry.transferBeams.length}</span><span>立柱 {geometry.columns.length}</span></footer>
  </section>;
}

type EnvelopeMode = 'displacement' | 'moment' | 'shear' | 'pressure';

function collectEnvelope(project: Project, mode: EnvelopeMode): { x: number; y: number }[] {
  const latest = project.calculationResults?.[project.calculationResults.length - 1];
  if (!latest) return [];
  let best: { x: number; y: number }[] = [];
  let amplitude = -1;
  for (const stage of latest.stageResults ?? []) {
    let points: { x: number; y: number }[] = [];
    if (mode === 'pressure') points = (stage.pressureProfile?.points ?? []).map((point) => ({ x: Number(point.totalPressure ?? 0), y: Number(point.elevation ?? -point.depth) }));
    else {
      const wallPoints = stage.wallInternalForce?.points ?? [];
      points = wallPoints.map((point: WallInternalForcePoint) => ({
        x: Number(mode === 'displacement' ? point.displacement ?? 0 : mode === 'moment' ? point.moment : point.shear),
        y: Number(point.elevation ?? -point.depth),
      }));
    }
    const currentAmplitude = Math.max(0, ...points.map((point) => Math.abs(point.x)));
    if (points.length > 1 && currentAmplitude > amplitude) { best = points; amplitude = currentAmplitude; }
  }
  return best;
}

function lineChartPath(points: { x: number; y: number }[], width = 560, height = 260) {
  if (points.length < 2) return { path: '', zeroX: width / 2, minX: 0, maxX: 0, minY: 0, maxY: 0 };
  const xs = points.map((point) => point.x); const ys = points.map((point) => point.y);
  const minX = Math.min(...xs, 0); const maxX = Math.max(...xs, 0);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1e-9); const spanY = Math.max(maxY - minY, 1e-9);
  const px = (x: number) => 46 + ((x - minX) / spanX) * (width - 72);
  const py = (y: number) => 18 + ((maxY - y) / spanY) * (height - 42);
  return { path: points.map((point, index) => `${index ? 'L' : 'M'} ${px(point.x).toFixed(2)} ${py(point.y).toFixed(2)}`).join(' '), zeroX: px(0), minX, maxX, minY, maxY };
}

export function CalculationEnvelopeVisual({ project }: { project: Project }) {
  const [mode, setMode] = useState<EnvelopeMode>('displacement');
  const points = useMemo(() => collectEnvelope(project, mode), [project, mode]);
  const chart = useMemo(() => lineChartPath(points), [points]);
  const labels: Record<EnvelopeMode, { title: string; unit: string }> = {
    displacement: { title: '墙体水平位移', unit: 'mm' },
    moment: { title: '墙体弯矩', unit: 'kN·m/m' },
    shear: { title: '墙体剪力', unit: 'kN/m' },
    pressure: { title: '侧向压力', unit: 'kPa' },
  };
  const value = Math.max(0, ...points.map((point) => Math.abs(point.x)));
  return <section className="coreVisualModule">
    <header><div><strong>内力与变形包络</strong><span>选择控制量查看沿深度分布</span></div><em>{points.length ? `峰值 ${value.toFixed(2)} ${labels[mode].unit}` : '待计算'}</em></header>
    <div className="coreVisualTabs">{(Object.keys(labels) as EnvelopeMode[]).map((key) => <button type="button" key={key} className={mode === key ? 'active' : 'secondary'} onClick={() => setMode(key)}>{labels[key].title}</button>)}</div>
    {chart.path ? <svg className="coreEnvelopeSvg" viewBox="0 0 560 260" role="img" aria-label={`${labels[mode].title}沿深度包络图`}>
      <line x1={chart.zeroX} x2={chart.zeroX} y1="18" y2="236" className="coreChartAxis" />
      <line x1="46" x2="534" y1="236" y2="236" className="coreChartAxis" />
      <path d={chart.path} className="coreEnvelopePath" />
      <text x="46" y="254">{chart.minX.toFixed(1)}</text><text x="490" y="254">{chart.maxX.toFixed(1)} {labels[mode].unit}</text>
      <text x="4" y="28">{chart.maxY.toFixed(1)} m</text><text x="4" y="232">{chart.minY.toFixed(1)} m</text>
    </svg> : <div className="coreVisualEmpty">完成施工阶段计算后显示内力和变形包络。</div>}
  </section>;
}

function forceMap(project: Project) {
  const latest = project.calculationResults?.[project.calculationResults.length - 1];
  const values = new Map<string, number>();
  for (const stage of latest?.stageResults ?? []) {
    for (const force of stage.supportForces ?? []) {
      if (!force.supportId) continue;
      const value = Math.abs(Number(force.axialForceDesign ?? force.effectiveAxialForce ?? force.axialForce ?? 0));
      values.set(force.supportId, Math.max(values.get(force.supportId) ?? 0, value));
    }
  }
  return values;
}

export function SupportForceCloudVisual({ project }: { project: Project }) {
  const forces = useMemo(() => forceMap(project), [project]);
  const system = project.retainingSystem;
  const outline = project.excavation?.outline.points ?? [];
  const points = [...outline, ...(system?.supports ?? []).flatMap((support) => [support.start, support.end])];
  const b = boundsFromPoints(points);
  const pad = Math.max(Math.min(b.width, b.height) * .08, 1);
  const viewBox = `${b.minX - pad} ${-(b.maxY + pad)} ${b.width + 2 * pad} ${b.height + 2 * pad}`;
  const maxForce = Math.max(0, ...forces.values());
  if (!system?.supports?.length) return <div className="coreVisualEmpty">生成围护方案后显示支撑受力分布。</div>;
  return <section className="coreVisualModule">
    <header><div><strong>支撑轴力云图</strong><span>线宽与透明度表示设计轴力相对大小</span></div><em>{maxForce ? formatEngineeringValue(maxForce, 'force') : '待计算'}</em></header>
    <svg className="coreEngineeringSvg forceCloud" viewBox={viewBox} preserveAspectRatio="xMidYMid meet" role="img" aria-label="支撑轴力平面云图">
      {outline.length ? <polygon points={outline.map((point) => `${point.x},${-point.y}`).join(' ')} className="corePitFill" /> : null}
      {system.supports.map((support, index) => {
        const force = forces.get(support.id) ?? Math.abs(Number(support.designAxialForce ?? 0));
        const ratio = maxForce > 0 ? force / maxForce : .15;
        const bin = Math.max(1, Math.min(5, Math.ceil(ratio * 5)));
        return <line key={support.id || index} x1={support.start.x} y1={-support.start.y} x2={support.end.x} y2={-support.end.y} className={`coreForceLine force-${bin}`} vectorEffect="non-scaling-stroke"><title>{support.code}: {force.toFixed(1)} kN</title></line>;
      })}
    </svg>
    <footer><span>低</span><div className="coreForceLegend">{[1, 2, 3, 4, 5].map((bin) => <i key={bin} className={`force-${bin}`} />)}</div><span>高</span></footer>
  </section>;
}

type InputRequirement = {
  code: string; label: string; stage?: string; stageLabel?: string; provider?: string;
  designStageAvailable?: boolean; action?: string; target?: string; available?: boolean;
};
type StabilityFactor = {
  code: string; ruleId?: string; label: string; value?: number | null; limit?: number | null;
  marginRatio?: number | null; status: string; standard?: string; clauseFocus?: string;
  evidenceState?: string; message?: string; nextAction?: string; missingInputDetails?: InputRequirement[];
};

function evidenceLabel(value: unknown): string {
  return ({ calculated: '已计算', missing_input: '缺资料', not_calculated: '待重算', manual_review: '专项复核', not_applicable: '不适用', not_implemented: '未实现' } as Record<string, string>)[String(value ?? '')] ?? '待复核';
}

export function StabilityDistributionVisual({ distribution }: { distribution?: { factors?: StabilityFactor[]; summary?: Record<string, any>; message?: string } }) {
  const factors = distribution?.factors ?? [];
  if (!factors.length) return <div className="coreVisualEmpty">稳定与水控制目录尚未加载。</div>;
  const knownMargins = factors.filter((factor) => factor.marginRatio != null).map((factor) => Number(factor.marginRatio));
  const useMargin = knownMargins.length > 0;
  const numericValues = factors.filter((factor) => factor.value != null).map((factor) => Number(factor.value));
  const maxRatio = Math.max(1.2, ...(useMargin ? knownMargins : numericValues));
  const pendingCount = factors.filter((factor) => factor.value == null).length;
  return <section className="coreVisualModule stabilityDistribution">
    <header><div><strong>稳定与水控制完整验算目录</strong><span>同时列出已计算系数、待补资料和专项复核项，空值不会被判为通过。</span></div><em className={Number(distribution?.summary?.failCount ?? 0) ? 'fail' : pendingCount ? 'warning' : 'pass'}>{distribution?.summary?.calculatedCount ?? 0} / {distribution?.summary?.count ?? factors.length} 已计算</em></header>
    <div className="coreStabilityRows">{factors.map((factor) => {
      const numeric = factor.value != null && Number.isFinite(Number(factor.value));
      const plotted = Number(factor.marginRatio ?? factor.value ?? 0);
      return <article key={factor.code} className={numeric ? '' : 'pending'}>
        <div><strong>{factor.label}</strong><span>{numeric ? `${Number(factor.value).toFixed(2)}${factor.limit != null ? ` / ${Number(factor.limit).toFixed(2)}` : ' / 限值待确认'}` : evidenceLabel(factor.evidenceState)}</span></div>
        {numeric ? <div className="coreStabilityTrack"><i className={factor.status} style={{ width: `${Math.max(2, Math.min(100, plotted / maxRatio * 100))}%` }} />{factor.marginRatio != null ? <b style={{ left: `${Math.min(100, 1 / maxRatio * 100)}%` }} /> : null}</div> : <div className="stabilityPendingReason">{factor.message ?? '当前结果尚未形成该项数值。'}</div>}
        <small>{factor.standard}{factor.clauseFocus ? ` · ${factor.clauseFocus}` : ''}</small>
        {!numeric && (factor.missingInputDetails?.length || factor.nextAction) ? <details className="verificationEvidenceDetails"><summary>查看缺失资料与补齐方法</summary>{factor.missingInputDetails?.map((input) => <div key={input.code}><b>{input.label}</b><span>{input.stageLabel} · {input.provider}{input.designStageAvailable ? ' · 设计阶段可提供' : ' · 需施工/专项阶段提供'}</span><em>{input.action}</em></div>)}{factor.nextAction ? <p>{factor.nextAction}</p> : null}</details> : null}
      </article>;
    })}</div>
    <footer><span>控制项余量 {distribution?.summary?.minimumMarginRatio != null ? Number(distribution.summary.minimumMarginRatio).toFixed(2) : '待确认'}</span><span>平均余量 {distribution?.summary?.averageMarginRatio != null ? Number(distribution.summary.averageMarginRatio).toFixed(2) : '待确认'}</span>{pendingCount ? <span>{pendingCount} 项待补资料/计算</span> : null}</footer>
  </section>;
}

function firstRecord(value: unknown): Record<string, any> | undefined {
  return Array.isArray(value) && value.length && value[0] && typeof value[0] === 'object' ? value[0] as Record<string, any> : undefined;
}

export function RebarDetailVisual({ project }: { project: Project }) {
  const scheme = project.retainingSystem?.rebarDesignScheme as Record<string, any> | undefined;
  const wall = firstRecord(scheme?.wallZones);
  const support = firstRecord(scheme?.supportSchemes);
  const diameter = numeric(wall?.diameterMm ?? wall?.barDiameter ?? wall?.mainBarDiameter ?? support?.diameterMm ?? support?.barDiameter);
  const spacing = numeric(wall?.spacingMm ?? wall?.barSpacing ?? support?.spacingMm ?? support?.barSpacing);
  const cover = numeric(wall?.coverMm ?? scheme?.summary?.coverMm);
  if (!scheme) return <div className="coreVisualEmpty">生成配筋方案后显示墙、围檩和支撑配筋细节。</div>;
  return <section className="coreVisualModule rebarDetail">
    <header><div><strong>配筋细节</strong><span>示意图用于快速审阅，正式数量以钢筋表和施工图为准</span></div><em>{String(scheme.mode ?? 'balanced')}</em></header>
    <div className="coreRebarLayout">
      <svg className="coreRebarSvg" viewBox="0 0 420 230" role="img" aria-label="围护墙钢筋笼截面示意">
        <rect x="52" y="24" width="316" height="182" className="coreConcreteSection" />
        <rect x="78" y="48" width="264" height="134" rx="4" className="coreRebarCage" />
        {[92, 130, 168, 206, 244, 282, 320].map((x) => <g key={x}><circle cx={x} cy="58" r="6" className="coreRebarBar" /><circle cx={x} cy="172" r="6" className="coreRebarBar" /></g>)}
        {[82, 112, 142, 172].map((y) => <line key={y} x1="80" x2="340" y1={y} y2={y} className="coreDistributionBar" />)}
        <text x="54" y="18">围护墙钢筋笼示意</text><text x="84" y="222">{diameter != null ? `主筋 Φ${diameter}` : '主筋参数见钢筋表'}{spacing != null ? `，参考间距 ${spacing} mm` : ''}{cover != null ? `，保护层 ${cover} mm` : ''}</text>
      </svg>
      <dl className="coreRebarSummary">
        <div><dt>墙体分区</dt><dd>{Array.isArray(scheme.wallZones) ? scheme.wallZones.length : 0}</dd></div>
        <div><dt>支撑配筋组</dt><dd>{Array.isArray(scheme.supportSchemes) ? scheme.supportSchemes.length : 0}</dd></div>
        <div><dt>节点附加筋</dt><dd>{Array.isArray(scheme.beamNodeSchemes) ? scheme.beamNodeSchemes.length : 0}</dd></div>
        <div><dt>配筋检查</dt><dd>{Array.isArray(scheme.checks) ? scheme.checks.length : 0}</dd></div>
      </dl>
    </div>
  </section>;
}

type VerificationRecord = {
  id: string; ruleId?: string; label: string; category: 'strength' | 'stiffness' | 'stability' | 'hydraulic' | 'constructability' | 'other';
  designValue?: number | null; limitValue?: number | null; unit?: string; safetyFactor?: number | null;
  utilization?: number | null; status: string; standard?: string; clause?: string; message?: string; objectCount?: number;
  evidenceState?: string; implementationState?: string; missingInputs?: string[]; missingInputDetails?: InputRequirement[];
  targetSafetyFactor?: number | null; nextAction?: string; objectCode?: string; stageResults?: VerificationRecord[];
};

type WallVerificationObject = {
  wallId: string; wallCode: string; wallTypeLabel?: string; segmentId?: string; thicknessM?: number;
  topElevationM?: number; bottomElevationM?: number; status?: string; summary?: Record<string, any>; checks?: VerificationRecord[];
};
type VerificationDistribution = {
  records?: VerificationRecord[]; wallObjects?: WallVerificationObject[]; missingInputSummary?: (InputRequirement & { affectedCheckCount?: number; affectedChecks?: string[] })[];
  summary?: Record<string, any>; message?: string;
};

export function VerificationSafetyPanel({ distribution }: { distribution?: VerificationDistribution }) {
  const [category, setCategory] = useState<'strength' | 'stiffness' | 'stability' | 'hydraulic' | 'constructability'>('strength');
  const [showAll, setShowAll] = useState(false);
  const labels = { strength: '强度', stiffness: '刚度', stability: '稳定性', hydraulic: '水控制', constructability: '施工性' };
  const records = (distribution?.records ?? []).filter((item) => item.category === category);
  const visible = showAll ? records : records.filter((item) => item.status !== 'pass').slice(0, 12).concat(records.filter((item) => item.status === 'pass').slice(0, 4));
  const summary = distribution?.summary?.[category] ?? {};
  const overall = distribution?.summary?.overall ?? {};
  const missingInputs = distribution?.missingInputSummary ?? [];
  const walls = distribution?.wallObjects ?? [];
  return <section className="coreVisualModule verificationSafetyPanel">
    <header><div><strong>基坑工程完整验算矩阵</strong><span>{overall.catalogCount ?? records.length} 项正式目录，覆盖构件强度、刚度、体系稳定、水控制与施工性；项目储备目标 ≥ {Number(overall.reserveThreshold ?? 1.1).toFixed(2)}。</span></div><em className={Number(overall.failCount ?? 0) ? 'fail' : Number(overall.warningCount ?? 0) ? 'warning' : 'pass'}>控制：{String(overall.controllingLabel ?? '待计算')}</em></header>
    <div className="verificationSummaryGrid">
      {(['strength', 'stiffness', 'stability', 'hydraulic', 'constructability'] as const).map((key) => { const item = distribution?.summary?.[key] ?? {}; return <button type="button" key={key} className={category === key ? 'active' : ''} onClick={() => { setCategory(key); setShowAll(false); }}><span>{labels[key]}</span><strong>{item.minimumSafetyFactor == null ? '-' : Number(item.minimumSafetyFactor).toFixed(2)}</strong><small>{item.failCount ?? 0} 不通过 · {item.warningCount ?? 0} 待闭合 · 共 {item.count ?? 0}</small></button>; })}
    </div>
    {records.length ? <div className="verificationTableWrap"><table className="table compactTable verificationTable"><thead><tr><th>校核项</th><th>设计值</th><th>规范限值</th><th>安全系数</th><th>目标值</th><th>利用率</th><th>证据与补齐方法</th><th>状态</th><th>规范依据</th></tr></thead><tbody>{visible.map((item) => <tr key={item.id} className={item.status}><td><strong>{item.label}</strong>{item.objectCount && item.objectCount > 1 ? <small>覆盖 {item.objectCount} 个对象</small> : null}</td><td>{item.designValue == null ? '-' : `${Number(item.designValue).toFixed(3)} ${item.unit ?? ''}`}</td><td>{item.limitValue == null ? '待确认' : `${Number(item.limitValue).toFixed(3)} ${item.unit ?? ''}`}</td><td>{item.safetyFactor == null ? '-' : Number(item.safetyFactor).toFixed(3)}</td><td>{item.targetSafetyFactor == null ? '-' : Number(item.targetSafetyFactor).toFixed(2)}</td><td>{item.utilization == null ? '-' : `${(Number(item.utilization) * 100).toFixed(1)}%`}</td><td><span className={`verificationEvidence ${item.evidenceState ?? 'manual_review'}`}>{evidenceLabel(item.evidenceState)}</span>{item.missingInputDetails?.length || item.nextAction ? <details className="verificationEvidenceDetails"><summary>{item.message ?? '查看补齐方法'}</summary>{item.missingInputDetails?.map((input) => <div key={input.code}><b>{input.label}</b><span>{input.stageLabel} · {input.provider}</span><em>{input.designStageAvailable ? '设计阶段可提供' : '需施工/专项阶段提供'}</em></div>)}{item.nextAction ? <p>{item.nextAction}</p> : null}</details> : item.message ? <small>{item.message}</small> : null}</td><td><span className={`verificationStatus ${item.status}`}>{item.status === 'pass' ? '通过' : item.status === 'fail' ? '不通过' : item.status === 'not_applicable' ? '不适用' : '需复核'}</span></td><td>{[item.standard, item.clause].filter(Boolean).join(' · ') || '-'}</td></tr>)}</tbody></table></div> : <div className="coreVisualEmpty">当前验算目录没有{labels[category]}项目。</div>}
    <footer><span>{labels[category]}共 {summary.count ?? 0} 项</span><span>平均安全系数 {summary.averageSafetyFactor == null ? '-' : Number(summary.averageSafetyFactor).toFixed(2)}</span>{records.length > visible.length ? <button type="button" className="secondary" onClick={() => setShowAll((v) => !v)}>{showAll ? '仅看控制项' : `查看全部 ${records.length} 项`}</button> : null}</footer>
    {missingInputs.length ? <details className="missingInputRegister" open><summary>缺资料闭合清单：{missingInputs.length} 类资料影响当前验算</summary><div>{missingInputs.map((input) => <article key={input.code}><span className={`designStageBadge ${input.designStageAvailable ? 'available' : 'later'}`}>{input.designStageAvailable ? '设计阶段可提供' : '施工/专项阶段提供'}</span><strong>{input.label}</strong><small>{input.stageLabel} · 提供方：{input.provider} · 影响 {input.affectedCheckCount ?? 0} 项</small><p>{input.action}</p>{input.affectedChecks?.length ? <details><summary>查看受影响验算</summary><span>{input.affectedChecks.join('、')}</span></details> : null}</article>)}</div></details> : null}
    {walls.length ? <details className="wallVerificationExplorer"><summary>逐墙展开验算结果：{walls.length} 个地下连续墙对象</summary><div>{walls.map((wall) => { const checks = (wall.checks ?? []).filter((item) => item.category === category); return <details key={wall.wallId} className={`wallVerificationCard ${wall.status ?? 'warning'}`}><summary><span><strong>{wall.wallCode}</strong><em>{wall.wallTypeLabel ?? '围护墙'} · 墙厚 {wall.thicknessM == null ? '-' : `${Number(wall.thicknessM).toFixed(2)} m`} · 标高 {wall.topElevationM ?? '-'}～{wall.bottomElevationM ?? '-'}</em></span><b>{wall.summary?.calculatedCount ?? 0} 已计算 · {wall.summary?.failCount ?? 0} 不通过 · {wall.summary?.reviewCount ?? 0} 待闭合</b></summary><div className="verificationTableWrap"><table className="table compactTable"><thead><tr><th>验算项目</th><th>控制值/限值</th><th>安全系数</th><th>证据</th><th>补齐方法</th></tr></thead><tbody>{checks.map((item) => <tr key={item.id} className={item.status}><td><strong>{item.label}</strong></td><td>{item.designValue == null ? '-' : Number(item.designValue).toFixed(3)} / {item.limitValue == null ? '-' : Number(item.limitValue).toFixed(3)}</td><td>{item.safetyFactor == null ? '-' : Number(item.safetyFactor).toFixed(3)}</td><td>{evidenceLabel(item.evidenceState)}{item.stageResults?.length ? ` · ${item.stageResults.length} 个工况记录` : ''}</td><td>{item.nextAction ?? item.message ?? '-'}</td></tr>)}</tbody></table></div></details>; })}</div></details> : null}
  </section>;
}

export function AdverseScenarioPanel({ scenarios }: { scenarios?: Record<string, any>[] }) {
  const rows = scenarios ?? [];
  if (!rows.length) return <section className="coreVisualModule adverseScenarioPanel"><header><div><strong>不利工况筛查</strong><span>启用降水失效、超挖、局部渗流、承压水抬升和长期效应后显示。</span></div></header><div className="coreVisualEmpty">当前结果尚未生成不利工况筛查。</div></section>;
  return <section className="coreVisualModule adverseScenarioPanel">
    <header><div><strong>不利工况与敏感性</strong><span>基于当前正式计算包络的透明筛查；高风险场景需要独立施工阶段重算。</span></div><em>{rows.filter((r) => r.status === 'fail').length} 不通过</em></header>
    <div className="adverseScenarioGrid">{rows.map((row, index) => {
      const formal = String(row.evidenceLevel ?? '').includes('formal');
      const scenarioCode = String(row.scenarioCode ?? row.scenarioId ?? '');
      const scenarioLabel = String(row.label ?? row.name ?? SCENARIO_LABELS[scenarioCode] ?? `场景 ${index + 1}`);
      return <article key={String(row.id ?? row.scenarioId ?? row.scenarioCode ?? index)} className={String(row.status ?? 'manual_review')}><strong>{scenarioLabel}</strong><span>{readableEngineeringText(row.description ?? row.message ?? row.parameters)}</span><dl>{formal ? <><div><dt>墙体位移</dt><dd>{row.maxWallDisplacementMm == null ? '-' : `${Number(row.maxWallDisplacementMm).toFixed(2)} mm`}</dd></div><div><dt>最大支撑轴力</dt><dd>{row.maxSupportForceKn == null ? '-' : `${Number(row.maxSupportForceKn).toFixed(0)} kN`}</dd></div></> : <><div><dt>控制值</dt><dd>{row.governingValue == null ? '-' : readableEngineeringText(row.governingValue)}</dd></div><div><dt>基准值</dt><dd>{row.baselineValue == null ? '-' : readableEngineeringText(row.baselineValue)}</dd></div></>}<div><dt>最小安全系数</dt><dd>{row.safetyFactor == null ? '-' : Number(row.safetyFactor).toFixed(3)}</dd></div><div><dt>证据</dt><dd>{formal ? '正式复算' : '筛查'}</dd></div></dl><small>{readableEngineeringText(row.boundary ?? row.recommendedAction ?? row.error)}</small></article>;
    })}</div>
  </section>;
}

export function RebarConstructabilityPanel({ scheme }: { scheme?: Record<string, any> }) {
  const constructability = scheme?.constructability as Record<string, any> | undefined;
  if (!constructability) return null;
  const summary = constructability.summary ?? {};
  const checks = (constructability.checks ?? []) as Record<string, any>[];
  return <section className="coreVisualModule rebarConstructabilityPanel">
    <header><div><strong>钢筋可施工性闭环</strong><span>锚固、搭接、机械连接、钢筋拥挤和节点刚域筛查。</span></div><em className={Number(summary.failCount ?? 0) ? 'fail' : Number(summary.warningCount ?? 0) ? 'warning' : 'pass'}>{Number(summary.failCount ?? 0)} 阻断 · {Number(summary.warningCount ?? 0)} 复核</em></header>
    <div className="verificationTableWrap"><table className="table compactTable"><thead><tr><th>构件</th><th>校核</th><th>计算值</th><th>控制值</th><th>状态</th><th>建议</th></tr></thead><tbody>{checks.filter((row) => row.status !== 'pass').slice(0, 30).map((row, index) => <tr key={String(row.checkId ?? row.ruleId ?? index)} className={String(row.status)}><td>{String(row.hostCode ?? row.objectId ?? '-')}</td><td>{REBAR_CHECK_LABELS[String(row.category)] ?? String(row.ruleId ?? '-')}</td><td>{row.calculatedValue == null ? '-' : `${String(row.calculatedValue)} ${String(row.unit ?? '')}`}</td><td>{row.limitValue == null ? '-' : String(row.limitValue)}</td><td>{CORE_STATUS_LABELS[String(row.status)] ?? '需复核'}</td><td>{String(row.recommendedAction ?? row.message ?? '')}</td></tr>)}</tbody></table></div>
    <footer><span>{String(constructability.boundary ?? '')}</span></footer>
  </section>;
}
