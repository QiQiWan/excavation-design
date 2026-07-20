import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import BoreholeImport from '../components/BoreholeImport';
import ExcavationEditor from '../components/ExcavationEditor';
import type { CalculationResult, ConstructionStageWorkspace, PitTask, Project, SupportLayoutOptimizationCandidate } from '../types/domain';
import { formatEngineeringValue } from '../utils/units';
import { AdverseScenarioPanel, CalculationEnvelopeVisual, CoreStandardGuidance, GeologySectionVisual, RebarConstructabilityPanel, RebarDetailVisual, RetainingPlanVisual, StabilityDistributionVisual, SupportForceCloudVisual, VerificationSafetyPanel } from '../components/CoreEngineeringVisuals';
import DesignBasisPanel from '../components/DesignBasisPanel';
import { waitForTaskWithHealth } from '../utils/taskPolling';
import ConstructionStageEditor from '../components/ConstructionStageEditor';

const GeologyViewer = lazy(() => import('../viewers/GeologyViewer'));
const RetainingSystemViewer = lazy(() => import('../viewers/RetainingSystemViewer'));
const ResultViewer = lazy(() => import('../viewers/ResultViewer'));
const RebarDesignPanel = lazy(() => import('../components/RebarDesignPanel'));
const RebarIfcViewer = lazy(() => import('../viewers/RebarIfcViewer'));
const SchemeComparisonPanel = lazy(() => import('../components/SchemeComparisonPanel'));


type CoreStageKey = 'basis' | 'input' | 'scheme' | 'calculation' | 'reinforcement' | 'deliverables';
type CoreStatus = {
  stages?: { key: CoreStageKey; title: string; status: 'done' | 'active' | 'pending'; message: string }[];
  nextStage?: CoreStageKey;
  nextAction?: string;
  blockers?: string[];
  summary?: Record<string, any>;
  storage?: Record<string, any>;
  standards?: Record<CoreStageKey, Record<string, any>[]>;
  designBasis?: Record<string, any>;
  stabilityDistribution?: { factors?: Record<string, any>[]; summary?: Record<string, any>; message?: string };
  verificationDistribution?: { records?: Record<string, any>[]; wallObjects?: Record<string, any>[]; missingInputSummary?: Record<string, any>[]; summary?: Record<string, any>; message?: string };
  schemeComparison?: { candidateCount?: number; fullCalculationCount?: number; selectedCandidateId?: string; comparisonAvailable?: boolean; rows?: Record<string, any>[] };
  adverseScenarios?: Record<string, any>[];
  formalAdverseScenarioSuite?: Record<string, any>;
  adverseScenarioCatalog?: Record<string, any>[];
  enterpriseLibraries?: Record<string, any>[];
  enterpriseLibraryValidation?: Record<string, any>;
  p3DetailingClosure?: Record<string, any>;
  calculationReadiness?: Record<string, any>;
  constructionStages?: ConstructionStageWorkspace;
  deepeningReadiness?: Record<string, any>;
  sectionCatalog?: Record<string, any>;
  designIntake?: Record<string, any>;
  macroStages?: { key: 'quick_scheme' | 'verification' | 'detailing'; title: string; status: 'done' | 'active' | 'pending'; summary: string; action: string }[];
};

type ActiveTask = { task: PitTask; title: string };

const STAGE_ORDER: CoreStageKey[] = ['basis', 'input', 'scheme', 'calculation', 'reinforcement', 'deliverables'];

function mb(bytes: unknown) {
  const value = Number(bytes ?? 0);
  return value > 0 ? `${(value / 1048576).toFixed(value > 104857600 ? 0 : 1)} MB` : '0 MB';
}

function candidateGeometry(candidate: SupportLayoutOptimizationCandidate) {
  const geometry = (candidate.planGeometry ?? {}) as Record<string, any>;
  const outline = Array.isArray(geometry.outline) ? geometry.outline : [];
  const supports = Array.isArray(geometry.supports) ? geometry.supports : [];
  const points = [
    ...outline,
    ...supports.flatMap((item: any) => [item.start, item.end]),
  ].filter((item: any) => Number.isFinite(Number(item?.x)) && Number.isFinite(Number(item?.y)));
  if (!points.length) return { outline, supports, viewBox: '0 0 100 40' };
  const xs = points.map((p: any) => Number(p.x));
  const ys = points.map((p: any) => Number(p.y));
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const pad = Math.max((maxY - minY) * .12, 1);
  return { outline, supports, viewBox: `${minX - pad} ${-(maxY + pad)} ${Math.max(maxX - minX + 2 * pad, 1)} ${Math.max(maxY - minY + 2 * pad, 1)}` };
}

