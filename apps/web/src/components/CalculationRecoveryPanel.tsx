import type { Project } from '../types/domain';
import { api } from '../api/client';

function latestResult(project: Project) {
  return project.calculationResults?.[project.calculationResults.length - 1];
}

function numberText(value: unknown, unit = '') {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(Math.abs(number) >= 100 ? 1 : 3).replace(/\.0+$/, '')}${unit}` : '-';
}

export default function CalculationRecoveryPanel({ project, runStep }: { project: Project; runStep: (label: string, step: () => Promise<unknown>) => Promise<void> }) {
  const result = latestResult(project);
  const diagnostics = (result?.designIterationSummary?.calculationDiagnostics ?? result?.reportDiagramData?.calculationDiagnostics) as Record<string, any> | undefined;
  const roots = (diagnostics?.rootCauses ?? []) as Record<string, any>[];
  const comparison = diagnostics?.comparisonWithPrevious as Record<string, any> | undefined;
  const failCount = Number(result?.checkSummary?.fail ?? 0);
  const warningCount = Number(result?.checkSummary?.warning ?? 0);
  const repaired = Boolean(diagnostics?.topologyPreflight?.changed || diagnostics?.supportTopologySynchronization?.synchronized);
  if (!result && !project.retainingSystem) return null;

  return <section className={`calculationRecoveryPanel ${failCount > 0 ? 'blocked' : repaired ? 'repaired' : 'ready'}`} aria-labelledby="calculation-recovery-title">
    <div className="sectionLead">
      <div>
        <h3 id="calculation-recovery-title">计算诊断与自动恢复</h3>
        <p className="small">先检查异形基坑回墙支撑、施工阶段构件引用和控制墙段，再决定配筋或截面调整。</p>
      </div>
      <span className={`diagnosticState ${failCount > 0 ? 'fail' : warningCount > 0 ? 'warn' : 'pass'}`}>{failCount > 0 ? `${failCount} 项阻断` : repaired ? '已自动修复复算' : result ? '计算链路有效' : '待计算'}</span>
    </div>

    {(roots.length > 0 || failCount > 0) && <div className="diagnosticRootGrid">
      {(roots.length ? roots : [{ code: 'UNCLASSIFIED_CALCULATION_FAILURE', title: '存在未分类计算阻断', description: '当前结果来自旧版本或尚未完成根因诊断。', recommendedAction: '运行诊断修复，系统将检查回墙支撑、工况引用、墙体抗剪和配筋构造。', severity: 'fail' }]).slice(0, 4).map((item) => <article key={String(item.code)} className={`diagnosticRootCard ${String(item.severity ?? 'warning')}`}>
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
      <button disabled={!project.retainingSystem} onClick={() => runStep('正在诊断支撑拓扑、同步施工工况并重新计算', () => api.diagnoseAndRepairCalculation(project.id))}>诊断并自动修复复算</button>
      <span className="small">自动修复只增补缺失的凹角回墙局部次对撑，保留现有人工支撑；新构件仍需复核净空、节点和施工顺序。</span>
    </div>
  </section>;
}
