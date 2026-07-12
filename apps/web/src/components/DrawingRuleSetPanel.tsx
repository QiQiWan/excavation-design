import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { DrawingRuleCandidate, DrawingRuleSet, DrawingRuleValidation, DrawingSetManifest, Project } from '../types/domain';

interface PresetSummary {
  id: string;
  name: string;
  description?: string;
  parameters: Record<string, any>;
  objectiveWeights: Record<string, number>;
  ruleCount: number;
}

function statusText(validation?: DrawingRuleValidation) {
  if (!validation) return '尚未校验';
  if (!validation.valid) return `${validation.errors.length} 项错误`;
  if (validation.warnings.length) return `有效，${validation.warnings.length} 项提示`;
  return '规则有效';
}

export default function DrawingRuleSetPanel({ project, onApplied }: { project: Project; onApplied?: () => void }) {
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [rules, setRules] = useState<DrawingRuleSet>();
  const [preview, setPreview] = useState<DrawingSetManifest>();
  const [validation, setValidation] = useState<DrawingRuleValidation>();
  const [candidates, setCandidates] = useState<DrawingRuleCandidate[]>([]);
  const [jsonDraft, setJsonDraft] = useState('');
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function load() {
    setError('');
    const [presetResult, ruleResult, plan] = await Promise.all([
      api.listDrawingRulePresets(), api.getDrawingRules(project.id), api.previewDrawingRules(project.id),
    ]);
    setPresets(presetResult.presets);
    setRules(ruleResult.ruleSet);
    setValidation(ruleResult.validation);
    setPreview(plan);
    setJsonDraft(JSON.stringify(ruleResult.ruleSet, null, 2));
  }

  useEffect(() => {
    let active = true;
    load().catch((err) => { if (active) setError(err instanceof Error ? err.message : String(err)); });
    return () => { active = false; };
  }, [project.id, project.updatedAt]);

  const excludedCount = useMemo(() => preview?.decisions?.filter((item) => !item.included).length ?? 0, [preview]);

  function patchParameters(patch: Record<string, unknown>) {
    setRules((current) => {
      if (!current) return current;
      const next = { ...current, parameters: { ...current.parameters, ...patch } };
      setJsonDraft(JSON.stringify(next, null, 2));
      return next;
    });
  }

  function patchModule(moduleName: string, enabled: boolean) {
    setRules((current) => {
      if (!current) return current;
      const modules = { ...(current.modules ?? {}) };
      const previous = (modules[moduleName] ?? {}) as Record<string, unknown>;
      modules[moduleName] = { ...previous, enabled };
      const next = { ...current, modules };
      setJsonDraft(JSON.stringify(next, null, 2));
      return next;
    });
  }

  async function applyPreset(preset: string) {
    try {
      setBusy('preset'); setError(''); setMessage('');
      const result = await api.applyDrawingRulePreset(project.id, preset);
      setRules(result.ruleSet); setPreview(result.preview);
      setValidation({ valid: true, errors: [], warnings: result.warnings });
      setJsonDraft(JSON.stringify(result.ruleSet, null, 2));
      setCandidates([]); setMessage(`已应用“${result.ruleSet.name}”。旧审签会因出图规则变化自动失效。`);
      onApplied?.();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }

  async function validateAndPreview() {
    if (!rules) return;
    try {
      setBusy('validate'); setError(''); setMessage('');
      const result = await api.validateDrawingRules(project.id, rules);
      setValidation(result); setPreview(result.preview);
      setRules(result.normalized); setJsonDraft(JSON.stringify(result.normalized, null, 2));
      setMessage(result.valid ? '规则校验通过，预览已刷新。' : '规则存在错误，未保存。');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }

  async function save() {
    if (!rules) return;
    try {
      setBusy('save'); setError(''); setMessage('');
      const result = await api.updateDrawingRules(project.id, rules);
      setRules(result.ruleSet); setPreview(result.preview); setValidation(result.validation);
      setJsonDraft(JSON.stringify(result.ruleSet, null, 2));
      setMessage('出图规则集已保存，后续 CAD/PDF 导出将使用该版本。');
      onApplied?.();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }

  async function optimize() {
    try {
      setBusy('optimize'); setError(''); setMessage('');
      const result = await api.optimizeDrawingRules(project.id, { ruleSet: rules });
      setCandidates(result.candidates.slice(0, 6));
      setMessage(`已比较 ${result.candidateCount} 个规则方案，推荐方案评分 ${result.candidates[0]?.score ?? '-'}。`);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }

  async function applyCandidate(candidate: DrawingRuleCandidate) {
    try {
      setBusy(candidate.candidateId); setError('');
      const result = await api.applyDrawingRuleCandidate(project.id, candidate.candidateId, undefined, { ruleSet: rules });
      setRules(result.candidate.ruleSet); setPreview(result.preview);
      setValidation({ valid: true, errors: [], warnings: [] });
      setJsonDraft(JSON.stringify(result.candidate.ruleSet, null, 2));
      setCandidates([]); setMessage(`已采用优化方案：${result.candidate.ruleSet.name}。`);
      onApplied?.();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }

  function parseJsonDraft() {
    try {
      const parsed = JSON.parse(jsonDraft) as DrawingRuleSet;
      setRules(parsed); setError(''); setMessage('JSON 已载入草稿，请执行校验。');
    } catch (err) { setError(`JSON 格式错误：${err instanceof Error ? err.message : String(err)}`); }
  }

  if (!rules) return <section className="summaryPanel"><h3>出图规则集</h3><p className="small">正在加载图纸触发、拆图、比例和发行规则。</p>{error ? <div className="error">{error}</div> : null}</section>;
  const params = rules.parameters ?? {};

  return <section className="summaryPanel drawingRulePanel" aria-labelledby="drawing-rule-title">
    <div className="sectionLead">
      <div><h3 id="drawing-rule-title">出图规则集</h3><p className="small">控制图纸选择、动态拆图、比例、节点大样触发和发行条件。图框与图层仍由企业 CAD 模板控制。</p></div>
      <div className={`ruleValidationBadge ${validation?.valid ? 'pass' : 'warn'}`}>{statusText(validation)}</div>
    </div>
    <div className="drawingRulePresetRow" role="group" aria-label="规则集预设">
      {presets.map((preset) => <button key={preset.id} className={rules.preset === preset.id ? 'active' : 'secondary'} disabled={!!busy} onClick={() => applyPreset(preset.id)} title={preset.description}>{preset.name}</button>)}
    </div>
    <div className="drawingRuleModuleRow" role="group" aria-label="图纸模块">
      {Object.entries(rules.modules ?? {}).map(([name, value]) => {
        const module = value as { enabled?: boolean; required?: boolean };
        const labels: Record<string, string> = { general: '总图', rebar: '配筋', details: '节点大样', quality: '质量复核', monitoring: '监测反演' };
        return <label key={name} className={`moduleToggle ${module.required ? 'required' : ''}`}><input type="checkbox" checked={Boolean(module.enabled)} disabled={Boolean(module.required)} onChange={(event) => patchModule(name, event.target.checked)} />{labels[name] ?? name}{module.required ? '（必需）' : ''}</label>;
      })}
    </div>
    <div className="cadTemplateGrid drawingRuleGrid">
      <label>默认图幅<select value={String(params.defaultPaperSize ?? 'A1')} onChange={(event) => patchParameters({ defaultPaperSize: event.target.value })}><option>A0</option><option>A1</option><option>A2</option><option>A3</option></select></label>
      <label>最大图纸数<input type="number" min={10} max={500} value={Number(params.maximumSheetCount ?? 80)} onChange={(event) => patchParameters({ maximumSheetCount: Number(event.target.value) })} /></label>
      <label>每张墙立面幅数<input type="number" min={1} max={12} value={Number(params.wallSheetsPerDrawing ?? 1)} onChange={(event) => patchParameters({ wallSheetsPerDrawing: Number(event.target.value) })} /></label>
      <label>有效图幅比例<input type="number" min={0.55} max={0.95} step={0.01} value={Number(params.usablePaperRatio ?? 0.82)} onChange={(event) => patchParameters({ usablePaperRatio: Number(event.target.value) })} /></label>
      <label className="checkLabel"><input type="checkbox" checked={Boolean(params.includePerWallElevations)} onChange={(event) => patchParameters({ includePerWallElevations: event.target.checked })} />逐墙输出配筋立面</label>
      <label className="checkLabel"><input type="checkbox" checked={Boolean(params.includeEmptyQualitySheets)} onChange={(event) => patchParameters({ includeEmptyQualitySheets: event.target.checked })} />保留空白质量检查图</label>
      <label className="checkLabel"><input type="checkbox" checked={Boolean(params.includeLegacyCompatibilitySheets)} onChange={(event) => patchParameters({ includeLegacyCompatibilitySheets: event.target.checked })} />保留旧版兼容图纸</label>
    </div>
    <div className="maturityGrid drawingRuleStats">
      <div className="statusCard pass"><span>当前图纸</span><strong>{preview?.sheetCount ?? '-'}</strong><em>动态展开后</em></div>
      <div className="statusCard review"><span>未触发规则</span><strong>{excludedCount}</strong><em>条件不满足或范围不匹配</em></div>
      <div className="statusCard review"><span>规则版本</span><strong>{rules.version}</strong><em>{rules.ruleSetHash ?? '未保存哈希'}</em></div>
      <div className={`statusCard ${(preview?.overflowSheets?.length ?? 0) ? 'warn' : 'pass'}`}><span>超限裁剪</span><strong>{preview?.overflowSheets?.length ?? 0}</strong><em>超过最大图纸数</em></div>
    </div>
    {preview?.drawingIntelligence && <div className="drawingIntelligencePanel" aria-label="智能出图建议">
      <div className="intelligenceHeader">
        <div><h4>智能出图建议</h4><p className="small">结合基坑几何、支撑拓扑、计算诊断和当前图纸覆盖生成。</p></div>
        <div className="drawingQualityScore"><span>图纸质量</span><strong>{preview.drawingIntelligence.quality?.overall ?? '-'}</strong><em>{preview.drawingIntelligence.quality?.grade ?? '-'}</em></div>
      </div>
      <div className="intelligenceRecommendationList">
        {(preview.drawingIntelligence.recommendations ?? []).slice(0, 4).map((item) => <article key={item.id} className={`intelligenceRecommendation ${item.priority}`}>
          <strong>{item.title}{item.satisfied ? ' · 已覆盖' : ''}</strong>
          <p>{item.reason}</p><em>{item.action}</em>
        </article>)}
      </div>
    </div>}
    <div className="actionStrip simplifiedActions">
      <button disabled={!!busy} onClick={validateAndPreview}>{busy === 'validate' ? '校验中…' : '校验并预览'}</button>
      <button disabled={!!busy} onClick={save}>保存规则集</button>
      <button className="secondary" disabled={!!busy} onClick={optimize}>{busy === 'optimize' ? '优化中…' : '自动优化规则'}</button>
      <span className="small">{message || '修改规则后会改变设计快照，原正式审签与施工版修订需要重新确认。'}</span>
    </div>
    {error ? <div className="error" role="alert">{error}</div> : null}
    {validation?.errors.length ? <ul className="ruleMessageList fail">{validation.errors.map((item) => <li key={`${item.path}-${item.message}`}><strong>{item.path}</strong>：{item.message}</li>)}</ul> : null}
    {validation?.warnings.length ? <ul className="ruleMessageList warn">{validation.warnings.map((item) => <li key={`${item.path}-${item.message}`}><strong>{item.path}</strong>：{item.message}</li>)}</ul> : null}
    {candidates.length ? <div className="drawingRuleCandidates"><h4>规则优化候选</h4><div className="candidateGrid">{candidates.map((candidate) => <article key={candidate.candidateId} className="candidateCard"><div><strong>#{candidate.rank} {candidate.ruleSetMeta?.name ?? candidate.label ?? candidate.preset}</strong><span>{candidate.paperSize} · 每图{candidate.wallSheetsPerDrawing ?? 1}幅墙 · {candidate.sheetCount} 张</span></div><b>{candidate.score}</b><p>覆盖 {candidate.metrics.coverage}% · 可读性 {candidate.metrics.readability}% · 施工深化 {candidate.metrics.constructability}% · 紧凑性 {candidate.metrics.compactness}%</p><button disabled={!!busy} onClick={() => applyCandidate(candidate)}>{busy === candidate.candidateId ? '应用中…' : '采用该方案'}</button></article>)}</div></div> : null}
    <details className="drawingRulePreview"><summary>图纸计划预览（{preview?.sheetCount ?? 0} 张）</summary><table className="table compactTable"><thead><tr><th>图号</th><th>图名</th><th>类别</th><th>比例</th><th>生成器</th></tr></thead><tbody>{(preview?.sheets ?? []).slice(0, 30).map((sheet) => <tr key={`${sheet.sheetNo}-${sheet.file}`}><td>{sheet.sheetNo}</td><td>{sheet.title}</td><td>{sheet.category}</td><td>{sheet.scale}</td><td>{sheet.renderer ?? '-'}</td></tr>)}</tbody></table></details>
    <details className="drawingRuleJson"><summary>高级 JSON 配置</summary><p className="small">条件仅支持安全 DSL，不执行任意代码。修改后先“载入草稿”，再校验和保存。</p><textarea aria-label="出图规则 JSON" value={jsonDraft} onChange={(event) => setJsonDraft(event.target.value)} rows={18} spellCheck={false} /><button className="secondary" onClick={parseJsonDraft}>载入 JSON 草稿</button></details>
  </section>;
}
