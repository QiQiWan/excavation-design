import { useEffect, useMemo, useRef, useState } from 'react';
import { AmbientLight, BoxGeometry, BufferGeometry, CanvasTexture, Color, CylinderGeometry, DirectionalLight, Float32BufferAttribute, GridHelper, LineBasicMaterial, LineSegments, Material, Mesh, MeshStandardMaterial, Object3D, PerspectiveCamera, Plane, Raycaster, Scene, SphereGeometry, Sprite, SpriteMaterial, Vector2, Vector3, WebGLRenderer } from 'three';
import { api } from '../api/client';
import type { Project, RebarIfcVisualization, RebarVisualizationBar, RebarVisualizationCage } from '../types/domain';
import FullscreenShell from '../components/FullscreenShell';
import { bindWebglContextLifecycle, createStableWebGLRenderer, releaseStableWebGLRenderer, stablePixelRatio, startStableRenderLoop } from './webglRuntime';

type HostFilter = 'all' | 'diaphragm_wall' | 'wale_or_crown_beam' | 'internal_support' | 'support_wale_node';
type BarFilter = 'all' | 'longitudinal' | 'distribution' | 'stirrup' | 'tie' | 'additional';
type ColorMode = 'barType' | 'status' | 'host';

const BAR_TYPE_LABELS: Record<string, string> = { longitudinal: '纵筋', distribution: '分布筋', stirrup: '箍筋', tie: '拉结/架立筋', additional: '附加筋' };
type ClipAxis = 'x' | 'y' | 'z';

function pointToVector(p: { x: number; y: number; z: number }): Vector3 {
  return new Vector3(p.x, p.z, p.y);
}

function barTypeColor(bar: RebarVisualizationBar): number {
  if (bar.barType === 'longitudinal') return 0x2563eb;
  if (bar.barType === 'distribution') return 0x0d9488;
  if (bar.barType === 'stirrup') return 0x9333ea;
  if (bar.barType === 'tie') return 0x64748b;
  return 0xea580c;
}

function statusColor(bar: RebarVisualizationBar): number {
  if (bar.checkStatus === 'fail') return 0xdc2626;
  if (bar.checkStatus === 'warning' || bar.checkStatus === 'manual_review') return 0xf59e0b;
  return 0x16a34a;
}

function hostColor(bar: RebarVisualizationBar): number {
  if (bar.hostType === 'diaphragm_wall') return 0x1d4ed8;
  if (bar.hostType === 'wale_or_crown_beam') return 0x0891b2;
  if (bar.hostType === 'internal_support') return 0x7c3aed;
  return 0xea580c;
}

function barColor(bar: RebarVisualizationBar, mode: ColorMode): number {
  if (mode === 'status') return statusColor(bar);
  if (mode === 'host') return hostColor(bar);
  return barTypeColor(bar);
}

function drawingRefsFor(info: Record<string, unknown>): string[] {
  if (Array.isArray(info.drawingRefs)) return info.drawingRefs.map(String);
  const hostType = String(info.hostType ?? '');
  if (hostType === 'diaphragm_wall') return ['R-01', 'R-02', 'R-03', 'D-04', 'D-06'];
  if (hostType === 'internal_support') return ['R-04', 'D-01', 'D-03', 'D-07'];
  if (hostType === 'wale_or_crown_beam') return ['R-05', 'D-01'];
  if (hostType === 'support_wale_node') return ['D-01', 'D-02'];
  return [];
}

function addCylinderBetween(scene: Scene, start: Vector3, end: Vector3, radius: number, color: number, info: Record<string, unknown>, pickables: Object3D[], clippingPlanes: Plane[]) {
  const delta = new Vector3().subVectors(end, start);
  const length = delta.length();
  if (length <= 1e-6) return;
  const geometry = new CylinderGeometry(radius, radius, length, 8, 1, false);
  const material = new MeshStandardMaterial({ color, metalness: 0.25, roughness: 0.48, clippingPlanes });
  const mesh = new Mesh(geometry, material);
  mesh.position.copy(start).add(end).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(new Vector3(0, 1, 0), delta.clone().normalize());
  mesh.userData.info = info;
  scene.add(mesh);
  pickables.push(mesh);
}


function addBarPolyline(scene: Scene, bar: RebarVisualizationBar, radius: number, color: number, info: Record<string, unknown>, pickables: Object3D[], clippingPlanes: Plane[]) {
  const pts = (bar.points?.length ? bar.points : [bar.start, bar.end]).map(pointToVector);
  for (let i = 0; i < pts.length - 1; i += 1) {
    addCylinderBetween(scene, pts[i], pts[i + 1], radius, color, info, pickables, clippingPlanes);
  }
  if ((bar.shapeKind ?? '').includes('closed') && pts.length > 2) {
    const sphereGeo = new SphereGeometry(radius * 1.35, 8, 8);
    const sphereMat = new MeshStandardMaterial({ color, metalness: 0.25, roughness: 0.48, clippingPlanes });
    pts.slice(0, -1).forEach((pt) => { const node = new Mesh(sphereGeo, sphereMat); node.position.copy(pt); node.userData.info = info; scene.add(node); pickables.push(node); });
  }
}

