import { lazy, Suspense, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import { api } from '../api/client';
import BoreholeImport from '../components/BoreholeImport';
import ExcavationEditor from '../components/ExcavationEditor';
import RebarDesignPanel from '../components/RebarDesignPanel';
import DrawingRuleSetPanel from '../components/DrawingRuleSetPanel';
import CalculationRecoveryPanel from '../components/CalculationRecoveryPanel';
import PanelErrorBoundary from '../app/PanelErrorBoundary';
import ConstructionStageEditor from '../components/ConstructionStageEditor';
import SchemeComparisonPanel from '../components/SchemeComparisonPanel';
import ProjectDataWorkspacePanel from '../components/ProjectDataWorkspacePanel';
import DesignQualificationPanel, { type Qualification } from '../components/DesignQualificationPanel';
import ProgressiveDesignPanel, { type ProgressiveDesignSession } from '../components/ProgressiveDesignPanel';
import { beginGlobalActivity, finishGlobalActivity, FullPageLoadingFallback, updateGlobalActivity } from '../app/GlobalRequestProgress';
import { formatEngineeringValue, withUnitLabel } from '../utils/units';
import { effectiveGeologicalSurfaces, hasGeologicalSurfacePreview } from '../utils/geology';
import type { AssuranceResult, BenchmarkCaseSpec, BenchmarkRunResult, CadTemplateConfig, CalculationResult, IssueCenterItem, IssueCenterResult, PitTask, Project, RebarDetailingResult, StandardsProcessMatrix, StandardsProcessStep } from '../types/domain';

const AdvancedEngineeringPanel = lazy(() => import('../components/AdvancedEngineeringPanel'));
const GeologyViewer = lazy(() => import('../viewers/GeologyViewer'));
const RetainingSystemViewer = lazy(() => import('../viewers/RetainingSystemViewer'));
const ResultViewer = lazy(() => import('../viewers/ResultViewer'));
const Engineering3DViewer = lazy(() => import('../viewers/Engineering3DViewer'));
const RebarIfcViewer = lazy(() => import('../viewers/RebarIfcViewer'));

type WorkflowStepKey = 'settings' | 'boreholes' | 'geology' | 'excavation' | 'retaining' | 'calculation' | 'assurance' | 'export';
type StepStatus = 'done' | 'ready' | 'blocked' | 'warning' | 'error';
type WorkspaceRole = 'designer' | 'calculator' | 'detailer' | 'construction' | 'reviewer' | 'publisher';

const ROLE_LABELS: Record<WorkspaceRole, string> = { designer: '方案设计', calculator: '结构计算', detailer: '配筋深化', construction: '施工策划', reviewer: '校核审核', publisher: '图纸发行' };
const ROLE_STEPS: Record<WorkspaceRole, WorkflowStepKey[]> = { designer: ['settings','boreholes','geology','excavation','retaining'], calculator: ['retaining','calculation','assurance'], detailer: ['calculation','assurance','export'], construction: ['retaining','assurance','export'], reviewer: ['calculation','assurance'], publisher: ['assurance','export'] };
const REBAR_TYPE_LABELS: Record<string, string> = { longitudinal: '纵向主筋', stirrup: '箍筋', distribution: '分布构造筋', tie: '拉结/架立筋', additional: '附加筋' };
const ENGINEERING_STATUS_LABELS: Record<string, string> = { pass: '通过', warning: '需复核', manual_review: '人工复核', fail: '不通过', ready: '已就绪', preliminary: '初步结果', not_applicable: '不适用' };
const DETAIL_LEVEL_LABELS: Record<string, string> = { shop_drawing: '施工详图级', construction: '施工配筋级', full: '完整逐根钢筋几何', detailed: '深化设计级' };

function engineeringLabel(value: unknown, labels: Record<string, string>): string {
  const token = String(value ?? '-');
  return labels[token] ?? token.replace(/_/g, ' ');
}

type OperationPhaseStatus = 'pending' | 'running' | 'done' | 'error';

interface OperationPhase { label: string; detail?: string; status: OperationPhaseStatus }

interface ActiveOperation { title: string; detail?: string; progress: number; phases: OperationPhase[]; logs?: string[] }

interface WorkflowAction { label: string; detail?: string; action: () => Promise<unknown> }
type BackendTaskOperation = 'storage_compaction' | 'support_layout_optimization' | 'adopt_support_candidate' | 'industrial_closure' | 'calculation_full' | 'candidate_comparison' | 'export_ifc_light' | 'export_ifc_analysis' | 'export_ifc_construction_visual' | 'export_ifc_detailed' | 'export_report' | 'export_drawings_cad' | 'export_drawings_svg' | 'export_formal_drawings' | 'export_coordinated_delivery' | 'export_json' | 'export_trace' | 'export_issue_report' | 'export_rebar_detailing' | 'export_benchmark_cases' | 'export_wall_length_redundancy' | 'export_design_scheme_ledger' | 'full_delivery';

function SystemReliabilityStrip({ project }: { project: Project }) {
  const [snapshot, setSnapshot] = useState<{ readiness?: Record<string, any>; metrics?: Record<string, any>; error?: string }>({});
  const [refreshing, setRefreshing] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      setRefreshing(true);
      const [readiness, metrics] = await Promise.allSettled([api.systemReadiness(), api.systemMetrics()]);
      if (!cancelled) {
        setSnapshot({
          readiness: readiness.status === 'fulfilled' ? readiness.value : undefined,
          metrics: metrics.status === 'fulfilled' ? metrics.value : undefined,
          error: readiness.status === 'rejected' && metrics.status === 'rejected' ? '系统健康数据暂不可用' : undefined,
        });
        setRefreshing(false);
      }
    };
    void load();
    timer = window.setInterval(() => { if (!document.hidden) void load(); }, 30000);
    return () => { cancelled = true; if (timer) window.clearInterval(timer); };
  }, [project.id, project.updatedAt, refreshToken]);

  const readiness = snapshot.readiness ?? {};
  const metrics = snapshot.metrics ?? {};
  const http = (metrics.http ?? {}) as Record<string, any>;
  const tasks = (metrics.tasks ?? readiness.tasks ?? {}) as Record<string, any>;
  const latency = (http.latencyMs ?? {}) as Record<string, any>;
  const p95 = Number(latency.p95 ?? 0);
  const errorRate = Number(http.serverErrorRate ?? 0);
  const active = Number(http.activeRequestCount ?? 0);
  const taskStatuses = (tasks.statusCounts ?? {}) as Record<string, any>;
  const running = Number(tasks.running ?? tasks.runningCount ?? taskStatuses.running ?? 0);
  const queued = Number(tasks.queued ?? tasks.queuedCount ?? taskStatuses.queued ?? 0);
  const processMemory = Number(tasks.processMemoryMb ?? tasks.processResidentMemoryMB ?? 0);
  const memoryLimit = Number(tasks.memorySoftLimitMb ?? 0);
  const ready = readiness.ready !== false && !snapshot.error;
  const degraded = Boolean(readiness.degraded) || readiness.status === 'degraded';
  const memoryRatio = memoryLimit > 0 ? processMemory / memoryLimit : 0;
  const tone = !ready || errorRate > 0.03 || memoryRatio > 0.95 ? 'fail' : degraded || p95 > 2500 || errorRate > 0.01 || queued > 3 || memoryRatio > 0.8 ? 'warn' : 'pass';
  const label = tone === 'fail' ? '需处置' : tone === 'warn' ? '负载偏高' : '运行正常';
  return <section className={`systemReliabilityStrip ${tone}`} aria-label="系统运行可靠性">
    <div className="reliabilityHeadline"><strong>系统可靠性 · {label}</strong><span>{snapshot.error ?? (degraded ? String((readiness.degradedReasons ?? []).join('；') || '系统可用，但资源状态需要关注') : ready ? '数据库、运行依赖与任务服务可用' : '系统就绪检查未通过')}</span></div>
    <div className="reliabilityMetrics">
      <span>API P95 <b>{p95 ? `${Math.round(p95)} ms` : '-'}</b></span>
      <span>服务端错误率 <b>{(errorRate * 100).toFixed(2)}%</b></span>
      <span>并发请求 <b>{active}</b></span>
      <span>任务 运行/排队 <b>{running}/{queued}</b></span>
      <span>进程内存 <b>{processMemory ? `${processMemory.toFixed(0)} MB` : '-'}</b></span>
      <span>路径聚合 <b>{Number(http.pathCardinality ?? 0)}/{Number(http.pathAggregationBound ?? 0) || '-'}</b></span>
    </div>
    <button className="secondary" onClick={() => setRefreshToken((value) => value + 1)} disabled={refreshing} title="健康数据每30秒自动刷新">{refreshing ? '刷新中' : '自动监测'}</button>
  </section>;
}

