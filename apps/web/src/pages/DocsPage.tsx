import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { OnlineDocumentation, StandardsProcessStep } from '../types/domain';

type DocSection = 'quickstart' | 'workflow' | 'formalization' | 'troubleshooting' | 'principles' | 'standards' | 'accuracy' | 'compliance' | 'deliverables' | 'deployment';

const SECTION_LABELS: Record<DocSection, string> = {
  quickstart: '快速开始', workflow: '设计主流程', formalization: '正式化阻断', troubleshooting: '故障诊断',
  principles: '计算原理', standards: '流程—规范矩阵', accuracy: '准确度与验证', compliance: '合规边界',
  deliverables: '成果文件', deployment: '部署维护',
};

const fallbackQuickStart = [
  { step: 1, title: '确认设计基准', action: '确定工程等级、规范体系、材料和正式分析等级。', doneWhen: '设计基准显示已确认。' },
  { step: 2, title: '导入工程输入', action: '导入钻孔、生成地质模型并绘制闭合基坑轮廓。', doneWhen: '地质域覆盖围护结构。' },
  { step: 3, title: '生成围护方案', action: '生成墙、围檩、支撑和立柱，并采用满足硬约束的方案。', doneWhen: '支撑拓扑和施工阶段有效。' },
  { step: 4, title: '计算、优化并闭合', action: '执行当前方案分阶段计算及限定边界内的自动修复。', doneWhen: '结构闭合且计算合同有效。' },
  { step: 5, title: '修复正式化阻断', action: '刷新软件证据并处理责任人确认事项。', doneWhen: '阻断为0或只剩明确的人工审签。' },
  { step: 6, title: '配筋与交付', action: '生成配筋、节点深化、计算书、CAD、IFC和审查包。', doneWhen: '对应发行门禁允许。' },
];

const fallbackTroubleshooting = [
  { code: 'CALCULATION_NOT_CURRENT', symptom: '配筋或出图提示计算失效', cause: '当前设计快照与计算合同不一致', action: '重新完整计算当前方案' },
  { code: 'ANALYSIS_ASSURANCE_BLOCK', symptom: '结构计算通过但正式出图被阻断', cause: '分析等级、参数来源或验证证据不足', action: '执行一键修复正式化阻断，再补齐真实资料' },
  { code: 'WORKER_RESTARTED', symptom: '后台任务中途退出', cause: 'Worker、内存、超时或runtime权限异常', action: '检查systemd与journalctl日志' },
  { code: 'WEBGL_CONTEXT', symptom: '三维模型空白', cause: 'GPU上下文耗尽或浏览器硬件加速异常', action: '重建三维视图并释放其他模型上下文' },
];

function statusLabel(value: string) {
  return ({ pass: '已通过', fail: '不合规', warning: '预警', manual_review: '需复核', not_run: '未计算', not_covered: '未覆盖', not_required: '不适用' } as Record<string, string>)[value] ?? value;
}

function initialSection(): DocSection {
  const hash = window.location.hash.replace('#', '') as DocSection;
  return hash in SECTION_LABELS ? hash : 'quickstart';
}

