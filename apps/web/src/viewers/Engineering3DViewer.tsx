import { useEffect, useMemo, useRef, useState } from 'react';
import { AmbientLight, AxesHelper, BoxGeometry, BufferGeometry, Color, CylinderGeometry, DirectionalLight, DoubleSide, Float32BufferAttribute, GridHelper, Line, LineBasicMaterial, LineSegments, Material, Mesh, MeshStandardMaterial, Object3D, PerspectiveCamera, Plane, Raycaster, Scene, SphereGeometry, Vector2, Vector3, WebGLRenderer } from 'three';
import type { GeologicalSurface, Point2D, Project, VtuCellBlock } from '../types/domain';

type LayerKey = 'boreholes' | 'surfaces' | 'vtu' | 'excavation' | 'walls' | 'beams' | 'supports' | 'columns' | 'results';

type Layers = Record<LayerKey, boolean>;

const DEFAULT_LAYERS: Layers = {
  boreholes: true,
  surfaces: true,
  vtu: true,
  excavation: true,
  walls: true,
  beams: true,
  supports: true,
  columns: true,
  results: true,
};

const PALETTE = [
  0x94a3b8, 0xd97706, 0x22c55e, 0x38bdf8, 0xa78bfa, 0xf472b6, 0xfacc15, 0x2dd4bf,
];

function stratumColor(project: Project, code: string | undefined, fallbackIndex = 0): Color {
  const stratum = project.strata.find((item) => item.code === code);
  if (stratum?.color) return new Color(stratum.color);
  const index = Math.max(0, project.strata.findIndex((item) => item.code === code));
  return new Color(PALETTE[(index >= 0 ? index : fallbackIndex) % PALETTE.length]);
}

function planLength(a: Point2D, b: Point2D): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

function planAngle(a: Point2D, b: Point2D): number {
  return Math.atan2(b.y - a.y, b.x - a.x);
}

function toVector3(x: number, y: number, elevation = 0): Vector3 {
  return new Vector3(x, elevation, y);
}

function addObjectInfo(object: Object3D, info: Record<string, unknown>, pickables: Object3D[]) {
  object.userData.info = info;
  pickables.push(object);
}

function humanInfo(info: Record<string, unknown>): [string, unknown][] {
  const labels: Record<string, string> = {
    type: '对象类型', code: '编号', role: '角色', level: '层号', axialForce: '轴力(kN)', designAxialForce: '设计轴力(kN)',
    spanLength: '跨长(m)', baySpacing: '分仓(m)', startFace: '起点墙面', endFace: '终点墙面', status: '状态',
    maxMomentDesign: '设计弯矩', maxShearDesign: '设计剪力', maxDeflection: '挠度', bearingStress: '承压应力', bearingCapacity: '承压限值',
    foundationType: '基础类型', pileLength: '桩长', pileCapacity: '桩承载力', section: '截面', material: '材料',
  };
  return Object.entries(info).map(([key, value]) => [labels[key] ?? key, value]);
}


function issueColor(status: string | undefined): number | undefined {
  if (status === 'fail') return 0xdc2626;
  if (status === 'warning' || status === 'manual_review') return 0xf59e0b;
  return undefined;
}

function issueSeverityMap(project: Project): Map<string, string> {
  const latest = project.calculationResults?.[project.calculationResults.length - 1];
  const issues = latest?.supportLayoutQuality?.issues ?? [];
  const map = new Map<string, string>();
  const rank: Record<string, number> = { pass: 0, warning: 1, manual_review: 2, fail: 3 };
  issues.forEach((issue) => {
    const ids = [issue.objectId, ...(issue.relatedObjectIds ?? [])].filter(Boolean) as string[];
    ids.forEach((id) => {
      const current = map.get(id);
      if (!current || (rank[issue.severity] ?? 0) > (rank[current] ?? 0)) map.set(id, issue.severity);
    });
  });
  return map;
}

function supportCloudColor(force: number | undefined, maxForce: number): number {
  if (!force || !Number.isFinite(force) || maxForce <= 0) return 0xdc2626;
  const ratio = Math.max(0, Math.min(1, force / maxForce));
  const color = new Color();
  color.setHSL((1 - ratio) * 0.33, 0.82, 0.48);
  return color.getHex();
}

