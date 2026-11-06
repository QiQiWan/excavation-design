import type { FrozenSectionInput } from '../api/client';

type Row = Record<string, unknown>;
export function asObject(value: unknown): Row { return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}; }
export function asRows(value: unknown): Row[] { return Array.isArray(value) ? value.map(asObject) : []; }
export function display(value: unknown): string { return value == null ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
export function statusLabel(value: unknown): string {
  const labels: Record<string,string> = { pass:'通过', passed:'通过', fail:'失败', failed:'失败', missing_input:'缺项', missing_inputs:'缺项', incomplete:'条件未齐', outside_method_scope:'超范围', outside_scope:'超范围', out_of_scope:'超范围', calculated:'已计算，待判定', not_calculated:'未计算', not_selected:'未选择', within_tolerance:'数值在容差内', outside_tolerance:'数值超出容差', conditions_mismatch:'条件不一致', ready:'条件已齐，待比较' };
  return typeof value === 'string' ? labels[value] ?? value : '未计算';
}
const historyLabels: Record<string,string> = { carry_plastic_history_v1:'继承塑性历史', reset_plastic_offset_to_zero_v1:'塑性偏移清零', rebase_soil_at_current_displacement_v1:'以当前位移重建土体参照' };
export function MethodReview({ input }: { input: FrozenSectionInput }) {
  if (input.schema_version !== 'pitguard.frozen-section/2') return null;
  const methods=asObject(input.method_extensions);
  return <details className="frozenSectionMethodReview"><summary>v2 方法与阶段假设</summary>
    <p className="frozenSectionFine">方法假设随输入冻结。有限邻载按所给位置计算，坡台主动合力核验尚未耦合为完整支护模型。</p>
    <div className="frozenSectionTableScroll"><table aria-label="v2 土层应力口径"><thead><tr><th>土层</th><th>水土 / 参数口径</th><th>采用依据</th></tr></thead><tbody>{asRows(methods.layer_stress_modes).map((r,i)=><tr key={i}><th>{display(r.layer_id)}</th><td>{r.mode==='effective_separated'?'水土分算 / 有效应力':r.mode==='total_combined'?'水土合算 / 总应力':display(r.mode)}</td><td>{display(r.basis)}</td></tr>)}</tbody></table></div>
    <div className="frozenSectionTableScroll"><table aria-label="v2 有限邻载"><thead><tr><th>荷载</th><th>压力 kPa</th><th>近 / 远边距 m</th><th>沿墙起止 m</th><th>位置依据</th></tr></thead><tbody>{asRows(methods.surface_loads).map((r,i)=><tr key={i}><th>{display(r.id)} · {r.kind==='rectangle'?'矩形':'条带'}</th><td>{display(r.q_kpa)}</td><td>{display(r.near_m)} / {display(r.far_m)}</td><td>{r.kind==='strip'?'无限长':`${display(r.along_start_m)} / ${display(r.along_end_m)}`}</td><td>{display(r.basis)}</td></tr>)}</tbody></table></div>
    <div className="frozenSectionTableScroll"><table aria-label="v2 阶段历史"><thead><tr><th>阶段</th><th>启用邻载</th><th>历史规则</th><th>采用依据</th></tr></thead><tbody>{asRows(methods.stage_options).map((r,i)=><tr key={i}><th>{display(r.stage_id)}</th><td>{Array.isArray(r.active_load_ids)?r.active_load_ids.join('、') || '无': '未提供'}</td><td>{historyLabels[display(r.soil_history_action)] ?? display(r.soil_history_action)}</td><td>{display(r.history_basis)}</td></tr>)}</tbody></table></div>
  </details>;
}
const checkLabels:Record<string,string>={ axial_flexural_interaction:'轴力–弯矩相互作用',shear:'抗剪',minimum_cover:'保护层',bar_spacing:'钢筋净距',stirrup_detailing:'箍筋构造',anchorage:'锚固',anchorage_confinement:'锚固约束',lap:'搭接',service_stress:'使用应力',crack_width:'裂缝宽度' };
export function EngineeringChecks({ value }: { value: unknown }) {
  const checks=asRows(asObject(value).checks);
  if (!checks.length) return null;
  return <section aria-label="工程检查明细"><h4>工程逐项检查</h4><p className="frozenSectionFine">各项按自身范围判定；纯弯或单项通过不代表整体通过。“已计算”表示仍需判据或复核。</p>
    <div className="frozenSectionTableScroll"><table aria-label="工程逐项检查"><thead><tr><th>检查项目</th><th>需求 / 判据</th><th>容量 / 实配</th><th>状态</th><th>范围与缺项</th></tr></thead><tbody>{checks.map((r,i)=><tr key={i}><th scope="row">{checkLabels[display(r.id)] ?? display(r.id)}</th><td>{display(r.demand)} {r.demand==null?'':display(r.units)}</td><td>{display(r.capacity)} {r.capacity==null?'':display(r.units)}</td><td><span className={`frozenCheckStatus ${r.status==='fail'?'failed':''}`}>{statusLabel(r.status)}</span></td><td>{r.scope ? <div>{display(r.scope)}</div>:null}{Array.isArray(r.requiredInputs)&&r.requiredInputs.length ? <div>待补：{r.requiredInputs.map(display).join('；')}</div>:null}{r.reason ? <div>{display(r.reason)}</div>:null}{Array.isArray(r.limitations)&&r.limitations.length ? <div>{r.limitations.map(display).join('；')}</div>:null}{r.details ? <details><summary>计算明细</summary><pre>{JSON.stringify(r.details,null,2)}</pre></details>:null}</td></tr>)}</tbody></table></div>
  </section>;
}
