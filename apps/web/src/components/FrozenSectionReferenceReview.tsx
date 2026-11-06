import { useEffect, useRef, useState } from 'react';
import { api, type FrozenSectionInput, type FrozenSectionResult, type SectionReferenceContext, type SectionReferenceComparison } from '../api/client';
import { asObject, asRows, display, statusLabel } from './FrozenSectionReviewDetails';

type Props = { input: FrozenSectionInput | null; result: FrozenSectionResult | null; generation: number; parentBusy: boolean; onBind: (hash: string) => void; downloadJson: (value: unknown, name: string) => void };
const evidenceLabels: Record<string,string> = { original_fact:'原文事实',derived_interpretation:'推导解释',unconfirmed:'未确认' };
export default function FrozenSectionReferenceReview({ input, result, generation, parentBusy, onBind, downloadJson }: Props) {
  const [reference,setReference]=useState<SectionReferenceContext|null>(null);
  const [bundle,setBundle]=useState<Record<string,unknown>|null>(null);
  const [comparison,setComparison]=useState<{value:SectionReferenceComparison;generation:number}|null>(null);
  const [busy,setBusy]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [all,setAll]=useState(false);
  const operation=useRef(0);
  const current=useRef({generation,result});
  current.current={generation,result};
  useEffect(()=>()=>{operation.current+=1;},[]);
  const contractHash=typeof reference?.contract.contractHash==='string'?reference.contract.contractHash:null;
  const bundleHash=typeof bundle?.bundleHash==='string'?bundle.bundleHash:null;
  const visible=comparison && comparison.generation===generation && result && comparison.value.snapshotHash===result.snapshotHash && comparison.value.resultHash===result.resultHash && comparison.value.contractHash===contractHash && comparison.value.curveBundleHash===bundleHash ? comparison.value:null;
  const blocked=!!busy||parentBusy;
  const parameters=asRows(reference?.contract.parameters);
  const critical=Array.isArray(reference?.contract.criticalConditionIds)?reference.contract.criticalConditionIds:[];
  const important=parameters.filter(p=>critical.includes(p.id));
  const shown=all?parameters:(important.length?important:parameters).slice(0,12);

  async function loadReference() {
    const token=++operation.current;
    setBusy('load');setError(null);setComparison(null);
    try {
      const value=await api.getA3Reference();
      if(token!==operation.current)return;
      if(!value.contract?.contractHash||!value.assessment)throw new Error('条件资料缺少合同身份或评估。');
      setReference(value);setBundle(null);
    } catch(reason){if(token===operation.current)setError(String(reason instanceof Error?reason.message:reason));}
    finally{if(token===operation.current)setBusy(null);}
  }
  async function upload(file:File|undefined,kind:'contract'|'curves') {
    if(!file)return;
    const token=++operation.current;
    setBusy(kind);setError(null);setComparison(null);
    try {
      const parsed:unknown=JSON.parse(await file.text());
      if(token!==operation.current)return;
      if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('请上传完整 JSON 对象。');
      if(kind==='contract') {
        const value=await api.saveSectionReferenceContract(parsed as Record<string,unknown>);
        if(token!==operation.current)return;
        if(!value.contract?.contractHash||!value.assessment)throw new Error('保存合同响应缺少身份。');
        setReference({...value,curveTemplate:reference?.curveTemplate});setBundle(null);
      } else {
        const value=await api.saveSectionReferenceCurves(parsed as Record<string,unknown>);
        if(token!==operation.current)return;
        if(!value.bundleHash)throw new Error('保存曲线响应缺少冻结身份。');
        setBundle(value);
      }
    } catch(reason){if(token===operation.current)setError(String(reason instanceof Error?reason.message:reason));}
    finally{if(token===operation.current)setBusy(null);}
  }
  async function compare() {
    if(!result||!contractHash||!bundleHash)return;
    const token=++operation.current, capturedGeneration=generation;
    const frozen=result;
    setBusy('compare');setError(null);setComparison(null);
    try {
      const value=await api.compareSectionReference(frozen.resultHash,{contract_hash:contractHash,curve_bundle_hash:bundleHash});
      if(token!==operation.current||current.current.generation!==capturedGeneration||current.current.result?.resultHash!==frozen.resultHash)return;
      if(value.schemaVersion!=='pitguard.reference-comparison-record/1'||value.snapshotHash!==frozen.snapshotHash||value.resultHash!==frozen.resultHash||value.contractHash!==contractHash||value.curveBundleHash!==bundleHash||!value.comparisonHash||!value.report)throw new Error('原项目比较响应与当前结果或参考资料身份不一致，未采用。');
      setComparison({value,generation:capturedGeneration});
    } catch(reason){if(token===operation.current&&current.current.generation===capturedGeneration)setError(String(reason instanceof Error?reason.message:reason));}
    finally{if(token===operation.current)setBusy(null);}
  }
  async function saveResultCurves() {
    if(!result)return;
    const token=++operation.current,capturedGeneration=generation,frozen=result;
    setBusy('download');setError(null);
    try {
      const value=await api.getSectionCurves(frozen.resultHash);
      if(token!==operation.current||current.current.generation!==capturedGeneration||current.current.result?.resultHash!==frozen.resultHash)return;
      if(asObject(value.source).sha256!==frozen.resultHash||!value.bundleHash)throw new Error('返回数值曲线与当前结果身份不一致。');
      downloadJson(value,`pitguard-curves-${frozen.resultHash.slice(0,12)}.json`);
    } catch(reason){if(token===operation.current&&current.current.generation===capturedGeneration)setError(String(reason instanceof Error?reason.message:reason));}
    finally{if(token===operation.current)setBusy(null);}
  }
  function downloadContract() {
    if(!reference)return;
    // Download an editable, unsealed contract. Its original facts and source
    // evidence are intact; the server assigns a new hash after confirmation.
    const value={...reference.contract};delete value.contractHash;
    downloadJson(value,'a3-3-reference-conditions.json');
  }
  return <details className="frozenSectionReference"><summary>原项目条件与数值曲线</summary>
    <p className="frozenSectionFine">原文、推导和未确认条件分别保留。上传有来源确认的条件及原软件数值曲线后，可与当前冻结结果比较；比较报告独立保存，不改写已封存结果。</p>
    <div className="frozenSectionActions">
      <button type="button" className="secondary" onClick={()=>void loadReference()} disabled={blocked}>加载 A3-3 条件资料</button>
      <button type="button" className="secondary" onClick={downloadContract} disabled={!reference||blocked}>下载条件 JSON</button>
      <button type="button" className="secondary" onClick={()=>reference?.curveTemplate&&downloadJson(reference.curveTemplate,'a3-3-original-curves-empty-template.json')} disabled={!reference?.curveTemplate||blocked}>下载空曲线模板</button>
    </div>
    {error?<div role="alert" className="frozenSectionError">{error}</div>:null}
    {busy?<p aria-live="polite" className="frozenSectionFine">正在{busy==='compare'?'比较原项目曲线':busy==='download'?'读取数值曲线':'处理参考资料'}…</p>:null}
    {reference?<>
      <p className="frozenSectionFine">条件合同 <code>{contractHash}</code></p>
      <div className="frozenSectionTableScroll"><table aria-label="原项目条件身份"><thead><tr><th>条件</th><th>原值</th><th>当前值 / 推导</th><th>证据状态</th></tr></thead><tbody>{shown.map((p,i)=><tr key={i}><th scope="row">{display(p.identity)}<small>{display(p.id)}</small></th><td>{display(p.originalValue)} {p.originalValue==null?'':display(p.unit)}</td><td>{display(p.value)} {p.value==null?'':display(p.unit)}{p.derivation?<small>{display(p.derivation)}</small>:null}</td><td>{evidenceLabels[display(p.status)]??display(p.status)}{p.requiresConfirmation&&!p.confirmation?<small>尚需原始来源确认</small>:null}</td></tr>)}</tbody></table></div>
      {parameters.length>shown.length||all?<button type="button" className="secondary" onClick={()=>setAll(!all)}>{all?'仅显示关键条件':`显示全部 ${parameters.length} 项条件`}</button>:null}
      <details><summary>待补条件（{Array.isArray(reference.assessment.missingConditions)?reference.assessment.missingConditions.length:0}）</summary><ul>{(Array.isArray(reference.assessment.missingConditions)?reference.assessment.missingConditions:[]).map((item,i)=>{const r=asObject(item);return <li key={i}>{typeof item==='string'?item:`${display(r.identity)} · ${display(r.reason)}`}</li>;})}</ul></details>
      <button type="button" className="secondary" disabled={blocked||!contractHash||input?.schema_version!=='pitguard.frozen-section/2'} onClick={()=>contractHash&&onBind(contractHash)}>将条件合同绑定到 v2 输入</button>
      <p className="frozenSectionFine">绑定仅写入合同身份，并使当前快照与结果失效；缺失参数仍需明确提供。v1 输入不会自动转换。</p>
    </>:null}
    <div className="frozenSectionActions">
      <label className="frozenSectionImport">上传已确认条件 JSON<input aria-label="上传已确认条件 JSON" type="file" accept=".json,application/json" disabled={blocked} onChange={e=>{const file=e.target.files?.[0];e.target.value='';void upload(file,'contract');}} /></label>
      <label className="frozenSectionImport">上传原项目数值曲线 JSON<input aria-label="上传原项目数值曲线 JSON" type="file" accept=".json,application/json" disabled={blocked} onChange={e=>{const file=e.target.files?.[0];e.target.value='';void upload(file,'curves');}} /></label>
    </div>
    {bundleHash?<p className="frozenSectionFine">曲线冻结身份 <code>{bundleHash}</code></p>:null}
    <div className="frozenSectionActions">
      <button type="button" onClick={()=>void compare()} disabled={blocked||!result||!contractHash||!bundleHash}>比较原项目数值曲线</button>
      <button type="button" className="secondary" onClick={()=>void saveResultCurves()} disabled={blocked||!result}>下载本结果数值曲线 JSON</button>
    </div>
    {visible?<article className="frozenReferenceComparison" aria-label="已绑定原项目曲线比较">
      <h4>{statusLabel(visible.report.status)}</h4><p className="frozenSectionFine">数值状态：{statusLabel(visible.report.numericalStatus)}；同条件核实：{visible.report.sameConditionsVerified===true?'已核实':'未完成'}。本比较不构成整体工程通过。</p>
      {Array.isArray(visible.report.blockers)&&visible.report.blockers.length?<ul>{visible.report.blockers.map((item,i)=><li key={i}>{display(item)}</li>)}</ul>:null}
      <p className="frozenSectionFine">比较身份 <code>{visible.comparisonHash}</code></p>
      <details><summary>逐阶段、包络与配筋比较数据</summary><pre>{JSON.stringify(visible.report,null,2)}</pre></details>
      <button type="button" className="secondary" onClick={()=>downloadJson(visible,`pitguard-reference-comparison-${visible.comparisonHash.slice(0,12)}.json`)}>下载原项目比较 JSON</button>
    </article>:null}
  </details>;
}
