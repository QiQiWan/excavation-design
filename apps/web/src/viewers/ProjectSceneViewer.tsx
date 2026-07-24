import { useEffect, useMemo, useRef, useState } from 'react';
import { AmbientLight, AxesHelper, Box3, BoxGeometry, BufferGeometry, Color, CylinderGeometry, DirectionalLight, DoubleSide, Float32BufferAttribute, GridHelper, Group, Intersection, Line, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, MeshLambertMaterial, MeshStandardMaterial, Object3D, PerspectiveCamera, Plane, Points, PointsMaterial, Raycaster, Scene, Vector2, Vector3, WebGLRenderer } from 'three';
import type { ExcavationSegment, Point2D, Project, VtuMesh } from '../types/domain';
import { effectiveGeologicalSurfaces } from '../utils/geology';
import FullscreenShell from '../components/FullscreenShell';
import { bindWebglContextLifecycle, createStableWebGLRenderer, releaseStableWebGLRenderer, stablePixelRatio, startStableRenderLoop } from './webglRuntime';

type LayerKey = 'boreholes' | 'surfaces' | 'vtu' | 'excavation' | 'walls' | 'supports' | 'results';

type LayerState = Record<LayerKey, boolean>;

const DEFAULT_LAYERS: LayerState = {
  boreholes: true,
  surfaces: true,
  vtu: true,
  excavation: true,
  walls: true,
  supports: true,
  results: true,
};

const STRATUM_COLORS = ['#93c5fd', '#86efac', '#fde68a', '#fca5a5', '#c4b5fd', '#67e8f9', '#fdba74', '#d9f99d'];
const VTK_CELL_NODE_LIMIT: Record<number, number> = { 1: 1, 3: 2, 5: 3, 9: 4, 10: 4, 12: 8, 13: 6, 14: 5 };

function hashColor(code: string | number | undefined): string {
  const text = String(code ?? 'unknown');
  let sum = 0;
  for (let i = 0; i < text.length; i += 1) sum += text.charCodeAt(i) * (i + 1);
  return STRATUM_COLORS[sum % STRATUM_COLORS.length];
}

function toScenePoint(x: number, y: number, elevation: number): Vector3 {
  return new Vector3(x, elevation, y);
}

function segmentAngle(segment: Pick<ExcavationSegment, 'start' | 'end'>): number {
  return Math.atan2(segment.end.y - segment.start.y, segment.end.x - segment.start.x);
}

function projectBounds(project: Project): { center: Vector3; radius: number } {
  const points: Vector3[] = [];
  project.boreholes.forEach((bh) => {
    points.push(toScenePoint(bh.x, bh.y, bh.collarElevation));
    points.push(toScenePoint(bh.x, bh.y, bh.collarElevation - bh.depth));
  });
  effectiveGeologicalSurfaces(project).forEach((surface) => {
    surface.grid.xValues.forEach((x, ix) => surface.grid.yValues.forEach((y, iy) => {
      points.push(toScenePoint(x, y, surface.grid.zValues[iy]?.[ix] ?? 0));
    }));
  });
  project.excavation?.outline.points.forEach((p) => {
    points.push(toScenePoint(p.x, p.y, project.excavation?.topElevation ?? 0));
    points.push(toScenePoint(p.x, p.y, project.excavation?.bottomElevation ?? -10));
  });
  project.geologicalModel?.vtuMesh?.points?.forEach((p) => points.push(toScenePoint(p[0] ?? 0, p[1] ?? 0, p[2] ?? 0)));
  if (!points.length) return { center: new Vector3(35, -8, 20), radius: 70 };
  const box = new Box3().setFromPoints(points);
  const center = box.getCenter(new Vector3());
  const size = box.getSize(new Vector3());
  const radius = Math.max(size.x, size.y, size.z, 20) * 1.3;
  return { center, radius };
}