function StandardsTable({ steps }: { steps: StandardsProcessStep[] }) {
  return <section className="standardsProcessStack">{steps.map((step) => <article className={`summaryPanel standardsProcessStep ${step.highlight === 'critical' ? 'critical' : ''}`} key={step.workflowStep}>
    <header><span>STEP {step.index}</span><div><h3>{step.title}</h3><p>{step.implementationLevel}</p></div><em className={`matrixStatus ${step.status}`}>{statusLabel(step.status)}</em></header>
    <div className="calculationLinkTimeline">{(step.calculationLinks ?? []).map((link) => <div className={`calculationLinkRow ${link.status}`} key={`${step.workflowStep}-${link.sequence}`}>
      <div className="calculationSequence">{step.index}.{link.sequence}</div>
      <div className="calculationDefinition"><h4>{link.calculation}</h4><p>{link.method}</p><small><b>输出：</b>{link.output}</small></div>
      <div className="calculationStandards"><strong>直接适用</strong><div>{link.standardRefs.length ? link.standardRefs.map((std) => <span className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></span>) : <span className="qualityEvidenceBadge">软件数值质量门禁</span>}</div><p><b>条文关注：</b>{link.clauseFocus}</p></div>
      <div className="calculationEvidence"><span className={`matrixStatus ${link.status}`}>{statusLabel(link.status)}</span><details className="ruleTraceDetails"><summary>{link.ruleCount} 条规则证据</summary>{link.rules.slice(0, 40).map((rule) => <span className="ruleTrace" key={String(rule.ruleId)}><b>{String(rule.ruleId ?? 'RULE')}</b><em>{String(rule.clauseReference ?? '适用条件需项目复核')}</em></span>)}</details></div>
    </div>)}</div>
    <footer><b>最终输出：</b>{step.outputs.join('；')}</footer>
  </article>)}</section>;
}