function DeferredDetails({ summary, defaultOpen = false, children }: { summary: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return <details className="focusDetails" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{summary}</summary>
    {open ? children : null}
  </details>;
}

interface WorkflowStep {
  key: WorkflowStepKey;
  index: number;
  title: string;
  subtitle: string;
  required: string[];
  status: StepStatus;
  message: string;
}

export default function ProjectWorkspace({ project, onBack, onProjectChange }: { project: Project; onBack: () => void; onProjectChange: (project: Project) => void }) {
  const [active, setActive] = useState<WorkflowStepKey>('settings');
  const [current, setCurrent] = useState<Project>(project);
  const [error, setError] = useState<string | undefined>();
  const [busy, setBusy] = useState<string | undefined>();
  const [operation, setOperation] = useState<ActiveOperation | undefined>();
  const [vtuMessage, setVtuMessage] = useState<string | undefined>();
  const [selectedLocator, setSelectedLocator] = useState<Record<string, unknown> | undefined>();
  const [viewMode, setViewMode] = useState<'compact' | 'professional'>(() => {
    const saved = window.localStorage.getItem('pitguard-workspace-mode');
    if (saved === 'professional' || saved === 'compact') return saved;
    return project.designSettings?.defaultWorkspaceMode === 'professional' ? 'professional' : 'compact';
  });
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState('');
  const operationActivityId = useRef<string | undefined>(undefined);
  const [workspaceRole, setWorkspaceRole] = useState<WorkspaceRole>(() => {
    const saved = window.localStorage.getItem('pitguard-workspace-role') as WorkspaceRole | null;
    return saved && saved in ROLE_LABELS ? saved : 'designer';
  });

  useEffect(() => {
    setCurrent(project);
  }, [project]);

  useEffect(() => { window.localStorage.setItem('pitguard-workspace-mode', viewMode); }, [viewMode]);
  useEffect(() => { window.localStorage.setItem('pitguard-workspace-role', workspaceRole); }, [workspaceRole]);

  useEffect(() => {
    if (!operation) {
      if (operationActivityId.current) {
        finishGlobalActivity(operationActivityId.current, { ok: true, phase: busy ? '任务已转入后台执行' : '操作已结束' });
        operationActivityId.current = undefined;
      }
      return;
    }
    if (!operationActivityId.current) {
      operationActivityId.current = beginGlobalActivity({
        label: operation.title,
        phase: operation.detail,
        expectedMs: 12000,
        blocking: true,
        progress: operation.progress,
        path: `local://project/${current.id}/operation`,
      });
    }
    const failed = operation.phases.some((phase) => phase.status === 'error');
    updateGlobalActivity(operationActivityId.current, {
      label: operation.title,
      phase: operation.detail || operation.phases.find((phase) => phase.status === 'running')?.label,
      progress: operation.progress,
      blocking: true,
    });
    if (failed) {
      finishGlobalActivity(operationActivityId.current, { ok: false, error: operation.detail || '工程操作失败', progress: operation.progress });
      operationActivityId.current = undefined;
    } else if (operation.progress >= 100) {
      finishGlobalActivity(operationActivityId.current, { ok: true, phase: operation.detail || '工程操作已完成', progress: 100 });
      operationActivityId.current = undefined;
    }
  }, [operation, busy, current.id]);

  useEffect(() => {
    let cancelled = false;
    const raw = window.sessionStorage.getItem('pitguard-active-task');
    if (!raw) return () => { cancelled = true; };
    let saved: { projectId?: string; taskId?: string; title?: string; autoDownload?: boolean };
    try { saved = JSON.parse(raw); } catch { window.sessionStorage.removeItem('pitguard-active-task'); return () => { cancelled = true; }; }
    if (saved.projectId !== current.id || !saved.taskId) return () => { cancelled = true; };

    const terminal = new Set(['success', 'failed', 'cancelled', 'interrupted']);
    void (async () => {
      let task: PitTask;
      try { task = await api.getTask(String(saved.taskId)); }
      catch { return; }
      if (cancelled) return;
      setBusy(saved.title || task.title || '恢复后台任务');
      setOperation({ title: saved.title || task.title || '恢复后台任务', detail: `检测到未完成任务：${task.currentStep}`, progress: task.progress, logs: task.logs?.slice(-8), phases: [{ label: task.currentStep || '恢复任务', status: terminal.has(task.status) ? (task.status === 'success' ? 'done' : 'error') : 'running' }] });
      let failures = 0;
      while (!cancelled && !terminal.has(task.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, document.hidden ? 5000 : 1500));
        try {
          task = await api.getTask(task.id);
          failures = 0;
          if (!cancelled) setOperation({ title: saved.title || task.title, detail: `${task.currentStep} · ${task.status}`, progress: task.progress, logs: task.logs?.slice(-8), phases: [{ label: task.currentStep || '后台任务', status: 'running' }] });
        } catch {
          failures += 1;
          if (!cancelled) setOperation((value) => value ? { ...value, detail: `任务仍在后台，API重连中（${failures}/8）` } : value);
          if (failures >= 8) break;
        }
      }
      if (cancelled) return;
      if (terminal.has(task.status)) {
        window.sessionStorage.removeItem('pitguard-active-task');
        if (task.status === 'success') {
          if (saved.autoDownload && task.result?.filePath) await downloadTaskFile(task);
          try {
            const updated = await api.getProject(current.id);
            if (!cancelled) { setCurrent(updated); onProjectChange(updated); }
          } catch { /* Completed task remains persisted; a later refresh can reload it. */ }
          if (!cancelled) setOperation({ title: saved.title || task.title, detail: '后台任务已完成，项目状态已恢复。', progress: 100, logs: task.logs?.slice(-8), phases: [{ label: '完成', status: 'done' }] });
        } else {
          if (!cancelled) setError(task.error || `后台任务状态：${task.status}`);
        }
      }
      if (!cancelled) setBusy(undefined);
    })();
    return () => { cancelled = true; };
  }, [current.id, onProjectChange]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setCommandOpen((value) => !value); }
      if (event.key === 'Escape') setCommandOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const steps = useMemo(() => buildWorkflowSteps(current), [current]);
  const activeStep = steps.find((step) => step.key === active) ?? steps[0];
  const activeIndex = steps.findIndex((step) => step.key === active);
  const nextStep = activeIndex >= 0 ? steps[activeIndex + 1] : undefined;
  const previousStep = activeIndex > 0 ? steps[activeIndex - 1] : undefined;
  const latestResult = getLatestResult(current);
  const failCount = latestResult?.checkSummary?.fail ?? 0;
  const warningCount = latestResult?.checkSummary?.warning ?? 0;
  const manualReviewCount = latestResult?.checkSummary?.manualReview ?? latestResult?.checkSummary?.manual_review ?? 0;

  async function refresh(provided?: Project) {
    const updated = provided ?? await api.getProject(current.id);
    setCurrent(updated);
    onProjectChange(updated);
  }

  async function runStep(label: string, step: () => Promise<unknown>) {
    return runWorkflow(label, [{ label, action: step }]);
  }

  async function runWorkflow(title: string, actions: WorkflowAction[]) {
    const total = Math.max(actions.length, 1);
    const phases: OperationPhase[] = actions.map((item, index) => ({ label: item.label, detail: item.detail, status: index === 0 ? 'running' : 'pending' }));
    try {
      setBusy(title);
      setError(undefined);
      setOperation({ title, detail: '后台任务已提交，正在按步骤执行。请勿重复点击同一操作。', progress: 2, phases });
      for (let i = 0; i < actions.length; i += 1) {
        setOperation((prev) => prev ? { ...prev, progress: Math.round((i / total) * 92 + 4), phases: prev.phases.map((phase, idx) => ({ ...phase, status: idx < i ? 'done' : idx === i ? 'running' : 'pending' })) } : prev);
        await actions[i].action();
        setOperation((prev) => prev ? { ...prev, progress: Math.round(((i + 1) / total) * 92 + 4), phases: prev.phases.map((phase, idx) => ({ ...phase, status: idx <= i ? 'done' : idx === i + 1 ? 'running' : 'pending' })) } : prev);
      }
      await refresh();
      setOperation((prev) => prev ? { ...prev, detail: '任务完成，项目数据已刷新。', progress: 100, phases: prev.phases.map((phase) => ({ ...phase, status: 'done' })) } : prev);
      window.setTimeout(() => setOperation(undefined), 1400);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      try {
        await refresh();
      } catch {
        // Keep the original operation error visible; refresh failure is secondary.
      }
      setOperation((prev) => prev ? { ...prev, detail: `${message}。已尝试刷新已完成步骤的数据。`, progress: Math.max(prev.progress, 5), phases: prev.phases.map((phase) => phase.status === 'running' ? { ...phase, status: 'error' } : phase) } : prev);
    } finally {
      setBusy(undefined);
    }
  }


  async function runBackendTask(title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload = false) {
    const terminalStatuses = new Set(['success', 'failed', 'cancelled', 'interrupted']);
    let task: PitTask | undefined;
    try {
      setBusy(title);
      setError(undefined);
      setOperation({ title, detail: '后端任务队列已接管该操作，正在轮询真实进度。', progress: 2, phases: [{ label: '提交任务', status: 'running' }] });
      task = await api.createTask(current.id, operationName, payload ?? {});
      window.sessionStorage.setItem('pitguard-active-task', JSON.stringify({ projectId: current.id, taskId: task.id, title, autoDownload }));
      setOperation({ title, detail: task.currentStep, progress: task.progress, logs: task.logs, phases: [{ label: task.currentStep || title, status: 'running' }] });
      const started = Date.now();
      let transientFailures = 0;
      while (!terminalStatuses.has(task.status)) {
        const pollDelay = document.hidden ? 5000 : Math.min(5000, 1000 + Math.floor((Date.now() - started) / 120000) * 500);
        await new Promise((resolve) => window.setTimeout(resolve, pollDelay));
        try {
          task = await api.getTask(task.id);
          transientFailures = 0;
          setOperation({
            title,
            detail: `${task.currentStep} · ${task.status}`,
            progress: task.progress,
            logs: task.logs?.slice(-8),
            phases: [{ label: task.currentStep || title, status: task.status === 'running' || task.status === 'queued' ? 'running' : task.status === 'success' ? 'done' : 'error' }]
          });
        } catch (pollError) {
          transientFailures += 1;
          const message = pollError instanceof Error ? pollError.message : String(pollError);
          setOperation((prev) => prev ? { ...prev, detail: `API暂时不可用，计算worker可能仍在运行。正在重连（${transientFailures}/8）：${message}` } : prev);
          if (transientFailures >= 8) {
            throw new Error('连续8次无法读取任务状态。网页已停止轮询，后台任务不会重复提交；请刷新页面或查看worker日志。');
          }
          continue;
        }
        if (Date.now() - started > 40 * 60 * 1000) throw new Error('任务轮询超过40分钟。后台硬超时会独立终止worker，请查看任务状态和worker日志。');
      }
      if (task.status !== 'success') throw new Error(task.error || `任务状态：${task.status}`);
      if (autoDownload && task.result?.filePath) await downloadTaskFile(task);
      try {
        await refresh();
      } catch (refreshError) {
        const message = refreshError instanceof Error ? refreshError.message : String(refreshError);
        setOperation({ title, detail: `任务已完成，但项目刷新暂时失败：${message}。可安全刷新浏览器，任务不会重复执行。`, progress: 100, logs: task.logs?.slice(-8), phases: [{ label: '计算完成，等待页面恢复', status: 'done' }] });
        return;
      }
      setOperation({ title, detail: autoDownload ? '文件已生成并开始下载，项目数据已刷新。' : '任务完成，项目数据已刷新。', progress: 100, logs: task.logs?.slice(-8), phases: [{ label: '完成', status: 'done' }] });
      window.setTimeout(() => setOperation(undefined), 1800);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setOperation((prev) => prev ? { ...prev, detail: message, phases: prev.phases.map((phase) => ({ ...phase, status: phase.status === 'done' ? 'done' : 'error' })) } : prev);
    } finally {
      if (task && terminalStatuses.has(task.status)) window.sessionStorage.removeItem('pitguard-active-task');
      setBusy(undefined);
    }
  }

  async function downloadTaskFile(task: PitTask) {
    const response = await fetch(api.taskDownloadUrl(task.id));
    if (!response.ok) throw new Error(await response.text());
    const blob = await response.blob();
    const filename = String(task.result?.filename || `pitguard-task-${task.id}`);
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  }

  async function importVtu(file?: File) {
    if (!file) return;
    try {
      setBusy('正在导入 VTU');
      setError(undefined);
      const mesh = await api.importVtu(current.id, file);
      setVtuMessage(`VTU 已导入：${mesh.summary?.pointCount ?? mesh.points?.length ?? 0} 点 / ${mesh.summary?.cellCount ?? mesh.cellBlocks?.length ?? 0} 单元`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(undefined);
    }
  }

  function goNext() {
    if (nextStep) setActive(nextStep.key);
  }

  function goPrevious() {
    if (previousStep) setActive(previousStep.key);
  }

  function locateIssue(item: IssueCenterItem) {
    const locator = item.locator ?? { workflowStep: item.workflowStep, targetPanel: item.targetPanel, objectType: item.objectType, objectId: item.objectId };
    setSelectedLocator({ ...locator, message: item.message, recommendation: item.recommendation });
    const step = String(locator.workflowStep ?? item.workflowStep ?? '') as WorkflowStepKey;
    if (['settings', 'boreholes', 'geology', 'excavation', 'retaining', 'calculation', 'assurance', 'export'].includes(step)) setActive(step);
  }

  return (
    <main className="page workflowPage" id="main-workspace"><a className="skipLink" href="#workflow-main-panel">跳到当前设计步骤</a>
      <div className="workspaceHeader card">
        <div className="workspaceTitle">
          <button className="secondary" onClick={onBack}>返回项目列表</button>
          <div>
            <h2>{current.name}</h2>
            <p>{current.location ?? '未设置地点'} · 基坑围护结构流程化设计工作台</p>
          </div>
        </div>
        <div className="workspaceBadges">
          <button className="secondary" onClick={() => setCommandOpen(true)} title="命令面板快捷键 Ctrl/Command + K">命令 Ctrl+K</button>
          <span className="unitSystemBadge" title="工程数据默认采用 SI 工程单位；表头和关键数值均显式标注单位。">SI · m / kN / MPa</span>
          <label className="workspaceRoleSelect">角色<select value={workspaceRole} onChange={(event) => setWorkspaceRole(event.target.value as WorkspaceRole)}>{Object.entries(ROLE_LABELS).map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <button className="secondary workspaceModeToggle" onClick={() => setViewMode((value) => value === 'compact' ? 'professional' : 'compact')} title="精简模式保留当前步骤、下一步和交付状态；专业模式显示全部工程指标与决策面板。">{viewMode === 'compact' ? '进入专业模式' : '返回专注模式'}</button>
          <StatusPill label="流程" value={overallFlowLabel(steps)} tone={steps.every((s) => s.status === 'done' || s.status === 'warning') ? 'pass' : 'warn'} />
          <StatusPill label="Fail" value={String(failCount)} tone={failCount > 0 ? 'fail' : 'pass'} />
          <StatusPill label="Warning" value={String(warningCount)} tone={warningCount > 0 ? 'warn' : 'pass'} />
          <StatusPill label="人工复核" value={String(manualReviewCount)} tone="review" />
        </div>
      </div>

      <SystemReliabilityStrip project={current} />
      <ProjectDataWorkspacePanel project={current} />

      {(error || busy || operation) && (
        <div className="workflowMessageStack">
          {operation && <OperationProgress operation={operation} onDismiss={() => setOperation(undefined)} />}
          {!operation && busy && <div className="info">{busy}...</div>}
          {error && <div className="error">{error}</div>}
        </div>
      )}

      <div className="workflowLayout">
        <aside className="workflowAside card">
          <h3>工程流程</h3>
          <p className="small">按设计流程推进。精简模式仅保留当前操作和必要状态。</p>
          <ol className="workflowStepper">
            {steps.map((step) => (
              <li key={step.key} className={`workflowStep ${active === step.key ? 'active' : ''} ${step.status} ${ROLE_STEPS[workspaceRole].includes(step.key) ? 'rolePrimary' : 'roleSecondary'}`}>
                <button onClick={() => setActive(step.key)}>
                  <span className="stepIndex">{step.index}</span>
                  <span className="stepText">
                    <strong>{step.title}</strong>
                    <em>{step.message}</em>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          {viewMode === 'professional' ? <ProjectTreeSummary project={current} /> : null}
        </aside>

        <section className="workflowMain card" id="workflow-main-panel" tabIndex={-1}>
          {viewMode === 'compact'
            ? <WorkspaceFocusSummary project={current} activeStep={activeStep} nextStep={nextStep} onJump={setActive} />
            : <><OperatorDashboard project={current} steps={steps} /><ProjectDeliveryDashboard project={current} onJump={setActive} /><NextActionPanel activeStep={activeStep} nextStep={nextStep} project={current} /><EngineeringDecisionBoard project={current} steps={steps} onJump={setActive} /></>}
          <StepHeader step={activeStep} project={current} compact={viewMode === 'compact'} />
          <WorkflowStandardsRibbon projectId={current.id} revision={current.updatedAt} stepKey={activeStep.key} />
          {selectedLocator && <LocatorBanner locator={selectedLocator} onClear={() => setSelectedLocator(undefined)} />}
          <Suspense fallback={<FullPageLoadingFallback label="正在加载当前专业视图" detail="正在按需装载三维查看器、计算结果或深化设计模块。" />}>
            <StepBody
              active={active}
              project={current}
              onRefresh={refresh}
              runStep={runStep}
              runWorkflow={runWorkflow}
              runTask={runBackendTask}
              importVtu={importVtu}
              vtuMessage={vtuMessage}
              selectedLocator={selectedLocator}
              onLocateIssue={locateIssue}
              onJump={setActive}
              viewMode={viewMode}
            />
          </Suspense>
          <div className="workflowFooter">
            <button className="secondary" onClick={goPrevious} disabled={!previousStep}>上一步</button>
            <button onClick={goNext} disabled={!nextStep}>下一步：{nextStep?.title ?? '已到末尾'}</button>
          </div>
        </section>
      </div>
      {commandOpen ? <CommandPalette steps={steps} query={commandQuery} onQuery={setCommandQuery} onClose={() => setCommandOpen(false)} onSelect={(key) => { setActive(key); setCommandOpen(false); setCommandQuery(''); }} onToggleMode={() => { setViewMode((value) => value === 'compact' ? 'professional' : 'compact'); setCommandOpen(false); }} /> : null}
    </main>
  );
}



function RoleFocusBar({ role, steps, active, onJump }: { role: WorkspaceRole; steps: WorkflowStep[]; active: WorkflowStepKey; onJump: (key: WorkflowStepKey) => void }) {
  const primary = ROLE_STEPS[role].map((key) => steps.find((step) => step.key === key)).filter(Boolean) as WorkflowStep[];
  const next = primary.find((step) => !['done','warning'].includes(step.status)) ?? primary.find((step) => step.key === active) ?? primary[0];
  return <section className="roleFocusBar">
    <div><span>当前角色</span><strong>{ROLE_LABELS[role]}</strong><em>仅调整信息优先级，不隐藏闭环审查和其他专业模块。</em></div>
    <nav aria-label={`${ROLE_LABELS[role]}重点流程`}>{primary.map((step) => <button type="button" key={step.key} className={step.key === active ? 'active' : ''} onClick={() => onJump(step.key)}>{step.title}<small>{step.status}</small></button>)}</nav>
    {next ? <button type="button" className="roleNextButton" onClick={() => onJump(next.key)}>角色下一步：{next.title}</button> : null}
  </section>;
}

function WorkspaceFocusSummary({ project, activeStep, nextStep, onJump }: { project: Project; activeStep: WorkflowStep; nextStep?: WorkflowStep; onJump: (key: WorkflowStepKey) => void }) {
  const latest = getLatestResult(project);
  const fail = Number(latest?.checkSummary?.fail ?? 0);
  const warning = Number(latest?.checkSummary?.warning ?? 0);
  const review = Number(latest?.checkSummary?.manualReview ?? latest?.checkSummary?.manual_review ?? 0);
  const gate = latest?.formalReportGate;
  const candidateCount = project.retainingSystem?.supportLayoutRepair?.candidates?.length ?? 0;
  const fullCount = latest?.supportLayoutRepair?.candidateFullCalculations?.length ?? 0;
  const governing = latest?.governingValues;
  const issueHeadline = fail > 0
    ? `${fail} 个硬性问题需要先处理`
    : warning > 0 || review > 0
      ? `${warning} 个预警、${review} 个人工复核项`
      : latest ? '当前计算未发现硬性阻断' : '尚未形成计算结果';
  const nextKey: WorkflowStepKey = fail > 0 ? 'calculation' : gate?.allowedForOfficialIssue ? 'export' : nextStep?.key ?? activeStep.key;
  const nextLabel = fail > 0 ? '处理计算问题' : gate?.allowedForOfficialIssue ? '进入图纸交付' : nextStep ? `继续：${nextStep.title}` : '保持当前步骤';
  return <section className="workspaceFocusSummary" aria-label="当前设计焦点">
    <article className="focusOutcomeCard">
      <span className="sectionKicker">当前成果</span>
      <strong>{activeStep.title}</strong>
      <p>{activeStep.message}</p>
      <div className="focusMetricRow">
        <span><small>{withUnitLabel('最大位移', 'displacement')}</small><b>{formatEngineeringValue(governing?.maxDisplacement, 'displacement')}</b></span>
        <span><small>{withUnitLabel('最大轴力', 'force')}</small><b>{formatEngineeringValue(governing?.maxSupportAxialForce, 'force')}</b></span>
        <span><small>候选 / 完整比选</small><b>{candidateCount} / {fullCount}</b></span>
      </div>
    </article>
    <article className={`focusIssueCard ${fail > 0 ? 'fail' : warning > 0 || review > 0 ? 'warn' : 'pass'}`}>
      <span className="sectionKicker">关键问题</span>
      <strong>{issueHeadline}</strong>
      <p>{gate?.headline ?? (latest ? '问题已按构件和控制工况归并，详细矩阵与原始台账放在专业模式。' : '先完成围护结构和施工工况。')}</p>
      <div className="focusIssueCounts"><span>Fail {fail}</span><span>Warning {warning}</span><span>复核 {review}</span></div>
    </article>
    <article className="focusNextCard">
      <span className="sectionKicker">下一步动作</span>
      <strong>{nextLabel}</strong>
      <p>主流程只保留一个推荐动作；高级参数、逐墙微调和原始结果均可在专业模式查看。</p>
      <button onClick={() => onJump(nextKey)}>{nextLabel}</button>
    </article>
  </section>;
}


function CommandPalette({ steps, query, onQuery, onClose, onSelect, onToggleMode }: { steps: WorkflowStep[]; query: string; onQuery: (value: string) => void; onClose: () => void; onSelect: (key: WorkflowStepKey) => void; onToggleMode: () => void }) {
  const q = query.trim().toLowerCase();
  const filtered = steps.filter((step) => !q || `${step.title} ${step.subtitle} ${step.message}`.toLowerCase().includes(q));
  return <div className="commandOverlay" role="dialog" aria-modal="true" aria-label="PitGuard 命令面板" onMouseDown={(event) => { if (event.target === event.currentTarget) { onClose(); } }}>
    <div className="commandPalette">
      <div className="commandHeader"><strong>快速跳转与显示</strong><button className="secondary tiny" onClick={onClose}>关闭 Esc</button></div>
      <input autoFocus value={query} onChange={(event) => onQuery(event.target.value)} placeholder="搜索流程、操作或状态…" aria-label="搜索命令" />
      <div className="commandList">{filtered.map((step) => <button key={step.key} onClick={() => onSelect(step.key)}><strong>{step.index}. {step.title}</strong><span>{step.message}</span></button>)}<button onClick={onToggleMode}><strong>切换精简/专业模式</strong><span>控制工作台信息密度</span></button></div>
    </div>
  </div>;
}


function formatLocatorCenter(value: unknown) {
  const v = value as { x?: number; y?: number; z?: number } | undefined;
  if (!v || typeof v !== 'object') return '无坐标';
  const xy = [v.x, v.y].map((n) => typeof n === 'number' ? n.toFixed(2) : '-').join(', ');
  return `坐标 (${xy}${typeof v.z === 'number' ? `, ${v.z.toFixed(2)}` : ''})`;
}

function LocatorBanner({ locator, onClear }: { locator: Record<string, unknown>; onClear: () => void }) {
  return (
    <div className="locatorBanner">
      <div>
        <strong>对象级定位</strong>
        <span>{String(locator.objectType ?? '-')} · {String(locator.objectId ?? locator.objectCode ?? '-')} · {String(locator.targetPanel ?? locator.workflowStep ?? '-')}</span>
        <em>{formatLocatorCenter(locator.center)}{locator.drawingSheet ? ` · CAD ${String(locator.drawingSheet)}` : ''}</em>
        {locator.message ? <p>{String(locator.message)}</p> : null}
      </div>
      <button className="secondary tiny" onClick={onClear}>清除定位</button>
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function statusToneFromText(value?: unknown): 'pass' | 'warn' | 'fail' | 'review' {
  const text = String(value ?? '').toLowerCase();
  if (['ready', 'pass', 'closed'].includes(text)) return 'pass';
  if (['blocked', 'fail'].includes(text)) return 'fail';
  if (text.includes('review')) return 'review';
  return 'warn';
}

function ProjectDeliveryDashboard({ project, onJump }: { project: Project; onJump: (key: WorkflowStepKey) => void }) {
  const [data, setData] = useState<Record<string, unknown> | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    api.getProjectDashboard(project.id).then((result) => { if (alive) setData(result); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, project.calculationResults.length]);
  if (error) return <div className="warning">项目驾驶舱读取失败：{error}</div>;
  if (!data) return <div className="deliveryDashboard skeletonPanel">正在读取项目交付状态...</div>;
  const kpis = asRecord(data.currentKpis);
  const gate = asRecord(data.deliveryGate);
  const activeScheme = asRecord(data.activeScheme);
  const closed = asRecord(data.wallLengthClosedLoop);
  const closedStatus = asRecord(closed.closedLoopStatus);
  const nextActions = (data.nextActions as Record<string, unknown>[] | undefined) ?? [];
  const check = asRecord(kpis.latestCheckSummary);
  const delta = asRecord(activeScheme.delta);
  return (
    <section className="deliveryDashboard">
      <div className="deliveryHeader">
        <div><strong>项目交付驾驶舱</strong><span>{String(data.headline ?? '正在评估交付状态')}</span></div>
        <button className="secondary" onClick={() => onJump(gate.recomputeRequired ? 'calculation' : gate.status === 'blocked' ? 'assurance' : 'export')}>{gate.recomputeRequired ? '去复算' : gate.status === 'blocked' ? '查看问题' : '去导出'}</button>
      </div>
      <div className="maturityGrid compactDashboardGrid">
        <StatusCard title="交付闸门" value={String(gate.status ?? '-')} detail={String(gate.headline ?? '-')} tone={statusToneFromText(gate.status)} />
        <StatusCard title="规范状态" value={`${String(check.fail ?? 0)}/${String(check.warning ?? 0)}`} detail="Fail / Warning" tone={Number(check.fail ?? 0) > 0 ? 'fail' : Number(check.warning ?? 0) > 0 ? 'warn' : 'pass'} />
        <StatusCard title="墙长闭环" value={String(closedStatus.status ?? '-')} detail={String(closed.historySummary ? `历史 ${String(asRecord(closed.historySummary).count ?? 0)} 次` : '暂无历史')} tone={statusToneFromText(closedStatus.severity ?? closedStatus.status)} />
        <StatusCard title="方案快照" value={String(activeScheme.schemeId ?? '-')} detail={activeScheme.status ? String(activeScheme.status) : '尚未采纳优化'} tone={statusToneFromText(activeScheme.status)} />
      </div>
      <div className="deliveryKpiRow">
        <span>统一墙厚 <strong>{formatEngineeringValue(kpis.uniformWallThickness, 'length')}</strong></span>
        <span>设计/物理长度 <strong>{String(kpis.designToPhysicalLengthRatio ?? '-')}</strong></span>
        <span>最大位移 <strong>{formatEngineeringValue(kpis.maxWallDisplacement, 'displacement')}</strong></span>
        <span>支撑轴力峰值 <strong>{formatEngineeringValue(kpis.maxSupportAxialForce, 'force')}</strong></span>
        <span>墙长变化 <strong>{formatEngineeringValue(delta.changedDesignLengthDelta ?? 0, 'length')}</strong></span>
      </div>
      {nextActions.length ? <ol className="nextActionList dashboardActions">{nextActions.slice(0, 4).map((item, index) => <li key={`${String(item.title)}-${index}`}><strong>{String(item.workflowStep)}</strong><span>{String(item.title)}</span><em>{String(item.recommendation)}</em></li>)}</ol> : null}
    </section>
  );
}

function OperatorDashboard({ project, steps }: { project: Project; steps: WorkflowStep[] }) {
  const latest = getLatestResult(project);
  const doneCount = steps.filter((s) => s.status === 'done' || s.status === 'warning').length;
  const quality = latest?.supportLayoutQuality;
  const gate = latest?.formalReportGate;
  return (
    <div className="operatorDashboard">
      <div className="operatorCard"><span>流程进度</span><strong>{doneCount}/{steps.length}</strong></div>
      <div className="operatorCard"><span>支撑布置评分</span><strong>{quality?.score ?? '-'}</strong></div>
      <div className="operatorCard"><span>支撑优化候选</span><strong>{latest?.supportLayoutRepair?.candidateCount ?? latest?.supportLayoutRepair?.candidates?.length ?? 0}</strong></div>
      <div className="operatorCard"><span>正式出图闸门</span><strong>{gate?.allowedForOfficialIssue ? '可出图' : gate ? '需复核' : '-'}</strong></div>
    </div>
  );
}

function NextActionPanel({ activeStep, nextStep, project }: { activeStep: WorkflowStep; nextStep?: WorkflowStep; project: Project }) {
  const latest = getLatestResult(project);
  let action = activeStep.message;
  if (activeStep.status === 'done' || activeStep.status === 'warning') action = nextStep ? `建议进入：${nextStep.title}` : '流程已到成果导出末端';
  if (latest?.formalReportGate && !latest.formalReportGate.allowedForOfficialIssue) action = latest.formalReportGate.headline || '正式出图前仍需处理警告/缺项';
  return <div className="nextActionPanel"><div><strong>操作员下一步</strong><span>{action}</span></div><span>{activeStep.status === 'blocked' ? '请先完成前置步骤' : '常用操作已保留在主按钮，高级参数收纳在侧拉框'}</span></div>;
}

function EngineeringDecisionBoard({ project, steps, onJump }: { project: Project; steps: WorkflowStep[]; onJump: (key: WorkflowStepKey) => void }) {
  const latest = getLatestResult(project);
  const ret = project.retainingSystem;
  const locks = ret?.optimizationLocks ?? [];
  const lineLocks = ret?.supports.filter((support) => support.optimizationLocked).length ?? 0;
  const endpointLocks = ret?.supports.reduce((sum, support) => sum + (support.optimizationLockedStart ? 1 : 0) + (support.optimizationLockedEnd ? 1 : 0), 0) ?? 0;
  const levelLocks = new Set(locks.filter((item) => String(item.targetType ?? item.target_type ?? '') === 'support_level' && item.locked !== false).map((item) => String(item.levelIndex ?? item.level_index))).size;
  const obstacleLocks = project.excavation?.obstacles?.filter((obstacle) => obstacle.optimizationLocked).length ?? 0;
  const candidateCount = ret?.supportLayoutRepair?.candidateCount ?? ret?.supportLayoutRepair?.candidates?.length ?? latest?.supportLayoutRepair?.candidateCount ?? latest?.supportLayoutRepair?.candidates?.length ?? 0;
  const fullComparison = (latest?.supportLayoutRepair?.candidateFullCalculations ?? (latest?.reportDiagramData?.candidateFullCalculationComparison as Record<string, unknown>[] | undefined) ?? []).length;
  const blockingItems = latest?.formalReportGate?.blockingItems?.length ?? latest?.checkSummary?.fail ?? 0;
  const warningItems = latest?.formalReportGate?.warningItems?.length ?? latest?.checkSummary?.warning ?? 0;
  const readiness = [
    { label: '地勘资料', value: `${project.boreholes.length} 钻孔 / ${project.strata.length} 地层`, done: project.boreholes.length > 0 && project.strata.length > 0, key: 'boreholes' as const },
    { label: '地质模型', value: hasGeologicalSurfacePreview(project) ? `${effectiveGeologicalSurfaces(project).length} 个地层面${project.geologicalModel?.coverageAudit?.autoExtended ? ' / 已外扩' : ''}` : '未生成', done: Boolean(hasGeologicalSurfacePreview(project) && project.geologicalModel?.coverageAudit?.designDomainCovered !== false), key: 'geology' as const },
    { label: '基坑轮廓', value: project.excavation ? `${project.excavation.segments.length} 边段 / ${project.excavation.depth}m` : '未录入', done: Boolean(project.excavation?.segments?.length), key: 'excavation' as const },
    { label: '围护体系', value: ret ? `${ret.diaphragmWalls.length} 墙 / ${ret.supports.length} 支撑 / ${ret.columns.length} 立柱` : '未生成', done: Boolean(ret?.diaphragmWalls?.length && ret?.supports?.length), key: 'retaining' as const },
    { label: '计算结果', value: latest ? `${latest.stageResults?.length ?? 0} 工况结果` : '未计算', done: Boolean(latest), key: 'calculation' as const },
    { label: '成果闸门', value: latest?.formalReportGate?.allowedForOfficialIssue ? '可出图' : latest?.formalReportGate ? '需复核' : '未检查', done: Boolean(latest?.formalReportGate?.allowedForOfficialIssue), key: 'export' as const }
  ];
  const firstOpen = steps.find((step) => ['ready', 'warning', 'error', 'blocked'].includes(step.status) && step.status !== 'done') ?? steps[0];
  return (
    <section className="decisionBoard">
      <div className="decisionBoardHeader">
        <div>
          <strong>设计决策驾驶舱</strong>
          <span>把数据完整性、人工锁定、候选比选和出图闸门集中显示，降低多步骤流程下的误操作风险。</span>
        </div>
        <button className="secondary" onClick={() => onJump(firstOpen.key)}>定位当前关键步骤：{firstOpen.title}</button>
      </div>
      <div className="readinessStrip">
        {readiness.map((item) => (
          <button key={item.label} className={`readinessItem ${item.done ? 'done' : 'open'}`} onClick={() => onJump(item.key)}>
            <span>{item.label}</span><strong>{item.value}</strong>
          </button>
        ))}
      </div>
      <div className="interactionSummary">
        <div><span>局部锁定</span><strong>{lineLocks + endpointLocks + levelLocks + obstacleLocks}</strong><em>整线 {lineLocks} / 端点 {endpointLocks} / 层 {levelLocks} / 通道 {obstacleLocks}</em></div>
        <div><span>候选方案</span><strong>{candidateCount}</strong><em>{candidateCount ? '可查看差异动画并采纳方案' : '建议先生成 3-5 个候选方案'}</em></div>
        <div><span>A/B/C 完整比选</span><strong>{fullComparison}</strong><em>{fullComparison ? '已进入计算书方案比选' : '计算页可运行前 3 个候选完整计算'}</em></div>
        <div><span>出图风险</span><strong>{blockingItems}/{warningItems}</strong><em>阻断项 / 警告项</em></div>
      </div>
    </section>
  );
}

function buildWorkflowSteps(project: Project): WorkflowStep[] {
  const latest = getLatestResult(project);
  const hasChecks = Boolean(latest?.checks?.length);
  const hasFail = Boolean((latest?.checkSummary?.fail ?? 0) > 0 || latest?.governingValues?.governingCheckStatus === 'fail');
  const hasWarnings = Boolean((latest?.checkSummary?.warning ?? 0) > 0);
  const hasManualReview = Boolean((latest?.checkSummary?.manualReview ?? latest?.checkSummary?.manual_review ?? 0) > 0);
  const base: WorkflowStep[] = [
    {
      key: 'settings', index: 1, title: '项目设置', subtitle: '确认单位、地下水位、超载和规范规则集。',
      required: ['项目已创建', '单位体系已建立', '设计参数可读取'], status: 'done', message: '基础设置已就绪'
    },
    {
      key: 'boreholes', index: 2, title: '地勘资料', subtitle: '导入钻孔 CSV/XLSX，检查地层和物理力学参数。',
      required: ['至少 1 个钻孔', '至少 1 个地层', '关键土参数已合并'], status: project.boreholes.length && project.strata.length ? 'done' : 'ready',
      message: project.boreholes.length ? `${project.boreholes.length} 钻孔 / ${project.strata.length} 地层` : '等待导入地勘数据'
    },
    {
      key: 'geology', index: 3, title: '三维地质模型', subtitle: '生成 IDW 地层面，必要时导入 VTU 非结构网格。',
      required: ['已导入钻孔', '已生成地层面', '可提取代表性剖面'],
      status: hasGeologicalSurfacePreview(project)
        ? (project.geologicalModel?.coverageAudit?.designDomainCovered === false ? 'error' : (project.geologicalModel?.warnings?.length || project.geologicalModel?.coverageAudit?.autoExtended ? 'warning' : 'done'))
        : (project.boreholes.length ? 'ready' : 'blocked'),
      message: hasGeologicalSurfacePreview(project)
        ? `${effectiveGeologicalSurfaces(project).length} 个地层面；${project.geologicalModel?.coverageAudit?.message ?? '等待覆盖检查'}`
        : '需要先导入钻孔并生成模型'
    },
    {
      key: 'excavation', index: 4, title: '基坑轮廓', subtitle: '定义开挖轮廓、坑顶/坑底标高，并生成设计边段。',
      required: ['轮廓闭合', '坑底低于坑顶', '已生成边段'],
      status: project.excavation?.segments?.length ? (project.excavation?.warnings?.length ? 'warning' : 'done') : 'ready',
      message: project.excavation?.segments?.length ? `${project.excavation.segments.length} 边段，深度 ${project.excavation.depth}m` : '等待绘制或录入基坑轮廓'
    },
    {
      key: 'retaining', index: 5, title: '围护结构', subtitle: '自动生成地连墙、冠梁、腰梁、水平支撑和临时立柱。',
      required: ['已有基坑边段', '已生成地连墙', '已生成支撑体系'],
      status: project.retainingSystem?.diaphragmWalls?.length && project.retainingSystem?.supports?.length ? (project.retainingSystem?.warnings?.length ? 'warning' : 'done') : (project.excavation ? 'ready' : 'blocked'),
      message: project.retainingSystem ? `${project.retainingSystem.diaphragmWalls.length} 墙 / ${project.retainingSystem.supports.length} 支撑` : '需要自动设计围护体系'
    },
    {
      key: 'calculation', index: 6, title: '计算校核', subtitle: '建立施工工况，运行土压力、水压力、内力、配筋和规范子集筛查。',
      required: ['已生成施工工况', '已运行计算', '已输出校核结果'],
      status: latest ? (hasFail ? 'error' : hasWarnings || hasManualReview ? 'warning' : 'done') : (project.retainingSystem ? 'ready' : 'blocked'),
      message: latest ? `最新结果：${latest.governingValues.governingCheckStatus ?? 'manual_review'}` : '等待运行计算'
    },
    {
      key: 'assurance', index: 7, title: '闭环审查', subtitle: '检查功能完成度、工程校核状态和出图闸门。',
      required: ['Assurance API 可读取', '无硬性 fail', '闭环状态独立于功能完成度'],
      status: hasChecks ? (hasFail ? 'error' : 'done') : 'ready',
      message: hasChecks ? `校核项 ${latest?.checks?.length ?? 0} 项` : '等待读取完成度分析'
    },
    {
      key: 'export', index: 8, title: 'BIM 与计算书', subtitle: '导出 IFC、DOCX 计算书和完整 JSON 数据。',
      required: ['已有围护结构', '已有计算结果', '可导出 IFC/DOCX/JSON'],
      status: latest && project.retainingSystem ? (hasFail ? 'error' : 'ready') : 'blocked',
      message: latest && project.retainingSystem ? '可导出，正式使用仍需专业复核' : '需要先完成设计和计算'
    }
  ];
  return base;
}

function StepHeader({ step, project, compact = false }: { step: WorkflowStep; project: Project; compact?: boolean }) {
  return (
    <header className={`stepHeader ${step.status}`}>
      <div>
        <span className="stepEyebrow">Step {step.index}</span>
        <h2>{step.title}</h2>
        <p>{step.subtitle}</p>
      </div>
      <div className="stepStatusBox">
        <strong>{statusText(step.status)}</strong>
        <span>{step.message}</span>
      </div>
      {!compact ? <div className="requiredList">
        {requirementStatuses(step.key, project).map((item) => <span key={item.label} className={item.done ? 'reqDone' : 'reqOpen'}>{item.done ? '✓' : '○'} {item.label}</span>)}
      </div> : null}
      {project.messages?.length > 0 && <div className="small">项目消息：{project.messages.slice(-2).join('；')}</div>}
    </header>
  );
}

const standardsCache = new Map<string, StandardsProcessMatrix>();

function WorkflowStandardsRibbon({ projectId, revision, stepKey }: { projectId: string; revision: string; stepKey: WorkflowStepKey }) {
  const cacheKey = `${projectId}:${revision}`;
  const [matrix, setMatrix] = useState<StandardsProcessMatrix | undefined>(() => standardsCache.get(cacheKey));
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    const cached = standardsCache.get(cacheKey);
    if (cached) { setMatrix(cached); return () => { alive = false; }; }
    api.getProjectStandardsMatrix(projectId).then((value) => { if (alive) { standardsCache.set(cacheKey, value); setMatrix(value); } }).catch(() => undefined);
    return () => { alive = false; };
  }, [projectId, cacheKey]);
  const step = matrix?.steps.find((item: StandardsProcessStep) => item.workflowStep === stepKey);
  if (!step) return null;
  const mandatory = step.standardRefs.filter((item) => item.level === 'mandatory_all');
  const links = step.calculationLinks ?? [];
  return <section className={`workflowStandardsRibbon ${step.status}`}>
    <div className="workflowStandardLead"><span>本步骤计算—规范链</span><strong>{step.index}. {step.title}</strong><em>{mandatory.length ? `${mandatory.length} 项全文强制规范参与门禁` : '专业标准按具体计算绑定'}</em></div>
    <div className="workflowStandardsSummary"><span className={`matrixStatus ${step.status}`}>{step.status}</span><b>{links.length} 个计算节点</b><b>{step.ruleCount} 条已实现规则</b></div>
    <button className="secondary tiny" onClick={() => setOpen((value) => !value)}>{open ? '收起对应关系' : '展开逐项计算—规范对应'}</button>
    {open ? <div className="workflowCalculationMap">{links.map((link) => <article className={`calculationStandardCard ${link.status}`} key={`${step.workflowStep}-${link.sequence}`}>
      <header><span>{step.index}.{link.sequence}</span><div><h4>{link.calculation}</h4><p>{link.method}</p></div><em className={`matrixStatus ${link.status}`}>{link.status}</em></header>
      <div className="calculationStandardRefs"><strong>直接适用规范</strong><div>{link.standardRefs.length ? link.standardRefs.map((std) => std.sourceUrl ? <a href={std.sourceUrl} target="_blank" rel="noreferrer" className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></a> : <span className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></span>) : <span className="qualityEvidenceBadge">软件数值质量门禁</span>}</div></div>
      <dl><dt>条文关注</dt><dd>{link.clauseFocus}</dd><dt>输出证据</dt><dd>{link.output}</dd></dl>
      <details className="ruleTraceDetails"><summary>规则与条文证据（{link.ruleCount}）</summary>{link.rules.length ? link.rules.slice(0, 12).map((rule) => <span className="ruleTrace" key={String(rule.ruleId)}><b>{String(rule.ruleId)}</b><em>{String(rule.clauseReference ?? '条文适用条件需项目复核')}</em></span>) : <p>该节点当前采用方法说明或人工复核门禁，尚无可自动执行的条文规则。</p>}</details>
    </article>)}</div> : <div className="workflowStandardsPreview">{links.slice(0, 4).map((link) => <span key={link.calculation}><b>{link.sequence}</b>{link.calculation}<em>{link.standardRefs.map((std) => std.code).join(' / ') || '数值质量'}</em></span>)}</div>}
    <a className="workflowDocsLink" href="/docs" target="_blank" rel="noreferrer">打开完整计算原理与规范追溯文档</a>
  </section>;
}

function StepBody({
  active,
  project,
  onRefresh,
  runStep,
  runWorkflow,
  runTask,
  importVtu,
  vtuMessage,
  selectedLocator,
  onLocateIssue,
  onJump,
  viewMode
}: {
  active: WorkflowStepKey;
  project: Project;
  onRefresh: (project?: Project) => void | Promise<void>;
  runStep: (label: string, step: () => Promise<unknown>) => Promise<void>;
  runWorkflow: (title: string, actions: WorkflowAction[]) => Promise<void>;
  runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>;
  importVtu: (file?: File) => Promise<void>;
  vtuMessage?: string;
  selectedLocator?: Record<string, unknown>;
  onLocateIssue: (issue: IssueCenterItem) => void;
  onJump: (key: WorkflowStepKey) => void;
  viewMode: 'compact' | 'professional';
}) {
  if (active === 'settings') return <SettingsStep project={project} onChanged={onRefresh} viewMode={viewMode} />;
  if (active === 'boreholes') return <BoreholeImport project={project} onImported={onRefresh} />;
  if (active === 'geology') return <GeologyStep project={project} runStep={runStep} importVtu={importVtu} vtuMessage={vtuMessage} />;
  if (active === 'excavation') return <ExcavationEditor project={project} onSaved={onRefresh} />;
  if (active === 'retaining') return <RetainingStep project={project} runStep={runStep} runTask={runTask} onRefresh={onRefresh} selectedLocator={selectedLocator} viewMode={viewMode} />;
  if (active === 'calculation') return <CalculationStep project={project} runStep={runStep} runWorkflow={runWorkflow} runTask={runTask} onRefresh={onRefresh} selectedLocator={selectedLocator} viewMode={viewMode} onJump={onJump} />;
  if (active === 'assurance') return <AssurancePanel project={project} onLocateIssue={onLocateIssue} onChanged={onRefresh} runTask={runTask} viewMode={viewMode} />;
  return <ExportPanel project={project} runTask={runTask} selectedLocator={selectedLocator} onRefresh={onRefresh} viewMode={viewMode} />;
}

function SettingsStep({ project, onChanged, viewMode }: { project: Project; onChanged: (project?: Project) => void | Promise<void>; viewMode: 'compact' | 'professional' }) {
  const [draft, setDraft] = useState(() => ({ ...project.designSettings }));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  useEffect(() => setDraft({ ...project.designSettings }), [project.id, project.updatedAt]);

  function numberValue(key: keyof typeof draft, value: string) {
    const parsed = Number(value);
    setDraft((current) => ({ ...current, [key]: Number.isFinite(parsed) ? parsed : 0 }));
  }

  async function save() {
    setSaving(true); setError(undefined); setMessage(undefined);
    try {
      const updated = await api.updateProject(project.id, { designSettings: draft } as Partial<Project>);
      await onChanged(updated);
      setMessage('设计控制参数已保存。涉及计算模型的参数变更后，请重新计算并重新完成审签。');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setSaving(false); }
  }

  return (
    <div className="settingsPage">
      {message ? <div className="rebarGateMessage pass" role="status">{message}</div> : null}
      {error ? <div className="error" role="alert">{error}</div> : null}
      <div className="stepGrid">
        <div className="summaryPanel">
          <h3>项目基础参数</h3>
          <dl className="definitionGrid">
            <dt>项目名称</dt><dd>{project.name}</dd>
            <dt>地点</dt><dd>{project.location ?? '-'}</dd>
            <dt>长度单位</dt><dd>{project.unitSystem.length}</dd>
            <dt>力单位</dt><dd>{project.unitSystem.force}</dd>
            <dt>应力单位</dt><dd>{project.unitSystem.stress}</dd>
            <dt>规则集</dt><dd>{draft.ruleSet}</dd>
          </dl>
        </div>
        <div className="summaryPanel">
          <h3>基本设计控制</h3>
          <div className="settingsFormGrid">
            <label>安全等级<input value={draft.safetyGrade} onChange={(e) => setDraft((v) => ({ ...v, safetyGrade: e.target.value }))} /></label>
            <label>环境等级<select value={draft.environmentGrade} onChange={(e) => setDraft((v) => ({ ...v, environmentGrade: e.target.value }))}><option>一般</option><option>严寒</option><option>腐蚀</option><option>地下水侵蚀</option></select></label>
            <label>地下水位（m）<input type="number" step="0.1" value={draft.groundwaterLevel} onChange={(e) => numberValue('groundwaterLevel', e.target.value)} /></label>
            <label>坑内水位（m）<input type="number" step="0.1" value={draft.groundwaterLevelInside ?? ''} onChange={(e) => setDraft((v) => ({ ...v, groundwaterLevelInside: e.target.value === '' ? undefined : Number(e.target.value) }))} /></label>
            <label>地面超载（kPa）<input type="number" step="1" min="0" value={draft.surcharge} onChange={(e) => numberValue('surcharge', e.target.value)} /></label>
            <label>位移限值比 1/n<input type="number" min="100" step="10" value={draft.displacementLimitRatio ?? 500} onChange={(e) => numberValue('displacementLimitRatio', e.target.value)} /></label>
          </div>
        </div>
      </div>
      <details className="focusDetails settingsAdvancedDetails" open={viewMode === 'professional'}><summary>高级设计控制：支撑拓扑、换撑刚度、长期效应与监测</summary>
      <section className="summaryPanel">
        <div className="panelTitleRow"><div><h3>支撑拓扑、墙体净距与换撑刚度</h3><p className="small">这些参数控制支撑轴线内偏移、超长对撑替代、斜撑候选和楼板换撑 EA/L 装配。</p></div><button onClick={() => void save()} disabled={saving}>{saving ? '保存中…' : '保存并使旧结果失效'}</button></div>
        <div className="settingsFormGrid advancedSettingsGrid">
          <label>支撑轴线距墙最小净距（m）<input type="number" min="0.35" max="3" step="0.1" value={draft.supportWallClearanceM ?? 1.0} onChange={(e) => numberValue('supportWallClearanceM', e.target.value)} /></label>
          <label>直对撑建议最大跨度（m）<input type="number" min="12" max="45" step="1" value={draft.maxDirectStrutSpanM ?? 24} onChange={(e) => numberValue('maxDirectStrutSpanM', e.target.value)} /></label>
          <label>长墙角撑优先阈值（m）<input type="number" min="8" max="40" step="1" value={draft.diagonalBraceMinWallLengthM ?? 18} onChange={(e) => numberValue('diagonalBraceMinWallLengthM', e.target.value)} /></label>
          <label>每个转角平行角撑数<input type="number" min="1" max="6" step="1" value={draft.cornerDiagonalFamilyCount ?? 4} onChange={(e) => numberValue('cornerDiagonalFamilyCount', e.target.value)} /></label>
          <label>平行角撑墙节点间距（m）<input type="number" min="2.5" max="6" step="0.25" value={draft.cornerDiagonalFamilySpacingM ?? 3} onChange={(e) => numberValue('cornerDiagonalFamilySpacingM', e.target.value)} /></label>
          <label>角撑平行角度容差（°）<input type="number" min="2" max="12" step="1" value={draft.cornerDiagonalParallelToleranceDeg ?? 5} onChange={(e) => numberValue('cornerDiagonalParallelToleranceDeg', e.target.value)} /></label>
          <label>支撑目标利用率<input type="number" min="0.4" max="1" step="0.05" value={draft.supportTargetUtilization ?? 0.85} onChange={(e) => numberValue('supportTargetUtilization', e.target.value)} /></label>
          <label>长细比筛查限值<input type="number" min="40" max="300" step="5" value={draft.supportScreeningSlendernessLimit ?? 150} onChange={(e) => numberValue('supportScreeningSlendernessLimit', e.target.value)} /></label>
          <label>预加轴力比例<input type="number" min="0" max="0.6" step="0.05" value={draft.supportPreloadRatio ?? 0.2} onChange={(e) => numberValue('supportPreloadRatio', e.target.value)} /></label>
          <label>温度约束系数<input type="number" min="0" max="1" step="0.05" value={draft.supportThermalRestraintFactor ?? 0.15} onChange={(e) => numberValue('supportThermalRestraintFactor', e.target.value)} /></label>
          <label>节点安装间隙（mm）<input type="number" min="0" max="30" step="1" value={draft.supportJointGapMm ?? 3} onChange={(e) => numberValue('supportJointGapMm', e.target.value)} /></label>
          <label>安装偏差（mm）<input type="number" min="0" max="100" step="5" value={draft.supportInstallationDeviationMm ?? 20} onChange={(e) => numberValue('supportInstallationDeviationMm', e.target.value)} /></label>
          <label className="settingCheck"><input type="checkbox" checked={draft.supportDeepDesignRequiredForCandidate ?? true} onChange={(e) => setDraft((v) => ({ ...v, supportDeepDesignRequiredForCandidate: e.target.checked }))} /><span>候选方案必须通过支撑稳定与施工效应深化筛查</span></label>
          <label className="settingCheck"><input type="checkbox" checked={draft.preferDiagonalBraces ?? true} onChange={(e) => setDraft((v) => ({ ...v, preferDiagonalBraces: e.target.checked }))} /><span>端部转角采用独立墙节点的平行角撑族，禁止V形扇撑</span></label>
          <label>换撑楼板有效宽度（m）<input type="number" min="0.5" max="30" step="0.5" value={draft.replacementSlabEffectiveWidthM ?? 6} onChange={(e) => numberValue('replacementSlabEffectiveWidthM', e.target.value)} /></label>
          <label>换撑楼板厚度（m）<input type="number" min="0.1" max="2" step="0.05" value={draft.replacementSlabThicknessM ?? 0.25} onChange={(e) => numberValue('replacementSlabThicknessM', e.target.value)} /></label>
          <label>换撑弹性模量（MPa）<input type="number" min="1000" max="60000" step="500" value={draft.replacementSlabElasticModulusMpa ?? 30000} onChange={(e) => numberValue('replacementSlabElasticModulusMpa', e.target.value)} /></label>
          <label>换撑连接折减系数<input type="number" min="0.05" max="1" step="0.05" value={draft.replacementConnectionReduction ?? 0.65} onChange={(e) => numberValue('replacementConnectionReduction', e.target.value)} /></label>
          <label>默认工作台模式<select value={draft.defaultWorkspaceMode ?? 'compact'} onChange={(e) => setDraft((v) => ({ ...v, defaultWorkspaceMode: e.target.value as 'compact' | 'professional' }))}><option value="compact">精简模式</option><option value="professional">专业模式</option></select></label>
        </div>
        <p className="small boundaryNote">未进入换撑阶段，刚度显示“未激活/—”；进入换撑阶段后若参数缺失，系统将产生硬阻断，不再用 0 代表未知状态。</p>
      </section>
      <section className="summaryPanel">
        <div className="panelTitleRow"><div><h3>工业计算质量门禁</h3><p className="small">控制输入冻结、数值质量和独立计算差异。阈值属于软件质量控制参数，应由项目计算负责人批准。</p></div><button onClick={() => void save()} disabled={saving}>{saving ? '保存中…' : '保存并使旧结果失效'}</button></div>
        <div className="settingsFormGrid advancedSettingsGrid">
          <label>计算质量等级<select value={draft.calculationAssuranceLevel ?? 'engineering'} onChange={(e) => setDraft((v) => ({ ...v, calculationAssuranceLevel: e.target.value as 'screening' | 'engineering' | 'official_issue' }))}><option value="screening">方案筛查</option><option value="engineering">工程设计</option><option value="official_issue">正式发行</option></select></label>
          <label className="settingCheck"><input type="checkbox" checked={draft.requireIndependentCalculationCheck ?? true} onChange={(e) => setDraft((v) => ({ ...v, requireIndependentCalculationCheck: e.target.checked }))} /><span>要求独立计算路径对账</span></label>
          <label>矩阵条件数复核阈值<input type="number" min="1000000" step="1000000000" value={draft.maximumMatrixConditionNumber ?? 1e12} onChange={(e) => numberValue('maximumMatrixConditionNumber', e.target.value)} /></label>
          <label>平衡相对残差阈值<input type="number" min="1e-14" max="0.01" step="1e-9" value={draft.maximumEquilibriumRelativeResidual ?? 1e-8} onChange={(e) => numberValue('maximumEquilibriumRelativeResidual', e.target.value)} /></label>
          <label>独立计算预警差异比<input type="number" min="0" max="1" step="0.05" value={draft.independentCheckWarningRatio ?? 0.25} onChange={(e) => numberValue('independentCheckWarningRatio', e.target.value)} /></label>
          <label>独立计算强制复核差异比<input type="number" min="0" max="2" step="0.05" value={draft.independentCheckFailRatio ?? 0.5} onChange={(e) => numberValue('independentCheckFailRatio', e.target.value)} /></label>
        </div>
        <p className="small boundaryNote">工程设计等级下，独立解差异超限进入人工复核；正式发行等级可升级为硬阻断。数值阈值用于软件求解质量控制，不替代结构规范限值。</p>
      </section>
      <section className="summaryPanel">
        <div className="panelTitleRow"><div><h3>长期效应、抗裂与监测控制</h3><p className="small">这些参数参与准永久组合、长期位移、裂缝筛查和正式图纸发行闸门。</p></div><button onClick={() => void save()} disabled={saving}>{saving ? '保存中…' : '保存设计参数'}</button></div>
        <div className="settingsFormGrid advancedSettingsGrid">
          <label>设计使用年限（年）<input type="number" min="1" max="200" value={draft.serviceLifeYears ?? 50} onChange={(e) => numberValue('serviceLifeYears', e.target.value)} /></label>
          <label>相对湿度<input type="number" min="0.2" max="1" step="0.05" value={draft.relativeHumidity ?? 0.75} onChange={(e) => numberValue('relativeHumidity', e.target.value)} /><span>0–1</span></label>
          <label>持续荷载比例<input type="number" min="0.1" max="1" step="0.05" value={draft.sustainedLoadRatio ?? 0.65} onChange={(e) => numberValue('sustainedLoadRatio', e.target.value)} /><span>0–1</span></label>
          <label>徐变系数 φ<input type="number" min="0" max="5" step="0.1" value={draft.creepCoefficient ?? 1.6} onChange={(e) => numberValue('creepCoefficient', e.target.value)} /></label>
          <label>收缩应变<input type="number" min="0" max="0.002" step="0.00001" value={draft.shrinkageStrain ?? 0.00025} onChange={(e) => numberValue('shrinkageStrain', e.target.value)} /></label>
          <label>温度变化范围（°C）<input type="number" min="0" max="80" step="1" value={draft.temperatureRangeC ?? 20} onChange={(e) => numberValue('temperatureRangeC', e.target.value)} /></label>
          <label>抗震/临时结构等级<select value={draft.seismicGrade ?? 'non_seismic_temporary'} onChange={(e) => setDraft((v) => ({ ...v, seismicGrade: e.target.value }))}><option value="non_seismic_temporary">临时结构常规工况</option><option value="seismic_grade_3">三级抗震构造</option><option value="seismic_grade_2">二级抗震构造</option><option value="special_review">专项抗震复核</option></select></label>
          <label className="settingCheck"><input type="checkbox" checked={draft.monitoringCalibrationEnabled ?? true} onChange={(e) => setDraft((v) => ({ ...v, monitoringCalibrationEnabled: e.target.checked }))} /><span>启用监测反演与参数校准</span></label>
          <label>监测阈值来源<select value={draft.monitoringThresholdSource ?? 'auto_screening'} onChange={(e) => setDraft((v) => ({ ...v, monitoringThresholdSource: e.target.value as 'auto_screening' | 'project_defined' }))}><option value="auto_screening">系统筛查阈值</option><option value="project_defined">项目监测方案阈值</option></select></label>
          <label>趋势外推时长（h）<input type="number" min="1" max="168" step="1" value={draft.monitoringProjectionHours ?? 24} onChange={(e) => numberValue('monitoringProjectionHours', e.target.value)} /></label>
          <label>墙位移预警值（mm）<input type="number" min="0" step="1" disabled={(draft.monitoringThresholdSource ?? 'auto_screening') !== 'project_defined'} value={draft.monitoringWallDisplacementWarningMm ?? ''} onChange={(e) => setDraft((v) => ({ ...v, monitoringWallDisplacementWarningMm: e.target.value === '' ? undefined : Number(e.target.value) }))} /></label>
          <label>墙位移报警值（mm）<input type="number" min="0" step="1" disabled={(draft.monitoringThresholdSource ?? 'auto_screening') !== 'project_defined'} value={draft.monitoringWallDisplacementAlarmMm ?? ''} onChange={(e) => setDraft((v) => ({ ...v, monitoringWallDisplacementAlarmMm: e.target.value === '' ? undefined : Number(e.target.value) }))} /></label>
          <label>沉降预警值（mm）<input type="number" min="0" step="1" disabled={(draft.monitoringThresholdSource ?? 'auto_screening') !== 'project_defined'} value={draft.monitoringSettlementWarningMm ?? ''} onChange={(e) => setDraft((v) => ({ ...v, monitoringSettlementWarningMm: e.target.value === '' ? undefined : Number(e.target.value) }))} /></label>
          <label>沉降报警值（mm）<input type="number" min="0" step="1" disabled={(draft.monitoringThresholdSource ?? 'auto_screening') !== 'project_defined'} value={draft.monitoringSettlementAlarmMm ?? ''} onChange={(e) => setDraft((v) => ({ ...v, monitoringSettlementAlarmMm: e.target.value === '' ? undefined : Number(e.target.value) }))} /></label>
          <label>支撑轴力预警比<input type="number" min="0" max="2" step="0.05" value={draft.monitoringSupportForceWarningRatio ?? 0.85} onChange={(e) => numberValue('monitoringSupportForceWarningRatio', e.target.value)} /></label>
          <label>支撑轴力报警比<input type="number" min="0" max="2" step="0.05" value={draft.monitoringSupportForceAlarmRatio ?? 1.0} onChange={(e) => numberValue('monitoringSupportForceAlarmRatio', e.target.value)} /></label>
          <label>水位预警偏移（m）<input type="number" min="0" step="0.1" value={draft.monitoringGroundwaterWarningOffsetM ?? 0.5} onChange={(e) => numberValue('monitoringGroundwaterWarningOffsetM', e.target.value)} /></label>
          <label>水位报警偏移（m）<input type="number" min="0" step="0.1" value={draft.monitoringGroundwaterAlarmOffsetM ?? 1.0} onChange={(e) => numberValue('monitoringGroundwaterAlarmOffsetM', e.target.value)} /></label>
          <label className="settingCheck"><input type="checkbox" checked={draft.requireFormalApprovalForConstruction ?? false} onChange={(e) => setDraft((v) => ({ ...v, requireFormalApprovalForConstruction: e.target.checked }))} /><span>施工图包必须完成四级批准</span></label>
        </div>
        <p className="small boundaryNote">裂缝与长期效应当前属于透明工程筛查；正式施工图仍应结合项目实际龄期、暴露环境、温度场、施工缝和监测方案完成专业复核。</p>
      </section>
      </details>
    </div>
  );
}

function GeologyStep({ project, runStep, importVtu, vtuMessage }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void>; importVtu: (file?: File) => Promise<void>; vtuMessage?: string }) {
  return (
    <div>
      <div className="actionStrip">
        <button onClick={() => runStep('正在生成 IDW 地质模型', () => api.buildGeology(project.id))} disabled={!project.boreholes.length}>生成 IDW 地质模型</button>
        <label className="fileButton">导入 VTU 网格<input type="file" accept=".vtu" onChange={(event) => importVtu(event.target.files?.[0])} /></label>
        {vtuMessage && <span className="small">{vtuMessage}</span>}
      </div>
      <GeologyViewer project={project} />
    </div>
  );
}

function RetainingStep({ project, runStep, runTask, onRefresh, selectedLocator, viewMode }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void>; runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; onRefresh: () => void | Promise<void>; selectedLocator?: Record<string, unknown>; viewMode: 'compact' | 'professional' }) {
  const [open, setOpen] = useState(false);
  const [shapeDiagnostics, setShapeDiagnostics] = useState<Record<string, any> | null>(null);
  const [designerAudit, setDesignerAudit] = useState<Record<string, any> | null>(null);
  const [deepDesign, setDeepDesign] = useState<Record<string, any> | null>(null);
  const [resourceEstimate, setResourceEstimate] = useState<Record<string, any> | null>(null);
  const [designBootstrap, setDesignBootstrap] = useState<Record<string, any> | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string>();
  const [bootstrapRefreshToken, setBootstrapRefreshToken] = useState(0);
  const [advancedAnalysisLoading, setAdvancedAnalysisLoading] = useState(false);
  const [advancedAnalysisAttempted, setAdvancedAnalysisAttempted] = useState(false);
  const [weightPreset, setWeightPreset] = useState<'balanced' | 'clean_support_layout' | 'fewer_columns' | 'low_axial_force' | 'muck_path_priority'>('clean_support_layout');
  const defaultWeights: Record<string, number> = {
    spacingDeviation: 20,
    spanLength: 16,
    obstacleConflict: 34,
    supportCrossing: 80,
    junctionComplexity: 64,
    columnCount: 7,
    muckPathContinuity: 8,
    axialPeakProxy: 11,
    symmetry: 10,
    endpointValidity: 18,
    replacementContinuity: 8,
    memberUtilization: 30,
    bucklingRisk: 26,
    constructionEffects: 14,
    materialVolume: 8,
    nodeReadiness: 16,
    loadPathRedundancy: 12,
    forceUniformity: 14
  };
  const objectiveMeta = [
    ['spacingDeviation', '间距偏差'], ['spanLength', '跨长'], ['obstacleConflict', '障碍冲突'], ['supportCrossing', '非法穿越'], ['junctionComplexity', '汇交节点复杂度'], ['columnCount', '立柱数量'], ['muckPathContinuity', '出土通道'], ['axialPeakProxy', '轴力峰值'], ['symmetry', '平面对称'], ['endpointValidity', '端点有效'], ['replacementContinuity', '换撑连续'], ['memberUtilization', '构件利用率'], ['bucklingRisk', '稳定风险'], ['constructionEffects', '施工效应'], ['materialVolume', '材料量'], ['nodeReadiness', '节点完整性'], ['loadPathRedundancy', '传力冗余'], ['forceUniformity', '轴力均衡']
  ] as const;
  const presetWeights: Record<string, Record<string, number>> = {
    balanced: {},
    clean_support_layout: { supportCrossing: 80, junctionComplexity: 80, symmetry: 18, spanLength: 18, memberUtilization: 30, bucklingRisk: 28 },
    fewer_columns: { columnCount: 28, spanLength: 18, bucklingRisk: 30 },
    low_axial_force: { axialPeakProxy: 32, spanLength: 25, spacingDeviation: 20, memberUtilization: 42, constructionEffects: 24 },
    muck_path_priority: { muckPathContinuity: 34, obstacleConflict: 48, supportCrossing: 80, junctionComplexity: 64 }
  };
  const presetToWeights = (preset: keyof typeof presetWeights) => ({ ...defaultWeights, ...presetWeights[preset] });
  const [weights, setWeights] = useState<Record<string, number>>(presetToWeights('clean_support_layout'));
  const [lockedIds, setLockedIds] = useState<string[]>(project.retainingSystem?.supports?.filter((s) => s.optimizationLocked).map((s) => s.id) ?? []);
  const [lockedStartIds, setLockedStartIds] = useState<string[]>(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedStart).map((s) => s.id) ?? []);
  const [lockedEndIds, setLockedEndIds] = useState<string[]>(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedEnd).map((s) => s.id) ?? []);
  const [lockedLevels, setLockedLevels] = useState<number[]>([]);
  const [lockedObstacleIds, setLockedObstacleIds] = useState<string[]>(project.excavation?.obstacles?.filter((o) => o.optimizationLocked && o.id).map((o) => o.id!) ?? []);
  useEffect(() => {
    setAdvancedAnalysisAttempted(false);
    setDesignerAudit(null);
    setDeepDesign(null);
    setResourceEstimate(null);
    setLockedIds(project.retainingSystem?.supports?.filter((s) => s.optimizationLocked).map((s) => s.id) ?? []);
    setLockedStartIds(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedStart).map((s) => s.id) ?? []);
    setLockedEndIds(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedEnd).map((s) => s.id) ?? []);
    const savedLevels = (project.retainingSystem?.optimizationLocks ?? []).filter((item) => String(item.targetType ?? item.target_type ?? '') === 'support_level' && item.locked !== false).map((item) => Number(item.levelIndex ?? item.level_index)).filter(Number.isFinite);
    setLockedLevels(Array.from(new Set(savedLevels)));
    setLockedObstacleIds(project.excavation?.obstacles?.filter((o) => o.optimizationLocked && o.id).map((o) => o.id!) ?? []);
  }, [project.retainingSystem, project.excavation]);
  useEffect(() => {
    let active = true;
    if (!project.excavation) {
      setDesignBootstrap(null);
      setShapeDiagnostics(null);
      setBootstrapError(undefined);
      return () => { active = false; };
    }
    setBootstrapError(undefined);
    api.getDesignWorkspaceBootstrap(project.id, bootstrapRefreshToken > 0)
      .then((value) => {
        if (!active) return;
        setDesignBootstrap(value);
        setShapeDiagnostics((value.shapeDiagnostics ?? null) as Record<string, any> | null);
      })
      .catch((error) => {
        if (!active) return;
        setBootstrapError(error instanceof Error ? error.message : String(error));
      });
    return () => { active = false; };
  }, [project.id, project.updatedAt, project.excavation?.id, project.excavation?.outline?.points?.length, bootstrapRefreshToken]);

  useEffect(() => {
    if (!open || !project.excavation || advancedAnalysisLoading || advancedAnalysisAttempted) return;
    let active = true;
    setAdvancedAnalysisAttempted(true);
    setAdvancedAnalysisLoading(true);
    Promise.allSettled([
      api.getSupportDesignerAudit(project.id),
      api.getSupportDeepDesign(project.id, false),
      api.getCalculationResourceEstimate(project.id, 0),
    ]).then(([auditResult, deepResult, resourceResult]) => {
      if (!active) return;
      setDesignerAudit(auditResult.status === 'fulfilled' ? auditResult.value : null);
      setDeepDesign(deepResult.status === 'fulfilled' ? deepResult.value : null);
      setResourceEstimate(resourceResult.status === 'fulfilled' ? resourceResult.value : null);
    }).finally(() => { if (active) setAdvancedAnalysisLoading(false); });
    return () => { active = false; };
  }, [open, project.id, project.excavation?.id, advancedAnalysisLoading, advancedAnalysisAttempted]);
  const runAuto = () => runTask(
    '正在由独立进程识别形状并生成围护支撑体系',
    'support_layout_optimization',
    { preset: 'clean_support_layout', objectiveWeights: weights },
  );
  const applyPreset = (value: typeof weightPreset) => {
    setWeightPreset(value);
    setWeights(presetToWeights(value));
  };
  const toggle = (setter: Dispatch<SetStateAction<string[]>>, id: string) => setter((prev) => prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]);
  const toggleLevel = (level: number) => setLockedLevels((prev) => prev.includes(level) ? prev.filter((item) => item !== level) : [...prev, level]);
  const levels = Array.from(new Set((project.retainingSystem?.supports ?? []).map((s) => s.levelIndex))).sort((a, b) => a - b);
  const candidates = project.retainingSystem?.supportLayoutRepair?.candidates ?? [];
  const layoutSummary = project.retainingSystem?.layoutSummary ?? {};
  const designNotes = Array.isArray(layoutSummary.designNotes)
    ? layoutSummary.designNotes.map((item) => String(item)).filter(Boolean)
    : [];
  const unresolvedWarnings = project.retainingSystem?.warnings ?? [];
  const activeShape = (layoutSummary.planShapeDiagnostics as Record<string, any> | undefined) ?? shapeDiagnostics;
  const shapeScheme = (activeShape?.engineeringScheme ?? {}) as Record<string, any>;
  const shapeCapability = String(activeShape?.capability ?? 'unknown');
  const shapeStatusClass = shapeCapability.startsWith('automatic') ? 'pass' : shapeCapability.startsWith('zoned') ? 'warning' : 'error';
  const previewRows = candidates.map((c) => {
    const terms = c.objectiveTerms ?? {};
    const penalty = Object.entries(weights).reduce((sum, [key, value]) => sum + value * Math.min(Number(terms[key] ?? 0), 3), 0)
      + (c.failCount ?? 0) * 35 + (c.warningCount ?? 0) * 1.5 + (c.hardConstraints?.passed ? 0 : 65);
    return { id: c.id ?? String(c.rank), rank: c.rank, oldScore: c.score, penalty, previewScore: Math.max(0, Math.min(100, 100 - penalty)), supportCount: c.supportCount, columnCount: c.columnCount };
  }).sort((a, b) => b.previewScore - a.previewScore).slice(0, 5);
  return (
    <div>
      {designBootstrap ? <ProgressiveDesignPanel project={project} runTask={runTask} onRefresh={onRefresh} initialSession={designBootstrap.progressive as ProgressiveDesignSession} /> : <section className={`progressiveDesignPanel ${bootstrapError ? 'errorPanel' : 'loadingPanel'}`}><strong>{bootstrapError ? '围护设计工作区读取失败' : '正在一次性装配设计资格、形状识别和渐进式配置…'}</strong>{bootstrapError ? <><p>{bootstrapError}</p><button className="secondary" onClick={() => setBootstrapRefreshToken((value) => value + 1)}>重新装配工作区</button></> : <p>核心数据先返回，构件深化、审计和资源估算将在展开高级面板后按需加载。</p>}</section>}
      <div className="actionStrip simplifiedActions progressiveQuickActions">
        <button className="secondary" title="识别形状并生成围护体系" onClick={runAuto} disabled={!project.excavation}>按默认配置快速生成</button>
        <button className="secondary" onClick={() => setOpen(true)}>高级操作与人工锁定</button>
        <span className="small">快捷生成会采用当前渐进式配置的默认值；正式设计建议逐步确认坐标、施工组织、围护体系、支撑体系和计算范围。</span>
      </div>
      {designBootstrap?.qualification ? <DesignQualificationPanel project={project} runTask={runTask} initialData={designBootstrap.qualification as Qualification} /> : null}
      {advancedAnalysisLoading ? <div className="infoBanner">正在按需读取布设审计、构件深化与计算资源估算；该过程不会阻塞基础设计界面。</div> : null}
      {activeShape && <section className="shapeStrategyPanel">
        <div className="shapeStrategyHeader">
          <div><h3>平面形状识别与支撑体系决策</h3><p className="small">识别结果用于候选生成和计算门禁；异形交汇区未形成明确转接体系时只允许方案设计。</p></div>
          <span className={`statusTag ${shapeStatusClass}`}>{shapeCapability}</span>
        </div>
        <div className="shapeMetricGrid">
          <div><strong>{String(activeShape.archetype ?? activeShape.classification)}</strong><span>识别形状</span></div>
          <div><strong>{Number(activeShape.longSpanM ?? 0).toFixed(1)} / {Number(activeShape.shortSpanM ?? 0).toFixed(1)} m</strong><span>局部长跨 / 短跨</span></div>
          <div><strong>{Number(activeShape.aspectRatio ?? 0).toFixed(2)}</strong><span>长宽比</span></div>
          <div><strong>{Number(activeShape.concaveVertexCount ?? 0)}</strong><span>凹角数量</span></div>
          <div><strong>{Number(activeShape.zoneCount ?? 0)}</strong><span>设计分区</span></div>
          <div><strong>{(Number(activeShape.recognitionConfidence ?? 0) * 100).toFixed(0)}%</strong><span>识别置信度</span></div>
          <div><strong>{String(activeShape.primarySystem ?? activeShape.recommendedTopology ?? '-')}</strong><span>推荐体系</span></div>
        </div>
        <div className="shapeSchemeSummary">
          <div><strong>{String(shapeScheme.name ?? '可见墙对支撑/环撑比选')}</strong><p>{String(shapeScheme.zoning ?? '按局部主轴、凸分解和施工分区确定支撑体系。')}</p></div>
          <div><strong>可生成拓扑</strong><p>{Array.isArray(activeShape.supportedTopologyFamilies) ? activeShape.supportedTopologyFamilies.join('、') : '-'}</p></div>
        </div>
        <details open={viewMode === 'professional'}><summary>查看布置规则、禁用形式和计算模型</summary>
          <div className="shapeRuleColumns">
            <div><h4>布置规则</h4><ul>{(Array.isArray(shapeScheme.layoutRules) ? shapeScheme.layoutRules : []).map((item: unknown, index: number) => <li key={`shape-rule-${index}`}>{String(item)}</li>)}</ul></div>
            <div><h4>禁止形式</h4><ul>{(Array.isArray(shapeScheme.forbidden) ? shapeScheme.forbidden : []).map((item: unknown, index: number) => <li key={`shape-forbidden-${index}`}>{String(item)}</li>)}</ul></div>
            <div><h4>计算与施工</h4><p>{String(shapeScheme.calculationModel ?? '-')}</p><p>{String(shapeScheme.construction ?? '-')}</p></div>
          </div>
        </details>
      </section>}
      {designerAudit && <section className="summaryPanel schemeDesignerAuditPanel">
        <div className="panelTitleRow">
          <div>
            <h3>布设方案设计器完整性审计</h3>
            <p className="small">审计范围包含形状识别、体系兼容、传力闭合、候选差异、施工约束、计算资源和交付门禁。</p>
          </div>
          <span className={`statusTag ${designerAudit.status === 'pass' ? 'pass' : designerAudit.status === 'warning' ? 'warning' : 'error'}`}>
            {designerAudit.status} · {Number(designerAudit.score ?? 0).toFixed(0)}分
          </span>
        </div>
        <div className="shapeMetricGrid">
          <div><strong>{Number(designerAudit.candidateDiversity?.count ?? 0)}</strong><span>候选数量</span></div>
          <div><strong>{Number(designerAudit.candidateDiversity?.topologyFamilyCount ?? 0)}</strong><span>拓扑族数量</span></div>
          <div><strong>{Number(designerAudit.candidateDiversity?.geometryFingerprintCount ?? 0)}</strong><span>实质几何方案</span></div>
          <div><strong>{Number(resourceEstimate?.estimatedPeakMemoryMb ?? designerAudit.resourceEstimate?.estimatedPeakMemoryMb ?? 0).toFixed(0)} MB</strong><span>估算峰值内存</span></div>
          <div><strong>{String(resourceEstimate?.status ?? designerAudit.resourceEstimate?.status ?? '-')}</strong><span>计算资源等级</span></div>
          <div><strong>{designerAudit.blockingItems?.length ?? 0} / {designerAudit.warningItems?.length ?? 0}</strong><span>阻断 / 警告</span></div>
        </div>
        <div className="schemeAuditSections" aria-label="设计器审计分项">
          {(Array.isArray(designerAudit.sections) ? designerAudit.sections : []).map((section: any) => (
            <article key={String(section.id)} className={`schemeAuditSection ${String(section.status ?? 'warning')}`}>
              <span>{String(section.name ?? section.id)}</span>
              <strong>{String(section.status ?? 'warning')}</strong>
            </article>
          ))}
        </div>
        {(designerAudit.blockingItems?.length ?? 0) > 0 && <div className="unresolvedWarningList">
          <h4>必须闭环</h4>
          <ol>{designerAudit.blockingItems.map((item: unknown, index: number) => <li key={`designer-block-${index}`}>{String(item)}</li>)}</ol>
        </div>}
        {(designerAudit.warningItems?.length ?? 0) > 0 && <details open={viewMode === 'professional'}>
          <summary>查看设计器警告与改进建议（{designerAudit.warningItems.length}）</summary>
          <ol>{designerAudit.warningItems.map((item: unknown, index: number) => <li key={`designer-warning-${index}`}>{String(item)}</li>)}</ol>
        </details>}
        <div className="buttonRow">
          <button className="secondary" onClick={() => {
            Promise.all([api.getSupportDesignerAudit(project.id), api.getSupportDeepDesign(project.id, false), api.getCalculationResourceEstimate(project.id, 0)])
              .then(([audit, deep, resource]) => { setDesignerAudit(audit); setDeepDesign(deep); setResourceEstimate(resource); })
              .catch(() => undefined);
          }}>重新审计</button>
          <span className="small">资源等级为 high/blocked 时，系统强制逐方案计算或阻断任务，避免计算拖垮API与登录服务。</span>
        </div>
      </section>}
      {deepDesign && <section className="summaryPanel supportDeepDesignPanel">
        <div className="panelTitleRow">
          <div><h3>水平支撑深化设计筛查</h3><p className="small">将构件稳定、施工预加轴力、温度约束、节点间隙、安装偏心、节点完整性与传力冗余纳入候选评价。系统区分候选筛查、完整计算就绪和正式设计就绪，禁止用过期或低等级证据替代正式结论。</p></div>
          <span className={`statusTag ${deepDesign.formalDesignReady ? 'pass' : deepDesign.screeningPass ? 'warning' : 'error'}`}>证据 {String(deepDesign.evidenceGrade ?? 'D')} · {deepDesign.formalDesignReady ? '可进入正式交付闸门' : deepDesign.calculationReady ? '计算证据已形成' : deepDesign.screeningPass ? '仅候选筛查通过' : '候选受控阻断'}</span>
        </div>
        <div className="shapeMetricGrid">
          <div><strong>{Number(deepDesign.metrics?.maximumInteractionUtilization ?? 0).toFixed(3)}</strong><span>最大轴压-偏心组合利用率</span></div>
          <div><strong>{Number(deepDesign.metrics?.maximumSlenderness ?? 0).toFixed(1)}</strong><span>最大长细比</span></div>
          <div><strong>{Number(deepDesign.metrics?.maximumEffectiveUnbracedLengthM ?? 0).toFixed(2)} m</strong><span>最大有效无侧向支承长度</span></div>
          <div><strong>{Number(deepDesign.metrics?.maximumConstructionEffectRatio ?? 0).toFixed(3)}</strong><span>施工附加效应比</span></div>
          <div><strong>{Number(deepDesign.metrics?.supportMaterialVolumeM3 ?? 0).toFixed(1)} m³</strong><span>支撑材料体积</span></div>
          <div><strong>{Number(deepDesign.metrics?.memberFailCount ?? 0)} / {Number(deepDesign.metrics?.memberWarningCount ?? 0)}</strong><span>构件失败 / 预警</span></div>
          <div><strong>{Number(deepDesign.metrics?.supportNodeUncheckedCount ?? 0)}</strong><span>未闭环节点</span></div>
          <div><strong>{Number(deepDesign.metrics?.singleMemberWallPairCount ?? 0)}</strong><span>单构件墙对路径</span></div>
          <div><strong>{(Number(deepDesign.metrics?.stagedCalculationCoverageRatio ?? 0) * 100).toFixed(0)}%</strong><span>本方案分阶段轴力覆盖</span></div>
          <div><strong>{deepDesign.evidence?.forceEnvelope?.current ? '当前' : '缺失/过期'}</strong><span>计算合同状态</span></div>
          <div><strong>{deepDesign.readiness?.geotechnicalEvidencePass ? '通过' : '待补齐'}</strong><span>地质参数证据</span></div>
          <div><strong>{deepDesign.formalDesignReady ? '就绪' : '未就绪'}</strong><span>正式设计就绪度</span></div>
        </div>
        <details open={viewMode === 'professional'}><summary>查看控制构件、数学模型与整改动作</summary>
          <p className="small">{String(deepDesign.summary ?? '')}</p>
          <div className="shapeRuleColumns">
            <div><h4>控制构件</h4><ol>{(deepDesign.governingMembers ?? []).slice(0, 8).map((item: any) => <li key={String(item.supportId)}>{String(item.supportCode)} · η={Number(item.interactionUtilization ?? 0).toFixed(3)} · λ={Number(item.slenderness ?? 0).toFixed(1)} · {String(item.status)}</li>)}</ol></div>
            <div><h4>主要问题</h4><ul>{(deepDesign.issues ?? []).map((item: unknown, index: number) => <li key={`deep-issue-${index}`}>{String(item)}</li>)}</ul></div>
            <div><h4>建议动作</h4><ul>{(deepDesign.designActions ?? []).map((item: unknown, index: number) => <li key={`deep-action-${index}`}>{String(item)}</li>)}</ul></div>
          </div>
          <p className="small">计算模型：{String(deepDesign.model?.constructionEffects ?? '-')}；{String(deepDesign.model?.stability ?? '-')}；{String(deepDesign.model?.interaction ?? '-')}</p>
        </details>
        <div className="buttonRow"><button onClick={() => runStep('正在迭代支撑截面、稳定和临时立柱', async () => { const result = await api.optimizeSupportDeepDesign(project.id, 3); setDeepDesign(result); await onRefresh(); return result; })} disabled={!project.retainingSystem?.supports?.length}>执行支撑深化迭代</button><span className="small">固定现有拓扑，优先缩短有效计算长度并升级截面；仍不满足时要求返回平面体系优化。</span></div>
      </section>}
      {open && <div className="drawerBackdrop" onClick={() => setOpen(false)}><aside className="sideDrawer wideDrawer" onClick={(e) => e.stopPropagation()}><div className="drawerHeader"><h3>围护结构高级操作</h3><button className="secondary" onClick={() => setOpen(false)}>关闭</button></div>
        <button onClick={() => runStep('正在生成地下连续墙', () => api.autoWall(project.id))} disabled={!project.excavation}>仅生成地连墙</button>
        <button onClick={() => runTask('正在由独立进程生成水平支撑和立柱', 'support_layout_optimization', { preset: 'clean_support_layout', objectiveWeights: weights })} disabled={!project.excavation}>仅生成支撑/立柱</button>
        <div className="drawerSection">
          <h4>支撑优化权重可视化</h4>
          <label className="stackedLabel">优化偏好
            <select value={weightPreset} onChange={(event) => applyPreset(event.target.value as any)}>
              <option value="clean_support_layout">整洁优先：交叉点和内部汇交节点最少</option>
              <option value="balanced">均衡：间距、跨长、交叉、障碍、立柱和轴力综合</option>
              <option value="fewer_columns">优先少立柱</option>
              <option value="low_axial_force">优先低轴力峰值</option>
              <option value="muck_path_priority">优先出土通道连续和障碍避让</option>
            </select>
          </label>
          <div className="weightSliderGrid">
            {objectiveMeta.map(([key, label]) => <label key={key}><span>{label}</span><input type="range" min="0" max="80" value={weights[key] ?? 0} onChange={(event) => setWeights((prev) => ({ ...prev, [key]: Number(event.target.value) }))} /><strong>{weights[key] ?? 0}</strong></label>)}
          </div>
          {previewRows.length ? <table className="table compactTable"><thead><tr><th>实时预览排名</th><th>原排名</th><th>原评分</th><th>新评分</th><th>支撑/立柱</th></tr></thead><tbody>{previewRows.map((row, idx) => <tr key={row.id}><td>{idx + 1}</td><td>{row.rank}</td><td>{row.oldScore}</td><td>{row.previewScore.toFixed(1)}</td><td>{row.supportCount}/{row.columnCount}</td></tr>)}</tbody></table> : <p className="small">生成候选方案后，滑块会实时显示权重变化对候选排序的影响。</p>}
          <button onClick={() => runTask('正在由独立进程按平面类型生成候选支撑方案', 'support_layout_optimization', { preset: weightPreset, objectiveWeights: weights, searchConfig: { requireDiverseSchemes: true, enableConcaveTransferTemplates: true, concaveTransferTemplates: ['compact_elbow_ring', 'junction_hub_frame', 'ring_chord_frame'] } })} disabled={!project.excavation}>按当前权重生成 3-5 个候选方案</button>
          <p className="small">优化器先剔除非法穿越、障碍冲突、端点失效、围檩支点超限和换撑中断方案，再优先减少内部 T/Y/X 汇交节点与高度拥挤节点。综合评分只用于区分同等整洁度的可行方案。</p>
        </div>
        <div className="drawerSection">
          <h4>候选方案局部锁定</h4>
          <p className="small">可锁定整条支撑、单侧端点、某一支撑层，以及出土通道/障碍物边界。后续优化会保留这些人工决策。</p>
          <h5>支撑层锁定</h5>
          <div className="lockChipGrid">{levels.map((level) => <label key={level}><input type="checkbox" checked={lockedLevels.includes(level)} onChange={() => toggleLevel(level)} />L{level}</label>)}</div>
          <h5>出土通道 / 障碍边界锁定</h5>
          <div className="lockChipGrid">{(project.excavation?.obstacles ?? []).map((o) => o.id ? <label key={o.id}><input type="checkbox" checked={lockedObstacleIds.includes(o.id)} onChange={() => toggle(setLockedObstacleIds, o.id!)} />{o.name} · {o.obstacleType}</label> : null) || <span className="small">未定义出土口或障碍物。</span>}</div>
          <h5>支撑线与端点锁定</h5>
          <div className="supportLockList localLockList">
            {(project.retainingSystem?.supports ?? []).slice(0, 80).map((s) => <div key={s.id} className="supportLockRow"><strong>{s.code} · L{s.levelIndex}</strong><label><input type="checkbox" checked={lockedIds.includes(s.id)} onChange={() => toggle(setLockedIds, s.id)} />整线</label><label><input type="checkbox" checked={lockedStartIds.includes(s.id)} onChange={() => toggle(setLockedStartIds, s.id)} />起点</label><label><input type="checkbox" checked={lockedEndIds.includes(s.id)} onChange={() => toggle(setLockedEndIds, s.id)} />终点</label></div>)}
          </div>
          <div className="buttonRow"><button onClick={() => runStep('正在保存局部锁定状态', async () => {
            const lockItems = [
              ...lockedStartIds.map((supportId) => ({ targetType: 'support_endpoint', supportId, endpoint: 'start' })),
              ...lockedEndIds.map((supportId) => ({ targetType: 'support_endpoint', supportId, endpoint: 'end' })),
              ...lockedLevels.map((levelIndex) => ({ targetType: 'support_level', levelIndex })),
              ...lockedObstacleIds.map((obstacleId) => ({ targetType: 'obstacle_boundary', obstacleId }))
            ];
            await api.setSupportOptimizationLocks(project.id, { replace: true, locked: true, supportIds: lockedIds, lockItems, reason: 'operator local lock before optimization' });
          })} disabled={!project.retainingSystem}>保存局部锁定</button><button className="secondary" onClick={() => runStep('正在解除全部局部锁定', () => api.setSupportOptimizationLocks(project.id, { replace: true, locked: false, supportIds: [], lockItems: [], levelIndices: [], obstacleIds: [] }))} disabled={!project.retainingSystem}>全部解除锁定</button></div>
        </div>
        <p className="small">提示：运行优化后，请到“计算校核”页查看候选方案差异动画、A/B/C 完整计算比选，并选择“采用此方案”。</p>
      </aside></div>}
      {(unresolvedWarnings.length > 0 || designNotes.length > 0) && <section className="retainingEvidencePanel">
        <div className="retainingEvidenceHeader">
          <div>
            <strong>围护布置结论与算法证据</strong>
            <p className="small">风险项只保留当前尚未闭环的问题；自动吸附、避让、重排和删除等成功动作单列为设计证据。</p>
          </div>
          <div className="evidenceStatusBadges">
            <span className={unresolvedWarnings.length ? 'statusTag warning' : 'statusTag pass'}>待处理 {unresolvedWarnings.length}</span>
            <span className="statusTag info">算法动作 {designNotes.length}</span>
          </div>
        </div>
        {unresolvedWarnings.length > 0 && <div className="unresolvedWarningList">
          <h4>尚未闭环的设计风险</h4>
          <ol>{unresolvedWarnings.map((item, index) => <li key={`retaining-warning-${index}`}>{item}</li>)}</ol>
        </div>}
        {designNotes.length > 0 && <details className="designEvidenceDetails" open={viewMode === 'professional'}>
          <summary>查看算法已执行动作与形成的设计证据（{designNotes.length}）</summary>
          <ol>{designNotes.map((item, index) => <li key={`retaining-note-${index}`}>{item}</li>)}</ol>
        </details>}
      </section>}
      <PanelErrorBoundary title="方案比选" resetKey={`${project.id}-${project.updatedAt}`}><SchemeComparisonPanel
        project={project}
        compact={viewMode === 'compact'}
        onGenerateCandidates={() => runTask('正在由独立进程生成 A/B/C 整体候选方案', 'support_layout_optimization', { preset: 'balanced', maxCandidates: 3, searchConfig: { requireDiverseSchemes: true, enableConcaveTransferTemplates: true, concaveTransferTemplates: ['compact_elbow_ring', 'junction_hub_frame', 'ring_chord_frame'] } })}
        onRunComparison={() => runTask('正在受控计算 A/B/C 整体方案', 'candidate_comparison', { topN: 3 })}
        onAdopt={(candidateId) => runTask('正在由独立worker采用支撑优化方案', 'adopt_support_candidate', { candidateId })}
        onRefresh={onRefresh}
      /></PanelErrorBoundary>
      <DeferredDetails summary="查看当前围护结构模型与构件明细" defaultOpen={viewMode === 'professional'}><RetainingSystemViewer project={project} highlightLocator={selectedLocator} /></DeferredDetails>
    </div>
  );
}