function addPolyline(scene: Scene, points: Vector3[], color: string, name: string, type: string, close = false) {
  if (points.length < 2) return;
  const linePoints = close ? [...points, points[0]] : points;
  const geometry = new BufferGeometry().setFromPoints(linePoints);
  const material = new LineBasicMaterial({ color });
  const line = new Line(geometry, material);
  line.userData = { name, type };
  scene.add(line);
}

function createPanelMesh(segment: Pick<ExcavationSegment, 'start' | 'end'>, topElevation: number, bottomElevation: number, thickness: number, color: string, name: string, type: string) {
  const length = Math.hypot(segment.end.x - segment.start.x, segment.end.y - segment.start.y);
  const height = Math.abs(topElevation - bottomElevation);
  const geometry = new BoxGeometry(length, height, thickness);
  const material = new MeshStandardMaterial({ color, transparent: true, opacity: 0.62, roughness: 0.7 });
  const mesh = new Mesh(geometry, material);
  mesh.position.copy(toScenePoint((segment.start.x + segment.end.x) / 2, (segment.start.y + segment.end.y) / 2, (topElevation + bottomElevation) / 2));
  mesh.rotation.y = -segmentAngle(segment);
  mesh.userData = { name, type, topElevation, bottomElevation, thickness };
  return mesh;
}

function createCylinderBetween(start: Vector3, end: Vector3, radius: number, color: string, name: string, type: string, extraData: Record<string, unknown> = {}) {
  const direction = new Vector3().subVectors(end, start);
  const length = direction.length();
  const geometry = new CylinderGeometry(radius, radius, length, 12);
  const material = new MeshStandardMaterial({ color, roughness: 0.55 });
  const mesh = new Mesh(geometry, material);
  mesh.position.copy(start).add(end).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(new Vector3(0, 1, 0), direction.clone().normalize());
  mesh.userData = { name, type, length: Number(length.toFixed(3)), ...extraData };
  return mesh;
}

function addBoreholes(scene: Scene, project: Project) {
  project.boreholes.forEach((bh) => {
    const top = bh.collarElevation;
    const points = [toScenePoint(bh.x, bh.y, top), toScenePoint(bh.x, bh.y, top - bh.depth)];
    addPolyline(scene, points, '#111827', bh.code, 'borehole');
    bh.layers.forEach((layer) => {
      const height = Math.max(0.05, layer.topElevation - layer.bottomElevation);
      const geometry = new CylinderGeometry(0.45, 0.45, height, 10);
      const material = new MeshStandardMaterial({ color: hashColor(layer.stratumCode), transparent: true, opacity: 0.72 });
      const mesh = new Mesh(geometry, material);
      mesh.position.copy(toScenePoint(bh.x, bh.y, (layer.topElevation + layer.bottomElevation) / 2));
      mesh.userData = { name: `${bh.code} ${layer.stratumCode}`, type: 'borehole-layer', layer };
      scene.add(mesh);
    });
  });
}

function addGeologicalSurfaces(scene: Scene, project: Project) {
  effectiveGeologicalSurfaces(project).forEach((surface) => {
    const nx = surface.grid.xValues.length;
    const ny = surface.grid.yValues.length;
    if (nx < 2 || ny < 2) return;
    const vertices: number[] = [];
    for (let iy = 0; iy < ny; iy += 1) {
      for (let ix = 0; ix < nx; ix += 1) {
        const vector = toScenePoint(surface.grid.xValues[ix], surface.grid.yValues[iy], surface.grid.zValues[iy]?.[ix] ?? 0);
        vertices.push(vector.x, vector.y, vector.z);
      }
    }
    const indices: number[] = [];
    for (let iy = 0; iy < ny - 1; iy += 1) {
      for (let ix = 0; ix < nx - 1; ix += 1) {
        const a = iy * nx + ix;
        const b = a + 1;
        const c = a + nx;
        const d = c + 1;
        indices.push(a, b, c, b, d, c);
      }
    }
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    const material = new MeshStandardMaterial({
      color: hashColor(surface.stratumCode),
      side: DoubleSide,
      transparent: true,
      opacity: surface.surfaceType === 'top' ? 0.24 : 0.38,
      metalness: 0,
      roughness: 0.8,
    });
    const mesh = new Mesh(geometry, material);
    mesh.userData = { name: `${surface.stratumCode}-${surface.surfaceType}`, type: 'geological-surface', confidence: surface.confidence };
    scene.add(mesh);
  });
}

