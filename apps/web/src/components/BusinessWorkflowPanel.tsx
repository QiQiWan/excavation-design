import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type {
  BusinessWorkflowOverview,
  ConstructionPlanStage,
  DesignControlStage,
  DesignScenario,
  DeviationEvent,
  FieldExecutionSnapshot,
  Project,
  WorkflowGate,
} from '../types/domain';

function GateCard({ title, gate, owner, note }: { title: string; gate?: WorkflowGate; owner: string; note: string }) {
  const blockers = gate?.items?.filter((item) => item.blocking) ?? [];
  const statusLabel = gate?.eligible ? '通过' : gate?.status === 'warning' ? '待完善' : '阻断';
  return <article className={`businessGateCard ${gate?.eligible ? 'isPass' : 'isBlocked'}`}>
    <div className="businessGateHeader">
      <div><strong>{title}</strong><span>{owner}</span></div>
      <div className="businessReadiness"><b>{Number(gate?.readiness ?? 0).toFixed(0)}%</b><small>{statusLabel}</small></div>
    </div>
    <p>{gate?.boundary ?? note}</p>
    {blockers.length ? <details><summary>查看 {blockers.length} 项阻断</summary><div className="businessBlockerList">
      {blockers.slice(0, 8).map((item) => <div key={item.code}><b>{item.label}</b><span>责任：{item.responsibility}</span><span>影响：{item.affects}</span><small>{item.action}</small></div>)}
    </div></details> : <div className="businessGatePass">当前门禁没有硬性阻断项。</div>}
  </article>;
}

