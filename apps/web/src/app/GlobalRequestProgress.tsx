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
  progress?: number;
  ok?: boolean;
  error?: string;
};

type ActiveRequest = RequestActivityDetail & { finishedAt?: number };
type ActivityUpdate = Pick<RequestActivityDetail, 'id'> & Partial<Pick<RequestActivityDetail, 'phase' | 'progress' | 'label' | 'blocking'>>;

const START_EVENT = 'pitguard:request-start';
const PHASE_EVENT = 'pitguard:request-phase';
const END_EVENT = 'pitguard:request-end';

export const requestActivityEvents = {
  start: START_EVENT,
  phase: PHASE_EVENT,
  end: END_EVENT,
};

function emit(name: string, detail: Record<string, unknown>) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

/** Register a page-level activity that is not represented by a single HTTP request. */
export function beginGlobalActivity(options: {
  id?: string;
  label: string;
  phase?: string;
  expectedMs?: number;
  blocking?: boolean;
  quiet?: boolean;
  path?: string;
  method?: string;
  progress?: number;
}) {
  const id = options.id ?? `activity-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  emit(START_EVENT, {
    id,
    label: options.label,
    phase: options.phase,
    expectedMs: Math.max(500, options.expectedMs ?? 6000),
    blocking: options.blocking ?? true,
    quiet: options.quiet ?? false,
    path: options.path ?? 'local://workflow',
    method: options.method ?? 'TASK',
    progress: options.progress,
    startedAt: Date.now(),
  });
  return id;
}

export function updateGlobalActivity(id: string, update: Omit<ActivityUpdate, 'id'>) {
  emit(PHASE_EVENT, { id, ...update });
}

export function finishGlobalActivity(id: string, result: { ok: boolean; phase?: string; error?: string; progress?: number }) {
  emit(END_EVENT, {
    id,
    progress: result.progress ?? 100,
    ok: result.ok,
    phase: result.phase,
    error: result.error,
  });
}

function estimatedProgress(item: ActiveRequest, clock: number) {
  if (item.finishedAt) return 100;
  if (Number.isFinite(Number(item.progress))) return Math.max(2, Math.min(98, Number(item.progress)));
  const elapsed = Math.max(0, clock - item.startedAt);
  return Math.min(93, Math.max(8, 8 + (1 - Math.exp(-elapsed / Math.max(650, item.expectedMs))) * 84));
}

export function FullPageLoadingFallback({ label, detail = '正在按需加载界面模块，请稍候。' }: { label: string; detail?: string }) {
  return <div className="pageLoadingFallback" role="status" aria-live="polite" aria-busy="true">
    <div className="pageLoadingBackdrop" />
    <section className="pageLoadingCard">
      <span className="globalRequestSpinner" aria-hidden="true" />
      <div><strong>{label}</strong><small>{detail}</small></div>
      <div className="globalRequestTrack indeterminate"><span /></div>
    </section>
  </div>;
}

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
      const detail = (event as CustomEvent<ActivityUpdate>).detail;
      if (!detail) return;
      setItems((current) => current[detail.id]
        ? { ...current, [detail.id]: { ...current[detail.id], ...detail } }
        : current);
    };
    const onEnd = (event: Event) => {
      const detail = (event as CustomEvent<RequestActivityDetail>).detail;
      if (!detail || detail.quiet) return;
      setItems((current) => current[detail.id]
        ? { ...current, [detail.id]: { ...current[detail.id], ...detail, progress: detail.progress ?? 100, finishedAt: Date.now() } }
        : current);
      const delay = detail.ok === false ? 5200 : 850;
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
    .filter((item) => item.finishedAt || clock - item.startedAt >= 180)
    .sort((a, b) => {
      if (Boolean(a.finishedAt) !== Boolean(b.finishedAt)) return a.finishedAt ? 1 : -1;
      return b.startedAt - a.startedAt;
    }), [items, clock]);
  if (!visible.length) return null;

  const current = visible[0];
  const currentProgress = estimatedProgress(current, clock);
  const activeItems = visible.filter((item) => !item.finishedAt);
  const blocking = activeItems.some((item) => item.blocking);
  const failed = Boolean(current.finishedAt && current.ok === false);
  const phase = failed
    ? (current.error || current.phase || '操作失败，可重试')
    : current.finishedAt
      ? (current.phase || '已完成')
      : (current.phase || (clock - current.startedAt > 1400 ? '服务器处理中，请勿重复点击' : '正在提交请求'));
  const aggregate = activeItems.length
    ? activeItems.reduce((sum, item) => sum + estimatedProgress(item, clock), 0) / activeItems.length
    : currentProgress;

  function dismissFinished() {
    setItems((existing) => {
      const next = { ...existing };
      Object.values(existing).forEach((item) => { if (item.finishedAt) delete next[item.id]; });
      return next;
    });
  }

  return <div className={`globalRequestOverlay ${blocking ? 'blocking' : 'passive'} ${failed ? 'failed' : current.finishedAt ? 'done' : ''}`} role="presentation">
    <div className="globalRequestBackdrop" aria-hidden="true" />
    <section className="globalRequestProgress" role="status" aria-live="polite" aria-busy={activeItems.length > 0}>
      <div className="globalRequestHeader">
        <span className="globalRequestSpinner" aria-hidden="true" />
        <div>
          <strong>{current.label || '后台操作'}</strong>
          <small>{phase}{activeItems.length > 1 ? ` · 共 ${activeItems.length} 项处理中` : ''}</small>
        </div>
        <b>{Math.round(currentProgress)}%</b>
      </div>
      <div className="globalRequestTrack"><span style={{ width: `${Math.max(2, Math.min(100, aggregate))}%` }} /></div>
      {visible.length > 1 ? <div className="globalRequestQueue" aria-label="并行加载任务">
        {visible.slice(0, 4).map((item) => <div key={item.id} className={item.finishedAt ? (item.ok === false ? 'failed' : 'done') : 'running'}>
          <span>{item.label || '后台操作'}</span>
          <progress max={100} value={estimatedProgress(item, clock)} />
          <b>{Math.round(estimatedProgress(item, clock))}%</b>
        </div>)}
      </div> : null}
      <div className="globalRequestFooter">
        <span>{blocking ? '当前操作会修改工程数据，完成前已暂停重复交互。' : '当前为读取操作，后台完成后页面将自动恢复。'}</span>
        {failed ? <button type="button" className="secondary" onClick={dismissFinished}>关闭</button> : null}
      </div>
    </section>
  </div>;
}
