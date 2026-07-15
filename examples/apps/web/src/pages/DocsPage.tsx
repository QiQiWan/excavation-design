import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { OnlineDocumentation, StandardsProcessStep } from '../types/domain';

const workflowFallback = [
  ['1. 项目与地勘', '确认单位、设计等级、地下水位和超载，导入钻孔并检查地层参数。'],
  ['2. 地质与基坑几何', '生成三维地质模型，定义闭合基坑轮廓、坑顶/坑底标高和周边约束。'],
  ['3. 围护与方案比选', '生成地连墙、围檩、支撑和立柱，运行 A/B/C 候选方案完整计算。'],
  ['4. 分阶段计算', '计算土水压力、墙体内力与变形、围檩和支撑、构件承载力与稳定性。'],
  ['5. 闭环审查', '按规范矩阵定位 Fail、Warning 和人工复核项，并完成监测、审签与修订。'],
  ['6. 成果交付', '按发行门禁导出 CAD/PDF、IFC、DOCX、钢筋深化 ZIP 和项目归档数据。'],
];

function statusLabel(value: string) {
  return ({ pass: '已通过', fail: '不合规', warning: '预警', manual_review: '需复核', not_run: '未计算', not_covered: '未覆盖' } as Record<string, string>)[value] ?? value;
}

function StandardsTable({ steps }: { steps: StandardsProcessStep[] }) {
  return <section className="standardsProcessStack">{steps.map((step) => <article className={`summaryPanel standardsProcessStep ${step.highlight === 'critical' ? 'critical' : ''}`} key={step.workflowStep}>
    <header><span>STEP {step.index}</span><div><h3>{step.title}</h3><p>{step.implementationLevel}</p></div><em className={`matrixStatus ${step.status}`}>{statusLabel(step.status)}</em></header>
    <div className="calculationLinkTimeline">{(step.calculationLinks ?? []).map((link) => <div className={`calculationLinkRow ${link.status}`} key={`${step.workflowStep}-${link.sequence}`}>
      <div className="calculationSequence">{step.index}.{link.sequence}</div>
      <div className="calculationDefinition"><h4>{link.calculation}</h4><p>{link.method}</p><small><b>输出：</b>{link.output}</small></div>
      <div className="calculationStandards"><strong>本计算直接适用</strong><div>{link.standardRefs.length ? link.standardRefs.map((std) => std.sourceUrl ? <a href={std.sourceUrl} target="_blank" rel="noreferrer" className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></a> : <span className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></span>) : <span className="qualityEvidenceBadge">软件数值质量门禁</span>}</div><p><b>条文关注：</b>{link.clauseFocus}</p></div>
      <div className="calculationEvidence"><span className={`matrixStatus ${link.status}`}>{statusLabel(link.status)}</span><details className="ruleTraceDetails"><summary>{link.ruleCount} 条规则证据</summary>{link.rules.map((rule) => <span className="ruleTrace" key={String(rule.ruleId)}><b>{String(rule.ruleId ?? 'RULE')}</b><em>{String(rule.clauseReference ?? '条文适用条件需项目复核')}</em></span>)}</details></div>
    </div>)}</div>
    <footer><b>本步骤最终输出：</b>{step.outputs.join('；')}</footer>
  </article>)}</section>;
}

