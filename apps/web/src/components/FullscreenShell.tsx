import { useEffect, useRef, useState, type ReactNode } from 'react';

type FullscreenElement = HTMLDivElement & { webkitRequestFullscreen?: () => Promise<void> | void };
type FullscreenDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};

function currentFullscreenElement(): Element | null {
  const doc = document as FullscreenDocument;
  return document.fullscreenElement ?? doc.webkitFullscreenElement ?? null;
}

export default function FullscreenShell({ children, className = '', label = '模型' }: { children: ReactNode; className?: string; label?: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [nativeActive, setNativeActive] = useState(false);
  const [fallbackActive, setFallbackActive] = useState(false);
  const [fullscreenError, setFullscreenError] = useState<string>();
  const active = nativeActive || fallbackActive;

  useEffect(() => {
    const onChange = () => {
      setNativeActive(currentFullscreenElement() === ref.current);
      window.requestAnimationFrame(() => window.dispatchEvent(new Event('pitguard:model-resize')));
    };
    document.addEventListener('fullscreenchange', onChange);
    document.addEventListener('webkitfullscreenchange', onChange as EventListener);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      document.removeEventListener('webkitfullscreenchange', onChange as EventListener);
    };
  }, []);

  useEffect(() => {
    if (!fallbackActive) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.body.classList.add('pitguardModelFullscreenOpen');
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setFallbackActive(false); };
    window.addEventListener('keydown', escape);
    window.requestAnimationFrame(() => window.dispatchEvent(new Event('pitguard:model-resize')));
    return () => {
      window.removeEventListener('keydown', escape);
      document.body.style.overflow = previousOverflow;
      document.body.classList.remove('pitguardModelFullscreenOpen');
      window.requestAnimationFrame(() => window.dispatchEvent(new Event('pitguard:model-resize')));
    };
  }, [fallbackActive]);

  async function toggle() {
    const element = ref.current as FullscreenElement | null;
    if (!element) return;
    setFullscreenError(undefined);
    if (fallbackActive) { setFallbackActive(false); return; }
    if (currentFullscreenElement() === element) {
      const doc = document as FullscreenDocument;
      if (document.exitFullscreen) await document.exitFullscreen();
      else await doc.webkitExitFullscreen?.();
      return;
    }
    try {
      if (element.requestFullscreen) await element.requestFullscreen({ navigationUI: 'hide' });
      else if (element.webkitRequestFullscreen) await element.webkitRequestFullscreen();
      else setFallbackActive(true);
    } catch (reason) {
      setFallbackActive(true);
      setFullscreenError(reason instanceof Error ? reason.message : '浏览器全屏接口不可用，已切换为页面内全屏。');
    }
  }

  return <div ref={ref} className={`modelFullscreenShell ${className} ${active ? 'isFullscreen' : ''} ${fallbackActive ? 'isFallbackFullscreen' : ''}`} data-model-fullscreen={active ? 'active' : 'inactive'}>
    <div className="modelFullscreenToolbar">
      <button type="button" className="modelFullscreenButton" onClick={() => void toggle()} aria-label={`${active ? '退出' : '进入'}${label}全屏`} aria-pressed={active} title={`${active ? '退出' : '进入'}${label}全屏`}>
        <span aria-hidden="true">{active ? '↙' : '⛶'}</span><span>{active ? '退出全屏' : '全屏'}</span>
      </button>
    </div>
    {fullscreenError ? <div className="modelFullscreenNotice">浏览器原生全屏不可用，当前使用页面内全屏模式。</div> : null}
    {children}
  </div>;
}
