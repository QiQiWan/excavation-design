import { useEffect, useMemo, useRef, useState } from 'react';
import { AmbientLight, BoxGeometry, BufferGeometry, Color, DirectionalLight, GridHelper, Material, Mesh, MeshStandardMaterial, Object3D, PerspectiveCamera, Raycaster, Scene, Vector2, Vector3, WebGLRenderer } from 'three';
import type { CalculationResult, Project } from '../types/domain';
import FullscreenShell from '../components/FullscreenShell';

type Metric = 'displacement' | 'moment' | 'shear';
type DisplayMode = 'signed' | 'absolute';
const ENVELOPE_STAGE = '__envelope__';

function toNumber(value: unknown, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function rawMetricValue(point: Record<string, unknown>, metric: Metric) {
  if (metric === 'moment') return toNumber(point.moment);
  if (metric === 'shear') return toNumber(point.shear);
  return toNumber(point.displacement);
}

function displayMetricValue(point: Record<string, unknown>, metric: Metric, mode: DisplayMode) {
  const value = rawMetricValue(point, metric);
  return mode === 'absolute' ? Math.abs(value) : value;
}

function colorForValue(value: number, maxAbs: number, mode: DisplayMode) {
  const normalized = Math.max(-1, Math.min(1, value / Math.max(maxAbs, 1e-9)));
  if (mode === 'absolute') {
    const color = new Color();
    color.setHSL((1 - Math.abs(normalized)) * 0.62, 0.82, 0.52);
    return color;
  }
  const negative = new Color(0x2563eb);
  const zero = new Color(0xf8fafc);
  const positive = new Color(0xdc2626);
  return normalized < 0 ? zero.clone().lerp(negative, Math.abs(normalized)) : zero.clone().lerp(positive, normalized);
}

function bounds(project: Project) {
  const pts = project.retainingSystem?.diaphragmWalls.flatMap((wall) => wall.axis.points) ?? project.excavation?.outline.points ?? [];
  const xs = pts.map((point) => point.x);
  const ys = pts.map((point) => point.y);
  if (!xs.length) { xs.push(0, 60); ys.push(0, 40); }
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const minZ = Math.min(project.excavation?.bottomElevation ?? -12, ...(project.retainingSystem?.diaphragmWalls.map((wall) => wall.bottomElevation) ?? []));
  const maxZ = project.excavation?.topElevation ?? 0;
  const size = Math.max(maxX - minX, maxY - minY, Math.max(8, maxZ - minZ), 20);
  return { center: new Vector3((minX + maxX) / 2, (minZ + maxZ) / 2, (minY + maxY) / 2), size, minZ, maxZ };
}

function sampleKey(point: Record<string, unknown>) {
  const elevation = toNumber(point.elevation, Number.NaN);
  if (Number.isFinite(elevation)) return elevation.toFixed(4);
  return `d:${toNumber(point.depth).toFixed(4)}`;
}

function envelopePoints(samples: Record<string, unknown>[][], metric: Metric) {
  const byElevation = new Map<string, Record<string, unknown>>();
  samples.flat().forEach((point) => {
    const key = sampleKey(point);
    const previous = byElevation.get(key);
    if (!previous || Math.abs(rawMetricValue(point, metric)) > Math.abs(rawMetricValue(previous, metric))) {
      byElevation.set(key, point);
    }
  });
  return [...byElevation.values()];
}

export default function WallCloud3DViewer({ project, latest, highlightLocator }: { project: Project; latest: CalculationResult; highlightLocator?: Record<string, unknown> }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [metric, setMetric] = useState<Metric>('displacement');
  const [displayMode, setDisplayMode] = useState<DisplayMode>('signed');
  const [stageId, setStageId] = useState(ENVELOPE_STAGE);
  const [selected, setSelected] = useState<Record<string, unknown> | undefined>();

  const samples = useMemo(() => {
    const legacy = (latest.reportDiagramData ?? {}).wallForceSamples as Record<string, unknown>[] | null | undefined;
    if (legacy?.length) return legacy;
    return latest.stageResults.map((result) => result.wallInternalForce).filter(Boolean) as unknown as Record<string, unknown>[];
  }, [latest]);

  const stages = useMemo(() => {
    const seen = new Set<string>();
    return samples.flatMap((sample) => {
      const id = String(sample.stageId ?? '');
      if (!id || seen.has(id)) return [];
      seen.add(id);
      return [{ id, label: id }];
    });
  }, [samples]);

  useEffect(() => {
    if (stageId !== ENVELOPE_STAGE && !stages.some((stage) => stage.id === stageId)) setStageId(ENVELOPE_STAGE);
  }, [stageId, stages]);

  const data = useMemo(() => {
    const bySegmentStage = new Map<string, Map<string, Record<string, unknown>[]>>();
    samples.forEach((sample) => {
      const segment = String(sample.segmentId ?? '');
      const stage = String(sample.stageId ?? '');
      if (!segment || !stage) return;
      const stageMap = bySegmentStage.get(segment) ?? new Map<string, Record<string, unknown>[]>();
      stageMap.set(stage, (sample.points ?? []) as Record<string, unknown>[]);
      bySegmentStage.set(segment, stageMap);
    });

    const rows = (project.retainingSystem?.diaphragmWalls ?? []).map((wall) => {
      const segmentKeys = [wall.segmentId, ...(wall.faceSegmentIds ?? [])].filter(Boolean).map(String);
      let points: Record<string, unknown>[] = [];
      for (const segmentKey of segmentKeys) {
        const stageMap = bySegmentStage.get(segmentKey);
        if (!stageMap) continue;
        if (stageId === ENVELOPE_STAGE) points.push(...envelopePoints([...stageMap.values()], metric));
        else points.push(...(stageMap.get(stageId) ?? []));
      }
      if (!points.length) {
        const topElevation = wall.topElevation ?? project.excavation?.topElevation ?? 0;
        const bottomElevation = wall.bottomElevation ?? project.excavation?.bottomElevation ?? -12;
        points = [topElevation, (topElevation + bottomElevation) / 2, bottomElevation].map((elevation) => ({ elevation, depth: topElevation - elevation, displacement: 0, moment: 0, shear: 0 }));
      }
      return { wall, points };
    });
    const values = rows.flatMap((row) => row.points.map((point) => rawMetricValue(point, metric)));
    const maxAbs = Math.max(1e-6, ...values.map(Math.abs));
    const minValue = Math.min(0, ...values);
    const maxValue = Math.max(0, ...values);
    return { rows, maxAbs, minValue, maxValue };
  }, [project, samples, metric, stageId]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    mount.innerHTML = '';
    const width = Math.max(mount.clientWidth, 640);
    const height = Math.max(mount.clientHeight, 480);
    const scene = new Scene();
    scene.background = new Color(0xf8fafc);
    const camera = new PerspectiveCamera(45, width / height, 0.1, 6000);
    const renderer = new WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    mount.appendChild(renderer.domElement);

    const box = bounds(project);
    scene.add(new AmbientLight(0xffffff, 0.76));
    const light = new DirectionalLight(0xffffff, 0.9);
    light.position.set(box.center.x + box.size, box.center.y + box.size, box.center.z + box.size);
    scene.add(light);
    const grid = new GridHelper(Math.max(box.size * 1.45, 30), 22, 0xcbd5e1, 0xe2e8f0);
    grid.position.set(box.center.x, box.minZ, box.center.z);
    scene.add(grid);

    const pickables: Object3D[] = [];
    const disposableGeometries: BufferGeometry[] = [];
    const disposableMaterials: Material[] = [];
    const targetId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');

    data.rows.forEach(({ wall, points }) => {
      const a = wall.axis.points[0]; const b = wall.axis.points[wall.axis.points.length - 1];
      if (!a || !b) return;
      const dx = b.x - a.x; const dy = b.y - a.y;
      const length = Math.hypot(dx, dy) || 1;
      const angle = -Math.atan2(dy, dx);
      const topElevation = wall.topElevation ?? project.excavation?.topElevation ?? 0;
      const normalizedPoints: Record<string, unknown>[] = points.map((point) => ({
        ...point,
        elevation: toNumber(point.elevation, topElevation - toNumber(point.depth)),
      }));
      const sorted = normalizedPoints.sort((left, right) => toNumber(right.elevation) - toNumber(left.elevation));
      const highlighted = Boolean(targetId && (targetId === wall.id || targetId === wall.panelCode || targetId === wall.segmentId));
      sorted.slice(0, 80).forEach((point, index) => {
        const elevation0 = toNumber(point.elevation);
        const elevation1 = index < sorted.length - 1
          ? toNumber(sorted[index + 1].elevation)
          : Math.max(wall.bottomElevation, elevation0 - Math.max(0.25, (wall.topElevation - wall.bottomElevation) / Math.max(sorted.length, 1)));
        const yTop = Math.max(elevation0, elevation1);
        const yBottom = Math.min(elevation0, elevation1);
        const cellHeight = Math.max(0.08, yTop - yBottom);
        const geometry = new BoxGeometry(length, cellHeight, Math.max(0.18, wall.thickness * 1.06));
        const signedValue = rawMetricValue(point, metric);
        const renderedValue = displayMetricValue(point, metric, displayMode);
        const material = new MeshStandardMaterial({
          color: highlighted ? new Color(0xeab308) : colorForValue(renderedValue, data.maxAbs, displayMode),
          transparent: true,
          opacity: highlighted ? 0.98 : 0.9,
          roughness: 0.45,
        });
        disposableGeometries.push(geometry);
        disposableMaterials.push(material);
        const mesh = new Mesh(geometry, material);
        mesh.position.set((a.x + b.x) / 2, (yTop + yBottom) / 2, (a.y + b.y) / 2);
        mesh.rotation.y = angle;
        mesh.userData.info = {
          type: 'WallCloudCell',
          wall: wall.panelCode,
          stage: stageId === ENVELOPE_STAGE ? '包络' : stageId,
          metric,
          value: signedValue.toFixed(metric === 'displacement' ? 3 : 1),
          depth: toNumber(point.depth, topElevation - elevation0).toFixed(2),
          elevation: elevation0.toFixed(2),
          status: highlighted ? 'highlight' : 'normal',
        };
        scene.add(mesh);
        pickables.push(mesh);
      });
    });

    let theta = Math.PI / 4; let phi = Math.PI / 3.2; let radius = Math.max(box.size * 1.8, 42);
    const target = box.center.clone();
    const updateCamera = () => {
      phi = Math.max(0.15, Math.min(Math.PI / 2.05, phi));
      camera.position.set(target.x + radius * Math.sin(phi) * Math.cos(theta), target.y + radius * Math.cos(phi), target.z + radius * Math.sin(phi) * Math.sin(theta));
      camera.lookAt(target);
    };
    updateCamera();

    let dragging = false; let moved = false; let lastX = 0; let lastY = 0;
    const raycaster = new Raycaster();
    const pointerPick = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(mouse, camera);
      return raycaster.intersectObjects(pickables, true)[0]?.object;
    };
    const onPointerDown = (event: PointerEvent) => { dragging = true; moved = false; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); };
    const onPointerMove = (event: PointerEvent) => {
      if (!dragging) return;
      const deltaX = event.clientX - lastX; const deltaY = event.clientY - lastY;
      moved = moved || Math.abs(deltaX) + Math.abs(deltaY) > 3; lastX = event.clientX; lastY = event.clientY;
      if (event.shiftKey) {
        const panScale = radius / 740;
        const right = new Vector3().subVectors(camera.position, target).cross(camera.up).normalize();
        target.addScaledVector(right, -deltaX * panScale).addScaledVector(camera.up.clone().normalize(), deltaY * panScale);
      } else { theta -= deltaX * 0.006; phi -= deltaY * 0.006; }
      updateCamera();
    };
    const onPointerUp = (event: PointerEvent) => { dragging = false; renderer.domElement.releasePointerCapture(event.pointerId); if (!moved) setSelected(pointerPick(event)?.userData.info); };
    const onWheel = (event: WheelEvent) => { event.preventDefault(); radius *= event.deltaY > 0 ? 1.08 : 0.92; radius = Math.max(4, Math.min(5000, radius)); updateCamera(); };
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false });

    const resizeObserver = new ResizeObserver(() => {
      const nextWidth = Math.max(mount.clientWidth, 640); const nextHeight = Math.max(mount.clientHeight, 480);
      renderer.setSize(nextWidth, nextHeight); camera.aspect = nextWidth / nextHeight; camera.updateProjectionMatrix();
    });
    resizeObserver.observe(mount);
    let animationFrame = 0;
    const animate = () => { animationFrame = requestAnimationFrame(animate); renderer.render(scene, camera); };
    animate();

    return () => {
      cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('wheel', onWheel);
      disposableGeometries.forEach((geometry) => geometry.dispose());
      disposableMaterials.forEach((material) => material.dispose());
      renderer.dispose();
      mount.innerHTML = '';
    };
  }, [data, project, metric, displayMode, stageId, highlightLocator]);

  if (!data.rows.length) return null;
  return (
    <FullscreenShell label="三维受力云图"><section className="wallCloud3dPanel">
      <div className="sectionLead">
        <h4>围护墙三维受力与变形云图</h4>
        <div className="segmentedControls">
          <select aria-label="施工阶段" value={stageId} onChange={(event) => setStageId(event.target.value)}>
            <option value={ENVELOPE_STAGE}>全阶段控制包络</option>
            {stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
          </select>
          <button className={metric === 'displacement' ? 'active' : ''} onClick={() => setMetric('displacement')}>变形</button>
          <button className={metric === 'moment' ? 'active' : ''} onClick={() => setMetric('moment')}>弯矩</button>
          <button className={metric === 'shear' ? 'active' : ''} onClick={() => setMetric('shear')}>剪力</button>
          <button className={displayMode === 'signed' ? 'active' : ''} onClick={() => setDisplayMode('signed')}>正负值</button>
          <button className={displayMode === 'absolute' ? 'active' : ''} onClick={() => setDisplayMode('absolute')}>绝对值</button>
        </div>
      </div>
      <div className="wallCloud3dViewport" ref={mountRef} />
      <div className={`heatLegend ${displayMode === 'signed' ? 'diverging' : ''}`}>
        <span>{displayMode === 'signed' ? data.minValue.toFixed(metric === 'displacement' ? 2 : 0) : '低'}</span><em />
        <span>{displayMode === 'signed' ? data.maxValue.toFixed(metric === 'displacement' ? 2 : 0) : `高：${data.maxAbs.toFixed(metric === 'displacement' ? 2 : 0)}`}</span>
        {selected && <strong>{String(selected.wall)} · {String(selected.stage)} · 深度 {String(selected.depth)}m · 高程 {String(selected.elevation)}m · {String(selected.value)}</strong>}
      </div>
    </section></FullscreenShell>
  );
}