function CalculationStep({ project, runStep, runWorkflow, runTask, onRefresh, selectedLocator, viewMode, onJump }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void>; runWorkflow: (title: string, actions: WorkflowAction[]) => Promise<void>; runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; onRefresh: () => void | Promise<void>; selectedLocator?: Record<string, unknown>; viewMode: 'compact' | 'professional'; onJump: (key: WorkflowStepKey) => void }) {
  const [open, setOpen] = useState(false);
  const [qualification, setQualification] = useState<Record<string, any>>();
  const [qualificationError, setQualificationError] = useState<string>();
  const [latestEvidence, setLatestEvidence] = useState<{ evidence: Record<string, any>; result?: CalculationResult }>();
  const workspaceLatest = project.calculationResults?.[project.calculationResults.length - 1];
  useEffect(() => {
    let cancelled = false;
    setQualificationError(undefined);
    void api.getDesignQualification(project.id).then((value) => {
      if (!cancelled) setQualification(value);
    }).catch((error) => {
      if (!cancelled) setQualificationError(error instanceof Error ? error.message : String(error));
    });
    return () => { cancelled = true; };
  }, [project.id, project.updatedAt, project.retainingSystem?.supportLayoutRepair?.checkedAt]);

  useEffect(() => {
    let cancelled = false;
    if (!workspaceLatest) {
      setLatestEvidence(undefined);
      return () => { cancelled = true; };
    }
    void api.getLatestCalculationEvidence(project.id).then((value) => {
      if (!cancelled) setLatestEvidence({ evidence: value.evidence, result: value.result });
    }).catch(() => {
      if (!cancelled) setLatestEvidence({ evidence: { state: 'load_failed', message: '最新施工阶段成果读取失败，请刷新或重新计算。' } });
    });
    return () => { cancelled = true; };
  }, [project.id, project.updatedAt, workspaceLatest?.id]);

  const calculationProject = useMemo<Project>(() => {
    if (!latestEvidence?.result || latestEvidence.result.id !== workspaceLatest?.id) return project;
    const history = [...(project.calculationResults ?? [])];
    if (history.length) history[history.length - 1] = latestEvidence.result;
    else history.push(latestEvidence.result);
    return { ...project, calculationResults: history };
  }, [project, latestEvidence?.result, workspaceLatest?.id]);

  const rawCandidates = project.retainingSystem?.supportLayoutRepair?.candidates ?? [];
  const localControlledBlock = rawCandidates.length > 0
    && !rawCandidates.some((candidate) => Boolean(candidate.hardConstraints?.passed))
    && rawCandidates.some((candidate) => String(candidate.variableSummary?.capabilityOutcome ?? '') === 'controlled_block');
  const calculationAllowed = qualification
    ? Boolean(qualification.calculationAllowed)
    : Boolean(project.retainingSystem) && !localControlledBlock;
  const calculationBlockers = ((qualification?.gates ?? []) as Record<string, any>[])
    .filter((gate) => ((gate.blocks ?? []) as string[]).includes('calculation'))
    .map((gate) => `${String(gate.title ?? gate.code)}：${String(gate.message ?? '')}`);
  const runAll = () => calculationAllowed ? runTask('一键计算校核', 'calculation_full', { topN: 0 }) : Promise.resolve();
  const calculationDisabled = !project.retainingSystem || !calculationAllowed;
  const navigateClosureTarget = (targetPanel: string) => {
    if (targetPanel.includes('施工阶段')) {
      document.getElementById('construction-stage-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (targetPanel.includes('地质')) { onJump('geology'); return; }
    if (targetPanel.includes('围护方案') || targetPanel.includes('梁构件') || targetPanel.includes('墙段') || targetPanel.includes('支撑')) { onJump('retaining'); return; }
    if (targetPanel.includes('工程输入')) { onJump('settings'); return; }
    onJump('assurance');
  };
  return (
    <div>
      {!calculationAllowed ? <div className="warning calculationQualificationBlock" role="alert"><strong>完整计算入口已由设计资格门禁锁定。</strong><span>{calculationBlockers.join('；') || (localControlledBlock ? '当前支撑体系仅形成诊断候选，需先完成体系选择与传力拓扑闭合。' : '请先完成围护结构和支撑体系资格检查。')}</span></div> : null}
      {qualificationError ? <div className="mutedNote">设计资格状态暂未刷新：{qualificationError}</div> : null}
      <ConstructionStageEditor project={project} onChanged={onRefresh} />
      {workspaceLatest ? <div className={`calculationEvidenceBanner ${String(latestEvidence?.evidence?.state ?? 'loading')}`}><div><strong>施工阶段计算证据</strong><span>{String(latestEvidence?.evidence?.message ?? '正在按最新计算结果编号读取阶段成果…')}</span></div><b>{String(latestEvidence?.evidence?.stageResultCount ?? 0)} / {String(latestEvidence?.evidence?.expectedStageResultCount || '-')} 条已载入</b><em>{String(latestEvidence?.evidence?.state ?? 'loading')}</em></div> : null}
      <div className="actionStrip simplifiedActions">
        <button onClick={runAll} disabled={calculationDisabled}>一键计算校核</button>
        <button className="secondary" onClick={() => setOpen(true)}>高级操作</button>
        
      </div>
      {open && <div className="drawerBackdrop" onClick={() => setOpen(false)}><aside className="sideDrawer" onClick={(e) => e.stopPropagation()}><div className="drawerHeader"><h3>计算高级操作</h3><button className="secondary" onClick={() => setOpen(false)}>关闭</button></div><button onClick={() => runStep('正在生成施工工况', () => api.buildCases(project.id))} disabled={calculationDisabled}>仅生成工况</button><button onClick={() => runTask('正在由独立计算进程运行当前方案', 'calculation_full', { topN: 0 })} disabled={calculationDisabled}>仅运行当前方案</button><button onClick={() => runTask('正在受控计算前 3 个候选方案', 'candidate_comparison', { topN: 3 })} disabled={calculationDisabled}>并行计算前 3 个候选方案</button></aside></div>}
      <PanelErrorBoundary title="计算阶段方案比选" resetKey={`${project.id}-${project.updatedAt}`}><SchemeComparisonPanel project={project} compact={viewMode === 'compact'} onGenerateCandidates={() => runTask('正在由独立进程生成 A/B/C 整体候选方案', 'support_layout_optimization', { preset: 'balanced', maxCandidates: 3, searchConfig: { requireDiverseSchemes: true, enableConcaveTransferTemplates: true, concaveTransferTemplates: ['compact_elbow_ring', 'junction_hub_frame', 'ring_chord_frame'] } })} onRunComparison={() => runTask('正在受控计算 A/B/C 整体方案', 'candidate_comparison', { topN: 3 })} onAdopt={(candidateId) => runTask('正在由独立worker采用支撑优化方案', 'adopt_support_candidate', { candidateId })} onRefresh={onRefresh} /></PanelErrorBoundary>
      <CalculationRecoveryPanel project={calculationProject} runStep={runStep} onNavigate={navigateClosureTarget} />
      <PanelErrorBoundary title="计算结果与规范复核" resetKey={`${calculationProject.id}-${calculationProject.updatedAt}`}><ResultViewer project={calculationProject} runStep={runStep} runTask={runTask} highlightLocator={selectedLocator} density={viewMode} /></PanelErrorBoundary>
      {viewMode === 'professional' ? <CalculationTracePanel project={calculationProject} /> : <details className="focusDetails"><summary>查看完整计算追溯链、公式和规范条文</summary><CalculationTracePanel project={calculationProject} /></details>}
    </div>
  );
}


function formulaToMathText(value?: string) {
  const raw = String(value ?? '-').trim();
  if (!raw || raw === '-') return '-';
  const subs: Record<string, string> = { '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉', a: 'ₐ', b: 'ᵦ', c: '꜀', d: 'ᵈ', e: 'ₑ', f: 'ᶠ', h: 'ₕ', i: 'ᵢ', j: 'ⱼ', k: 'ₖ', l: 'ₗ', m: 'ₘ', n: 'ₙ', p: 'ₚ', q: 'ᵩ', r: 'ᵣ', s: 'ₛ', t: 'ₜ', u: 'ᵤ', v: 'ᵥ', w: 'ᵥ', x: 'ₓ', y: 'ᵧ' };
  const toSub = (text: string) => text.split('').map((ch) => subs[ch] ?? ch).join('');
  let out = raw;
  const phraseMap: [RegExp, string][] = [
    [/gamma_eff/gi, 'γ′'], [/gamma_0/gi, 'γ₀'], [/gamma_F/g, 'γF'], [/gamma_w/gi, 'γw'], [/gamma/g, 'γ'],
    [/embedment[_ ]?depth/gi, 'h' + toSub('emb')], [/head[_ ]?difference/gi, 'Δh'], [/cover[_ ]?thickness/gi, 't' + toSub('cover')], [/confined[_ ]?head[_ ]?above[_ ]?pit[_ ]?bottom/gi, 'h' + toSub('conf')],
    [/tributary[_ ]?width/gi, 'b' + toSub('trib')], [/stage[_ ]?load/gi, 'q' + toSub('stage')], [/elastic[_ ]?supports/gi, 'k' + toSub('s')], [/continuous[_ ]?beam[_ ]?reaction/gi, 'R' + toSub('wale')],
    [/construction[_ ]?effects/gi, 'N' + toSub('c')], [/preload/gi, 'N' + toSub('pre')], [/temperature/gi, 'N' + toSub('T')], [/gap/gi, 'N' + toSub('gap')],
    [/project[_ ]?limit/gi, 'u' + toSub('lim')], [/environment[_ ]?grade/gi, 'env'], [/ratio/gi, 'η'], [/demand/gi, 'S'], [/limit/gi, 'R'], [/resistance/gi, 'R'], [/action/gi, 'S'],
    [/delta_max/gi, 'δ' + toSub('max')], [/u_max/gi, 'u' + toSub('max')], [/M_stage/gi, 'M' + toSub('stage')], [/M_d/g, 'M' + toSub('d')], [/N_d/g, 'N' + toSub('d')], [/N_eff/g, 'N' + toSub('eff')], [/M_e/g, 'M' + toSub('e')], [/K_heave/gi, 'K' + toSub('heave')], [/phi/g, 'φ'],
    [/alpha1/gi, 'α₁'], [/fc/g, 'f' + toSub('c')], [/ft/g, 'f' + toSub('t')], [/fy/g, 'f' + toSub('y')], [/As/g, 'A' + toSub('s')], [/Ac/g, 'A' + toSub('c')], [/Ap/g, 'A' + toSub('p')], [/qs/g, 'q' + toSub('s')], [/qp/g, 'q' + toSub('p')], [/h0/g, 'h₀'], [/sigma_bearing/gi, 'σ' + toSub('b')], [/A_plate/gi, 'A' + toSub('plate')], [/f_c/gi, 'f' + toSub('c')],
  ];
  phraseMap.forEach(([pattern, replacement]) => { out = out.replace(pattern, replacement); });
  out = out
    .replace(/<=/g, '≤')
    .replace(/>=/g, '≥')
    .replace(/\*/g, ' · ')
    .replace(/\bplus\b/gi, '+')
    .replace(/\bintegral\(([^)]+)\)/gi, '∫($1)')
    .replace(/envelope\(([^)]+)\)/gi, 'env($1)')
    .replace(/max\(([^)]+)\)/gi, 'max($1)')
    .replace(/min\(([^)]+)\)/gi, 'min($1)')
    .replace(/\|u_i\|/g, '|uᵢ|')
    .replace(/_/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return out;
}

