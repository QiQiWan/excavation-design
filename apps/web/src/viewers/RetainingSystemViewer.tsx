import type { Point2D, Project, QualityGateIssue } from '../types/domain';
import Engineering3DViewer from './Engineering3DViewer';

function fmt(value: unknown, digits = 2) {
  if (typeof value !== 'number') return value === undefined || value === null ? '-' : String(value);
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}


function issueSeverityByObject(issues: QualityGateIssue[] | undefined) {
  const map = new Map<string, string>();
  const rank: Record<string, number> = { fail: 3, manual_review: 2, warning: 1, pass: 0 };
  (issues ?? []).forEach((issue) => {
    const ids = [issue.objectId, ...(issue.relatedObjectIds ?? [])].filter(Boolean) as string[];
    ids.forEach((id) => {
      const prev = map.get(id);
      if (!prev || (rank[issue.severity] ?? 0) > (rank[prev] ?? 0)) map.set(id, issue.severity);
    });
  });
  return map;
}

function supportStroke(severity?: string, role?: string) {
  if (severity === 'fail') return '#dc2626';
  if (severity === 'warning' || severity === 'manual_review') return '#f59e0b';
  if (role === 'corner_diagonal') return '#ea580c';
  if (role === 'ring_strut') return '#7c3aed';
  return '#2563eb';
}

function projectBounds(project: Project) {
  const pts: Point2D[] = [];
  project.excavation?.outline.points.forEach((p) => pts.push(p));
  project.excavation?.obstacles?.forEach((o) => o.outline?.points.forEach((p) => pts.push(p)));
  project.retainingSystem?.supports.forEach((s) => { pts.push(s.start, s.end); });
  project.retainingSystem?.columns.forEach((c) => pts.push(c.location));
  if (!pts.length) return { minX: -5, minY: -5, maxX: 65, maxY: 45 };
  const xs = pts.map((p) => p.x); const ys = pts.map((p) => p.y);
  const pad = 5;
  return { minX: Math.min(...xs) - pad, minY: Math.min(...ys) - pad, maxX: Math.max(...xs) + pad, maxY: Math.max(...ys) + pad };
}

function SupportQualityPlan({ project, highlightLocator }: { project: Project; highlightLocator?: Record<string, unknown> }) {
  const ret = project.retainingSystem;
  const latest = project.calculationResults?.[project.calculationResults.length - 1];
  const quality = latest?.supportLayoutQuality;
  if (!ret || !project.excavation) return null;
  const b = projectBounds(project);
  const w = Math.max(10, b.maxX - b.minX);
  const h = Math.max(10, b.maxY - b.minY);
  const viewBox = `${b.minX} ${b.minY} ${w} ${h}`;
  const issueMap = issueSeverityByObject(quality?.issues);
  const targetId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');
  const conflictObstacle = (quality?.issues ?? []).some((i) => i.category === 'obstacle_clearance' && ['fail', 'warning'].includes(i.severity));
  return (
    <div className="supportQualityPlan">
      <div className="planHeader">
        <h4>支撑布置评分平面高亮</h4>
        <span className="small">红=阻断/交叉/严重超限，橙=警告，蓝=通过或未标记；黄色半透明区=障碍/出土口需避让。</span>
      </div>
      <svg viewBox={viewBox} className="supportPlanSvg" preserveAspectRatio="xMidYMid meet">
        <defs>
          <pattern id="plan-grid" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M 5 0 L 0 0 0 5" fill="none" stroke="#e2e8f0" strokeWidth="0.08" /></pattern>
        </defs>
        <rect x={b.minX} y={b.minY} width={w} height={h} fill="url(#plan-grid)" />
        <polyline points={project.excavation.outline.points.map((p) => `${p.x},${p.y}`).join(' ')} fill="rgba(37,99,235,0.05)" stroke="#0f172a" strokeWidth="0.25" />
        {project.excavation.obstacles?.map((obs) => obs.outline?.points?.length ? (
          <polygon key={obs.id} points={obs.outline.points.map((p) => `${p.x},${p.y}`).join(' ')} fill={conflictObstacle ? 'rgba(239,68,68,0.18)' : 'rgba(245,158,11,0.16)'} stroke={conflictObstacle ? '#dc2626' : '#f59e0b'} strokeWidth="0.22" />
        ) : null)}
        {ret.supports.map((s) => {
          const sev = issueMap.get(s.id);
          const selected = targetId && (targetId === s.id || targetId === s.code);
          return <line key={s.id} className={selected ? 'locatorPulseStroke' : ''} x1={s.start.x} y1={s.start.y} x2={s.end.x} y2={s.end.y} stroke={selected ? '#eab308' : supportStroke(sev, s.supportRole)} strokeWidth={selected ? 1.25 : sev === 'fail' ? 0.7 : 0.42} strokeLinecap="round"><title>{s.code} {sev ?? 'ok'} span={s.spanLength}</title></line>;
        })}
        {quality?.crossingPairs?.map((pair, idx) => {
          const pt = pair.point as { x?: number; y?: number } | undefined;
          if (pt?.x === undefined || pt?.y === undefined) return null;
          return <g key={`cross-${idx}`}><circle cx={pt.x} cy={pt.y} r="0.9" fill="#dc2626" /><text x={pt.x + 1} y={pt.y - 1} fontSize="2" fill="#dc2626">交叉</text></g>;
        })}
        {ret.columns.map((c) => { const selected = targetId && (targetId === c.id || targetId === c.code); return <g key={c.id} className={selected ? 'locatorPulseFill' : ''}><circle cx={c.location.x} cy={c.location.y} r={selected ? '1.35' : '0.8'} fill={selected ? '#eab308' : '#78350f'} /><title>{c.code}: {c.supportCodes?.join(', ')}</title></g>; })}
      </svg>
      <div className="metricGrid compact">
        <div><strong>{quality?.score ?? '-'}</strong><span>评分</span></div>
        <div><strong>{String(quality?.metrics?.supportCrossingCount ?? 0)}</strong><span>交叉</span></div>
        <div><strong>{String(quality?.metrics?.maxBaySpacing ?? '-')}</strong><span>最大分仓</span></div>
        <div><strong>{String(quality?.metrics?.maxSpanLength ?? '-')}</strong><span>最大跨长</span></div>
      </div>
    </div>
  );
}

