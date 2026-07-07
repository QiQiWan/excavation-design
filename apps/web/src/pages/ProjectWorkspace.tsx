import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { api } from '../api/client';
import BoreholeImport from '../components/BoreholeImport';
import ExcavationEditor from '../components/ExcavationEditor';
import GeologyViewer from '../viewers/GeologyViewer';
import RetainingSystemViewer from '../viewers/RetainingSystemViewer';
import ResultViewer from '../viewers/ResultViewer';
import Engineering3DViewer from '../viewers/Engineering3DViewer';
import RebarIfcViewer from '../viewers/RebarIfcViewer';
import type { AssuranceResult, BenchmarkCaseSpec, BenchmarkRunResult, CadTemplateConfig, IssueCenterItem, IssueCenterResult, PitTask, Project, RebarDetailingResult } from '../types/domain';

type WorkflowStepKey = 'settings' | 'boreholes' | 'geology' | 'excavation' | 'retaining' | 'calculation' | 'assurance' | 'export';
type StepStatus = 'done' | 'ready' | 'blocked' | 'warning' | 'error';

type OperationPhaseStatus = 'pending' | 'running' | 'done' | 'error';

interface OperationPhase { label: string; detail?: string; status: OperationPhaseStatus }

interface ActiveOperation { title: string; detail?: string; progress: number; phases: OperationPhase[]; logs?: string[] }

