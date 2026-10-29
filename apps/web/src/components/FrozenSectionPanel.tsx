import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { api, type FrozenSectionComparison, type FrozenSectionInput, type FrozenSectionResult, type FrozenSectionSnapshotRecord } from '../api/client';
import './FrozenSectionPanel.css';
import NormativeCalculationPanel from './NormativeCalculationPanel';
import FrozenSectionReferenceReview from './FrozenSectionReferenceReview';
import { EngineeringChecks, MethodReview } from './FrozenSectionReviewDetails';

type JsonObject = Record<string, unknown>;
type BusyAction = 'example' | 'import' | 'freeze' | 'analyze' | 'recover' | 'package' | null;
const INPUT_SCHEMAS = ['pitguard.frozen-section/1', 'pitguard.frozen-section/2'];
const eventLabels: Record<string, string> = { initial: '初始平衡', excavate: '开挖', install: '安装支撑', remove: '拆除支撑', water_or_load: '水位 / 荷载变化', soil_reset: '显式土体历史重置' };
const comparisonLabels = { hand: '手算比较', independent: '独立求解器比较', mesh: '网格比较', original: '原项目比较' } as const;

function object(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}
function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(object) : [];
}
function label(value: unknown, fallback = '未提供'): string {
  return typeof value === 'string' && value ? value : fallback;
}
function number(value: unknown, digits = 2, missing = '未计算'): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : missing;
}
function ids(value: unknown): string {
  return Array.isArray(value) && value.length ? value.map((item) => label(item)).join('、') : '—';
}
function parseInput(text: string): { input: FrozenSectionInput | null; error: string | null } {
  if (!text.trim()) return { input: null, error: null };
  try {
    const parsed = object(JSON.parse(text));
    const candidate = 'input' in parsed && ('snapshotHash' in parsed || 'schemaVersion' in parsed) ? object(parsed.input) : parsed;
    if (!INPUT_SCHEMAS.includes(String(candidate.schema_version))) throw new Error('请使用完整剖面输入 JSON 或其冻结快照；schema_version 应为 pitguard.frozen-section/1 或 pitguard.frozen-section/2。');
    return { input: candidate, error: null };
  } catch (reason) {
    return { input: null, error: reason instanceof SyntaxError ? `JSON 格式错误：${reason.message}` : reason instanceof Error ? reason.message : '无法读取输入 JSON。' };
  }
}
function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function comparisonStatus(status?: string) {
  const labels: Record<string, string> = {
    passed: '数值比较通过', failed: '未达到容差', incomplete: '条件未齐',
    unavailable: '工具不可用', not_run: '未计算', not_calculated: '未计算',
    conditions_mismatch: '条件不一致', not_applicable: '不适用',
  };
  return status ? labels[status] ?? `待复核（${status}）` : '未计算';
}

function assertResultIdentity(value: FrozenSectionResult, frozen: FrozenSectionSnapshotRecord) {
  if (value.snapshotHash !== frozen.snapshotHash || value.analysisHash !== frozen.analysisHash || value.sectionId !== frozen.input.section_id || !value.resultHash || !Array.isArray(value.stages)) {
    throw new Error('返回结果与冻结输入的身份不一致，未采用该结果。');
  }
}

