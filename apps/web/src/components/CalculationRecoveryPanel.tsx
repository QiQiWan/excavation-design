import type { Project } from '../types/domain';
import { api } from '../api/client';

function latestResult(project: Project) {
  return project.calculationResults?.[project.calculationResults.length - 1];
}

function numberText(value: unknown, unit = '') {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(Math.abs(number) >= 100 ? 1 : 3).replace(/\.0+$/, '')}${unit}` : '-';
}

function engineeringValue(value: unknown): string {
  if (value && typeof value === 'object') {
    const row = value as Record<string, unknown>;
    if (row.widthM !== undefined || row.heightM !== undefined) return `宽 ${numberText(row.widthM, 'm')} × 高 ${numberText(row.heightM, 'm')}`;
    return Object.values(row).map((item) => String(item)).join(' / ');
  }
  return String(value ?? '-');
}

export default function CalculationRecoveryPanel({ project, runStep, onNavigate }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void>; onNavigate?: (targetPanel: string) => void }) {
  const result = latestResult(project);
  const diagnostics = (result?.designIterationSummary?.calculationDiagnostics ?? result?.reportDiagramData?.calculationDiagnostics) as Record<string, any> | undefined;
  const assurance = (result?.calculationAssurance ?? result?.designIterationSummary?.industrialCalculationAssurance) as Record<string, any> | undefined;
  const assuranceStatus = String(assurance?.status ?? (result ? 'missing' : 'pending'));
  const contract = (assurance?.contract ?? {}) as Record<string, any>;
  const stageCoverage = (assurance?.stageCoverage ?? {}) as Record<string, any>;
  const numericalQuality = (assurance?.numericalQuality ?? {}) as Record<string, any>;
  const independentCheck = (assurance?.independentCheck ?? {}) as Record<string, any>;
  const traceability = (assurance?.traceability ?? {}) as Record<string, any>;
  const assuranceIssues = (assurance?.issues ?? []) as Record<string, any>[];
  const roots = (diagnostics?.rootCauses ?? []) as Record<string, any>[];
  const comparison = diagnostics?.comparisonWithPrevious as Record<string, any> | undefined;
  const strengthLoop = diagnostics?.strengthDesignLoop as Record<string, any> | undefined;
  const intelligentClosure = (result?.designIterationSummary?.intelligentDesignClosure ?? result?.reportDiagramData?.intelligentDesignClosure) as Record<string, any> | undefined;
  const closureHistory = (intelligentClosure?.history ?? []) as Record<string, any>[];
  const interventionOptions = (intelligentClosure?.interventionOptions ?? []) as Record<string, any>[];
  const topology = (diagnostics?.topologyPreflight ?? result?.designIterationSummary?.topologyPreflight) as Record<string, any> | undefined;
  const waleRepair = topology?.waleSupportBayRepair as Record<string, any> | undefined;
  const wallEmbedment = (diagnostics?.wallEmbedmentPreflight ?? result?.designIterationSummary?.wallEmbedmentPreflight) as Record<string, any> | undefined;
  const beforeAudit = waleRepair?.auditBefore as Record<string, any> | undefined;
  const afterAudit = waleRepair?.auditAfter as Record<string, any> | undefined;
  const failCount = Number(result?.checkSummary?.fail ?? 0);
  const warningCount = Number(result?.checkSummary?.warning ?? 0);
  const repaired = Boolean(topology?.changed || diagnostics?.supportTopologySynchronization?.synchronized);
  if (!result && !project.retainingSystem) return null;

  return <section className={`calculationRecoveryPanel ${failCount > 0 ? 'blocked' : repaired ? 'repaired' : 'ready'}`} aria-labelledby="calculation-recovery-title">
    <div className="sectionLead">
      <div>
        <h3 id="calculation-recovery-title">计算诊断与强度驱动恢复</h3>
        <p className="small">先闭合墙趾嵌固稳定，再修复支撑拓扑、围檩支点间距和拆换撑传力路径，随后执行墙、围檩、支撑截面与配筋验算。</p>
      </div>
      <span className={`diagnosticState ${failCount > 0 ? 'fail' : warningCount > 0 ? 'warn' : 'pass'}`}>{failCount > 0 ? `${failCount} 项阻断` : repaired ? '已自动修复并复算' : result ? '计算链路有效' : '待计算'}</span>
    </div>

    {result && <div className={`calculationAssurancePanel ${assuranceStatus}`} aria-label="工业计算质量包">
      <div className="calculationAssuranceHeader">
        <div>
          <strong>工业计算质量包</strong>
          <p className="small">冻结输入、施工阶段覆盖、数值质量、独立计算对账和规范追溯共同决定计算结果能否进入工程交付。</p>
        </div>
        <span className={`diagnosticState ${assuranceStatus === 'pass' ? 'pass' : assuranceStatus === 'fail' || assuranceStatus === 'missing' ? 'fail' : 'warn'}`}>
          {assuranceStatus === 'pass' ? '计算基线通过' : assuranceStatus === 'fail' ? '计算基线阻断' : assuranceStatus === 'missing' ? '缺少质量包' : '需要复核'}
        </span>
      </div>
      <div className="assuranceMetricGrid">
        <div><span>计算合同</span><strong title={String(result.calculationContractId ?? contract.contractId ?? '')}>{String(result.calculationContractId ?? contract.contractId ?? '-').slice(0, 24)}</strong></div>
        <div><span>阶段覆盖</span><strong>{Number(stageCoverage.actual ?? 0)} / {Number(stageCoverage.expected ?? 0)}</strong></div>
        <div><span>最大条件数</span><strong>{numberText(numericalQuality.maxConditionNumber)}</strong></div>
        <div><span>最大平衡残差</span><strong>{numberText(numericalQuality.maxRelativeResidual)}</strong></div>
        <div><span>回退求解</span><strong>{Number(numericalQuality.fallbackCount ?? 0)} 次</strong></div>
        <div><span>独立位移差</span><strong>{numberText(Number(independentCheck.maxWallDisplacementRelativeDifference ?? 0) * 100, '%')}</strong></div>
        <div><span>支撑对账复核</span><strong>{Number(independentCheck.supportReconciliationWarningCount ?? 0) + Number(independentCheck.supportReconciliationManualReviewCount ?? 0)} 项</strong></div>
        <div><span>规范追溯完整率</span><strong>{numberText(Number(traceability.coverage ?? 0) * 100, '%')}</strong></div>
      </div>
      <div className="calculationHashStrip">
        <span title={String(result.inputSnapshotHash ?? '')}>输入 {String(result.inputSnapshotHash ?? '-').slice(0, 12)}</span>
        <span title={String(result.adoptedDesignSnapshotHash ?? '')}>采用设计 {String(result.adoptedDesignSnapshotHash ?? '-').slice(0, 12)}</span>
        <span title={String(result.resultHash ?? '')}>结果 {String(result.resultHash ?? '-').slice(0, 12)}</span>
      </div>
      {assuranceIssues.some((item) => String(item.status) !== 'pass') && <div className="calculationAssuranceIssues">
        {assuranceIssues.filter((item) => String(item.status) !== 'pass').slice(0, 4).map((item) => <article key={String(item.code)} className={String(item.status ?? 'warning')}>
          <strong>{String(item.title ?? item.code)}</strong>
          <p>{String(item.message ?? '')}</p>
          {item.requiredAction && <em>{String(item.requiredAction)}</em>}
        </article>)}
      </div>}
    </div>}

    {strengthLoop && <div className="strengthLoopPanel" aria-label="强度驱动设计闭环">
      <div className="strengthLoopTitle">
        <strong>强度驱动设计闭环</strong>
        <span className={`diagnosticState ${strengthLoop.strengthStatus === 'fail' || strengthLoop.stiffnessStatus === 'fail' || strengthLoop.topologyStatus === 'fail' ? 'fail' : 'pass'}`}>
          拓扑 {String(strengthLoop.topologyStatus ?? '-')} · 强度 {String(strengthLoop.strengthStatus ?? '-')} · 刚度 {String(strengthLoop.stiffnessStatus ?? '-')}
        </span>
      </div>
      <div className="diagnosticComparison">
        <div><span>围檩最大支点间距</span><strong>{numberText(strengthLoop.waleBayBeforeM ?? beforeAudit?.maxBayM, ' m')} → {numberText(strengthLoop.waleBayAfterM ?? afterAudit?.maxBayM, ' m')}</strong></div>
        <div><span>自动增补支撑</span><strong>{Number(strengthLoop.addedSupportCount ?? topology?.addedSupportCount ?? 0)} 根</strong></div>
        <div><span>拓扑修复</span><strong>{strengthLoop.topologyAdjusted ? '已执行' : '无需调整'}</strong></div>
        <div><span>最大设计迭代</span><strong>{Number(strengthLoop.iterationLimit ?? project.designSettings?.maxDesignIterations ?? 3)} 次</strong></div>
      </div>
      {wallEmbedment && <div className="diagnosticComparison" aria-label="墙趾嵌固稳定闭环">
        <div><span>统一墙趾标高</span><strong>{numberText(wallEmbedment.beforeBottomElevationM, ' m')} → {numberText(wallEmbedment.afterBottomElevationM, ' m')}</strong></div>
        <div><span>最小嵌固筛查系数</span><strong>{numberText(wallEmbedment.beforeMinimumFactor)} → {numberText(wallEmbedment.afterMinimumFactor)}</strong></div>
        <div><span>自动加深</span><strong>{numberText(wallEmbedment.addedEmbedmentM, ' m')}</strong></div>
        <div><span>墙趾设计状态</span><strong>{String(wallEmbedment.status ?? '-')}</strong></div>
      </div>}
      <p className="small strengthLoopNote">墙趾嵌固采用统一标高前置闭环；拆换撑阶段保留楼板/换撑标高参与竖向荷载分带；闭合围檩端部按刚性转角节点形成环向传力。模型假定与复核边界均写入计算书。</p>
    </div>}

    {intelligentClosure && <div className={`intelligentClosurePanel ${String(intelligentClosure.status ?? 'needs_intervention')}`} aria-label="校核补强复算智能闭环">
      <div className="intelligentClosureHeader">
        <div><strong>校核—补强—复算智能闭环</strong><p className="small">把低于目标的验算项反馈给设计器，按构件补强后重新计算；不自动修改土层、水位和锁定施工顺序。</p></div>
        <span className={`diagnosticState ${intelligentClosure.calculationClosed ? 'pass' : intelligentClosure.structuralClosed ? 'warn' : 'fail'}`}>
          {intelligentClosure.calculationClosed
            ? Number(intelligentClosure.reviewCount ?? 0) > 0 ? '数值闭合，复核项已列出' : '全部定量项闭合'
            : intelligentClosure.structuralClosed ? '结构闭合，待专业复核' : '需要继续补强'}
        </span>
      </div>
      <div className="assuranceMetricGrid closureMetrics">
        <div><span>自动复算</span><strong>{Number(intelligentClosure.executedIterations ?? 0)} / {Number(intelligentClosure.maximumIterations ?? 0)} 轮</strong></div>
        <div><span>结构硬失败</span><strong>{Number(intelligentClosure.structuralFailCount ?? 0)} 项</strong></div>
        <div><span>目标储备不足</span><strong>{Number(intelligentClosure.reserveShortfallCount ?? 0)} 项</strong></div>
        <div><span>专业复核提醒</span><strong>{Number(intelligentClosure.reviewGroupCount ?? intelligentClosure.reviewCount ?? 0)} 类 / {Number(intelligentClosure.reviewCount ?? 0)} 个对象</strong></div>
        <div><span>当前策略</span><strong>{String(intelligentClosure.strategy ?? 'balanced') === 'stiffness_first' ? '刚度优先' : String(intelligentClosure.strategy ?? '') === 'economic_zoned' ? '经济分区' : String(intelligentClosure.strategy ?? '') === 'section_first' ? '截面优先' : '安全与经济平衡'}</strong></div>
      </div>
      {closureHistory.length ? <div className="closureIterationList">{closureHistory.map((round) => <details key={String(round.iteration)} open={Number(round.iteration) === closureHistory.length}>
        <summary>第 {String(round.iteration)} 轮：定量未闭合 {String(round.quantitativeOpenBefore ?? 0)} 项，补强 {String((round.actions ?? []).length)} 项</summary>
        {(round.actions ?? []).length ? <ul>{(round.actions as Record<string, any>[]).slice(0, 12).map((action, index) => <li key={`${String(action.objectId ?? action.action)}-${index}`}><b>{String(action.objectCode ?? '支护体系')}</b><span>{String(action.reason ?? '已执行自动强化')}</span>{action.before !== undefined ? <em>{engineeringValue(action.before)} → {engineeringValue(action.after)} {String(action.unit ?? '')}</em> : null}</li>)}</ul> : <p className="small">本轮没有可安全自动执行的增强动作，系统已转为人工交互建议。</p>}
      </details>)}</div> : null}
      {interventionOptions.length ? <div className="closureOptionGrid">
        {interventionOptions.slice(0, 12).map((option) => <article key={String(option.actionId)} className={option.automaticAllowed ? 'automatic' : 'manual'}>
          <header><strong>{String(option.label ?? option.actionId)}</strong><span>{option.automaticAllowed ? '可自动执行' : '需工程师确认'}</span></header>
          {option.objectCode ? <p><b>{String(option.objectCode)}</b> · {String(option.reason ?? '')}</p> : null}
          <p>{String(option.instruction ?? '')}</p>
          <footer><span>位置：{String(option.targetPanel ?? '计算验算')}</span>{option.automaticAllowed ? <button onClick={() => runStep(`正在应用：${String(option.label)}`, () => api.applyCalculationClosureAction(project.id, { actionId: String(option.actionId), value: option.proposedValue }))}>应用并继续闭环</button> : <button className="secondary" onClick={() => onNavigate?.(String(option.targetPanel ?? '计算验算'))}>定位并处理</button>}</footer>
        </article>)}
      </div> : null}
      <p className="small boundaryNote">{String(intelligentClosure.engineeringBoundary ?? '')}</p>
    </div>}

    {(roots.length > 0 || failCount > 0) && <div className="diagnosticRootGrid">
      {(roots.length ? roots : [{ code: 'CALCULATION_CHECK_DETAILS_REQUIRED', title: '存在硬性校核未闭环', description: `当前共有 ${failCount} 项 fail。请打开校核清单查看规则、构件与控制工况。`, recommendedAction: '重新运行诊断；若仍无根因卡片，按校核清单逐项处理并保留规则 ID。', severity: 'fail' }]).slice(0, 6).map((item) => <article key={String(item.code)} className={`diagnosticRootCard ${String(item.severity ?? 'warning')}`}>
        <strong>{String(item.title ?? item.code)}</strong>
        <p>{String(item.description ?? '')}</p>
        <em>{String(item.recommendedAction ?? '')}</em>
      </article>)}
    </div>}

    {comparison && <div className="diagnosticComparison" aria-label="修复前后计算指标">
      <div><span>阻断项</span><strong>{comparison.failCount?.before ?? '-'} → {comparison.failCount?.after ?? '-'}</strong></div>
      <div><span>最大位移</span><strong>{numberText(comparison.maxDisplacementMm?.before, ' mm')} → {numberText(comparison.maxDisplacementMm?.after, ' mm')}</strong></div>
      <div><span>最大弯矩</span><strong>{numberText(comparison.maxWallMomentKnMPerM?.before)} → {numberText(comparison.maxWallMomentKnMPerM?.after)}</strong></div>
      <div><span>最大剪力</span><strong>{numberText(comparison.maxWallShearKnPerM?.before)} → {numberText(comparison.maxWallShearKnPerM?.after)}</strong></div>
    </div>}

    <div className="actionStrip simplifiedActions">
      <button aria-label="诊断并执行强度闭环" disabled={!project.retainingSystem} onClick={() => runStep('正在执行校核—补强—复算智能闭环', () => api.diagnoseAndRepairCalculation(project.id))}>{intelligentClosure?.calculationClosed ? '重新验证智能闭环' : '自动诊断、补强并复算'}</button>
      <span className="small">系统逐轮定位控制构件、调用设计器强化墙厚/梁截面/支撑，再用相同施工阶段复算；不能安全自动修改的资料和专业假定会转为可操作建议。</span>
    </div>
  </section>;
}
