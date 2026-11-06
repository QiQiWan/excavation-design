import { useEffect, useState } from 'react';
import './agent-pilot.css';

type Json = Record<string, any>;
type Run = {
  id: string; actor: string; status: string; step: string; progress: number;
  sourceResultHash: string; sourceSnapshotHash: string; diagnostics?: Json;
  summary?: Json; error?: string | null; modelMode: string;
};
type Candidate = { id:string; patch:Json; status:string; metrics:Json; resultHash?:string; error?:string | null };
const base = '/api/agents';
async function req<T>(url: string, payload?: Json): Promise<T> {
  const response = await fetch(base + url, {
    credentials:'include', method: payload === undefined ? 'GET' : 'POST',
    headers:payload === undefined ? undefined : {'Content-Type':'application/json'},
    body:payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
  return data as T;
}
function metric(value:unknown,unit=''):string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}${unit}` : '未计算';
}
const statusName:Record<string,string> = {
  queued:'等待执行', running:'执行中', paused:'已暂停', awaiting_review:'等待工程师处理',
  completed:'已记录工作版采用', rejected:'已拒绝', cancelled:'已取消', failed:'执行失败',
  completed_no_candidates:'没有可执行候选',
};

/** A5/A6 pilot only; no direct mutation of project geometry or construction issue. */
export default function AgentPilotPanel({result,projectId,onAdoptResult}:{result:Json;projectId:string;onAdoptResult?:(resultHash:string)=>Promise<void>}) {
  const [run,setRun] = useState<Run|null>(null);
  const [candidates,setCandidates] = useState<Candidate[]>([]);
  const [mode,setMode] = useState<'off'|'optional'>('off');
  const [modelStatus,setModelStatus] = useState<Json|null>(null);
  const [selected,setSelected] = useState('');
  const [comment,setComment] = useState('');
  const [ack,setAck] = useState(false);
  const [busy,setBusy] = useState('');
  const [error,setError] = useState('');
  const [events,setEvents] = useState<Json[]>([]);
  const [selection,setSelection] = useState<Json|null>(null);
  useEffect(() => {
    let disposed = false;
    setRun(null);setCandidates([]);setEvents([]);setSelection(null);setError('');
    void Promise.all([req<Json>('/model/configuration'),req<Json>('/runs?project_id='+encodeURIComponent(projectId))])
      .then(([config,rows]) => {
        if (disposed) return;
        setModelStatus(config);
        const matching = (rows.runs||[]).find((x:Run) => x.sourceResultHash === result.resultHash);
        if (matching) setRun(matching);
      }).catch(e => { if (!disposed) setError(String(e)); });
    return () => { disposed = true; };
  },[result.resultHash,projectId]);
  useEffect(() => {
    if (!run?.id) return;
    const id=run.id;
    let disposed=false;
    const poll = async () => {
      try {
        const overview=await req<Json>('/runs/'+id+'/overview');
        if (disposed) return;
        setRun(overview.run as Run);setCandidates(overview.candidates||[]);
        setEvents(overview.events||[]);setSelection(overview.selection);
      } catch (e) { if (!disposed) setError(String(e)); }
    };
    void poll();
    const active=['queued','running'].includes(run.status);
    const timer=active ? window.setInterval(() => { void poll(); },2500) : null;
    return () => { disposed=true;if(timer!==null)window.clearInterval(timer); };
  },[run?.id,run?.status]);
  async function begin() {
    setBusy('start');setError('');
    try {
      const newRun=await req<Run>('/runs',{
        result_hash:result.resultHash,max_candidates:3,model_mode:mode,
        client_request_id:crypto.randomUUID(),
      });
      setRun(newRun);setCandidates([]);setEvents([]);setSelected('');setAck(false);
    } catch(e) { setError(String(e)); } finally { setBusy(''); }
  }
  async function action(name:'advance'|'cancel'|'pause'|'resume') {
    if (!run) return;
    setBusy(name);setError('');
    try { setRun(await req<Run>('/runs/'+run.id+'/'+name,{})); }
    catch(e) {setError(String(e));} finally {setBusy('');}
  }
  async function decide(decision:'adopt_for_working_copy'|'reject') {
    if (!run||!selection) return;
    setBusy('decision');setError('');
    try {
      await req<Json>('/runs/'+run.id+'/decision',{
        candidate_id:decision==='reject'?null:selected,
        decision,expected_active_snapshot_hash:selection.snapshotHash,
        acknowledgement:ack,comment,
      });
      setRun(await req<Run>('/runs/'+run.id));
      const latest=await req<Json>('/runs/'+run.id+'/selection');
      setSelection(latest);
      if (decision==='adopt_for_working_copy' && latest.resultHash && onAdoptResult) await onAdoptResult(latest.resultHash);
    } catch(e) { setError(String(e)); } finally {setBusy('');}
  }
  const activeRun=run?.status==='queued'||run?.status==='running';
  const actionable=run?.status==='awaiting_review';
  const eligible=candidates.find(c=>c.id===selected)?.metrics?.workingSelectionEligible===true;
  const runFromCurrent=selection?.snapshotHash===run?.sourceSnapshotHash;
  return <details className="ossAgent" open={!!run}>
    <summary>智能设计助手 · A5 计算诊断 / A6 候选优化 <span>V3.88.0 试点</span></summary>
    <p className="ossAgentNote">使用冻结计算结果生成独立试算方案。所有候选调用原计算内核；AI 解释不影响规范判定。正式施工图签发始终关闭。</p>
    {!run && <div className="ossAgentControls">
      <label>模型解释
        <select value={mode} onChange={e=>setMode(e.target.value as 'off'|'optional')}>
          <option value="off">关闭（确定性规则）</option>
          <option value="optional" disabled={!modelStatus?.enabled||!modelStatus?.configured}>启用已配置的推理模型</option>
        </select>
      </label>
      <button onClick={()=>void begin()} disabled={!!busy||result.analysisStatus!=='complete'}>{busy?'提交中…':'诊断并试算 3 个候选'}</button>
      <small>{modelStatus?.enabled&&modelStatus?.configured?'模型服务已配置':'模型默认关闭，工程工具可离线使用'}</small>
    </div>}
    {run && <>
      <div className="ossAgentRun">
        <b>任务：{statusName[run.status]||run.status}</b><span>{run.step}</span>
        <progress aria-label="Agent任务进度" max={100} value={run.progress}/>
        <small>工作快照 {run.sourceSnapshotHash.slice(0,14)} · 任务 {run.id.slice(0,12)}</small>
      </div>
      {run.error && <p className="ossError">{run.error}</p>}
      <div className="ossAgentControls">
        {activeRun && <button className="secondary" disabled={!!busy} onClick={()=>void action('advance')}>手动推进一步</button>}
        {activeRun && <button className="secondary" disabled={!!busy} onClick={()=>void action('pause')}>暂停后续步骤</button>}
        {activeRun && <button className="secondary" disabled={!!busy} onClick={()=>void action('cancel')}>取消任务</button>}
        {(run.status==='failed'||run.status==='paused') && <button className="secondary" disabled={!!busy} onClick={()=>void action('resume')}>从当前阶段恢复</button>}
        {!activeRun && <button className="secondary" disabled={!!busy} onClick={()=>setRun(null)}>新建任务</button>}
      </div>
      {run.diagnostics && <details className="ossAgentSection"><summary>计算问题：{run.diagnostics.unresolvedCount} 项（展开查看）</summary>
        <p>物理约束问题：{run.diagnostics.physicalFailureCount} 项；模型解释：{run.diagnostics.modelExplanation?.status||'未调用'}。</p>
        {(run.diagnostics.issues||[]).slice(0,25).map((x:Json,i:number)=><p key={i}><b>{x.code}</b>：{x.message}</p>)}
      </details>}
      {candidates.length>0 && <div className="ossAgentTable"><h4>候选独立复算</h4>
        <div className="ossTableScroll"><table><thead><tr><th>选择</th><th>调整参数</th><th>原值 → 候选值</th><th>位移</th><th>求解</th><th>工作版资格</th></tr></thead><tbody>{candidates.map(c=><tr key={c.id}>
          <td><input type="radio" aria-label={`选择候选${c.id.slice(0,6)}`} name="agent-candidate" checked={selected===c.id} onChange={()=>setSelected(c.id)}/></td>
          <td>{c.patch.field}</td><td>{metric(c.patch.before)} → {metric(c.patch.after)}</td>
          <td>{metric(c.metrics.candidateDisplacementMm,' mm')}</td><td>{c.status}</td>
          <td>{c.metrics.workingSelectionEligible?'可提交人工确认':'暂不可采用'}</td>
        </tr>)}</tbody></table></div>
        {selected && <p className="ossAgentNote">{candidates.find(x=>x.id===selected)?.patch.reason}。未完成的规范项仍需工程师独立复核。</p>}
      </div>}
      {actionable && <section className="ossAgentReview">
        <h4>工程师处理</h4>
        <p>采用仅修改单剖面 Agent 的工作版选择，不覆盖原计算快照，也不修改正式项目图纸。</p>
        <label>处理意见<textarea value={comment} onChange={e=>setComment(e.target.value)} placeholder="说明选择理由、仍需补充的工程验算及来源证据" /></label>
        <label className="ossAgentAck"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>
          已知候选不代表规范验算完成，正式施工签发保持禁止</label>
        <div className="ossAgentControls">
          <button disabled={!!busy||!ack||comment.trim().length<12||!eligible||!runFromCurrent} onClick={()=>void decide('adopt_for_working_copy')}>采用至工作版选择（需校审权限）</button>
          <button className="secondary" disabled={!!busy||!ack||comment.trim().length<12} onClick={()=>void decide('reject')}>退回 / 不采用</button>
        </div>
        {!runFromCurrent && <p className="ossError">工作选择版本已变化，禁止采用过期候选。</p>}
      </section>}
      {run.summary && <p className="ossAgentNote">已计算 {run.summary.calculatedCount}/{run.summary.candidateCount} 个候选。正式工程资格：未确认。</p>}
      <details className="ossAgentSection"><summary>执行追溯 · {events.length} 条记录</summary>
        {events.map(x=><p key={x.id}><small>{x.createdAt} · {x.type}</small></p>)}
      </details>
    </>}
    {error && <p role="alert" className="ossError">{error}</p>}
  </details>;
}
