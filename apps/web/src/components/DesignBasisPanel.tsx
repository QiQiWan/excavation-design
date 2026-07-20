import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type { DesignSettings, Project } from '../types/domain';

type DesignBasis = {
  confirmed?: boolean;
  loadCombinations?: Record<string, any>[];
  parameters?: Record<string, any>[];
  standards?: Record<string, any>[];
  blockers?: string[];
  summary?: Record<string, any>;
  templateCatalog?: Record<string, any>[];
  selectedTemplateId?: string;
  actionGroups?: Record<string, any>[];
  safetyTargets?: Record<string, number>;
  analysisModel?: Record<string, any>;
  enterprise?: {
    libraries?: Record<string, any>[];
    selection?: Record<string, any>;
    standardTemplate?: Record<string, any>;
    standardTemplates?: Record<string, any>[];
    nodeTemplateCount?: number;
    rebarCombinationCount?: number;
    validation?: Record<string, any>;
    boundary?: string;
  };
};

type DesignIntake = {
  confirmed?: boolean;
  goal?: 'quick_scheme' | 'standard_design' | 'formal_issue';
  objective?: 'balanced' | 'safety_first' | 'economy_first';
  principle?: string;
  facts?: Record<string, any>[];
  inputTiers?: {
    requiredNow?: Record<string, any>[];
    systemRecommended?: Record<string, any>[];
    beforeCalculation?: Record<string, any>[];
    beforeFormalIssue?: Record<string, any>[];
  };
};