function FormulaDisplay({ value }: { value?: string }) {
  return <span className="formulaDisplay" aria-label={String(value ?? '-')}>{formulaToMathText(value)}</span>;
}

function statusLabel(status?: string) {
  const key = String(status ?? '').toLowerCase();
  if (key === 'pass') return '合规';
  if (key === 'fail') return '不合规';
  if (key === 'warning') return '预警';
  if (key === 'manual_review') return '需复核';
  return status || '-';
}

function statusTone(status?: string): 'pass' | 'warn' | 'fail' | 'review' {
  const key = String(status ?? '').toLowerCase();
  if (key === 'pass') return 'pass';
  if (key === 'fail') return 'fail';
  if (key === 'manual_review') return 'review';
  return 'warn';
}

function formatNumber(value?: number, digits = 3) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-';
  const rounded = Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(digits);
  return rounded.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
}

function utilizationPercent(item: import('../types/domain').CalculationTraceEntry) {
  if (typeof item.utilization === 'number' && Number.isFinite(item.utilization)) return Math.max(0, item.utilization * 100);
  if (typeof item.demandValue === 'number' && typeof item.capacityValue === 'number' && Number.isFinite(item.capacityValue) && Math.abs(item.capacityValue) > 1e-9) {
    return Math.max(0, (Math.abs(item.demandValue) / Math.abs(item.capacityValue)) * 100);
  }
  return undefined;
}