interface WorkflowAction { label: string; detail?: string; action: () => Promise<unknown> }
type BackendTaskOperation = 'calculation_full' | 'candidate_comparison' | 'export_ifc_light' | 'export_ifc_analysis' | 'export_ifc_construction_visual' | 'export_ifc_detailed' | 'export_report' | 'export_drawings_cad' | 'export_drawings_svg' | 'export_json' | 'export_trace' | 'export_issue_report' | 'export_benchmark_cases' | 'full_delivery';

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

  useEffect(() => {
    setCurrent(project);
  }, [project]);

  const steps = useMemo(() => buildWorkflowSteps(current), [current]);
  const activeStep = steps.find((step) => step.key === active) ?? steps[0];
  const activeIndex = steps.findIndex((step) => step.key === active);
  const nextStep = activeIndex >= 0 ? steps[activeIndex + 1] : undefined;
  const previousStep = activeIndex > 0 ? steps[activeIndex - 1] : undefined;
  const latestResult = getLatestResult(current);
  const failCount = latestResult?.checkSummary?.fail ?? 0;
  const warningCount = latestResult?.checkSummary?.warning ?? 0;
  const manualReviewCount = latestResult?.checkSummary?.manualReview ?? latestResult?.checkSummary?.manual_review ?? 0;

  async function refresh() {
    const updated = await api.getProject(current.id);
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
    try {
      setBusy(title);
      setError(undefined);
      setOperation({ title, detail: '后端任务队列已接管该操作，正在轮询真实进度。', progress: 2, phases: [{ label: '提交任务', status: 'running' }] });
      let task = await api.createTask(current.id, operationName, payload ?? {});
      setOperation({ title, detail: task.currentStep, progress: task.progress, logs: task.logs, phases: [{ label: task.currentStep || title, status: 'running' }] });
      const started = Date.now();
      while (!['success', 'failed', 'cancelled'].includes(task.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 850));
        task = await api.getTask(task.id);
        setOperation({
          title,
          detail: `${task.currentStep} · ${task.status}`,
          progress: task.progress,
          logs: task.logs?.slice(-8),
          phases: [{ label: task.currentStep || title, status: task.status === 'running' || task.status === 'queued' ? 'running' : task.status === 'success' ? 'done' : 'error' }]
        });
        if (Date.now() - started > 10 * 60 * 1000) throw new Error('任务轮询超时，请检查后端日志。');
      }
      if (task.status !== 'success') throw new Error(task.error || `任务状态：${task.status}`);
      if (autoDownload && task.result?.filePath) await downloadTaskFile(task);
      await refresh();
      setOperation({ title, detail: autoDownload ? '文件已生成并开始下载，项目数据已刷新。' : '任务完成，项目数据已刷新。', progress: 100, logs: task.logs?.slice(-8), phases: [{ label: '完成', status: 'done' }] });
      window.setTimeout(() => setOperation(undefined), 1800);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setOperation((prev) => prev ? { ...prev, detail: message, phases: prev.phases.map((phase) => ({ ...phase, status: phase.status === 'done' ? 'done' : 'error' })) } : prev);
    } finally {
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
    <main className="page workflowPage">
      <div className="workspaceHeader card">
        <div className="workspaceTitle">
          <button className="secondary" onClick={onBack}>返回项目列表</button>
          <div>
            <h2>{current.name}</h2>
            <p>{current.location ?? '未设置地点'} · 基坑围护结构流程化设计工作台</p>
          </div>
        </div>
        <div className="workspaceBadges">
          <StatusPill label="流程" value={overallFlowLabel(steps)} tone={steps.every((s) => s.status === 'done' || s.status === 'warning') ? 'pass' : 'warn'} />
          <StatusPill label="Fail" value={String(failCount)} tone={failCount > 0 ? 'fail' : 'pass'} />
          <StatusPill label="Warning" value={String(warningCount)} tone={warningCount > 0 ? 'warn' : 'pass'} />
          <StatusPill label="人工复核" value={String(manualReviewCount)} tone="review" />
        </div>
      </div>

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
          <p className="small">按设计流程推进。步骤状态来自当前项目数据，不再依赖用户记忆操作顺序。</p>
          <ol className="workflowStepper">
            {steps.map((step) => (
              <li key={step.key} className={`workflowStep ${active === step.key ? 'active' : ''} ${step.status}`}>
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
          <ProjectTreeSummary project={current} />
        </aside>

        <section className="workflowMain card">
          <OperatorDashboard project={current} steps={steps} />
          <NextActionPanel activeStep={activeStep} nextStep={nextStep} project={current} />
          <EngineeringDecisionBoard project={current} steps={steps} onJump={setActive} />
          <StepHeader step={activeStep} project={current} />
          {selectedLocator && <LocatorBanner locator={selectedLocator} onClear={() => setSelectedLocator(undefined)} />}
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
          />
          <div className="workflowFooter">
            <button className="secondary" onClick={goPrevious} disabled={!previousStep}>上一步</button>
            <button onClick={goNext} disabled={!nextStep}>下一步：{nextStep?.title ?? '已到末尾'}</button>
          </div>
        </section>
      </div>
    </main>
  );
}



function LocatorBanner({ locator, onClear }: { locator: Record<string, unknown>; onClear: () => void }) {
  return (
    <div className="locatorBanner">
      <div>
        <strong>对象级定位</strong>
        <span>{String(locator.objectType ?? '-')} · {String(locator.objectId ?? locator.objectCode ?? '-')} · {String(locator.targetPanel ?? locator.workflowStep ?? '-')}</span>
        <em>{locator.center ? `坐标 ${JSON.stringify(locator.center)}` : '无坐标'}{locator.drawingSheet ? ` · CAD ${String(locator.drawingSheet)}` : ''}</em>
        {locator.message ? <p>{String(locator.message)}</p> : null}
      </div>
      <button className="secondary tiny" onClick={onClear}>清除定位</button>
    </div>
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
    { label: '地质模型', value: project.geologicalModel?.surfaces?.length ? `${project.geologicalModel.surfaces.length} 个地层面` : '未生成', done: Boolean(project.geologicalModel?.surfaces?.length), key: 'geology' as const },
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
      status: project.geologicalModel?.surfaces?.length ? (project.geologicalModel?.warnings?.length ? 'warning' : 'done') : (project.boreholes.length ? 'ready' : 'blocked'),
      message: project.geologicalModel?.surfaces?.length ? `${project.geologicalModel.surfaces.length} 个地层面` : '需要先导入钻孔并生成模型'
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

function StepHeader({ step, project }: { step: WorkflowStep; project: Project }) {
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
      <div className="requiredList">
        {requirementStatuses(step.key, project).map((item) => <span key={item.label} className={item.done ? 'reqDone' : 'reqOpen'}>{item.done ? '✓' : '○'} {item.label}</span>)}
      </div>
      {project.messages?.length > 0 && <div className="small">项目消息：{project.messages.slice(-2).join('；')}</div>}
    </header>
  );
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
  onLocateIssue
}: {
  active: WorkflowStepKey;
  project: Project;
  onRefresh: () => void;
  runStep: (label: string, step: () => Promise<unknown>) => Promise<void>;
  runWorkflow: (title: string, actions: WorkflowAction[]) => Promise<void>;
  runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>;
  importVtu: (file?: File) => Promise<void>;
  vtuMessage?: string;
  selectedLocator?: Record<string, unknown>;
  onLocateIssue: (issue: IssueCenterItem) => void;
}) {
  if (active === 'settings') return <SettingsStep project={project} />;
  if (active === 'boreholes') return <BoreholeImport project={project} onImported={onRefresh} />;
  if (active === 'geology') return <GeologyStep project={project} runStep={runStep} importVtu={importVtu} vtuMessage={vtuMessage} />;
  if (active === 'excavation') return <ExcavationEditor project={project} onSaved={onRefresh} />;
  if (active === 'retaining') return <RetainingStep project={project} runStep={runStep} selectedLocator={selectedLocator} />;
  if (active === 'calculation') return <CalculationStep project={project} runStep={runStep} runWorkflow={runWorkflow} runTask={runTask} selectedLocator={selectedLocator} />;
  if (active === 'assurance') return <AssurancePanel project={project} onLocateIssue={onLocateIssue} />;
  return <ExportPanel project={project} runTask={runTask} selectedLocator={selectedLocator} />;
}

function SettingsStep({ project }: { project: Project }) {
  return (
    <div className="stepGrid">
      <div className="summaryPanel">
        <h3>项目基础参数</h3>
        <dl className="definitionGrid">
          <dt>项目名称</dt><dd>{project.name}</dd>
          <dt>地点</dt><dd>{project.location ?? '-'}</dd>
          <dt>长度单位</dt><dd>{project.unitSystem.length}</dd>
          <dt>力单位</dt><dd>{project.unitSystem.force}</dd>
          <dt>应力单位</dt><dd>{project.unitSystem.stress}</dd>
          <dt>规则集</dt><dd>{project.designSettings.ruleSet}</dd>
        </dl>
      </div>
      <div className="summaryPanel">
        <h3>设计控制参数</h3>
        <dl className="definitionGrid">
          <dt>安全等级</dt><dd>{project.designSettings.safetyGrade}</dd>
          <dt>环境等级</dt><dd>{project.designSettings.environmentGrade}</dd>
          <dt>地下水位</dt><dd>{project.designSettings.groundwaterLevel} m</dd>
          <dt>坑内水位</dt><dd>{project.designSettings.groundwaterLevelInside ?? '-'} m</dd>
          <dt>地面超载</dt><dd>{project.designSettings.surcharge} kPa</dd>
          <dt>最小边段</dt><dd>{project.designSettings.minimumSegmentLength} m</dd>
        </dl>
      </div>
      <div className="warning fullWidth">当前前端流程已按工程设计顺序重构。参数编辑仍采用后端默认值，下一阶段应增加项目设置表单和规范版本选择器。</div>
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

function RetainingStep({ project, runStep, selectedLocator }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void>; selectedLocator?: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const [weightPreset, setWeightPreset] = useState<'balanced' | 'fewer_columns' | 'low_axial_force' | 'muck_path_priority'>('balanced');
  const defaultWeights: Record<string, number> = {
    spacingDeviation: 20,
    spanLength: 16,
    obstacleConflict: 34,
    supportCrossing: 40,
    columnCount: 7,
    muckPathContinuity: 8,
    axialPeakProxy: 11,
    symmetry: 10,
    endpointValidity: 18,
    replacementContinuity: 8
  };
  const objectiveMeta = [
    ['spacingDeviation', '间距偏差'], ['spanLength', '跨长'], ['obstacleConflict', '障碍冲突'], ['supportCrossing', '支撑交叉'], ['columnCount', '立柱数量'], ['muckPathContinuity', '出土通道'], ['axialPeakProxy', '轴力峰值'], ['symmetry', '平面对称'], ['endpointValidity', '端点有效'], ['replacementContinuity', '换撑连续']
  ] as const;
  const presetWeights: Record<string, Record<string, number>> = {
    balanced: {},
    fewer_columns: { columnCount: 28, spanLength: 18 },
    low_axial_force: { axialPeakProxy: 32, spanLength: 25, spacingDeviation: 20 },
    muck_path_priority: { muckPathContinuity: 34, obstacleConflict: 48, supportCrossing: 44 }
  };
  const presetToWeights = (preset: keyof typeof presetWeights) => ({ ...defaultWeights, ...presetWeights[preset] });
  const [weights, setWeights] = useState<Record<string, number>>(presetToWeights('balanced'));
  const [lockedIds, setLockedIds] = useState<string[]>(project.retainingSystem?.supports?.filter((s) => s.optimizationLocked).map((s) => s.id) ?? []);
  const [lockedStartIds, setLockedStartIds] = useState<string[]>(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedStart).map((s) => s.id) ?? []);
  const [lockedEndIds, setLockedEndIds] = useState<string[]>(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedEnd).map((s) => s.id) ?? []);
  const [lockedLevels, setLockedLevels] = useState<number[]>([]);
  const [lockedObstacleIds, setLockedObstacleIds] = useState<string[]>(project.excavation?.obstacles?.filter((o) => o.optimizationLocked && o.id).map((o) => o.id!) ?? []);
  useEffect(() => {
    setLockedIds(project.retainingSystem?.supports?.filter((s) => s.optimizationLocked).map((s) => s.id) ?? []);
    setLockedStartIds(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedStart).map((s) => s.id) ?? []);
    setLockedEndIds(project.retainingSystem?.supports?.filter((s) => s.optimizationLockedEnd).map((s) => s.id) ?? []);
    const savedLevels = (project.retainingSystem?.optimizationLocks ?? []).filter((item) => String(item.targetType ?? item.target_type ?? '') === 'support_level' && item.locked !== false).map((item) => Number(item.levelIndex ?? item.level_index)).filter(Number.isFinite);
    setLockedLevels(Array.from(new Set(savedLevels)));
    setLockedObstacleIds(project.excavation?.obstacles?.filter((o) => o.optimizationLocked && o.id).map((o) => o.id!) ?? []);
  }, [project.retainingSystem, project.excavation]);
  const runAuto = () => runStep('正在生成围护结构体系', async () => { await api.autoWall(project.id); await api.autoSupports(project.id); });
  const applyPreset = (value: typeof weightPreset) => {
    setWeightPreset(value);
    setWeights(presetToWeights(value));
  };
  const toggle = (setter: Dispatch<SetStateAction<string[]>>, id: string) => setter((prev) => prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]);
  const toggleLevel = (level: number) => setLockedLevels((prev) => prev.includes(level) ? prev.filter((item) => item !== level) : [...prev, level]);
  const levels = Array.from(new Set((project.retainingSystem?.supports ?? []).map((s) => s.levelIndex))).sort((a, b) => a - b);
  const candidates = project.retainingSystem?.supportLayoutRepair?.candidates ?? [];
  const previewRows = candidates.map((c) => {
    const terms = c.objectiveTerms ?? {};
    const penalty = Object.entries(weights).reduce((sum, [key, value]) => sum + value * Math.min(Number(terms[key] ?? 0), 3), 0)
      + (c.failCount ?? 0) * 35 + (c.warningCount ?? 0) * 1.5 + (c.hardConstraints?.passed ? 0 : 65);
    return { id: c.id ?? String(c.rank), rank: c.rank, oldScore: c.score, penalty, previewScore: Math.max(0, Math.min(100, 100 - penalty)), supportCount: c.supportCount, columnCount: c.columnCount };
  }).sort((a, b) => b.previewScore - a.previewScore).slice(0, 5);
  return (
    <div>
      <div className="actionStrip simplifiedActions">
        <button onClick={runAuto} disabled={!project.excavation}>一键生成围护体系</button>
        <button className="secondary" onClick={() => setOpen(true)}>高级操作</button>
        <span className="small">常用路径：一键生成地连墙、围檩、水平支撑、节点和立柱。候选优化、权重和支撑锁定放在高级操作中。</span>
      </div>
      {open && <div className="drawerBackdrop" onClick={() => setOpen(false)}><aside className="sideDrawer wideDrawer" onClick={(e) => e.stopPropagation()}><div className="drawerHeader"><h3>围护结构高级操作</h3><button className="secondary" onClick={() => setOpen(false)}>关闭</button></div>
        <button onClick={() => runStep('正在生成地下连续墙', () => api.autoWall(project.id))} disabled={!project.excavation}>仅生成地连墙</button>
        <button onClick={() => runStep('正在生成水平支撑和立柱', () => api.autoSupports(project.id))} disabled={!project.excavation}>仅生成支撑/立柱</button>
        <div className="drawerSection">
          <h4>支撑优化权重可视化</h4>
          <label className="stackedLabel">优化偏好
            <select value={weightPreset} onChange={(event) => applyPreset(event.target.value as any)}>
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
          <button onClick={() => runStep('正在按约束优化器生成候选支撑方案', () => api.optimizeSupports(project.id, { preset: weightPreset, objectiveWeights: weights }))} disabled={!project.excavation}>按当前权重生成 3-5 个候选方案</button>
          <p className="small">优化器以支撑线位置为变量，硬约束包括交叉、障碍穿越、端点吸附、立柱落点和换撑路径；软目标由当前滑块权重控制。</p>
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
      <RetainingSystemViewer project={project} highlightLocator={selectedLocator} />
    </div>
  );
}

function CalculationStep({ project, runStep, runWorkflow, runTask, selectedLocator }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void>; runWorkflow: (title: string, actions: WorkflowAction[]) => Promise<void>; runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; selectedLocator?: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const runAll = () => runTask('一键计算校核', 'calculation_full', { topN: 3 });
  return (
    <div>
      <div className="actionStrip simplifiedActions">
        <button onClick={runAll} disabled={!project.retainingSystem}>一键计算校核</button>
        <button className="secondary" onClick={() => setOpen(true)}>高级操作</button>
        <span className="small">常用路径：生成施工工况并运行强度、刚度、稳定性复核。</span>
      </div>
      {open && <div className="drawerBackdrop" onClick={() => setOpen(false)}><aside className="sideDrawer" onClick={(e) => e.stopPropagation()}><div className="drawerHeader"><h3>计算高级操作</h3><button className="secondary" onClick={() => setOpen(false)}>关闭</button></div><button onClick={() => runStep('正在生成施工工况', () => api.buildCases(project.id))} disabled={!project.retainingSystem}>仅生成工况</button><button onClick={() => runStep('正在运行计算和规范子集校核', () => api.runCalculation(project.id))} disabled={!project.retainingSystem}>仅运行计算</button><button onClick={() => runTask('正在并行计算前 3 个候选方案', 'candidate_comparison', { topN: 3 })} disabled={!project.retainingSystem}>并行计算前 3 个候选方案</button><p className="small">用于调试施工阶段、复算内力或单独刷新候选 A/B/C 完整计算比选。常规设计建议使用一键计算校核。</p></aside></div>}
      <ResultViewer project={project} runStep={runStep} highlightLocator={selectedLocator} />
      <CalculationTracePanel project={project} />
    </div>
  );
}

function CalculationTracePanel({ project }: { project: Project }) {
  const [trace, setTrace] = useState<import('../types/domain').CalculationTraceResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    if (!project.calculationResults.length) return () => { alive = false; };
    api.getCalculationTrace(project.id).then((data) => { if (alive) setTrace(data); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.calculationResults.length]);
  if (!project.calculationResults.length) return null;
  if (error) return <div className="error">计算追溯链读取失败：{error}</div>;
  if (!trace) return <div className="summaryPanel"><h3>计算追溯链</h3><p className="small">正在读取工况—构件—截面—公式—规范条文追溯链...</p></div>;
  return (
    <section className="tracePanel">
      <div className="issueCenterHeader"><div><h3>计算追溯链 · V2.3.0</h3><p>每个控制值都绑定到工况、构件、公式、规范条文和结果路径，便于复核和定位。</p></div><strong>{trace.summary.controlPathCompleteness}%</strong></div>
      <div className="maturityGrid">
        <StatusCard title="追溯条目" value={String(trace.summary.traceCount)} detail="控制值、规范筛查与稳定性条目" tone={trace.summary.traceCount ? 'pass' : 'warn'} />
        <StatusCard title="控制对象" value={String(trace.summary.governingObjectCount)} detail="可定位构件/截面数量" tone={trace.summary.governingObjectCount ? 'pass' : 'warn'} />
        <StatusCard title="规范引用" value={String(trace.summary.codeReferenceCount)} detail="公式和条文来源" tone={trace.summary.codeReferenceCount ? 'pass' : 'warn'} />
        <StatusCard title="状态" value={trace.summary.status} detail={trace.summary.message} tone={trace.summary.status === 'pass' ? 'pass' : trace.summary.status === 'fail' ? 'fail' : 'warn'} />
      </div>
      <table className="table compactTable"><thead><tr><th>状态</th><th>工况</th><th>对象</th><th>控制项</th><th>需求/限值</th><th>公式</th><th>规范</th></tr></thead><tbody>
        {trace.entries.slice(0, 36).map((item) => <tr key={item.id} className={`issue-${item.status}`}><td>{item.status}</td><td>{item.stageName}</td><td>{item.objectId ?? '-'}</td><td>{item.title}</td><td>{item.demandValue ?? '-'} / {item.capacityValue ?? '-'} {item.unit ?? ''}</td><td>{item.formula ?? '-'}</td><td>{item.codeReference ?? '-'}</td></tr>)}
      </tbody></table>
      <ul className="small traceNotes">{trace.notes.map((note) => <li key={note}>{note}</li>)}</ul>
    </section>
  );
}

function AssurancePanel({ project, onLocateIssue }: { project: Project; onLocateIssue: (issue: IssueCenterItem) => void }) {
  const [assurance, setAssurance] = useState<AssuranceResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  useEffect(() => {
    let alive = true;
    api.getAssurance(project.id).then((data) => { if (alive) setAssurance(data); }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.calculationResults.length]);
  if (error) return <div className="error">{error}</div>;
  if (!assurance) return <div className="small">正在读取完成度分析...</div>;
  const capability = assurance.capabilityCompleteness ?? assurance.completionPercent;
  return (
    <div>
      <div className="assuranceCards">
        <StatusCard title="功能完成度" value={`${capability}%`} detail="只描述软件功能路径覆盖率；缺项见下方列表" tone={capability >= 100 ? 'pass' : 'warn'} />
        <StatusCard title="软件流程" value={assurance.softwareFlowComplete ? '完整' : '未完整'} detail={assurance.softwareFlowComplete ? '资料-设计-计算-成果路径完整' : `仍缺 ${assurance.softwareFlowMissingItems?.length ?? 0} 个流程项`} tone={assurance.softwareFlowComplete ? 'pass' : 'warn'} />
        <StatusCard title="工程校核" value={assurance.engineeringCheckStatus ?? 'manual_review'} detail={`fail=${assurance.failureCount}，人工复核=${assurance.manualReviewCount}`} tone={assurance.engineeringCheckStatus === 'fail' ? 'fail' : assurance.engineeringCheckStatus === 'pass' ? 'pass' : 'review'} />
        <StatusCard title="正式出图闸门" value={assurance.officialIssueGateAllowed ? '可正式出图' : '暂不建议正式出图'} detail={assurance.officialIssueGateHeadline ?? assurance.officialIssueGateDetail ?? '查看阻断/警告/缺项'} tone={assurance.officialIssueGateStatus === 'fail' ? 'fail' : assurance.officialIssueGateStatus === 'pass' ? 'pass' : 'warn'} />
      </div>
      <IssueCenterPanel project={project} onLocateIssue={onLocateIssue} />
      {assurance.supportLayoutQuality && <div className="summaryPanel"><h3>支撑布置评分</h3><p>{assurance.supportLayoutQuality.summary}</p><p className="small">状态：{assurance.supportLayoutQuality.status}；评分：{assurance.supportLayoutQuality.score}</p></div>}
      {assurance.ifcCompatibility && <div className="summaryPanel"><h3>IFC 兼容性自检</h3><p>{assurance.ifcCompatibility.summary}</p><p className="small">raw unicode：{String(assurance.ifcCompatibility.rawUnicodeFound)}；零尺寸：{assurance.ifcCompatibility.zeroDimensionCount ?? 0}；材料缺失：{assurance.ifcCompatibility.missingMaterialAssociationCount ?? 0}</p></div>}
      <div className="stepGrid">
        <IssueTable title="出图阻断项" items={assurance.officialIssueBlockingItems ?? []} empty="没有硬性阻断项。" />
        <IssueTable title="出图警告项" items={assurance.officialIssueWarningItems ?? []} empty="没有警告项。" />
        <IssueTable title="流程/成果缺项" items={assurance.officialIssueMissingItems ?? []} empty="没有缺项。" />
      </div>
      {assurance.softwareFlowMissingItems?.length ? <><h3>软件流程缺项</h3><table className="dataTable"><thead><tr><th>ID</th><th>验收项</th><th>状态</th><th>说明</th></tr></thead><tbody>{assurance.softwareFlowMissingItems.map((item) => <tr key={item.id} className={`check-${item.status}`}><td>{item.id}</td><td>{item.title}</td><td>{item.status}</td><td>{item.message}</td></tr>)}</tbody></table></> : null}
      <h3>完整验收矩阵</h3>
      <table className="dataTable">
        <thead><tr><th>ID</th><th>验收项</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>{assurance.acceptanceMatrix.map((item) => <tr key={item.id} className={`check-${item.status}`}><td>{item.id}</td><td>{item.title}</td><td>{item.status}</td><td>{item.message}</td></tr>)}</tbody>
      </table>
      <h3>边界策略</h3>
      <ul>{assurance.remainingBoundaryPolicy.map((item) => <li key={item}>{item}</li>)}</ul>
    </div>
  );
}


function IssueCenterPanel({ project, onLocateIssue }: { project: Project; onLocateIssue: (issue: IssueCenterItem) => void }) {
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
      <div className="maturityGrid">
        <StatusCard title="系统模块完成度" value={`${maturity.overallCompletion}%`} detail={maturity.closedLoopComplete ? '软件闭环模块 100%' : '模块仍有缺项'} tone={maturity.closedLoopComplete ? 'pass' : 'warn'} />
        <StatusCard title="数据建模" value={`${maturity.dataModelCompletion}%`} detail="地勘、地质、基坑轮廓" tone={maturity.dataModelCompletion >= 90 ? 'pass' : 'warn'} />
        <StatusCard title="设计计算" value={`${maturity.designCalculationCompletion}%`} detail="围护、计算、规范筛查、闸门" tone={maturity.designCalculationCompletion >= 90 ? 'pass' : 'warn'} />
        <StatusCard title="BIM/CAD 交付" value={`${maturity.bimCadDeliverableCompletion}%`} detail="IFC、CAD、钢筋、图表" tone={maturity.bimCadDeliverableCompletion >= 85 ? 'pass' : 'warn'} />
        <StatusCard title="工程出图准备" value={`${maturity.engineeringAcceptanceReadiness ?? maturity.officialIssueReadiness}%`} detail="项目数据和专业复核状态" tone={(maturity.engineeringAcceptanceReadiness ?? maturity.officialIssueReadiness) >= 90 ? 'pass' : 'review'} />
      </div>
      <div className="issueCounters">
        <span className="fail">阻断 {data.summary.fail ?? 0}</span>
        <span className="warn">警告 {data.summary.warning ?? 0}</span>
        <span className="review">人工复核 {data.summary.manual_review ?? 0}</span>
        <span>合计 {data.issueCount}</span>
      </div>
      {(maturity.moduleLedger ?? data.moduleLedger ?? []).length ? <div className="moduleLedger"><h4>V2.3.0 软件模块验收清单</h4><div className="moduleLedgerGrid">{(maturity.moduleLedger ?? data.moduleLedger ?? []).map((item) => <div key={item.id} className="moduleLedgerItem"><strong>{item.id} · {item.name}</strong><span>{item.completion}% · {item.status}</span><em>{item.evidence}</em></div>)}</div></div> : null}
      <div className="stepGrid">
        <div className="summaryPanel"><h4>优先处理动作</h4><ol className="nextActionList">{data.nextActions.slice(0, 6).map((item, index) => <li key={`${item.title}-${index}`}><strong>{item.workflowStep}</strong><span>{item.title}</span><em>{item.recommendation}</em></li>)}</ol></div>
        <div className="summaryPanel"><h4>当前边界</h4><ul>{maturity.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>
      </div>
      <table className="table compactTable"><thead><tr><th>等级</th><th>流程</th><th>类别</th><th>对象</th><th>问题</th><th>定位</th><th>建议</th></tr></thead><tbody>{data.issues.slice(0, 30).map((item) => <tr key={item.id} className={`issue-${item.severity} clickableRow`} onClick={() => onLocateIssue(item)} title="点击定位到对应流程、构件或 CAD 图纸"><td>{item.severity}</td><td>{item.workflowStep}</td><td>{item.category}</td><td>{item.objectId ?? '-'}</td><td>{item.message}</td><td>{String(item.locator?.targetPanel ?? item.targetPanel ?? item.workflowStep)}{item.locator?.drawingSheet ? <em> · {String(item.locator.drawingSheet)}</em> : null}</td><td>{item.recommendation}</td></tr>)}</tbody></table>
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

function ExportPanel({ project, runTask, selectedLocator }: { project: Project; runTask: (title: string, operationName: BackendTaskOperation, payload?: Record<string, unknown>, autoDownload?: boolean) => Promise<void>; selectedLocator?: Record<string, unknown> }) {
  const latest = getLatestResult(project);
  const blocked = !latest || !project.retainingSystem || latest.governingValues.governingCheckStatus === 'fail' || (latest.checkSummary?.fail ?? 0) > 0;
  return (
    <div>
      {blocked && <div className="warning">当前导出入口处于审查提示状态：没有完整设计/计算结果，或存在 fail。仍可下载调试文件，但正式成果应先修复问题。</div>}
      <div className="actionStrip simplifiedActions"><button onClick={() => runTask('全流程计算与成果生成', 'full_delivery', { topN: 3 })} disabled={!project.retainingSystem}>一键生成完整交付包</button><span className="small">后台依次计算、生成 IFC、CAD、SVG 和 DOCX，并保留任务日志。</span></div>
      <div className="exportGrid">
        <ExportCard projectId={project.id} taskOperation="export_ifc_light" title="IFC 轻量协调版" description="coordination_light.ifc：减少钢筋、承压板和预埋件实体，优先用于 Revit/Navisworks 协调浏览。" href={api.exportUrl(project.id, 'ifc-light')} button="下载轻量 IFC" />
        <ExportCard projectId={project.id} taskOperation="export_ifc_analysis" title="IFC 分析模型版" description="analysis_model.ifc：保留构件轴线、节点、弹簧、荷载和工况信息，不导出实体钢筋，便于计算模型交换。" href={api.exportUrl(project.id, 'ifc-analysis')} button="下载分析 IFC" />
        <ExportCard projectId={project.id} taskOperation="export_ifc_construction_visual" title="IFC 施工图可视化版" description="construction_visual.ifc：钢筋以可视化代理构件表达，优先保证 Web Viewer / Revit / Navisworks 可见；钢筋参数保存在属性集。" href={api.exportUrl(project.id, 'ifc-construction-visual')} button="下载可视化 IFC" />
        <ExportCard projectId={project.id} taskOperation="export_ifc_detailed" title="IFC 施工图语义详细版" description="design_detailed.ifc：保留 IfcReinforcingBar、承压板、预埋件、节点构造和完整属性集，用于 BIM 语义审查。" href={api.exportUrl(project.id, 'ifc-detailed')} button="下载语义详细 IFC" />
        <ExportCard title="IFC 兼容性自检" description="支持 mode=coordination_light/analysis_model/construction_visual/design_detailed，按 BlenderBIM、BIMVision、Solibri、Revit、Navisworks 分级。" href={api.ifcCheckUrl(project.id, 'construction_visual')} button="查看可视化 IFC 自检" />
        <ExportCard projectId={project.id} taskOperation="export_drawings_cad" title="施工 CAD 图纸包" description="DXF R12 + CSV/JSON：包含 6 张正式图纸接口、图纸目录、材料表、钢筋表和交付一致性矩阵，可在 AutoCAD、中望 CAD、浩辰 CAD 中继续深化。" href={api.exportUrl(project.id, 'drawings-cad')} button="下载 CAD 图纸包" />
        <ExportCard projectId={project.id} taskOperation="export_drawings_svg" title="施工图 SVG 图纸包" description="用于汇报、校审和文档插图的 SVG 图纸包；与计算书中的图纸数据同源。" href={api.exportUrl(project.id, 'drawings-svg')} button="下载 SVG 图纸包" />
        <ExportCard projectId={project.id} taskOperation="export_report" title="DOCX 计算书" description="首页包含审图清单和支撑评分平面图，集中展示阻断项、警告项、缺项和人工复核项。" href={api.exportUrl(project.id, 'report')} button="下载 DOCX" />
        <ExportCard projectId={project.id} taskOperation="export_trace" title="计算追溯链 JSON" description="导出工况—构件—控制值—公式—规范条文—结果路径追溯链，用于计算复核和审查。" href={api.exportUrl(project.id, 'json')} button="下载追溯链" />
        <ExportCard projectId={project.id} taskOperation="export_issue_report" title="问题清单与完成度 JSON" description="导出 V2.3.0 模块验收清单、项目风险、出图准备度和下一步动作。" href={api.exportUrl(project.id, 'json')} button="下载问题清单" />
        <ExportCard projectId={project.id} taskOperation="export_benchmark_cases" title="公开论文典型基坑算例包" description="按规范算法完整跑通多个公开论文派生基坑算例，含 JSON、CAD、SVG、DOCX、IFC 和追溯链。" href={api.benchmarkPackageUrl()} button="下载回归算例包" />
        <ExportCard projectId={project.id} taskOperation="export_json" title="完整 JSON" description="用于调试、归档和后续数据迁移。" href={api.exportUrl(project.id, 'json')} button="下载 JSON" />
      </div>
      <CadTemplatePanel project={project} />
      <CadLocatorPreview project={project} locator={selectedLocator} />
      <RebarDetailingPanel project={project} />
      <RebarIfcViewer project={project} highlightLocator={selectedLocator} />
      <BenchmarkPanel />
      <div className="summaryPanel exportModelPreview">
        <h3>模型可视化预览</h3>
        <p className="small">下载成果前可在此快速检查支撑间距、交叉高亮、地连墙位置、节点和立柱。若 IFC 在外部 Viewer 打开异常，先查看上方 IFC 兼容性自检 JSON。</p>
        <Engineering3DViewer project={project} focus="retaining" highlightLocator={selectedLocator} />
      </div>
    </div>
  );
}

function ProjectTreeSummary({ project }: { project: Project }) {
  return (
    <div className="projectTreeSummary">
      <h4>当前数据摘要</h4>
      <ul>
        <li>钻孔：{project.boreholes.length}</li>
        <li>地层：{project.strata.length}</li>
        <li>地质面：{project.geologicalModel?.surfaces?.length ?? 0}</li>
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
  return <section className="summaryPanel cadTemplatePanel"><div className="sectionLead"><h3>企业 CAD 模板配置 · V2.5.0</h3><p className="small">配置图号前缀、阶段、签审栏、关键图层和定位图层；导出的 DXF、图纸目录、签审清单和 manifest 会同步记录该标准。</p></div><div className="cadTemplateGrid"><label>企业名称<input value={draft.enterpriseName ?? ''} onChange={(e) => setDraft((v) => ({ ...v, enterpriseName: e.target.value }))} /></label><label>项目代号<input value={draft.projectCode ?? ''} onChange={(e) => setDraft((v) => ({ ...v, projectCode: e.target.value }))} /></label><label>图号前缀<input value={draft.sheetPrefix ?? 'S'} onChange={(e) => setDraft((v) => ({ ...v, sheetPrefix: e.target.value }))} /></label><label>阶段<input value={draft.stage ?? ''} onChange={(e) => setDraft((v) => ({ ...v, stage: e.target.value }))} /></label><label>设计<input value={draft.designer ?? ''} onChange={(e) => setDraft((v) => ({ ...v, designer: e.target.value }))} /></label><label>校核<input value={draft.checker ?? ''} onChange={(e) => setDraft((v) => ({ ...v, checker: e.target.value }))} /></label><label>审定<input value={draft.approver ?? ''} onChange={(e) => setDraft((v) => ({ ...v, approver: e.target.value }))} /></label><label>支撑图层<input value={layers.support ?? 'PIT_SUPPORT'} onChange={(e) => setDraft((v) => ({ ...v, layerStandard: { ...(v.layerStandard ?? {}), support: e.target.value } }))} /></label><label>钢筋图层<input value={layers.rebarMain ?? 'PIT_REBAR_MAIN'} onChange={(e) => setDraft((v) => ({ ...v, layerStandard: { ...(v.layerStandard ?? {}), rebarMain: e.target.value } }))} /></label><label>定位高亮图层<input value={layers.highlight ?? 'PIT_HIGHLIGHT'} onChange={(e) => setDraft((v) => ({ ...v, layerStandard: { ...(v.layerStandard ?? {}), highlight: e.target.value } }))} /></label></div><div className="actionStrip simplifiedActions"><button onClick={save}>保存 CAD 模板</button><span className="small">{status || '当前模板会进入 enterprise_template_manifest.json、drawing_package_manifest.json 和签审清单。'}</span></div></section>;
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
  return <section className="summaryPanel"><h3>钢筋施工详图深化 · V2.5.0</h3><p className="small">{data.detailLevel}</p><div className="maturityGrid"><StatusCard title="钢筋编号" value={String(data.summary.barMarkCount ?? data.entries.length)} detail="bar mark" tone="pass" /><StatusCard title="逐根几何" value={String(data.summary.individualBarCount ?? bars.length)} detail={`omitted ${String(data.summary.omittedBarCount ?? 0)}`} tone="pass" /><StatusCard title="下料总长" value={`${data.summary.totalCutLengthM ?? '-'} m`} detail="中心线+锚固/搭接/弯钩" tone="review" /><StatusCard title="总重量" value={`${data.summary.totalWeightKg ?? '-'} kg`} detail="按 7850kg/m³ 估算" tone="review" /><StatusCard title="笼段" value={String(data.summary.cageSegmentCount ?? data.cageSegments?.length ?? 0)} detail="施工缝/分节" tone="pass" /><StatusCard title="签审清单" value={String(data.signoffChecklist?.length ?? 0)} detail={String(data.shopDrawingReadiness?.status ?? 'ready')} tone="pass" /></div><table className="table compactTable"><thead><tr><th>编号</th><th>宿主</th><th>类型</th><th>直径</th><th>形状</th><th>数量</th><th>单长</th><th>重量</th></tr></thead><tbody>{data.entries.slice(0, 12).map((item) => <tr key={item.barMark}><td>{item.barMark}</td><td>{item.hostCode}</td><td>{item.barType}</td><td>D{item.diameterMm}</td><td>{item.shapeCode}</td><td>{item.quantity}</td><td>{item.singleLengthM}m</td><td>{item.totalWeightKg}kg</td></tr>)}</tbody></table><h4>施工详图深化状态</h4><table className="table compactTable"><thead><tr><th>项目</th><th>状态</th><th>证据数</th></tr></thead><tbody>{(data.signoffChecklist ?? []).map((item) => <tr key={String(item.id)}><td>{String(item.label ?? item.item)}</td><td>{String(item.status)}</td><td>{String(item.evidenceCount ?? '-')}</td></tr>)}</tbody></table><h4>逐根钢筋几何样本</h4><table className="table compactTable"><thead><tr><th>Bar ID</th><th>宿主</th><th>类型</th><th>点数</th><th>中心线</th><th>锚固</th><th>搭接</th><th>弯钩</th><th>下料</th></tr></thead><tbody>{bars.slice(0, 12).map((bar) => <tr key={bar.barId}><td>{bar.barId}</td><td>{bar.hostCode}</td><td>{bar.barType}</td><td>{bar.points.length}</td><td>{bar.centerlineLengthM}m</td><td>{bar.anchorageLengthM}m</td><td>{bar.lapLengthM}m</td><td>{bar.hookLengthM}m</td><td>{bar.cutLengthM}m</td></tr>)}</tbody></table></section>;
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
  async function download() {
    try {
      setState({ running: true, progress: 12, phase: '提交导出请求' });
      if (projectId && taskOperation) {
        let task = await api.createTask(projectId, taskOperation, {});
        while (!['success', 'failed', 'cancelled'].includes(task.status)) {
          setState({ running: true, progress: Math.max(6, task.progress), phase: `${task.currentStep} · ${task.status}` });
          await new Promise((resolve) => window.setTimeout(resolve, 850));
          task = await api.getTask(task.id);
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

function getLatestResult(project: Project) {
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
    { label: '已生成地层面', done: Boolean(project.geologicalModel?.surfaces?.length) },
    { label: '可提取代表性剖面', done: Boolean(project.geologicalModel?.surfaces?.length || project.geologicalModel?.vtuMesh) }
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
