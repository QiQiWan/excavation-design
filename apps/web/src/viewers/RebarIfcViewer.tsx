import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { api } from '../api/client';
import type { Project, RebarIfcVisualization, RebarVisualizationBar } from '../types/domain';

type HostFilter = 'all' | 'diaphragm_wall' | 'wale_or_crown_beam' | 'internal_support' | 'support_wale_node';
type BarFilter = 'all' | 'longitudinal' | 'distribution' | 'stirrup' | 'tie' | 'additional';

function pointToVector(p: { x: number; y: number; z: number }): THREE.Vector3 {
  return new THREE.Vector3(p.x, p.z, p.y);
}

function barColor(bar: RebarVisualizationBar): number {
  if (bar.checkStatus === 'fail') return 0xdc2626;
  if (bar.checkStatus === 'warning' || bar.checkStatus === 'manual_review') return 0xf59e0b;
  if (bar.barType === 'longitudinal') return 0x2563eb;
  if (bar.barType === 'distribution') return 0x0d9488;
  if (bar.barType === 'stirrup') return 0x9333ea;
  if (bar.barType === 'tie') return 0x64748b;
  return 0xea580c;
}

function addCylinderBetween(scene: THREE.Scene, start: THREE.Vector3, end: THREE.Vector3, radius: number, color: number, info: Record<string, unknown>, pickables: THREE.Object3D[], clippingPlanes: THREE.Plane[]) {
  const delta = new THREE.Vector3().subVectors(end, start);
  const length = delta.length();
  if (length <= 1e-6) return;
  const geometry = new THREE.CylinderGeometry(radius, radius, length, 8, 1, false);
  const material = new THREE.MeshStandardMaterial({ color, metalness: 0.25, roughness: 0.48, clippingPlanes });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.copy(start).add(end).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.clone().normalize());
  mesh.userData.info = info;
  scene.add(mesh);
  pickables.push(mesh);
}

function boundsFromBars(bars: RebarVisualizationBar[], project: Project) {
  const xs: number[] = [];
  const ys: number[] = [];
  const zs: number[] = [];
  bars.forEach((bar) => {
    [bar.start, bar.end].forEach((p) => { xs.push(p.x); ys.push(p.y); zs.push(p.z); });
  });
  project.excavation?.outline.points.forEach((p) => { xs.push(p.x); ys.push(p.y); });
  if (!xs.length) { xs.push(0, 60); ys.push(0, 30); zs.push(-18, 0); }
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const minZ = Math.min(...zs, project.excavation?.bottomElevation ?? -12); const maxZ = Math.max(...zs, project.excavation?.topElevation ?? 0);
  const size = Math.max(maxX - minX, maxY - minY, Math.max(maxZ - minZ, 1) * 1.8, 20);
  return { center: new THREE.Vector3((minX + maxX) / 2, (minZ + maxZ) / 2, (minY + maxY) / 2), size, minX, maxX, minY, maxY, minZ, maxZ };
}

function humanInfo(info: Record<string, unknown>): [string, string][] {
  const labels: Record<string, string> = {
    ifcClass: 'IFC 类', hostType: '宿主类型', hostCode: '宿主编号', groupName: '钢筋组', barType: '钢筋类型', diameterMm: '直径(mm)', spacingMm: '间距(mm)', count: '数量', grade: '钢筋等级', lengthM: '长度(m)', representation: '表达方式', checkStatus: '状态', estimatedFullCount: '估算完整数量', sampledFromCount: '当前采样数量', locationDescription: '位置说明'
  };
  return Object.entries(info).map(([key, value]) => [labels[key] ?? key, String(value ?? '-')]);
}