function ComplianceCompare({ item }: { item: import('../types/domain').CalculationTraceEntry }) {
  const pct = utilizationPercent(item);
  const capped = typeof pct === 'number' ? Math.min(140, pct) : undefined;
  return <div className="complianceCompare">
    <div className="compareValues"><span>需求 {formatNumber(item.demandValue)}</span><span>限值 {formatNumber(item.capacityValue)}</span><span>{typeof pct === 'number' ? `${formatNumber(pct, 1)}%` : '-'}</span></div>
    <div className="compareBar"><i className={`compareFill ${statusTone(item.status)}`} style={{ width: `${capped ?? 0}%` }} /><b style={{ left: '100%' }} /></div>
  </div>;
}

function statusCounts(entries: import('../types/domain').CalculationTraceEntry[]) {
  return entries.reduce<Record<string, number>>((acc, item) => { const k = String(item.status || 'unknown'); acc[k] = (acc[k] ?? 0) + 1; return acc; }, {});
}


function CalculationTracePanel({ project }: { project: Project }) {
  const [trace, setTrace] = useState<import('../types/domain').CalculationTraceResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    if (!project.calculationResults.length) return () => { alive = false; };
    api.getCalculationTrace(project.id).then((data) => { if (alive) setTrace(data); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, project.calculationResults.length]);
  if (!project.calculationResults.length) return null;
  if (error) return <div className="error">计算追溯链读取失败：{error}</div>;
  if (!trace) return <div className="summaryPanel"><h3>计算追溯链</h3><p className="small">正在读取校验条文和控制值...</p></div>;
  const counts = { ...(trace.summary.statusCounts ?? {}), ...statusCounts(trace.entries) };
  const governingEntries = [...trace.entries].sort((a, b) => (utilizationPercent(b) ?? -1) - (utilizationPercent(a) ?? -1)).slice(0, 48);
  return (
    <section className="tracePanel">
      <div className="issueCenterHeader"><div><h3>计算追溯链</h3><p>按“需求值—限值—利用率—条文”对比校验。</p></div><strong>{trace.summary.controlPathCompleteness}%</strong></div>
      <div className="traceStatusStrip">
        <span className="pass">合规 {counts.pass ?? 0}</span>
        <span className="fail">不合规 {counts.fail ?? 0}</span>
        <span className="warn">预警 {counts.warning ?? 0}</span>
        <span className="review">复核 {counts.manual_review ?? 0}</span>
      </div>
      <div className="maturityGrid compactMaturity">
        <StatusCard title="追溯条目" value={String(trace.summary.traceCount)} detail="控制值与规范筛查" tone={trace.summary.traceCount ? 'pass' : 'warn'} />
        <StatusCard title="控制对象" value={String(trace.summary.governingObjectCount)} detail="构件/截面数量" tone={trace.summary.governingObjectCount ? 'pass' : 'warn'} />
        <StatusCard title="规范引用" value={String(trace.summary.codeReferenceCount)} detail="条文和公式来源" tone={trace.summary.codeReferenceCount ? 'pass' : 'warn'} />
        <StatusCard title="总状态" value={statusLabel(trace.summary.status)} detail={trace.summary.message} tone={statusTone(trace.summary.status)} />
      </div>
      <div className="traceCompareTableWrap"><table className="table traceCompareTable"><thead><tr><th>判定</th><th>对象 / 工况</th><th>校验项</th><th>需求—限值</th><th>公式</th><th>规范条文</th></tr></thead><tbody>
        {governingEntries.map((item) => <tr key={item.id} className={`issue-${item.status}`}><td><span className={`statusBadge ${statusTone(item.status)}`}>{statusLabel(item.status)}</span></td><td><strong>{item.objectId ?? '-'}</strong><em>{item.stageName}</em></td><td><strong>{item.title}</strong><em>{item.demandName || item.category}</em></td><td><ComplianceCompare item={item} /><em>{item.unit ?? ''}</em></td><td className="complianceFormulaCell"><FormulaDisplay value={item.formula} /></td><td>{item.codeReference ?? '-'}</td></tr>)}
      </tbody></table></div>
      {trace.notes.length ? <ul className="small traceNotes">{trace.notes.slice(0, 3).map((note) => <li key={note}>{note}</li>)}</ul> : null}
    </section>
  );
}

