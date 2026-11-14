import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import {
  api,
  type AgentMessage,
  type AgentPlan,
  type AgentProjectStatus,
  type SuperAgentTriggerResult,
} from '../../api/client';
import './assistant-panel.css';

type Props = {
  projectId: string;
  projectName?: string;
  onProjectChanged?: () => void;
};
type Notice = { id: number; kind: 'alert' | 'error'; title: string; text: string };
type Gap = { text: string; items: string[]; tone: 'ok' | 'warn' | 'error' };

const POLL_INTERVAL_MS = 15000;
const NOTICE_LIFETIME_MS = 12000;
const PET_POS_KEY = 'pitguard-agent-pet-pos-v2';
const OPEN_KEY = 'pitguard-agent-open';
const FIELD_KEYS: Record<string, string> = {
  surchargeGeneral: 'surcharge',
  safetyLevel: 'excavationSafetyLevel',
  importanceFactor: 'importanceFactor',
  concreteGrade: 'defaultConcreteGrade',
};

function fallbackGap(type: string, title: string): string[] {
  switch (type) {
    case 'human_confirm': return ['设计基准确认'];
    case 'needs_data': return ['工程输入资料'];
    case 'needs_frozen_result': return ['冻结的单剖面结果'];
    case 'role_not_ready': return [`「${title}」的执行链`];
    case 'upstream_incomplete': return ['上一步尚未完成'];
    default: return [];
  }
}

function describeGap(plan: AgentPlan): Gap {
  const next = plan.next;
  const gate = next.gate;
  if (gate.type === 'done') return { text: '六步都完成了', items: [], tone: 'ok' };
  if (gate.type === 'already_in_progress') {
    if (/awaiting_review/.test(gate.reason)) {
      return { text: '还缺：人工复核（任务已算完，等你采用或拒绝）', items: ['人工复核'], tone: 'warn' };
    }
    return { text: '当前有任务在运行，暂无缺项', items: [], tone: 'ok' };
  }
  const items = next.missing?.length ? next.missing : fallbackGap(gate.type, next.title);
  if (!items.length) {
    return { text: gate.reason || '暂无缺项', items: [], tone: 'ok' };
  }
  return { text: `还缺：${items.join('、')}`, items, tone: 'warn' };
}

/**
 * Minimal desktop-pet assistant: launcher, one conversation, one "what is
 * missing" line. No role roster, no task launcher, no conversation switcher.
 *
 * Assistant turns remain status receipts only: no model call, no engineering
 * judgement, no authority to change a design.
 */