function numberOrUndefined(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export default function BusinessWorkflowPanel({ project, onChanged }: { project: Project; onChanged?: () => Promise<void> | void }) {
  const [overview, setOverview] = useState<BusinessWorkflowOverview>();
  const [controls, setControls] = useState<DesignControlStage[]>([]);
  const [plans, setPlans] = useState<ConstructionPlanStage[]>([]);
  const [scenarios, setScenarios] = useState<DesignScenario[]>([]);
  const [scenarioEnvelope, setScenarioEnvelope] = useState<Record<string, unknown>>({});
  const [deviations, setDeviations] = useState<DeviationEvent[]>([]);
  const [selectedControlId, setSelectedControlId] = useState('');
  const [selectedPlanId, setSelectedPlanId] = useState('');
  const [planElevation, setPlanElevation] = useState('');
  const [planWater, setPlanWater] = useState('');
  const [planSurcharge, setPlanSurcharge] = useState('');
  const [fieldElevation, setFieldElevation] = useState('');
  const [fieldWater, setFieldWater] = useState('');
  const [scenarioTaskMessage, setScenarioTaskMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const counts = overview?.counts;

  async function load() {
    setLoading(true); setError(undefined);
    try {
      const [workflow, controlRows, scenarioRows, planRows, deviationRows] = await Promise.all([
        api.getBusinessWorkflow(project.id),
        api.getDesignControlStages(project.id),
        api.getDesignScenarios(project.id),
        api.getConstructionPlanStages(project.id),
        api.getDeviationEvents(project.id),
      ]);
      setOverview(workflow);
      setControls(controlRows.stages ?? []);
      setScenarios(scenarioRows.scenarios ?? []);
      setScenarioEnvelope(scenarioRows.envelope ?? {});
      setPlans((planRows.stages ?? []).map(({ compliance: _compliance, ...row }) => row));
      setDeviations(deviationRows.events ?? []);
      const firstControl = selectedControlId || controlRows.stages?.[0]?.id || '';
      const firstPlan = selectedPlanId || planRows.stages?.[0]?.id || '';
      setSelectedControlId(firstControl);
      setSelectedPlanId(firstPlan);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [project.id, project.updatedAt]);

  const designCanProceed = useMemo(() => Boolean(overview?.designIssue?.eligible), [overview]);
  const selectedControl = useMemo(() => controls.find((row) => row.id === selectedControlId), [controls, selectedControlId]);
  const selectedPlan = useMemo(() => plans.find((row) => row.id === selectedPlanId), [plans, selectedPlanId]);

  async function migrate() {
    setLoading(true); setError(undefined);
    try { await api.migrateLegacyDesignStages(project.id, false); await load(); await onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function generateScenarios() {
    setLoading(true); setError(undefined);
    try { await api.generateDesignScenarios(project.id); await load(); await onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function approveDesignControls() {
    if (!controls.length) return;
    setLoading(true); setError(undefined);
    try {
      const result = await api.saveDesignControlStages(project.id, controls.map((row) => ({ ...row, dataStatus: 'approved', updatedAt: new Date().toISOString() })));
      setScenarioTaskMessage(result.calculationInvalidated
        ? '设计控制工况的数值或控制边界发生变化，既有计算与情景包络已失效。'
        : '仅批准设计控制工况，数值输入未变化，既有计算结果保持有效。');
      await load(); await onChanged?.();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function approveAdverseScenarios() {
    const ids = scenarios.filter((row) => row.category !== 'baseline' && row.enabled).map((row) => row.id);
    if (!ids.length) return;
    setLoading(true); setError(undefined);
    try { await api.updateDesignScenarioApproval(project.id, ids, 'approved', true); await load(); await onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function updateScenarioEnvelope() {
    setLoading(true); setError(undefined);
    try { await api.buildDesignScenarioEnvelope(project.id); await load(); await onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function executeApprovedScenarios() {
    const ids = scenarios.filter((row) => row.category !== 'baseline' && row.enabled && row.approvalStatus === 'approved').map((row) => row.id);
    if (!ids.length) return;
    setLoading(true); setError(undefined); setScenarioTaskMessage('');
    try {
      const task = await api.executeApprovedDesignScenarios(project.id, ids, Math.min(12, ids.length));
      setScenarioTaskMessage(`后台任务 ${task.id} 已提交：${task.title}`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }

  async function submitPlan() {
    if (!selectedControl) return;
    const stage: ConstructionPlanStage = {
      id: `cps-${Date.now()}`,
      designControlStageId: selectedControl.id,
      contractorRevision: 'A',
      plannedExcavationElevation: numberOrUndefined(planElevation) ?? selectedControl.excavationElevationLower,
      plannedSupportIds: [...selectedControl.requiredSupportIds],
      plannedPreloads: {},
      plannedGroundwaterLevel: numberOrUndefined(planWater) ?? selectedControl.groundwaterLevelLimit,
      plannedSurcharge: numberOrUndefined(planSurcharge) ?? selectedControl.surchargeLimit,
      approvalStatus: 'submitted',
      submittedBy: '施工单位待确认',
      note: '由施工准备域提交，未修改设计控制工况。',
    };
    setLoading(true); setError(undefined);
    try { await api.saveConstructionPlanStage(project.id, stage); setSelectedPlanId(stage.id); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function submitFieldSnapshot() {
    if (!selectedPlan) return;
    const snapshot: FieldExecutionSnapshot = {
      id: `field-${Date.now()}`,
      constructionPlanStageId: selectedPlan.id,
      observedAt: new Date().toISOString(),
      actualExcavationElevation: numberOrUndefined(fieldElevation) ?? selectedPlan.plannedExcavationElevation,
      activeSupportIds: [...selectedPlan.plannedSupportIds],
      measuredPreloads: {},
      groundwaterLevels: numberOrUndefined(fieldWater) === undefined ? {} : { '现场代表点': Number(fieldWater) },
      concreteStrengths: {},
      evidenceFileIds: [],
      quality: 'provisional',
      source: 'manual',
      note: '现场责任方快照；只用于偏差评估和阶段放行。',
    };
    setLoading(true); setError(undefined);
    try { await api.addFieldExecutionSnapshot(project.id, snapshot); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  return <section className="card businessWorkflowPanel">
    <div className="businessWorkflowTitle">
      <div><h3>业务责任与阶段门禁</h3><p>设计发行、施工准备和现场放行分别评价。尚未发生的现场信息不会降低设计就绪度。</p></div>
      <div className="businessWorkflowActions">
        <button type="button" className="secondary compactButton" disabled={loading} onClick={() => void migrate()}>初始化设计控制工况</button>
        <button type="button" className="secondary compactButton" disabled={loading || !controls.length} onClick={() => void approveDesignControls()}>批准设计控制工况</button>
        <button type="button" className="secondary compactButton" disabled={loading || !counts?.designControlStages} onClick={() => void generateScenarios()}>生成设计包络情景</button>
        <button type="button" className="secondary compactButton" disabled={loading || !scenarios.some((row) => row.category !== 'baseline')} onClick={() => void approveAdverseScenarios()}>批准建议不利情景</button>
        <button type="button" className="secondary compactButton" disabled={loading || !scenarios.some((row) => row.category !== 'baseline' && row.enabled && row.approvalStatus === 'approved')} onClick={() => void executeApprovedScenarios()}>正式复算已批准情景</button>
        <button type="button" className="secondary compactButton" disabled={loading || !scenarios.length} onClick={() => void updateScenarioEnvelope()}>更新真实结果包络</button>
        <button type="button" className="secondary compactButton" disabled={loading} onClick={() => void load()}>{loading ? '刷新中' : '刷新状态'}</button>
      </div>
    </div>
    {error ? <div className="errorBanner">{error}</div> : null}
    {scenarioTaskMessage ? <div className="businessTaskMessage">{scenarioTaskMessage}</div> : null}
    <div className="businessGateGrid">
      <GateCard title="设计成果发行" gate={overview?.designIssue} owner="责任主体：设计单位" note="只检查设计资料、控制工况、计算和校审。" />
      <GateCard title="施工准备" gate={overview?.constructionPreparation} owner="责任主体：施工、监理、专家及监测单位" note="专项方案、专家论证和监测方案在此阶段补充。" />
      <GateCard title="现场阶段放行" gate={overview?.fieldExecution} owner="责任主体：施工、监理与监测单位" note="实际开挖、水位、预加轴力和验收只控制现场放行。" />
    </div>
    <div className="businessWorkflowStats">
      <span>设计控制工况 <b>{counts?.designControlStages ?? 0}</b></span>
      <span>设计情景 <b>{counts?.designScenarios ?? 0}</b></span>
      <span>已批准情景 <b>{scenarios.filter((row) => row.enabled && row.approvalStatus === 'approved').length}</b></span>
      <span>施工计划阶段 <b>{counts?.constructionPlanStages ?? 0}</b></span>
      <span>现场快照 <b>{counts?.fieldSnapshots ?? 0}</b></span>
      <span>未关闭偏差 <b>{counts?.openDeviationEvents ?? 0}</b></span>
    </div>
    <div className="businessScenarioStatus"><strong>包络状态：{String(scenarioEnvelope.status ?? '未生成')}</strong><span>真实结果 {Number(scenarioEnvelope.candidateResultCount ?? 0)} 组；待正式复算 {Array.isArray(scenarioEnvelope.pendingFormalScenarioCodes) ? scenarioEnvelope.pendingFormalScenarioCodes.length : 0} 组。未执行情景不会伪造内力。</span></div>
    <div className={`businessDesignBoundary ${designCanProceed ? 'ok' : 'warn'}`}>
      <strong>{designCanProceed ? '设计链可继续推进' : '设计链存在阻断'}</strong>
      <span>专项施工方案、实际开挖日期、现场验收和实测监测数据均不作为设计文件首次发行的前置条件。</span>
    </div>

    <details className="businessCollaborationPanel">
      <summary>施工计划符合性与现场偏差协同</summary>
      <div className="businessCollaborationGrid">
        <section>
          <h4>施工计划阶段</h4>
          <p>由施工单位提交，并绑定设计控制工况。系统只做允许域比对。</p>
          <label>设计控制工况<select value={selectedControlId} onChange={(event) => setSelectedControlId(event.target.value)}>{controls.map((row) => <option value={row.id} key={row.id}>{row.name}</option>)}</select></label>
          <label>计划开挖标高（m）<input value={planElevation} onChange={(event) => setPlanElevation(event.target.value)} placeholder={selectedControl ? String(selectedControl.excavationElevationLower) : ''} /></label>
          <label>计划控制水位（m）<input value={planWater} onChange={(event) => setPlanWater(event.target.value)} placeholder={selectedControl?.groundwaterLevelLimit === undefined ? '' : String(selectedControl.groundwaterLevelLimit)} /></label>
          <label>计划坑边荷载（kPa）<input value={planSurcharge} onChange={(event) => setPlanSurcharge(event.target.value)} placeholder={selectedControl?.surchargeLimit === undefined ? '' : String(selectedControl.surchargeLimit)} /></label>
          <button type="button" disabled={loading || !selectedControl} onClick={() => void submitPlan()}>提交并检查计划</button>
        </section>
        <section>
          <h4>现场执行快照</h4>
          <p>由现场责任方填写。超出设计允许域时自动生成偏差事件。</p>
          <label>施工计划阶段<select value={selectedPlanId} onChange={(event) => setSelectedPlanId(event.target.value)}>{plans.map((row) => <option value={row.id} key={row.id}>{row.contractorRevision} · {row.plannedExcavationElevation ?? '未定标高'}</option>)}</select></label>
          <label>实际开挖标高（m）<input value={fieldElevation} onChange={(event) => setFieldElevation(event.target.value)} placeholder={selectedPlan?.plannedExcavationElevation === undefined ? '' : String(selectedPlan.plannedExcavationElevation)} /></label>
          <label>实测代表水位（m）<input value={fieldWater} onChange={(event) => setFieldWater(event.target.value)} placeholder={selectedPlan?.plannedGroundwaterLevel === undefined ? '' : String(selectedPlan.plannedGroundwaterLevel)} /></label>
          <button type="button" disabled={loading || !selectedPlan} onClick={() => void submitFieldSnapshot()}>提交现场快照</button>
        </section>
      </div>
      <div className="businessDeviationList">
        <h4>偏差事件</h4>
        {deviations.length ? deviations.slice(0, 12).map((row) => <div className={`businessDeviation ${row.severity}`} key={row.id}><b>{row.deviationType}</b><span>{row.severity} · {row.status}</span><small>{row.workHoldRequired ? '建议暂停相关工序；' : ''}{row.recalculationRequired ? '需要复算；' : ''}{row.designerResponseRequired ? '需要设计回复。' : '由现场责任方闭环。'}</small></div>) : <p>当前没有偏差事件。</p>}
      </div>
    </details>
  </section>;
}