const num = (value: unknown, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback;

type ImpactKey = 'classification' | 'site' | 'loads' | 'analysis' | 'materials' | 'enterprise';

export default function DesignBasisPanel({ project, basis, intake, onSaved, onContinue }: {
  project: Project;
  basis?: DesignBasis;
  intake?: DesignIntake;
  onSaved: (project: Project) => void | Promise<void>;
  onContinue?: () => void;
}) {
  const source = project.designSettings;
  const [draft, setDraft] = useState<DesignSettings>({ ...source });
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string>();
  const [showDetails, setShowDetails] = useState(false);
  const [professionalOpen, setProfessionalOpen] = useState(false);
  const [goal, setGoal] = useState<'quick_scheme' | 'standard_design' | 'formal_issue'>(source.designIntentGoal ?? 'quick_scheme');
  const [environmentLevel, setEnvironmentLevel] = useState<'一般' | '较高' | '高'>(source.surroundingEnvironmentLevel ?? '一般');
  const [objective, setObjective] = useState<'balanced' | 'safety_first' | 'economy_first'>(source.designObjective ?? 'balanced');
  const [designStage, setDesignStage] = useState<'temporary' | 'permanent_combined'>(source.designStage ?? 'temporary');
  const [activeImpact, setActiveImpact] = useState<ImpactKey>('classification');
  const groups = useMemo(() => {
    const out = new Map<string, Record<string, any>[]>();
    for (const item of basis?.parameters ?? []) {
      const key = String(item.group ?? '其他');
      out.set(key, [...(out.get(key) ?? []), item]);
    }
    return [...out.entries()];
  }, [basis]);
  const set = <K extends keyof DesignSettings>(key: K, value: DesignSettings[K]) => setDraft((prev) => ({ ...prev, [key]: value }));
  const applyTemplate = (templateId: string) => {
    const template = (basis?.templateCatalog ?? []).find((item) => String(item.id) === templateId) ?? {};
    setDraft((prev) => ({
      ...prev,
      designBasisTemplateId: templateId,
      projectGrade: (template.projectGrade ?? prev.projectGrade) as DesignSettings['projectGrade'],
      excavationSafetyLevel: (template.excavationSafetyLevel ?? prev.excavationSafetyLevel) as DesignSettings['excavationSafetyLevel'],
      siteComplexity: (template.siteComplexity ?? prev.siteComplexity) as DesignSettings['siteComplexity'],
      surroundingEnvironmentLevel: (template.surroundingEnvironmentLevel ?? prev.surroundingEnvironmentLevel) as DesignSettings['surroundingEnvironmentLevel'],
      loadCombinationPolicy: (template.loadCombinationPolicy ?? prev.loadCombinationPolicy) as DesignSettings['loadCombinationPolicy'],
      importanceFactor: num(template.importanceFactor, prev.importanceFactor ?? 1),
      stabilityReserveRatio: num(template.stabilityReserveRatio, prev.stabilityReserveRatio ?? .1),
      wallCrackedStiffnessFactor: num(template.wallCrackedStiffnessFactor, prev.wallCrackedStiffnessFactor ?? .72),
      waleCrackedStiffnessFactor: num(template.waleCrackedStiffnessFactor, prev.waleCrackedStiffnessFactor ?? .75),
      jointRotationalStiffnessFactor: num(template.jointRotationalStiffnessFactor, prev.jointRotationalStiffnessFactor ?? .65),
      initialImperfectionRatio: num(template.initialImperfectionRatio, prev.initialImperfectionRatio ?? .001),
    }));
  };
  const applyEnterpriseStandard = (templateId: string) => {
    const templates = basis?.enterprise?.standardTemplates ?? [];
    const current = basis?.enterprise?.standardTemplate;
    const template = templates.find((item: any) => String(item.id) === templateId) ?? (String(current?.id ?? '') === templateId ? current : undefined);
    setDraft((prev) => ({
      ...prev,
      localStandardTemplateId: templateId,
      safetyFactorOverrides: template?.safetyTargets ? { ...prev.safetyFactorOverrides, ...template.safetyTargets } : prev.safetyFactorOverrides,
      loadCombinationPolicy: (template?.loadCombinationPolicy ?? prev.loadCombinationPolicy) as DesignSettings['loadCombinationPolicy'],
    }));
  };
  const impactRows: { key: ImpactKey; title: string; value: string; impact: string; outputs: string[] }[] = [
    { key: 'classification', title: '工程与安全等级', value: `${draft.projectGrade ?? '二级'} / ${draft.excavationSafetyLevel ?? '二级'}`, impact: '控制项目重要性、校审深度以及强度、变形和稳定储备目标。', outputs: ['重要性系数', '位移控制', '安全储备', '校审等级'] },
    { key: 'site', title: '场地与周边环境', value: `${draft.siteComplexity ?? '中等'} / ${draft.surroundingEnvironmentLevel ?? '一般'}`, impact: '控制地质外推、周边附加作用、变形控制和需要生成的不利工况。', outputs: ['地质外推', '附加荷载', '变形限值', '专项工况'] },
    { key: 'loads', title: '荷载与规范组合', value: draft.loadCombinationPolicy === 'conservative' ? '保守组合' : draft.loadCombinationPolicy === 'custom' ? '项目自定义' : '标准组合', impact: '直接形成土压力、水压力、堆载及施工阶段作用的设计组合。', outputs: ['γG', 'γQ', 'ψ', '控制组合'] },
    { key: 'analysis', title: '结构分析模型', value: draft.structuralAnalysisModel === 'compact_spatial' ? '紧凑空间模型' : '工程空间模型', impact: '控制墙和围檩开裂刚度、节点半刚性、刚域以及内力重分配。', outputs: ['墙体刚度', '围檩刚度', '节点半刚性', '位移/内力'] },
    { key: 'materials', title: '材料与设计储备', value: `${draft.defaultConcreteGrade ?? 'C35'} / ${draft.defaultRebarGrade ?? 'HRB400'} / +${Math.round(Number(draft.stabilityReserveRatio ?? .1) * 100)}%`, impact: '控制抗弯抗剪承载力、配筋组合、裂缝控制和项目安全目标。', outputs: ['抗弯', '抗剪', '配筋', '裂缝/稳定'] },
    { key: 'enterprise', title: '企业资源与模板', value: `${draft.enterpriseLibraryId ?? 'pitguard_default'} / ${draft.localStandardTemplateId ?? 'national_core_2026'}`, impact: '控制企业安全目标、节点大样、钢筋组合和施工图表达规则。', outputs: ['地方标准', '节点模板', '钢筋组合', '出图规则'] },
  ];
  const selectedImpact = impactRows.find((item) => item.key === activeImpact) ?? impactRows[0];
  const impactFieldProps = (key: ImpactKey) => ({
    'data-impact-key': key,
    className: activeImpact === key ? 'basisFieldActive' : undefined,
    onFocusCapture: () => setActiveImpact(key),
    onClick: () => setActiveImpact(key),
  });
  const focusImpactFields = (key: ImpactKey) => {
    setActiveImpact(key);
    window.requestAnimationFrame(() => {
      const field = document.querySelector<HTMLElement>(`.designBasisForm [data-impact-key="${key}"]`);
      field?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      field?.querySelector<HTMLElement>('select,input,button')?.focus();
    });
  };

  const updateActionGroup = (id: string, patch: Record<string, unknown>) => {
    const rows = (draft.actionGroupCatalog?.length ? draft.actionGroupCatalog : (basis?.actionGroups ?? [])) as Record<string, unknown>[];
    set('actionGroupCatalog', rows.map((row) => String(row.id) === id ? { ...row, ...patch } : row));
  };

  async function save() {
    setSaving(true); setError(undefined);
    try {
      const settings = {
        ...draft,
        actionGroupCatalog: draft.actionGroupCatalog?.length ? draft.actionGroupCatalog : (basis?.actionGroups ?? []),
        safetyFactorOverrides: Object.keys(draft.safetyFactorOverrides ?? {}).length ? draft.safetyFactorOverrides : (basis?.safetyTargets ?? {}),
        designBasisConfirmed: true,
        designIntentConfirmed: true,
        designIntentSource: 'professional_override',
      };
      const updated = await api.updateProject(project.id, { designSettings: settings } as Partial<Project>);
      setDraft({ ...updated.designSettings });
      await onSaved(updated);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  }

  async function applyGuidedIntake() {
    setApplying(true); setError(undefined);
    try {
      const updated = await api.applyGuidedDesignIntake(project.id, { goal, environmentLevel, objective, designStage });
      setDraft({ ...updated.designSettings });
      await onSaved(updated);
      onContinue?.();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setApplying(false); }
  }

  return <section className="designBasisPanel">
    <header className="designBasisHeader">
      <div><strong>最小设计任务书</strong><span>工程师只确认四项设计意图；规范组合、首轮材料、分析模型和施工阶段由系统推荐，真正影响结论时再补专业资料。</span></div>
      <span className={`designBasisState ${basis?.confirmed ? 'pass' : 'warning'}`}>{basis?.confirmed ? '已确认' : '待确认'}</span>
    </header>
    {error ? <div className="error">{error}</div> : null}
    <div className="guidedDesignBrief">
      <div className="guidedBriefIntro">
        <div><span>当前原则</span><strong>{intake?.principle ?? '先形成方案，再按控制结果补资料。'}</strong></div>
        <em>4 项确认 · 其余由系统带来源地推荐</em>
      </div>
      <section className="intentChoiceSet">
        <header><i>1</i><div><strong>这次希望做到哪一步？</strong><span>决定当前需要补资料的深度，不会删除后续能力。</span></div></header>
        <div className="intentChoiceCards">
          {[
            { value: 'quick_scheme', title: '先看快速方案', note: '轮廓和深度齐全即可生成 A/B/C；暂不要求正式计算资料。' },
            { value: 'standard_design', title: '做到计算校核', note: '方案后补地勘，自动生成施工阶段并进入校核优化。' },
            { value: 'formal_issue', title: '做到正式出图', note: '在前两步基础上，再逐项关闭专项资料、配筋和校审门禁。' },
          ].map((item) => <button type="button" key={item.value} className={goal === item.value ? 'selected' : ''} onClick={() => setGoal(item.value as typeof goal)}><strong>{item.title}</strong><span>{item.note}</span></button>)}
        </div>
      </section>
      <section className="intentChoiceSet">
        <header><i>2</i><div><strong>周边保护要求？</strong><span>只需按工程认知选择，系统据此推定安全等级、荷载策略和储备。</span></div></header>
        <div className="intentChoiceCards compact">
          {[
            { value: '一般', title: '一般', note: '周边无特别敏感保护对象。' },
            { value: '较高', title: '较敏感', note: '邻近一般建构筑物或市政设施。' },
            { value: '高', title: '高度敏感', note: '邻近轨道、重要管线或变形敏感结构。' },
          ].map((item) => <button type="button" key={item.value} className={environmentLevel === item.value ? 'selected' : ''} onClick={() => setEnvironmentLevel(item.value as typeof environmentLevel)}><strong>{item.title}</strong><span>{item.note}</span></button>)}
        </div>
      </section>
      <section className="intentChoiceSet">
        <header><i>3</i><div><strong>本轮设计取向？</strong><span>仅改变方案搜索和自动补强的倾向，所有方案仍执行同一套验算。</span></div></header>
        <div className="intentChoiceCards compact">
          {[
            { value: 'balanced', title: '安全与经济均衡', note: '默认推荐，先控制风险再优化用量。' },
            { value: 'safety_first', title: '安全储备优先', note: '提高储备并优先增强刚度。' },
            { value: 'economy_first', title: '经济性优先', note: '允许墙段分区和截面分级优化。' },
          ].map((item) => <button type="button" key={item.value} className={objective === item.value ? 'selected' : ''} onClick={() => setObjective(item.value as typeof objective)}><strong>{item.title}</strong><span>{item.note}</span></button>)}
        </div>
      </section>
      <section className="intentChoiceSet">
        <header><i>4</i><div><strong>围护结构使用阶段？</strong><span>这项会影响长期效应和构造要求，因此保留为人工确认。</span></div></header>
        <div className="intentChoiceCards compact two">
          <button type="button" className={designStage === 'temporary' ? 'selected' : ''} onClick={() => setDesignStage('temporary')}><strong>临时支护</strong><span>地下室完成后退出结构工作。</span></button>
          <button type="button" className={designStage === 'permanent_combined' ? 'selected' : ''} onClick={() => setDesignStage('permanent_combined')}><strong>兼作永久结构</strong><span>增加耐久、裂缝和长期效应要求。</span></button>
        </div>
      </section>
      <section className="guidedRecommendationSummary">
        <header><strong>系统先替你配置</strong><span>每项均保留来源与修改入口，不把默认值伪装成项目实测值。</span></header>
        <div>{(intake?.inputTiers?.systemRecommended ?? []).map((item) => <article key={String(item.key)}><span>{String(item.title)}</span><strong>{String(item.value)}</strong><em>{String(item.source)}</em></article>)}</div>
      </section>
      <div className="guidedBriefActions">
        <button type="button" onClick={() => void applyGuidedIntake()} disabled={applying}>{applying ? '正在应用并检查…' : basis?.confirmed ? '更新任务书并继续' : '采用推荐值并继续'}</button>
        <button type="button" className="secondary" onClick={() => setProfessionalOpen((value) => !value)}>{professionalOpen ? '收起专业设置' : '需要时展开专业设置'}</button>
      </div>
      <p className="guidedBoundary">采用推荐值只代表允许进入当前设计目标；地勘、水位、承载力与专项施工资料不会被系统虚构，并会在影响对应结论前单独提示。</p>
    </div>
    {professionalOpen ? <>
    <div className="designBasisWorkspace">
    <div className="designBasisForm">
      <label {...impactFieldProps('classification')}>工程等级<select value={draft.projectGrade ?? '二级'} onChange={(e) => set('projectGrade', e.target.value as DesignSettings['projectGrade'])}><option>一级</option><option>二级</option><option>三级</option></select></label>
      <label {...impactFieldProps('classification')}>基坑安全等级<select value={draft.excavationSafetyLevel ?? '二级'} onChange={(e) => set('excavationSafetyLevel', e.target.value as DesignSettings['excavationSafetyLevel'])}><option>一级</option><option>二级</option><option>三级</option></select></label>
      <label {...impactFieldProps('site')}>场地复杂程度<select value={draft.siteComplexity ?? '中等'} onChange={(e) => set('siteComplexity', e.target.value as DesignSettings['siteComplexity'])}><option>简单</option><option>中等</option><option>复杂</option></select></label>
      <label {...impactFieldProps('site')}>周边环境等级<select value={draft.surroundingEnvironmentLevel ?? '一般'} onChange={(e) => set('surroundingEnvironmentLevel', e.target.value as DesignSettings['surroundingEnvironmentLevel'])}><option>一般</option><option>较高</option><option>高</option></select></label>
      <label {...impactFieldProps('classification')}>设计阶段<select value={draft.designStage ?? 'temporary'} onChange={(e) => set('designStage', e.target.value as DesignSettings['designStage'])}><option value="temporary">临时支护阶段</option><option value="permanent_combined">兼作永久结构</option></select></label>
      <label {...impactFieldProps('loads')}>规范体系<select value={draft.standardProfile ?? 'national_core'} onChange={(e) => set('standardProfile', e.target.value as DesignSettings['standardProfile'])}><option value="national_core">国家标准核心体系</option><option value="national_plus_local">国家标准 + 地方标准</option><option value="custom_review">项目专项审查体系</option></select></label>
      <label {...impactFieldProps('loads')}>荷载组合策略<select value={draft.loadCombinationPolicy ?? 'standard'} onChange={(e) => set('loadCombinationPolicy', e.target.value as DesignSettings['loadCombinationPolicy'])}><option value="standard">标准组合</option><option value="conservative">保守组合</option><option value="custom">项目自定义组合</option></select></label>
      <label {...impactFieldProps('enterprise')}>企业工程资源库<select value={draft.enterpriseLibraryId ?? 'pitguard_default'} onChange={(e) => set('enterpriseLibraryId', e.target.value)}>{(basis?.enterprise?.libraries ?? [{ libraryId: 'pitguard_default', name: 'PitGuard 默认企业工程资源库' }]).map((item: any) => <option key={String(item.libraryId)} value={String(item.libraryId)}>{String(item.name ?? item.libraryId)} · {String(item.libraryVersion ?? '')}</option>)}</select></label>
      <label {...impactFieldProps('enterprise')}>地方/企业标准模板<select value={draft.localStandardTemplateId ?? String(basis?.enterprise?.selection?.localStandardTemplateId ?? 'national_core_2026')} onChange={(e) => applyEnterpriseStandard(e.target.value)}>{(basis?.enterprise?.standardTemplates ?? [basis?.enterprise?.standardTemplate].filter(Boolean)).map((item: any) => <option key={String(item.id)} value={String(item.id)}>{String(item.name ?? item.id)}</option>)}</select></label>
      <label {...impactFieldProps('enterprise')}>设计基准模板<select value={draft.designBasisTemplateId ?? basis?.selectedTemplateId ?? 'standard_level_2'} onChange={(e) => applyTemplate(e.target.value)}>{(basis?.templateCatalog ?? []).map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.label ?? item.name ?? item.id)}</option>)}</select></label>
      <label {...impactFieldProps('analysis')}>结构分析模型<select value={draft.structuralAnalysisModel ?? 'engineering_spatial'} onChange={(e) => set('structuralAnalysisModel', e.target.value as DesignSettings['structuralAnalysisModel'])}><option value="engineering_spatial">工程空间模型（半刚性节点）</option><option value="compact_spatial">紧凑空间模型</option></select></label>
      <label {...impactFieldProps('analysis')}>墙体开裂刚度系数<input type="number" step="0.01" min="0.2" max="1" value={draft.wallCrackedStiffnessFactor ?? .72} onChange={(e) => set('wallCrackedStiffnessFactor', num(e.target.value, .72))} /></label>
      <label {...impactFieldProps('analysis')}>围檩开裂刚度系数<input type="number" step="0.01" min="0.2" max="1" value={draft.waleCrackedStiffnessFactor ?? .75} onChange={(e) => set('waleCrackedStiffnessFactor', num(e.target.value, .75))} /></label>
      <label {...impactFieldProps('analysis')}>节点转动刚度系数<input type="number" step="0.01" min="0.05" max="1" value={draft.jointRotationalStiffnessFactor ?? .65} onChange={(e) => set('jointRotationalStiffnessFactor', num(e.target.value, .65))} /></label>
      <label {...impactFieldProps('site')}>地基承载力特征值 (kPa)<input type="number" min="1" value={draft.bearingCapacityKpa ?? ''} onChange={(e) => set('bearingCapacityKpa', e.target.value ? Number(e.target.value) : undefined)} /></label>
      <label {...impactFieldProps('loads')}>永久作用分项系数 γG<input type="number" step="0.01" value={draft.loadGammaG ?? 1.35} onChange={(e) => set('loadGammaG', num(e.target.value, 1.35))} /></label>
      <label {...impactFieldProps('loads')}>可变作用分项系数 γQ<input type="number" step="0.01" value={draft.loadGammaQ ?? 1.4} onChange={(e) => set('loadGammaQ', num(e.target.value, 1.4))} /></label>
      <label {...impactFieldProps('loads')}>组合值系数 ψ<input type="number" step="0.05" min="0" max="1" value={draft.loadPsi ?? 1} onChange={(e) => set('loadPsi', num(e.target.value, 1))} /></label>
      <label {...impactFieldProps('classification')}>重要性系数<input type="number" step="0.05" min="0.5" value={draft.importanceFactor ?? 1} onChange={(e) => set('importanceFactor', num(e.target.value, 1))} /></label>
      <label {...impactFieldProps('materials')}>安全系数附加储备<input type="number" step="0.01" min="0" max="1" value={draft.stabilityReserveRatio ?? .1} onChange={(e) => set('stabilityReserveRatio', num(e.target.value, .1))} /></label>
      <label {...impactFieldProps('materials')}>混凝土等级<input value={draft.defaultConcreteGrade ?? 'C35'} onChange={(e) => set('defaultConcreteGrade', e.target.value)} /></label>
      <label {...impactFieldProps('materials')}>钢筋等级<input value={draft.defaultRebarGrade ?? 'HRB400'} onChange={(e) => set('defaultRebarGrade', e.target.value)} /></label>
      <label {...impactFieldProps('materials')}>保护层 (mm)<input type="number" min="20" value={draft.defaultCoverMm ?? 50} onChange={(e) => set('defaultCoverMm', num(e.target.value, 50))} /></label>
    </div>
    <aside className="designBasisImpact" aria-live="polite">
      <header><strong>参数影响</strong><span>选中左侧参数后同步更新</span></header>
      <nav>{impactRows.map((item) => <button type="button" key={item.key} className={activeImpact === item.key ? 'active' : ''} onClick={() => focusImpactFields(item.key)}><strong>{item.title}</strong><span>{item.value}</span></button>)}</nav>
      <article><strong>{selectedImpact.title}</strong><b>{selectedImpact.value}</b><p>{selectedImpact.impact}</p><div>{selectedImpact.outputs.map((item) => <span key={item}>{item}</span>)}</div></article>
    </aside>
    </div>
    <div className="designBasisActions"><button type="button" onClick={() => void save()} disabled={saving}>{saving ? '正在保存…' : '确认并应用设计基准'}</button><button type="button" className="secondary" onClick={() => setShowDetails((v) => !v)}>{showDetails ? '收起规范取值' : '查看荷载组合与规范取值'}</button></div>
    {(basis?.blockers ?? []).length ? <div className="designBasisBlockers">{basis?.blockers?.map((item) => <span key={item}>{item}</span>)}</div> : null}
    {showDetails ? <div className="designBasisDetails">
      <section><h4>荷载组合</h4><div className="basisCombinationGrid">{(basis?.loadCombinations ?? []).map((item) => <article key={String(item.id)}><strong>{String(item.name)}</strong><code>{String(item.expression)}</code><span>γG {String(item.gammaG ?? '-')} · γQ {String(item.gammaQ ?? '-')} · ψ {String(item.psi ?? '-')}</span></article>)}</div></section>
      <section><h4>作用分组与组合责任</h4><div className="basisCombinationGrid">{((draft.actionGroupCatalog?.length ? draft.actionGroupCatalog : basis?.actionGroups) ?? []).map((item: any) => <article key={String(item.id)}><label className="basisActionToggle"><input type="checkbox" checked={item.enabled !== false} onChange={(event) => updateActionGroup(String(item.id), { enabled: event.target.checked })} /><strong>{String(item.label ?? item.name ?? item.id)}</strong></label><span>{String(item.category ?? '')} · {item.enabled === false ? '未启用' : '启用'}</span><small>{String(item.verification ?? item.note ?? item.standardBasis ?? '')}</small></article>)}</div></section>
      <section><h4>企业资源库</h4><div className="basisStandards"><span><b>{String(basis?.enterprise?.selection?.enterpriseLibraryId ?? draft.enterpriseLibraryId ?? 'pitguard_default')}</b>版本 {String((basis?.enterprise?.libraries ?? [])[0]?.libraryVersion ?? '-')} · 节点模板 {String(basis?.enterprise?.nodeTemplateCount ?? 0)} · 钢筋组合 {String(basis?.enterprise?.rebarCombinationCount ?? 0)}</span><span><b>适用边界</b>{String(basis?.enterprise?.boundary ?? '正式项目由企业总工办确认资源库。')}</span></div></section>
      <section><h4>工程分析模型</h4><div className="basisStandards"><span><b>{String(basis?.analysisModel?.model ?? draft.structuralAnalysisModel ?? 'engineering_spatial')}</b>墙刚度 {String(basis?.analysisModel?.wallCrackedStiffnessFactor ?? draft.wallCrackedStiffnessFactor ?? .72)} · 围檩刚度 {String(basis?.analysisModel?.waleCrackedStiffnessFactor ?? draft.waleCrackedStiffnessFactor ?? .75)} · 节点转动 {String(basis?.analysisModel?.jointRotationalStiffnessFactor ?? draft.jointRotationalStiffnessFactor ?? .65)}</span></div></section>
      <section><h4>安全系数目标</h4><div className="basisStandards">{Object.entries(basis?.safetyTargets ?? {}).map(([key, value]) => <span key={key}><b>{key}</b>{Number(value).toFixed(2)}</span>)}</div></section>
      <section><h4>设计取值</h4>{groups.map(([group, items]) => <div className="basisParameterGroup" key={group}><strong>{group}</strong><table><tbody>{items.map((item, index) => <tr key={`${group}-${index}`}><td>{String(item.name)}</td><td>{item.value == null ? '待录入' : `${String(item.value)}${item.unit ? ` ${item.unit}` : ''}`}</td><td>{String(item.source ?? '-')}</td></tr>)}</tbody></table></div>)}</section>
      <section><h4>规范职责</h4><div className="basisStandards">{(basis?.standards ?? []).map((item) => <span key={String(item.code)}><b>{String(item.code)}</b>{String(item.role)}</span>)}</div></section>
    </div> : null}
    </> : null}
  </section>;
}
