import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { DesignCoreOverview, ExternalCollaborationRecord, ParameterGovernance, Project } from '../types/domain';

export type PrimaryDesignStageKey = 'basis' | 'input' | 'scheme' | 'calculation' | 'reinforcement' | 'deliverables';

interface Props {
  project: Project;
  onChanged?: () => void | Promise<void>;
  activeStage?: PrimaryDesignStageKey;
  onNavigateStage?: (stage: PrimaryDesignStageKey) => void;
  onClose?: () => void;
}

type Tab = 'overview' | 'parameters' | 'rules' | 'schemes' | 'reinforcement' | 'delivery' | 'collaboration';

type QualityGroup = {
  key: PrimaryDesignStageKey;
  title: string;
  evidenceStageIds: string[];
  description: string;
};

const QUALITY_GROUPS: QualityGroup[] = [
  { key: 'basis', title: '设计基准', evidenceStageIds: ['D1_BASIS'], description: '规范、参数来源和项目控制值。' },
  { key: 'input', title: '工程输入', evidenceStageIds: ['D2_INPUT'], description: '地勘、水位、周边条件和基坑几何。' },
  { key: 'scheme', title: '围护方案', evidenceStageIds: ['D3_SCHEME_SEARCH', 'D4_RETAINING_DESIGN'], description: '候选搜索、完整比选和围护体系联合设计。' },
  { key: 'calculation', title: '计算验算', evidenceStageIds: ['D5_CALCULATION'], description: '施工工况、数值健康和逐构件结果包络。' },
  { key: 'reinforcement', title: '配筋深化', evidenceStageIds: ['D6_REINFORCEMENT'], description: '配筋回代、节点构造和可施工性检查。' },
  { key: 'deliverables', title: '成果交付', evidenceStageIds: ['D7_DRAWINGS', 'D8_REPORT', 'D9_REVIEW_ISSUE'], description: '施工图、计算书、校审、快照和发行。' },
];

function statusText(status: string) {
  return ({ ready: '就绪', warning: '预警', blocked: '阻断', not_started: '未开始', missing: '缺失', qualified: '合格' } as Record<string, string>)[status] ?? status;
}

function groupStatus(statuses: string[]) {
  if (statuses.includes('blocked')) return 'blocked';
  if (statuses.includes('warning')) return 'warning';
  if (statuses.length && statuses.every((status) => status === 'ready')) return 'ready';
  return 'not_started';
}

