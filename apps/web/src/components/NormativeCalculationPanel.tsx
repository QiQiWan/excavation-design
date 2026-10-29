import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import './NormativeCalculationPanel.css';

type NormMethod = { id: string; label: string; clause: string; status: string; scope: string; required: string[] };
type Suggestion = { parameter: string; label: string; value?: number; unit: string; source: string; applicability: string };
type CalcResult = { methodResults?: { methodId: string; status: string; missing?: string[]; reason?: string;
  dataStatus?: string; output?: Record<string, unknown> }[]; issues?: { code: string; message: string }[]; previewOnly?: boolean;
  formalIssueReady?: boolean; requestHash?: string };
const definitions: Record<string, [string, string, string[]]> = {
  sigma_total: ['竖向总应力', 'kPa', ['kPa', 'MPa']],
  pore_pressure: ['孔隙水压力', 'kPa', ['kPa', 'MPa']],
  cohesion: ['黏聚力 c / cʹ', 'kPa', ['kPa', 'MPa']],
  phi: ['内摩擦角 φ / φʹ', 'deg', ['deg']],
  vb: ['坑底位移 vb（规范公式可在≤10mm时取10mm）', 'mm', ['mm', 'm']],
  m: ['水平反力比例 m', 'kN/m4', ['kN/m4', 'MPa/m2']],
  depth: ['计算点深度 z', 'm', ['m', 'mm']],
  excavation_depth: ['开挖深度 h', 'm', ['m', 'mm']],
  displacement: ['坑内土体压缩位移', 'mm', ['mm', 'm']],
  pile_width: ['桩径或桩宽', 'mm', ['mm', 'm']],
  pile_spacing: ['桩中心距', 'mm', ['mm', 'm']],
  gamma0: ['结构重要性系数', 'dimensionless', ['dimensionless']],
  design_effect: ['基本组合内力设计值', 'kN', ['kN']],
  design_resistance: ['构件抗力设计值', 'kN', ['kN']],
  service_effect: ['标准组合位移/沉降', 'mm', ['mm', 'm']],
  service_limit: ['已确认控制限值', 'mm', ['mm', 'm']],
};
const enums: Record<string, [string, string][]> = {
  strength_basis: [['effective','有效应力指标cʹ、φʹ'],['total','总应力指标c、φ']],
  stress_mode: [['effective_separated', '有效应力分算'], ['total_combined', '总应力合算']],
  pile_section: [['circular', '圆桩'], ['rectangular', '矩形桩'], ['h_section', '工字形桩']],
  stress_side: [['excavation_inside','坑内嵌固段'],['retained_outside','坑外（本方法不适用）']],
  effect_type: [['axial','轴力 N'],['shear','剪力 V'],['moment','弯矩 M'],['torsion','扭矩 T']],
};
function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export default function NormativeCalculationPanel() {
  const [methods, setMethods] = useState<NormMethod[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [methodId, setMethodId] = useState('JGJ120_3_4_2_RANKINE');
  const [values, setValues] = useState<Record<string, string>>({});
  const [units, setUnits] = useState<Record<string, string>>({});
  const [source, setSource] = useState('');
  const [parameterSources, setParameterSources] = useState<Record<string, string>>({});
  const [actor, setActor] = useState('');
  const [approved, setApproved] = useState(false);
  const [region, setRegion] = useState('浙江省');
  const [designDate, setDesignDate] = useState(new Date().toISOString().slice(0,10));
  const [result, setResult] = useState<CalcResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestVersion = useRef(0);
  useEffect(() => {
    let alive = true;
    void Promise.all([api.getNormativeMethods(), api.getNormativeCandidates()]).then(([m, c]) => {
      if (!alive) return;
      setMethods(Array.isArray(m.methods) ? m.methods as NormMethod[] : []);
      setSuggestions(Array.isArray(c.candidates) ? c.candidates as Suggestion[] : []);
    }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { alive = false; requestVersion.current += 1; };
  }, []);
  const method = useMemo(() => methods.find((item) => item.id === methodId), [methodId, methods]);
  const required = method?.required || [];
  const candidates = suggestions.filter((suggestion) => required.includes(suggestion.parameter));
  function updateValue(key: string, next: string) {
    requestVersion.current += 1;
    setValues((prev) => ({ ...prev, [key]: next })); setResult(null); setApproved(false);
  }
  async function run() {
    if (!method || busy) return;
    const inputs: Record<string, unknown> = {};
    for (const key of required) {
      if (enums[key]) { if (values[key]) inputs[key] = values[key]; continue; }
      if (!definitions[key] || !values[key]?.trim()) continue;
      const number = Number(values[key]);
      if (!Number.isFinite(number)) { setError(`${definitions[key][0]}应输入有效数值`); return; }
      const dynamicUnit = ['design_effect', 'design_resistance'].includes(key) && ['moment', 'torsion'].includes(values.effect_type) ? 'kN.m' : definitions[key][1];
      inputs[key] = { value: number, unit: units[key] || dynamicUnit,
        source_type: approved ? 'design_confirmed' : 'unconfirmed_candidate',
        source_ref: parameterSources[key]?.trim() || source.trim() || '尚未提供正式来源（预览）', approval: approved ? 'approved' : 'pending',
        approved_by: approved ? actor.trim() : null, detail: '' };
    }
    const payload = { schema_version: 'pitguard.norm-calc-request/1', project_id: 'CURRENT-REVIEW',
      project_region: region.trim() || '待确认地区', design_date: designDate,
      basis: 'GB55003-2021+JGJ120-2012', task: 'calculate', method_ids: [methodId],
      inputs, scope_note: '工程师交互式单项规范公式计算；未自动采用候选参数。' };
    const current = ++requestVersion.current; setBusy(true); setError(''); setResult(null);
    try {
      const response = await api.executeNormativeMethod(payload) as CalcResult;
      if (requestVersion.current === current) setResult(response);
    } catch (reason) { if (requestVersion.current === current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (requestVersion.current === current) setBusy(false); }
  }
  return <section className="normCalc" aria-label="规范计算助手">
    <h4>规范方法计算</h4>
    <p>选择一项计算方法，核对参数与来源。每个结果均列出公式依据；完整支护结构验算仍需其他计算模块。</p>
    <label>计算方法
      <select aria-label="规范计算方法" value={methodId} onChange={(event) => { setMethodId(event.target.value); setResult(null); setApproved(false); requestVersion.current += 1; }}>
        {methods.map((item) => <option key={item.id} value={item.id} disabled={item.status === 'integration_pending'}>
          {item.label} · {item.clause}{item.status === 'integration_pending' ? '（开发中）' : ''}
        </option>)}
      </select>
    </label>
    {method ? <p className="normCalcScope">适用范围：{method.scope}</p> : null}
    <div className="normCalcFields">
      <label>工程所在地（地方标准适用）<input aria-label="工程所在地" value={region} onChange={e => { setRegion(e.target.value); setResult(null); requestVersion.current += 1; }} /></label>
      <label>计算基准日期<input aria-label="计算基准日期" type="date" value={designDate} onChange={e => { setDesignDate(e.target.value); setResult(null); requestVersion.current += 1; }} /></label>
      {required.filter((name) => name !== 'stages').map((name) => {
        if (enums[name]) return <label key={name}>{name === 'strength_basis' ? '强度参数试验口径' : name === 'stress_mode' ? '土水分析口径' : name === 'stress_side' ? '土反力作用侧' : name === 'pile_section' ? '桩截面形式' : '承载力效应类型'}
          <select value={values[name] || ''} onChange={(e) => { updateValue(name, e.target.value); if (name === 'effect_type') setUnits((p) => ({...p, design_effect:'', design_resistance:''})); }} aria-label={name}>
            <option value="">请选择</option>{enums[name].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></label>;
        if (!definitions[name]) return null;
        const [title, defaultUnit, allowedUnits] = definitions[name];
        const unit = ['design_effect','design_resistance'].includes(name) && ['moment','torsion'].includes(values.effect_type) ? 'kN.m' : defaultUnit;
        const options = ['design_effect','design_resistance'].includes(name) && ['moment','torsion'].includes(values.effect_type) ? ['kN.m','N.mm'] : allowedUnits;
        return <label key={name}>{title}
          <div className="normCalcNumber"><input type="number" step="any" aria-label={title} placeholder="待输入"
            value={values[name] || ''} onChange={(e) => updateValue(name, e.target.value)} />
            <select aria-label={`${title}单位`} value={options.includes(units[name]) ? units[name] : unit} onChange={(e) => { setUnits((p) => ({ ...p, [name]: e.target.value })); setResult(null); setApproved(false); }}>
              {options.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </div></label>;
      })}
    </div>
    {required.includes('stages') ? <p>阶段覆盖检查请通过后端结构化输入调用。当前表单不会将未确认的阶段自动补齐。</p> : null}
    <div className="normCalcApproval">
      <label>参数来源（文件、试验报告章节或项目确认记录）<input value={source} onChange={(e) => { setSource(e.target.value); setResult(null); setApproved(false); }} placeholder="未确认时可以先计算预览" /></label>
      <label>确认人/审查标识<input value={actor} onChange={(e) => { setActor(e.target.value); setApproved(false); setResult(null); }} placeholder="工程师确认后填写" /></label>
      <details className="normCalcPerField"><summary>不同参数来自不同资料？逐项指定出处</summary>
        {required.filter(key => definitions[key]).map(key => <label key={key}>{definitions[key][0]}
          <input aria-label={`${definitions[key][0]}来源`} value={parameterSources[key] || ''}
            onChange={e => { setParameterSources(prev => ({...prev,[key]:e.target.value})); setResult(null); setApproved(false); requestVersion.current+=1; }}
            placeholder="留空则使用上面的共同来源" /></label>)}
      </details>
      <label className="normCalcCheckbox"><input type="checkbox" checked={approved}
        disabled={!actor.trim() || !required.filter(key => definitions[key]).every(key => !!(parameterSources[key]?.trim() || source.trim()))}
        onChange={(e) => { setApproved(e.target.checked); setResult(null); }} />我已逐项核对以上参数及其来源</label>
    </div>
    <button type="button" onClick={() => void run()} disabled={!method || busy || method.status === 'integration_pending'}>{busy ? '计算中…' : '运行所选规范方法'}</button>
    {candidates.length ? <details><summary>查看参考候选（{candidates.length}）</summary>
      {candidates.map((item, i) => <p key={i}><strong>{item.label}</strong>：{item.value ?? '经验公式'} {item.unit}。{item.applicability}<small> 来源：{item.source}；需人工确认，不自动填入。</small></p>)}
    </details> : null}
    {error ? <p role="alert" className="normCalcError">{error}</p> : null}
    {result ? <div className="normCalcOutcome" aria-live="polite"><strong>{result.previewOnly ? '参数预览 / 未完成工程确认' : '本公式按已确认输入计算完成'}</strong>
      {result.methodResults?.map((item) => <section key={item.methodId}>
        <p>{item.status === 'calculated' ? `公式计算完成 · ${item.dataStatus}` : `未计算 · ${item.status}`}</p>
        {item.output ? <dl>{Object.entries(item.output).filter(([, v]) => typeof v === 'number' || typeof v === 'boolean').map(([k, v]) =>
          <div key={k}><dt>{k}</dt><dd>{typeof v === 'number' ? Number(v.toPrecision(8)).toString() : String(v)}</dd></div>)}</dl> : null}
        {item.reason ? <p>{item.reason}</p> : null}{item.missing?.length ? <p>缺项：{item.missing.join('、')}</p> : null}
      </section>)}
      {result.issues?.map((issue, i) => <p key={i}>{issue.message}</p>)}
      <small>来源请求：{String(asObject(result).requestHash || '').slice(0, 16)}。本结果不能单独用于工程发行。</small>
    </div> : null}
  </section>;
}