function ModuleCompletionReview({ assurance }: { assurance: AssuranceResult }) {
  const modules = assurance.moduleCompletionReview ?? [];
  if (!modules.length) return null;
  const blocking = modules.filter((item) => item.blocking).length;
  const incomplete = modules.filter((item) => item.completion < 100).length;
  return <section className="moduleCompletionReview">
    <div className="moduleCompletionHeader">
      <div><span className="sectionKicker">闭环审查核心成果</span><h3>全系统模块完成度审查</h3><p>保留全部模块，不因界面精简而隐藏。每个模块显示完成度、阻断状态、证据、缺项和下一步动作。</p></div>
      <div className="moduleCompletionSummary"><strong>{assurance.moduleOverallCompleteness ?? assurance.completionPercent}%</strong><span>12 个模块 · 阻断 {blocking} · 未完成 {incomplete}</span></div>
    </div>
    <div className="moduleCompletionGrid">
      {modules.map((item) => <article key={item.id} className={`moduleCompletionCard status-${item.status} ${item.blocking ? 'blocking' : ''}`}>
        <div className="moduleCardTitle"><span>{item.id}</span><strong>{item.name}</strong><em>{item.ownerRole}</em></div>
        <div className="moduleProgressLine"><progress max={100} value={item.completion} /><b>{item.completion}%</b></div>
        <p>{item.completedItemCount}/{item.totalItemCount} 项已完成{item.blocking ? ' · 当前存在阻断' : ''}</p>
        {item.gaps.length ? <ul>{item.gaps.slice(0, 2).map((gap) => <li key={gap.item}><strong>{gap.item}</strong><span>{gap.recommendation}</span></li>)}</ul> : <div className="moduleCompleteEvidence">证据链完整：{item.evidence.slice(0, 2).join('、')}</div>}
        <footer>{item.nextAction}</footer>
      </article>)}
    </div>
  </section>;
}

