import { useEffect, useRef, useState, type ReactNode } from 'react';

export default function FullscreenShell({ children, className = '', label = '模型' }: { children: ReactNode; className?: string; label?: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState(false);
  useEffect(() => {
    const onChange = () => setActive(document.fullscreenElement === ref.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);
  async function toggle() {
    if (!ref.current) return;
    if (document.fullscreenElement === ref.current) await document.exitFullscreen();
    else await ref.current.requestFullscreen();
  }
  return <div ref={ref} className={`modelFullscreenShell ${className} ${active ? 'isFullscreen' : ''}`}>
    <button type="button" className="modelFullscreenButton" onClick={() => void toggle()} aria-label={`${active ? '退出' : '进入'}${label}全屏`}>{active ? '退出全屏' : '全屏查看'}</button>
    {children}
  </div>;
}
