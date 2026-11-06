import { useEffect, useRef, useState } from 'react';
import './one-strut.css';
import AssuranceGatePanel from './AssuranceGatePanel';
import AgentPilotPanel from './AgentPilotPanel';

type Json = Record<string, any>;
type Preflight = { schemaValid: boolean; engineeringEvidenceComplete?: boolean; issues: { field: string; code: string; message: string }[] };
type Calculation = { resultHash: string; snapshotHash: string; analysisStatus: string; stages: Json[]; governing: Json;
  checks: Json; issues: { code: string; field: string; message: string }[]; recommendations: { field: string; text: string }[];
  reinforcement: Json; engineeringStatus: string; formalIssueReady: boolean; normativeCoverage?: Json; actionCombinations?: Json; historicalReferenceComparison?: Json | null };
const endpoint = '/api/engineering/one-strut';
async function request<T>(path: string, payload?: unknown): Promise<T> {
  const result = await fetch(endpoint + path, {
    method: payload === undefined ? 'GET' : 'POST', credentials: 'include',
    headers: payload === undefined ? undefined : {'Content-Type': 'application/json'},
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  if (!result.ok) {
    const body = await result.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${result.status})`);
  }
  return result.json() as Promise<T>;
}
function clone(input: Json): Json { return JSON.parse(JSON.stringify(input)) as Json; }
function format(value: unknown, digits = 2): string { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '未计算'; }
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1500);
}
export default function OneStrutWorkbench({ projectId }: { projectId: string }) {
  const [input, setInput] = useState<Json | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [result, setResult] = useState<Calculation | null>(null);
  const [review, setReview] = useState<Json | null>(null);
  const [reviewer, setReviewer] = useState('');
  const [note, setNote] = useState('');
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [view, setView] = useState<'input' | 'results'>('input');
  const [boundaryReport, setBoundaryReport] = useState<Json | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  function edit(fn: (draft: Json) => void) {
    generation.current++; setInput(prev => { if (!prev) return prev; const next = clone(prev); fn(next); return next; });
    setResult(null); setReview(null); setPreflight(null); setAcknowledged({}); setBoundaryReport(null); setError('');
  }
  async function example() {
    const token = ++generation.current; setBusy('example'); setError('');
    try { const value = await request<Json>('/example'); if (generation.current === token) {
      value.project_id = projectId; setInput(value); setResult(null); setReview(null); setView('input');
    }} catch (e) { if (generation.current === token) setError(String(e)); }
    finally { if (generation.current === token) setBusy(''); }
  }
  async function importFile(file?: File) {
    if (!file) return;
    const token = ++generation.current; setBusy('import'); setError('');
    try { const data = JSON.parse(await file.text()) as Json;
      if (data.schema_version !== 'pitguard.one-strut/1') throw new Error('请导入单排桩一道支撑 JSON 输入');
      if (generation.current === token) { setInput(data); setResult(null); setReview(null); setPreflight(null); setView('input'); }
    } catch(e) { if (generation.current === token) setError(String(e)); }
    finally { if (generation.current === token) setBusy(''); }
  }
  async function run(checkOnly: boolean) {
    if (!input) return;
    const token = ++generation.current; setBusy(checkOnly ? 'check' : 'calculate'); setError(''); setResult(null); setReview(null);
    try {
      const r = checkOnly ? await request<Preflight>('/preflight', input) : await request<Calculation>('/calculate', input);
      if (generation.current !== token) return;
      if (checkOnly) setPreflight(r as Preflight);
      else { const calculation = r as Calculation; setResult(calculation); setPreflight(null); setView('results'); }
    } catch(e) { if (generation.current === token) setError(String(e)); }
    finally { if (generation.current === token) setBusy(''); }
  }
  async function approve() {
    if (!result) return;
    const token = ++generation.current; setBusy('review'); setError('');
    try {
      const accepted = await request<Json>('/review', { result_hash: result.resultHash, reviewer, notes: note,
        acknowledged_codes: Object.keys(acknowledged).filter(key => acknowledged[key]), });
      if (generation.current === token) setReview(accepted);
    } catch(e) { if (generation.current === token) setError(String(e)); }
    finally { if (generation.current === token) setBusy(''); }
  }
  async function exportFiles() {
    if (!result || !review) return;
    const token = ++generation.current; setBusy('export'); setError('');
    try {
      const resp = await fetch(endpoint+'/exports',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ result_hash:result.resultHash, review_hash:review.reviewHash })});
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(String(e.detail || '成果生成失败')); }
      const blob=await resp.blob();
      if (generation.current === token) saveBlob(blob,`one_strut_${result.resultHash.slice(0,12)}_review.zip`);
    } catch(e) { if (generation.current === token) setError(String(e)); }
    finally { if (generation.current === token) setBusy(''); }
  }
  function changeValue(path: string, value: string) {
    edit(draft => { const parts=path.split('.'); let target: Json=draft; for(const part of parts.slice(0,-1)) target=target[part] as Json;
      target[parts[parts.length-1]]=value === '' ? 0 : Number(value); });
  }
  function numberField(path: string, text: string, unit: string) {
    if (!input) return null;
    const val=path.split('.').reduce((a: any,key) => a?.[key],input);
    return <label className="ossField" key={path}><span>{text} <small>{unit}</small></span>
      <input aria-label={text} type="number" step="any" value={val ?? ''} onChange={e=>changeValue(path,e.target.value)} /></label>;
  }
  const sourcePaths = input ? [
    'pile.diameter_m','pile.spacing_m','pile.length_m','pile.elastic_modulus_kpa','pile.inertia_factor',
    'strut.stiffness_kn_m','strut.depth_m','displacement_limit_mm','design_effect_factor',
    'gamma0','safety_grade','cage.minimum_ratio','cage.concrete_design_mpa','cage.steel_design_mpa',
    ...(input.groundwater_check?.mode && input.groundwater_check.mode!=='unknown'?['groundwater_check.mode','groundwater_check.assessment_basis']:[]),
    ...(input.support_member?['support_member.member_length_m','support_member.tributary_wall_width_m','support_member.angle_to_wall_normal_deg']:[]),
    ...input.layers.flatMap((layer:Json,i:number) => ['top_m','bottom_m','gamma_natural_kn_m3','gamma_sat_kn_m3',
      'c_effective_kpa','phi_effective_deg','m_kn_m4','soil_group','heave_strength_basis',
      ...(layer.soil_group==='clay'?['consistency']:[]),
      ...(layer.heave_strength_basis==='total'?['heave_c_kpa','heave_phi_deg']:[])].map(field=>`layers.${i}.${field}`)),
  ] : [];
  function setSource(path: string, attribute: string, value: string) {
    edit(draft => { let r=draft.sources.find((x:Json)=>x.path===path);
      if (!r) { r={path,origin:'geotechnical_report',reference:'请补充报告章节页码',state:'candidate',approved_by:null};draft.sources.push(r); }
      r[attribute] = attribute==='approved_by' && !value ? null : value;
      if (attribute==='state' && value==='candidate') r.approved_by=null;
    });
  }
  const uniqueIssues = result ? Array.from(new Set(result.issues.map(item => item.code))) : [];
  const allAcknowledged = uniqueIssues.every(key => acknowledged[key]);
  const issueCounts = result ? result.issues.reduce((out: Record<string, number>, item) => { out[item.code] = (out[item.code] || 0) + 1; return out; }, {}) : {};
  const distinctIssues = result ? result.issues.filter((x,i) => result.issues.findIndex(y => y.code === x.code) === i) : [];
  return <section className="oss" aria-label="单排桩规范设计工作台">
    <header className="ossHead"><div><h3>单排桩 · 一道内支撑</h3><p>输入与来源 → 阶段计算 → 超限处理 → 复核 → 同源成果</p></div><span>规范方法受限工作版</span></header>
    <nav className="ossTabs" aria-label="工作区"><button className={view==='input' ? 'active' : ''} onClick={()=>setView('input')}>工程输入</button><button className={view==='results' ? 'active' : ''} disabled={!result} onClick={()=>setView('results')}>计算与复核</button></nav>
    {error && <p className="ossError" role="alert">{error}</p>}
    {!input && <div className="ossEmpty"><p>导入经核对的剖面参数，或加载仅用于数值测试的演示输入。</p><div className="ossActions"><button onClick={()=>void example()} disabled={!!busy}>加载演示输入</button><label>导入 JSON <input type="file" accept=".json" onChange={e=>{void importFile(e.target.files?.[0]);e.target.value='';}} /></label></div></div>}
    {input && view==='input' && <>
      <div className="ossBanner">{input.purpose==='verification_example' ? '演示参数：仅用于数值验证。请逐项核对勘察与材料输入，再作为真实工程审查。' : '项目设计：各参数必须具有实际来源与审批记录，缺少证据会在结果中显式提示。'}</div>
      <div className="ossGrid">
        <label className="ossField"><span>工程编号</span><input value={input.project_id} onChange={e=>edit(d=>{d.project_id=e.target.value;})} /></label>
        <label className="ossField"><span>剖面编号</span><input value={input.section_id} onChange={e=>edit(d=>{d.section_id=e.target.value;})} /></label>
        <label className="ossField"><span>项目所在地</span><input value={input.project_region} onChange={e=>edit(d=>{d.project_region=e.target.value;})} /></label>
        <label className="ossField"><span>输入用途</span><select value={input.purpose} onChange={e=>edit(d=>{d.purpose=e.target.value;})}><option value="verification_example">数值验证</option><option value="project_review">工程复核</option></select></label>
      </div>
      <h4>围护桩与支撑</h4><div className="ossGrid">
        {numberField('pile.diameter_m','桩径','m')}{numberField('pile.spacing_m','桩中心距','m')}{numberField('pile.length_m','桩长','m')}{numberField('pile.elastic_modulus_kpa','混凝土弹性模量','kPa')}{numberField('pile.inertia_factor','刚度折减','—')}
        {numberField('strut.depth_m','支撑深度','m')}{numberField('strut.stiffness_kn_m','单根支撑等效水平刚度','kN/m')}{numberField('strut.spacing_along_wall_m','支撑沿墙间距','m')}{numberField('displacement_limit_mm','水平位移限值','mm')}{numberField('design_effect_factor','设计作用系数','—')}
      </div>
      <details><summary>规范与水文适用条件（缺项会阻断正式判定）</summary>
        <div className="ossGrid"><label className="ossField"><span>基坑安全等级</span><select value={input.safety_grade ?? ''} onChange={e=>edit(d=>{d.safety_grade=e.target.value?Number(e.target.value):null;})}><option value="">尚未确认</option><option value="1">一级</option><option value="2">二级</option><option value="3">三级</option></select></label>
        <label className="ossField"><span>水力稳定调查结果</span><select value={input.groundwater_check?.mode || 'unknown'} onChange={e=>edit(d=>{d.groundwater_check={...(d.groundwater_check||{}),mode:e.target.value};})}><option value="unknown">待调查</option><option value="no_trigger">已确认无触发条件</option><option value="confined">承压水突涌</option><option value="hanging_curtain">悬挂式帷幕流土</option><option value="both">两项均需检查</option></select></label>
        <label className="ossField"><span>水文判断依据</span><input value={input.groundwater_check?.assessment_basis || ''} onChange={e=>edit(d=>{d.groundwater_check={...(d.groundwater_check||{}),assessment_basis:e.target.value};})} placeholder="勘察报告页码/专业复核结论" /></label></div>
        {(input.groundwater_check?.mode==='confined' || input.groundwater_check?.mode==='both') && <div className="ossGrid">
          {numberField('groundwater_check.confined_aquifer_top_depth_m','承压含水层顶深度','m')}
          {numberField('groundwater_check.confined_head_difference_m','水头差','m')}
        </div>}
        {(input.groundwater_check?.mode==='hanging_curtain' || input.groundwater_check?.mode==='both') && <div className="ossGrid">
          {numberField('groundwater_check.hanging_curtain_toe_depth_m','帷幕底深度','m')}
          {numberField('groundwater_check.aquifer_top_or_water_depth_m','含水层水位深度','m')}
          {numberField('groundwater_check.submerged_unit_weight_kn_m3','水下有效重度','kN/m³')}
          <label className="ossField"><span>均质含水层</span><select value={input.groundwater_check?.homogeneous_aquifer===true?'yes':input.groundwater_check?.homogeneous_aquifer===false?'no':''} onChange={e=>edit(d=>{d.groundwater_check.homogeneous_aquifer=e.target.value==='yes'?true:e.target.value==='no'?false:null;})}><option value="">未核实</option><option value="yes">是</option><option value="no">否，需渗流数值分析</option></select></label>
        </div>}
        <p>土类、软土情况、隆起强度口径、承压水头及截水帷幕条件应逐项确认；未确认时结果维持缺项状态。</p>
      </details>
      <details><summary>圆形桩裂缝控制与使用阶段组合</summary>
        <p>一、二级可对明确的使用组合进行混凝土受拉边缘应力核查；三级需要经验证的圆桩裂缝宽度方法。该模块不会用矩形截面公式自行生成裂缝宽度。</p>
        <div className="ossGrid">
          <label className="ossField"><span>裂缝控制等级</span><select value={input.crack_control?.grade ?? ''} onChange={e=>edit(d=>{const grade=Number(e.target.value);d.crack_control=grade?{grade,concrete_tensile_standard_mpa:2.01,material_basis:'待工程师确认的材料抗拉标准值与规范版本',width_limit_mm:grade===3?.2:null}:null;})}><option value="">未核定</option><option value="1">一级</option><option value="2">二级</option><option value="3">三级</option></select></label>
          {input.crack_control && <>
            {numberField('crack_control.concrete_tensile_standard_mpa','混凝土抗拉标准值','MPa')}
            {input.crack_control.grade===3 && numberField('crack_control.width_limit_mm','裂缝宽度限值','mm')}
            <label className="ossField"><span>材料与控制依据</span><input value={input.crack_control.material_basis||''} onChange={e=>edit(d=>{d.crack_control.material_basis=e.target.value;})}/></label>
          </>}
        </div>
        <label>导入已经核对的使用组合及配套轴力 JSON <input type="file" accept=".json" onChange={e=>{void (async()=>{const file=e.target.files?.[0];if(!file)return;try{const data=JSON.parse(await file.text()) as Json;
          if(!Array.isArray(data.load_combinations)||!Array.isArray(data.service_axial_actions))throw new Error('需要 load_combinations 和 service_axial_actions 数组');
          edit(d=>{d.load_combinations=data.load_combinations;d.service_axial_actions=data.service_axial_actions;});
        }catch(err){setError('使用组合文件无效：'+String(err));}})();e.target.value='';}} /></label>
        <p>不得把 ULS 轴力填入 SLS。未提供同一组合和施工阶段的使用轴力时，裂缝验算保持缺项。</p>
      </details>
      <h4>地层与勘察参数</h4>
      <div className="ossTableScroll"><table><thead><tr><th>土层</th><th>层顶 m</th><th>层底 m</th><th>天然重度</th><th>饱和重度</th><th>c′ kPa</th><th>φ′ °</th><th>m kN/m⁴</th><th>土类</th><th>软土判定</th><th>抗隆起强度口径</th></tr></thead><tbody>{input.layers.map((layer: Json,i:number)=><tr key={layer.id}><td>{layer.label}</td>{['top_m','bottom_m','gamma_natural_kn_m3','gamma_sat_kn_m3','c_effective_kpa','phi_effective_deg','m_kn_m4'].map(key=><td key={key}><input aria-label={`${layer.label}${key}`} type="number" step="any" value={layer[key]} onChange={e=>changeValue(`layers.${i}.${key}`,e.target.value)} /></td>)}
            <td><select aria-label={`${layer.label}土类`} value={layer.soil_group||''} onChange={e=>edit(d=>{d.layers[i].soil_group=e.target.value||null;})}><option value="">待确认</option><option value="sand">砂土</option><option value="gravel">碎石土</option><option value="silt">粉土</option><option value="clay">黏性土</option><option value="rock">岩石</option></select></td>
            <td><select aria-label={`${layer.label}软土状态`} value={layer.consistency||''} onChange={e=>edit(d=>{d.layers[i].consistency=e.target.value||null;})}><option value="">未鉴别</option><option value="soft">软土</option><option value="not_soft">非软土</option><option value="unknown">未确认</option></select></td>
            <td><select aria-label={`${layer.label}强度口径`} value={layer.heave_strength_basis||''} onChange={e=>edit(d=>{d.layers[i].heave_strength_basis=e.target.value||null;})}><option value="">待确认</option><option value="effective">有效应力</option><option value="total">总应力</option></select></td>
          </tr>)}</tbody></table></div>
      <details><summary>黏性土抗隆起强度专用值（有试验依据时填写）</summary>
        <div className="ossTableScroll"><table><thead><tr><th>土层</th><th>c / cu (kPa)</th><th>φ / φu (°)</th><th>适用说明</th></tr></thead><tbody>{input.layers.map((l:Json,i:number)=><tr key={l.id}><td>{l.label}</td>{['heave_c_kpa','heave_phi_deg'].map(key=><td key={key}><input type="number" step="any" value={l[key]??''} onChange={e=>edit(d=>{d.layers[i][key]=e.target.value===''?null:Number(e.target.value);})} /></td>)}<td>按对应试验类型与阶段取值</td></tr>)}</tbody></table></div>
      </details>
      <details><summary>实际支撑杆件及节点验算（按支撑平面传力）</summary>
        <label className="ossField"><span>实际支撑材料</span><select value={input.support_member?.kind||''} onChange={e=>edit(d=>{d.support_member=e.target.value?{kind:e.target.value,member_length_m:10,tributary_wall_width_m:d.strut.spacing_along_wall_m,angle_to_wall_normal_deg:0}:null;})}><option value="">未录入，待校审</option><option value="steel">钢支撑</option><option value="concrete">混凝土支撑</option></select></label>
        {input.support_member && <div className="ossGrid">
          {['member_length_m','tributary_wall_width_m','angle_to_wall_normal_deg','section_area_mm2','section_modulus_mm3','material_design_strength_mpa','stability_factor','connection_capacity_kn','design_eccentricity_mm'].map((key,i)=>{
            const labels=['杆件长度 m','沿墙分担宽度 m','与墙面法向夹角 °','截面面积 mm²','截面模量 mm³','设计强度 MPa','经核定稳定系数','节点承载力 kN','设计偏心 mm'];
            return <label className="ossField" key={key}><span>{labels[i]}</span><input type="number" step="any" value={input.support_member[key]??''} onChange={e=>edit(d=>{d.support_member[key]=e.target.value===''?null:Number(e.target.value);})} /></label>;
          })}
          {['section_basis','stability_factor_basis','connection_basis'].map(key=><label className="ossField" key={key}><span>{({section_basis:'截面参数来源',stability_factor_basis:'稳定系数来源',connection_basis:'节点承载力来源'} as Json)[key]}</span><input value={input.support_member[key]||''} onChange={e=>edit(d=>{d.support_member[key]=e.target.value||null;})}/></label>)}
        </div>}
      </details>
      <details><summary>施工不利工况与工程配筋数据</summary>
        <p>针对开挖阶段添加额外地面荷载或不利水位变化；水位变更后需重新评估地下水安全。</p>
        {(input.adverse_cases || []).map((item:Json,i:number)=><div className="ossGrid" key={item.name+i}>
          <label className="ossField"><span>工况名称</span><input value={item.name} onChange={e=>edit(d=>{d.adverse_cases[i].name=e.target.value;})}/></label>
          <label className="ossField"><span>起始阶段</span><select value={item.from_stage_id} onChange={e=>edit(d=>{d.adverse_cases[i].from_stage_id=e.target.value;})}>{input.stages.map((st:Json)=><option key={st.id} value={st.id}>{st.id}</option>)}</select></label>
          {['additional_surcharge_kpa','outside_water_raise_m','inside_water_lower_m'].map((key,j)=><label className="ossField" key={key}><span>{['附加超载 kPa','坑外水位抬升 m','坑内水位下降 m'][j]}</span><input type="number" step="any" value={item[key]??0} onChange={e=>edit(d=>{d.adverse_cases[i][key]=Number(e.target.value);})}/></label>)}
          <label className="ossField"><span>依据</span><input value={item.rationale||''} onChange={e=>edit(d=>{d.adverse_cases[i].rationale=e.target.value;})}/></label>
          <button className="secondary" onClick={()=>edit(d=>{d.adverse_cases.splice(i,1);})}>移除该工况</button>
        </div>)}
        <button className="secondary" onClick={()=>edit(d=>{d.adverse_cases=d.adverse_cases||[];d.adverse_cases.push({name:'ADVERSE_'+(d.adverse_cases.length+1),from_stage_id:d.stages[d.stages.length-1].id,additional_surcharge_kpa:0,outside_water_raise_m:0,inside_water_lower_m:0,rationale:'需要由工程师明确不利荷载来源及组合条件'});})}>增加一个不利工况</button>
        <p>圆桩 N–M、抗剪、锚固和构造的工程数据可通过下方 JSON 方案文件导入；未填写时保持未验证。</p>
        <label>导入工程构件验算 JSON <input type="file" accept=".json" onChange={e=>{void (async()=>{const file=e.target.files?.[0];if(!file)return;try{const content=JSON.parse(await file.text()) as Json;edit(d=>{d.engineering_design=content;});}catch(err){setError('构件参数 JSON 格式有误：'+String(err));} })();e.target.value='';}}/></label>
      </details>
      <h4>施工工况</h4>
      <div className="ossTableScroll"><table><thead><tr><th>阶段</th><th>施工事件</th><th>开挖深度 m</th><th>坑外水位埋深 m</th><th>坑内水位埋深 m</th><th>超载 kPa</th></tr></thead><tbody>{input.stages.map((s: Json,i:number)=><tr key={s.id}><td>{s.id}</td><td>{({initial:'初始',excavate:'开挖',install_strut:'支撑安装',change_water:'水位变化',change_surcharge:'荷载变化'} as Json)[s.event]}</td>{['excavation_depth_m','water_outside_depth_m','water_inside_depth_m','surcharge_kpa'].map(key=><td key={key}><input type="number" step="any" aria-label={`${s.id}${key}`} value={s[key]} onChange={e=>changeValue(`stages.${i}.${key}`,e.target.value)} /></td>)}</tr>)}</tbody></table></div>
      <details><summary>计算系数与给定纵筋</summary><div className="ossGrid">{numberField('gamma0','结构重要性系数','—')}{numberField('cage.bar_count','纵筋根数','根')}{numberField('cage.bar_diameter_mm','纵筋直径','mm')}{numberField('cage.bar_axis_cover_mm','纵筋轴线距边','mm')}{numberField('cage.minimum_ratio','最低配筋率','—')}{numberField('cage.stirrup_diameter_mm','箍筋直径','mm')}{numberField('cage.stirrup_spacing_mm','箍筋间距','mm')}</div></details>
      <details><summary>逐参数依据与确认（已记录 {input.sources.length}/{sourcePaths.length} 项）</summary>
        <p>按勘察报告页码、设计单或材料证明记录原值来源；未确认参数可先做受限计算，不能宣称工程参数齐备。</p>
        <div className="ossTableScroll"><table><thead><tr><th>参数路径</th><th>来源类别</th><th>文档与页码</th><th>状态</th><th>确认人</th></tr></thead><tbody>{sourcePaths.map(path=>{
          const s:Json=input.sources.find((e:Json)=>e.path===path) || {};
          return <tr key={path}><td>{path}</td><td><select aria-label={`${path}来源类别`} value={s.origin||'geotechnical_report'} onChange={e=>setSource(path,'origin',e.target.value)}>
            <option value="geotechnical_report">勘察报告</option><option value="project_design">设计依据</option><option value="survey">测量资料</option><option value="standard_table">材料标准</option><option value="calculation">计算推导</option><option value="example">验证例</option>
          </select></td><td><input aria-label={`${path}来源`} value={s.reference||''} onChange={e=>setSource(path,'reference',e.target.value)} placeholder="文件及页码" /></td><td><select aria-label={`${path}确认状态`} value={s.state||'candidate'} onChange={e=>setSource(path,'state',e.target.value)}>
            <option value="candidate">待确认</option><option value="confirmed">已确认</option><option value="rejected">拒绝</option>
          </select></td><td><input aria-label={`${path}确认人`} value={s.approved_by||''} onChange={e=>setSource(path,'approved_by',e.target.value)} placeholder="签名/工号" /></td></tr>;
        })}</tbody></table></div>
      </details>
      <div className="ossActions"><button className="secondary" onClick={()=>void run(true)} disabled={!!busy}>检查缺项</button><button onClick={()=>void run(false)} disabled={!!busy}>{busy==='calculate'?'求解中…':'冻结并计算'}</button><button className="secondary" onClick={()=>{const json=new Blob([JSON.stringify(input,null,2)],{type:'application/json'});saveBlob(json,'one-strut-input.json');}}>保存输入 JSON</button></div>
      {preflight && <article className="ossIssues"><strong>输入检查：{preflight.schemaValid ? '结构有效' : '需要修正'}</strong><p>工程依据：{preflight.engineeringEvidenceComplete ? '已确认' : '待补齐'}</p>{preflight.issues.map((x,i)=><p key={i}>{x.field}：{x.message}</p>)}</article>}
    </>}
    {input && result && view==='results' && <>
      <div className="ossStatus"><strong>{result.analysisStatus==='complete' ? '数值阶段求解完成' : '计算受阻'}</strong><span>工程状态：仍需复核；正式施工发行：不允许</span></div>
      <div className="ossMetrics"><div><small>最大位移</small><strong>{format(result.checks.displacement?.valueMm)} mm</strong></div><div><small>最大设计弯矩</small><strong>{format(result.reinforcement.momentDesignKnM)} kN·m</strong></div><div><small>设计剪力需求</small><strong>{format(result.reinforcement.shearDesignKn)} kN</strong></div></div>
      <h4>阶段计算及控制</h4><div className="ossTableScroll"><table><thead><tr><th>工况</th><th>事件</th><th>最大位移 mm</th><th>最大弯矩 kN·m</th><th>土体合力上限</th></tr></thead><tbody>{result.stages.map(s=><tr key={s.id}><td>{s.id}</td><td>{s.event}</td><td>{format(s.uMm)}</td><td>{format(s.maxAbsMomentKnM)}</td><td>{s.passiveLimitSatisfied===true?'满足':s.passiveLimitSatisfied===false?'超限':'未计算'}</td></tr>)}</tbody></table></div>
      <h4>规范验算与方法边界</h4>
      {result.normativeCoverage && <div className="ossTableScroll"><table><thead><tr><th>验算</th><th>状态</th><th>控制安全系数</th><th>阶段</th></tr></thead>
      <tbody>{Object.entries(result.normativeCoverage.requiredChecks || {}).map(([code,raw])=>{ const item=raw as Json; const gov=item.governingStage as Json | null;
        return <tr key={code}><td>{code}</td><td>{({pass:'满足本项',fail:'未满足',missing_input:'缺参数依据',not_calculated:'尚未计算',outside_method_scope:'超出方法范围',not_applicable:'本例不适用',calculated_scope_limited:'仅限本方法',review_required:'待工程复核'} as Json)[item.status] || item.status}</td><td>{format(gov?.safetyFactor,3)}</td><td>{gov?.stageId||'—'}</td></tr>;})}</tbody></table></div>}
      {result.historicalReferenceComparison && <p className="ossBanner">原项目同条件复核：{result.historicalReferenceComparison.comparisonStatus}；完整曲线：{result.historicalReferenceComparison.fullCurveComparisonStatus}，不可据峰值推断误差率。</p>}
      {input.section_id==='3-3' && <details><summary>螺屿村 3-3 原工程边界核对（研发数据）</summary>
        <p>原计算书包含冠梁等效刚度与支撑几何，但其分担宽度、桩端边界、对侧围护刚度及原生曲线仍有未确认条件。不得根据峰值接近宣称同条件复现。</p>
        <button className="secondary" disabled={!!busy} onClick={()=>{void (async()=>{try{setBoundaryReport(await request<Json>('/research/luoyu-3-3/boundary-register'));}catch(e){setError(String(e));}})();}}>查看原项目边界证据</button>
        {boundaryReport && <div className="ossIssues"><p>待确认关键项：{boundaryReport.mandatoryUnconfirmed?.length ?? '未计算'}</p>{(boundaryReport.conditions || []).map((e:Json)=><p key={e.field}><strong>{e.field}</strong>：{e.status} {e.note || ''}</p>)}</div>}
      </details>}
      <h4>超限与尚未完成的检查（{result.issues.length}项）</h4><div className="ossIssues">{distinctIssues.map((x,i)=><p key={i}><b>{x.code}{issueCounts[x.code] > 1 ? `（${issueCounts[x.code]}项）` : ''}</b> {x.message}{issueCounts[x.code] > 1 && <small>（完整字段见结果 JSON）</small>}</p>)}</div>
      {result.recommendations.length > 0 && <><h4>可考虑的修改</h4>{result.recommendations.map((x,i)=><p key={i}>{x.text}</p>)}</>}
      <div className="ossTrace">输入 {result.snapshotHash.slice(0,18)} · 结果 {result.resultHash.slice(0,18)}。修改输入后结果和复核记录自动失效。</div>
      <h4>工程师受限计算复核</h4><p>受限复核需具备校审权限的账户操作；只能导出带未覆盖项目的工作版成果，不能代替正式结构、稳定或构造验算。</p>
      <div className="ossGrid"><label className="ossField"><span>复核人</span><input value={reviewer} onChange={e=>setReviewer(e.target.value)} /></label><label className="ossField"><span>复核说明</span><input value={note} onChange={e=>setNote(e.target.value)} placeholder="已检查适用范围，清楚尚缺哪些计算" /></label></div>
      <div className="ossAck">{uniqueIssues.map(code=><label key={code}><input type="checkbox" checked={!!acknowledged[code]} onChange={e=>setAcknowledged(prev=>({...prev,[code]:e.target.checked}))}/>{code}</label>)}</div>
      <div className="ossActions"><button onClick={()=>void approve()} disabled={!!busy || result.analysisStatus!=='complete' || !allAcknowledged || !reviewer.trim() || note.trim().length<8}>确认受限复核</button><button className="secondary" onClick={()=>void exportFiles()} disabled={!!busy || !review}>下载同源工作版成果</button><button className="secondary" onClick={()=>setView('input')}>修改输入</button></div>
      {review && <div className="ossTrace">受限复核记录已保存：{review.reviewHash?.slice(0,20)}；图纸和计算书均保留未完成验算标识。</div>}
      <AgentPilotPanel result={result} projectId={projectId} onAdoptResult={async hash => {
        const candidate=await request<Json>('/results/'+encodeURIComponent(hash));
        generation.current++;
        setInput(clone(candidate.snapshot));setResult(candidate as Calculation);setReview(null);
        setPreflight(candidate.preflight || null);setAcknowledged({});setView('results');
      }} />
      <AssuranceGatePanel result={result} preliminaryReview={review} />
    </>}
  </section>;
}
