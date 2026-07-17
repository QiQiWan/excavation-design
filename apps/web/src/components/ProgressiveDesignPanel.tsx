import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../types/domain';

type Stage = {
  code: string;
  index: number;
  title: string;
  purpose: string;
  status: 'complete' | 'ready' | 'attention' | 'waiting' | 'blocked' | string;
  summary: string;
  choices?: { field?: string; value: string | number; label: string; recommended?: boolean; available?: boolean; readiness?: string; description?: string }[];
  requiredInputs?: string[];
  nextAction?: string;
  blocksNext?: boolean;
};

export type ProgressiveDesignSession = {
  projectId: string;
  config: {
    currentStage?: string;
    sessionVersion?: number;
    decisions?: Record<string, any>;
    constraints?: Record<string, any>;
    resourcePolicy?: Record<string, any>;
    confirmedStages?: string[];
    dirtyFromStage?: string;
  };
  stages: Stage[];
  currentStage: string;
  recommendedStage: string;
  progress: number;
  resourcePolicy?: Record<string, any>;
  qualification?: Record<string, any>;
  configurationTraceHash?: string;
};

type TaskOperation = 'support_layout_optimization' | 'calculation_full' | 'candidate_comparison' | 'storage_compaction';

function statusText(status: string) {
  return ({ complete: '已完成', ready: '可配置', attention: '需确认', waiting: '待前序', blocked: '阻断' } as Record<string, string>)[status] ?? status;
}

function mb(bytes: unknown) {
  const value = Number(bytes ?? 0) / 1048576;
  return Number.isFinite(value) ? `${value.toFixed(value >= 100 ? 0 : 1)} MB` : '—';
}


const choiceFieldLabels: Record<string, string> = {
  coordinateMode: '坐标处理',
  geologyPolicy: '地质覆盖策略',
  constructionMethod: '施工组织',
  retainingWallFamily: '围护墙体系',
  wallVerticalStrategy: '墙趾与竖向分区',
  supportSystemFamily: '坑内支撑体系',
  cornerTreatment: '角部处理',
  transitionTreatment: '分区转接',
  objectivePreset: '优化主目标',
  fullCalculationCount: '完整计算范围',
  detailLevel: '深化交付等级',
  selection: '设计选择',
};