function IndustrialReadinessPanel({ data, onRun }: { data?: import('../types/domain').IndustrialReadinessResult; onRun: () => void }) {
  if (!data) return <section className="summaryPanel"><h3>P0-P3 工业闭环</h3><p className="small">正在读取工业成熟度闸门...</p></section>;
  return <section className="summaryPanel industrialReadinessPanel">
    <div className="issueCenterHeader"><div><span className="sectionKicker">工业化闭环控制</span><h3>P0-P3 工业成熟度闸门</h3><p>{data.boundary}</p></div><strong>{data.industrialReadinessScore}%</strong></div>
    <div className="maturityGrid compactMaturity">
      {data.phases.map((phase) => <StatusCard key={phase.phaseId} title={`${phase.phaseId} · ${phase.title}`} value={`${phase.completion}%`} detail={`阻断 ${phase.blockingCount} · 警告 ${phase.warningCount}`} tone={statusTone(phase.status)} />)}
    </div>
    <div className="actionStrip"><button onClick={onRun}>运行 P0-P3 工业闭环</button><span className={`statusBadge ${statusTone(data.status)}`}>{statusLabel(data.status)} · 阻断 {data.blockingCount} · 警告 {data.warningCount}</span></div>
    <details className="focusDetails"><summary>查看 P0-P3 检查项</summary><table className="dataTable"><thead><tr><th>阶段</th><th>检查项</th><th>状态</th><th>整改动作</th></tr></thead><tbody>{data.phases.flatMap((phase) => phase.checks.map((check) => <tr key={`${phase.phaseId}-${check.code}`} className={`check-${check.status}`}><td>{phase.phaseId}</td><td>{check.title}</td><td>{check.status}</td><td>{check.requiredAction || '已闭环'}</td></tr>))}</tbody></table></details>
  </section>;
}