export default function DesignCoreWorkflowPanel({ project, onChanged, activeStage, onNavigateStage, onClose }: Props) {
  const [overview, setOverview] = useState<DesignCoreOverview>();
  const [parameters, setParameters] = useState<ParameterGovernance>();
  const [rules, setRules] = useState<Record<string, any>>({});
  const [schemes, setSchemes] = useState<Record<string, any>>({});
  const [reinforcement, setReinforcement] = useState<Record<string, any>>({});
  const [delivery, setDelivery] = useState<Record<string, any>>({});
  const [collaboration, setCollaboration] = useState<Record<string, any>>({ records: [], reviewRequests: [] });
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [referenceTitle, setReferenceTitle] = useState('');
  const [referenceSummary, setReferenceSummary] = useState('');

  const loadSequence = useRef(0);

  async function load() {
    const sequence = ++loadSequence.current;
    setLoading(true); setError(undefined);
    try {
      const bundle = await api.getDesignCoreBundle(project.id);
      if (sequence !== loadSequence.current) return;
      setOverview(bundle.overview); setParameters(bundle.parameters); setRules(bundle.rules); setSchemes(bundle.schemes);
      setReinforcement(bundle.reinforcement); setDelivery(bundle.delivery); setCollaboration(bundle.collaboration);
    } catch (reason) {
      if (sequence === loadSequence.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    return () => { loadSequence.current += 1; };
  }, [project.id]);

  const groupedQuality = useMemo(() => {
    const byId = new Map((overview?.stages ?? []).map((stage) => [stage.stageId, stage]));
    return QUALITY_GROUPS.map((group) => {
      const rows = group.evidenceStageIds.map((id) => byId.get(id)).filter(Boolean) as NonNullable<DesignCoreOverview['stages']>[number][];
      const blockers = [...new Set(rows.flatMap((row) => row.blockers ?? []))];
      const warnings = [...new Set(rows.flatMap((row) => row.warnings ?? []))];
      const nextActions = [...new Set(rows.flatMap((row) => row.nextActions ?? []))];
      const readiness = rows.length ? rows.reduce((sum, row) => sum + Number(row.readiness ?? 0), 0) / rows.length : 0;
      return {
        ...group,
        status: groupStatus(rows.map((row) => String(row.status ?? 'not_started'))),
        readiness: Math.round(readiness * 10) / 10,
        blockers,
        warnings,
        nextActions,
        evidenceTitles: rows.map((row) => row.title),
      };
    });
  }, [overview]);

  const criticalUnconfirmed = useMemo(() => parameters?.records.filter((row) => row.critical && !row.usableForFormalDesign) ?? [], [parameters]);
  const criticalConfirmable = useMemo(() => criticalUnconfirmed.filter((row) => row.sourceEligibleForFormalDesign && row.value != null), [criticalUnconfirmed]);
  const criticalSourceBlocked = criticalUnconfirmed.length - criticalConfirmable.length;

  async function confirmCritical() {
    if (!criticalConfirmable.length) return;
    setLoading(true); setError(undefined);
    try {
      const result = await api.confirmDesignParameters(project.id, criticalConfirmable.map((row) => ({ parameterKey: row.parameterKey, confirmationStatus: 'confirmed', formalDesignAllowed: true })));
      const rejected = Number((result as any)?.update?.rejectedCount ?? 0);
      await load();
      if (rejected > 0) setError(`${rejected} 个参数因来源不满足正式设计要求，未被批准。`);
      await onChanged?.();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function createSnapshot() {
    setLoading(true); setError(undefined);
    try { await api.createDesignSnapshot(project.id, 'internal_review'); await load(); await onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  async function submitReference() {
    if (!referenceTitle.trim()) return;
    const record: ExternalCollaborationRecord = {
      id: `collab-${Date.now()}`, category: 'construction_reference', title: referenceTitle.trim(), summary: referenceSummary.trim(),
      affectedObjectIds: [], fileIds: [], designReviewRequired: true, status: 'received', sourceParty: '外部责任方',
    };
    setLoading(true); setError(undefined);
    try { await api.addDesignCollaboration(project.id, record); setReferenceTitle(''); setReferenceSummary(''); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); }
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: '质量总览' }, { id: 'parameters', label: '参数证据' }, { id: 'rules', label: '条文证据' },
    { id: 'schemes', label: '方案证据' }, { id: 'reinforcement', label: '配筋证据' }, { id: 'delivery', label: '交付证据' }, { id: 'collaboration', label: '外部资料' },
  ];

  return <section className="designCorePanel designAssurancePanel">
    <header className="designCoreHeader">
      <div><span className="sectionKicker">辅助检查，不承担流程导航</span><h3>设计质量与追溯中心</h3><p>主流程仅保留上方六个设计步骤。本中心按需汇总参数、规则、方案、计算、配筋和交付证据，关闭后不影响当前步骤。</p></div>
      <div className="designCoreHeaderStatus"><b>{overview?.overallReadiness ?? 0}%</b><span>{statusText(overview?.status ?? 'not_started')}</span><div className="buttonRow"><button type="button" className="secondary compactButton" onClick={() => void load()} disabled={loading}>{loading ? '刷新中' : '刷新证据'}</button>{onClose ? <button type="button" className="secondary compactButton" onClick={onClose}>关闭</button> : null}</div></div>
    </header>
    {error ? <div className="errorBanner">{error}</div> : null}
    <nav className="designCoreTabs" aria-label="设计质量证据分类">{tabs.map((item) => <button type="button" key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>

    {tab === 'overview' ? <div className="designCoreStages designQualityGroups">
      {groupedQuality.map((group, index) => <article className={`designCoreStage ${group.status} ${activeStage === group.key ? 'current' : ''}`} key={group.key}>
        <div className="designCoreStageIndex">{index + 1}</div><div className="designCoreStageBody"><div className="designCoreStageTitle"><strong>{group.title}</strong><span>{group.readiness}% · {statusText(group.status)}</span></div>
          <p>{group.description}</p>
          {group.blockers.length ? <ul>{group.blockers.slice(0, 3).map((row) => <li key={row}>{row}</li>)}</ul> : <p>当前质量证据未发现硬阻断。</p>}
          {group.warnings.length ? <small>{group.warnings[0]}</small> : <small>{group.nextActions[0] ?? '随主流程推进自动更新。'}</small>}
          <div className="qualityEvidenceMeta"><span>证据域：{group.evidenceTitles.join('、') || '待生成'}</span>{onNavigateStage ? <button type="button" className="secondary compactButton" onClick={() => onNavigateStage(group.key)}>{activeStage === group.key ? '返回当前步骤' : '进入该步骤'}</button> : null}</div>
        </div>
      </article>)}
    </div> : null}

    {tab === 'parameters' ? <div className="designCoreSection">
      <div className="designCoreSummary"><span>参数 {parameters?.total ?? 0}</span><span>已确认 {parameters?.confirmed ?? 0}</span><span>正式阻断 {parameters?.formalBlockerCount ?? 0}</span>{criticalSourceBlocked > 0 ? <span className="designCoreSourceBlock">来源待补 {criticalSourceBlocked}</span> : null}<button type="button" disabled={loading || !criticalConfirmable.length} onClick={() => void confirmCritical()}>确认可正式使用的关键参数</button></div>
      <div className="designCoreTable"><table><thead><tr><th>参数</th><th>数值</th><th>来源</th><th>状态</th><th>影响</th></tr></thead><tbody>{(parameters?.records ?? []).slice(0, 80).map((row) => <tr key={row.parameterKey} className={!row.usableForFormalDesign && row.critical ? 'blockedRow' : ''}><td>{row.displayName}</td><td>{String(row.value ?? '-')} {row.unit ?? ''}</td><td>{row.sourceType}<small>{row.sourceReference ?? ''}</small></td><td>{row.confirmationStatus}{row.critical ? ' · 关键' : ''}<small>{row.formalEligibilityReason ?? ''}</small></td><td>{row.affects.join('、')}</td></tr>)}</tbody></table></div>
    </div> : null}

    {tab === 'rules' ? <div className="designCoreSection"><div className="designCoreSummary"><span>规则 {Number(rules.ruleCount ?? 0)}</span><span>已执行 {Number(rules.executedRuleCount ?? 0)}</span><span>覆盖率 {Math.round(Number(rules.coverageRatio ?? 0) * 100)}%</span></div><div className="designCoreTable"><table><thead><tr><th>规则</th><th>条文</th><th>实现状态</th><th>执行</th><th>结论</th></tr></thead><tbody>{(rules.rows ?? []).map((row: any) => <tr key={row.ruleId}><td>{row.name ?? row.ruleId}<small>{row.ruleId}</small></td><td>{row.clauseReference ?? '-'}</td><td>{row.implementationStatus}</td><td>{row.executionCount}</td><td>{row.resultStatus}</td></tr>)}</tbody></table></div><p className="designCoreBoundary">{rules.boundary}</p></div> : null}

    {tab === 'schemes' ? <div className="designCoreSection"><div className="designCoreSummary"><span>候选 {Number(schemes.candidateCount ?? 0)}</span><span>体系族 {Number(schemes.familyCount ?? 0)}</span><span>完整计算 {Number(schemes.fullyCalculatedCount ?? 0)}</span><span>采用方案 {schemes.selectedCandidateId ?? '未选'}</span></div><div className="designCoreLevels">{(schemes.levels ?? []).map((row: any) => <div className={row.status} key={row.level}><b>L{row.level}</b><span>{row.name}</span><em>{statusText(row.status)}</em></div>)}</div>{(schemes.blockers ?? []).map((row: string) => <div className="warningBanner" key={row}>{row}</div>)}</div> : null}

    {tab === 'reinforcement' ? <div className="designCoreSection"><div className="designCoreSummary"><span>构件 {Number(reinforcement.componentCount ?? 0)}</span><span>失败 {Number(reinforcement.failCount ?? 0)}</span><span>预警 {Number(reinforcement.warningCount ?? 0)}</span><span>需回代 {Number(reinforcement.sectionFeedbackRequiredCount ?? 0)}</span></div><div className="designCoreTable"><table><thead><tr><th>构件</th><th>类型</th><th>状态</th><th>缺失钢筋</th><th>回代</th></tr></thead><tbody>{(reinforcement.records ?? []).slice(0, 100).map((row: any) => <tr key={row.objectId} className={row.status === 'fail' ? 'blockedRow' : ''}><td>{row.code}</td><td>{row.componentKind}</td><td>{row.status}</td><td>{(row.missingBarTypes ?? []).join('、') || '-'}</td><td>{row.sectionFeedbackRequired ? '需要' : '完成'}</td></tr>)}</tbody></table></div></div> : null}

    {tab === 'delivery' ? <div className="designCoreSection"><div className="designCoreSummary"><span>计算当前性 {delivery.calculationCurrent ? '当前' : '失效'}</span><span>缺图 {Number(delivery.missingDrawingTypes?.length ?? 0)}</span><span>缺计算书章节 {Number(delivery.missingReportSections?.length ?? 0)}</span><button type="button" disabled={loading} onClick={() => void createSnapshot()}>生成统一设计快照</button></div><div className="designCoreDeliveryGrid"><section><h4>缺失图种</h4><p>{(delivery.missingDrawingTypes ?? []).join('、') || '无'}</p></section><section><h4>缺失计算书章节</h4><p>{(delivery.missingReportSections ?? []).join('、') || '无'}</p></section><section><h4>发行阻断</h4><p>{(delivery.blockers ?? []).join('；') || '无'}</p></section></div></div> : null}

    {tab === 'collaboration' ? <div className="designCoreSection"><p className="designCoreBoundary">{collaboration.boundary}</p><div className="designCoreCollabForm"><label>外部资料标题<input value={referenceTitle} onChange={(event) => setReferenceTitle(event.target.value)} placeholder="例如：施工单位开挖顺序调整联系单" /></label><label>摘要<textarea value={referenceSummary} onChange={(event) => setReferenceSummary(event.target.value)} placeholder="只记录与原设计边界相关的信息" /></label><button type="button" disabled={loading || !referenceTitle.trim()} onClick={() => void submitReference()}>登记并发起设计复核</button></div><div className="designCoreCollabList">{(collaboration.reviewRequests ?? []).map((row: any) => <article key={row.id}><b>{row.title}</b><span>{row.status}</span><p>{row.description}</p><small>需要复算：{row.recalculationRequired ? '是' : '待判定'}；需要设计变更：{row.designChangeRequired ? '是' : '待判定'}</small></article>)}</div></div> : null}
  </section>;
}