function edgesForCell(block: VtuCellBlock): [number, number][] {
  const n = block.nodes;
  if (block.cellType === 'triangle' && n.length >= 3) return [[n[0], n[1]], [n[1], n[2]], [n[2], n[0]]];
  if (block.cellType === 'quad' && n.length >= 4) return [[n[0], n[1]], [n[1], n[2]], [n[2], n[3]], [n[3], n[0]]];
  if (block.cellType === 'tetra' && n.length >= 4) return [[n[0], n[1]], [n[0], n[2]], [n[0], n[3]], [n[1], n[2]], [n[1], n[3]], [n[2], n[3]]];
  if (block.cellType === 'hexahedron' && n.length >= 8) return [[n[0], n[1]], [n[1], n[2]], [n[2], n[3]], [n[3], n[0]], [n[4], n[5]], [n[5], n[6]], [n[6], n[7]], [n[7], n[4]], [n[0], n[4]], [n[1], n[5]], [n[2], n[6]], [n[3], n[7]]];
  const pairs: [number, number][] = [];
  for (let i = 0; i < n.length; i += 1) for (let j = i + 1; j < n.length; j += 1) pairs.push([n[i], n[j]]);
  return pairs;
}

function makeSurfaceMesh(project: Project, surface: GeologicalSurface, opacity: number, clippingPlanes: Plane[]): Mesh | undefined {
  const xs = surface.grid.xValues;
  const ys = surface.grid.yValues;
  const zs = surface.grid.zValues;
  if (xs.length < 2 || ys.length < 2 || zs.length < 2) return undefined;
  const vertices: number[] = [];
  for (let j = 0; j < ys.length; j += 1) {
    for (let i = 0; i < xs.length; i += 1) {
      const row = zs[j] ?? [];
      vertices.push(xs[i], Number(row[i] ?? 0), ys[j]);
    }
  }
  const indices: number[] = [];
  for (let j = 0; j < ys.length - 1; j += 1) {
    for (let i = 0; i < xs.length - 1; i += 1) {
      const a = j * xs.length + i;
      const b = a + 1;
      const c = a + xs.length;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  const material = new MeshStandardMaterial({
    color: stratumColor(project, surface.stratumCode),
    transparent: true,
    opacity,
    side: DoubleSide,
    clippingPlanes,
  });
  const mesh = new Mesh(geometry, material);
  mesh.name = `地层面 ${surface.stratumCode} ${surface.surfaceType}`;
  mesh.userData.info = {
    type: 'GeologicalSurface',
    stratumCode: surface.stratumCode,
    surfaceType: surface.surfaceType,
    grid: `${xs.length} x ${ys.length}`,
    confidence: surface.confidence,
  };
  return mesh;
}

function boundsFromProject(project: Project) {
  const xs: number[] = [];
  const ys: number[] = [];
  const zs: number[] = [0, project.excavation?.bottomElevation ?? -12];
  project.boreholes.forEach((bh) => { xs.push(bh.x); ys.push(bh.y); zs.push(bh.collarElevation, bh.collarElevation - bh.depth); });
  project.excavation?.outline.points.forEach((p) => { xs.push(p.x); ys.push(p.y); });
  project.geologicalModel?.vtuMesh?.points?.forEach((p) => { xs.push(Number(p[0] ?? 0)); ys.push(Number(p[1] ?? 0)); zs.push(Number(p[2] ?? 0)); });
  if (!xs.length) { xs.push(0, 60); ys.push(0, 40); }
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  return { minX, maxX, minY, maxY, minZ: Math.min(...zs), maxZ: Math.max(...zs), size: Math.max(maxX - minX, maxY - minY, 20), center: new Vector3((minX + maxX) / 2, (Math.min(...zs) + Math.max(...zs)) / 2, (minY + maxY) / 2) };
}


function BoreholeDetailPanel({ project, info }: { project: Project; info: Record<string, unknown> }) {
  const code = String(info.borehole ?? '');
  const borehole = project.boreholes.find((item) => item.code === code || item.id === info.boreholeId);
  if (!borehole) return null;
  return (
    <div className="boreholeDetailPanel">
      <h4>{borehole.code} 地层分布</h4>
      <div className="boreholeMeta"><span>孔口标高 {borehole.collarElevation} m</span><span>孔深 {borehole.depth} m</span></div>
      <div className="boreholeLayerList">
        {borehole.layers.map((layer) => {
          const color = stratumColor(project, layer.stratumCode).getStyle();
          return <div key={layer.id} className="boreholeLayerRow"><em style={{ background: color }} /><strong>{layer.stratumCode}</strong><span>{layer.stratumName}</span><small>{layer.topElevation.toFixed(2)} ~ {layer.bottomElevation.toFixed(2)} m</small></div>;
        })}
      </div>
    </div>
  );
}

function isBoreholeInfo(info?: Record<string, unknown>) {
  return String(info?.type ?? '') === 'BoreholeLayer';
}

export default function Engineering3DViewer({ project, focus = 'all', highlightLocator }: { project: Project; focus?: 'all' | 'geology' | 'retaining'; highlightLocator?: Record<string, unknown> }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [layers, setLayers] = useState<Layers>({ ...DEFAULT_LAYERS, walls: focus !== 'geology', beams: focus !== 'geology', supports: focus !== 'geology', columns: focus !== 'geology' });
  const [opacity, setOpacity] = useState(0.55);
  const [clip, setClip] = useState(false);
  const [clipAxis, setClipAxis] = useState<'x' | 'y' | 'z'>('x');
  const [clipOffset, setClipOffset] = useState(0);
  const [measureMode, setMeasureMode] = useState(false);
  const [measureStart, setMeasureStart] = useState<Vector3 | undefined>();
  const [measureText, setMeasureText] = useState<string | undefined>();
  const [selected, setSelected] = useState<Record<string, unknown> | undefined>();
  const [hoverInfo, setHoverInfo] = useState<Record<string, unknown> | undefined>();
  const [renderQuality, setRenderQuality] = useState<'auto' | 'performance' | 'balanced' | 'high'>('auto');

  const stats = useMemo(() => ({
    boreholes: project.boreholes.length,
    surfaces: project.geologicalModel?.surfaces.length ?? 0,
    vtuCells: project.geologicalModel?.vtuMesh?.summary?.cellCount ?? project.geologicalModel?.vtuMesh?.cellBlocks?.length ?? 0,
    walls: project.retainingSystem?.diaphragmWalls.length ?? 0,
    supports: project.retainingSystem?.supports.length ?? 0,
    maxSupportAxialForce: Math.max(0, ...(project.retainingSystem?.supports.map((s) => s.designAxialForce ?? 0) ?? [])),
  }), [project]);

  const sceneComplexity = stats.vtuCells + stats.supports * 20 + stats.walls * 12 + stats.boreholes * 8 + stats.surfaces * 80;
  const effectiveQuality: 'performance' | 'balanced' | 'high' = renderQuality === 'auto'
    ? sceneComplexity > 16000 ? 'performance' : sceneComplexity > 5000 ? 'balanced' : 'high'
    : renderQuality;
  const vtuStride = effectiveQuality === 'performance' ? 8 : effectiveQuality === 'balanced' ? 3 : 1;
  const radialSegments = effectiveQuality === 'performance' ? 6 : effectiveQuality === 'balanced' ? 8 : 12;

  const maxMomentWall = useMemo(() => {
    return project.retainingSystem?.diaphragmWalls.reduce((best, wall) => ((wall.designResults?.maxMomentDesign ?? 0) > (best?.designResults?.maxMomentDesign ?? -1) ? wall : best), project.retainingSystem?.diaphragmWalls[0]);
  }, [project]);
  const maxAxialSupport = useMemo(() => {
    return project.retainingSystem?.supports.reduce((best, support) => ((support.designAxialForce ?? 0) > (best?.designAxialForce ?? -1) ? support : best), project.retainingSystem?.supports[0]);
  }, [project]);
  const reviewIssues = useMemo(() => {
    const latest = project.calculationResults?.[project.calculationResults.length - 1];
    return (latest?.checks ?? []).filter((c) => ['fail', 'warning', 'manual_review'].includes(String(c.status))).slice(0, 12);
  }, [project]);
  const supportIssueMap = useMemo(() => issueSeverityMap(project), [project]);
  const highlightId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    mount.innerHTML = '';
    const { clientWidth, clientHeight } = mount;
    const width = Math.max(clientWidth, 640);
    const height = Math.max(clientHeight, 420);
    const bbox = boundsFromProject(project);
    const scene = new Scene();
    scene.background = new Color(0xf8fafc);
    const camera = new PerspectiveCamera(45, width / height, 0.1, 5000);
    const renderer = new WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, effectiveQuality === 'high' ? 2 : effectiveQuality === 'balanced' ? 1.35 : 1));
    renderer.localClippingEnabled = clip;
    mount.appendChild(renderer.domElement);

    const pickables: Object3D[] = [];
    const clipNormal = clipAxis === 'x' ? new Vector3(-1, 0, 0) : clipAxis === 'y' ? new Vector3(0, -1, 0) : new Vector3(0, 0, -1);
    const clippingPlanes = clip ? [new Plane(clipNormal, clipOffset || (clipAxis === 'x' ? bbox.center.x : clipAxis === 'y' ? bbox.center.y : bbox.center.z))] : [];
    scene.add(new AmbientLight(0xffffff, 0.72));
    const sun = new DirectionalLight(0xffffff, 0.8);
    sun.position.set(bbox.center.x - bbox.size, bbox.center.y + bbox.size * 1.5, bbox.center.z + bbox.size);
    scene.add(sun);
    const grid = new GridHelper(Math.max(bbox.size * 1.4, 20), 20, 0x94a3b8, 0xcbd5e1);
    grid.position.set(bbox.center.x, 0, bbox.center.z);
    scene.add(grid);
    const axes = new AxesHelper(Math.max(8, bbox.size * 0.18));
    axes.position.copy(bbox.center).setY(0);
    scene.add(axes);

    if (layers.surfaces) {
      project.geologicalModel?.surfaces.forEach((surface) => {
        const mesh = makeSurfaceMesh(project, surface, opacity, clippingPlanes);
        if (mesh) { scene.add(mesh); addObjectInfo(mesh, mesh.userData.info, pickables); }
      });
    }

    if (layers.boreholes) {
      project.boreholes.forEach((bh) => {
        bh.layers.forEach((layer, index) => {
          const h = Math.max(0.05, layer.topElevation - layer.bottomElevation);
          const geometry = new CylinderGeometry(0.45, 0.45, h, radialSegments);
          const material = new MeshStandardMaterial({ color: stratumColor(project, layer.stratumCode, index), transparent: true, opacity: 0.82, clippingPlanes });
          const cyl = new Mesh(geometry, material);
          cyl.position.set(bh.x, (layer.topElevation + layer.bottomElevation) / 2, bh.y);
          scene.add(cyl);
          addObjectInfo(cyl, { type: 'BoreholeLayer', boreholeId: bh.id, borehole: bh.code, stratumCode: layer.stratumCode, stratumName: layer.stratumName, top: layer.topElevation, bottom: layer.bottomElevation }, pickables);
        });
      });
    }

    if (layers.vtu && project.geologicalModel?.vtuMesh?.points) {
      const points = project.geologicalModel.vtuMesh.points;
      const linePositions: number[] = [];
      project.geologicalModel.vtuMesh.cellBlocks?.forEach((block, blockIndex) => {
        if (blockIndex % vtuStride !== 0) return;
        edgesForCell(block).forEach(([a, b]) => {
          const pa = points[a]; const pb = points[b];
          if (!pa || !pb) return;
          linePositions.push(Number(pa[0] ?? 0), Number(pa[2] ?? 0), Number(pa[1] ?? 0), Number(pb[0] ?? 0), Number(pb[2] ?? 0), Number(pb[1] ?? 0));
        });
      });
      if (linePositions.length) {
        const geometry = new BufferGeometry();
        geometry.setAttribute('position', new Float32BufferAttribute(linePositions, 3));
        const material = new LineBasicMaterial({ color: 0x334155, transparent: true, opacity: 0.7, clippingPlanes });
        const lines = new LineSegments(geometry, material);
        lines.userData.info = { type: 'VTU mesh', cells: stats.vtuCells, fields: project.geologicalModel.vtuMesh.detectedFields?.join(', ') || '-' };
        scene.add(lines);
        pickables.push(lines);
      }
    }

    if (layers.excavation && project.excavation) {
      const top = project.excavation.topElevation;
      const bottom = project.excavation.bottomElevation;
      const pts = project.excavation.outline.points;
      const topPts = pts.map((p) => toVector3(p.x, p.y, top));
      topPts.push(topPts[0].clone());
      const bottomPts = pts.map((p) => toVector3(p.x, p.y, bottom));
      bottomPts.push(bottomPts[0].clone());
      const lineMat = new LineBasicMaterial({ color: 0x2563eb, linewidth: 2, clippingPlanes });
      scene.add(new Line(new BufferGeometry().setFromPoints(topPts), lineMat));
      scene.add(new Line(new BufferGeometry().setFromPoints(bottomPts), lineMat));
      pts.forEach((p) => scene.add(new Line(new BufferGeometry().setFromPoints([toVector3(p.x, p.y, top), toVector3(p.x, p.y, bottom)]), lineMat)));
    }

    const isHighlighted = (id?: string, code?: string) => Boolean(highlightId && (highlightId === id || highlightId === code));
    const applyHighlight = (mesh: Mesh, selected: boolean) => {
      if (!selected) return;
      const mat = mesh.material as MeshStandardMaterial;
      mat.color = new Color(0xeab308);
      mat.emissive = new Color(0x854d0e);
      mat.emissiveIntensity = 0.18;
      mat.opacity = 1.0;
      mesh.scale.multiplyScalar(1.08);
    };

    if (project.retainingSystem) {
      if (layers.walls) project.retainingSystem.diaphragmWalls.forEach((wall) => {
        const a = wall.axis.points[0]; const b = wall.axis.points[wall.axis.points.length - 1];
        if (!a || !b) return;
        const length = planLength(a, b);
        const heightWall = wall.topElevation - wall.bottomElevation;
        const geometry = new BoxGeometry(length, heightWall, wall.thickness);
        const selected = isHighlighted(wall.id, wall.panelCode);
        const material = new MeshStandardMaterial({ color: selected ? 0xeab308 : 0x64748b, transparent: true, opacity: selected ? 1.0 : 0.82, clippingPlanes });
        const mesh = new Mesh(geometry, material);
        mesh.position.set((a.x + b.x) / 2, wall.bottomElevation + heightWall / 2, (a.y + b.y) / 2);
        mesh.rotation.y = -planAngle(a, b);
        applyHighlight(mesh, selected);
        scene.add(mesh);
        addObjectInfo(mesh, { type: 'DiaphragmWall', id: wall.id, code: wall.panelCode, thickness: wall.thickness, top: wall.topElevation, bottom: wall.bottomElevation, check: wall.designResults?.checkStatus, maxMomentDesign: wall.designResults?.maxMomentDesign, rebar: wall.reinforcement.map((r) => `${r.name} D${r.diameter}${r.spacing ? '@' + r.spacing : ''}`).join('; ') }, pickables);
      });
      const addBeam = (code: string, a: Point2D, b: Point2D, elevation: number, width = 0.8, heightBeam = 0.8, materialColor = 0x0f766e, info: Record<string, unknown> = {}) => {
        const length = planLength(a, b);
        const geometry = new BoxGeometry(length, heightBeam, width);
        const selected = isHighlighted(String(info.id ?? ''), code);
        const material = new MeshStandardMaterial({ color: selected ? 0xeab308 : materialColor, transparent: true, opacity: selected ? 1.0 : 0.86, clippingPlanes });
        const mesh = new Mesh(geometry, material);
        mesh.position.set((a.x + b.x) / 2, elevation, (a.y + b.y) / 2);
        mesh.rotation.y = -planAngle(a, b);
        applyHighlight(mesh, selected);
        scene.add(mesh);
        addObjectInfo(mesh, { code, ...info, highlighted: selected }, pickables);
      };
      if (layers.beams) [...project.retainingSystem.crownBeams, ...project.retainingSystem.waleBeams, ...(project.retainingSystem.ringBeams ?? [])].forEach((beam) => {
        const a = beam.axis.points[0]; const b = beam.axis.points[beam.axis.points.length - 1];
        if (!a || !b) return;
        const color = beam.beamRole === 'ring_beam' ? 0x7c3aed : 0x0369a1;
        addBeam(beam.code, a, b, beam.elevation, beam.section.width ?? 0.8, beam.section.height ?? 0.8, color, { type: 'Beam', id: beam.id, code: beam.code, role: beam.beamRole, level: beam.supportLevel, section: beam.section.name, material: beam.material.grade, maxMomentDesign: beam.designResult?.maxMomentDesign, maxShearDesign: beam.designResult?.maxShearDesign, maxDeflection: beam.designResult?.maxDeflection, status: beam.designResult?.checkStatus });
      });
      if (layers.supports) project.retainingSystem.supports.forEach((support) => {
        const issue = supportIssueMap.get(support.id);
        const color = issueColor(issue) ?? (layers.results ? supportCloudColor(support.designAxialForce, stats.maxSupportAxialForce) : (support.supportRole === 'ring_strut' ? 0x9333ea : support.supportRole === 'corner_diagonal' ? 0xf97316 : support.supportRole === 'secondary_strut' ? 0x0891b2 : 0xdc2626));
        addBeam(support.code, support.start, support.end, support.elevation, support.section.width ?? 0.8, support.section.height ?? 0.8, color, { type: 'InternalSupport', id: support.id, code: support.code, role: support.supportRole, level: support.levelIndex, axialForce: support.designAxialForce, preload: support.preload, thermalAxialForce: support.thermalAxialForce, gapClosureForce: support.gapClosureForce, spanLength: support.spanLength, baySpacing: support.baySpacing, startFace: support.startFaceCode, endFace: support.endFaceCode, centerlineOffset: support.centerlineOffsetM, startWallClearance: support.startWallClearanceM, endWallClearance: support.endWallClearanceM, topologyFamily: support.topologyFamily, startTributaryWidth: support.startTributaryWidth, endTributaryWidth: support.endTributaryWidth, section: support.section.name, material: support.material.grade, qualityIssue: issue ?? 'none' });
        // The support centreline is offset from the wall/wale. Short rigid links
        // make the force-transfer endpoint visible without merging both solids.
        if (support.startWallConnection && planLength(support.startWallConnection, support.start) > 0.05) {
          addBeam(`${support.code}-WS`, support.startWallConnection, support.start, support.elevation, Math.min(0.35, support.section.width ?? 0.35), Math.min(0.35, support.section.height ?? 0.35), 0x64748b, { type: 'SupportRigidLink', supportId: support.id, endpoint: 'start', clearance: support.startWallClearanceM });
        }
        if (support.endWallConnection && planLength(support.end, support.endWallConnection) > 0.05) {
          addBeam(`${support.code}-WE`, support.end, support.endWallConnection, support.elevation, Math.min(0.35, support.section.width ?? 0.35), Math.min(0.35, support.section.height ?? 0.35), 0x64748b, { type: 'SupportRigidLink', supportId: support.id, endpoint: 'end', clearance: support.endWallClearanceM });
        }
      });
      if (layers.supports) project.retainingSystem.supportNodes?.forEach((node) => {
        const geometry = new SphereGeometry(0.75, 16, 12);
        const selected = isHighlighted(node.id, node.code);
        const material = new MeshStandardMaterial({ color: selected ? 0xeab308 : node.checkStatus === 'fail' ? 0xef4444 : 0x0f766e, transparent: true, opacity: selected ? 1.0 : 0.88, clippingPlanes });
        const mesh = new Mesh(geometry, material);
        mesh.position.set(node.location.x, node.elevation, node.location.y);
        applyHighlight(mesh, selected);
        scene.add(mesh);
        addObjectInfo(mesh, { type: 'SupportWaleNode', id: node.id, code: node.code, support: node.supportCode, face: node.faceCode, waleBeam: node.waleBeamCode, bearingStress: node.bearingPlate?.bearingStress, bearingCapacity: node.bearingPlate?.bearingCapacity, status: node.checkStatus }, pickables);
      });
      if (layers.columns) project.retainingSystem.columns.forEach((column) => {
        const h = column.topElevation - column.bottomElevation;
        const geometry = new BoxGeometry(column.section.width ?? 0.6, h, column.section.height ?? column.section.width ?? 0.6);
        const selected = isHighlighted(column.id, column.code);
        const material = new MeshStandardMaterial({ color: selected ? 0xeab308 : 0x78350f, transparent: true, opacity: selected ? 1.0 : 0.86, clippingPlanes });
        const mesh = new Mesh(geometry, material);
        mesh.position.set(column.location.x, column.bottomElevation + h / 2, column.location.y);
        applyHighlight(mesh, selected);
        scene.add(mesh);
        addObjectInfo(mesh, { type: 'TemporaryColumn', id: column.id, code: column.code, top: column.topElevation, bottom: column.bottomElevation, section: column.section.name, supportCodes: column.supportCodes?.join(', '), foundationType: column.foundationDesign?.foundationType, pileLength: column.foundationDesign?.pileLength, pileCapacity: column.foundationDesign?.pileCapacity }, pickables);
      });
    }

    let theta = Math.PI / 4;
    let phi = Math.PI / 4;
    let radius = Math.max(bbox.size * 1.8, 45);
    const target = bbox.center.clone();
    const updateCamera = () => {
      phi = Math.max(0.12, Math.min(Math.PI / 2.05, phi));
      camera.position.set(target.x + radius * Math.sin(phi) * Math.cos(theta), target.y + radius * Math.cos(phi), target.z + radius * Math.sin(phi) * Math.sin(theta));
      camera.lookAt(target);
    };
    updateCamera();

    let dragging = false;
    let moved = false;
    let lastX = 0;
    let lastY = 0;
    const onPointerDown = (event: PointerEvent) => { dragging = true; moved = false; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); };
    const raycaster = new Raycaster();
    let hovered: Object3D | undefined;
    const getHoverMaterial = (object?: Object3D) => {
      const material = (object as Mesh).material as MeshStandardMaterial | Material[] | undefined;
      return Array.isArray(material) ? undefined : material as MeshStandardMaterial | undefined;
    };
    const setHoverMesh = (next?: Object3D) => {
      if (hovered === next) return;
      if (hovered) {
        const mat = getHoverMaterial(hovered);
        const original = hovered.userData.__hoverOriginal as { color?: number; emissive?: number; emissiveIntensity?: number; scale: number } | undefined;
        if (mat && original) {
          if (original.color !== undefined && mat.color) mat.color.setHex(original.color);
          if (original.emissive !== undefined && mat.emissive) mat.emissive.setHex(original.emissive);
          if (original.emissiveIntensity !== undefined) mat.emissiveIntensity = original.emissiveIntensity;
          hovered.scale.setScalar(original.scale);
        }
      }
      hovered = next;
      if (hovered) {
        const mat = getHoverMaterial(hovered);
        if (mat?.color) {
          hovered.userData.__hoverOriginal = { color: mat.color.getHex(), emissive: mat.emissive?.getHex?.(), emissiveIntensity: mat.emissiveIntensity, scale: hovered.scale.x || 1 };
          mat.color.setHex(0x38bdf8);
          if (mat.emissive) { mat.emissive.setHex(0x075985); mat.emissiveIntensity = 0.28; }
          hovered.scale.setScalar((hovered.scale.x || 1) * 1.04);
        }
      }
      renderer.domElement.style.cursor = hovered ? 'pointer' : 'default';
    };
    const pickObject = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(mouse, camera);
      return raycaster.intersectObjects(pickables, true)[0];
    };
    const onPointerMove = (event: PointerEvent) => {
      if (!dragging) {
        let obj: Object3D | undefined = pickObject(event)?.object;
        while (obj && !obj.userData.info) obj = obj.parent ?? undefined;
        setHoverMesh(obj);
        setHoverInfo(obj?.userData.info);
        return;
      }
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      moved = moved || Math.abs(dx) + Math.abs(dy) > 3;
      lastX = event.clientX; lastY = event.clientY;
      if (event.shiftKey || event.buttons === 4) {
        const panScale = radius / 750;
        const right = new Vector3().subVectors(camera.position, target).cross(camera.up).normalize();
        const up = camera.up.clone().normalize();
        target.addScaledVector(right, -dx * panScale).addScaledVector(up, dy * panScale);
      } else {
        theta -= dx * 0.006;
        phi -= dy * 0.006;
      }
      updateCamera();
    };
    const onPointerUp = (event: PointerEvent) => {
      dragging = false;
      renderer.domElement.releasePointerCapture(event.pointerId);
      if (moved) return;
      const hit = pickObject(event);
      if (measureMode && hit?.point) {
        if (!measureStart) { setMeasureStart(hit.point.clone()); setMeasureText('已选取测量起点，请点击终点。'); }
        else { const d = measureStart.distanceTo(hit.point); setMeasureText(`测距 ${d.toFixed(3)} m`); setMeasureStart(undefined); }
      }
      let obj: Object3D | undefined = hit?.object;
      while (obj && !obj.userData.info) obj = obj.parent ?? undefined;
      setSelected(obj?.userData.info);
    };
    const onWheel = (event: WheelEvent) => { event.preventDefault(); radius *= event.deltaY > 0 ? 1.08 : 0.92; radius = Math.max(5, Math.min(5000, radius)); updateCamera(); };
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false });

    const resizeObserver = new ResizeObserver(() => {
      const w = Math.max(mount.clientWidth, 640);
      const h = Math.max(mount.clientHeight, 420);
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    resizeObserver.observe(mount);
    let raf = 0;
    const animate = () => { raf = requestAnimationFrame(animate); renderer.render(scene, camera); };
    animate();
    return () => {
      setHoverMesh(undefined);
      setHoverInfo(undefined);
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('wheel', onWheel);
      renderer.dispose();
      scene.traverse((object: Object3D) => {
        const mesh = object as Mesh;
        mesh.geometry?.dispose?.();
        const material = mesh.material as Material | Material[] | undefined;
        if (Array.isArray(material)) material.forEach((item) => item.dispose()); else material?.dispose?.();
      });
      mount.innerHTML = '';
    };
  }, [project, layers, opacity, clip, clipAxis, clipOffset, focus, stats.vtuCells, stats.maxSupportAxialForce, measureMode, measureStart, supportIssueMap, highlightId, effectiveQuality, vtuStride, radialSegments]);

  const layerEntries = Object.entries(layers) as [LayerKey, boolean][];
  return (
    <div className="viewer threeViewerShell">
      <div className="viewerHeader">
        <div>
          <h3>工程三维视图</h3>
          <p className="small">左键旋转，Shift+拖拽平移，滚轮缩放，点击构件查看属性。剖切面为按 X 方向的快速剖切。</p>
        </div>
        <div className="viewerStats"><span>钻孔 {stats.boreholes}</span><span>地层面 {stats.surfaces}</span><span>VTU 单元 {stats.vtuCells}</span><span>墙 {stats.walls}</span><span>支撑 {stats.supports}</span><span>LOD {effectiveQuality}</span></div>
      </div>
      <div className="viewerControls">
        {layerEntries.map(([key, value]) => (
          <label key={key}><input type="checkbox" checked={value} onChange={(event) => setLayers((prev) => ({ ...prev, [key]: event.target.checked }))} /> {key}</label>
        ))}
        <label>渲染质量 <select value={renderQuality} onChange={(event) => setRenderQuality(event.target.value as 'auto' | 'performance' | 'balanced' | 'high')}><option value="auto">自动（当前 {effectiveQuality}）</option><option value="performance">性能优先</option><option value="balanced">均衡</option><option value="high">高质量</option></select></label>
        <label>透明度 <input type="range" min="0.15" max="1" step="0.05" value={opacity} onChange={(event) => setOpacity(Number(event.target.value))} /></label>
        <label><input type="checkbox" checked={clip} onChange={(event) => setClip(event.target.checked)} /> 剖切</label>
        <label>剖切轴 <select value={clipAxis} onChange={(event) => setClipAxis(event.target.value as 'x' | 'y' | 'z')}><option value="x">X</option><option value="y">Z/标高</option><option value="z">Y</option></select></label>
        <label>剖切位置 <input type="range" min="-80" max="120" step="1" value={clipOffset} onChange={(event) => setClipOffset(Number(event.target.value))} /></label>
        <button className="secondary" onClick={() => setMeasureMode((v) => !v)}>{measureMode ? '关闭测距' : '测距'}</button>
        <button className="secondary" onClick={() => maxAxialSupport && setSelected({ type: 'MaxSupportAxialForce', code: maxAxialSupport.code, designAxialForce: maxAxialSupport.designAxialForce, spanLength: maxAxialSupport.spanLength })}>定位最大轴力支撑</button>
        <button className="secondary" onClick={() => maxMomentWall && setSelected({ type: 'MaxWallMoment', code: maxMomentWall.panelCode, maxMomentDesign: maxMomentWall.designResults?.maxMomentDesign, check: maxMomentWall.designResults?.checkStatus })}>定位最大弯矩墙段</button>
      </div>
      {reviewIssues.length > 0 && <div className="reviewJumpPanel">
        <strong>审查定位</strong>
        {reviewIssues.map((issue, idx) => (
          <button key={`${issue.ruleId}-${idx}`} onClick={() => setSelected({ type: 'CheckIssue', ruleId: issue.ruleId, status: issue.status, objectId: issue.objectId, objectType: issue.objectType, message: issue.message })}>
            {String(issue.status)} · {String(issue.ruleId).slice(0, 28)}
          </button>
        ))}
      </div>}
      {measureText && <div className="warning">{measureText}</div>}
      {layers.results && <div className="viewerLegend"><span>支撑轴力云图：</span><span className="legendLow">低</span><span className="legendMid">中</span><span className="legendHigh">高</span><span>；质量高亮：红=支撑交叉/严重超限，橙=间距/避让/立柱服务警告。</span></div>}
      <div className="threeViewport" ref={mountRef} />
      {hoverInfo && <div className="hoverObjectBadge"><strong>悬浮</strong><span>{String(hoverInfo.code ?? hoverInfo.borehole ?? hoverInfo.type ?? '-')}</span><em>{String(hoverInfo.type ?? '-')}</em></div>}
      <div className="propertyPanel threePropertyPanel">
        <strong>属性</strong>
        {selected ? (isBoreholeInfo(selected) ? <BoreholeDetailPanel project={project} info={selected} /> : <table className="table compactTable"><tbody>{humanInfo(selected).map(([key, value]) => <tr key={key}><td>{key}</td><td>{String(value ?? '-')}</td></tr>)}</tbody></table>) : <span className="small">未选择对象</span>}
      </div>
    </div>
  );
}