export default function AssistantPanel({ projectId, projectName, onProjectChanged }: Props) {
  // 工作区恢复数据时会重渲染外层，展开状态放 sessionStorage 才不会被重置。
  const [open, setOpen] = useState(() => {
    try { return sessionStorage.getItem(OPEN_KEY) === '1'; } catch { return false; }
  });
  const [activeId, setActiveId] = useState('');
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState<AgentProjectStatus | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [gap, setGap] = useState<Gap>({ text: '正在读取缺项…', items: [], tone: 'ok' });
  const [attachBusy, setAttachBusy] = useState(false);
  const [petOffset, setPetOffset] = useState<{ x: number; y: number }>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(PET_POS_KEY) || 'null');
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        return { x: saved.x, y: saved.y };
      }
    } catch (error) { void error; }
    return { x: 0, y: 0 };
  });
  const [dragging, setDragging] = useState(false);
  const [viewport, setViewport] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));
  const threadRef = useRef<HTMLDivElement>(null);
  const attachRef = useRef<HTMLInputElement>(null);
  const loadedRef = useRef(false);
  const noticeSeq = useRef(0);
  const lastSignature = useRef('');
  const dragRef = useRef<{
    startX: number; startY: number; baseX: number; baseY: number;
    moved: boolean; lastX?: number; lastY?: number;
  } | null>(null);
  const offsetRef = useRef(petOffset);
  const suppressClick = useRef(false);

  useEffect(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    setPetOffset((prev) => {
      const next = {
        x: Math.min(12, Math.max(78 - vw, prev.x)),
        y: Math.min(12, Math.max(78 - vh, prev.y)),
      };
      offsetRef.current = next;
      return next.x === prev.x && next.y === prev.y ? prev : next;
    });
  }, []);

  useEffect(() => {
    try { sessionStorage.setItem(OPEN_KEY, open ? '1' : '0'); } catch (error) { void error; }
  }, [open]);

  useEffect(() => {
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onPetPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    dragRef.current = {
      startX: event.clientX, startY: event.clientY,
      baseX: offsetRef.current.x, baseY: offsetRef.current.y, moved: false,
    };
    setDragging(true);
  };

  // Listen on the window, not the launcher: the pointer leaves the 54px circle
  // almost immediately, and element-level listeners would stop receiving moves.
  useEffect(() => {
    if (!dragging) return;
    const clampOffset = (x: number, y: number) => ({
      x: Math.min(12, Math.max(78 - window.innerWidth, x)),
      y: Math.min(12, Math.max(78 - window.innerHeight, y)),
    });
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const px = event.clientX;
      const py = event.clientY;
      if ((px === 0 && py === 0) || px < 0 || py < 0
          || px > window.innerWidth || py > window.innerHeight) return;
      const dx = px - drag.startX;
      const dy = py - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true;
      drag.lastX = px;
      drag.lastY = py;
      const next = clampOffset(drag.baseX + dx, drag.baseY + dy);
      offsetRef.current = next;
      setPetOffset(next);
    };
    const onUp = () => {
      const drag = dragRef.current;
      dragRef.current = null;
      setDragging(false);
      if (!drag) return;
      if (!drag.moved && drag.lastX !== undefined && drag.lastY !== undefined) {
        const dx = drag.lastX - drag.startX;
        const dy = drag.lastY - drag.startY;
        if (Math.hypot(dx, dy) >= 4) {
          drag.moved = true;
          const next = clampOffset(drag.baseX + dx, drag.baseY + dy);
          offsetRef.current = next;
          setPetOffset(next);
        }
      }
      if (!drag.moved) return;
      suppressClick.current = true;
      window.setTimeout(() => { suppressClick.current = false; }, 350);
      try {
        localStorage.setItem(PET_POS_KEY, JSON.stringify(offsetRef.current));
      } catch (error) { void error; }
    };
    const onBlur = () => onUp();
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      window.removeEventListener('blur', onBlur);
    };
  }, [dragging]);

  const resetPetPosition = () => {
    const origin = { x: 0, y: 0 };
    offsetRef.current = origin;
    setPetOffset(origin);
    setOpen(false);
    try { localStorage.removeItem(PET_POS_KEY); } catch (error) { void error; }
  };

  const pushNotice = useCallback((kind: Notice['kind'], title: string, text: string) => {
    setNotices((prev) => {
      if (prev.some((n) => n.kind === kind && n.text === text)) return prev;
      const id = ++noticeSeq.current;
      if (kind === 'alert') {
        window.setTimeout(
          () => setNotices((current) => current.filter((n) => n.id !== id)),
          NOTICE_LIFETIME_MS,
        );
      }
      return [...prev, { id, kind, title, text }].slice(-2);
    });
  }, []);

  const dismissNotice = useCallback((id: number) => {
    setNotices((prev) => prev.filter((n) => n.id !== id));
  }, []);

  // Unattended state watch: runs whether or not the panel is open.
  useEffect(() => {
    let dead = false;
    const load = async () => {
      try {
        const { digest } = await api.agentProjectStatus(projectId);
        if (dead) return;
        setStatus(digest);
        const fail = digest.failCount ?? 0;
        const blockers = digest.blockers ?? [];
        const signature = [
          digest.statusUnavailable ? 'unavailable' : 'ok',
          fail, digest.warningCount ?? 0, blockers.join('|'),
        ].join('::');
        if (signature === lastSignature.current) return;
        lastSignature.current = signature;
        if (digest.statusUnavailable) {
          pushNotice('error', '工程状态读取失败', '未能读取当前工程状态，请检查后端服务。');
        } else if (fail > 0 || blockers.length) {
          const headline = fail > 0 ? `验算未通过 ${fail} 项` : `${blockers.length} 项待处理`;
          const detail = blockers.length ? blockers.slice(0, 2).join('；') : '打开助手查看当前缺项。';
          pushNotice('alert', headline, detail);
        }
      } catch (e) {
        if (!dead) pushNotice('error', '助手连接失败', String(e));
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => { dead = true; window.clearInterval(timer); };
  }, [projectId, pushNotice]);

  const refreshGap = useCallback(async () => {
    try {
      const basis = await api.designBasisMissing(projectId);
      if (basis.count > 0) {
        setGap({ text: basis.text, items: basis.items.map((item) => item.label), tone: 'warn' });
        return;
      }
      const plan = await api.agentPlan(projectId);
      setGap(describeGap(plan));
    } catch (e) {
      setGap({ text: '缺项读取失败', items: [], tone: 'error' });
      void e;
    }
  }, [projectId]);

  // Conversation data is loaded on first open only.
  useEffect(() => {
    if (!open || loadedRef.current) return;
    loadedRef.current = true;
    let dead = false;
    (async () => {
      try {
        const list = await api.agentConversations(projectId);
        if (dead) return;
        const items = list.conversations ?? [];
        if (items.length) {
          setActiveId(items[0].id);
          return;
        }
        const created = await api.createAgentConversation(projectId, '设计助手会话');
        if (dead) return;
        setActiveId(created.id);
      } catch (e) {
        const text = String(e);
        if (!dead) setError(text);
        pushNotice('error', '助手加载失败', text);
      }
    })();
    return () => { dead = true; };
  }, [open, projectId, pushNotice]);

  useEffect(() => {
    if (!open) return;
    void refreshGap();
  }, [open, refreshGap]);

  useEffect(() => {
    if (!activeId) return;
    let dead = false;
    api.agentConversation(activeId)
      .then((data) => { if (!dead) setMessages(data.messages ?? []); })
      .catch((e) => { if (!dead) setError(String(e)); });
    return () => { dead = true; };
  }, [activeId]);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const reloadMessages = useCallback(async () => {
    if (!activeId) return;
    const data = await api.agentConversation(activeId);
    setMessages(data.messages ?? []);
  }, [activeId]);

  const reportRouter = useCallback((result: SuperAgentTriggerResult) => {
    const execution = result.execution;
    if (execution && !execution.dispatched) {
      pushNotice('error', '总控未派单', execution.reason || result.reason);
      return;
    }
    const outcome = execution?.outcome;
    if (!outcome) {
      pushNotice('alert', '总控已记录', result.reason);
      return;
    }
    if (outcome.mode === 'chain') {
      // 连续执行：停在哪个闸口、下一步要人做什么，一次说清。
      const stage = outcome.currentStage?.title ?? '已完成';
      const actionNote = outcome.nextAction?.note ? ` ${outcome.nextAction.note}` : '';
      pushNotice(outcome.flowStatus === 'blocked' ? 'error' : 'alert',
        `连续执行 · ${stage}`, outcome.summary + actionNote);
      onProjectChanged?.();
      return;
    }
    if (outcome.mode === 'run_created') {
      // A5 诊断 / A6 优化：任务在后台跑，进展会作为消息回到对话框。
      pushNotice('alert', `${execution?.roleId ?? 'Agent'} 已受理`,
        `任务 ${String(outcome.run?.id ?? '').slice(0, 8)} 在后台执行（${outcome.run?.step ?? ''}），`
        + '诊断与候选复算结果会陆续回到这里；采用候选仍需人工复核。');
      onProjectChanged?.();
      return;
    } else if (outcome.mode === 'assessment') {
      // A2/A7/A8 的盘点与生成回执：现状、缺项建议，必要时附任务号。
      pushNotice('alert', outcome.label ?? '资料盘点',
        outcome.summary
        + (outcome.suggestedNext?.length ? ' 建议：' + outcome.suggestedNext.join('；') : ''));
      return;
    } else if (outcome.mode === 'filled') {
      pushNotice('alert', `已填入 ${outcome.applied.length} 项设计基准`,
        (outcome.remaining?.text ?? '')
        + (outcome.conflicts?.length
            ? `；其中 ${outcome.conflicts.length} 项覆盖了此前的自动录入值（已标记参数冲突，请核对）。`
            : '')
        + (outcome.requiresRecalculation ? '；原计算结果已失效，请重新验算。' : '')
        + (outcome.modelStatus === 'not_configured' ? '（模型未接入，按确定性规则抽取）' : ''));
      onProjectChanged?.();
    } else {
      pushNotice('alert', '未识别到可填字段', outcome.remaining?.text ?? '');
    }
    if (outcome.warnings?.length) {
      pushNotice('alert', '录入提示', outcome.warnings.join(' '));
    }
  }, [pushNotice, onProjectChanged]);

  async function send() {
    const text = draft.trim();
    if (!text || !activeId || busy) return;
    setBusy(true); setError('');
    setDraft('');
    try {
      // 不写死角色：后端按内容分流到 A1（参数录入）/ A5（诊断）/ A6（优化）。
      const result = await api.superTrigger({
        projectId, kind: 'user_message',
        payload: { text },
        conversationId: activeId, acceptMode: 'auto',
      });
      await reloadMessages();
      await refreshGap();
      reportRouter(result);
    } catch (e) {
      const message = String(e);
      setError(message);
      setDraft(text);
      pushNotice('error', '录入失败', message);
    } finally { setBusy(false); }
  }

  const ingestFile = useCallback(async (file: File) => {
    setAttachBusy(true); setError('');
    try {
      let fields: Array<Record<string, unknown>> = [];
      if (file.name.toLowerCase().endsWith('.json')) {
        const parsed = JSON.parse(await file.text()) as {
          scalarParameters?: Array<Record<string, unknown>>;
          fields?: Array<Record<string, unknown>>;
        };
        fields = parsed.scalarParameters ?? parsed.fields ?? [];
      } else {
        const extracted = await api.extractAgentDocument(file);
        fields = extracted.fields.map((item) => ({
          key: FIELD_KEYS[item.key] ?? item.targetField.split('.').pop(),
          value: item.value, raw: item.raw, page: item.page,
        }));
      }
      const mapped = fields.map((item) => ({
        ...item,
        key: FIELD_KEYS[String(item.key)] ?? String(item.key),
      }));
      const result = await api.superTrigger({
        projectId, kind: 'document_uploaded',
        payload: { roleId: 'A1', fields: mapped, documentName: file.name },
        conversationId: activeId || undefined, acceptMode: 'auto',
      });
      await reloadMessages();
      await refreshGap();
      reportRouter(result);
    } catch (e) {
      const text = String(e);
      setError(text);
      pushNotice('error', '文件录入失败', text);
    } finally { setAttachBusy(false); }
  }, [projectId, activeId, reloadMessages, refreshGap, reportRouter, pushNotice]);

  const failCount = status?.failCount ?? 0;
  const badge = status?.statusUnavailable || notices.some((n) => n.kind === 'error')
    ? '!'
    : failCount > 0 ? (failCount > 99 ? '99+' : String(failCount)) : '';

  const renderNotices = () => notices.map((notice) => (
    <div key={notice.id} className={`agentNotice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
      <div>
        <strong>{notice.title}</strong>
        <p>{notice.text}</p>
        <button type="button" className="agentNoticeOpen" onClick={() => { dismissNotice(notice.id); setOpen(true); }}>
          打开助手
        </button>
      </div>
      <button type="button" className="agentNoticeClose" aria-label="关闭提醒" onClick={() => dismissNotice(notice.id)}>×</button>
    </div>
  ));

  // Open the dock toward whichever side has room, so dragging the pet to a
  // corner never pushes the conversation off screen.
  const ballRight = viewport.w - 20 + petOffset.x;
  const ballBottom = viewport.h - 20 + petOffset.y;
  const ballLeft = ballRight - 54;
  const ballTop = ballBottom - 54;
  const roomAbove = ballTop - 18;
  const roomBelow = viewport.h - ballBottom - 18;
  const vertical = roomAbove >= roomBelow ? 'above' : 'below';
  const roomRight = viewport.w - ballRight - 8;
  const roomLeft = ballLeft - 8;
  const panelWidth = Math.min(356, viewport.w - 40);
  const horizontal = roomRight >= panelWidth || roomRight >= roomLeft ? 'fromLeft' : 'fromRight';
  const dockHeight = Math.max(240, Math.min(500, vertical === 'above' ? roomAbove : roomBelow));
  const placement = `place-${vertical}-${horizontal}`;

  return (
    <div
      className={`agentPet ${placement} ${dragging ? 'isDragging' : ''}`}
      style={{ transform: `translate(${petOffset.x}px, ${petOffset.y}px)` }}
    >
      {open ? (
        <aside className="agentDock" style={{ height: dockHeight }} aria-label="智能设计助手">
          <header className="agentDockHeader">
            <div>
              <strong>智能设计助手</strong>
              <span>{projectName || '当前项目'}</span>
            </div>
            <button type="button" className="secondary" onClick={() => setOpen(false)}>收起</button>
          </header>

          <div className={`agentDockGap ${gap.tone}`} role="status" title={gap.text}>
            {gap.text}
          </div>

          <div className="agentDockMessages" ref={threadRef}>
            {messages.length === 0 ? (
              <p className="agentDockEmpty">
                贴一段参数（如「施工荷载 30kPa，基坑安全等级三级」）或上传 PDF，会自动填入设计基准并反馈还缺什么。
              </p>
            ) : messages.map((message) => (
              message.role === 'tool' ? (
                <article key={message.id} className="agentMessage tool">
                  <small>任务 · {new Date(message.createdAt).toLocaleTimeString()}</small>
                  <p>{message.content}</p>
                </article>
              ) : (
                <article key={message.id} className={`agentMessage ${message.role}`}>
                  <small>
                    {message.role === 'user' ? '我' : '助手'}
                    {' · '}{new Date(message.createdAt).toLocaleTimeString()}
                  </small>
                  <p>{message.content}</p>
                </article>
              )
            ))}
          </div>

          <footer className="agentDockCompose agentDockComposeSlim">
            <button
              type="button"
              className="agentDockAttach"
              onClick={() => attachRef.current?.click()}
              disabled={attachBusy}
              title="上传 PDF 或提取结果 JSON，自动填入设计基准"
              aria-label="上传文件自动填入设计基准"
            >
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M21.44 11.05l-9.19 9.19a5 5 0 01-7.07-7.07l9.19-9.19a3 3 0 014.24 4.24l-9.19 9.19a1 1 0 01-1.41-1.41l8.49-8.49" />
              </svg>
            </button>
            <input
              ref={attachRef}
              type="file"
              accept=".pdf,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void ingestFile(file);
                event.target.value = '';
              }}
            />
            <input
              className="agentDockInput"
              type="text"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') { event.preventDefault(); void send(); }
              }}
              placeholder={attachBusy ? '正在读取文件…' : '描述问题 / 贴参数，或上传 PDF'}
              aria-label="向智能设计助手提问"
            />
            <button type="button" onClick={() => void send()} disabled={busy || attachBusy || !draft.trim()}>
              {busy ? '发送中' : '发送'}
            </button>
          </footer>

          {error ? <p className="agentDockError" role="alert">{error}</p> : null}
        </aside>
      ) : (
        notices.length ? <div className="agentDockToasts">{renderNotices()}</div> : null
      )}

      <div className="agentPetLauncherWrap">
        <button
          type="button"
          className={`agentPetLauncher ${open ? 'isOpen' : ''} ${badge ? 'hasBadge' : ''}`}
          onPointerDown={onPetPointerDown}
          onDragStart={(event) => event.preventDefault()}
          onDoubleClick={resetPetPosition}
          onClick={() => {
            if (suppressClick.current) { suppressClick.current = false; return; }
            setOpen((prev) => !prev);
          }}
          aria-label={open ? '收起智能设计助手' : '打开智能设计助手'}
          aria-expanded={open}
          title={open ? '收起助手 · 可拖动，双击回原位'
            : `智能设计助手${badge ? ` · ${badge} 项需关注` : ''} · 拖动移动，双击回原位`}
        >
          {open
            ? <span className="agentPetGlyph">×</span>
            : <img className="agentPetLogo" src="/assistant-logo.jpg" alt="" draggable={false} />}
        </button>
        {badge ? <span className="agentPetBadge">{badge}</span> : null}
      </div>
    </div>
  );
}
