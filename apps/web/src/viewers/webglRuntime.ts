import {
  Material,
  Object3D,
  Scene,
  Texture,
  WebGLRenderer,
  type Camera,
  type WebGLRendererParameters,
} from 'three';

type ContextHandlers = {
  onLost?: (message: string) => void;
  onRestored?: () => void;
};

const rendererRegistry = new WeakSet<WebGLRenderer>();
let activeRendererCount = 0;

export function stablePixelRatio(cap = 1.75): number {
  const deviceMemory = Number((navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8);
  const memoryCap = deviceMemory <= 4 ? 1 : deviceMemory <= 8 ? 1.35 : cap;
  return Math.max(1, Math.min(window.devicePixelRatio || 1, cap, memoryCap));
}

export function createStableWebGLRenderer(parameters: WebGLRendererParameters = {}): WebGLRenderer {
  const renderer = new WebGLRenderer({
    antialias: true,
    alpha: false,
    depth: true,
    stencil: false,
    powerPreference: 'high-performance',
    preserveDrawingBuffer: false,
    failIfMajorPerformanceCaveat: false,
    ...parameters,
  });
  rendererRegistry.add(renderer);
  activeRendererCount += 1;
  renderer.domElement.dataset.pitguardWebglContext = String(activeRendererCount);
  return renderer;
}

export function bindWebglContextLifecycle(renderer: WebGLRenderer, handlers: ContextHandlers = {}): () => void {
  const canvas = renderer.domElement;
  const lost = (event: Event) => {
    event.preventDefault();
    handlers.onLost?.('三维渲染上下文暂时丢失。工程数据未受影响，可点击“重建三维视图”恢复。');
  };
  const restored = () => handlers.onRestored?.();
  canvas.addEventListener('webglcontextlost', lost, false);
  canvas.addEventListener('webglcontextrestored', restored, false);
  return () => {
    canvas.removeEventListener('webglcontextlost', lost, false);
    canvas.removeEventListener('webglcontextrestored', restored, false);
  };
}

export function startStableRenderLoop(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  mount: HTMLElement,
  options: { maxFps?: number } = {},
): () => void {
  const maxFps = Math.max(8, Math.min(options.maxFps ?? 30, 60));
  const minimumInterval = 1000 / maxFps;
  let frame = 0;
  let stopped = false;
  let visible = true;
  let lastPaint = 0;

  const paint = (time: number) => {
    if (stopped) return;
    frame = window.requestAnimationFrame(paint);
    if (!visible || document.hidden || time - lastPaint < minimumInterval) return;
    lastPaint = time;
    renderer.render(scene, camera);
  };

  const intersection = typeof IntersectionObserver !== 'undefined'
    ? new IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting || entry.intersectionRatio > 0);
      if (visible && !document.hidden) renderer.render(scene, camera);
    }, { rootMargin: '160px' })
    : undefined;
  intersection?.observe(mount);

  const repaint = () => {
    if (!stopped && visible && !document.hidden) renderer.render(scene, camera);
  };
  document.addEventListener('visibilitychange', repaint);
  window.addEventListener('resize', repaint);
  window.addEventListener('pitguard:model-resize', repaint as EventListener);
  renderer.render(scene, camera);
  frame = window.requestAnimationFrame(paint);

  return () => {
    stopped = true;
    window.cancelAnimationFrame(frame);
    intersection?.disconnect();
    document.removeEventListener('visibilitychange', repaint);
    window.removeEventListener('resize', repaint);
    window.removeEventListener('pitguard:model-resize', repaint as EventListener);
  };
}

function disposeMaterial(material: Material) {
  for (const value of Object.values(material as unknown as Record<string, unknown>)) {
    if (value instanceof Texture) value.dispose();
  }
  material.dispose();
}

export function disposeSceneResources(root: Object3D) {
  root.traverse((object) => {
    const candidate = object as Object3D & { geometry?: { dispose?: () => void }; material?: Material | Material[] };
    candidate.geometry?.dispose?.();
    if (Array.isArray(candidate.material)) candidate.material.forEach(disposeMaterial);
    else if (candidate.material) disposeMaterial(candidate.material);
  });
}

export function releaseStableWebGLRenderer(renderer: WebGLRenderer, scene?: Object3D, mount?: HTMLElement) {
  if (scene) disposeSceneResources(scene);
  try { renderer.setAnimationLoop(null); } catch { /* already lost */ }
  try { renderer.renderLists.dispose(); } catch { /* optional */ }
  try { renderer.dispose(); } catch { /* best effort */ }
  try { renderer.forceContextLoss(); } catch { /* browser already released it */ }
  if (renderer.domElement.parentElement) renderer.domElement.parentElement.removeChild(renderer.domElement);
  else if (mount?.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
  if (rendererRegistry.has(renderer)) {
    rendererRegistry.delete(renderer);
    activeRendererCount = Math.max(0, activeRendererCount - 1);
  }
}

export function activeWebglRendererCount() {
  return activeRendererCount;
}