function AssurancePanel({ project, onLocateIssue, onChanged, runTask, viewMode }: { project: Project; onLocateIssue: (issue: IssueCenterItem) => void; onChanged: () => void | Promise<void>; runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; viewMode: 'compact' | 'professional' }) {
  const [assurance, setAssurance] = useState<AssuranceResult | undefined>();
  const [industrial, setIndustrial] = useState<import('../types/domain').IndustrialReadinessResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    api.getAssurance(project.id).then((data) => { if (alive) setAssurance(data); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    api.getIndustrialReadiness(project.id).then((data) => { if (alive) setIndustrial(data); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.calculationResults.length]);
  if (error) return <div className="error">{error}</div>;
  if (!assurance) return <div className="small">正在读取完成度分析...</div>;
  const capability = assurance.capabilityCompleteness ?? assurance.completionPercent;
  const detailedAcceptance = <>
    {assurance.supportLayoutQuality && <div className="summaryPanel"><h3>支撑布置评分</h3><p>{assurance.supportLayoutQuality.summary}</p><p className="small">状态：{assurance.supportLayoutQuality.status}；评分：{assurance.supportLayoutQuality.score}</p></div>}
    {assurance.ifcCompatibility && <div className="summaryPanel"><h3>IFC 兼容性自检</h3><p>{assurance.ifcCompatibility.summary}</p><p className="small">raw unicode：{String(assurance.ifcCompatibility.rawUnicodeFound)}；零尺寸：{assurance.ifcCompatibility.zeroDimensionCount ?? 0}；材料缺失：{assurance.ifcCompatibility.missingMaterialAssociationCount ?? 0}</p></div>}
    <div className="stepGrid">
      <IssueTable title="出图阻断项" items={assurance.officialIssueBlockingItems ?? []} empty="没有硬性阻断项。" />
      <IssueTable title="出图警告项" items={assurance.officialIssueWarningItems ?? []} empty="没有警告项。" />
      <IssueTable title="流程/成果缺项" items={assurance.officialIssueMissingItems ?? []} empty="没有缺项。" />
    </div>
    {assurance.softwareFlowMissingItems?.length ? <><h3>软件流程缺项</h3><table className="dataTable"><thead><tr><th>ID</th><th>验收项</th><th>状态</th><th>说明</th></tr></thead><tbody>{assurance.softwareFlowMissingItems.map((item) => <tr key={item.id} className={`check-${item.status}`}><td>{item.id}</td><td>{item.title}</td><td>{item.status}</td><td>{item.message}</td></tr>)}</tbody></table></> : null}
    <h3>完整验收矩阵</h3>
    <table className="dataTable"><thead><tr><th>ID</th><th>验收项</th><th>状态</th><th>说明</th></tr></thead><tbody>{assurance.acceptanceMatrix.map((item) => <tr key={item.id} className={`check-${item.status}`}><td>{item.id}</td><td>{item.title}</td><td>{item.status}</td><td>{item.message}</td></tr>)}</tbody></table>
    <h3>边界策略</h3><ul>{assurance.remainingBoundaryPolicy.map((item) => <li key={item}>{item}</li>)}</ul>
  </>;
  const advancedEngineering = <Suspense fallback={<div className="summaryPanel"><h3>工程深化与发行闭环</h3><p className="small">正在按需加载深化分析模块…</p></div>}><AdvancedEngineeringPanel project={project} onChanged={onChanged} /></Suspense>;
  return <div>
    <IndustrialReadinessPanel data={industrial} onRun={() => runTask('P0-P3 工业闭环计算与资格评估', 'industrial_closure', { topN: 3 })} />
    <div className="assuranceCards">
      <StatusCard title="功能完成度" value={`${capability}%`} detail="软件功能路径覆盖率" tone={capability >= 100 ? 'pass' : 'warn'} />
      <StatusCard title="软件流程" value={assurance.softwareFlowComplete ? '完整' : '未完整'} detail={assurance.softwareFlowComplete ? '资料—设计—计算—成果路径完整' : `仍缺 ${assurance.softwareFlowMissingItems?.length ?? 0} 个流程项`} tone={assurance.softwareFlowComplete ? 'pass' : 'warn'} />
      <StatusCard title="工程校核" value={assurance.engineeringCheckStatus ?? 'manual_review'} detail={`Fail ${assurance.failureCount} · 复核 ${assurance.manualReviewCount}`} tone={assurance.engineeringCheckStatus === 'fail' ? 'fail' : assurance.engineeringCheckStatus === 'pass' ? 'pass' : 'review'} />
      <StatusCard title="正式出图闸门" value={assurance.officialIssueGateAllowed ? '允许' : '阻断'} detail={assurance.officialIssueGateHeadline ?? assurance.officialIssueGateDetail ?? '查看阻断/警告/缺项'} tone={assurance.officialIssueGateStatus === 'fail' ? 'fail' : assurance.officialIssueGateStatus === 'pass' ? 'pass' : 'warn'} />
    </div>
    <ModuleCompletionReview assurance={assurance} />
    <IssueCenterPanel project={project} onLocateIssue={onLocateIssue} compact={viewMode === 'compact'} />
    {viewMode === 'professional' ? <>{advancedEngineering}{detailedAcceptance}</> : <>
      <details className="focusDetails"><summary>专业深化：长期效应、碰撞、节点、监测、审签与吊装</summary>{advancedEngineering}</details>
      <details className="focusDetails"><summary>完整验收矩阵、IFC 自检和工程边界</summary>{detailedAcceptance}</details>
    </>}
  </div>;
}

function IssueCenterPanel({ project, onLocateIssue, compact = false }: { project: Project; onLocateIssue: (issue: IssueCenterItem) => void; compact?: boolean }) {
  const [data, setData] = useState<IssueCenterResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    api.getIssueCenter(project.id).then((result) => { if (alive) setData(result); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, project.calculationResults.length]);
  if (error) return <div className="error">问题清单读取失败：{error}</div>;
  if (!data) return <div className="summaryPanel"><h3>问题清单中心</h3><p className="small">正在读取问题清单和 2.1.0 完成度评估...</p></div>;
  const maturity = data.maturity;
  return (
    <section className="issueCenterPanel">
      <div className="issueCenterHeader">
        <div><h3>问题清单中心 · V{maturity.softwareVersion}</h3><p>把阻断项、警告项、人工复核项和下一步动作集中到同一个闭环面板。</p></div>
        <strong>{maturity.overallCompletion}%</strong>
      </div>
      {!compact && <div className="maturityGrid">
        <StatusCard title="系统模块完成度" value={`${maturity.overallCompletion}%`} detail={maturity.closedLoopComplete ? '软件闭环模块 100%' : '模块仍有缺项'} tone={maturity.closedLoopComplete ? 'pass' : 'warn'} />
        <StatusCard title="数据建模" value={`${maturity.dataModelCompletion}%`} detail="地勘、地质、基坑轮廓" tone={maturity.dataModelCompletion >= 90 ? 'pass' : 'warn'} />
        <StatusCard title="设计计算" value={`${maturity.designCalculationCompletion}%`} detail="围护、计算、规范筛查、闸门" tone={maturity.designCalculationCompletion >= 90 ? 'pass' : 'warn'} />
        <StatusCard title="BIM/CAD 交付" value={`${maturity.bimCadDeliverableCompletion}%`} detail="IFC、CAD、钢筋、图表" tone={maturity.bimCadDeliverableCompletion >= 85 ? 'pass' : 'warn'} />
        <StatusCard title="工程出图准备" value={`${maturity.engineeringAcceptanceReadiness ?? maturity.officialIssueReadiness}%`} detail="项目数据和专业复核状态" tone={(maturity.engineeringAcceptanceReadiness ?? maturity.officialIssueReadiness) >= 90 ? 'pass' : 'review'} />
      </div>}
      <div className="issueCounters">
        <span className="fail">阻断 {data.summary.fail ?? 0}</span>
        <span className="warn">警告 {data.summary.warning ?? 0}</span>
        <span className="review">人工复核 {data.summary.manual_review ?? 0}</span>
        <span>合计 {data.issueCount}</span>
      </div>
      {!compact && (maturity.moduleLedger ?? data.moduleLedger ?? []).length ? <div className="moduleLedger"><h4>软件模块清单</h4><div className="moduleLedgerGrid">{(maturity.moduleLedger ?? data.moduleLedger ?? []).map((item) => <div key={item.id} className="moduleLedgerItem"><strong>{item.id} · {item.name}</strong><span>{item.completion}% · {item.status} </span><em>{item.evidence}</em></div>)}</div></div> : null}
      <div className={`stepGrid ${compact ? 'compactIssueActions' : ''}`}>
        <div className="summaryPanel"><h4>优先处理动作</h4><ol className="nextActionList">{data.nextActions.slice(0, compact ? 3 : 6).map((item, index) => <li key={`${item.title}-${index}`}><strong>{item.workflowStep}</strong><span>{item.title}</span><em>{item.recommendation}</em></li>)}</ol></div>
        {!compact ? <div className="summaryPanel"><h4>当前边界</h4><ul>{maturity.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
      </div>
      {compact ? <div className="compactIssueList">{data.issues.slice(0, 8).map((item) => <button key={item.id} className={`compactIssueItem issue-${item.severity}`} onClick={() => onLocateIssue(item)}><span>{item.severity} · {item.workflowStep}</span><strong>{item.message}</strong><em>{item.recommendation}</em></button>)}</div> : <table className="table compactTable"><thead><tr><th>等级</th><th>流程</th><th>类别</th><th>对象</th><th>问题</th><th>定位</th><th>建议</th></tr></thead><tbody>{data.issues.slice(0, 30).map((item) => <tr key={item.id} className={`issue-${item.severity} clickableRow`} onClick={() => onLocateIssue(item)} title="点击定位到对应流程、构件或 CAD 图纸"><td>{item.severity}</td><td>{item.workflowStep}</td><td>{item.category}</td><td>{item.objectId ?? '-'}</td><td>{item.message}</td><td>{String(item.locator?.targetPanel ?? item.targetPanel ?? item.workflowStep)}{item.locator?.drawingSheet ? <em> · {String(item.locator.drawingSheet)}</em> : null}</td><td>{item.recommendation}</td></tr>)}</tbody></table>}
    </section>
  );
}

function IssueTable({ title, items, empty }: { title: string; items: { category: string; severity: string; objectId?: string; message: string; recommendation?: string }[]; empty: string }) {
  return (
    <div className="summaryPanel">
      <h3>{title}</h3>
      <table className="table compactTable"><thead><tr><th>类别</th><th>等级</th><th>对象</th><th>说明</th><th>建议</th></tr></thead><tbody>
        {items.length ? items.slice(0, 12).map((item, index) => <tr key={`${title}-${index}`}><td>{item.category}</td><td>{item.severity}</td><td>{item.objectId ?? '-'}</td><td>{item.message}</td><td>{item.recommendation ?? '-'}</td></tr>) : <tr><td colSpan={5}>{empty}</td></tr>}
      </tbody></table>
    </div>
  );
}

function ExportPanel({ project, runTask, selectedLocator, onRefresh, viewMode }: { project: Project; runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; selectedLocator?: Record<string, unknown>; onRefresh: () => void; viewMode: 'compact' | 'professional' }) {
  const latest = getLatestResult(project);
  const [advanced, setAdvanced] = useState(false);
  const blocked = !latest || !project.retainingSystem || latest.governingValues.governingCheckStatus === 'fail' || (latest.checkSummary?.fail ?? 0) > 0;
  const professionalPanels = <>
    <CadTemplatePanel project={project} />
    <DrawingRuleSetPanel project={project} onApplied={onRefresh} />
    <CadLocatorPreview project={project} locator={selectedLocator} />
    <RebarDesignPanel project={project} onApplied={onRefresh} />
    <RebarDetailingPanel project={project} />
    <RebarIfcViewer project={project} highlightLocator={selectedLocator} />
    {advanced && <BenchmarkPanel />}
    <div className="summaryPanel exportModelPreview"><h3>模型预览</h3><Engineering3DViewer project={project} focus="retaining" highlightLocator={selectedLocator} /></div>
  </>;
  return <div>
    {blocked && <div className="warning">当前成果存在阻断或尚未计算。审查成果可以生成，施工版发行仍由质量闸门控制。</div>}
    <div className="actionStrip simplifiedActions"><button onClick={() => runTask('全流程计算与成果生成', 'full_delivery', { topN: 3 })} disabled={!project.retainingSystem}>一键生成完整交付包</button><button className="secondary" onClick={() => setAdvanced((v) => !v)}>{advanced ? '收起扩展格式' : '扩展导出格式'}</button></div>
    <div className="exportGrid preferredExports">
      <ExportCard projectId={project.id} taskOperation="export_coordinated_delivery" title="协同成果交付包" description="施工图、批量PDF、IFC多配置、计算书、钢筋深化、项目快照、逐图质量和验收矩阵。" href={api.coordinatedDeliveryPackageUrl(project.id, 'review')} button="生成协同交付包" />
      <ExportCard projectId={project.id} taskOperation="export_formal_drawings" title="正式图纸发行包" description="CAD、批量PDF、图纸—模型—计算—规范索引、修订和逐图质量门禁。" href={api.formalDrawingPackageUrl(project.id, 'review')} button="生成图纸包" />
      <ExportCard projectId={project.id} taskOperation="export_ifc_construction_visual" title="IFC 可视化模型" description="用于外部 BIM 查看和专业协调。" href={api.exportUrl(project.id, 'ifc-construction-visual')} button="下载 IFC" />
      <ExportCard projectId={project.id} taskOperation="export_report" title="计算书 DOCX" description="控制结果、问题清单和 A/B/C 方案比选。" href={api.exportUrl(project.id, 'report')} button="下载计算书" />
      <ExportCard projectId={project.id} taskOperation="export_rebar_detailing" title="钢筋加工深化包" description="逐根钢筋、BBS、套筒、吊装和碰撞协调数据。" href={api.rebarDetailingPackageUrl(project.id)} button="下载钢筋 ZIP" />
    </div>
    {advanced && <div className="exportGrid advancedExports">
      <ExportCard projectId={project.id} taskOperation="export_ifc_light" title="IFC 轻量协调版" description="用于快速协调浏览。" href={api.exportUrl(project.id, 'ifc-light')} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_ifc_analysis" title="IFC 分析模型版" description="用于计算模型交换。" href={api.exportUrl(project.id, 'ifc-analysis')} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_ifc_detailed" title="IFC 语义详细版" description="用于 BIM 语义审查。" href={api.exportUrl(project.id, 'ifc-detailed')} button="下载" />
      <ExportCard title="IFC 自检" description="检查目标 Viewer 兼容性。" href={api.ifcCheckUrl(project.id, 'construction_visual')} button="查看" />
      <ExportCard projectId={project.id} taskOperation="export_drawings_svg" title="SVG 图纸包" description="用于汇报和插图。" href={api.exportUrl(project.id, 'drawings-svg')} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_trace" title="计算追溯链" description="JSON 追溯数据。" href={api.exportUrl(project.id, 'json')} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_issue_report" title="问题清单" description="JSON 审查清单。" href={api.exportUrl(project.id, 'json')} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_json" title="完整 JSON" description="项目归档和迁移。" href={api.exportUrl(project.id, 'json')} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_wall_length_redundancy" title="围护墙冗余优化报告" description="设计长度、分幅和局部加强记录。" href={api.wallLengthRedundancyReportUrl(project.id)} button="下载" />
      <ExportCard projectId={project.id} taskOperation="export_design_scheme_ledger" title="方案快照与交付台账" description="采纳历史、复算状态、交付闸门和当前方案 KPI。" href={api.designSchemeLedgerReportUrl(project.id)} button="下载" />
    </div>}
    {viewMode === 'professional' ? professionalPanels : <DeferredDetails summary="专业出图配置、配筋深化和三维检查">{professionalPanels}</DeferredDetails>}
  </div>;
}

function ProjectTreeSummary({ project }: { project: Project }) {
  return (
    <div className="projectTreeSummary">
      <h4>当前数据摘要</h4>
      <ul>
        <li>钻孔：{project.boreholes.length}</li>
        <li>地层：{project.strata.length}</li>
        <li>地质面：{effectiveGeologicalSurfaces(project).length}</li>
        <li>设计域覆盖：{project.geologicalModel?.coverageAudit?.designDomainCovered === false ? '不足' : (project.geologicalModel?.coverageAudit?.designDomainCovered ? '已覆盖' : '未检查')}</li>
        <li>平面外扩：{project.geologicalModel?.coverageAudit?.autoExtended ? `${project.geologicalModel.coverageAudit.maximumExtrapolationDistanceM ?? 0} m` : '未外扩'}</li>
        <li>VTU：{project.geologicalModel?.vtuMesh ? '已导入' : '未导入'}</li>
        <li>基坑边段：{project.excavation?.segments?.length ?? 0}</li>
        <li>地连墙：{project.retainingSystem?.diaphragmWalls?.length ?? 0}</li>
        <li>支撑：{project.retainingSystem?.supports?.length ?? 0}</li>
        <li>计算结果：{project.calculationResults.length}</li>
      </ul>
    </div>
  );
}

function StatusPill({ label, value, tone }: { label: string; value: string; tone: 'pass' | 'warn' | 'fail' | 'review' }) {
  return <span className={`statusPill ${tone}`}><em>{label}</em><strong>{value}</strong></span>;
}

function StatusCard({ title, value, detail, tone }: { title: string; value: string; detail: string; tone: 'pass' | 'warn' | 'fail' | 'review' }) {
  return <div className={`statusCard ${tone}`}><span>{title}</span><strong>{value}</strong><em>{detail}</em></div>;
}


function CadTemplatePanel({ project }: { project: Project }) {
  const [template, setTemplate] = useState<CadTemplateConfig | undefined>();
  const [draft, setDraft] = useState<CadTemplateConfig>({});
  const [status, setStatus] = useState<string>('');
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    api.getCadTemplate(project.id).then((result) => { if (alive) { setTemplate(result); setDraft(result); } }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt]);
  async function save() {
    try {
      setError(undefined); setStatus('正在保存 CAD 企业模板...');
      const updated = await api.updateCadTemplate(project.id, draft);
      setTemplate(updated); setDraft(updated); setStatus('模板已保存；后续 CAD 导出将使用新的图框、签审栏和图层标准。');
      window.setTimeout(() => setStatus(''), 2200);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); setStatus(''); }
  }
  if (error) return <div className="error">CAD 模板读取失败：{error}</div>;
  if (!template) return <div className="summaryPanel"><h3>企业 CAD 模板</h3><p className="small">正在读取图框、图号、签审栏和图层标准。</p></div>;
  const layers = draft.layerStandard ?? {};
  return <section className="summaryPanel cadTemplatePanel"><div className="sectionLead"><h3>企业 CAD 模板</h3><p className="small">配置图号、签审栏和图层。</p></div><div className="cadTemplateGrid"><label>企业名称<input value={draft.enterpriseName ?? ''} onChange={(e) => setDraft((v) => ({ ...v, enterpriseName: e.target.value }))} /></label><label>项目代号<input value={draft.projectCode ?? ''} onChange={(e) => setDraft((v) => ({ ...v, projectCode: e.target.value }))} /></label><label>图号前缀<input value={draft.sheetPrefix ?? 'S'} onChange={(e) => setDraft((v) => ({ ...v, sheetPrefix: e.target.value }))} /></label><label>阶段<input value={draft.stage ?? ''} onChange={(e) => setDraft((v) => ({ ...v, stage: e.target.value }))} /></label><label>设计<input value={draft.designer ?? ''} onChange={(e) => setDraft((v) => ({ ...v, designer: e.target.value }))} /></label><label>校核<input value={draft.checker ?? ''} onChange={(e) => setDraft((v) => ({ ...v, checker: e.target.value }))} /></label><label>审定<input value={draft.approver ?? ''} onChange={(e) => setDraft((v) => ({ ...v, approver: e.target.value }))} /></label><label>支撑图层<input value={layers.support ?? 'PIT_SUPPORT'} onChange={(e) => setDraft((v) => ({ ...v, layerStandard: { ...(v.layerStandard ?? {}), support: e.target.value } }))} /></label><label>钢筋图层<input value={layers.rebarMain ?? 'PIT_REBAR_MAIN'} onChange={(e) => setDraft((v) => ({ ...v, layerStandard: { ...(v.layerStandard ?? {}), rebarMain: e.target.value } }))} /></label><label>定位高亮图层<input value={layers.highlight ?? 'PIT_HIGHLIGHT'} onChange={(e) => setDraft((v) => ({ ...v, layerStandard: { ...(v.layerStandard ?? {}), highlight: e.target.value } }))} /></label></div><div className="actionStrip simplifiedActions"><button onClick={save}>保存 CAD 模板</button><span className="small">{status || '当前模板会进入 enterprise_template_manifest.json、drawing_package_manifest.json 和签审清单。'}</span></div></section>;
}

function CadLocatorPreview({ project, locator }: { project: Project; locator?: Record<string, unknown> }) {
  if (!locator) return null;
  const sheet = String(locator.drawingSheet ?? (locator.objectType === 'support' ? 'S-01' : locator.objectType === 'rebar' ? 'S-08' : 'S-01'));
  const objectId = String(locator.objectId ?? locator.objectCode ?? '-');
  const center = locator.center as { x?: number; y?: number } | undefined;
  const x = Number(center?.x ?? 50); const y = Number(center?.y ?? 32);
  return <section className="summaryPanel cadLocatorPanel"><div className="sectionLead"><h3>CAD 图纸定位预览</h3><p className="small">问题清单点击后同步给出图纸页、对象编号和轻量高亮预览；正式 DXF 内使用 PIT_HIGHLIGHT 图层承接后续 CAD 定位。</p></div><div className="cadSheetMock"><svg viewBox="0 0 120 80"><rect x="4" y="4" width="112" height="72" className="cadFrame"/><text x="8" y="12" className="cadText">{sheet} · {project.name}</text>{project.excavation?.outline.points?.length ? <polygon points={project.excavation.outline.points.map((p) => `${8 + p.x * 0.45},${18 + p.y * 0.45}`).join(' ')} className="cadPitOutline"/> : null}<circle cx={Math.max(12, Math.min(108, 8 + x * 0.45))} cy={Math.max(18, Math.min(68, 18 + y * 0.45))} r="3.2" className="cadLocatorPulse"/><text x="8" y="72" className="cadText">对象：{objectId} · {String(locator.objectType ?? '-')}</text></svg></div></section>;
}

function RebarDetailingPanel({ project }: { project: Project }) {
  const [data, setData] = useState<RebarDetailingResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    api.getRebarDetailing(project.id).then((result) => { if (alive) setData(result); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt]);
  if (error) return <div className="error">钢筋大样读取失败：{error}</div>;
  if (!data) return <div className="summaryPanel"><h3>钢筋大样与料表</h3><p className="small">正在读取钢筋编号、逐根几何、分节、吊装、搭接、保护层和签审清单。</p></div>;
  const bars = data.individualBars ?? [];
  return <section className="summaryPanel"><h3>钢筋施工详图</h3><p className="small">{engineeringLabel(data.detailLevel, DETAIL_LEVEL_LABELS)}</p><div className="maturityGrid"><StatusCard title="钢筋编号" value={String(data.summary.barMarkCount ?? data.entries.length)} detail="钢筋标记" tone="pass" /><StatusCard title="逐根几何" value={String(data.summary.individualBarCount ?? bars.length)} detail={`因显示上限省略 ${String(data.summary.omittedBarCount ?? 0)} 根`} tone="pass" /><StatusCard title="下料总长" value={`${data.summary.totalCutLengthM ?? '-'} m`} detail="中心线+锚固/搭接/弯钩" tone="review" /><StatusCard title="总重量" value={`${data.summary.totalWeightKg ?? '-'} kg`} detail="按 7850kg/m³ 估算" tone="review" /><StatusCard title="笼段" value={String(data.summary.cageSegmentCount ?? data.cageSegments?.length ?? 0)} detail="施工缝/分节" tone="pass" /><StatusCard title="签审清单" value={String(data.signoffChecklist?.length ?? 0)} detail={engineeringLabel(data.shopDrawingReadiness?.status ?? 'ready', ENGINEERING_STATUS_LABELS)} tone="pass" /></div><table className="table compactTable"><thead><tr><th>编号</th><th>宿主</th><th>类型</th><th>直径</th><th>形状代码</th><th>数量</th><th>单长</th><th>重量</th></tr></thead><tbody>{data.entries.slice(0, 12).map((item) => <tr key={item.barMark}><td>{item.barMark}</td><td>{item.hostCode}</td><td>{engineeringLabel(item.barType, REBAR_TYPE_LABELS)}</td><td>D{item.diameterMm}</td><td>{item.shapeCode}</td><td>{item.quantity}</td><td>{item.singleLengthM}m</td><td>{item.totalWeightKg}kg</td></tr>)}</tbody></table><h4>施工详图深化状态</h4><table className="table compactTable"><thead><tr><th>项目</th><th>状态</th><th>证据数</th></tr></thead><tbody>{(data.signoffChecklist ?? []).map((item) => <tr key={String(item.id)}><td>{String(item.label ?? item.item)}</td><td>{engineeringLabel(item.status, ENGINEERING_STATUS_LABELS)}</td><td>{String(item.evidenceCount ?? '-')}</td></tr>)}</tbody></table><h4>逐根钢筋几何样本</h4><table className="table compactTable"><thead><tr><th>钢筋 ID</th><th>宿主</th><th>类型</th><th>点数</th><th>中心线</th><th>锚固</th><th>搭接</th><th>弯钩</th><th>下料</th></tr></thead><tbody>{bars.slice(0, 12).map((bar) => <tr key={bar.barId}><td>{bar.barId}</td><td>{bar.hostCode}</td><td>{engineeringLabel(bar.barType, REBAR_TYPE_LABELS)}</td><td>{bar.points.length}</td><td>{bar.centerlineLengthM}m</td><td>{bar.anchorageLengthM}m</td><td>{bar.lapLengthM}m</td><td>{bar.hookLengthM}m</td><td>{bar.cutLengthM}m</td></tr>)}</tbody></table></section>;
}

function BenchmarkPanel() {
  const [cases, setCases] = useState<BenchmarkCaseSpec[]>([]);
  const [result, setResult] = useState<BenchmarkRunResult | undefined>();
  const [busyCase, setBusyCase] = useState<string | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => { api.listBenchmarks().then((data) => setCases(data.cases)).catch((err) => setError(err instanceof Error ? err.message : String(err))); }, []);
  async function run(caseId?: string) {
    try { setBusyCase(caseId ?? 'all'); setError(undefined); setResult(await api.runBenchmarks(caseId, false)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusyCase(undefined); }
  }
  async function downloadPackage() {
    const response = await fetch(api.benchmarkPackageUrl());
    if (!response.ok) { setError(await response.text()); return; }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'pitguard_v2_3_0_public_benchmark_cases.zip'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }
  return <section className="summaryPanel benchmarkPanel"><h3>公开论文典型基坑规范算法回归算例</h3><p className="small">不接入有限元；使用公开论文摘要给出的深度、面积、墙深和支撑道数，构建可复现的规范算法流程回归样例。</p>{error && <div className="error">{error}</div>}<div className="actionStrip simplifiedActions"><button onClick={() => run()} disabled={Boolean(busyCase)}>{busyCase === 'all' ? '运行中...' : '运行全部算例并入库'}</button><button className="secondary" onClick={downloadPackage}>下载算例成果包</button></div><table className="table compactTable"><thead><tr><th>算例</th><th>深度</th><th>平面</th><th>公开数据依据</th><th>操作</th></tr></thead><tbody>{cases.map((item) => <tr key={item.caseId}><td><strong>{item.caseId}</strong><br/><span className="small">{item.name}</span></td><td>{item.depthM}m</td><td>{item.lengthM}×{item.widthM}m</td><td>{item.publicDataBasis}</td><td><button className="secondary tiny" disabled={Boolean(busyCase)} onClick={() => run(item.caseId)}>{busyCase === item.caseId ? '运行中' : '运行'}</button></td></tr>)}</tbody></table>{result && <div className="small">最近运行：{result.caseId ?? `全部 ${result.caseCount} 个算例`}；项目ID：{result.projectId ?? '-'}；trace：{result.traceCount ?? '-'}</div>}</section>;
}

function ExportCard({ title, description, href, button, projectId, taskOperation }: { title: string; description: string; href: string; button: string; projectId?: string; taskOperation?: BackendTaskOperation }) {
  const [state, setState] = useState<{ running: boolean; progress: number; phase: string; error?: string }>({ running: false, progress: 0, phase: '' });
  const exportActivityId = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (state.running && !exportActivityId.current) {
      exportActivityId.current = beginGlobalActivity({
        label: `正在生成${title}`,
        phase: state.phase || '提交导出请求',
        expectedMs: taskOperation ? 120000 : 15000,
        blocking: true,
        progress: Math.max(2, state.progress),
        path: projectId ? `local://project/${projectId}/export/${taskOperation ?? 'direct'}` : href,
      });
    }
    if (!exportActivityId.current) return;
    updateGlobalActivity(exportActivityId.current, {
      phase: state.error || state.phase || '后端正在生成成果',
      progress: Math.max(2, Math.min(100, state.progress || 6)),
      blocking: true,
    });
    if (state.error) {
      finishGlobalActivity(exportActivityId.current, { ok: false, error: state.error, progress: state.progress });
      exportActivityId.current = undefined;
    } else if (!state.running && state.progress >= 100) {
      finishGlobalActivity(exportActivityId.current, { ok: true, phase: state.phase || '成果已生成', progress: 100 });
      exportActivityId.current = undefined;
    }
  }, [state, title, href, projectId, taskOperation]);

  async function download() {
    try {
      setState({ running: true, progress: 12, phase: '提交导出请求' });
      if (projectId && taskOperation) {
        let task = await api.createTask(projectId, taskOperation, {});
        const terminalStatuses = new Set(['success', 'failed', 'cancelled', 'interrupted']);
        const startedAt = Date.now();
        let transientFailures = 0;
        while (!terminalStatuses.has(task.status)) {
          setState({ running: true, progress: Math.max(6, task.progress), phase: `${task.currentStep} · ${task.status}` });
          await new Promise((resolve) => window.setTimeout(resolve, document.hidden ? 4000 : 1000));
          try {
            task = await api.getTask(task.id);
            transientFailures = 0;
          } catch (pollError) {
            transientFailures += 1;
            const message = pollError instanceof Error ? pollError.message : String(pollError);
            setState({ running: true, progress: Math.max(6, task.progress), phase: `API暂时不可用，正在重连（${transientFailures}/8）：${message}` });
            if (transientFailures >= 8) throw new Error('连续8次无法读取导出任务状态。后台任务不会重复提交，可刷新页面后从任务记录继续下载。');
          }
          if (Date.now() - startedAt > 40 * 60 * 1000) throw new Error('导出任务轮询超过40分钟，独立worker会按硬超时受控终止。');
        }
        if (task.status !== 'success') throw new Error(task.error || `任务状态：${task.status}`);
        setState({ running: true, progress: 86, phase: '浏览器准备下载' });
        const response = await fetch(api.taskDownloadUrl(task.id), { method: 'GET' });
        if (!response.ok) throw new Error(await response.text());
        const blob = await response.blob();
        const filename = String(task.result?.filename || `${title.replace(/\s+/g, '_')}.zip`);
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        setState({ running: false, progress: 100, phase: `已生成：${filename}` });
        window.setTimeout(() => setState({ running: false, progress: 0, phase: '' }), 1800);
        return;
      }
      const response = await fetch(href, { method: 'GET' });
      if (!response.ok) {
        const text = await response.text().catch(() => '');
        throw new Error(text || `${response.status} ${response.statusText}`);
      }
      setState({ running: true, progress: 58, phase: '后端正在生成文件或读取缓存' });
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') ?? '';
      const matched = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
      const fallback = `${title.replace(/\s+/g, '_')}.${href.includes('report') ? 'docx' : href.includes('json') ? 'json' : href.includes('drawings') ? 'zip' : 'ifc'}`;
      const filename = decodeURIComponent((matched?.[1] ?? fallback).replace(/\"/g, '').trim());
      setState({ running: true, progress: 82, phase: '浏览器准备下载' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      setState({ running: false, progress: 100, phase: `已生成：${filename}` });
      window.setTimeout(() => setState({ running: false, progress: 0, phase: '' }), 1800);
    } catch (err) {
      setState({ running: false, progress: 0, phase: '导出失败', error: err instanceof Error ? err.message : String(err) });
    }
  }
  return <div className="exportCard"><h3>{title}</h3><p>{description}</p><button onClick={download} disabled={state.running}>{state.running ? '生成中...' : button}</button>{(state.running || state.phase || state.error) && <div className="downloadProgress"><div><em style={{ width: `${Math.max(2, Math.min(100, state.progress || 6))}%` }} /></div><span>{state.error ? `失败：${state.error}` : state.phase}</span></div>}</div>;
}

function calculationRequiresRefresh(project: Project) {
  const state = (project.advancedEngineering?.calculationState ?? {}) as Record<string, unknown>;
  return Boolean(state.requiresRecalculation);
}

function getLatestResult(project: Project) {
  // A result belongs to one immutable geometry/topology snapshot.  Hiding stale
  // values here prevents workflow cards, gates and A/B/C panels from mixing a
  // newly adopted support system with an earlier calculation.
  if (calculationRequiresRefresh(project)) return undefined;
  return project.calculationResults.length ? project.calculationResults[project.calculationResults.length - 1] : undefined;
}

function overallFlowLabel(steps: WorkflowStep[]) {
  const done = steps.filter((s) => s.status === 'done' || s.status === 'warning').length;
  return `${done}/${steps.length}`;
}


function requirementStatuses(key: WorkflowStepKey, project: Project): { label: string; done: boolean }[] {
  const latest = getLatestResult(project);
  const ret = project.retainingSystem;
  if (key === 'settings') return [
    { label: '项目已创建', done: Boolean(project.id) },
    { label: '单位体系已建立', done: Boolean(project.unitSystem?.length && project.unitSystem?.force) },
    { label: '设计参数可读取', done: Boolean(project.designSettings?.ruleSet) }
  ];
  if (key === 'boreholes') return [
    { label: '至少 1 个钻孔', done: project.boreholes.length > 0 },
    { label: '至少 1 个地层', done: project.strata.length > 0 },
    { label: '关键土参数已合并', done: project.strata.length > 0 }
  ];
  if (key === 'geology') return [
    { label: '已导入钻孔', done: project.boreholes.length > 0 },
    { label: '已生成地层面', done: hasGeologicalSurfacePreview(project) },
    { label: '可提取代表性剖面', done: Boolean(hasGeologicalSurfacePreview(project) || project.geologicalModel?.vtuMesh) },
    { label: '覆盖围护结构及施工影响区', done: project.geologicalModel?.coverageAudit?.designDomainCovered !== false && hasGeologicalSurfacePreview(project) }
  ];
  if (key === 'excavation') return [
    { label: '轮廓闭合', done: Boolean(project.excavation?.outline?.closed) },
    { label: '坑底低于坑顶', done: Boolean(project.excavation && project.excavation.bottomElevation < project.excavation.topElevation) },
    { label: '已生成边段', done: Boolean(project.excavation?.segments?.length) }
  ];
  if (key === 'retaining') return [
    { label: '已有基坑边段', done: Boolean(project.excavation?.segments?.length) },
    { label: '已生成地连墙', done: Boolean(ret?.diaphragmWalls?.length) },
    { label: '已生成支撑体系', done: Boolean(ret?.supports?.length) }
  ];
  if (key === 'calculation') return [
    { label: '已生成施工工况', done: project.calculationCases.length > 0 },
    { label: '已运行计算', done: Boolean(latest) },
    { label: '已输出校核结果', done: Boolean(latest?.checks?.length) }
  ];
  if (key === 'assurance') return [
    { label: 'Assurance API 可读取', done: Boolean(latest?.formalReportGate || latest?.checkSummary) },
    { label: '无硬性 fail', done: Boolean(latest && (latest.checkSummary?.fail ?? 0) === 0) },
    { label: '闭环状态独立于功能完成度', done: Boolean(latest?.formalReportGate) }
  ];
  return [
    { label: '已有围护结构', done: Boolean(ret?.supports?.length) },
    { label: '已有计算结果', done: Boolean(latest) },
    { label: '可导出 IFC/DOCX/JSON', done: Boolean(ret?.supports?.length) }
  ];
}

function OperationProgress({ operation, onDismiss }: { operation: ActiveOperation; onDismiss: () => void }) {
  return (
    <div className="operationPanel">
      <div className="operationHeader"><div><strong>{operation.title}</strong><span>{operation.detail}</span></div><button className="secondary tiny" onClick={onDismiss}>收起</button></div>
      <div className="operationBar"><em style={{ width: `${Math.max(2, Math.min(100, operation.progress))}%` }} /></div>
      <div className="operationPhases">
        {operation.phases.map((phase, index) => <span key={`${phase.label}-${index}`} className={`phase ${phase.status}`}><b>{index + 1}</b>{phase.label}{phase.detail ? <small>{phase.detail}</small> : null}</span>)}
      </div>
      {operation.logs?.length ? <pre className="operationLogs">{operation.logs.join('\n')}</pre> : null}
    </div>
  );
}

function statusText(status: StepStatus) {
  if (status === 'done') return '已完成';
  if (status === 'ready') return '可执行';
  if (status === 'blocked') return '前置不足';
  if (status === 'warning') return '需复核';
  return '存在错误';
}