function addVtuMesh(scene: Scene, vtuMesh?: VtuMesh) {
  if (!vtuMesh?.points?.length) return;
  const group = new Group();
  group.userData = { name: 'VTU mesh', type: 'vtu-group' };
  const vertices = vtuMesh.points.map((p) => toScenePoint(p[0] ?? 0, p[1] ?? 0, p[2] ?? 0));
  const material = new LineBasicMaterial({ color: '#7c3aed', transparent: true, opacity: 0.75 });
  const edges: Vector3[] = [];
  (vtuMesh.cellBlocks ?? []).forEach((cell) => {
    const nodeLimit = VTK_CELL_NODE_LIMIT[cell.vtkType ?? 0] ?? cell.nodes.length;
    const nodes = cell.nodes.slice(0, nodeLimit).filter((idx) => idx >= 0 && idx < vertices.length);
    for (let i = 0; i < nodes.length; i += 1) {
      const a = vertices[nodes[i]];
      const b = vertices[nodes[(i + 1) % nodes.length]];
      edges.push(a, b);
    }
  });
  if (edges.length) {
    const geometry = new BufferGeometry().setFromPoints(edges);
    const lineSegments = new LineSegments(geometry, material);
    lineSegments.userData = { name: 'VTU cell edges', type: 'vtu', summary: vtuMesh.summary };
    group.add(lineSegments);
  }
  const pointGeometry = new BufferGeometry().setFromPoints(vertices);
  const pointMaterial = new PointsMaterial({ color: '#4f46e5', size: 0.45 });
  const points = new Points(pointGeometry, pointMaterial);
  points.userData = { name: 'VTU points', type: 'vtu-points' };
  group.add(points);
  scene.add(group);
}

function addExcavation(scene: Scene, project: Project) {
  const excavation = project.excavation;
  if (!excavation) return;
  const topPoints = excavation.outline.points.map((p) => toScenePoint(p.x, p.y, excavation.topElevation));
  const bottomPoints = excavation.outline.points.map((p) => toScenePoint(p.x, p.y, excavation.bottomElevation));
  addPolyline(scene, topPoints, '#2563eb', 'excavation top outline', 'excavation', true);
  addPolyline(scene, bottomPoints, '#1d4ed8', 'excavation bottom outline', 'excavation', true);
  excavation.outline.points.forEach((p) => addPolyline(scene, [toScenePoint(p.x, p.y, excavation.topElevation), toScenePoint(p.x, p.y, excavation.bottomElevation)], '#93c5fd', 'excavation vertical edge', 'excavation'));
}