function CandidateMiniPreview({ candidate }: { candidate: SupportLayoutOptimizationCandidate }) {
  const geometry = useMemo(() => candidateGeometry(candidate), [candidate]);
  const polygon = geometry.outline.map((p: any) => `${Number(p.x)},${-Number(p.y)}`).join(' ');
  if (!polygon || !geometry.supports.length) return <div className="corePreviewEmpty">预览待生成</div>;
  return <svg className="coreCandidateSvg" viewBox={geometry.viewBox} preserveAspectRatio="xMidYMid meet">
    <polygon points={polygon} className="coreCandidateOutline" />
    {geometry.supports.slice(0, 500).map((support: any, index: number) => <line
      key={String(support.id ?? index)}
      x1={Number(support.start?.x ?? 0)} y1={-Number(support.start?.y ?? 0)}
      x2={Number(support.end?.x ?? 0)} y2={-Number(support.end?.y ?? 0)}
      className={String(support.role ?? support.supportRole ?? '').includes('corner') ? 'coreCandidateBrace' : 'coreCandidateSupport'}
      vectorEffect="non-scaling-stroke"
    />)}
  </svg>;
}

export default function CoreProjectWorkspace({ project, onBack, onProjectChange }: { project: Project; onBack: () => void; onProjectChange: (project: Project) => void }) {
  const [current, setCurrent] = useState(project);
  const [status, setStatus] = useState<CoreStatus>();
  const [active, setActive] = useState<CoreStageKey>('basis');
  const [taskState, setTaskState] = useState<ActiveTask>();
  const [error, setError] = useState<string>();
  const [inputEditor, setInputEditor] = useState<'boreholes' | 'geology' | 'excavation' | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<string>();
  const [inspectedCandidate, setInspectedCandidate] = useState<SupportLayoutOptimizationCandidate>();
  const [visualMode, setVisualMode] = useState<'professional' | 'compact'>('professional');
  const [flowMode, setFlowMode] = useState<'guided' | 'professional'>('guided');
  const [showRebar3d, setShowRebar3d] = useState(false);
  const [calculationEvidence, setCalculationEvidence] = useState<CalculationResult>();
  const pollingTaskRef = useRef<string | undefined>(undefined);
  const initialStageAppliedRef = useRef(false);

  useEffect(() => { setCurrent(project); }, [project]);

  async function refresh() {
    const [updated, core] = await Promise.all([api.getProject(current.id), api.getCoreDesignStatus(current.id)]);
    setCurrent(updated);
    setStatus(core as CoreStatus);
    onProjectChange(updated);
    return core as CoreStatus;
  }

  useEffect(() => {
    let cancelled = false;
    api.getCoreDesignStatus(current.id)
      .then((value) => {
        if (cancelled) return;
        const core = value as CoreStatus;
        setStatus(core);
        if (!initialStageAppliedRef.current && core.nextStage) {
          setActive(core.nextStage);
          initialStageAppliedRef.current = true;
        }
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, [current.id, current.updatedAt]);

  useEffect(() => {
    let cancelled = false;
    api.listProjectTasks(current.id).then((tasks) => {
      if (cancelled || pollingTaskRef.current) return;
      const activeTask = tasks.find((item) => item.status === 'queued' || item.status === 'running');
      if (activeTask) void followTask(activeTask, activeTask.title || '恢复后台计算');
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [current.id]);

  async function followTask(task: PitTask, title: string, autoDownload = false) {
    if (pollingTaskRef.current === task.id) return;
    pollingTaskRef.current = task.id;
    setTaskState({ task, title });
    try {
      const finished = await waitForTaskWithHealth(task, (next) => setTaskState({ task: next, title }));
      if (finished.status !== 'success') throw new Error(finished.error || `${title}未完成：${finished.status}`);
      if (autoDownload && finished.result?.filePath) window.location.href = api.taskDownloadUrl(finished.id);
      await refresh();
      window.setTimeout(() => setTaskState(undefined), 700);
    } catch (reason) {
      setTaskState(undefined);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (pollingTaskRef.current === task.id) pollingTaskRef.current = undefined;
    }
  }

  async function runTask(title: string, operation: string, payload: Record<string, unknown> = {}, autoDownload = false) {
    setError(undefined);
    try {
      const created = await api.createTask(current.id, operation, payload);
      await followTask(created, title, autoDownload);
    } catch (reason) {
      setTaskState(undefined);
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function cancelActiveTask() {
    if (!taskState) return;
    try {
      const cancelled = await api.cancelTask(taskState.task.id);
      setTaskState({ ...taskState, task: cancelled });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function buildGeology() {
    setError(undefined);
    try {
      await api.buildGeology(current.id);
      setInputEditor(null);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function prepareAndRunCalculation() {
    setError(undefined);
    try {
      if (!current.geologicalModel && current.boreholes.length) {
        await api.buildGeology(current.id);
      }
      await runTask('计算并自动优化当前方案', 'calculation_full', { topN: 0 });
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function adoptCandidate(id: string) {
    setSelectedCandidate(id);
    await runTask('采用支撑候选方案', 'adopt_support_candidate', { candidateId: id });
  }

  async function applyRebar(mode: 'conservative' | 'balanced' | 'economic') {
    await runTask(
      `补齐梁设计并应用${mode === 'conservative' ? '保守' : mode === 'economic' ? '经济' : '均衡'}配筋方案`,
      'rebar_design',
      { mode, apply: true, recalculate: true },
    );
  }


  const workspaceLatest = current.calculationResults?.[current.calculationResults.length - 1];
  const latest = calculationEvidence?.id === workspaceLatest?.id ? calculationEvidence : workspaceLatest;
  const visualProject = useMemo<Project>(() => {
    if (!latest || latest === workspaceLatest) return current;
    const history = [...(current.calculationResults ?? [])];
    if (history.length) history[history.length - 1] = latest;
    else history.push(latest);
    return { ...current, calculationResults: history };
  }, [current, latest, workspaceLatest]);

  useEffect(() => {
    let cancelled = false;
    if (!workspaceLatest || !['calculation', 'reinforcement'].includes(active)) return () => { cancelled = true; };
    if (calculationEvidence?.id === workspaceLatest.id && (calculationEvidence.stageResults?.length ?? 0) > 0) return () => { cancelled = true; };
    void api.getLatestCalculationEvidence(current.id).then((value) => {
      if (!cancelled && value.result?.id === workspaceLatest.id) setCalculationEvidence(value.result);
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, [active, current.id, workspaceLatest?.id, calculationEvidence?.id]);
  const candidates = current.retainingSystem?.supportLayoutRepair?.candidates?.slice(0, 3) ?? [];
  const rebar = current.retainingSystem?.rebarDesignScheme as Record<string, any> | undefined;
  const stages = status?.stages ?? STAGE_ORDER.map((key) => ({ key, title: key, status: 'pending' as const, message: '' }));
  const selected = selectedCandidate ?? current.retainingSystem?.supportLayoutRepair?.selectedCandidateId;
  const basisReady = Boolean(status?.designBasis?.confirmed ?? current.designSettings.designBasisConfirmed);
  const intake = (status?.designIntake ?? {}) as Record<string, any>;
  const intakeReadiness = (intake.readiness ?? {}) as Record<string, any>;
  const inputReady = basisReady && Boolean(current.excavation?.outline?.closed && current.excavation.outline.points.length >= 3);
  const canGenerateConceptScheme = Boolean(intakeReadiness.canGenerateConceptScheme ?? inputReady);
  const hasCalculationGeology = Boolean(current.geologicalModel || current.boreholes.length);
  const canPrepareCalculation = Boolean(current.retainingSystem && basisReady && hasCalculationGeology);
  const macroStages = status?.macroStages ?? [
    { key: 'quick_scheme' as const, title: '快速方案', status: current.retainingSystem ? 'done' as const : 'active' as const, summary: '轮廓与深度齐全即可开始。', action: '生成方案' },
    { key: 'verification' as const, title: '计算与优化', status: latest ? 'done' as const : 'pending' as const, summary: '补高影响资料后计算。', action: '进入计算' },
    { key: 'detailing' as const, title: '配筋与交付', status: rebar ? 'active' as const : 'pending' as const, summary: '用有效内力包络深化。', action: '进入配筋' },
  ];
  const formalScenarioRows = ((status?.formalAdverseScenarioSuite?.summaries ?? []) as Record<string, any>[]).map((row) => ({
    ...row,
    label: row.scenarioLabel,
    safetyFactor: row.minimumSafetyFactor,
    description: `正式施工阶段复算 · ${String(row.evidenceLevel ?? '')}`,
    boundary: row.assumptions ? Object.entries(row.assumptions).filter(([key]) => key !== 'scenarioInputHash').map(([key, value]) => `${key}=${String(value)}`).join('；') : '',
  }));
  const adverseRows = formalScenarioRows.length ? formalScenarioRows : (status?.adverseScenarios ?? []);
  const p3Summary = (status?.p3DetailingClosure?.summary ?? {}) as Record<string, any>;
  const deepeningReadiness = (status?.deepeningReadiness ?? {}) as Record<string, any>;
  const p3EntryBlockers = (Array.isArray(deepeningReadiness.blockers) ? deepeningReadiness.blockers : []) as Record<string, any>[];
  const canRunP3 = Boolean(deepeningReadiness.canRunP3);
  const calculationCurrent = Boolean(status?.calculationReadiness?.valid ?? latest);
  const automaticP3Blockers = p3EntryBlockers.filter((item) => String((item.resolution as Record<string, any> | undefined)?.mode ?? '') === 'automatic');

  async function repairRebarEntry() {
    setActive('reinforcement');
    if (!calculationCurrent) {
      setError('当前配筋阻断的第一原因是计算结果缺失或已过期。请先在“计算与优化”中完成当前方案计算，再返回执行一键配筋闭合。');
      setActive('calculation');
      return;
    }
    await applyRebar('balanced');
  }

  function openMacroStage(key: 'quick_scheme' | 'verification' | 'detailing') {
    if (key === 'quick_scheme') {
      if (!basisReady) setActive('basis');
      else if (!inputReady) setActive('input');
      else setActive('scheme');
    } else if (key === 'verification') setActive('calculation');
    else setActive(rebar ? 'deliverables' : 'reinforcement');
  }

  async function handlePrimaryAction() {
    const action = (intake.primaryAction as Record<string, any> | undefined) ?? {};
    const key = String(action.key ?? '');
    const target = String(action.target ?? status?.nextStage ?? 'basis') as CoreStageKey;
    if (key === 'close_rebar_entry') {
      await repairRebarEntry();
      return;
    }
    if (key === 'build_geology') {
      setActive('input');
      await buildGeology();
      return;
    }
    if (key === 'run_calculation') {
      setActive('calculation');
      await prepareAndRunCalculation();
      return;
    }
    if (STAGE_ORDER.includes(target)) setActive(target);
  }

  return <main className="coreWorkspace">
    <header className="coreHeader">
      <div className="coreProjectIdentity">
        <button className="secondary" onClick={onBack}>项目列表</button>
        <div><h2>{current.name}</h2><span>{current.location || '未设置地点'}</span></div>
      </div>
      <div className="coreHeaderActions">
        <div className="coreFlowMode" aria-label="流程显示深度">
          <button type="button" className={flowMode === 'guided' ? 'active' : ''} onClick={() => setFlowMode('guided')}>简明流程</button>
          <button type="button" className={flowMode === 'professional' ? 'active' : ''} onClick={() => setFlowMode('professional')}>专业流程</button>
        </div>
        <span className={`coreGate ${Number(status?.summary?.failCount ?? 0) ? 'fail' : 'pass'}`}>Fail {Number(status?.summary?.failCount ?? 0)}</span>
        <span className="coreGate warning">Warning {Number(status?.summary?.warningCount ?? 0)}</span>
      </div>
    </header>

    {flowMode === 'guided' ? <nav className="coreStageNav coreMacroNav" aria-label="简明设计流程">
      {macroStages.map((stage, index) => <button key={stage.key} className={`${stage.status}`} onClick={() => openMacroStage(stage.key)}>
        <i>{stage.status === 'done' ? '✓' : index + 1}</i><span><strong>{stage.title}</strong><em>{stage.summary}</em></span>
      </button>)}
    </nav> : <nav className="coreStageNav" aria-label="专业设计流程">
      {stages.map((stage, index) => <button key={stage.key} className={`${active === stage.key ? 'active' : ''} ${stage.status}`} onClick={() => setActive(stage.key)}>
        <i>{stage.status === 'done' ? '✓' : index + 1}</i><span><strong>{stage.title}</strong><em>{stage.message}</em></span>
      </button>)}
    </nav>}

    {taskState ? <section className="coreTaskOverlay" aria-live="polite"><div className="coreTaskCard">
      <strong>{taskState.title}</strong><span>{taskState.task.currentStep || taskState.task.status}</span>
      <progress max={100} value={taskState.task.progress ?? 0} /><b>{Math.round(taskState.task.progress ?? 0)}%</b>
      <small>心跳：{taskState.task.heartbeatAt ? new Date(taskState.task.heartbeatAt).toLocaleTimeString() : '等待worker领取'}</small>
      <button type="button" className="secondary" onClick={() => void cancelActiveTask()}>取消任务</button>
    </div></section> : null}
    {error ? <div className="coreError"><strong>操作未完成</strong><span>{error}</span><button className="secondary" onClick={() => setError(undefined)}>关闭</button></div> : null}
    <section className="coreMain">
      <div className="coreStageHeading">
        <div><span>当前步骤</span><h3>{stages.find((item) => item.key === active)?.title}</h3></div>
        <div className="coreStageTools">
          <div className="coreVisualMode" aria-label="可视化深度">
            <button type="button" className={visualMode === 'professional' ? 'active' : 'secondary'} onClick={() => setVisualMode('professional')}>专业视图</button>
            <button type="button" className={visualMode === 'compact' ? 'active' : 'secondary'} onClick={() => setVisualMode('compact')}>精简视图</button>
          </div>
          <div className="coreStorage">工作区 {mb(status?.storage?.workspaceBytes)} · 外部成果 {mb(status?.storage?.externalBytes)}</div>
        </div>
      </div>
      {flowMode === 'guided' && intake.primaryAction ? <div className="guidedNextAction">
        <div><span>现在只处理一件事</span><strong>{String(intake.primaryAction.label ?? status?.nextAction ?? '继续设计')}</strong><em>{String(intake.primaryAction.reason ?? '')}</em></div>
        <button type="button" disabled={Boolean(taskState)} onClick={() => void handlePrimaryAction()}>{['close_rebar_entry', 'build_geology', 'run_calculation'].includes(String((intake.primaryAction as Record<string, any>).key ?? '')) ? '立即处理' : '定位到操作'}</button>
      </div> : null}
      <CoreStandardGuidance standards={(status?.standards?.[active] ?? []) as any[]} />

      {active === 'basis' ? <section className="corePanel">
        <DesignBasisPanel project={current} basis={status?.designBasis} intake={status?.designIntake} onSaved={async (updated) => { setCurrent(updated); onProjectChange(updated); await refresh(); }} onContinue={() => setActive(current.excavation ? 'scheme' : 'input')} />
      </section> : null}

      {active === 'input' ? <section className="corePanel">
        <section className="inputPriorityGroup requiredNow">
          <header><div><span>现在必须</span><strong>只录入基坑轮廓与最终开挖深度</strong></div><em>决定墙线、墙段和支撑层数</em></header>
          <div className="coreInputGrid one"><button className={current.excavation ? 'complete' : ''} onClick={() => setInputEditor('excavation')}><strong>轮廓与最终开挖深度</strong><span>{current.excavation ? `${current.excavation.outline.points.length} 个点 · 深度 ${Number(current.excavation.depth ?? 0).toFixed(2)} m` : '待录入'}</span></button></div>
        </section>
        <section className="inputPriorityGroup deferred">
          <header><div><span>正式计算前再补</span><strong>地勘由系统转成计算模型</strong></div><em>不阻断快速方案</em></header>
          <div className="coreInputGrid">
            <button className={(current.boreholes.length || current.strata.length) ? 'complete' : ''} onClick={() => setInputEditor('boreholes')}><strong>钻孔与土层参数</strong><span>钻孔 {current.boreholes.length} · 地层 {current.strata.length}</span></button>
            <button className={current.geologicalModel ? 'complete' : ''} onClick={() => setInputEditor('geology')}><strong>计算用地质设计域</strong><span>{current.geologicalModel ? '系统已建立' : current.boreholes.length ? '可由系统自动建立' : '等待钻孔数据'}</span></button>
          </div>
        </section>
        {inputEditor === 'boreholes' ? <div className="coreEditor"><BoreholeImport project={current} onImported={() => void refresh()} /></div> : null}
        {inputEditor === 'geology' ? <div className="coreActionCard"><div><strong>建立轻量地质模型</strong><span>完整网格进入外部对象存储，网页只保留预览。</span></div><button onClick={() => void buildGeology()} disabled={!current.boreholes.length}>生成地质模型</button></div> : null}
        {inputEditor === 'excavation' ? <div className="coreEditor"><ExcavationEditor project={current} onSaved={() => void refresh()} /></div> : null}
        {current.geologicalModel ? visualMode === 'professional' ? <Suspense fallback={<div className="coreModuleFallback">正在加载地质三维模型与地层明细…</div>}><section className="coreProfessionalSection"><GeologyViewer project={current} /></section></Suspense> : <GeologySectionVisual project={current} /> : null}
        {canGenerateConceptScheme ? <div className="coreNextBar"><span>轮廓与深度已齐全，可以先生成方案；地勘将在正式计算前提示补充。</span><button onClick={() => setActive('scheme')}>先生成快速方案</button></div> : <p className="coreHint">当前只需完成最小设计任务书和闭合轮廓，不要求先填写全部专业参数。</p>}
      </section> : null}

      {active === 'scheme' ? <section className="corePanel">
        <div className="corePrimaryAction"><div><strong>按轮廓和规范生成多方案</strong><span>生成结构体系、支撑间距和立柱布置均有明显差异的 A/B/C 候选；先做拓扑预检，再按需运行完整比选。</span></div><div className="coreButtonGroup"><button disabled={!basisReady || !inputReady} onClick={() => void runTask('生成 A/B/C 围护方案', 'support_layout_optimization', { preset: 'balanced', maxCandidates: 3, searchConfig: { requireDiverseSchemes: true } })}>生成/更新 A/B/C</button><button className="secondary" disabled={candidates.length < 2} onClick={() => void runTask('完整计算 A/B/C', 'candidate_comparison', { topN: Math.min(3, candidates.length) })}>完整比选</button></div></div>
        {visualMode === 'professional' ? <Suspense fallback={<div className="coreModuleFallback">正在加载方案比选、三维模型和构件校核…</div>}>
          <section className="coreProfessionalSection">
            <SchemeComparisonPanel
              project={current}
              onGenerateCandidates={() => runTask('生成 A/B/C 围护方案', 'support_layout_optimization', { preset: 'balanced', maxCandidates: 3, searchConfig: { requireDiverseSchemes: true } })}
              onRunComparison={() => runTask('完整计算 A/B/C', 'candidate_comparison', { topN: Math.min(3, candidates.length) })}
              onAdopt={adoptCandidate}
              onRefresh={refresh}
              onSelectCandidate={setInspectedCandidate}
            />
            {current.retainingSystem ? <RetainingSystemViewer project={current} previewCandidate={inspectedCandidate} /> : <div className="coreEmpty">生成并采用方案后显示完整围护结构三维模型、构件表和支撑质量平面。</div>}
          </section>
        </Suspense> : <>
          {candidates.length ? <div className="coreCandidateGrid">{candidates.map((candidate, index) => <article key={String(candidate.id ?? index)} className={String(candidate.id) === String(selected) ? 'selected' : ''}>
            <header><strong>方案 {String.fromCharCode(65 + index)}</strong><span>{String(candidate.variableSummary?.schemeLabel ?? candidate.variableSummary?.topologyFamily ?? '支撑体系')}</span><em>评分 {candidate.score?.toFixed?.(1) ?? candidate.score ?? '-'}</em></header>
            <CandidateMiniPreview candidate={candidate} />
            <dl><div><dt>支撑 / 立柱</dt><dd>{candidate.supportCount} / {candidate.columnCount}</dd></div><div><dt>最长跨度</dt><dd>{formatEngineeringValue(candidate.maxSpanLength, 'length')}</dd></div><div><dt>最大位移</dt><dd>{candidate.fullCalculation?.maxDisplacement != null ? formatEngineeringValue(candidate.fullCalculation.maxDisplacement, 'displacement') : '待完整计算'}</dd></div><div><dt>最小稳定系数</dt><dd>{String(candidate.fullCalculation?.minStabilitySafetyFactor ?? '待完整计算')}</dd></div><div><dt>拓扑</dt><dd>{candidate.hardConstraints?.passed ? '通过' : '受控阻断'}</dd></div><div><dt>完整比选</dt><dd>{candidate.fullCalculation && Object.keys(candidate.fullCalculation).length ? '已完成' : '未运行'}</dd></div></dl>
            <button className={String(candidate.id) === String(selected) ? 'secondary' : ''} disabled={!candidate.id || !candidate.hardConstraints?.passed} onClick={() => candidate.id && void adoptCandidate(candidate.id)}>{String(candidate.id) === String(selected) ? '当前采用' : '采用方案'}</button>
          </article>)}</div> : <div className="coreEmpty">尚未生成候选方案。</div>}
          <RetainingPlanVisual project={current} candidate={candidates.find((item) => String(item.id) === String(selected)) ?? candidates[0]} />
          {status?.schemeComparison?.rows?.length ? <div className="coreSchemeComparisonTable"><table><thead><tr><th>方案</th><th>体系</th><th>支撑/立柱</th><th>最大轴力</th><th>最大位移</th><th>最小稳定系数</th><th>Pareto</th><th>综合排名</th></tr></thead><tbody>{status.schemeComparison.rows.map((row: any) => <tr key={String(row.candidateId)} className={row.recommended ? 'recommended' : ''}><td>{row.schemeLabel}{row.recommended ? '（推荐）' : ''}</td><td>{String(row.schemeName ?? row.topologyFamily ?? '-')}</td><td>{row.supportCount} / {row.columnCount}</td><td>{row.maxSupportAxialForce != null ? formatEngineeringValue(row.maxSupportAxialForce, 'force') : '待计算'}</td><td>{row.maxDisplacement != null ? formatEngineeringValue(row.maxDisplacement, 'displacement') : '待计算'}</td><td>{String(row.minStabilitySafetyFactor ?? '待计算')}</td><td>{row.paretoFront ? `前沿 F${String(row.paretoRank ?? 1)}` : `F${String(row.paretoRank ?? '-')}`}</td><td>{String(row.decisionRank ?? '-')}</td></tr>)}</tbody></table></div> : null}
        </>}
        {current.retainingSystem ? <div className="coreNextBar"><span>围护墙 {current.retainingSystem.diaphragmWalls?.length ?? 0} · 支撑 {current.retainingSystem.supports?.length ?? 0}</span><button onClick={() => setActive('calculation')}>进入计算验算</button></div> : null}
      </section> : null}

      {active === 'calculation' ? <section className="corePanel">
        <ConstructionStageEditor project={current} onChanged={async () => { await refresh(); }} />
        {status?.calculationReadiness ? <div className={`calculationEvidenceBanner ${String(status.calculationReadiness.stageEvidenceState ?? 'not_generated')}`}><div><strong>施工阶段计算证据</strong><span>{String((status.calculationReadiness.messages as string[] | undefined)?.[0] ?? '等待计算')}</span></div><b>{String(status.calculationReadiness.stageResultCount ?? 0)} / {String(status.calculationReadiness.expectedStageResultCount || '-')} 条已载入</b><em>{String(status.calculationReadiness.stageEvidenceState ?? 'not_generated')}</em></div> : null}
        <div className="calculationPreparationGuide">
          <strong>计算前只核对高影响资料</strong>
          <span className={current.retainingSystem ? 'pass' : 'pending'}>① 已采用围护方案</span>
          <span className={(current.boreholes.length || current.strata.length) ? 'pass' : 'pending'}>② 钻孔与土层参数</span>
          <span className={current.geologicalModel ? 'pass' : current.boreholes.length ? 'auto' : 'pending'}>③ 地质设计域{!current.geologicalModel && current.boreholes.length ? '（系统将自动建立）' : ''}</span>
          <span className="auto">④ 施工阶段按开挖深度与支撑标高自动生成，运行前可修改</span>
        </div>
        <div className="corePrimaryAction"><div><strong>计算—校核—补强自动闭环</strong><span>系统先补齐计算用地质模型，再生成施工阶段，计算围护墙、围檩、支撑、位移和稳定性；定量不足项进入受控补强复算。</span></div><button disabled={!canPrepareCalculation} onClick={() => void prepareAndRunCalculation()}>{!current.retainingSystem ? '请先采用方案' : !hasCalculationGeology ? '请先补地勘' : !current.geologicalModel ? '自动建模并计算' : '计算并自动优化'}</button></div>
        <div className="coreSecondaryAction"><div><strong>正式不利工况专项复算</strong><span>按降水失效、超挖、局部渗流、承压水抬升、预加轴力/温度偏差和长期效应分别重建施工工况并独立计算。</span></div><button className="secondary" disabled={!latest} onClick={() => void runTask('正式不利工况专项复算', 'formal_adverse_scenarios', { codes: (status?.adverseScenarioCatalog ?? []).filter((row: any) => row.selected !== false && row.applicable !== false).map((row: any) => row.code) })}>运行专项复算</button></div>
        {latest ? <><div className="coreMetricGrid">
          <div><span>最大位移</span><strong>{formatEngineeringValue(latest.governingValues?.maxDisplacement, 'displacement')}</strong></div>
          <div><span>最大支撑轴力</span><strong>{formatEngineeringValue(latest.governingValues?.maxSupportAxialForce, 'force')}</strong></div>
          <div><span>墙体弯矩</span><strong>{formatEngineeringValue(latest.governingValues?.maxWallMoment, 'moment')}</strong></div>
          <div><span>检查</span><strong>{latest.checkSummary?.fail ?? 0} Fail / {latest.checkSummary?.warning ?? 0} Warning</strong></div>
        </div>
        <div className="coreCheckList">{(latest.checks ?? []).filter((item: any) => item.status !== 'pass').slice(0, 8).map((item: any, index: number) => <div key={String(item.ruleId ?? index)} className={String(item.status)}><strong>{String(item.ruleId ?? item.category ?? '检查')}</strong><span>{String(item.message ?? '')}</span></div>)}{!(latest.checks ?? []).some((item: any) => item.status !== 'pass') ? <div className="pass"><strong>当前已实现检查未发现超限</strong></div> : null}</div>
        </> : <div className="coreEmpty">尚未生成当前方案计算结果。</div>}
        {visualMode === 'professional' ? <Suspense fallback={<div className="coreModuleFallback">正在加载完整内力包络、变形云图和规范校核明细…</div>}>
          <section className="coreProfessionalSection">
            <VerificationSafetyPanel distribution={status?.verificationDistribution as any} />
            <AdverseScenarioPanel scenarios={adverseRows as any[]} />
            <StabilityDistributionVisual distribution={status?.stabilityDistribution as any} />
            <ResultViewer project={visualProject} density="professional" coreMode />
          </section>
        </Suspense> : <>
          <div className="coreVisualGrid"><CalculationEnvelopeVisual project={visualProject} /><SupportForceCloudVisual project={visualProject} /></div>
          <VerificationSafetyPanel distribution={status?.verificationDistribution as any} />
          <AdverseScenarioPanel scenarios={adverseRows as any[]} />
          <StabilityDistributionVisual distribution={status?.stabilityDistribution as any} />
        </>}
        {latest ? <div className={`coreNextBar ${calculationCurrent ? '' : 'blocked'}`}><span>{calculationCurrent ? '当前计算合同有效，可检查配筋深化入口。' : String((status?.calculationReadiness?.messages as string[] | undefined)?.[0] ?? '计算结果与当前设计快照不一致，请重新计算。')}</span><button onClick={() => setActive('reinforcement')}>查看配筋深化诊断</button></div> : null}
      </section> : null}

      {active === 'reinforcement' ? <section className="corePanel">
        <div className="corePrimaryAction rebarClosureAction"><div><strong>{p3EntryBlockers.length ? '选择策略并自动关闭配筋入口' : '围护墙、围檩和支撑配筋'}</strong><span>{p3EntryBlockers.length ? '系统会读取当前施工阶段包络，补齐几何梁的设计记录、写入五类构造钢筋；截面变化时自动复算。' : '基于当前计算内力包络选择配筋，重型逐根钢筋几何仅在导出时生成。'}</span></div><div className="coreButtonGroup"><button disabled={!calculationCurrent || Boolean(taskState)} onClick={() => void applyRebar('conservative')}>采用保守</button><button className="recommended" disabled={!calculationCurrent || Boolean(taskState)} onClick={() => void applyRebar('balanced')}>采用均衡（推荐）</button><button disabled={!calculationCurrent || Boolean(taskState)} onClick={() => void applyRebar('economic')}>采用经济</button></div></div>
        <div className="coreSecondaryAction"><div><strong>节点、预埋件与钢筋空间深化</strong><span>匹配企业节点模板，生成套筒、锚固、预埋件、局部子模型和碰撞协调结果；完整逐根数据进入外部成果。</span></div><button className="secondary" disabled={!canRunP3} title={canRunP3 ? '入口条件已满足' : String(p3EntryBlockers[0]?.requiredAction ?? '请先关闭配筋深化入口阻断')} onClick={() => void runTask('企业节点与钢筋深化闭环', 'p3_detailing_closure', { mode: String(rebar?.mode ?? 'balanced'), topNodeCount: 8 })}>运行 P3 深化闭环</button></div>
        {!canRunP3 && p3EntryBlockers.length ? <div className="coreP3EntryDiagnostic"><header><div><strong>配筋入口尚未闭合：共 {String(deepeningReadiness.blockerCount ?? p3EntryBlockers.length)} 个对象</strong><span>系统已按原因合并展示，不需要逐个寻找构件。</span></div>{automaticP3Blockers.length ? <button type="button" disabled={Boolean(taskState) || !calculationCurrent} onClick={() => void repairRebarEntry()}>一键关闭配筋入口</button> : null}</header>{p3EntryBlockers.slice(0, 6).map((item) => { const resolution = (item.resolution ?? {}) as Record<string, any>; const objects = Array.isArray(item.objects) ? item.objects.slice(0, 8) : []; return <div key={String(item.id ?? item.reasonCode)}><b>{String(item.title ?? item.reasonCode)} · {String(item.count ?? 1)} 项</b><span><strong>为什么：</strong>{String(item.message ?? '')}</span><em><strong>怎么处理：</strong>{String(item.requiredAction ?? '')}</em>{objects.length ? <small>涉及：{objects.join('、')}{Number(item.objectCount ?? item.count ?? 0) > objects.length ? ` 等 ${String(item.objectCount ?? item.count)} 个对象` : ''}</small> : null}<i className={String(resolution.mode ?? 'manual')}>{String(resolution.mode) === 'automatic' ? '系统可自动处理' : '需要工程师确认'}</i></div>; })}{automaticP3Blockers.length ? <footer><strong>点击后系统将依次执行</strong><span>读取当前施工阶段结果 → 补齐冠梁/围檩设计记录 → 写入墙、梁、支撑配筋 → 截面变化时自动复算 → 重新检查 P3 入口。</span></footer> : null}</div> : null}
        {rebar ? <div className="coreMetricGrid"><div><span>配筋模式</span><strong>{String(rebar.mode ?? 'balanced')}</strong></div><div><span>检查数量</span><strong>{Array.isArray(rebar.checks) ? rebar.checks.length : '-'}</strong></div><div><span>状态</span><strong>{String(rebar.status ?? '已生成')}</strong></div></div> : <div className="coreEmpty">尚未生成配筋方案。</div>}
        {status?.p3DetailingClosure?.status ? <div className="coreMetricGrid p3DetailingMetrics"><div><span>P3 深化状态</span><strong>{String(status.p3DetailingClosure.status)}</strong></div><div><span>局部子模型</span><strong>{String(p3Summary.nodeSubmodelCount ?? 0)}</strong></div><div><span>套筒</span><strong>{String(p3Summary.couplerCount ?? 0)}</strong></div><div><span>碰撞阻断</span><strong>{String(p3Summary.hardCollisionCount ?? 0)}</strong></div><div><span>企业节点未匹配</span><strong>{String(p3Summary.unmatchedEnterpriseNodeCount ?? 0)}</strong></div></div> : null}
        {visualMode === 'professional' ? <Suspense fallback={<div className="coreModuleFallback">正在加载配筋分区、构件设计值和施工图明细…</div>}>
          <section className="coreProfessionalSection">
            <RebarDesignPanel project={current} onApplied={async () => { await refresh(); }} />
            <RebarConstructabilityPanel scheme={rebar} />
            <div className="coreOnDemandToolbar"><div><strong>逐根钢筋三维检查</strong><span>按需读取钢筋可视化数据，避免普通页面加载逐根钢筋几何。</span></div><button type="button" className="secondary" onClick={() => setShowRebar3d((value) => !value)}>{showRebar3d ? '关闭三维钢筋' : '加载三维钢筋'}</button></div>
            {showRebar3d ? <RebarIfcViewer project={current} /> : null}
          </section>
        </Suspense> : <RebarDetailVisual project={current} />}
        {rebar ? <div className="coreNextBar"><span>配筋方案已保存。</span><button onClick={() => setActive('deliverables')}>进入成果交付</button></div> : null}
      </section> : null}

      {active === 'deliverables' ? <section className="corePanel">
        <div className="coreDeliverableGrid">
          <button disabled={!latest} onClick={() => void runTask('生成管理者可读计算书', 'export_report', {}, true)}><strong>计算书</strong><span>排版 DOCX · 明文结论</span></button>
          <button disabled={!latest} onClick={() => void runTask('生成施工图', 'export_drawings_cad', { scope: 'full', issueMode: 'review' }, true)}><strong>施工图</strong><span>CAD ZIP</span></button>
          <button disabled={!current.retainingSystem} onClick={() => void runTask('生成协调模型', 'export_ifc_construction_visual', {}, true)}><strong>BIM 模型</strong><span>IFC</span></button>
          <button disabled={!latest} onClick={() => void runTask('生成项目管理者成果包', 'export_coordinated_delivery', { issueMode: 'review', includeIfcProfiles: false, managerReadable: true }, true)}><strong>审查成果包</strong><span>项目摘要 + 图纸 + 计算书</span></button>
        </div>
      </section> : null}
    </section>
  </main>;
}