export default function DocsPage() {
  const [data, setData] = useState<OnlineDocumentation>();
  const [error, setError] = useState<string>();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<DocSection>(initialSection);

  useEffect(() => { api.getDocumentation().then(setData).catch((err) => setError(err instanceof Error ? err.message : String(err))); }, []);
  useEffect(() => {
    const onHash = () => setActive(initialSection());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  function select(section: DocSection) {
    setActive(section);
    window.history.replaceState({}, '', `/docs#${section}`);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  const filteredSteps = useMemo(() => {
    const steps = data?.standardsMatrix.steps ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return steps;
    return steps.filter((step) => `${step.title} ${step.keyCalculations.join(' ')} ${step.standardRefs.map((s) => `${s.code} ${s.name}`).join(' ')} ${step.clauseFocus.join(' ')}`.toLowerCase().includes(q));
  }, [data, query]);

  const troubleshooting = data?.troubleshooting ?? fallbackTroubleshooting;
  const filteredTroubleshooting = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return troubleshooting;
    return troubleshooting.filter((item) => `${item.code} ${item.symptom} ${item.cause} ${item.action}`.toLowerCase().includes(q));
  }, [query, troubleshooting]);

  return <main className="page docsPage">
    <section className="card docsHero docsHeroEnhanced">
      <div><span className="sectionKicker">在线操作、计算、阻断处理与部署文档</span><h2>{data?.title ?? 'PitGuard 在线说明文档'}</h2><p>以任务为入口说明“怎么做、为什么被阻断、系统能自动处理什么、哪些必须由工程师确认”。规范矩阵和软件版本由后端实时提供，接口暂不可用时仍保留关键应急说明。</p><div className="docsHeroLinks"><a className="buttonLink" href="/">进入系统</a><button className="secondary" onClick={() => select('formalization')}>查看阻断处理</button><button className="secondary" onClick={() => select('troubleshooting')}>故障检索</button></div></div>
      <div className="docsVersion"><strong>V{data?.version ?? '离线'}</strong><span>文档、规则矩阵与后端版本同步</span></div>
    </section>
    {error ? <div className="warning">文档接口暂不可用：{error}。当前页面已切换到内置应急说明。</div> : null}
    <section className="docsSearchBar"><label>全文检索<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="错误代码、阻断、规范、构件或操作，例如 ANALYSIS_ASSURANCE、支撑轴力、GB 50010" /></label>{query ? <button className="secondary compactButton" onClick={() => setQuery('')}>清除</button> : null}</section>
    <nav className="docsTabs" aria-label="在线文档章节">{(Object.keys(SECTION_LABELS) as DocSection[]).map((key) => <button className={active === key ? 'active' : ''} onClick={() => select(key)} key={key}>{SECTION_LABELS[key]}</button>)}</nav>

    {active === 'quickstart' ? <section className="docsQuickStart">{(data?.quickStart ?? fallbackQuickStart).map((item) => <article className="summaryPanel docsQuickStep" key={item.step}><span>{item.step}</span><div><h3>{item.title}</h3><p>{item.action}</p><small><b>完成判据：</b>{item.doneWhen}</small></div></article>)}</section> : null}

    {active === 'workflow' ? <>{data?.standardsMatrix.steps?.length ? <section className="engineeringWorkflow">{data.standardsMatrix.steps.map((step) => <article className={`summaryPanel workflowDocCard ${step.highlight === 'critical' ? 'critical' : ''}`} key={step.workflowStep}><header><span>STEP {step.index}</span><h3>{step.title}</h3><em>{step.implementationLevel}</em></header><p><strong>关键计算：</strong>{step.keyCalculations.join('；')}</p><div className="standardBadgeRow">{step.standardRefs.map((std) => <span className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></span>)}</div><footer><strong>输出：</strong>{step.outputs.join('；')}</footer></article>)}</section> : null}<section className="summaryPanel docsCallout"><h3>依赖失效原则</h3><p>地质、轮廓、支撑拓扑、施工阶段、构件截面或配筋变化后，下游结果按依赖关系失效。历史结果保留用于审计，不能继续作为当前方案的正式设计证据。</p></section></> : null}

    {active === 'formalization' ? <section className="docsFormalization">
      <article className="summaryPanel docsCallout"><span className="sectionKicker">正式出图闸门</span><h3>结构计算通过后，仍需要证明结果可追溯、参数来源真实、验证证据完整</h3><p>{data?.formalizationGuide?.definition ?? '正式化门禁用于区分结构计算闭合与成果是否具备正式发行条件。'}</p></article>
      <div className="docsBoundaryGrid"><article className="summaryPanel automatic"><h3>系统可以自动处理</h3><ol>{(data?.formalizationGuide?.automaticBoundary ?? ['重建当前计算合同和结果哈希', '刷新分析保证、IFC和验证矩阵', '重新评估正式化门禁']).map((item) => <li key={item}>{item}</li>)}</ol></article><article className="summaryPanel manual"><h3>必须由责任人确认</h3><ol>{(data?.formalizationGuide?.manualBoundary ?? ['真实参数来源', '外部软件验证', '专业签署和专项设计']).map((item) => <li key={item}>{item}</li>)}</ol></article></div>
      <article className="summaryPanel"><h3>推荐处理顺序</h3><div className="docsResolutionFlow"><span>1 读取当前闸门</span><b>→</b><span>2 一键修复软件证据</span><b>→</b><span>3 重新计算或刷新验证</span><b>→</b><span>4 展示剩余人工事项</span><b>→</b><span>5 审签与成果冻结</span></div></article>
      <section className="standardCatalog">{(data?.formalizationGuide?.statusMeaning ?? []).map((item) => <article className="summaryPanel standardCard" key={item.status}><div><span>{item.status}</span><h3>{item.meaning}</h3></div><p><strong>处理：</strong>{item.action}</p></article>)}</section>
      <article className="summaryPanel"><h3>反力迭代显示“0轮”的判读</h3><p>异形转接体系不适用时，状态应显示“不适用 · 0轮”，不会单独造成阻断。只有工程明确需要转接框架且迭代缺失、失败或未收敛时，才阻断正式发行。</p></article>
    </section> : null}

    {active === 'troubleshooting' ? <section className="docsTroubleshooting">{filteredTroubleshooting.map((item) => <article className="summaryPanel troubleshootingCard" key={item.code}><code>{item.code}</code><h3>{item.symptom}</h3><dl><dt>常见原因</dt><dd>{item.cause}</dd><dt>处理方法</dt><dd>{item.action}</dd></dl></article>)}{!filteredTroubleshooting.length ? <div className="coreEmpty">没有匹配的故障条目。</div> : null}</section> : null}

    {active === 'principles' ? <section className="principleGrid">{(data?.calculationPrinciples ?? []).filter((item) => !query || `${item.name} ${item.inputs} ${item.method} ${item.outputs}`.toLowerCase().includes(query.toLowerCase())).map((item) => <article className="summaryPanel principleCard" key={item.name}><h3>{item.name}</h3><dl><dt>输入</dt><dd>{item.inputs}</dd><dt>模型</dt><dd>{item.method}</dd>{item.equations?.length ? <><dt>公式</dt><dd className="equationList">{item.equations.map((eq) => <code key={eq}>{eq}</code>)}</dd></> : null}<dt>输出</dt><dd>{item.outputs}</dd>{item.verification ? <><dt>复核</dt><dd>{item.verification}</dd></> : null}</dl><div className="standardBadgeRow">{item.standards.map((std) => <span className="standardBadge primary" key={std}>{std}</span>)}</div></article>)}</section> : null}

    {active === 'standards' ? <><section className="summaryPanel standardsIntro"><div><h3>规范优先级与适用边界</h3>{(data?.standardsMatrix.precedence ?? []).map((item) => <p key={item}>{item}</p>)}</div></section><StandardsTable steps={filteredSteps} /></> : null}

    {active === 'accuracy' ? <><section className="stepGrid docsGrid">{(data?.analysisLevels ?? []).map((item) => <article className="summaryPanel" key={item.level}><span className="sectionKicker">{item.level}</span><h3>{item.name}</h3><p>{item.use}</p></article>)}</section><section className="summaryPanel"><h3>验证边界</h3><p>内置算例、单元测试和数值平衡用于证明软件实现一致性；正式项目要求的成熟外部软件复核、试验参数和工程师签署必须单独保存。</p></section></> : null}

    {active === 'compliance' ? <><section className="standardCatalog">{(data?.complianceSemantics ?? []).map((item) => <article className={`summaryPanel standardCard ${item.formalUse ? 'mandatory' : ''}`} key={item.type}><div><span>{item.formalUse ? '可正式使用' : '仅筛查/待确认'}</span><h3>{item.label}</h3></div><p>{item.description}</p></article>)}</section><section className="summaryPanel"><h3>法定证据链</h3><p>{data?.statutoryWorkflow?.classification}</p><ol>{(data?.statutoryWorkflow?.evidence ?? []).map((item) => <li key={item}>{item}</li>)}</ol><p><strong>发行门禁：</strong>{data?.statutoryWorkflow?.issueGate}</p></section></> : null}

    {active === 'deliverables' ? <><section className="stepGrid docsGrid">{(data?.fileGuide ?? []).map((item) => <article className="summaryPanel" key={item.file}><h3>{item.file}</h3><p>{item.use}</p></article>)}</section><section className="summaryPanel"><h3>成果一致性要求</h3><p>计算书、CAD、IFC、钢筋表和项目快照必须引用同一DesignSnapshotId、当前计算结果ID及哈希。浏览器LOD只用于查看，完整数量和加工语义以正式成果包为准。</p></section></> : null}

    {active === 'deployment' ? <section className="docsDeployment"><article className="summaryPanel"><h3>生产构建</h3><pre>{(data?.deploymentGuide?.build ?? ['npm ci', 'npm run build']).join('\n')}</pre></article><article className="summaryPanel"><h3>健康检查</h3><pre>{(data?.deploymentGuide?.health ?? ['curl http://127.0.0.1:8002/health/ready']).join('\n')}</pre></article><article className="summaryPanel"><h3>日志定位</h3><pre>{(data?.deploymentGuide?.logs ?? ['journalctl -u pitguard-api -n 200 --no-pager']).join('\n')}</pre></article><article className="summaryPanel docsCallout"><h3>备份与回滚</h3><p>{data?.deploymentGuide?.backup ?? '升级前备份数据库、环境变量和Nginx配置。'}</p></article></section> : null}

    <footer className="docsFooter"><span>文档版本 V{data?.version ?? '离线'} · 规则矩阵仅覆盖软件已实现的计算与校核子集。</span><a className="buttonLink secondary" href="/">返回系统</a></footer>
  </main>;
}