function addRetaining(scene: Scene, project: Project) {
  const retaining = project.retainingSystem;
  if (!retaining) return;
  const segmentsById = new Map(project.excavation?.segments.map((segment) => [segment.id, segment]) ?? []);
  retaining.diaphragmWalls.forEach((wall) => {
    const segment = segmentsById.get(wall.segmentId) ?? { start: wall.axis.points[0], end: wall.axis.points[wall.axis.points.length - 1] };
    if (!segment?.start || !segment?.end) return;
    scene.add(createPanelMesh(segment, wall.topElevation, wall.bottomElevation, wall.thickness, '#64748b', wall.panelCode, 'diaphragm-wall'));
  });
  retaining.crownBeams.forEach((beam) => {
    if (beam.axis.points.length < 2) return;
    const segment = { start: beam.axis.points[0], end: beam.axis.points[beam.axis.points.length - 1] };
    scene.add(createPanelMesh(segment, beam.elevation + 0.35, beam.elevation - 0.35, beam.section.width ?? 0.8, '#0f766e', beam.code, 'crown-beam'));
  });
  retaining.waleBeams.forEach((beam) => {
    if (beam.axis.points.length < 2) return;
    const segment = { start: beam.axis.points[0], end: beam.axis.points[beam.axis.points.length - 1] };
    scene.add(createPanelMesh(segment, beam.elevation + 0.25, beam.elevation - 0.25, beam.section.width ?? 0.6, '#0e7490', beam.code, 'wale-beam'));
  });
  retaining.supports.forEach((support) => {
    const radius = Math.max(0.14, (support.section.width ?? support.section.diameter ?? 0.7) / 2);
    const start = toScenePoint(support.start.x, support.start.y, support.elevation);
    const end = toScenePoint(support.end.x, support.end.y, support.elevation);
    const color = support.supportRole === 'corner_diagonal' ? '#f97316' : support.supportRole === 'secondary_strut' ? '#0891b2' : support.supportRole === 'ring_strut' ? '#9333ea' : '#dc2626';
    scene.add(createCylinderBetween(start, end, radius, color, support.code, 'support', {
      supportRole: support.supportRole ?? 'main_strut',
      layoutNote: support.layoutNote,
      levelIndex: support.levelIndex,
      elevation: support.elevation,
      section: support.section.name,
    }));
  });
  retaining.columns.forEach((column) => {
    const radius = Math.max(0.16, (column.section.width ?? column.section.diameter ?? 0.7) / 2);
    const start = toScenePoint(column.location.x, column.location.y, column.topElevation);
    const end = toScenePoint(column.location.x, column.location.y, column.bottomElevation);
    scene.add(createCylinderBetween(start, end, radius, '#a16207', column.code, 'column'));
  });
}

function addResultGlyphs(scene: Scene, project: Project) {
  const latest = project.calculationResults.length ? project.calculationResults[project.calculationResults.length - 1] : undefined;
  const excavation = project.excavation;
  if (!latest || !excavation) return;
  const bySegment = new Map(excavation.segments.map((s) => [s.id, s]));
  latest.stageResults.forEach((stageResult) => {
    const segment = bySegment.get(stageResult.segmentId);
    const maxPressure = Math.max(...stageResult.pressureProfile.points.map((p) => p.totalPressure), 0);
    if (!segment || maxPressure <= 0) return;
    const mid = segment.midpoint;
    const depth = excavation.depth;
    const length = Math.min(6, 1 + maxPressure / 80);
    const base = toScenePoint(mid.x, mid.y, (excavation.topElevation + excavation.bottomElevation) / 2);
    const normal = new Vector3(segment.outwardNormal.x * length, -depth * 0.02, segment.outwardNormal.y * length);
    const end = base.clone().add(normal);
    addPolyline(scene, [base, end], '#f97316', `pressure ${segment.name}`, 'result-pressure');
  });
}

function SvgFallback({ project }: { project: Project }) {
  const excavation = project.excavation;
  return (
    <svg className="svgCanvas" viewBox="-10 -25 120 80" role="img" aria-label="2D fallback viewer">
      {project.boreholes.map((bh) => (
        <g key={bh.id}>
          <line x1={bh.x} x2={bh.x} y1={-bh.y} y2={-bh.y - 8} stroke="#111827" strokeWidth="0.5" />
          <text x={bh.x + 1.5} y={-bh.y}>{bh.code}</text>
        </g>
      ))}
      {excavation && <polygon points={excavation.outline.points.map((p) => `${p.x},${-p.y}`).join(' ')} fill="rgba(37,99,235,.08)" stroke="#2563eb" />}
      {project.retainingSystem?.supports.map((s) => <line key={s.id} x1={s.start.x} y1={-s.start.y} x2={s.end.x} y2={-s.end.y} stroke="#dc2626" strokeWidth="1.2" />)}
    </svg>
  );
}

