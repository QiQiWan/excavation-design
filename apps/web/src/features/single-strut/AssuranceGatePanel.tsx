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
  const [fieldObservations, setFieldObservations] = useState('');
  const [pumpingTest, setPumpingTest] = useState('');
  const [crackCrosscheck, setCrackCrosscheck] = useState('');
  const [eulerScreen, setEulerScreen] = useState('');
  const [nativeFile, setNativeFile] = useState<File | null>(null);
  const [dossierFile, setDossierFile] = useState<File | null>(null);
  const [evidenceReport, setEvidenceReport] = useState<Record<string, Row>>({});
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    setPlan(null); setPhaseRecords({}); setError(''); setMessage(''); setHydro(''); setLuoyu('');
    setFieldObservations(''); setPumpingTest(''); setCrackCrosscheck(''); setEulerScreen('');
    setNativeFile(null); setDossierFile(null); setEvidenceReport({});
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
  async function run(action: 'plan' | 'review' | 'dossier' | 'template' | 'sensitivity') {
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
      } else if (action === 'sensitivity') {
        const data = await fetchJson<Row>('/luoyu-3-3/boundary-sensitivity?mesh_max_m=0.25');
        if (data.sameConditionsAsOriginal !== false || data.formalSameConditionVerification !== false)
          throw new Error('研究输出缺少正式验收限制，拒绝下载');
        download(new Blob([JSON.stringify(data, null, 2)], {type:'application/json;charset=utf-8'}),
          'luoyu-3-3-boundary-sensitivity-conditional.json');
        setMessage('已导出桩端、冠梁和支撑刚度的七组条件化研究结果。该结果不能用于判定原工程真实边界。');
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
  async function evidenceAction(action: 'field' | 'pumping' | 'crack' | 'euler' | 'native' | 'compare' | 'package' | 'schema' | 'native-template') {
    setError(''); setMessage(''); setWorking(true);
    try {
      if (action === 'schema') {
        const data = await fetchJson<Row>('/schemas');
        download(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json;charset=utf-8'}), 'P1_P4_input_schemas.json');
        setMessage('已下载全部专项 JSON 输入合同和单位约束。');
        return;
      }
      if (action === 'native-template') {
        const r = await fetch(base + '/luoyu-3-3/native-numeric/blank-template', {credentials: 'include'});
        if (!r.ok) throw new Error(`模板下载失败（${r.status}）`);
        download(await r.blob(), 'luoyu_3_3_native_numeric_blank.zip');
        setMessage('已下载五阶段空白模板。请使用真实软件的原始数值填写，并重新计算 metadata.json 中的文件 SHA256。');
        return;
      }
      const isFile = ['native','compare','package'].includes(action);
      let path = '';
      let jsonText = '';
      if (action === 'field') { path='/hydraulic/observations/validate'; jsonText=fieldObservations; }
      if (action === 'pumping') { path='/hydraulic/pumping-test/interpret'; jsonText=pumpingTest; }
      if (action === 'crack') { path='/circular/crack-crosscheck'; jsonText=crackCrosscheck; }
      if (action === 'euler') { path='/circular/second-order/reference-screen'; jsonText=eulerScreen; }
      if (action === 'native') path='/luoyu-3-3/native-numeric/validate';
      if (action === 'compare') path='/luoyu-3-3/native-numeric/compare';
      if (action === 'package') path='/acceptance/verify-package';
      let data: Row;
      if (isFile) {
        const file = action === 'package' ? dossierFile : nativeFile;
        if (!file) throw new Error('请先选择要检验的 ZIP 文件');
        if (file.size > (action === 'package' ? 50_000_000 : 16_000_000)) throw new Error('上传文件超过大小限制');
        const form = new FormData(); form.set('upload', file);
        if (action === 'compare') {
          const candidate = await (async () => {
            const r=await fetch('/api/engineering/one-strut/research/luoyu-3-3',{credentials:'include'});
            if(!r.ok) throw new Error('研究模型计算失败，不能进行逐点比较');
            return await r.json() as Row;
          })();
          form.set('candidate_json', JSON.stringify(candidate));
        }
        const response = await fetch(base + path, {method:'POST', credentials:'include', body:form});
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(String(detail.detail || `ZIP 校验失败（${response.status}）`));
        }
        data = await response.json() as Row;
      } else {
        if (!jsonText.trim()) throw new Error('请先填写或导入符合 Schema 的 JSON 输入');
        let input:unknown;
        try { input = JSON.parse(jsonText); } catch { throw new Error('JSON 格式有误，请下载输入 Schema 检查字段'); }
        if ((action === 'field' || action === 'pumping') &&
            (safeObject(input).project_id !== result.projectId || safeObject(input).section_id !== result.sectionId)) {
          throw new Error('现场证据的项目与剖面必须与当前冻结计算一致');
        }
        if (action === 'crack' && (safeObject(input).result_hash !== result.resultHash ||
                                   safeObject(input).snapshot_hash !== result.snapshotHash)) {
          throw new Error('裂缝计算报告的结果身份必须与当前冻结计算一致');
        }
        data = await fetchJson<Row>(path, input);
      }
      const status = String(data.assessmentStatus ?? data.status ?? '已核验');
      if (action === 'native' && (data.projectId !== result.projectId || data.sectionId !== result.sectionId)) {
        throw new Error('原生数值 ZIP 的项目/剖面与当前冻结结果不一致；请先校正并重新校验');
      }
      if (action === 'package' && data.resultHash !== result.resultHash) {
        throw new Error('成果包内部结果身份与当前冻结结果不一致，不能作为本项目复核资料');
      }
      setEvidenceReport(prev => ({...prev, [action]: data}));
      setMessage(`${action.toUpperCase()}：${status}。已记录本次界面的检查结果；请查看适用限制。`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setWorking(false); }
  }
  return <details className="ossAssurance" aria-label="P1到P4工程验收">
    <summary>P1—P4 规范证据与工程校审</summary>
    <p>统一使用当前计算结果：{String(result.resultHash).slice(0, 24)}…。圆桩轴压抗剪仅在 GB/T 50010 第 6.3.15/6.3.12 条已确认适用范围内计算；圆桩裂缝及现场水文仍需独立证据。本模块只能导出受限工作版。</p>
    <div className="ossActions">
      <button type="button" disabled={working} onClick={() => void run('plan')}>核对 P1—P4 缺项</button>
      {String(result.sectionId) === '3-3' && <button type="button" className="secondary" disabled={working} onClick={() => void run('template')}>下载 3-3 补件模板</button>}
      {String(result.sectionId) === '3-3' && <button type="button" className="secondary" disabled={working} onClick={() => void run('sensitivity')}>导出桩端与支撑敏感性研究</button>}
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
    <details className="ossEvidenceTools">
      <summary>专项证据导入与校验（P1—P4）</summary>
      <p className="ossEvidenceNote">每项检查仅核对已提交数据、数值或文件字节，未完成的现场调查与规范验算继续列入阻断清单。</p>
      <div className="ossActions"><button type="button" className="secondary" disabled={working} onClick={() => void evidenceAction('schema')}>下载输入 Schema 与单位合同</button></div>
      <div className="ossEvidenceGrid">
        <section className="ossEvidenceCard" aria-label="P1现场水文证据">
          <h4>P1 · 现场水位及抽水试验</h4>
          <p>观测按施工阶段、井号和含时区时间一对一配对；独立给出 Thiem 承压含水层参数候选。</p>
          <label className="ossAssuranceField">水位观测 JSON
            <textarea value={fieldObservations} onChange={e=>setFieldObservations(e.target.value)} placeholder="ObservationReviewInput：项目、剖面、阶段、井号、坑内外、观测时间及文件哈希" />
          </label>
          <div className="ossActions"><button type="button" disabled={working} onClick={() => void evidenceAction('field')}>核对同步水位</button></div>
          <label className="ossAssuranceField">抽水试验 JSON
            <textarea value={pumpingTest} onChange={e=>setPumpingTest(e.target.value)} placeholder="PumpingTestInterpretationInput：Q、两观测半径、降深、含水层厚度、方法适用条件" />
          </label>
          <div className="ossActions"><button type="button" disabled={working} onClick={() => void evidenceAction('pumping')}>解释抽水试验</button></div>
          {evidenceReport.field && <p role="status">水位配对 {String(evidenceReport.field.pairedCount ?? 0)} 组；观测最大外减内水头差 {String(evidenceReport.field.maxObservedPairedDeltaHeadM ?? '未取得')} m。设计最不利水头待核定。</p>}
          {evidenceReport.pumping && <p role="status">Thiem 候选 K：{String(evidenceReport.pumping.hydraulicConductivityMPerS ?? '不适用')} m/s；方法条件与现场边界需复核。</p>}
        </section>
        <section className="ossEvidenceCard" aria-label="P2圆桩专项校核">
          <h4>P2 · 圆桩裂缝与二阶效应</h4>
          <p>双报告校验需要不同审核人和来源文件；欧拉计算提供理论参考，不代表实际土—结构二阶效应验算。</p>
          <label className="ossAssuranceField">裂缝宽度双报告 JSON
            <textarea value={crackCrosscheck} onChange={e=>setCrackCrosscheck(e.target.value)} placeholder="CrackCrosscheckInput：冻结结果哈希、SLS 控制点、两份报告及差值容许量" />
          </label>
          <div className="ossActions"><button type="button" disabled={working} onClick={() => void evidenceAction('crack')}>对照双份裂缝报告</button></div>
          <label className="ossAssuranceField">欧拉二阶筛查 JSON
            <textarea value={eulerScreen} onChange={e=>setEulerScreen(e.target.value)} placeholder="EulerSecondOrderScreenInput：N、EI、有效长度与计算依据" />
          </label>
          <div className="ossActions"><button type="button" disabled={working} onClick={() => void evidenceAction('euler')}>运行二阶参考筛查</button></div>
          {evidenceReport.crack && <p role="status">裂缝复核：{String(evidenceReport.crack.assessmentStatus)}，适用圆桩计算方法仍需审核。</p>}
          {evidenceReport.euler && <p role="status">N/Ncr：{Number(evidenceReport.euler.compressionOverEulerCritical).toFixed(4)}；实际体系二阶验算仍待完成。</p>}
        </section>
        <section className="ossEvidenceCard" aria-label="P3螺屿村原生数值">
          <h4>P3 · 螺屿村原生五阶段数值</h4>
          <p>校验 S1—S5 的逐点 u/M/V、坑内外土压力、活动支撑及安装参考位移。比较前须确认同一工程坐标与边界条件。</p>
          <div className="ossActions"><button type="button" className="secondary" disabled={working} onClick={() => void evidenceAction('native-template')}>下载五阶段空白 ZIP</button></div>
          <label className="ossAssuranceField">原始数值 ZIP（最大 16 MB）
            <input type="file" accept=".zip,application/zip" key={String(result.resultHash)} onChange={e=>setNativeFile(e.target.files?.[0] ?? null)} />
          </label>
          <div className="ossActions"><button type="button" disabled={working || !nativeFile || String(result.sectionId)!=='3-3'} onClick={() => void evidenceAction('native')}>检查原始数值完整性</button>
            <button type="button" className="secondary" disabled={working || !nativeFile || String(result.sectionId)!=='3-3'} onClick={() => void evidenceAction('compare')}>与条件化研究模型逐点比较</button>
          </div>
          {evidenceReport.native && <p role="status">文件内容与声明 SHA256 一致；原始来源和工程方法仍未独立认证。ZIP：{String(evidenceReport.native.archiveSha256).slice(0,16)}…</p>}
          {evidenceReport.compare && <p role="status">数值比较：{String(evidenceReport.compare.assessmentStatus)}。该结果不构成原工程同条件验收通过。</p>}
        </section>
        <section className="ossEvidenceCard" aria-label="P4成果包校验">
          <h4>P4 · 成果包完整性</h4>
          <p>验证外层和原始成果两套清单、冻结计算身份与阶段记录。签名须由独立可信公钥离线校验。</p>
          <label className="ossAssuranceField">P1—P4 工作版 ZIP（最大 50 MB）
            <input type="file" accept=".zip,application/zip" key={String(result.resultHash)} onChange={e=>setDossierFile(e.target.files?.[0] ?? null)} />
          </label>
          <div className="ossActions"><button type="button" disabled={working || !dossierFile} onClick={() => void evidenceAction('package')}>验证双层成果清单</button></div>
          {evidenceReport.package && <p role="status">完整性：{String(evidenceReport.package.status)}；可信签名和正式校审仍待完成。</p>}
        </section>
      </div>
    </details>
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