function roleText(role?: string) {
  if (role === 'main_strut') return '主对撑';
  if (role === 'corner_diagonal') return '角撑';
  if (role === 'ring_strut') return '环撑径向撑';
  return role ?? '-';
}

export default function RetainingSystemViewer({ project, highlightLocator }: { project: Project; highlightLocator?: Record<string, unknown> }) {
  const retaining = project.retainingSystem;
  const supportCountByRole = retaining?.layoutSummary?.supportCountByRole as Record<string, number> | undefined;
  return (
    <div>
      <Engineering3DViewer project={project} focus="retaining" highlightLocator={highlightLocator} />
      <SupportQualityPlan project={project} highlightLocator={highlightLocator} />
      <div className="card">
        <h3>围护结构参数与校核状态</h3>
        {retaining?.layoutSummary && (
          <div className="metricGrid compact">
            <div><strong>{retaining.layoutSummary.supportCount as number}</strong><span>支撑总数</span></div>
            <div><strong>{supportCountByRole?.main_strut ?? 0}</strong><span>主对撑</span></div>
            <div><strong>{supportCountByRole?.corner_diagonal ?? 0}</strong><span>角撑</span></div>
            <div><strong>{supportCountByRole?.ring_strut ?? 0}</strong><span>环撑径向撑</span></div>
            <div><strong>{retaining.layoutSummary.columnCount as number}</strong><span>临时立柱</span></div>
            <div><strong>{retaining.layoutSummary.ringBeamCount as number}</strong><span>环梁</span></div>
          </div>
        )}
        <h4>地连墙</h4>
        <table className="table">
          <thead><tr><th>编号</th><th>设计面</th><th>厚度</th><th>墙底</th><th>设计弯矩</th><th>承载力</th><th>配筋</th><th>状态</th></tr></thead>
          <tbody>{retaining?.diaphragmWalls.map((w) => <tr key={w.id}><td>{w.panelCode}</td><td>{w.designFaceCode ?? '-'}</td><td>{w.thickness}</td><td>{w.bottomElevation}</td><td>{w.designResults?.maxMomentDesign ?? '-'}</td><td>{w.designResults?.momentCapacity ?? '-'}</td><td>{w.reinforcement.map((r) => `${r.name} D${r.diameter}${r.spacing ? '@' + r.spacing : ''}`).join('; ')}</td><td>{w.designResults?.checkStatus ?? 'manual_review'}</td></tr>) ?? <tr><td colSpan={8}>未生成</td></tr>}</tbody>
        </table>
        <h4>水平支撑分仓与围檩连续梁轴力分配</h4>
        <table className="table">
          <thead><tr><th>编号</th><th>角色</th><th>层号</th><th>标高</th><th>跨长</th><th>分仓</th><th>连接墙面</th><th>参考 tributary width</th><th>设计轴力</th><th>预加/温度/间隙</th><th>生命周期</th><th>分配模型</th><th>逻辑说明</th></tr></thead>
          <tbody>{retaining?.supports.map((s) => <tr key={s.id}>
            <td>{s.code}</td><td>{roleText(s.supportRole)}</td><td>{s.levelIndex}</td><td>{s.elevation}</td>
            <td>{fmt(s.spanLength)} m</td><td>{fmt(s.baySpacing)} m</td><td>{s.startFaceCode ?? '-'} / {s.endFaceCode ?? '-'}</td>
            <td>{fmt(s.startTributaryWidth)} / {fmt(s.endTributaryWidth)} m</td><td>{s.designAxialForce ? fmt(s.designAxialForce, 1) + ' kN' : '-'}</td>
            <td className="small">{fmt(s.preload)} / {fmt(s.thermalAxialForce)} / {fmt(s.gapClosureForce)} kN</td>
            <td className="small">{s.preloadStageId ?? '-'} → {s.removalStageId ?? '-'}</td>
            <td className="small">围檩连续梁-弹性支座反力</td>
            <td className="small">{s.constructionEffectNote ?? s.forceDistributionNote ?? s.layoutNote ?? '-'}</td>
          </tr>) ?? <tr><td colSpan={12}>未生成</td></tr>}</tbody>
        </table>
        <h4>围檩本体设计与多工况包络</h4>
        <table className="table">
          <thead><tr><th>围檩</th><th>层号</th><th>截面</th><th>墙面</th><th>Md</th><th>Vd</th><th>挠度/限值</th><th>主筋</th><th>箍筋</th><th>截面优化</th><th>连接构造</th><th>状态</th></tr></thead>
          <tbody>{retaining?.waleBeams.filter((b) => b.designResult).slice(0, 80).map((b) => <tr key={b.id}>
            <td>{b.code}</td><td>{b.supportLevel ?? '-'}</td><td>{b.section.name}</td><td>{b.designResult?.faceCode ?? '-'}</td>
            <td>{fmt(b.designResult?.maxMomentDesign)} kN·m</td><td>{fmt(b.designResult?.maxShearDesign)} kN</td><td>{fmt(b.designResult?.maxDeflection, 4)} / {fmt(b.designResult?.deflectionLimit, 4)}</td>
            <td>D{b.designResult?.mainBarDiameter}@{b.designResult?.mainBarSpacing}</td><td>D{b.designResult?.stirrupDiameter}@{b.designResult?.stirrupSpacing}</td>
            <td className="small">{fmt(b.designResult?.optimizedWidth)}×{fmt(b.designResult?.optimizedHeight)}m；试算 {b.designResult?.optimizationHistory?.length ?? 0} 组</td>
            <td className="small">{b.designResult?.wallConnectionNote ?? b.designResult?.nodeAdditionalReinforcementNote ?? '-'}</td><td>{b.designResult?.checkStatus ?? '-'}</td>
          </tr>) ?? <tr><td colSpan={12}>未生成</td></tr>}</tbody>
        </table>
        <h4>支撑-围檩节点</h4>
        <table className="table">
          <thead><tr><th>节点</th><th>支撑</th><th>墙面/围檩</th><th>承压面积</th><th>承压应力</th><th>承载限值</th><th>节点配筋</th><th>状态</th></tr></thead>
          <tbody>{retaining?.supportNodes?.map((n) => <tr key={n.id}>
            <td>{n.code}</td><td>{n.supportCode}</td><td>{n.faceCode ?? '-'} / {n.waleBeamCode ?? '-'}</td>
            <td>{fmt(n.bearingPlate?.bearingArea)} m²</td><td>{fmt(n.bearingPlate?.bearingStress)} kPa</td><td>{fmt(n.bearingPlate?.bearingCapacity)} kPa</td>
            <td>{n.reinforcement.map((r) => `${r.name} D${r.diameter}${r.count ? 'x' + r.count : r.spacing ? '@' + r.spacing : ''}`).join('; ')}</td><td>{n.checkStatus ?? 'manual_review'}</td>
          </tr>) ?? <tr><td colSpan={8}>未生成</td></tr>}</tbody>
        </table>
        <h4>环梁 / 冠梁 / 腰梁 / 立柱桩</h4>
        <p className="small">冠梁 {retaining?.crownBeams.length ?? 0}；腰梁 {retaining?.waleBeams.length ?? 0}；环梁 {retaining?.ringBeams?.length ?? 0}；临时立柱 {retaining?.columns.length ?? 0}。</p>
        <table className="table">
          <thead><tr><th>立柱</th><th>位置</th><th>服务支撑</th><th>基础类型</th><th>桩径</th><th>桩长</th><th>承载力</th><th>利用率</th></tr></thead>
          <tbody>{retaining?.columns.map((c) => <tr key={c.id}>
            <td>{c.code}</td><td>({fmt(c.location.x)}, {fmt(c.location.y)})</td><td>{c.supportCodes?.join(', ') || '-'}</td>
            <td>{c.foundationDesign?.foundationType ?? '-'}</td><td>{fmt(c.foundationDesign?.pileDiameter)} m</td><td>{fmt(c.foundationDesign?.pileLength)} m</td><td>{fmt(c.foundationDesign?.pileCapacity)} kN</td><td>{fmt(c.foundationDesign?.pileUtilization)}</td>
          </tr>) ?? <tr><td colSpan={8}>未生成</td></tr>}</tbody>
        </table>
        {retaining?.replacementPath?.length ? <><h4>分区开挖与换撑路径</h4><ol>{retaining.replacementPath.map((step, idx) => <li key={idx}><strong>{String(step.name ?? step.action)}</strong><span className="small"> {JSON.stringify(step)}</span></li>)}</ol></> : null}
        {retaining?.warnings.map((item) => <div key={item} className="warning">{item}</div>)}
      </div>
    </div>
  );
}