function addCageGrid(scene: Scene, cage: RebarVisualizationCage, clippingPlanes: Plane[]) {
  const rawPath = (cage.planPath?.length ? cage.planPath : [cage.start, cage.end]).map(pointToVector);
  const path: Vector3[] = [];
  rawPath.forEach((point) => {
    if (!path.length || path[path.length - 1].distanceTo(point) > 1e-6) path.push(point);
  });
  if (path.length < 2) return;
  const cumulative: number[] = [0];
  for (let i = 0; i < path.length - 1; i += 1) cumulative.push(cumulative[cumulative.length - 1] + path[i].distanceTo(path[i + 1]));
  const length = Math.max(cumulative[cumulative.length - 1], 0.01);
  const pointTangentAt = (chainage: number): { point: Vector3; tangent: Vector3 } => {
    const target = Math.max(0, Math.min(length, chainage));
    let index = path.length - 2;
    for (let i = 0; i < cumulative.length - 1; i += 1) {
      if (target <= cumulative[i + 1] + 1e-8) { index = i; break; }
    }
    const segmentLength = Math.max(cumulative[index + 1] - cumulative[index], 1e-9);
    const t = Math.max(0, Math.min(1, (target - cumulative[index]) / segmentLength));
    const point = path[index].clone().lerp(path[index + 1], t);
    const tangent = path[index + 1].clone().sub(path[index]).setY(0).normalize();
    return { point, tangent };
  };
  const offsetPath = (offset: number): Vector3[] => {
    const tangents: Vector3[] = [];
    const normals: Vector3[] = [];
    for (let i = 0; i < path.length - 1; i += 1) {
      const tangent = path[i + 1].clone().sub(path[i]).setY(0).normalize();
      tangents.push(tangent);
      normals.push(new Vector3(-tangent.z, 0, tangent.x));
    }
    return path.map((point, index) => {
      if (index === 0) return point.clone().addScaledVector(normals[0], offset);
      if (index === path.length - 1) return point.clone().addScaledVector(normals[normals.length - 1], offset);
      const miter = normals[index - 1].clone().add(normals[index]);
      if (miter.lengthSq() <= 1e-10) return point.clone().addScaledVector(normals[index], offset);
      miter.normalize();
      const denom = Math.max(0.15, Math.abs(miter.dot(normals[index])));
      const scale = Math.sign(offset || 1) * Math.min(Math.abs(offset) / denom, Math.abs(offset) * 3);
      return point.clone().addScaledVector(miter, scale);
    });
  };
  const topY = cage.topElevation;
  const bottomY = cage.bottomElevation;
  const cap = Math.max(20, cage.displayLineCap ?? 160);
  const positions: number[] = [];
  const pushSegment = (a: Vector3, b: Vector3) => { positions.push(a.x, a.y, a.z, b.x, b.y, b.z); };
  const pushPath = (points: Vector3[], y: number) => {
    for (let i = 0; i < points.length - 1; i += 1) {
      const a = points[i].clone(); const b = points[i + 1].clone();
      a.y = y; b.y = y; pushSegment(a, b);
    }
  };
  cage.faces.forEach((faceSpec) => {
    const sign = faceSpec.face === 'inner' ? -1 : 1;
    const offset = Math.max(cage.thicknessM / 2 - cage.coverM, 0.02) * sign;
    const facePath = offsetPath(offset);
    const vCount = Math.max(2, Math.min(cap, faceSpec.estimatedVerticalBarCount || Math.floor(length / Math.max(faceSpec.spacingMm / 1000, 0.05)) + 1));
    for (let i = 0; i < vCount; i += 1) {
      const chainage = length * i / Math.max(vCount - 1, 1);
      const { point, tangent } = pointTangentAt(chainage);
      const normal = new Vector3(-tangent.z, 0, tangent.x);
      point.addScaledVector(normal, offset);
      pushSegment(new Vector3(point.x, bottomY + 0.1, point.z), new Vector3(point.x, topY - 0.1, point.z));
    }
    const estimatedH = cage.horizontal.estimatedBarCountPerFace || Math.floor(cage.heightM / Math.max(cage.horizontal.spacingMm / 1000, 0.05)) + 1;
    const hCount = Math.max(2, Math.min(cap, estimatedH));
    for (let i = 0; i < hCount; i += 1) {
      const t = i / Math.max(hCount - 1, 1);
      const y = bottomY + 0.1 + Math.max(cage.heightM - 0.2, 0.01) * t;
      pushPath(facePath, y);
    }
  });
  const tieSpacing = Math.max(cage.ties.spacingMm / 1000, 0.25);
  const tieCount = Math.max(2, Math.min(24, Math.floor(length / tieSpacing) + 1));
  for (let i = 0; i < tieCount; i += 1) {
    const chainage = length * i / Math.max(tieCount - 1, 1);
    const { point, tangent } = pointTangentAt(chainage);
    const normal = new Vector3(-tangent.z, 0, tangent.x);
    const half = Math.max(cage.thicknessM / 2 - cage.coverM, 0.02);
    const a = point.clone().addScaledVector(normal, -half);
    const b = point.clone().addScaledVector(normal, half);
    a.y = b.y = (topY + bottomY) / 2; pushSegment(a, b);
  }
  if (!positions.length) return;
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
  const material = new LineBasicMaterial({ color: 0x1d4ed8, transparent: true, opacity: 0.72, clippingPlanes });
  const lines = new LineSegments(geometry, material);
  lines.userData.info = { type: 'RebarCage', hostId: cage.hostId, hostCode: cage.hostCode, panelCode: cage.panelCode, panelLengthM: cage.panelLengthM, topElevation: cage.topElevation, bottomElevation: cage.bottomElevation, verticalBars: cage.faces.map((row) => `${row.face}:D${row.diameterMm}@${row.spacingMm}`).join('; '), horizontalBars: `D${cage.horizontal.diameterMm}@${cage.horizontal.spacingMm}`, ties: `D${cage.ties.diameterMm}@${cage.ties.spacingMm}`, zoneIds: cage.zoneIds, segmentCount: cage.segmentCount, cageStatus: cage.cageStatus, geometryStatus: cage.geometryStatus, storedGeometryDeviationM: cage.storedGeometryDeviationM };
  scene.add(lines);

  const jointPositions: number[] = [];
  (cage.jointMarkers ?? []).forEach((marker) => {
    const p = pointToVector(marker.point);
    jointPositions.push(p.x, bottomY, p.z, p.x, topY, p.z);
  });
  if (jointPositions.length) {
    const jointGeometry = new BufferGeometry();
    jointGeometry.setAttribute('position', new Float32BufferAttribute(jointPositions, 3));
    const jointLines = new LineSegments(jointGeometry, new LineBasicMaterial({ color: 0xea580c, transparent: true, opacity: 0.92, clippingPlanes }));
    jointLines.userData.info = { type: 'ConstructionJoint', hostCode: cage.hostCode, panelCode: cage.panelCode, jointType: cage.jointType };
    scene.add(jointLines);
  }

  const liftingGeometry = new SphereGeometry(Math.max(0.08, Math.min(cage.thicknessM * 0.18, 0.18)), 10, 10);
  (cage.liftingPoints ?? []).forEach((lifting) => {
    const marker = new Mesh(liftingGeometry, new MeshStandardMaterial({ color: lifting.reviewRequired ? 0xf59e0b : 0x16a34a, metalness: 0.15, roughness: 0.45, clippingPlanes }));
    marker.position.copy(pointToVector(lifting.point));
    marker.userData.info = { type: 'CageLiftingPoint', hostCode: cage.hostCode, panelCode: cage.panelCode, liftingPointId: lifting.id, ratio: lifting.ratio, reviewRequired: lifting.reviewRequired };
    scene.add(marker);
  });

  const splicePositions: number[] = [];
  (cage.spliceZones ?? []).forEach((zone) => {
    for (let i = 0; i < path.length - 1; i += 1) {
      const a = path[i].clone(); const b = path[i + 1].clone();
      a.y = zone.elevation; b.y = zone.elevation;
      splicePositions.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
  });
  if (splicePositions.length) {
    const spliceGeometry = new BufferGeometry();
    spliceGeometry.setAttribute('position', new Float32BufferAttribute(splicePositions, 3));
    const spliceLines = new LineSegments(spliceGeometry, new LineBasicMaterial({ color: 0x9333ea, transparent: true, opacity: 0.88, clippingPlanes }));
    spliceLines.userData.info = { type: 'CageSpliceZone', hostCode: cage.hostCode, panelCode: cage.panelCode, spliceZones: cage.spliceZones };
    scene.add(spliceLines);
  }
}

function makeTextSprite(text: string, color = '#0f172a') {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  canvas.width = 256; canvas.height = 72;
  if (ctx) {
    ctx.fillStyle = 'rgba(255,255,255,0.92)';
    ctx.strokeStyle = 'rgba(148,163,184,0.85)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(8, 8, 240, 48, 14);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = color;
    ctx.font = 'bold 24px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 128, 34);
  }
  const texture = new CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new Sprite(material);
  sprite.scale.set(3.2, 0.9, 1);
  return sprite;
}

function supportAnnotation(bar: RebarVisualizationBar): string | undefined {
  if (bar.hostType !== 'internal_support') return undefined;
  if (bar.barType === 'stirrup' && bar.stirrupZoneLabel) return bar.stirrupZoneLabel;
  if (bar.barType === 'stirrup' && bar.stirrupZoneType === 'end_left') return 'A端加密箍筋';
  if (bar.barType === 'stirrup' && bar.stirrupZoneType === 'middle') return '跨中普通箍筋';
  if (bar.barType === 'stirrup' && bar.stirrupZoneType === 'end_right') return 'B端加密箍筋';
  const rep = String(bar.representation ?? '');
  if (rep.includes('longitudinal')) return '错开搭接';
  if (rep.includes('closed_stirrups')) return '闭合箍筋（未分区）';
  if (rep.includes('distribution')) return '分布筋';
  if (rep.includes('tie')) return '拉结/架立';
  if (rep.includes('additional')) return '搭接加强';
  return undefined;
}

function addSupportDetailLabels(scene: Scene, bars: RebarVisualizationBar[], clippingPlanes: Plane[]) {
  const placed = new Set<string>();
  bars.forEach((bar) => {
    const label = supportAnnotation(bar);
    if (!label) return;
    const key = `${bar.hostCode}-${label}`;
    if (placed.has(key) || placed.size >= 32) return;
    placed.add(key);
    const pts = (bar.points?.length ? bar.points : [bar.start, bar.end]).map(pointToVector);
    if (!pts.length) return;
    const pos = pts[Math.floor(pts.length / 2)].clone();
    pos.y += label.includes('箍筋') ? 0.72 : label.includes('搭接') ? 0.95 : 0.55;
    const sprite = makeTextSprite(label, label.includes('搭接') ? '#c2410c' : label.includes('箍筋') ? '#6d28d9' : '#075985');
    sprite.position.copy(pos);
    sprite.userData.info = { type: 'RebarAnnotation', hostCode: bar.hostCode, label, barType: bar.barType };
    scene.add(sprite);
    if (label === '错开搭接') {
      const anchorA = makeTextSprite('端部锚固', '#166534');
      anchorA.position.copy(pts[0]).add(new Vector3(0, 0.75, 0));
      scene.add(anchorA);
      const anchorB = makeTextSprite('端部锚固', '#166534');
      anchorB.position.copy(pts[pts.length - 1]).add(new Vector3(0, 0.75, 0));
      scene.add(anchorB);
    }
    void clippingPlanes;
  });
}

function boundsFromBars(bars: RebarVisualizationBar[], project: Project, includeProjectOutline = true) {
  const xs: number[] = [];
  const ys: number[] = [];
  const zs: number[] = [];
  bars.forEach((bar) => {
    const pts = bar.points?.length ? bar.points : [bar.start, bar.end];
    pts.forEach((p) => { xs.push(p.x); ys.push(p.y); zs.push(p.z); });
  });
  if (includeProjectOutline) project.excavation?.outline.points.forEach((p) => { xs.push(p.x); ys.push(p.y); });
  if (!xs.length) { xs.push(0, 60); ys.push(0, 30); zs.push(-18, 0); }
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const minZ = includeProjectOutline ? Math.min(...zs, project.excavation?.bottomElevation ?? -12) : Math.min(...zs);
  const maxZ = includeProjectOutline ? Math.max(...zs, project.excavation?.topElevation ?? 0) : Math.max(...zs);
  const size = Math.max(maxX - minX, maxY - minY, Math.max(maxZ - minZ, 1) * 1.8, includeProjectOutline ? 20 : 2.5);
  return { center: new Vector3((minX + maxX) / 2, (minZ + maxZ) / 2, (minY + maxY) / 2), size, minX, maxX, minY, maxY, minZ, maxZ };
}

function humanInfo(info: Record<string, unknown>): [string, string][] {
  const labels: Record<string, string> = {
    ifcClass: 'IFC 类', hostType: '宿主类型', hostCode: '宿主编号', groupName: '钢筋组', barType: '钢筋类型', diameterMm: '直径(mm)', spacingMm: '间距(mm)', count: '数量', grade: '钢筋等级', lengthM: '单根长度(m)', checkStatus: '设计状态', estimatedFullCount: '该组估算总数', sampledFromCount: '近景显示数量', locationDescription: '位置说明', zoneId: '配筋分区', zoneType: '分区类型', face: '墙体侧别', drawingRefs: '关联图纸', envelopeSource: '内力包络来源', zoneTopElevation: '分区顶标高', zoneBottomElevation: '分区底标高', stirrupZoneType: '箍筋区段', stirrupZoneLabel: '箍筋区段名称', zoneStartM: '区段起点(m)', zoneEndM: '区段终点(m)', previewStationM: '本箍筋里程(m)', geometricLegCount: '闭合箍肢数', designSource: '设计来源'
  };
  const translateValue = (key: string, value: unknown) => {
    const text = String(value ?? '-');
    const dictionaries: Record<string, Record<string, string>> = {
      hostType: { diaphragm_wall: '地下连续墙', wale_or_crown_beam: '冠梁/围檩', internal_support: '水平支撑', support_wale_node: '支撑节点' }, barType: BAR_TYPE_LABELS,
      checkStatus: { pass: '已通过', warning: '需关注', manual_review: '待工程师复核', fail: '不满足' },
      stirrupZoneType: { end_left: 'A端加密区', middle: '跨中普通区', end_right: 'B端加密区' },
      zoneType: { full_length: '全长', end_zones: '两端加密区', middle_zone: '跨中普通区', node_zone: '节点区', lap_zone: '搭接区' },
      designSource: { support_transverse_design: '支撑斜截面受剪与构造联合设计', applied_rebar_design_scheme: '已应用的正式配筋方案' },
    };
    return dictionaries[key]?.[text] ?? text;
  };
  return Object.entries(info).filter(([key]) => Boolean(labels[key])).map(([key, value]) => [labels[key], translateValue(key, value)]);
}

export default function RebarIfcViewer({ project, highlightLocator }: { project: Project; highlightLocator?: Record<string, unknown> }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [data, setData] = useState<RebarIfcVisualization | undefined>();
  const [globalData, setGlobalData] = useState<RebarIfcVisualization | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [renderError, setRenderError] = useState<string | undefined>();
  const [renderNonce, setRenderNonce] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hostFilter, setHostFilter] = useState<HostFilter>('all');
  const [barFilter, setBarFilter] = useState<BarFilter>('all');
  const [showHosts, setShowHosts] = useState(true);
  const [showCageGrid, setShowCageGrid] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [colorMode, setColorMode] = useState<ColorMode>('barType');
  const [hostOpacity, setHostOpacity] = useState(0.16);
  const [barScale, setBarScale] = useState(1);
  const [clip, setClip] = useState(false);
  const [clipAxis, setClipAxis] = useState<ClipAxis>('x');
  const [clipOffset, setClipOffset] = useState(0);
  const [selected, setSelected] = useState<Record<string, unknown> | undefined>();
  const [isolatedHost, setIsolatedHost] = useState<string | undefined>();
  const [supportViewMode, setSupportViewMode] = useState<'global' | 'support' | 'stirrup'>('global');
  const [focusSupportId, setFocusSupportId] = useState<string>('');
  const supportDetailMode = supportViewMode !== 'global';

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(undefined);
    api.getRebarIfcVisualization(project.id, 1600)
      .then((result) => { if (alive) { setData(result); setGlobalData(result); setSupportViewMode('global'); } })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [project.id, project.retainingSystem?.diaphragmWalls.length, project.retainingSystem?.supports.length, project.calculationResults.length]);

  const bars = useMemo(() => (data?.bars ?? []).filter((bar) => (hostFilter === 'all' || bar.hostType === hostFilter) && (barFilter === 'all' || bar.barType === barFilter) && (!isolatedHost || bar.hostId === isolatedHost || bar.hostCode === isolatedHost)), [data, hostFilter, barFilter, isolatedHost]);
  const supportTypesPresent = data?.summary.supportBarTypesPresent ?? [];
  const supportTypesMissing = data?.summary.supportBarTypesMissing ?? [];
  const supportCatalog = globalData?.supportContracts ?? data?.supportContracts ?? [];
  const representativeSupport = useMemo(() => supportCatalog.find((item) => item.status === 'complete' && item.stirrupZoneStatus === 'complete') ?? supportCatalog[0], [supportCatalog]);
  const selectedSupportKey = focusSupportId || representativeSupport?.hostId || representativeSupport?.hostCode || '';
  const activeContract = useMemo(() => (data?.supportContracts ?? []).find((item) => [item.hostId, item.hostCode].includes(selectedSupportKey)) ?? (data?.supportContracts ?? [])[0], [data, selectedSupportKey]);
  const openSupportDetail = async (stirrupOnly: boolean) => {
    const hostId = selectedSupportKey; if (!hostId) return;
    setLoading(true); setError(undefined);
    try {
      const result = await api.getRebarIfcVisualization(project.id, 520, hostId);
      setData(result); setFocusSupportId(hostId); setSupportViewMode(stirrupOnly ? 'stirrup' : 'support'); setHostFilter('internal_support'); setBarFilter(stirrupOnly ? 'stirrup' : 'all'); setIsolatedHost(hostId); setShowHosts(true); setShowCageGrid(false); setShowLabels(true); setBarScale(stirrupOnly ? 2.1 : 1.55); setSelected(undefined);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); } finally { setLoading(false); }
  };
  const refreshCurrentView = async () => {
    setLoading(true); setError(undefined);
    try { const focusId = supportDetailMode ? selectedSupportKey : undefined; const result = await api.getRebarIfcVisualization(project.id, supportDetailMode ? 520 : 1600, focusId); setData(result); if (!focusId) setGlobalData(result); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); } finally { setLoading(false); }
  };
  const exitSupportDetail = () => { if (globalData) setData(globalData); setSupportViewMode('global'); setHostFilter('all'); setBarFilter('all'); setIsolatedHost(undefined); setShowCageGrid(true); setBarScale(1); setSelected(undefined); };
  const maxDia = useMemo(() => Math.max(8, ...bars.map((bar) => Number(bar.diameterMm || 0))), [bars]);
  const viewerBounds = useMemo(() => boundsFromBars(bars, project, !supportDetailMode), [bars, project, supportDetailMode]);
  const clipRange = Math.max(5, viewerBounds.size * 0.7);
  const highlightId = String(highlightLocator?.objectId ?? highlightLocator?.objectCode ?? '');
  const transverseDesign = activeContract?.transverseDesign;
  const missingZoneLabels = (activeContract?.missingSampledStirrupZones ?? activeContract?.missingStirrupZones ?? []).map((zone) => ({ end_left: 'A端加密区', middle: '跨中普通区', end_right: 'B端加密区' }[zone] ?? zone));
  const contractHasProblem = (data?.summary.supportContractIncompleteCount ?? 0) > 0 || (data?.summary.supportStirrupZoneIncompleteCount ?? 0) > 0;
  const visibleContractIssue = contractHasProblem || supportTypesMissing.length > 0 || missingZoneLabels.length > 0;
  const evidenceLabel = ({ calculated_stage_envelope: '施工阶段内力包络', geometry_and_self_weight_only: '仅几何、自重和构造下限' } as Record<string, string>)[String(transverseDesign?.evidenceStatus ?? '')] ?? '尚未形成可追溯依据';

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !data) return;
    mount.innerHTML = '';
    const width = Math.max(mount.clientWidth, 640);
    const height = Math.max(mount.clientHeight, 460);
    const scene = new Scene();
    scene.background = new Color(0xf8fafc);
    const camera = new PerspectiveCamera(45, width / height, 0.1, 8000);
    let renderer: WebGLRenderer;
    try {
      renderer = createStableWebGLRenderer({ antialias: !supportDetailMode });
      setRenderError(undefined);
    } catch (reason) {
      setRenderError(reason instanceof Error ? reason.message : '无法创建配筋三维 WebGL 上下文。');
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(stablePixelRatio(supportDetailMode ? 1.35 : 1.5));
    renderer.localClippingEnabled = clip;
    mount.appendChild(renderer.domElement);
    const detachContextLifecycle = bindWebglContextLifecycle(renderer, {
      onLost: setRenderError,
      onRestored: () => { setRenderError(undefined); setRenderNonce((value) => value + 1); },
    });
    const bbox = boundsFromBars(bars, project, !supportDetailMode);
    const clipConfig: Record<ClipAxis, { normal: Vector3; center: number }> = {
      x: { normal: new Vector3(-1, 0, 0), center: bbox.center.x },
      y: { normal: new Vector3(0, 0, -1), center: bbox.center.z },
      z: { normal: new Vector3(0, -1, 0), center: bbox.center.y }
    };
    const clipPlane = new Plane(clipConfig[clipAxis].normal, clipConfig[clipAxis].center + clipOffset);
    const clippingPlanes = clip ? [clipPlane] : [];
    const pickables: Object3D[] = [];

    scene.add(new AmbientLight(0xffffff, 0.7));
    const light = new DirectionalLight(0xffffff, 0.9);
    light.position.set(bbox.center.x + bbox.size, bbox.center.y + bbox.size, bbox.center.z + bbox.size);
    scene.add(light);
    const grid = new GridHelper(Math.max(bbox.size * 1.4, 20), 20, 0xcbd5e1, 0xe2e8f0);
    grid.position.set(bbox.center.x, supportDetailMode ? bbox.minZ : (project.excavation?.bottomElevation ?? bbox.minZ), bbox.center.z);
    scene.add(grid);

    if (showHosts && project.retainingSystem) {
      const hostVisible = (type: HostFilter, id?: string, code?: string) =>
        (hostFilter === 'all' || hostFilter === type)
        && (!isolatedHost || isolatedHost === id || isolatedHost === code);
      project.retainingSystem.diaphragmWalls.forEach((wall) => {
        if (!hostVisible('diaphragm_wall', wall.id, wall.panelCode)) return;
        const wallCages = (data.cages ?? [])
          .filter((item) => item.hostId === wall.id || item.hostCode === wall.panelCode)
          .sort((a, b) => a.panelIndex - b.panelIndex);
        const hostPaths = wallCages.length
          ? wallCages.map((cage) => ({
              points: (cage.planPath?.length ? cage.planPath : [cage.start, cage.end]),
              topElevation: cage.topElevation,
              bottomElevation: cage.bottomElevation,
              thickness: cage.thicknessM,
              panelCode: cage.panelCode,
              recovered: true,
            }))
          : [{
              points: wall.axis.points,
              topElevation: wall.topElevation,
              bottomElevation: wall.bottomElevation,
              thickness: wall.thickness,
              panelCode: wall.panelCode,
              recovered: false,
            }];
        hostPaths.forEach((hostPath) => {
          for (let index = 0; index < hostPath.points.length - 1; index += 1) {
            const a = hostPath.points[index]; const b = hostPath.points[index + 1];
            if (!a || !b) continue;
            const length = Math.hypot(b.x - a.x, b.y - a.y);
            if (length <= 1e-6) continue;
            const h = hostPath.topElevation - hostPath.bottomElevation;
            const mesh = new Mesh(
              new BoxGeometry(Math.max(length, 0.1), Math.max(h, 0.1), Math.max(hostPath.thickness, 0.1)),
              new MeshStandardMaterial({ color: 0x94a3b8, transparent: true, opacity: hostOpacity, depthWrite: hostOpacity >= 0.55, clippingPlanes })
            );
            mesh.position.set((a.x + b.x) / 2, hostPath.bottomElevation + h / 2, (a.y + b.y) / 2);
            mesh.rotation.y = -Math.atan2(b.y - a.y, b.x - a.x);
            mesh.userData.info = { type: 'DiaphragmWallHost', hostId: wall.id, hostCode: wall.panelCode, constructionPanelCode: hostPath.panelCode, geometrySource: hostPath.recovered ? 'canonical_cage_path' : 'project_wall_axis' };
            scene.add(mesh);
          }
        });
      });
      [...project.retainingSystem.crownBeams, ...project.retainingSystem.waleBeams, ...(project.retainingSystem.ringBeams ?? [])].forEach((beam: any) => {
        if (!hostVisible('wale_or_crown_beam', beam.id, beam.code)) return;
        const a = beam.axis?.points?.[0] ?? beam.start;
        const b = beam.axis?.points?.[beam.axis?.points?.length - 1] ?? beam.end;
        if (!a || !b) return;
        const length = Math.hypot(b.x - a.x, b.y - a.y);
        const widthBeam = beam.section?.width ?? 0.8;
        const heightBeam = beam.section?.height ?? 0.8;
        const mesh = new Mesh(new BoxGeometry(Math.max(length, 0.1), heightBeam, widthBeam), new MeshStandardMaterial({ color: 0x0f172a, transparent: true, opacity: Math.max(0.04, hostOpacity * 0.78), depthWrite: hostOpacity >= 0.55, clippingPlanes }));
        mesh.position.set((a.x + b.x) / 2, beam.elevation ?? 0, (a.y + b.y) / 2);
        mesh.rotation.y = -Math.atan2(b.y - a.y, b.x - a.x);
        scene.add(mesh);
      });
      project.retainingSystem.supports.forEach((beam: any) => {
        if (!hostVisible('internal_support', beam.id, beam.code)) return;
        const a = beam.axis?.points?.[0] ?? beam.start;
        const b = beam.axis?.points?.[beam.axis?.points?.length - 1] ?? beam.end;
        if (!a || !b) return;
        const length = Math.hypot(b.x - a.x, b.y - a.y);
        const widthBeam = beam.section?.width ?? 0.8;
        const heightBeam = beam.section?.height ?? 0.8;
        const mesh = new Mesh(new BoxGeometry(Math.max(length, 0.1), heightBeam, widthBeam), new MeshStandardMaterial({ color: 0x334155, transparent: true, opacity: Math.max(0.04, hostOpacity * 0.72), depthWrite: hostOpacity >= 0.55, clippingPlanes }));
        mesh.position.set((a.x + b.x) / 2, beam.elevation ?? 0, (a.y + b.y) / 2);
        mesh.rotation.y = -Math.atan2(b.y - a.y, b.x - a.x);
        scene.add(mesh);
      });
    }

    if (showCageGrid && (hostFilter === 'all' || hostFilter === 'diaphragm_wall')) {
      (data.cages ?? []).filter((cage) => !isolatedHost || cage.hostId === isolatedHost || cage.hostCode === isolatedHost).forEach((cage) => addCageGrid(scene, cage, clippingPlanes));
    }

    bars.forEach((bar) => {
      const highlighted = Boolean(highlightId && (highlightId === bar.hostId || highlightId === bar.hostCode || highlightId === bar.id || highlightId === bar.groupId));
      const radiusBase = highlighted ? Math.max(0.05, Math.min(0.14, (bar.diameterMm / maxDia) * 0.11)) : Math.max(0.018, Math.min(0.09, (bar.diameterMm / maxDia) * 0.06));
      const familyScale = bar.barType === 'stirrup' ? 1.45 : bar.barType === 'tie' ? 1.18 : 1;
      const radiusValue = radiusBase * barScale * familyScale;
      addBarPolyline(scene, bar, radiusValue, highlighted ? 0xeab308 : barColor(bar, colorMode), { ...(bar as unknown as Record<string, unknown>), highlighted }, pickables, clippingPlanes);
    });
    if (showLabels) addSupportDetailLabels(scene, bars, clippingPlanes);

    let theta = Math.PI / 4;
    let phi = Math.PI / 3.2;
    let radius = supportDetailMode ? Math.max(bbox.size * 1.25, 5) : Math.max(bbox.size * 1.7, 30);
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
        const right = new Vector3().subVectors(camera.position, target).cross(camera.up).normalize();
        const up = camera.up.clone().normalize();
        target.addScaledVector(right, -dx * panScale).addScaledVector(up, dy * panScale);
      } else { theta -= dx * 0.006; phi -= dy * 0.006; }
      updateCamera();
    };
    const onPointerUp = (event: PointerEvent) => {
      dragging = false; renderer.domElement.releasePointerCapture(event.pointerId);
      if (moved) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      const raycaster = new Raycaster(); raycaster.setFromCamera(mouse, camera);
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
    const stopRenderLoop = startStableRenderLoop(renderer, scene, camera, mount, { maxFps: supportDetailMode ? 30 : 22 });
    return () => {
      stopRenderLoop();
      detachContextLifecycle();
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('wheel', onWheel);
      releaseStableWebGLRenderer(renderer, scene, mount);
      mount.innerHTML = '';
    };
  }, [data, bars, showHosts, showCageGrid, showLabels, hostOpacity, barScale, colorMode, clip, clipAxis, clipOffset, project, maxDia, highlightId, hostFilter, isolatedHost, supportDetailMode, renderNonce]);

  return (
    <FullscreenShell label="钢筋三维模型"><section className="summaryPanel rebarIfcPanel">
      <div className="viewerHeader">
        <div>
          <h3>钢筋级 IFC 可视化</h3>
          <p className="small">地下连续墙按实际施工槽段显示双面竖筋、水平分布筋和拉结筋组成的钢筋笼网格；可选钢筋仍采用分区采样实体，以兼顾构造识别与浏览性能。</p>
        </div>
        <div className="viewerStats">
          <span>钢筋笼 {data?.summary.cageCount ?? 0}</span>
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
        <div className="supportStirrupFocusToolbar">
          <div className="supportStirrupFocusIntro"><span>水平支撑专项检查</span><strong>选择一根支撑，核对 A 端、跨中和 B 端三段箍筋</strong><small>系统将只加载该构件，避免地下连续墙钢筋遮挡；“仅看箍筋”用于检查分区、间距和闭合几何。</small></div>
          <label>检查对象<select value={selectedSupportKey} onChange={(event) => setFocusSupportId(event.target.value)}>{supportCatalog.map((item) => <option key={item.hostId || item.hostCode} value={item.hostId || item.hostCode}>{item.hostCode} · {item.stirrupZoneStatus === 'complete' ? '三段已形成' : '分区待补齐'}</option>)}</select></label>
          <div className="supportStirrupFocusActions"><button className="secondary" onClick={() => openSupportDetail(false)} disabled={!selectedSupportKey || loading}>查看该支撑全部钢筋</button><button onClick={() => openSupportDetail(true)} disabled={!selectedSupportKey || loading}>仅看该支撑箍筋</button>{supportDetailMode && <button className="secondary" onClick={exitSupportDetail}>返回全局模型</button>}</div>
        </div>
        <div className={`supportRebarContractStatus ${(data.summary.missingWallHostCodes?.length ?? 0) > 0 ? 'warning' : 'success'}`}>
          <strong>地下连续墙钢筋覆盖</strong>
          <span>应显示 {data.summary.expectedWallHostCount ?? 0} 面，已显示 {data.summary.representedWallHostCount ?? 0} 面</span>
          <span>自动修复墙轴线 {data.summary.repairedWallAxisCount ?? 0} 面</span>
          <span>重建槽段几何 {data.summary.repairedPanelGeometryCount ?? 0} 段</span>
          {(data.summary.missingWallHostCodes?.length ?? 0) > 0
            ? <em>仍缺少：{data.summary.missingWallHostCodes?.join('、')}。请检查墙段轴线、槽段映射和当前配筋方案宿主签名。</em>
            : (data.summary.wallPlanGeometryStatus === 'auto_repaired'
              ? <em>检测到历史槽段端点偏离当前围护墙，已按当前墙轴线与里程自动重建；最大历史偏差 {(data.summary.maximumPanelGeometryDeviationM ?? 0).toFixed(3)} m。</em>
              : <em>钢筋笼平面路径与当前围护墙轴线一致，槽段端点按当前里程实时生成。</em>)}
        </div>
        {supportDetailMode && activeContract && <div className={`supportStirrupDesignCard ${visibleContractIssue ? 'warning' : 'success'}`}>
          <header><div><span>当前支撑</span><strong>{activeContract.hostCode}</strong></div><em>{activeContract.stirrupPreviewStatus === 'complete' ? '三段箍筋均已生成并显示' : `尚缺 ${missingZoneLabels.join('、') || '分区钢筋记录'}`}</em></header>
          <div className="supportStirrupMetricGrid">
            <article><span>A、B 端加密区</span><strong>{transverseDesign?.endZone?.token ?? (transverseDesign?.endZone ? `D${transverseDesign.endZone.diameterMm}@${transverseDesign.endZone.spacingMm}` : '待形成')}</strong><small>每端长度 {transverseDesign?.endZone?.lengthM != null ? `${Number(transverseDesign.endZone.lengthM).toFixed(2)} m` : '待确认'}</small></article>
            <article><span>跨中普通区</span><strong>{transverseDesign?.middleZone?.token ?? (transverseDesign?.middleZone ? `D${transverseDesign.middleZone.diameterMm}@${transverseDesign.middleZone.spacingMm}` : '待形成')}</strong><small>区段长度 {transverseDesign?.middleZone?.lengthM != null ? `${Number(transverseDesign.middleZone.lengthM).toFixed(2)} m` : '待确认'}</small></article>
            <article><span>斜截面受剪验算</span><strong>{transverseDesign?.utilization != null ? `${(Number(transverseDesign.utilization) * 100).toFixed(1)}%` : '待计算'}</strong><small>设计剪力 {transverseDesign?.designShearKn != null ? `${Number(transverseDesign.designShearKn).toFixed(1)} kN` : '待载入'} / 承载力 {transverseDesign?.totalCapacityKn != null ? `${Number(transverseDesign.totalCapacityKn).toFixed(1)} kN` : '待计算'}</small></article>
            <article><span>计算依据</span><strong>{evidenceLabel}</strong><small>{transverseDesign?.effectiveLegCount ?? '-'} 肢参与受剪，闭合几何共 {transverseDesign?.geometricLegCount ?? '-'} 肢</small></article>
          </div>
          <div className="supportStirrupZoneStrip"><span className={(activeContract.sampledStirrupZones ?? []).includes('end_left') ? 'done' : 'missing'}>A端加密区</span><span className={(activeContract.sampledStirrupZones ?? []).includes('middle') ? 'done' : 'missing'}>跨中普通区</span><span className={(activeContract.sampledStirrupZones ?? []).includes('end_right') ? 'done' : 'missing'}>B端加密区</span></div>
          <footer><span>{transverseDesign?.engineeringNote ?? '尚未形成可追溯的支撑箍筋设计说明。'}</span><small>{transverseDesign?.formula ?? '需先应用正式配筋方案，系统才会生成分区箍筋。'}</small></footer>
        </div>}
        <div className="viewerControls rebarControls advancedRebarControls">
          <label>宿主 <select value={hostFilter} onChange={(event) => setHostFilter(event.target.value as HostFilter)}><option value="all">全部</option><option value="diaphragm_wall">地连墙</option><option value="wale_or_crown_beam">冠梁/围檩</option><option value="internal_support">水平支撑</option><option value="support_wale_node">节点</option></select></label>
          <label>钢筋 <select value={barFilter} onChange={(event) => setBarFilter(event.target.value as BarFilter)}><option value="all">全部</option><option value="longitudinal">纵筋</option><option value="distribution">分布筋</option><option value="stirrup">箍筋</option><option value="tie">拉结筋</option><option value="additional">附加筋</option></select></label>
          <label>着色 <select value={colorMode} onChange={(event) => setColorMode(event.target.value as ColorMode)}><option value="barType">钢筋类型</option><option value="status">校核状态</option><option value="host">宿主构件</option></select></label>
          <label><input type="checkbox" checked={showHosts} onChange={(event) => setShowHosts(event.target.checked)} /> 宿主构件</label>
          <label><input type="checkbox" checked={showCageGrid} onChange={(event) => setShowCageGrid(event.target.checked)} /> 钢筋笼网格</label>
          <label><input type="checkbox" checked={showLabels} onChange={(event) => setShowLabels(event.target.checked)} /> 构造标注</label>
          <label>宿主透明度 <input type="range" min="0.04" max="0.72" step="0.02" value={hostOpacity} onChange={(event) => setHostOpacity(Number(event.target.value))} /><span>{Math.round(hostOpacity * 100)}%</span></label>
          <label>钢筋显示倍率 <input type="range" min="0.6" max="2.4" step="0.1" value={barScale} onChange={(event) => setBarScale(Number(event.target.value))} /><span>{barScale.toFixed(1)}×</span></label>
          <label><input type="checkbox" checked={clip} onChange={(event) => setClip(event.target.checked)} /> 剖切</label>
          <label>剖切方向 <select value={clipAxis} onChange={(event) => { setClipAxis(event.target.value as ClipAxis); setClipOffset(0); }}><option value="x">平面 X</option><option value="y">平面 Y</option><option value="z">高程 Z</option></select></label>
          <label>剖切位置 <input type="range" min={-clipRange} max={clipRange} step={Math.max(0.1, clipRange / 100)} value={clipOffset} onChange={(event) => setClipOffset(Number(event.target.value))} /><span>{clipOffset.toFixed(1)} m</span></label>
          {isolatedHost && !supportDetailMode && <button className="secondary" onClick={() => setIsolatedHost(undefined)}>退出构件隔离</button>}
          <button className="secondary" onClick={refreshCurrentView} disabled={loading}>刷新当前视图</button>
        </div>
        <div className={`supportRebarContractStatus ${visibleContractIssue ? 'warning' : 'success'}`}><strong>水平支撑钢筋完整性</strong><span>当前视图：{supportTypesPresent.map((item) => BAR_TYPE_LABELS[item] ?? item).join('、') || '无支撑钢筋'}</span><span>分区检查：完整 {data.summary.supportStirrupZoneCompleteCount ?? 0} 根，待补齐 {data.summary.supportStirrupZoneIncompleteCount ?? 0} 根</span>{visibleContractIssue ? <em>{supportTypesMissing.length ? `缺少钢筋族：${supportTypesMissing.map((item) => BAR_TYPE_LABELS[item] ?? item).join('、')}。` : ''}{missingZoneLabels.length ? `当前支撑未显示：${missingZoneLabels.join('、')}。` : ''}请先重新生成并应用配筋方案，再用上方“仅看该支撑箍筋”逐根核对。</em> : <em>纵筋、分布筋、箍筋、拉结筋和附加筋均已形成；箍筋三段预览完整。</em>}</div>
        {highlightId && <div className="locatorHint">当前定位对象：{highlightId}；匹配宿主构件或钢筋组时会以金色加粗显示。</div>}
        <div className="rebarLegend"><span className="rbMain">纵筋</span><span className="rbDist">分布筋</span><span className="rbStirrup">箍筋（端部加密/跨中）</span><span>拉结/架立筋</span><span className="rbAdd">搭接加强筋</span><span className="rbJoint">槽段接头</span><span className="rbLift">吊点</span><span className="rbFail">校核不满足</span></div>
        {renderError && <div className="modelRenderRecovery"><span>{renderError}</span><button type="button" className="secondary" onClick={() => setRenderNonce((value) => value + 1)}>重建配筋三维视图</button></div>}
        <div className="rebarViewport" ref={mountRef} />
        <div className="stepGrid rebarBottomGrid">
          <div className="propertyPanel"><strong>钢筋属性</strong>{selected ? <><table className="table compactTable"><tbody>{humanInfo(selected).map(([key, value]) => <tr key={key}><td>{key}</td><td>{String(value ?? '-')}</td></tr>)}</tbody></table><div className="selectedRebarActions"><span className="small">关联图纸：{drawingRefsFor(selected).join('、') || '—'}</span><button className="secondary" onClick={() => setIsolatedHost(String(selected.hostId ?? selected.hostCode ?? ''))} disabled={!selected.hostId && !selected.hostCode}>隔离该宿主</button></div></> : <span className="small">点击任意钢筋查看 IFC 类、宿主构件、钢筋组、间距、校核状态和关联图纸。</span>}</div>
          <div className="summaryPanel miniPanel"><h4>钢筋组摘要</h4><table className="table compactTable"><thead><tr><th>类型</th><th>数量</th></tr></thead><tbody>{Object.entries(data.summary.byBarType).map(([key, value]) => <tr key={key}><td>{BAR_TYPE_LABELS[key] ?? key}</td><td>{value}</td></tr>)}</tbody></table><p className="small">支撑配筋完整 {data.summary.supportContractCompleteCount ?? 0} 根，待补齐 {data.summary.supportContractIncompleteCount ?? 0} 根；三维已显示箍筋 {data.summary.supportStirrupPreviewCount ?? 0} 道。</p></div>
          <div className="summaryPanel miniPanel"><h4>当前限制</h4><p className="small">{data.summary.officialDetailingLimit}</p><ul className="small">{data.notes.map((note) => <li key={note}>{note}</li>)}</ul></div>
        </div>
      </>}
    </section></FullscreenShell>
  );
}
