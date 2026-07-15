import { useEffect, useMemo, useRef, useState } from 'react';

export type RequestActivityDetail = {
  id: string;
  label: string;
  method: string;
  path: string;
  startedAt: number;
  expectedMs: number;
  blocking: boolean;
  quiet?: boolean;
  phase?: string;
  ok?: boolean;
  error?: string;
};

type ActiveRequest = RequestActivityDetail & { finishedAt?: number };

const START_EVENT = 'pitguard:request-start';
const PHASE_EVENT = 'pitguard:request-phase';
const END_EVENT = 'pitguard:request-end';

export const requestActivityEvents = {
  start: START_EVENT,
  phase: PHASE_EVENT,
  end: END_EVENT,
};

export function GlobalRequestProgress() {
  const [items, setItems] = useState<Record<string, ActiveRequest>>({});
  const [clock, setClock] = useState(Date.now());
  const timers = useRef<number[]>([]);

  useEffect(() => {
    const onStart = (event: Event) => {
      const detail = (event as CustomEvent<RequestActivityDetail>).detail;
      if (!detail || detail.quiet) return;
      setItems((current) => ({ ...current, [detail.id]: detail }));
    };
    const onPhase = (event: Event) => {
      const detail = (event as CustomEvent<Pick<RequestActivityDetail, 'id' | 'phase'>>).detail;
      if (!detail) return;
      setItems((current) => current[detail.id]
        ? { ...current, [detail.id]: { ...current[detail.id], phase: detail.phase } }
        : current);
    };
    const onEnd = (event: Event) => {
      const detail = (event as CustomEvent<RequestActivityDetail>).detail;
      if (!detail || detail.quiet) return;
      setItems((current) => current[detail.id]
        ? { ...current, [detail.id]: { ...current[detail.id], ...detail, finishedAt: Date.now() } }
        : current);
      const delay = detail.ok === false ? 2600 : 650;
      const timer = window.setTimeout(() => {
        setItems((current) => {
          const next = { ...current };
          delete next[detail.id];
          return next;
        });
      }, delay);
      timers.current.push(timer);
    };
    window.addEventListener(START_EVENT, onStart);
    window.addEventListener(PHASE_EVENT, onPhase);
    window.addEventListener(END_EVENT, onEnd);
    return () => {
      window.removeEventListener(START_EVENT, onStart);
      window.removeEventListener(PHASE_EVENT, onPhase);
      window.removeEventListener(END_EVENT, onEnd);
      timers.current.forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  useEffect(() => {
    if (!Object.keys(items).length) return;
    const timer = window.setInterval(() => setClock(Date.now()), 160);
    return () => window.clearInterval(timer);
  }, [items]);

  const visible = useMemo(() => Object.values(items)
    .filter((item) => item.finishedAt || clock - item.startedAt >= 260)
    .sort((a, b) => b.startedAt - a.startedAt), [items, clock]);
  if (!visible.length) return null;

  const current = visible[0];
  const elapsed = Math.max(0, clock - current.startedAt);
  const progress = current.finishedAt
    ? (current.ok === false ? 100 : 100)
    : Math.min(93, Math.max(8, 8 + (1 - Math.exp(-elapsed / Math.max(650, current.expectedMs))) * 84));
  const blocking = visible.some((item) => item.blocking && !item.finishedAt);
  const failed = current.finishedAt && current.ok === false;
  const phase = failed ? (current.error || '操作失败，可重试') : current.finishedAt ? '已完成' : (current.phase || elapsed > 1400 ? '服务器处理中，请勿重复点击' : '正在提交请求');

  return <>
    {blocking ? <div className="requestInteractionShield" aria-hidden="true" /> : null}
    <section className={`globalRequestProgress ${failed ? 'failed' : current.finishedAt ? 'done' : ''}`} role="status" aria-live="polite" aria-busy={!current.finishedAt}>
      <div className="globalRequestHeader">
        <span className="globalRequestSpinner" aria-hidden="true" />
        <div><strong>{current.label}</strong><small>{phase}{visible.length > 1 ? ` · 另有 ${visible.length - 1} 项处理中` : ''}</small></div>
        <b>{Math.round(progress)}%</b>
      </div>
      <div className="globalRequestTrack"><span style={{ width: `${progress}%` }} /></div>
    </section>
  </>;
}