export default function ProgressiveDesignPanel({
  project,
  runTask,
  onRefresh,
  initialSession,
}: {
  project: Project;
  runTask: (title: string, operation: TaskOperation, payload?: Record<string, unknown>) => Promise<void>;
  onRefresh?: () => Promise<unknown> | unknown;
  initialSession?: ProgressiveDesignSession;
}) {
  const [session, setSession] = useState<ProgressiveDesignSession | undefined>(initialSession);
  const [selectedCode, setSelectedCode] = useState<string>();
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  async function reload() {
    try {
      const value = await api.getProgressiveDesign(project.id) as ProgressiveDesignSession;
      setSession(value);
      setSelectedCode((current) => current || value.recommendedStage || value.currentStage || value.stages?.[0]?.code);
      setError(undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    if (initialSession) {
      setSession(initialSession);
      setSelectedCode((current) => current || initialSession.recommendedStage || initialSession.currentStage || initialSession.stages?.[0]?.code);
      setError(undefined);
      return;
    }
    void reload();
  }, [initialSession, project.id, project.updatedAt, project.retainingSystem?.supportLayoutRepair?.checkedAt]);

  const selected = useMemo(
    () => session?.stages.find((stage) => stage.code === selectedCode) ?? session?.stages[0],
    [session, selectedCode],
  );
  const decisions = session?.config.decisions ?? {};
  const constraints = session?.config.constraints ?? {};
  const choiceGroups = useMemo(() => {
    const groups = new Map<string, NonNullable<Stage['choices']>>();
    for (const choice of selected?.choices ?? []) {
      const field = choice.field ?? (selected?.code === 'support_system_strategy' ? 'supportSystemFamily' : 'selection');
      const rows = groups.get(field) ?? [];
      rows.push(choice);
      groups.set(field, rows);
    }
    return [...groups.entries()];
  }, [selected]);

  async function savePatch(patch: Record<string, unknown>) {
    if (!session) return undefined;
    setSaving(true);
    try {
      const requestedStage = typeof patch.currentStage === 'string' ? patch.currentStage : (selected?.code ?? session.currentStage);
      const updated = await api.updateProgressiveDesign(project.id, {
        ...patch,
        currentStage: requestedStage,
        expectedVersion: session.config.sessionVersion,
      }) as ProgressiveDesignSession;
      setSession(updated);
      if (typeof patch.currentStage === 'string') setSelectedCode(updated.currentStage);
      setError(undefined);
      return updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return undefined;
    } finally {
      setSaving(false);
    }
  }

  async function choose(field: string, value: string | number) {
    await savePatch({ decisions: { [field]: value }, action: `decision:${field}` });
  }

  async function updateConstraint(field: string, value: number | boolean) {
    await savePatch({ constraints: { [field]: value }, action: `constraint:${field}` });
  }

  async function generateCandidates() {
    const updated = await savePatch({ action: 'generate_candidates' });
    const cfg = updated?.config ?? session?.config;
    const d = cfg?.decisions ?? {};
    const c = cfg?.constraints ?? {};
    await runTask('正在按渐进式设计配置生成体系级候选', 'support_layout_optimization', {
      preset: d.objectivePreset ?? 'balanced',
      topologyFamily: d.supportSystemFamily === 'auto' ? undefined : d.supportSystemFamily,
      maxCandidates: Number(d.candidateCount ?? 3),
      searchConfig: {
        spacingMinM: Number(c.supportSpacingMinM ?? 3),
        spacingMaxM: Number(c.supportSpacingMaxM ?? 6),
        preferredSpacingM: Number(c.preferredSupportSpacingM ?? 5),
        columnSpanMaxM: Number(c.columnServiceSpanMaxM ?? 18),
        preserveMuckPath: Boolean(c.preserveMuckPath ?? true),
        avoidObstacleBoundaries: Boolean(c.avoidObstacleBoundaries ?? true),
      },
    });
    await onRefresh?.();
    await reload();
  }

  async function runCalculation() {
    const count = Math.max(1, Math.min(3, Number(decisions.fullCalculationCount ?? 1)));
    await savePatch({ action: 'run_stage_calculation' });
    await runTask(
      count > 1 ? `正在完整计算前 ${count} 个候选方案` : '正在完整计算当前采用方案',
      count > 1 ? 'candidate_comparison' : 'calculation_full',
      count > 1 ? { topN: count } : { topN: 0 },
    );
    await onRefresh?.();
    await reload();
  }

  if (error && !session) return <section className="progressiveDesignPanel errorPanel"><strong>渐进式设计配置读取失败</strong><p>{error}</p><button className="secondary" onClick={() => void reload()}>重新读取</button></section>;
  if (!session || !selected) return <section className="progressiveDesignPanel loadingPanel"><strong>正在解析轮廓并建立渐进式设计过程…</strong></section>;

  const resource = session.resourcePolicy ?? {};
  const fullLimit = resource.apiFullLoadLimitBytes;
  const workerSoft = resource.workerSoftLimitBytes;
  const workerHard = resource.workerHardLimitBytes;
  const available = resource.effectiveAvailableBytes;
  const diskFree = resource.diskFreeBytes;
  const diskReserve = resource.diskReserveBytes;
  const cpuLoadRatio = Number(resource.cpuLoadRatio ?? 0);

  return <section className="progressiveDesignPanel">
    <div className="progressiveHeader">
      <div>
        <span className="sectionKicker">渐进式设计总控</span>
        <h3>先确认设计意图，再逐级增加模型复杂度</h3>
        <p>每一步只要求确认会改变后续结构体系和计算模型的关键决策。可随时返回前序阶段，修改后仅使受影响的下游结果失效。</p>
        <small className="progressiveTrace">配置追踪 {String(session.configurationTraceHash ?? '—').slice(0, 12)} · 待重算起点 {String(session.config.dirtyFromStage ?? '—')}</small>
      </div>
      <div className="progressiveProgress"><strong>{session.progress.toFixed(0)}%</strong><span>流程成熟度</span><progress max={100} value={session.progress} /></div>
    </div>

    <nav className="progressiveStageRail" aria-label="渐进式设计阶段">
      {session.stages.map((stage) => <button
        type="button"
        key={stage.code}
        className={`${stage.code === selected.code ? 'active' : ''} stage-${stage.status}`}
        onClick={() => { setSelectedCode(stage.code); void savePatch({ currentStage: stage.code, action: 'stage_selected' }); }}
      >
        <span>{stage.index}</span><strong>{stage.title}</strong><small>{statusText(stage.status)}</small>
      </button>)}
    </nav>

    <div className={`progressiveStageBody stage-${selected.status}`}>
      <div className="progressiveStageSummary">
        <div><span>STEP {selected.index}</span><h4>{selected.title}</h4><p>{selected.purpose}</p></div>
        <strong>{statusText(selected.status)}</strong>
      </div>
      <div className="progressiveStageMessage"><b>当前判断</b><span>{selected.summary}</span></div>

      {selected.requiredInputs?.length ? <div className="progressiveInputChecklist">
        {selected.requiredInputs.map((item) => <span key={item}>{item}</span>)}
      </div> : null}

      {choiceGroups.length ? <div className="progressiveChoiceGroups">
        {choiceGroups.map(([field, choices]) => <section className="progressiveChoiceGroup" key={field}>
          <div className="progressiveChoiceGroupHeader"><strong>{choiceFieldLabels[field] ?? field}</strong><span>当前：{String(decisions[field] ?? '待选择')}</span></div>
          <div className="progressiveChoiceGrid">
            {choices.map((choice, index) => {
              const currentValue = decisions[field];
              const active = String(currentValue) === String(choice.value);
              const disabled = choice.available === false && field === 'supportSystemFamily';
              return <button
                type="button"
                key={`${field}-${choice.value}-${index}`}
                className={`${active ? 'selected' : ''} ${choice.recommended ? 'recommended' : ''}`}
                disabled={saving || disabled}
                onClick={() => void choose(field, choice.value)}
              >
                <strong>{choice.label}</strong>
                {choice.description ? <span>{choice.description}</span> : null}
                {choice.readiness ? <small>{choice.readiness}</small> : null}
              </button>;
            })}
          </div>
        </section>)}
      </div> : null}

      {selected.code === 'topology_search' ? <div className="progressiveParameterGrid">
        <label>最小支撑间距（m）<input key={`min-${constraints.supportSpacingMinM}`} type="number" min="2" max="10" step="0.5" defaultValue={Number(constraints.supportSpacingMinM ?? 3)} onBlur={(event) => void updateConstraint('supportSpacingMinM', Number(event.target.value))} /></label>
        <label>最大支撑间距（m）<input key={`max-${constraints.supportSpacingMaxM}`} type="number" min="3" max="12" step="0.5" defaultValue={Number(constraints.supportSpacingMaxM ?? 6)} onBlur={(event) => void updateConstraint('supportSpacingMaxM', Number(event.target.value))} /></label>
        <label>优选支撑间距（m）<input key={`preferred-${constraints.preferredSupportSpacingM}`} type="number" min="2" max="12" step="0.5" defaultValue={Number(constraints.preferredSupportSpacingM ?? 5)} onBlur={(event) => void updateConstraint('preferredSupportSpacingM', Number(event.target.value))} /></label>
        <label>立柱最大服务跨（m）<input key={`column-${constraints.columnServiceSpanMaxM}`} type="number" min="6" max="30" step="1" defaultValue={Number(constraints.columnServiceSpanMaxM ?? 18)} onBlur={(event) => void updateConstraint('columnServiceSpanMaxM', Number(event.target.value))} /></label>
        <label>候选数量<select value={Number(decisions.candidateCount ?? 3)} onChange={(event) => void choose('candidateCount', Number(event.target.value))}><option value={3}>3 个</option><option value={5}>5 个</option><option value={8}>8 个</option></select></label>
        <label>完整计算数量<select value={Number(decisions.fullCalculationCount ?? 1)} onChange={(event) => void choose('fullCalculationCount', Number(event.target.value))}><option value={1}>先算推荐方案</option><option value={3}>完整比选前三</option></select></label>
      </div> : null}

      {selected.code === 'stage_calculation' ? <div className="progressiveResourcePanel">
        <div><small>服务器当前可用内存</small><strong>{mb(available)}</strong></div>
        <div><small>API 动态全量预算</small><strong>{mb(fullLimit)}</strong></div>
        <div><small>worker 软/硬上限</small><strong>{mb(workerSoft)} / {mb(workerHard)}</strong></div>
        <div><small>建议重型并发</small><strong>{String(resource.recommendedHeavyConcurrency ?? 1)}</strong></div>
        <div><small>磁盘空闲 / 保留</small><strong>{mb(diskFree)} / {mb(diskReserve)}</strong></div>
        <div><small>CPU 1分钟负载率</small><strong>{(cpuLoadRatio * 100).toFixed(0)}%</strong></div>
        <p>项目完整快照大小不再作为固定阻断条件。网页只读取工作区投影，worker 根据提交时的内存、CPU负载和磁盘余量选择串行、有限并发、分步执行或延后外部化。</p>
      </div> : null}

      <div className="progressiveActionBar">
        <div><strong>推荐下一步</strong><span>{selected.nextAction}</span>{error ? <em>{error}</em> : null}</div>
        {['support_system_strategy', 'topology_search', 'candidate_screening'].includes(selected.code)
          ? <button disabled={saving || selected.blocksNext} onClick={() => void generateCandidates()}>按当前配置生成候选</button>
          : selected.code === 'stage_calculation'
            ? <button disabled={saving || !session.qualification?.calculationAllowed} onClick={() => void runCalculation()}>启动完整计算</button>
            : <button className="secondary" disabled={saving} onClick={() => void savePatch({ action: 'stage_confirmed' })}>{saving ? '保存中…' : '确认本阶段配置'}</button>}
      </div>
    </div>
  </section>;
}