function Comparison({ name, comparison }: { name: string; comparison?: FrozenSectionComparison | null }) {
  return <article aria-label={name} className={`frozenComparison ${comparison?.status === 'passed' ? 'compared' : comparison?.status === 'failed' ? 'failed' : ''}`}>
    <h5>{name}</h5><strong>{comparisonStatus(comparison?.status)}</strong>
    <p>{comparison?.reason || comparison?.scope || '尚无比较证据。'}</p>
    {comparison?.status ? <small>{comparison.status}</small> : null}
    {comparison?.missingConditions?.length ? <details><summary>待补条件（{comparison.missingConditions.length}）</summary><ul>{comparison.missingConditions.map((item, index) => <li key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul></details> : null}
  </article>;
}

export default function FrozenSectionPanel() {
  const [jsonText, setJsonText] = useState('');
  const [showNormative, setShowNormative] = useState(false);
  const [snapshot, setSnapshot] = useState<FrozenSectionSnapshotRecord | null>(null);
  const [result, setResult] = useState<FrozenSectionResult | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('加载合成验证例，或导入已完整定义的剖面输入。');
  const revision = useRef(0);
  const inputId = useId();
  const parsed = useMemo(() => parseInput(jsonText), [jsonText]);
  const input = parsed.input;
  const pile = object(input?.pile);
  const datum = object(input?.datum);
  const analysis = object(input?.analysis);
  const reinforcement = object(input?.reinforcement);
  const inputStages = rows(input?.stages);
  const inputSupports = rows(input?.supports);
  const governing = result?.envelope?.governing;

  // Requests carry a revision so editing or closing the panel cannot revive old evidence.
  useEffect(() => () => { revision.current += 1; }, []);

  function invalidate() {
    revision.current += 1;
    setSnapshot(null);
    setResult(null);
    setError(null);
    setBusy(null);
    return revision.current;
  }
  function edit(text: string) {
    invalidate();
    setJsonText(text);
    setNotice('输入已修改；请重新冻结后计算。');
  }
  function fail(reason: unknown, token: number) {
    if (revision.current === token) setError(reason instanceof Error ? reason.message : String(reason));
  }
  async function loadExample(v2 = false) {
    const token = invalidate();
    setBusy('example');
    try {
      const value = await (v2 ? api.getSectionExampleV2() : api.getSectionExample());
      if (revision.current !== token) return;
      setJsonText(JSON.stringify(value, null, 2));
      setNotice('已加载合成验证例，请核对输入后冻结。');
    } catch (reason) { fail(reason, token); }
    finally { if (revision.current === token) setBusy(null); }
  }
  async function importFile(file?: File) {
    if (!file) return;
    const token = invalidate();
    setBusy('import');
    try {
      const text = await file.text();
      if (revision.current !== token) return;
      const imported = parseInput(text);
      if (!imported.input) throw new Error(imported.error || '文件中没有完整剖面输入。');
      setJsonText(JSON.stringify(imported.input, null, 2));
      setNotice(`已导入 ${file.name}；请核对并重新冻结，以建立服务端快照。`);
    } catch (reason) { fail(reason, token); }
    finally { if (revision.current === token) setBusy(null); }
  }
  async function freeze() {
    if (!input) return;
    const token = invalidate();
    setBusy('freeze');
    try {
      const value = await api.freezeSection(input);
      if (revision.current !== token) return;
      if (!value.snapshotHash || !value.analysisHash || !INPUT_SCHEMAS.includes(value.schemaVersion) || value.schemaVersion !== input.schema_version || !value.input || value.input.schema_version !== input.schema_version || value.input.section_id !== input.section_id) throw new Error('冻结响应与输入的版本或剖面身份不一致。');
      setSnapshot(value);
      setJsonText(JSON.stringify(value.input, null, 2));
      setNotice('输入已冻结，可以运行阶段分析与比较，或恢复最近已保存结果。');
    } catch (reason) { fail(reason, token); }
    finally { if (revision.current === token) setBusy(null); }
  }
  async function analyze() {
    if (!snapshot) return;
    const token = ++revision.current;
    const frozen = snapshot;
    setBusy('analyze'); setError(null); setResult(null);
    try {
      const value = await api.analyzeSectionSnapshot(frozen.snapshotHash);
      if (revision.current !== token) return;
      assertResultIdentity(value, frozen);
      setResult(value);
      setNotice(value.status === 'complete' ? '阶段计算已完成；比较结果与工程状态见下方。' : '计算未完整完成，请检查阶段状态与失败原因。');
    } catch (reason) { fail(reason, token); }
    finally { if (revision.current === token) setBusy(null); }
  }
  async function recoverSavedResult() {
    if (!snapshot || result) return;
    const token = ++revision.current;
    const frozen = snapshot;
    setBusy('recover'); setError(null);
    try {
      const value = await api.getLatestSectionResult(frozen.snapshotHash);
      if (revision.current !== token) return;
      assertResultIdentity(value, frozen);
      setResult(value);
      setNotice('已恢复该冻结输入最近已保存的结果，请核对其阶段状态、比较证据与结果身份。');
    } catch (reason) { fail(reason, token); }
    finally { if (revision.current === token) setBusy(null); }
  }
  async function downloadPackage() {
    if (!result || !snapshot || result.snapshotHash !== snapshot.snapshotHash || result.analysisHash !== snapshot.analysisHash) return;
    const token = revision.current;
    const hash = result.resultHash;
    setBusy('package'); setError(null);
    try {
      const blob = await api.downloadSectionPackage(hash);
      if (revision.current !== token) return;
      download(blob, `pitguard-section-${hash.slice(0, 12)}.zip`);
    } catch (reason) { fail(reason, token); }
    finally { if (revision.current === token) setBusy(null); }
  }
  function saveSnapshot() {
    if (!snapshot) return;
    try {
      download(new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json;charset=utf-8' }), `pitguard-frozen-section-${snapshot.snapshotHash.slice(0, 12)}.json`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }
  function controlStage(stageId?: string) {
    if (!stageId) return '控制阶段：未计算';
    const stage = result?.stages.find((item) => item.id === stageId);
    return `控制阶段：${stageId}${stage?.name ? ` · ${stage.name}` : ''}`;
  }

  return <section className="frozenSectionPanel" aria-label="冻结剖面验证面板">
    <header className="frozenSectionHeading"><div><h3>冻结剖面验证</h3><p>独立二维排桩剖面输入，经冻结、阶段求解与比较后生成同源成果。</p></div><span className="frozenSectionBadge">工程复核入口</span></header>
    <button type="button" className="secondary" aria-expanded={showNormative} onClick={() => setShowNormative((value) => !value)}>
      {showNormative ? '收起规范计算助手' : '规范计算助手 · 方法与参数'}
    </button>
    {showNormative ? <NormativeCalculationPanel /> : null}
    <p className="frozenSectionScope">合成验证例用于检验计算与成果链。此面板使用独立输入，当前项目与原 A3-3 的工程参数应由完整 JSON 明确提供。</p>
    <div className="frozenSectionActions">
      <button type="button" className="secondary" onClick={() => void loadExample()} disabled={!!busy}>{busy === 'example' ? '正在加载…' : '加载合成验证例'}</button>
      <button type="button" className="secondary" onClick={() => void loadExample(true)} disabled={!!busy}>加载 v2 方法验证例</button>
      <label className="frozenSectionImport" htmlFor={inputId}>导入输入 / 快照 JSON<input id={inputId} aria-label="导入剖面输入 JSON" type="file" accept=".json,application/json" disabled={!!busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void importFile(file); }} /></label>
    </div>
    {(error || parsed.error) ? <div className="frozenSectionError" role="alert">{error || parsed.error}</div> : null}
    <p className="frozenSectionNotice" role="status" aria-live="polite">{busy === 'analyze' ? '正在求解各阶段并运行比较，请稍候…' : busy === 'recover' ? '正在读取该冻结输入最近已保存的结果…' : busy === 'package' ? '正在生成同源成果包，请稍候…' : notice}</p>

    {input ? <div className="frozenSectionInputReview">
      <div className="frozenSectionInputTitle"><h4>{label(input.name)}</h4><span className="frozenSectionBadge">{input.purpose === 'verification_fixture' ? '合成验证例' : input.purpose === 'project_reconstruction' ? '项目资料重建' : '项目剖面复核'}</span></div>
      <dl className="frozenSectionParameters">
        <div><dt>桩径 / 桩距</dt><dd>{number(pile.diameter_m, 3, '未提供')} / {number(pile.spacing_m, 3, '未提供')} m</dd></div>
        <div><dt>桩长 / 桩端约束</dt><dd>{number(pile.length_m, 3, '未提供')} m / {({ free: '自由', fixed: '固定', pinned: '铰支' } as Record<string, string>)[label(pile.toe_boundary)] ?? label(pile.toe_boundary)}</dd></div>
        <div><dt>混凝土 E / I 折减</dt><dd>{number(pile.elastic_modulus_kpa, 0, '未提供')} kPa / {number(pile.inertia_factor, 3, '未提供')}</dd></div>
        <div><dt>土弹簧有效宽度 / 网格上限</dt><dd>{number(input.soil_effective_width_m, 3, '未提供')} / {number(analysis.max_element_size_m, 3, '未提供')} m</dd></div>
        <div><dt>土层 / 定义支撑</dt><dd>{Array.isArray(input.soil_layers) ? input.soil_layers.length : '未提供'} / {Array.isArray(input.supports) ? inputSupports.length : '未提供'}</dd></div>
        <div><dt>设计内力效应系数</dt><dd>{number(analysis.design_effect_factor, 3, '未提供')}</dd></div>
      </dl>
      <p className="frozenSectionFine">深度自模型桩顶向下为正；基准：{label(datum.label)}，桩顶标高 {number(datum.wall_top_elevation_m, 3, '未提供')} m。土抗力：{analysis.soil_method === 'two_sided_elastoplastic_winkler_v1' ? '双侧弹塑性 Winkler' : label(analysis.soil_method)}；压力界限：{analysis.pressure_method === 'effective_rankine_bounds_k0_v1' ? '有效应力 Rankine 主被动界限，K₀ 初态' : label(analysis.pressure_method)}。</p>
      {inputSupports.length ? <p className="frozenSectionFine">支撑：{inputSupports.map((support) => `${label(support.id)} @ ${number(support.depth_m, 3, '未提供')} m，${support.kind === 'rigid' ? '刚性位移约束' : `K=${number(support.stiffness_kn_per_m, 1, '未提供')} kN/m`}`).join('；')}。</p> : null}
      <details><summary>土层与配筋输入</summary><div className="frozenSectionTableScroll"><table aria-label="土层输入"><thead><tr><th>土层 / 深度 m</th><th>天然 / 饱和重度 kN/m³</th><th>c kPa / φ °（口径见方法）</th><th>K₀</th><th>坑内 / 坑外 m 值 kN/m⁴</th></tr></thead><tbody>{rows(input.soil_layers).map((layer, index) => <tr key={label(layer.id, String(index))}><th scope="row">{label(layer.name)}<br />{number(layer.top_m, 2, '未提供')}–{number(layer.bottom_m, 2, '未提供')}</th><td>{number(layer.gamma_unsat_kn_m3, 2, '未提供')} / {number(layer.gamma_sat_kn_m3, 2, '未提供')}</td><td>{number(layer.cohesion_kpa, 2, '未提供')} / {number(layer.phi_deg, 2, '未提供')}</td><td>{number(layer.k0, 3, '未提供')}</td><td>{number(layer.m_inside_kn_m4, 0, '未提供')} / {number(layer.m_outside_kn_m4, 0, '未提供')}</td></tr>)}</tbody></table></div><p className="frozenSectionFine">既定纵筋：{number(reinforcement.bar_count, 0, '未提供')} 根 × φ{number(reinforcement.bar_diameter_mm, 0, '未提供')} mm；钢筋中心至边缘 {number(reinforcement.bar_axis_cover_mm, 1, '未提供')} mm；截面轴力 {number(reinforcement.axial_force_kn, 2, '未提供')} kN。其余材料曲线、箍筋与采用依据见完整输入。</p></details>
      <MethodReview input={input} />
      <div className="frozenSectionTableScroll"><table aria-label="输入阶段"><thead><tr><th>阶段</th><th>事件</th><th>开挖深度 m</th><th>坑内 / 坑外水深 m</th><th>超载 kPa</th><th>安装 / 拆除支撑</th></tr></thead><tbody>{inputStages.map((stage, index) => <tr key={label(stage.id, String(index))}><th scope="row">{label(stage.id)} · {label(stage.name)}</th><td>{eventLabels[label(stage.event)] ?? label(stage.event)}</td><td>{number(stage.excavation_depth_m, 3, '未提供')}</td><td>{number(stage.water_inside_depth_m, 3, '未提供')} / {number(stage.water_outside_depth_m, 3, '未提供')}</td><td>{number(stage.surcharge_kpa, 2, '未提供')}</td><td>{ids(stage.install_support_ids)} / {ids(stage.remove_support_ids)}</td></tr>)}</tbody></table></div>
    </div> : null}

    <details className="frozenSectionJson"><summary>原始 JSON 编辑</summary><p>完整字段由服务端校验。任何编辑都会清除已有快照与结果身份。</p><textarea aria-label="完整剖面输入 JSON" spellCheck={false} value={jsonText} onChange={(event) => edit(event.target.value)} placeholder="粘贴完整剖面输入 JSON 或冻结快照记录" /></details>
    <div className="frozenSectionActions frozenSectionRunActions">
      <button type="button" onClick={() => void freeze()} disabled={!input || !!busy}>{busy === 'freeze' ? '正在冻结…' : '冻结输入'}</button>
      <button type="button" onClick={() => void analyze()} disabled={!snapshot || !!busy}>{busy === 'analyze' ? '正在计算…' : '运行冻结剖面'}</button>
      {snapshot && !result ? <button type="button" className="secondary" onClick={() => void recoverSavedResult()} disabled={!!busy}>{busy === 'recover' ? '正在恢复…' : '恢复已保存结果'}</button> : null}
      <button type="button" className="secondary" onClick={saveSnapshot} disabled={!snapshot || !!busy}>保存冻结输入</button>
      <button type="button" className="secondary" onClick={() => void downloadPackage()} disabled={!result || !!busy}>{busy === 'package' ? '正在生成成果包…' : '下载同源成果包'}</button>
    </div>

    {result ? <div className="frozenSectionResults">
      <div className="frozenSectionReview"><strong>{result.engineeringStatus === 'engineering_fail' ? '存在未通过检查；' : ''}工程复核待完成（{result.engineeringStatus || '未返回工程状态'}）</strong><p>本结果用于剖面计算复核；正式设计尚未就绪。数值比较结论与工程合规结论分别审查。</p></div>
      <div className="frozenSectionMetrics">
        <article aria-label="最大绝对位移"><span>最大绝对位移</span><strong>{typeof governing?.displacementAbs?.valueMm === 'number' ? `${number(Math.abs(governing.displacementAbs.valueMm))} mm` : '未计算'}</strong><small>{controlStage(governing?.displacementAbs?.stageId)}</small></article>
        <article aria-label="最大绝对弯矩"><span>最大绝对弯矩</span><strong>{typeof governing?.momentAbs?.valueKnM === 'number' ? `${number(Math.abs(governing.momentAbs.valueKnM))} kN·m` : '未计算'}</strong><small>{controlStage(governing?.momentAbs?.stageId)}</small></article>
        <article aria-label="最大绝对剪力"><span>最大绝对剪力</span><strong>{typeof governing?.shearAbs?.valueKn === 'number' ? `${number(Math.abs(governing.shearAbs.valueKn))} kN` : '未计算'}</strong><small>{controlStage(governing?.shearAbs?.stageId)}</small></article>
      </div>
      <p className="frozenSectionFine">位移为特征阶段响应，内力为单桩特征值。支点反力表示单桩分担的水平力；设计内力及配筋见同源成果包。{result.envelope?.complete === false ? '包络未完整生成。' : ''}</p>
      <div className="frozenSectionTableScroll"><table aria-label="阶段计算结果"><thead><tr><th>阶段</th><th>计算状态</th><th>最大 |y| mm</th><th>最大 |M| kN·m</th><th>最大 |V| kN</th><th>水平支点反力 kN</th></tr></thead><tbody>{result.stages.map((stage) => <tr key={stage.id}><th scope="row">{stage.id} · {stage.name || stage.id}</th><td>{stage.status === 'success' ? '已计算' : stage.status === 'failed' ? '计算失败' : '未计算'}{stage.failureReason ? <small className="frozenSectionFailureReason">{stage.failureReason}</small> : null}</td><td>{number(stage.maxAbsDisplacementMm)}</td><td>{number(stage.maxAbsMomentKnM)}</td><td>{number(stage.maxAbsShearKn)}</td><td>{stage.supports?.length ? stage.supports.map((support) => <div key={support.id}>{support.id}：{support.status === 'removed' ? '已拆除' : support.status === 'not_installed' ? '未安装' : number(support.reactionCharacteristicKn)}</div>) : Array.isArray(stage.supports) && stage.status === 'success' ? '无支撑' : '未计算'}</td></tr>)}</tbody></table></div>
      <EngineeringChecks value={result.engineeringChecks} />
      <h4>比较证据</h4><div className="frozenSectionComparisons">{(Object.keys(comparisonLabels) as (keyof typeof comparisonLabels)[]).map((key) => <Comparison key={key} name={comparisonLabels[key]} comparison={result.comparisons?.[key]} />)}</div>
    </div> : null}

    <FrozenSectionReferenceReview input={input} result={result} generation={revision.current} parentBusy={!!busy}
      onBind={(hash) => { if (input?.schema_version === 'pitguard.frozen-section/2') edit(JSON.stringify({ ...input, reference_contract_hash: hash }, null, 2)); }}
      downloadJson={(value, name) => { try { download(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }), name); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } }} />

    {snapshot ? <details className="frozenSectionTrace"><summary>冻结与结果溯源</summary><dl><dt>剖面身份</dt><dd>{label(snapshot.input.section_id)}</dd><dt>快照 snapshotHash</dt><dd><code>{snapshot.snapshotHash}</code></dd><dt>分析 analysisHash</dt><dd><code>{snapshot.analysisHash}</code></dd>{result ? <><dt>结果 resultHash</dt><dd><code>{result.resultHash}</code></dd><dt>离散输入</dt><dd><code>{result.discreteInputHash || '未提供'}</code></dd><dt>算法版本</dt><dd>{result.algorithmVersion || '未提供'}</dd></> : null}</dl><p>冻结输入和成果包分别保存服务端返回的快照记录与结果身份。</p></details> : null}
  </section>;
}
