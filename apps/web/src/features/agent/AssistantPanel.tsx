import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import {
  api,
  type AgentConversation,
  type AgentMessage,
  type AgentProjectStatus,
  type AgentRole,
  type AgentRunBrief,
  type AgentFrozenResult,
} from '../../api/client';
import './assistant-panel.css';

type Props = {
  projectId: string;
  projectName?: string;
  onProjectChanged?: () => void;
};
type Notice = { id: number; kind: 'alert' | 'error'; title: string; text: string };
type IntakeField = {
  key: string; label: string; value: string | number; unit?: string;
  page?: number; target: string; checked: boolean;
};
type IntakeRule = { target: string; label: string; validate: (value: unknown) => boolean };

/**
 * Only unambiguous scalars may be written. Water levels, elevations and soil
 * parameters need a datum /折减值 decision first, so they are never auto-filled.
 */
const INTAKE_RULES: Record<string, IntakeRule> = {
  surchargeGeneral: {
    target: 'surcharge', label: '施工荷载',
    validate: (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0,
  },
  safetyLevel: {
    target: 'excavationSafetyLevel', label: '基坑安全等级',
    validate: (v) => ['一级', '二级', '三级'].includes(String(v)),
  },
  importanceFactor: {
    target: 'importanceFactor', label: '侧壁重要性系数',
    validate: (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0.8 && v <= 1.5,
  },
  concreteGrade: {
    target: 'defaultConcreteGrade', label: '混凝土等级',
    validate: (v) => typeof v === 'string' && /^C\d{2,3}$/.test(v),
  },
};

const POLL_INTERVAL_MS = 60000;
const NOTICE_LIFETIME_MS = 12000;
const PET_POS_KEY = 'pitguard-agent-pet-pos-v2';

/**
 * Desktop-pet style assistant.
 *
 * The dock is hidden until the launcher is clicked, so it takes no layout
 * space. Project state is still polled while hidden; hard failures and
 * request errors raise a toast and a badge on the launcher, so a problem is
 * never silenced just because the panel is closed.
 *
 * Assistant turns remain status receipts only: no model call, no engineering
 * judgement, no authority to change a design.
 */
export default function AssistantPanel({ projectId, projectName, onProjectChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [roles, setRoles] = useState<AgentRole[]>([]);
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [activeId, setActiveId] = useState('');
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState<AgentProjectStatus | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [runs, setRuns] = useState<AgentRunBrief[]>([]);
  const [frozenResults, setFrozenResults] = useState<AgentFrozenResult[]>([]);
  const [selectedResult, setSelectedResult] = useState('');
  const [runBusy, setRunBusy] = useState(false);
  const [intakeFields, setIntakeFields] = useState<IntakeField[]>([]);
  const [intakeFileName, setIntakeFileName] = useState('');
  const [intakeBusy, setIntakeBusy] = useState(false);
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
          const detail = blockers.length ? blockers.slice(0, 2).join('；') : '打开助手查看当前步骤与可执行角色。';
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

  // Conversation data is loaded on first open only.
  useEffect(() => {
    if (!open || loadedRef.current) return;
    loadedRef.current = true;
    let dead = false;
    (async () => {
      try {
        const [roleData, list] = await Promise.all([
          api.agentRoles(),
          api.agentConversations(projectId),
        ]);
        if (dead) return;
        setRoles(roleData.roles ?? []);
        const items = list.conversations ?? [];
        if (items.length) {
          setConversations(items);
          setActiveId(items[0].id);
          return;
        }
        const created = await api.createAgentConversation(projectId, '设计助手会话');
        if (dead) return;
        setConversations([created]);
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
    let dead = false;
    api.agentFrozenResults()
      .then((data) => {
        if (dead) return;
        const items = data.results ?? [];
        setFrozenResults(items);
        setSelectedResult((prev) => prev || items.find((item) => item.launchable)?.resultHash || '');
      })
      .catch(() => undefined);
    return () => { dead = true; };
  }, [open]);

  useEffect(() => {
    if (!activeId) return;
    let dead = false;
    api.agentConversation(activeId)
      .then((data) => {
        if (dead) return;
        setMessages(data.messages ?? []);
        setRuns(data.runs ?? []);
      })
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

  const startConversation = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const created = await api.createAgentConversation(projectId, '设计助手会话');
      setConversations((prev) => [created, ...prev]);
      setActiveId(created.id);
      setMessages([]);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }, [projectId]);

  async function send() {
    const text = draft.trim();
    if (!text || !activeId || busy) return;
    setBusy(true); setError('');
    setDraft('');
    try {
      const result = await api.postAgentMessage(activeId, text);
      setMessages((prev) => [...prev, result.message, result.reply]);
      if (result.reply.metadata?.dispatchedRunId) {
        api.agentConversationRuns(activeId)
          .then((data) => setRuns(data.runs ?? []))
          .catch(() => undefined);
      }
    } catch (e) {
      const message = String(e);
      setError(message);
      setDraft(text);
      pushNotice('error', '发送失败', message);
    } finally { setBusy(false); }
  }

  const refreshSession = useCallback(async (id: string) => {
    const [detail, runList] = await Promise.all([
      api.agentConversation(id),
      api.agentConversationRuns(id),
    ]);
    setMessages(detail.messages ?? []);
    setRuns(runList.runs ?? []);
  }, []);

  const launchRun = useCallback(async () => {
    const hash = selectedResult || frozenResults[0]?.resultHash;
    if (!hash || !activeId || runBusy) return;
    if (!frozenResults.find((item) => item.resultHash === hash)?.launchable) {
      setError('所选冻结结果与当前工作版本不一致，无法发起任务');
      return;
    }
    setRunBusy(true); setError('');
    try {
      await api.postAgentMessageWithRun(activeId, '发起一次试点任务', hash);
      await refreshSession(activeId);
    } catch (e) {
      const text = String(e);
      setError(text);
      pushNotice('error', '发起任务失败', text);
    } finally { setRunBusy(false); }
  }, [selectedResult, frozenResults, activeId, runBusy, refreshSession, pushNotice]);

  const advanceRun = useCallback(async () => {
    const run = runs[0];
    if (!run || !activeId || runBusy) return;
    setRunBusy(true); setError('');
    try {
      await api.advanceAgentRun(run.id);
      await refreshSession(activeId);
    } catch (e) {
      const text = String(e);
      setError(text);
      pushNotice('error', '推进失败', text);
    } finally { setRunBusy(false); }
  }, [runs, activeId, runBusy, refreshSession, pushNotice]);

  /** Read the assistant-produced extraction JSON. Never parses a PDF itself. */
  const loadIntakeFile = useCallback(async (file: File) => {
    const name = file.name.toLowerCase();
    if (name.endsWith('.pdf')) {
      pushNotice('alert', '需要提取结果 JSON', 'PDF 由助手读取并提取；请把助手生成的提取结果 JSON 选进来。');
      return;
    }
    if (!name.endsWith('.json')) {
      pushNotice('error', '文件类型不支持', '请选择 .json 提取结果，或把 PDF 交给助手提取。');
      return;
    }
    if (file.size > 2_000_000) {
      pushNotice('error', '提取结果过大', '文件超过 2 MB，请只保留提取结果 JSON。');
      return;
    }
    try {
      const data = JSON.parse(await file.text()) as { scalarParameters?: Array<Record<string, unknown>> };
      const rows = data.scalarParameters;
      if (!Array.isArray(rows)) throw new Error('文件里没有 scalarParameters 数组，不是有效的提取结果。');
      const fields: IntakeField[] = [];
      for (const row of rows) {
        const rule = INTAKE_RULES[String(row.key ?? '')];
        if (!rule || !rule.validate(row.value)) continue;
        fields.push({
          key: String(row.key), label: rule.label, value: row.value as string | number,
          unit: row.unit == null ? undefined : String(row.unit),
          page: Number(row.page) || undefined, target: rule.target, checked: true,
        });
      }
      if (!fields.length) {
        setIntakeFields([]); setIntakeFileName('');
        pushNotice('error', '没有可写入的字段',
          '提取结果里没有命中白名单的参数；水位、高程、土层参数需人工确认后另行填入。');
        return;
      }
      setIntakeFields(fields);
      setIntakeFileName(file.name);
      setError('');
      pushNotice('alert', `读出 ${fields.length} 项可填参数`, `来自 ${file.name}；请核对后点「填入当前项目」。`);
    } catch (e) {
      setIntakeFields([]); setIntakeFileName('');
      pushNotice('error', '提取结果读取失败', String(e));
    }
  }, [pushNotice]);

  const applyIntake = useCallback(async () => {
    const picked = intakeFields.filter((field) => field.checked);
    if (!picked.length) {
      pushNotice('error', '未选择字段', '至少勾选一项再填入。');
      return;
    }
    if (!projectId) {
      pushNotice('error', '项目未打开', '请先打开一个项目，再填入提取结果。');
      return;
    }
    setIntakeBusy(true); setError('');
    try {
      // Merge into the existing settings: sending a partial object would let
      // the backend re-default every untouched design parameter.
      const currentProject = await api.getProject(projectId);
      const existing = (currentProject.designSettings ?? {}) as unknown as Record<string, unknown>;
      const mergedSettings: Record<string, unknown> = {
        ...existing,
        ...Object.fromEntries(intakeFields.filter((f) => f.checked).map((f) => [f.target, f.value])),
      };
      const picked = intakeFields.filter((field) => field.checked);
      await api.updateProject(projectId,
        { designSettings: mergedSettings } as unknown as Parameters<typeof api.updateProject>[1]);
      pushNotice('alert', `已填入 ${picked.length} 项`,
        '已写入当前项目；设计输入变更会作废旧计算结果，请重新建立工况并验算。');
      setIntakeFields([]); setIntakeFileName('');
      onProjectChanged?.();
    } catch (e) {
      const text = String(e);
      setError(text);
      pushNotice('error', '填入失败', text);
    } finally { setIntakeBusy(false); }
  }, [intakeFields, projectId, onProjectChanged, pushNotice]);

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
  const dockHeight = Math.max(180, Math.min(660, vertical === 'above' ? roomAbove : roomBelow));
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

          {status && (failCount > 0 || status.blockers?.length) ? (
            <div className="agentDockStatusStrip" role="status">
              验算未通过 <b>{failCount}</b> 项{status.warningCount != null ? ` · 警告 ${status.warningCount} 项` : ''}
              {status.activeStage ? ` · 当前：${status.activeStage.title}` : ''}
            </div>
          ) : null}

          {runs.length ? (
            <div className="agentDockRunStrip" role="status">
              <span>关联任务 {runs.length} 个 · 最近 {runs[0].status} / {runs[0].step}</span>
              <button
                type="button"
                className="agentDockAdvance"
                onClick={() => void advanceRun()}
                disabled={runBusy || !['queued', 'running'].includes(runs[0].status)}
              >
                推进一步
              </button>
            </div>
          ) : null}

          {notices.length ? <div className="agentDockInlineNotices">{renderNotices()}</div> : null}

          <section className="agentDockRoles" aria-label="专业角色">
            <h4>专业角色</h4>
            <ul>
              {roles.map((role) => (
                <li key={role.id} className={role.executable ? 'ready' : 'planned'} title={role.capability}>
                  <b>{role.id}</b>
                  <span>{role.name}</span>
                  <em>{role.stateLabel}</em>
                </li>
              ))}
              {roles.length === 0 ? <li className="planned"><span>正在读取角色目录…</span></li> : null}
            </ul>
          </section>

          <div className="agentDockRunLauncher" aria-label="发起试点任务">
            <select
              value={selectedResult}
              onChange={(event) => setSelectedResult(event.target.value)}
              aria-label="选择冻结剖面结果"
              disabled={frozenResults.length === 0 || runBusy}
            >
              {frozenResults.length === 0 ? (
                <option value="">暂无可用冻结结果</option>
              ) : frozenResults.map((item) => (
                <option key={item.resultHash} value={item.resultHash} title={item.resultHash}
                  disabled={!item.launchable}>
                  {item.resultHash.slice(0, 8)} · {item.sectionId || item.projectId || '单排桩剖面'}
                  {item.launchable ? ' · 可启动' : ' · 与当前工作版本不一致'}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void launchRun()}
              disabled={runBusy || !activeId
                || !frozenResults.find((item) => item.resultHash === selectedResult)?.launchable}
            >
              {runBusy ? '处理中…' : '发起任务'}
            </button>
          </div>

          <section className="agentDockIntake" aria-label="检索 Agent 参数填入">
            <div className="agentDockIntakeBar">
              <strong title={intakeFileName || undefined}>检索 Agent · 参数填入</strong>
              <label className="agentDockIntakePick">
                选择提取结果
                <input
                  type="file"
                  accept=".json,.pdf"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void loadIntakeFile(file);
                    event.target.value = '';
                  }}
                />
              </label>
            </div>
            {intakeFields.length ? (
              <>
                <ul className="agentDockIntakeList">
                  {intakeFields.map((field) => (
                    <li key={field.key} title={`${field.target} · 第 ${field.page ?? '?'} 页`}>
                      <label>
                        <input
                          type="checkbox"
                          checked={field.checked}
                          onChange={(event) => setIntakeFields((prev) => prev.map((item) =>
                            item.key === field.key ? { ...item, checked: event.target.checked } : item))}
                        />
                        <span>{field.label}</span>
                        <b>{String(field.value)}{field.unit ? ` ${field.unit}` : ''}</b>
                      </label>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="agentDockIntakeApply"
                  onClick={() => void applyIntake()}
                  disabled={intakeBusy}
                >
                  {intakeBusy ? '填入中…' : `填入当前项目（${intakeFields.filter((f) => f.checked).length} 项）`}
                </button>
              </>
            ) : (
              <p className="agentDockIntakeHint">选入提取结果 JSON → 预览 → 填入当前项目；水位、高程、土层参数需人工确认，不自动写入。</p>
            )}
          </section>

          <section className="agentDockThread" aria-label="会话">
            <div className="agentDockThreadBar">
              <select
                value={activeId}
                onChange={(event) => setActiveId(event.target.value)}
                aria-label="选择会话"
              >
                {conversations.map((item) => (
                  <option key={item.id} value={item.id}>{item.title}</option>
                ))}
              </select>
              <button type="button" className="secondary" onClick={() => void startConversation()} disabled={busy}>新建</button>
            </div>
            <div className="agentDockMessages" ref={threadRef}>
              {messages.length === 0 ? (
                <p className="agentDockEmpty">
                  提问会完整保留。当前助手只返回状态回执，等专业执行链接入后再由它作答。
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
          </section>

          <footer className="agentDockCompose">
            <input
              type="text"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') { event.preventDefault(); void send(); }
              }}
              placeholder="描述问题，例如：这批验算失败先改哪一项？"
              aria-label="向智能设计助手提问"
            />
            <div className="agentDockComposeBar">
              <small>模型未接入 · 仅状态回执</small>
              <button type="button" onClick={() => void send()} disabled={busy || !draft.trim()}>
                {busy ? '发送中…' : '发送'}
              </button>
            </div>
          </footer>

          {error ? <p className="agentDockError" role="alert">{error}</p> : null}
          <p className="agentDockNote">本助手不作工程判断，正式施工图签发始终关闭。</p>
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
