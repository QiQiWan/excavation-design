import { useEffect, useState } from 'react';

type Row = Record<string, any>;
const base = '/api/engineering/one-strut/assurance';
const titles: Record<string, string> = {
  P1: 'P1 地下水与渗透稳定', P2: 'P2 圆桩强度、裂缝与构造',
  P3: 'P3 原项目五阶段对照', P4: 'P4 计算书、图纸与校审',
};
function safeObject(v: unknown): Row { return v && typeof v === 'object' && !Array.isArray(v) ? v as Row : {}; }
async function fetchJson<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(base + url, { method: body === undefined ? 'GET' : 'POST',
    credentials: 'include', headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : `操作失败（${res.status}）`);
  }
  return res.json() as Promise<T>;
}
function download(data: Blob, name: string) {
  const u = URL.createObjectURL(data); const a = document.createElement('a');
  a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(u), 1500);
}
export default function AssuranceGatePanel({ result, preliminaryReview }: {result: Row; preliminaryReview: Row | null}) {
  const [hydro, setHydro] = useState('');
  const [luoyu, setLuoyu] = useState('');
  const [plan, setPlan] = useState<Row | null>(null);
  const [methodRevision, setMethodRevision] = useState('');
  const [phase, setPhase] = useState('P1');
  const [decision, setDecision] = useState('returned_for_revision');
  const [notes, setNotes] = useState('');
  const [sourceRefs, setSourceRefs] = useState('');
  const [independentRef, setIndependentRef] = useState('');
  const [negativeRef, setNegativeRef] = useState('');
  const [phaseRecords, setPhaseRecords] = useState<Record<string, Row>>({});
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    setPlan(null); setPhaseRecords({}); setError(''); setMessage(''); setHydro(''); setLuoyu('');
  }, [result.resultHash]);
  function payload(): Row {
    const bundle = (text: string, title: string) => {
      if (!text.trim()) return undefined;
      try { return JSON.parse(text); } catch { throw new Error(`${title} JSON 格式有误`); }
    };
    return {
      result_hash: result.resultHash,
      circular: {project_id: result.projectId, section_id: result.sectionId,
        result_hash: result.resultHash,
        evidence: {project_id: result.projectId, section_id: result.sectionId,
          reference_version: '待项目核定', records: []}},
      hydraulic: bundle(hydro, '水文证据'),
      luoyu: bundle(luoyu, '原项目证据'),
    };
  }
  async function run(action: 'plan' | 'review' | 'dossier' | 'template') {
    setError(''); setMessage(''); setWorking(true);
    try {
      if (action === 'plan') {
        const data = await fetchJson<Row>('/acceptance/plan', payload());
        if (data.resultHash !== result.resultHash) throw new Error('验收计划与当前结果身份不一致');
        setPlan(data); setMessage('已核对当前冻结结果；缺项和工程限制见下表。');
      } else if (action === 'review') {
        if (!methodRevision.trim() || notes.trim().length < 12) throw new Error('请填写方法版别和不少于12字的复核意见');
        const data = await fetchJson<Row>('/acceptance/review-phase', {
          result_hash: result.resultHash, phase, decision, method_revision: methodRevision, notes,
          source_refs: sourceRefs.split(/[,\n]/).map(x=>x.trim()).filter(Boolean),
          independent_verification_ref: independentRef.trim() || null,
          negative_case_ref: negativeRef.trim() || null,
        });
        if (!data.phaseReviewHash) throw new Error('未收到阶段复核身份');
        setPhaseRecords(old => ({ ...old, [phase]: data }));
        setMessage(`${phase} 阶段复核已保存，需重新运行验收计划以更新状态。`);
      } else if (action === 'template') {
        const data = await fetchJson<Row>('/luoyu-3-3/template');
        download(new Blob([JSON.stringify(data, null, 2)], {type:'application/json;charset=utf-8'}),
          'luoyu-3-3-conditions-template.json');
      } else {
        if (!preliminaryReview?.reviewHash) throw new Error('请先完成本剖面的受限工程校审');
        const res = await fetch(base + '/acceptance/dossier', {method:'POST',credentials:'include',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({unified:payload(),review_hash:preliminaryReview.reviewHash,
            phase_review_hashes: Object.values(phaseRecords).map(r => r.phaseReviewHash)})});
        if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(String(d.detail || `成果生成失败 (${res.status})`)); }
        if (res.headers.get('X-PitGuard-Formal-Issue') !== 'false') throw new Error('成果的发行状态缺失，拒绝下载');
        download(await res.blob(),`engineering_P1_P4_${String(result.resultHash).slice(0,12)}_working.zip`);
        setMessage('已生成含校审意见和未完成事项的工作版成果，不得用于施工。');
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setWorking(false); }
  }
  return <details className="ossAssurance" aria-label="P1到P4工程验收">
    <summary>P1—P4 规范证据与工程校审</summary>
    <p>统一使用当前计算结果：{String(result.resultHash).slice(0, 24)}…。缺少原始参数或方法证明时保持待复核；本模块只能导出受限工作版。</p>
    <div className="ossActions">
      <button type="button" disabled={working} onClick={() => void run('plan')}>核对 P1—P4 缺项</button>
      {String(result.sectionId) === '3-3' && <button type="button" className="secondary" disabled={working} onClick={() => void run('template')}>下载 3-3 补件模板</button>}
    </div>
    <details><summary>补充水文和原工程证据（JSON）</summary>
      <label className="ossAssuranceField">水文参数和独立计算依据<textarea value={hydro} onChange={e=>{setHydro(e.target.value);setPlan(null);}} placeholder="粘贴 /assurance/schemas 对应的 hydraulic JSON；不填写表示待提交" /></label>
      <label className="ossAssuranceField">3-3 原项目条件与全阶段数值<textarea value={luoyu} onChange={e=>{setLuoyu(e.target.value);setPlan(null);}} placeholder="粘贴 LuoyuAuditInput JSON；缺原始曲线时留空" /></label>
    </details>
    {plan && <div className="ossTableScroll"><table aria-label="P1到P4工程验收清单"><thead><tr><th>阶段</th><th>当前状态</th><th>需补证据</th><th>校审记录</th></tr></thead>
      <tbody>{(Array.isArray(plan.phases) ? plan.phases : []).map((item: Row) => <tr key={item.phase}>
        <th scope="row">{titles[item.phase] || item.phase}</th><td>{item.assessmentStatus}</td>
        <td>{Array.isArray(item.outstanding) && item.outstanding.length ? <details><summary>{item.outstanding.length} 项</summary><ul>{item.outstanding.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul></details> : '本模块未列缺项'}</td>
        <td>{phaseRecords[item.phase]?.phaseReviewHash ? '已有受限校审记录' : '未记录'}</td>
      </tr>)}</tbody></table></div>}
    <h4>单项工程师复核</h4><p>只有校审权限账户可保存。确认问题仅表示已知晓风险，不代表规范验算或正式施工图已通过。</p>
    <div className="ossGrid">
      <label className="ossAssuranceField">审查阶段<select value={phase} onChange={e=>setPhase(e.target.value)}>{Object.entries(titles).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label className="ossAssuranceField">使用的方法与标准版别<input value={methodRevision} onChange={e=>setMethodRevision(e.target.value)} placeholder="规范编号、年份及方法版本" /></label>
      <label className="ossAssuranceField">处理结论<select value={decision} onChange={e=>setDecision(e.target.value)}><option value="returned_for_revision">退回补充</option><option value="acknowledged_for_working_copy">确认工作版限制</option></select></label>
    </div>
    <label className="ossAssuranceField">原始输入依据（每行一个文件或证据编号）<textarea value={sourceRefs} onChange={e=>setSourceRefs(e.target.value)} placeholder="勘察报告页码、附件编号、测值记录；登记不等于核验原始文件" /></label>
    <div className="ossGrid">
      <label className="ossAssuranceField">手算或独立求解记录<input value={independentRef} onChange={e=>setIndependentRef(e.target.value)} placeholder="可复核记录编号或文件 SHA256" /></label>
      <label className="ossAssuranceField">超限负例记录<input value={negativeRef} onChange={e=>setNegativeRef(e.target.value)} placeholder="失败案例及验证记录编号" /></label>
    </div>
    <label className="ossAssuranceField">复核意见<textarea value={notes} onChange={e=>setNotes(e.target.value)} placeholder="写明实际核查的输入、方法、不满足项目及下一步处理" /></label>
    <div className="ossActions"><button type="button" disabled={working || !preliminaryReview} onClick={() => void run('review')}>保存阶段复核</button>
      <button type="button" className="secondary" disabled={working || !preliminaryReview?.reviewHash} onClick={() => void run('dossier')}>下载 P1—P4 同源校审成果</button></div>
    {error && <p className="ossError" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
  </details>;
}