function ObjectPropertyTable({ data }: { data: Record<string, unknown> }) {
  return <table className="table compactTable"><tbody>{Object.entries(data).map(([key, value]) => <tr key={key}><td>{key}</td><td>{typeof value === 'object' && value !== null ? Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k}: ${String(v)}`).join('；') : String(value ?? '-')}</td></tr>)}</tbody></table>;
}

export default function ProjectSceneViewer({ project, mode = 'all' }: { project: Project; mode?: 'all' | 'geology' | 'retaining' | 'results' }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<WebGLRenderer | null>(null);
  const [layers, setLayers] = useState<LayerState>(() => ({ ...DEFAULT_LAYERS }));
  const [opacity, setOpacity] = useState(0.68);
  const [selected, setSelected] = useState<Record<string, unknown> | undefined>();
  const [renderError, setRenderError] = useState<string | undefined>();
  const [renderNonce, setRenderNonce] = useState(0);
  const [clipEnabled, setClipEnabled] = useState(false);
  const [clipOffset, setClipOffset] = useState(0);
  const bounds = useMemo(() => projectBounds(project), [project]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return undefined;
    mount.innerHTML = '';
    let renderer: WebGLRenderer;
    try {
      renderer = createStableWebGLRenderer({ antialias: true });
    } catch (err) {
      setRenderError(err instanceof Error ? err.message : '当前环境不支持 WebGL，已切换为 SVG 降级视图。');
      return undefined;
    }
    rendererRef.current = renderer;
    renderer.localClippingEnabled = clipEnabled;
    renderer.setPixelRatio(stablePixelRatio(1.5));
    renderer.setSize(mount.clientWidth || 900, 460);
    renderer.domElement.className = 'sceneCanvas';
    mount.appendChild(renderer.domElement);
    const detachContextLifecycle = bindWebglContextLifecycle(renderer, {
      onLost: setRenderError,
      onRestored: () => { setRenderError(undefined); setRenderNonce((value) => value + 1); },
    });

    const scene = new Scene();
    scene.background = new Color('#f8fafc');
    const camera = new PerspectiveCamera(48, (mount.clientWidth || 900) / 460, 0.1, 10000);
    const { center, radius } = bounds;
    let theta = -Math.PI / 4;
    let phi = Math.PI / 3;
    let distance = radius * 1.8;
    const target = center.clone();
    const updateCamera = () => {
      const x = target.x + distance * Math.sin(phi) * Math.cos(theta);
      const y = target.y + distance * Math.cos(phi);
      const z = target.z + distance * Math.sin(phi) * Math.sin(theta);
      camera.position.set(x, y, z);
      camera.lookAt(target);
    };
    updateCamera();

    scene.add(new AmbientLight('#ffffff', 0.65));
    const light = new DirectionalLight('#ffffff', 0.9);
    light.position.set(center.x + radius, center.y + radius, center.z + radius);
    scene.add(light);
    const grid = new GridHelper(Math.max(30, radius * 2), 20, '#94a3b8', '#e2e8f0');
    grid.position.set(center.x, Math.min(0, center.y - radius * 0.2), center.z);
    scene.add(grid);
    const axes = new AxesHelper(Math.min(20, radius * 0.4));
    axes.position.set(center.x - radius * 0.7, center.y - radius * 0.3, center.z - radius * 0.7);
    scene.add(axes);

    if (layers.boreholes && mode !== 'retaining') addBoreholes(scene, project);
    if (layers.surfaces && mode !== 'retaining') addGeologicalSurfaces(scene, project);
    if (layers.vtu && mode !== 'retaining') addVtuMesh(scene, project.geologicalModel?.vtuMesh);
    if (layers.excavation) addExcavation(scene, project);
    if (layers.walls || layers.supports || mode === 'retaining') addRetaining(scene, project);
    if (layers.results && mode !== 'geology') addResultGlyphs(scene, project);

    const clipPlane = new Plane(new Vector3(-1, 0, 0), bounds.center.x + clipOffset);
    scene.traverse((object: Object3D) => {
      if (object instanceof Mesh) {
        const material = object.material;
        if (material instanceof MeshStandardMaterial && object.userData.type !== 'support' && object.userData.type !== 'column') {
          material.opacity = opacity;
          material.transparent = opacity < 1;
        }
        if (material instanceof MeshStandardMaterial || material instanceof MeshLambertMaterial || material instanceof MeshBasicMaterial) {
          material.clippingPlanes = clipEnabled ? [clipPlane] : [];
          material.needsUpdate = true;
        }
      }
    });

    const raycaster = new Raycaster();
    const pointer = new Vector2();
    const onClick = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(scene.children, true).find((item: Intersection) => Object.keys(item.object.userData).length > 0);
      setSelected(hit?.object.userData as Record<string, unknown> | undefined);
    };

    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    const onPointerDown = (event: PointerEvent) => { dragging = true; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture?.(event.pointerId); };
    const onPointerMove = (event: PointerEvent) => {
      if (!dragging) return;
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      theta -= dx * 0.008;
      phi = Math.max(0.2, Math.min(Math.PI - 0.2, phi + dy * 0.006));
      updateCamera();
    };
    const onPointerUp = (event: PointerEvent) => { dragging = false; renderer.domElement.releasePointerCapture?.(event.pointerId); };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      distance = Math.max(radius * 0.35, Math.min(radius * 5, distance * (event.deltaY > 0 ? 1.08 : 0.92)));
      updateCamera();
    };
    const onResize = () => {
      const width = Math.max(mount.clientWidth || 900, 320);
      const height = Math.max(mount.clientHeight || 460, 360);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    };
    renderer.domElement.addEventListener('click', onClick);
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('resize', onResize);

    const stopRenderLoop = startStableRenderLoop(renderer, scene, camera, mount, { maxFps: 24 });
    setRenderError(undefined);

    return () => {
      stopRenderLoop();
      detachContextLifecycle();
      renderer.domElement.removeEventListener('click', onClick);
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('wheel', onWheel);
      window.removeEventListener('resize', onResize);
      releaseStableWebGLRenderer(renderer, scene, mount);
      rendererRef.current = null;
      mount.innerHTML = '';
    };
  }, [bounds, clipEnabled, clipOffset, layers, mode, opacity, project, renderNonce]);

  const layerLabels: Record<LayerKey, string> = {
    boreholes: '钻孔柱',
    surfaces: '地层面',
    vtu: 'VTU网格',
    excavation: '基坑轮廓',
    walls: '地连墙/梁柱',
    supports: '支撑',
    results: '计算结果',
  };

  return (
    <FullscreenShell label="项目三维模型"><div className="sceneShell">
      <div className="layerControls">
        {(Object.keys(layerLabels) as LayerKey[]).map((key) => (
          <label key={key}>
            <input type="checkbox" checked={layers[key]} onChange={() => setLayers((prev) => ({ ...prev, [key]: !prev[key] }))} />
            {layerLabels[key]}
          </label>
        ))}
        <label className="opacityControl">透明度 <input type="range" min="0.15" max="1" step="0.05" value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} /></label>
        <label><input type="checkbox" checked={clipEnabled} onChange={(e) => setClipEnabled(e.target.checked)} /> X向剖切</label>
        {clipEnabled && <label className="opacityControl">剖切位置 <input type="range" min={-bounds.radius.toFixed(0)} max={bounds.radius.toFixed(0)} step="1" value={clipOffset} onChange={(e) => setClipOffset(Number(e.target.value))} /></label>}
      </div>
      {renderError && <div className="modelRenderRecovery"><span>{renderError}</span><button type="button" className="secondary" onClick={() => setRenderNonce((value) => value + 1)}>重建三维视图</button></div>}
      <div ref={mountRef} className="sceneMount" />
      {renderError && <SvgFallback project={project} />}
      <div className="propertyPanel">
        <strong>对象属性</strong>
        {selected ? <ObjectPropertyTable data={selected} /> : <span className="small">点击三维对象查看构件、地层或 VTU 属性。</span>}
      </div>
    </div></FullscreenShell>
  );
}
