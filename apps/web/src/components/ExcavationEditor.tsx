import { useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ConstructionObstacle, Point2D, Project } from '../types/domain';
import { polygonArea, polygonPerimeter } from '../drawing/geometry';

const defaultPoints: Point2D[] = [
  { x: 5, y: 5 }, { x: 55, y: 5 }, { x: 55, y: 35 }, { x: 5, y: 35 }
];

interface ViewBox { x: number; y: number; w: number; h: number }
type DragMode = { type: 'point'; index: number } | { type: 'pan'; startX: number; startY: number; viewBox: ViewBox } | undefined;
type DrawLayerKey = 'grid' | 'outline' | 'dimensions' | 'obstacles' | 'axis';

const DEFAULT_LAYERS: Record<DrawLayerKey, boolean> = { grid: true, outline: true, dimensions: true, obstacles: true, axis: true };

export default function ExcavationEditor({ project, onSaved }: { project: Project; onSaved: () => void }) {
  const [points, setPointsRaw] = useState<Point2D[]>(project.excavation?.outline.points ?? defaultPoints);
  const [polylineClosed, setPolylineClosed] = useState(project.excavation?.outline.closed ?? true);
  const [topElevation, setTopElevation] = useState(project.excavation?.topElevation ?? 0);
  const [bottomElevation, setBottomElevation] = useState(project.excavation?.bottomElevation ?? -12);
  const [obstacles, setObstacles] = useState<ConstructionObstacle[]>(project.excavation?.obstacles ?? []);
  const [activeObstacleType, setActiveObstacleType] = useState<'ramp' | 'muck_out_opening' | 'center_island' | 'basement_column_grid'>('ramp');
  const [command, setCommand] = useState('');
  const [offsetDistance, setOffsetDistance] = useState(1.0);
  const [supportAxisOffset, setSupportAxisOffset] = useState(project.excavation?.supportAxisOffset ?? 1.2);
  const [basementWallOffset, setBasementWallOffset] = useState(project.excavation?.basementWallOffset ?? 0.0);
  const [explicitPlacement, setExplicitPlacement] = useState(project.excavation?.explicitPlacement ?? false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [selectedIndex, setSelectedIndex] = useState<number | undefined>();
  const [history, setHistory] = useState<Point2D[][]>([]);
  const [redoStack, setRedoStack] = useState<Point2D[][]>([]);
  const [drag, setDrag] = useState<DragMode>();
  const [viewBox, setViewBox] = useState<ViewBox>(() => initialViewBox(project.excavation?.outline.points ?? defaultPoints));
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [orthoEnabled, setOrthoEnabled] = useState(false);
  const [layers, setLayers] = useState<Record<DrawLayerKey, boolean>>(DEFAULT_LAYERS);
  const svgRef = useRef<SVGSVGElement | null>(null);

  function commitPoints(next: Point2D[]) {
    setHistory((items) => [...items.slice(-40), points]);
    setRedoStack([]);
    setPointsRaw(next.map(roundPoint));
  }

  function setPointDraft(next: Point2D[]) { setPointsRaw(next.map(roundPoint)); }
  function updatePoint(index: number, key: 'x' | 'y', value: number) { commitPoints(points.map((p, i) => i === index ? { ...p, [key]: value } : p)); }

  function undo() {
    const previous = history[history.length - 1];
    if (!previous) return;
    setRedoStack((items) => [points, ...items]);
    setHistory((items) => items.slice(0, -1));
    setPointsRaw(previous);
    setSelectedIndex(undefined);
  }

  function redo() {
    const next = redoStack[0];
    if (!next) return;
    setHistory((items) => [...items, points]);
    setRedoStack((items) => items.slice(1));
    setPointsRaw(next);
    setSelectedIndex(undefined);
  }

  async function save() {
    try {
      const validation = validateOutline(points, topElevation, bottomElevation, polylineClosed);
      if (validation.errors.length) { setError(validation.errors.join('；')); return; }
      setError(undefined);
      await api.createExcavation(project.id, {
        name: 'Main excavation',
        topElevation,
        bottomElevation,
        outline: { closed: polylineClosed, points },
        obstacles,
        supportAxisOffset,
        basementWallOffset,
        explicitPlacement,
        drawingLayers: Object.entries(layers).map(([name, visible]) => ({ name, visible, source: 'frontend-cad-editor' })),
      });
      onSaved();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }

  const validation = useMemo(() => validateOutline(points, topElevation, bottomElevation, polylineClosed), [points, topElevation, bottomElevation, polylineClosed]);
  const pointString = points.map((p) => `${p.x},${p.y}`).join(' ');
  const gridLines = useMemo(() => makeGridLines(viewBox), [viewBox]);
  const dimensions = useMemo(() => points.map((p, i) => ({ a: p, b: points[(i + 1) % points.length], length: Math.hypot(points[(i + 1) % points.length].x - p.x, points[(i + 1) % points.length].y - p.y) })), [points]);

  function svgPoint(event: React.PointerEvent<SVGSVGElement> | React.WheelEvent<SVGSVGElement>): Point2D {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return { x: viewBox.x + (event.clientX - rect.left) / rect.width * viewBox.w, y: viewBox.y + (event.clientY - rect.top) / rect.height * viewBox.h };
  }

  function transformDraftPoint(index: number, raw: Point2D): Point2D {
    const previous = points[index];
    let next = snapEnabled ? snapPoint(raw, 0.5) : raw;
    if (orthoEnabled || (window.event instanceof MouseEvent && window.event.shiftKey)) {
      const prev = points[(index - 1 + points.length) % points.length];
      const nxt = points[(index + 1) % points.length];
      const dxPrev = Math.abs(next.x - prev.x), dyPrev = Math.abs(next.y - prev.y);
      const dxNext = Math.abs(next.x - nxt.x), dyNext = Math.abs(next.y - nxt.y);
      if (Math.min(dxPrev, dxNext) < Math.min(dyPrev, dyNext)) next = { ...next, x: dxPrev < dxNext ? prev.x : nxt.x };
      else next = { ...next, y: dyPrev < dyNext ? prev.y : nxt.y };
    }
    if (!Number.isFinite(next.x) || !Number.isFinite(next.y)) return previous;
    return roundPoint(next);
  }

  function onPointerDown(event: React.PointerEvent<SVGSVGElement>) {
    if (event.button === 1 || event.altKey) {
      event.currentTarget.setPointerCapture(event.pointerId);
      setDrag({ type: 'pan', startX: event.clientX, startY: event.clientY, viewBox });
    }
  }

  function onPointerMove(event: React.PointerEvent<SVGSVGElement>) {
    if (!drag) return;
    if (drag.type === 'point') {
      const next = [...points];
      next[drag.index] = transformDraftPoint(drag.index, svgPoint(event));
      setPointDraft(next);
    } else if (drag.type === 'pan') {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const dx = (event.clientX - drag.startX) / rect.width * drag.viewBox.w;
      const dy = (event.clientY - drag.startY) / rect.height * drag.viewBox.h;
      setViewBox({ ...drag.viewBox, x: drag.viewBox.x - dx, y: drag.viewBox.y - dy });
    }
  }

  function onPointerUp(event: React.PointerEvent<SVGSVGElement>) {
    if (drag?.type === 'point') { setHistory((items) => [...items.slice(-40), points]); setRedoStack([]); }
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* no-op */ }
    setDrag(undefined);
  }

  function onWheel(event: React.WheelEvent<SVGSVGElement>) {
    event.preventDefault();
    const p = svgPoint(event);
    const factor = event.deltaY > 0 ? 1.12 : 0.88;
    const newW = clamp(viewBox.w * factor, 10, 500), newH = clamp(viewBox.h * factor, 8, 500);
    const rx = (p.x - viewBox.x) / viewBox.w, ry = (p.y - viewBox.y) / viewBox.h;
    setViewBox({ x: p.x - rx * newW, y: p.y - ry * newH, w: newW, h: newH });
  }

  function insertPointOnEdge(index: number) {
    const a = points[index], b = points[(index + 1) % points.length];
    const mid = snapPoint({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, 0.5);
    commitPoints([...points.slice(0, index + 1), mid, ...points.slice(index + 1)]);
    setSelectedIndex(index + 1);
  }

  function deleteSelected() {
    if (selectedIndex === undefined || points.length <= 3) return;
    commitPoints(points.filter((_, i) => i !== selectedIndex));
    setSelectedIndex(undefined);
  }

  function offsetSelectedEdge() {
    if (selectedIndex === undefined) return;
    const a = points[selectedIndex], b = points[(selectedIndex + 1) % points.length];
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const nx = -(b.y - a.y) / len, ny = (b.x - a.x) / len;
    const d = Number(offsetDistance) || 0;
    const next = points.map((p, i) => (i === selectedIndex || i === (selectedIndex + 1) % points.length) ? { x: p.x + nx * d, y: p.y + ny * d } : p);
    commitPoints(next);
  }

  function applyCommand() {
    const text = command.trim();
    if (!text) return;
    const nums = text.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
    const upper = text.toUpperCase();
    if (upper.startsWith('RECT') && nums.length >= 4) {
      const [x, y, w, h] = nums;
      commitPoints([{ x, y }, { x: x + w, y }, { x: x + w, y: y + h }, { x, y: y + h }]);
      setPolylineClosed(true);
    } else if (upper.startsWith('OFFSET')) {
      setOffsetDistance(nums[0] ?? offsetDistance);
      offsetSelectedEdge();
    } else if (upper.startsWith('AXIS_OFFSET') && nums.length >= 1) {
      setSupportAxisOffset(nums[0]);
    } else if (upper.startsWith('BASEMENT_OFFSET') && nums.length >= 1) {
      setBasementWallOffset(nums[0]);
    } else if (upper === 'CLOSE') {
      setPolylineClosed(true);
    } else if (upper === 'OPEN') {
      setPolylineClosed(false);
    } else if (upper.startsWith('FILLET') && selectedIndex !== undefined) {
      commitPoints(filletVertex(points, selectedIndex, nums[0] ?? 1.0));
    } else if (upper.startsWith('CHAMFER') && selectedIndex !== undefined) {
      commitPoints(chamferVertex(points, selectedIndex, nums[0] ?? 1.0));
    } else if (upper === 'REPAIR') {
      commitPoints(repairPolygon(points));
      setPolylineClosed(true);
    } else if ((upper.startsWith('RAMP') || upper.startsWith('ISLAND') || upper.startsWith('MUCK')) && nums.length >= 4) {
      const [x, y, w, h] = nums;
      const type = upper.startsWith('ISLAND') ? 'center_island' : upper.startsWith('MUCK') ? 'muck_out_opening' : 'ramp';
      addObstacle(type, { x, y }, w, h);
    } else if (nums.length >= 2) {
      commitPoints([...points, { x: nums[0], y: nums[1] }]);
    }
    setCommand('');
  }

  function addObstacle(type = activeObstacleType, center?: Point2D, width = 10, length = 18) {
    const c = center ?? centroid(points);
    setObstacles((items) => [...items, { name: type === 'center_island' ? '中心岛' : type === 'ramp' ? '坡道' : '障碍区', obstacleType: type, center: c, width, length, clearance: 1, active: true }]);
  }

  async function importDxf(file: File) {
    const text = await file.text();
    const parsed = parseDxfPolyline(text);
    if (parsed.length >= 3) { commitPoints(parsed); setPolylineClosed(true); setViewBox(initialViewBox(parsed)); setError(undefined); }
    else setError('DXF 未识别到有效 LINE/LWPOLYLINE 轮廓。');
  }

  function exportDxf() {
    const text = makeDxfPolyline(points, polylineClosed, obstacles);
    const blob = new Blob([text], { type: 'application/dxf' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${project.name || 'pitguard'}-excavation-outline.dxf`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const supportAxisPoints = useMemo(() => offsetClosedPolyline(points, supportAxisOffset), [points, supportAxisOffset]);
  const basementWallPoints = useMemo(() => offsetClosedPolyline(points, basementWallOffset), [points, basementWallOffset]);

  return (
    <div className="cadEditor">
      <div className="cadHeader">
        <div>
          <h2>基坑轮廓 CAD-like 编辑器</h2>
          <p className="small">常用操作保留在主工具条；DXF、偏距、倒角/圆角、障碍物和图层放入右侧配置抽屉，降低界面噪声。拖动点时背景网格和吸附参考线保持显示，便于水平/竖直对齐。</p>
        </div>
        <div className="cadStats"><span>面积 <strong>{polygonArea(points).toFixed(2)}</strong> m²</span><span>周长 <strong>{polygonPerimeter(points).toFixed(2)}</strong> m</span><span>点数 <strong>{points.length}</strong></span></div>
      </div>

      <div className="toolbar cadToolbar">
        <label>坑顶标高（m） <input type="number" value={topElevation} onChange={(e) => setTopElevation(Number(e.target.value))} /></label>
        <label>坑底标高（m） <input type="number" value={bottomElevation} onChange={(e) => setBottomElevation(Number(e.target.value))} /></label>
        <button className="secondary" onClick={() => commitPoints([...points, offsetNewPoint(points)])}>添加点</button>
        <button className="secondary" onClick={deleteSelected} disabled={selectedIndex === undefined || points.length <= 3}>删除点</button>
        <button className="secondary" onClick={undo} disabled={!history.length}>撤销</button>
        <button className="secondary" onClick={redo} disabled={!redoStack.length}>重做</button>
        <button className="secondary" onClick={() => setViewBox(initialViewBox(points))}>适配视图</button>
        <label><input type="checkbox" checked={snapEnabled} onChange={(e) => setSnapEnabled(e.target.checked)} /> 网格吸附</label>
        <label><input type="checkbox" checked={orthoEnabled} onChange={(e) => setOrthoEnabled(e.target.checked)} /> 正交约束</label>
        <button className="secondary" onClick={() => setAdvancedOpen(true)}>配置/高级</button>
        <button onClick={save}>保存并生成边段</button>
      </div>
      <div className="advancedHintChips" aria-label="高级工具入口提示">
        <span>导入 DXF</span>
        <span>选中边偏移</span>
        <span>添加障碍</span>
      </div>
      <div className="quickCommandBar">
        <label>快速命令 <input className="commandInput" value={command} placeholder="10,20 / RECT 0 0 60 30 / RAMP 30 15 10 20" onChange={(e) => setCommand(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') applyCommand(); }} /></label>
        <button className="secondary" onClick={applyCommand}>执行命令</button>
      </div>

      {advancedOpen && <div className="drawerBackdrop" onClick={() => setAdvancedOpen(false)}>
        <aside className="sideDrawer cadAdvancedDrawer" onClick={(e) => e.stopPropagation()}>
          <div className="drawerHeader"><h3>绘图配置 / 高级工具</h3><button className="secondary" onClick={() => setAdvancedOpen(false)}>关闭</button></div>
          <div className="drawerSection">
            <h4>定位与偏距</h4>
            <label><input type="checkbox" checked={explicitPlacement} onChange={(e) => setExplicitPlacement(e.target.checked)} /> 锁定当前绝对坐标；不自动居中到地质模型</label>
            <label>支护轴线偏距 <input type="number" value={supportAxisOffset} step="0.1" onChange={(e) => setSupportAxisOffset(Number(e.target.value))} /></label>
            <label>地下室外墙偏距 <input type="number" value={basementWallOffset} step="0.1" onChange={(e) => setBasementWallOffset(Number(e.target.value))} /></label>
          </div>
          <div className="drawerSection">
            <h4>DXF 与命令</h4>
            <label className="fileButton">导入 DXF<input type="file" accept=".dxf,text/plain" onChange={(e) => e.target.files?.[0] && importDxf(e.target.files[0])} /></label>
            <button className="secondary" onClick={exportDxf}>导出 DXF</button>
            <label>命令 <input className="commandInput" value={command} placeholder="10,20 / RECT 0 0 60 30 / RAMP 30 15 10 20" onChange={(e) => setCommand(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') applyCommand(); }} /></label>
            <button className="secondary" onClick={applyCommand}>执行命令</button>
          </div>
          <div className="drawerSection">
            <h4>编辑工具</h4>
            <button className="secondary" onClick={() => setPolylineClosed((v) => !v)}>{polylineClosed ? '断开多段线' : '闭合多段线'}</button>
            <label>偏移距离 <input type="number" value={offsetDistance} step="0.5" onChange={(e) => setOffsetDistance(Number(e.target.value))} /></label>
            <button className="secondary" onClick={offsetSelectedEdge} disabled={selectedIndex === undefined}>选中边偏移</button>
            <button className="secondary" onClick={() => selectedIndex !== undefined && commitPoints(chamferVertex(points, selectedIndex, offsetDistance || 1))} disabled={selectedIndex === undefined}>倒角</button>
            <button className="secondary" onClick={() => selectedIndex !== undefined && commitPoints(filletVertex(points, selectedIndex, offsetDistance || 1))} disabled={selectedIndex === undefined}>圆角</button>
            <button className="secondary" onClick={() => commitPoints(repairPolygon(points))}>修复轮廓</button>
          </div>
          <div className="drawerSection">
            <h4>障碍物与图层</h4>
            <label>障碍物 <select value={activeObstacleType} onChange={(e) => setActiveObstacleType(e.target.value as typeof activeObstacleType)}><option value="ramp">坡道</option><option value="muck_out_opening">出土口</option><option value="center_island">中心岛</option><option value="basement_column_grid">柱网</option></select></label>
            <button className="secondary" onClick={() => addObstacle()}>添加障碍</button>
            {(Object.entries(layers) as [DrawLayerKey, boolean][]).map(([key, value]) => <label key={key}><input type="checkbox" checked={value} onChange={(e) => setLayers((prev) => ({ ...prev, [key]: e.target.checked }))} /> 图层:{layerText(key)}</label>)}
          </div>
        </aside>
      </div>}
      {error && <div className="error">{error}</div>}
      {validation.errors.length > 0 && <div className="error">{validation.errors.join('；')}</div>}
      {validation.warnings.length > 0 && <div className="warning">{validation.warnings.join('；')}</div>}

      <div className="cadLayout">
        <svg ref={svgRef} className="cadCanvas" viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onWheel={onWheel}>
          {layers.grid && <g className="cadGrid">{gridLines.x.map((x) => <line key={`x-${x}`} x1={x} y1={viewBox.y} x2={x} y2={viewBox.y + viewBox.h} />)}{gridLines.y.map((y) => <line key={`y-${y}`} x1={viewBox.x} y1={y} x2={viewBox.x + viewBox.w} y2={y} />)}</g>}
          {layers.axis && <g className="cadAxis"><line x1={viewBox.x} y1={0} x2={viewBox.x + viewBox.w} y2={0} /><line x1={0} y1={viewBox.y} x2={0} y2={viewBox.y + viewBox.h} /></g>}
          {drag?.type === 'point' && <g className="cadDragGuide"><line x1={points[drag.index].x} y1={viewBox.y} x2={points[drag.index].x} y2={viewBox.y + viewBox.h} /><line x1={viewBox.x} y1={points[drag.index].y} x2={viewBox.x + viewBox.w} y2={points[drag.index].y} /></g>}
          {layers.outline && polylineClosed ? <polygon points={pointString} className="cadPolygon" /> : <polyline points={pointString} className="cadPolygon" />}
          {layers.outline && supportAxisOffset !== 0 && supportAxisPoints.length >= 3 && <polygon points={supportAxisPoints.map((p) => `${p.x},${p.y}`).join(' ')} className="cadSupportAxis" />}
          {layers.outline && basementWallOffset !== 0 && basementWallPoints.length >= 3 && <polygon points={basementWallPoints.map((p) => `${p.x},${p.y}`).join(' ')} className="cadBasementWall" />}
          {points.map((point, index) => {
            if (!polylineClosed && index === points.length - 1) return null;
            const next = points[(index + 1) % points.length];
            const mid = { x: (point.x + next.x) / 2, y: (point.y + next.y) / 2 };
            return <g key={`edge-${index}`}><line className={`cadEdge ${selectedIndex === index ? 'selectedEdge' : ''}`} x1={point.x} y1={point.y} x2={next.x} y2={next.y} onClick={() => setSelectedIndex(index)} /><circle className="cadInsertHandle" cx={mid.x} cy={mid.y} r={viewBox.w / 180} onClick={(e) => { e.stopPropagation(); insertPointOnEdge(index); }}><title>插入点</title></circle></g>;
          })}
          {layers.dimensions && dimensions.map((d, i) => {
            if (!polylineClosed && i === points.length - 1) return null;
            const mid = { x: (d.a.x + d.b.x) / 2, y: (d.a.y + d.b.y) / 2 };
            return <text key={`dim-${i}`} className="cadDimension" x={mid.x + viewBox.w / 120} y={mid.y - viewBox.h / 120}>{d.length.toFixed(2)}m</text>;
          })}
          {layers.obstacles && obstacles.map((obs, idx) => <g key={`obs-${idx}`} className="cadObstacle"><rect x={(obs.center?.x ?? 0) - (obs.width ?? 1) / 2} y={(obs.center?.y ?? 0) - (obs.length ?? 1) / 2} width={obs.width ?? 1} height={obs.length ?? 1} /><text x={(obs.center?.x ?? 0) + 0.5} y={obs.center?.y ?? 0}>{obs.name}</text></g>)}
          {points.map((point, index) => <g key={index}><circle className={`cadPoint ${selectedIndex === index ? 'selected' : ''}`} cx={point.x} cy={point.y} r={viewBox.w / 110} onPointerDown={(event) => { event.stopPropagation(); (event.target as SVGCircleElement).setPointerCapture(event.pointerId); setSelectedIndex(index); setHistory((items) => [...items.slice(-40), points]); setDrag({ type: 'point', index }); }} /><text className="cadPointLabel" x={point.x + viewBox.w / 100} y={point.y - viewBox.h / 100}>{index + 1}</text></g>)}
        </svg>

        <div className="cadSidePanel">
          <h3>点坐标</h3>
          <table className="table compactTable"><thead><tr><th>#</th><th>x</th><th>y</th></tr></thead><tbody>{points.map((point, index) => <tr key={index} className={selectedIndex === index ? 'selectedRow' : ''} onClick={() => setSelectedIndex(index)}><td>{index + 1}</td><td><input type="number" value={point.x} onChange={(event) => updatePoint(index, 'x', Number(event.target.value))} /></td><td><input type="number" value={point.y} onChange={(event) => updatePoint(index, 'y', Number(event.target.value))} /></td></tr>)}</tbody></table>
          <h3>障碍物/施工保留区</h3>
          <table className="table compactTable"><thead><tr><th>类型</th><th>中心</th><th>尺寸</th><th></th></tr></thead><tbody>{obstacles.map((o, i) => <tr key={i}><td>{o.obstacleType}</td><td>{o.center ? `${o.center.x},${o.center.y}` : '-'}</td><td>{o.width}×{o.length}</td><td><button className="secondary" onClick={() => setObstacles((items) => items.filter((_, idx) => idx !== i))}>删除</button></td></tr>)}</tbody></table>
          <h3>工程图层/偏距</h3>
          <div className="small">支护轴线偏距：{supportAxisOffset}m；地下室外墙偏距：{basementWallOffset}m。{explicitPlacement ? '已锁定绝对坐标。' : '未锁定坐标：保存后优先与地质模型中心对齐。'}</div>
          {project.excavation?.placementNote && <div className="small"><strong>放置说明：</strong>{project.excavation.placementNote}</div>}
          {project.excavation && <div className="small"><strong>已生成边段</strong><br />{project.excavation.segments.map((s) => `${s.name}(${s.length.toFixed(1)}m)`).join('、')}</div>}
        </div>
      </div>
    </div>
  );
}

function initialViewBox(points: Point2D[]): ViewBox {
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const w = Math.max(20, maxX - minX), h = Math.max(16, maxY - minY), pad = Math.max(w, h) * 0.15;
  return { x: minX - pad, y: minY - pad, w: w + 2 * pad, h: h + 2 * pad };
}
function makeGridLines(viewBox: ViewBox) { const step = viewBox.w > 160 ? 10 : viewBox.w > 70 ? 5 : 2; const xs: number[] = []; const ys: number[] = []; for (let x = Math.floor(viewBox.x / step) * step; x <= viewBox.x + viewBox.w; x += step) xs.push(x); for (let y = Math.floor(viewBox.y / step) * step; y <= viewBox.y + viewBox.h; y += step) ys.push(y); return { x: xs, y: ys }; }
function snapPoint(point: Point2D, step: number): Point2D { return { x: round3(Math.round(point.x / step) * step), y: round3(Math.round(point.y / step) * step) }; }
function round3(value: number) { return Math.round(value * 1000) / 1000; }
function roundPoint(point: Point2D): Point2D { return { x: round3(point.x), y: round3(point.y) }; }
function clamp(value: number, min: number, max: number) { return Math.max(min, Math.min(max, value)); }
function offsetNewPoint(points: Point2D[]): Point2D { const p = points[points.length - 1] ?? { x: 0, y: 0 }; return { x: p.x + 5, y: p.y + 5 }; }
function centroid(points: Point2D[]): Point2D { const n = Math.max(points.length, 1); return { x: round3(points.reduce((s, p) => s + p.x, 0) / n), y: round3(points.reduce((s, p) => s + p.y, 0) / n) }; }
function layerText(key: DrawLayerKey) { return { grid: '网格', outline: '轮廓', dimensions: '尺寸', obstacles: '障碍', axis: '坐标轴' }[key]; }

function validateOutline(points: Point2D[], topElevation: number, bottomElevation: number, closed: boolean): { errors: string[]; warnings: string[] } {
  const errors: string[] = [], warnings: string[] = [];
  if (points.length < 3) errors.push('至少需要 3 个轮廓点');
  if (!closed) errors.push('基坑轮廓必须闭合后才能保存');
  if (bottomElevation >= topElevation) errors.push('坑底标高必须低于坑顶标高');
  const shortEdges = points.map((p, i) => Math.hypot(points[(i + 1) % points.length].x - p.x, points[(i + 1) % points.length].y - p.y)).filter((l) => l < 0.5);
  if (shortEdges.length) errors.push('存在小于 0.5m 的短边');
  if (selfIntersects(points)) errors.push('轮廓存在自交，请调整点序或删除交叉边');
  if (polygonArea(points) < 1) warnings.push('基坑面积过小，可能不是有效工程轮廓');
  return { errors, warnings };
}
function selfIntersects(points: Point2D[]) { for (let i = 0; i < points.length; i += 1) { const a1 = points[i], a2 = points[(i + 1) % points.length]; for (let j = i + 1; j < points.length; j += 1) { if (Math.abs(i - j) <= 1 || (i === 0 && j === points.length - 1)) continue; const b1 = points[j], b2 = points[(j + 1) % points.length]; if (segmentsIntersect(a1, a2, b1, b2)) return true; } } return false; }
function segmentsIntersect(a: Point2D, b: Point2D, c: Point2D, d: Point2D) { const ccw = (p1: Point2D, p2: Point2D, p3: Point2D) => (p3.y - p1.y) * (p2.x - p1.x) > (p2.y - p1.y) * (p3.x - p1.x); return ccw(a, c, d) !== ccw(b, c, d) && ccw(a, b, c) !== ccw(a, b, d); }

function parseDxfPolyline(text: string): Point2D[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim());
  const points: Point2D[] = [];
  for (let i = 0; i < lines.length - 1; i += 1) {
    if (lines[i] === 'LWPOLYLINE') {
      const poly: Point2D[] = [];
      for (let j = i + 1; j < lines.length - 1 && lines[j] !== 'ENDSEC' && lines[j] !== 'LINE' && lines[j] !== 'LWPOLYLINE'; j += 2) {
        if (lines[j] === '10') {
          const x = Number(lines[j + 1]);
          let y = 0;
          for (let k = j + 2; k < Math.min(j + 8, lines.length - 1); k += 2) if (lines[k] === '20') y = Number(lines[k + 1]);
          if (Number.isFinite(x) && Number.isFinite(y)) poly.push({ x, y });
        }
      }
      if (poly.length >= 3) return poly.map(roundPoint);
    }
    if (lines[i] === 'LINE') {
      let x1: number | undefined, y1: number | undefined, x2: number | undefined, y2: number | undefined;
      for (let j = i + 1; j < Math.min(i + 22, lines.length - 1); j += 2) {
        if (lines[j] === '10') x1 = Number(lines[j + 1]);
        if (lines[j] === '20') y1 = Number(lines[j + 1]);
        if (lines[j] === '11') x2 = Number(lines[j + 1]);
        if (lines[j] === '21') y2 = Number(lines[j + 1]);
      }
      if ([x1, y1, x2, y2].every((v) => Number.isFinite(v))) {
        if (!points.length || Math.hypot(points[points.length - 1].x - (x1 as number), points[points.length - 1].y - (y1 as number)) > 1e-6) points.push({ x: x1 as number, y: y1 as number });
        points.push({ x: x2 as number, y: y2 as number });
      }
    }
  }
  return points.length >= 3 ? points.map(roundPoint) : [];
}

function offsetClosedPolyline(points: Point2D[], offset: number): Point2D[] {
  if (!offset || points.length < 3) return [];
  const ccw = polygonSignedArea(points) > 0;
  const result: Point2D[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const prev = points[(i - 1 + points.length) % points.length];
    const curr = points[i];
    const next = points[(i + 1) % points.length];
    const line1 = offsetLine(prev, curr, offset, ccw);
    const line2 = offsetLine(curr, next, offset, ccw);
    const hit = lineIntersection(line1.a, line1.b, line2.a, line2.b);
    result.push(roundPoint(hit ?? { x: curr.x + line1.nx * offset, y: curr.y + line1.ny * offset }));
  }
  return selfIntersects(result) ? offsetByCentroidFallback(points, offset) : result;
}
function polygonSignedArea(points: Point2D[]): number { let a = 0; for (let i = 0; i < points.length; i += 1) { const p = points[i], q = points[(i + 1) % points.length]; a += p.x * q.y - q.x * p.y; } return a / 2; }
function offsetLine(a: Point2D, b: Point2D, offset: number, ccw: boolean) { const dx = b.x - a.x, dy = b.y - a.y; const len = Math.hypot(dx, dy) || 1; const sign = ccw ? -1 : 1; const nx = sign * -dy / len, ny = sign * dx / len; return { a: { x: a.x + nx * offset, y: a.y + ny * offset }, b: { x: b.x + nx * offset, y: b.y + ny * offset }, nx, ny }; }
function lineIntersection(a1: Point2D, a2: Point2D, b1: Point2D, b2: Point2D): Point2D | undefined { const dax = a2.x - a1.x, day = a2.y - a1.y, dbx = b2.x - b1.x, dby = b2.y - b1.y; const den = dax * dby - day * dbx; if (Math.abs(den) < 1e-9) return undefined; const t = ((b1.x - a1.x) * dby - (b1.y - a1.y) * dbx) / den; return { x: a1.x + t * dax, y: a1.y + t * day }; }
function offsetByCentroidFallback(points: Point2D[], offset: number): Point2D[] { const c = centroid(points); return points.map((p) => { const vx = p.x - c.x; const vy = p.y - c.y; const len = Math.hypot(vx, vy) || 1; return roundPoint({ x: p.x + (vx / len) * offset, y: p.y + (vy / len) * offset }); }); }
function chamferVertex(points: Point2D[], index: number, dist: number): Point2D[] { if (points.length < 3) return points; const prev = points[(index - 1 + points.length) % points.length], curr = points[index], next = points[(index + 1) % points.length]; const a = pointToward(curr, prev, dist); const b = pointToward(curr, next, dist); return [...points.slice(0, index), a, b, ...points.slice(index + 1)].map(roundPoint); }
function filletVertex(points: Point2D[], index: number, radius: number): Point2D[] { if (points.length < 3) return points; const chamfered = chamferVertex(points, index, radius); const insertAt = index + 1; if (insertAt <= 0 || insertAt >= chamfered.length) return chamfered; const mid = pointToward(chamfered[index], chamfered[insertAt], Math.hypot(chamfered[insertAt].x - chamfered[index].x, chamfered[insertAt].y - chamfered[index].y) / 2); return [...chamfered.slice(0, insertAt), mid, ...chamfered.slice(insertAt)].map(roundPoint); }
function pointToward(a: Point2D, b: Point2D, dist: number): Point2D { const len = Math.hypot(b.x - a.x, b.y - a.y) || 1; const d = Math.min(Math.max(dist, 0.1), len / 2); return { x: a.x + (b.x - a.x) / len * d, y: a.y + (b.y - a.y) / len * d }; }
function repairPolygon(points: Point2D[]): Point2D[] { const unique = points.filter((p, i) => i === 0 || Math.hypot(p.x - points[i - 1].x, p.y - points[i - 1].y) > 1e-6); if (!selfIntersects(unique)) return unique.map(roundPoint); const c = centroid(unique); return [...unique].sort((a, b) => Math.atan2(a.y - c.y, a.x - c.x) - Math.atan2(b.y - c.y, b.x - c.x)).map(roundPoint); }

function makeDxfPolyline(points: Point2D[], closed: boolean, obstacles: ConstructionObstacle[]): string {
  const rows: string[] = ['0','SECTION','2','HEADER','0','ENDSEC','0','SECTION','2','TABLES','0','TABLE','2','LAYER','0','LAYER','2','EXCAVATION_OUTLINE','70','0','62','7','6','CONTINUOUS','0','LAYER','2','CONSTRUCTION_OBSTACLE','70','0','62','1','6','CONTINUOUS','0','ENDTAB','0','ENDSEC','0','SECTION','2','ENTITIES'];
  rows.push('0','LWPOLYLINE','8','EXCAVATION_OUTLINE','90',String(points.length),'70',closed ? '1' : '0');
  points.forEach((p) => rows.push('10',String(p.x),'20',String(p.y)));
  obstacles.forEach((obs) => {
    if (!obs.center || !obs.width || !obs.length) return;
    const x = obs.center.x - obs.width / 2; const y = obs.center.y - obs.length / 2;
    const rect = [{x,y},{x:x+obs.width,y},{x:x+obs.width,y:y+obs.length},{x,y:y+obs.length}];
    rows.push('0','LWPOLYLINE','8','CONSTRUCTION_OBSTACLE','90','4','70','1');
    rect.forEach((p) => rows.push('10',String(round3(p.x)),'20',String(round3(p.y))));
  });
  rows.push('0','ENDSEC','0','EOF');
  return rows.join('\n');
}