export default function DocsPage() {
  const [data, setData] = useState<OnlineDocumentation>();
  const [error, setError] = useState<string>();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<'workflow' | 'principles' | 'standards' | 'deliverables'>('workflow');

  useEffect(() => {
    api.getDocumentation().then(setData).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const filteredSteps = useMemo(() => {
    const steps = data?.standardsMatrix.steps ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return steps;
    return steps.filter((step) => `${step.title} ${step.keyCalculations.join(' ')} ${step.standardRefs.map((s) => `${s.code} ${s.name}`).join(' ')} ${step.clauseFocus.join(' ')}`.toLowerCase().includes(q));
  }, [data, query]);

  return (
    <main className="page docsPage">
      <section className="card docsHero">
        <div><span className="sectionKicker">在线操作、计算与规范文档</span><h2>{data?.title ?? 'PitGuard 操作文档'}</h2><p>文档同时覆盖软件操作、计算模型、关键公式链、规范对应关系、结果边界和成果文件使用。每个设计步骤均与相应标准、规则和输出建立显式追溯。</p></div>
        <div className="docsVersion"><strong>V{data?.version ?? '-'}</strong><span>规则矩阵与后端版本同步</span></div>
      </section>

      {error ? <div className="warning">在线文档接口读取失败：{error}。以下保留基础操作说明。</div> : null}

      <nav className="docsTabs" aria-label="在线文档章节">
        {(['workflow', 'principles', 'standards', 'deliverables'] as const).map((key) => {
          const title = data?.chapters.find((item) => item.id === key)?.title ?? ({ workflow: '操作流程', principles: '计算原理', standards: '流程—规范矩阵', deliverables: '成果文件使用' } as const)[key];
          return <button className={active === key ? 'active' : ''} onClick={() => setActive(key)} key={key}>{title}</button>;
        })}
      </nav>

      {active === 'workflow' && <>
        {data?.standardsMatrix.steps?.length ? <section className="engineeringWorkflow">{data.standardsMatrix.steps.map((step, index) => <article className={`summaryPanel workflowDocCard ${step.highlight === 'critical' ? 'critical' : ''}`} key={step.workflowStep}><header><span>STEP {step.index}</span><h3>{step.title}</h3><em>{step.implementationLevel}</em></header><p><strong>关键计算：</strong>{step.keyCalculations.join('；')}</p><div className="standardBadgeRow">{step.standardRefs.map((std) => std.sourceUrl ? <a href={std.sourceUrl} target="_blank" rel="noreferrer" className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></a> : <span className={`standardBadge ${std.level === 'mandatory_all' ? 'mandatory' : 'primary'}`} key={std.id}><b>{std.code}</b><em>{std.levelLabel}</em></span>)}</div><footer><strong>输出：</strong>{step.outputs.join('；')}</footer>{index < data.standardsMatrix.steps.length - 1 ? <span className="workflowArrow" aria-hidden="true">↓</span> : null}</article>)}</section> : <section className="stepGrid docsGrid">{workflowFallback.map(([title, text]) => <div className="summaryPanel" key={title}><h3>{title}</h3><p>{text}</p></div>)}</section>}
        <section className="summaryPanel docsCallout"><h3>贯穿全流程的审查原则</h3><p>任何一步修改地质参数、基坑几何、支撑拓扑、构件截面或配筋后，后续计算、图纸和发行状态均应标记为需要重算。全文强制性通用规范优先，专业规程与设计标准提供具体方法，地方标准、审图意见和专家论证在项目级规则集中补充。</p></section>
      </>}

      {active === 'principles' && <section className="principleGrid">{(data?.calculationPrinciples ?? []).map((item) => <article className="summaryPanel principleCard" key={item.name}><h3>{item.name}</h3><dl><dt>输入</dt><dd>{item.inputs}</dd><dt>模型</dt><dd>{item.method}</dd>{item.equations?.length ? <><dt>公式</dt><dd className="equationList">{item.equations.map((eq) => <code key={eq}>{eq}</code>)}</dd></> : null}{item.assumptions?.length ? <><dt>假定</dt><dd>{item.assumptions.join('；')}</dd></> : null}<dt>输出</dt><dd>{item.outputs}</dd>{item.verification ? <><dt>复核</dt><dd>{item.verification}</dd></> : null}</dl><div className="standardBadgeRow">{item.standards.map((std) => <span className="standardBadge primary" key={std}>{std}</span>)}</div></article>)}</section>}

      {active === 'standards' && <>
        <section className="summaryPanel standardsIntro"><div><h3>规范优先级与适用边界</h3>{(data?.standardsMatrix.precedence ?? []).map((item) => <p key={item}>{item}</p>)}</div><label>检索流程、计算或规范<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：抗隆起、GB 50010、支撑轴力" /></label></section>
        <StandardsTable steps={filteredSteps} />
        <section className="standardCatalog">{(data?.standardsMatrix.catalog ?? []).map((std) => <article className={`summaryPanel standardCard ${std.level === 'mandatory_all' ? 'mandatory' : ''}`} key={std.id}><div><span>{std.levelLabel}</span><h3>{std.code} {std.name}</h3></div><p><strong>系统覆盖：</strong>{std.implementedScope}</p><p><strong>边界：</strong>{std.boundary}</p>{std.sourceUrl ? <a href={std.sourceUrl} target="_blank" rel="noreferrer">查看官方或国家标准平台来源</a> : null}</article>)}</section>
      </>}

      {active === 'deliverables' && <>
        <section className="stepGrid docsGrid">{(data?.fileGuide ?? []).map((item) => <article className="summaryPanel" key={item.file}><h3>{item.file}</h3><p>{item.use}</p></article>)}</section>
        <section className="summaryPanel rebarUsage"><h3>钢筋加工深化包的正确用法</h3><p>下载结果现在为 ZIP。根目录 XLSX 是人工复核和加工交接的主要文件；CSV 用于表格系统、ERP 或加工设备字段映射；JSON 保存逐根钢筋几何和完整机器语义；可打印、可审签的钢筋施工图位于 CAD 图纸包或正式图纸发行包。</p><ol><li>先检查汇总、钢筋编号表、BBS、接头、钢筋笼分段与吊装计划。</li><li>处理净距、保护层、弯曲半径和签审清单中的失败或复核项。</li><li>确认单位、材料、接头工艺和企业构造标准后，再导入加工或物料系统。</li></ol></section>
      </>}

      <section className="summaryPanel"><h3>状态含义</h3><table className="table compactTable"><thead><tr><th>状态</th><th>含义</th><th>处理方式</th></tr></thead><tbody><tr><td>合规</td><td>已实现的规范子集未发现超限。</td><td>保留追溯，继续后续设计。</td></tr><tr><td>预警</td><td>接近限值、输入不完整或存在构造风险。</td><td>复核参数和控制工况。</td></tr><tr><td>不合规</td><td>计算值超限或存在硬性阻断。</td><td>调整方案并重新计算。</td></tr><tr><td>需复核</td><td>超出自动规则覆盖范围。</td><td>由相应专业工程师确认并签审。</td></tr></tbody></table></section>
      <div className="toolbar"><a className="buttonLink" href="/">返回项目列表</a></div>
    </main>
  );
}