export default function RebarIfcViewer({ project, highlightLocator }: { project: Project; highlightLocator?: Record<string, unknown> }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [data, setData] = useState<RebarIfcVisualization | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [hostFilter, setHostFilter] = useState<HostFilter>('all');
  const [barFilter, setBarFilter] = useState<BarFilter>('all');
  const [showHosts, setShowHosts] = useState(true);
  const [clip, setClip] = useState(false);
  const [clipOffset, setClipOffset] = useState(0);
  const [selected, setSelected] = useState<Record<string, unknown> | undefined>();

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(undefined);
    api.getRebarIfcVisualization(project.id, 950)
      .then((result) => { if (alive) setData(result); })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project.id, project.retainingSystem?.diaphragmWalls.length, project.retainingSystem?.supports.length, project.calculationResults.length]);

  const bars = useMemo(() => (data?.bars ?? []).filter((bar) => (hostFilter === 'all' || bar.hostType === hostFilter) && (barFilter === 'all' || bar.barType === barFilter)), [data, hostFilter, barFilter]);
  const maxDia = useMemo(() => Math.max(8, ...bars.map((bar) => Number(bar.diameterMm || 0))), [bars]);
  const highlightId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !data) return;
    mount.innerHTML = '';
    const width = Math.max(mount.clientWidth, 640);
    const height = Math.max(mount.clientHeight, 460);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf8fafc);
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 8000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.localClippingEnabled = clip;
    mount.appendChild(renderer.domElement);
    const bbox = boundsFromBars(bars, project);
    const clipPlane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), bbox.center.x + clipOffset);
    const clippingPlanes = clip ? [clipPlane] : [];
    const pickables: THREE.Object3D[] = [];

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const light = new THREE.DirectionalLight(0xffffff, 0.9);
    light.position.set(bbox.center.x + bbox.size, bbox.center.y + bbox.size, bbox.center.z + bbox.size);
    scene.add(light);
    const grid = new THREE.GridHelper(Math.max(bbox.size * 1.4, 20), 20, 0xcbd5e1, 0xe2e8f0);
    grid.position.set(bbox.center.x, project.excavation?.bottomElevation ?? bbox.minZ, bbox.center.z);
    scene.add(grid);

    if (showHosts && project.retainingSystem) {
      project.retainingSystem.diaphragmWalls.forEach((wall) => {
        const a = wall.axis.points[0]; const b = wall.axis.points[wall.axis.points.length - 1];
        if (!a || !b) return;
        const length = Math.hypot(b.x - a.x, b.y - a.y);
        const h = wall.topElevation - wall.bottomElevation;
        const mesh = new THREE.Mesh(
          new THREE.BoxGeometry(Math.max(length, 0.1), Math.max(h, 0.1), Math.max(wall.thickness, 0.1)),
          new THREE.MeshStandardMaterial({ color: 0x94a3b8, transparent: true, opacity: 0.18, clippingPlanes })
        );
        mesh.position.set((a.x + b.x) / 2, wall.bottomElevation + h / 2, (a.y + b.y) / 2);
        mesh.rotation.y = -Math.atan2(b.y - a.y, b.x - a.x);
        scene.add(mesh);
      });
      [...project.retainingSystem.crownBeams, ...project.retainingSystem.waleBeams, ...(project.retainingSystem.ringBeams ?? []), ...project.retainingSystem.supports].forEach((beam: any) => {
        const a = beam.axis?.points?.[0] ?? beam.start;
        const b = beam.axis?.points?.[beam.axis?.points?.length - 1] ?? beam.end;
        if (!a || !b) return;
        const length = Math.hypot(b.x - a.x, b.y - a.y);
        const widthBeam = beam.section?.width ?? 0.8;
        const heightBeam = beam.section?.height ?? 0.8;
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(Math.max(length, 0.1), heightBeam, widthBeam), new THREE.MeshStandardMaterial({ color: 0x0f172a, transparent: true, opacity: 0.12, clippingPlanes }));
        mesh.position.set((a.x + b.x) / 2, beam.elevation ?? 0, (a.y + b.y) / 2);
        mesh.rotation.y = -Math.atan2(b.y - a.y, b.x - a.x);
        scene.add(mesh);
      });
    }

    bars.forEach((bar) => {
      const highlighted = Boolean(highlightId && (highlightId === bar.hostId || highlightId === bar.hostCode || highlightId === bar.id || highlightId === bar.groupId));
      const radius = highlighted ? Math.max(0.05, Math.min(0.14, (bar.diameterMm / maxDia) * 0.11)) : Math.max(0.018, Math.min(0.09, (bar.diameterMm / maxDia) * 0.06));
      addCylinderBetween(scene, pointToVector(bar.start), pointToVector(bar.end), radius, highlighted ? 0xeab308 : barColor(bar), { ...(bar as unknown as Record<string, unknown>), highlighted }, pickables, clippingPlanes);
    });

    let theta = Math.PI / 4;
    let phi = Math.PI / 3.2;
    let radius = Math.max(bbox.size * 1.7, 30);
    const target = bbox.center.clone();
    const updateCamera = () => {
      phi = Math.max(0.12, Math.min(Math.PI / 2.05, phi));
      camera.position.set(target.x + radius * Math.sin(phi) * Math.cos(theta), target.y + radius * Math.cos(phi), target.z + radius * Math.sin(phi) * Math.sin(theta));
      camera.lookAt(target);
    };
    updateCamera();
    let dragging = false; let moved = false; let lastX = 0; let lastY = 0;
    const onPointerDown = (event: PointerEvent) => { dragging = true; moved = false; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); };
    const onPointerMove = (event: PointerEvent) => {
      if (!dragging) return;
      const dx = event.clientX - lastX; const dy = event.clientY - lastY;
      moved = moved || Math.abs(dx) + Math.abs(dy) > 3; lastX = event.clientX; lastY = event.clientY;
      if (event.shiftKey) {
        const panScale = radius / 740;
        const right = new THREE.Vector3().subVectors(camera.position, target).cross(camera.up).normalize();
        const up = camera.up.clone().normalize();
        target.addScaledVector(right, -dx * panScale).addScaledVector(up, dy * panScale);
      } else { theta -= dx * 0.006; phi -= dy * 0.006; }
      updateCamera();
    };
    const onPointerUp = (event: PointerEvent) => {
      dragging = false; renderer.domElement.releasePointerCapture(event.pointerId);
      if (moved) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      const raycaster = new THREE.Raycaster(); raycaster.setFromCamera(mouse, camera);
      const hit = raycaster.intersectObjects(pickables, true)[0];
      setSelected(hit?.object?.userData?.info);
    };
    const onWheel = (event: WheelEvent) => { event.preventDefault(); radius *= event.deltaY > 0 ? 1.08 : 0.92; radius = Math.max(3, Math.min(5000, radius)); updateCamera(); };
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false });
    const resizeObserver = new ResizeObserver(() => {
      const w = Math.max(mount.clientWidth, 640); const h = Math.max(mount.clientHeight, 460);
      renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
    });
    resizeObserver.observe(mount);
    let raf = 0;
    const animate = () => { raf = requestAnimationFrame(animate); renderer.render(scene, camera); };
    animate();
    return () => {
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('wheel', onWheel);
      renderer.dispose();
      scene.traverse((object: THREE.Object3D) => {
        const mesh = object as THREE.Mesh;
        mesh.geometry?.dispose?.();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((m) => m.dispose()); else material?.dispose?.();
      });
      mount.innerHTML = '';
    };
  }, [data, bars, showHosts, clip, clipOffset, project, maxDia, highlightId]);

  return (
    <section className="summaryPanel rebarIfcPanel">
      <div className="viewerHeader">
        <div>
          <h3>钢筋级 IFC 可视化</h3>
          <p className="small">从后端 IFC 钢筋表达数据生成浏览器内 3D 预览。蓝=纵筋，青=分布筋，紫=箍筋，橙=附加筋/需复核，红=失败项。</p>
        </div>
        <div className="viewerStats">
          <span>采样 {data?.summary.sampledBarCount ?? 0}</span>
          <span>估算完整 {data?.summary.estimatedFullBarCount ?? 0}</span>
          <span>宿主 {data?.summary.hostCount ?? 0}</span>
          <span>钢量代理 {data?.summary.steelMassProxyKg ?? '-'} kg</span>
        </div>
      </div>
      {loading && <div className="operationPanel compactOperation"><div className="operationHeader"><strong>正在读取钢筋 IFC 可视化数据</strong><span>生成采样钢筋、IFC 映射和钢筋组摘要。</span></div><div className="operationBar"><em style={{ width: '48%' }} /></div></div>}
      {error && <div className="error">{error}</div>}
      {data && <>
        <div className="rebarIfcMapping">
          <div><strong>design_detailed.ifc</strong><span>{data.exportProfileMapping.designDetailed}</span></div>
          <div><strong>construction_visual.ifc</strong><span>{data.exportProfileMapping.constructionVisual}</span></div>
          <div><strong>coordination_light.ifc</strong><span>{data.exportProfileMapping.coordinationLight}</span></div>
        </div>
        <div className="viewerControls rebarControls">
          <label>宿主 <select value={hostFilter} onChange={(event) => setHostFilter(event.target.value as HostFilter)}><option value="all">全部</option><option value="diaphragm_wall">地连墙</option><option value="wale_or_crown_beam">冠梁/围檩</option><option value="internal_support">水平支撑</option><option value="support_wale_node">节点</option></select></label>
          <label>钢筋 <select value={barFilter} onChange={(event) => setBarFilter(event.target.value as BarFilter)}><option value="all">全部</option><option value="longitudinal">纵筋</option><option value="distribution">分布筋</option><option value="stirrup">箍筋</option><option value="tie">拉结筋</option><option value="additional">附加筋</option></select></label>
          <label><input type="checkbox" checked={showHosts} onChange={(event) => setShowHosts(event.target.checked)} /> 显示透明宿主构件</label>
          <label><input type="checkbox" checked={clip} onChange={(event) => setClip(event.target.checked)} /> X 向剖切</label>
          <label>剖切位置 <input type="range" min="-100" max="120" step="1" value={clipOffset} onChange={(event) => setClipOffset(Number(event.target.value))} /></label>
          <button className="secondary" onClick={() => api.getRebarIfcVisualization(project.id, 950).then(setData).catch((err) => setError(err instanceof Error ? err.message : String(err)))}>刷新钢筋数据</button>
        </div>
        {highlightId && <div className="locatorHint">当前定位对象：{highlightId}；匹配宿主构件或钢筋组时会以金色加粗显示。</div>}
        <div className="rebarLegend"><span className="rbMain">纵筋</span><span className="rbDist">分布筋</span><span className="rbStirrup">箍筋</span><span className="rbAdd">附加/需复核</span><span className="rbFail">Fail</span></div>
        <div className="rebarViewport" ref={mountRef} />
        <div className="stepGrid rebarBottomGrid">
          <div className="propertyPanel"><strong>钢筋属性</strong>{selected ? <table className="table compactTable"><tbody>{humanInfo(selected).map(([key, value]) => <tr key={key}><td>{key}</td><td>{String(value ?? '-')}</td></tr>)}</tbody></table> : <span className="small">点击任意钢筋查看 IFC 类、宿主构件、钢筋组、间距和状态。</span>}</div>
          <div className="summaryPanel miniPanel"><h4>钢筋组摘要</h4><table className="table compactTable"><thead><tr><th>类型</th><th>数量</th></tr></thead><tbody>{Object.entries(data.summary.byBarType).map(([key, value]) => <tr key={key}><td>{key}</td><td>{value}</td></tr>)}</tbody></table></div>
          <div className="summaryPanel miniPanel"><h4>当前限制</h4><p className="small">{data.summary.officialDetailingLimit}</p><ul className="small">{data.notes.map((note) => <li key={note}>{note}</li>)}</ul></div>
        </div>
      </>}
    </section>
  );
}
