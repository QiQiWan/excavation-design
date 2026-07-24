import { useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { api } from '../api/client';
import type { CalculationCase, ConstructionStage, ConstructionStageType, ConstructionStageWorkspace, Project } from '../types/domain';

const stageTypeLabels: Record<ConstructionStageType, string> = {
  excavation: '分步开挖',
  support_installation: '开挖并安装支撑',
  bottom_slab: '底板/楼板形成',
  replacement: '换撑',
  support_removal: '拆撑',
  final: '最终开挖与使用校核',
};

function copyCase(value?: CalculationCase): CalculationCase | undefined {
  return value ? JSON.parse(JSON.stringify(value)) as CalculationCase : undefined;
}

function finite(value: string, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function stageId(): string {
  return `stage-user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function ConstructionStageEditor({ project, onChanged }: { project: Project; onChanged?: () => void | Promise<void> }) {
  const [workspace, setWorkspace] = useState<ConstructionStageWorkspace>();
  const [draft, setDraft] = useState<CalculationCase>();
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  async function reload() {
    try {
      const value = await api.getConstructionStages(project.id);
      setWorkspace(value);
      setDraft(copyCase(value.case));
      setDirty(false);
      setError(undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  useEffect(() => { void reload(); }, [project.id, project.updatedAt]);

  const supportOptions = workspace?.supportOptions ?? [];
  const supportLabel = useMemo(() => new Map(supportOptions.map((item) => [item.id, `${item.code} · L${item.levelIndex} · EL ${item.elevation.toFixed(2)}m`])), [supportOptions]);

  function updateCase(patch: Partial<CalculationCase>) {
    setDraft((current) => current ? { ...current, ...patch } : current);
    setDirty(true); setNotice(undefined);
  }

  function updateStage(index: number, patch: Partial<ConstructionStage>) {
    if (!draft) return;
    const stages = draft.stages.map((stage, current) => current === index ? { ...stage, ...patch } : stage);
    updateCase({ stages });
  }

  function selectedValues(event: ChangeEvent<HTMLSelectElement>): string[] {
    return Array.from(event.currentTarget.selectedOptions).map((option) => option.value);
  }

  function addStage() {
    if (!draft) return;
    const previous = draft.stages[draft.stages.length - 1];
    const bottom = project.excavation?.bottomElevation ?? previous?.excavationElevation ?? 0;
    const next: ConstructionStage = {
      id: stageId(),
      name: `新增阶段 ${draft.stages.length + 1}`,
      excavationElevation: Math.max(bottom, previous?.excavationElevation ?? bottom),
      activeSupportIds: [...(previous?.activeSupportIds ?? [])],
      deactivatedSupportIds: [],
      activeSupportLevels: [...(previous?.activeSupportLevels ?? [])],
      transferredSupportLevels: [],
      stageType: 'excavation',
      zone: `Z${draft.stages.length + 1}`,
      groundwaterLevelInside: previous?.groundwaterLevelInside ?? project.designSettings.groundwaterLevelInside ?? project.designSettings.groundwaterLevel,
      groundwaterLevelOutside: previous?.groundwaterLevelOutside ?? project.designSettings.groundwaterLevel,
      surcharge: previous?.surcharge ?? project.designSettings.surcharge ?? 0,
    };
    updateCase({ stages: [...draft.stages, next] });
  }

  function moveStage(index: number, offset: number) {
    if (!draft) return;
    const target = index + offset;
    if (target < 0 || target >= draft.stages.length) return;
    const stages = [...draft.stages];
    [stages[index], stages[target]] = [stages[target], stages[index]];
    updateCase({ stages });
  }

  function removeStage(index: number) {
    if (!draft || draft.stages.length <= 1) return;
    updateCase({ stages: draft.stages.filter((_stage, current) => current !== index) });
  }

  async function save() {
    if (!draft) return;
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      const value = await api.saveConstructionStages(project.id, draft);
      setWorkspace(value); setDraft(copyCase(value.case)); setDirty(false);
      setNotice('设计控制工况已保存；原计算结果已失效，请重新运行当前方案。');
      await onChanged?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setSaving(false); }
  }

  async function reset() {
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      const value = await api.resetConstructionStages(project.id);
      setWorkspace(value); setDraft(copyCase(value.case)); setDirty(false);
      setNotice('已恢复推荐设计控制工况；原计算结果已失效，请重新计算。');
      await onChanged?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setSaving(false); }
  }

  if (!workspace || !draft) return <section id="construction-stage-editor" className="constructionStageEditor loading"><strong>正在读取设计控制工况与计算证据…</strong>{error ? <span>{error}</span> : null}</section>;
  const issues = workspace.validation?.issues ?? [];
  const sourceLabel = draft.source === 'user_defined' ? '项目自定义并锁定' : '系统推荐阶段';

  return <section id="construction-stage-editor" className={`constructionStageEditor ${workspace.summary.validationStatus}`}>
    <header>
      <div><span className="sectionKicker">设计控制工况</span><h4>设计成立所需的开挖边界、支撑激活与换撑条件</h4><p>这里记录设计计算采用的控制边界，不填写实际施工日期、现场实测轴力或阶段验收。逐工况内力和位移由计算生成。</p></div>
      <div className="constructionStageStatus"><strong>{workspace.summary.stageCount} 个阶段</strong><span>{sourceLabel}</span><em>{workspace.summary.failCount} 错误 · {workspace.summary.warningCount} 复核</em></div>
    </header>
    <div className="constructionStageToolbar">
      <button type="button" className="secondary" onClick={() => setExpanded((value) => !value)}>{expanded ? '收起阶段编辑' : '展开并检查阶段'}</button>
      <button type="button" className="secondary" disabled={saving} onClick={() => void reset()}>恢复推荐工况</button>
      <button type="button" disabled={saving || !dirty} onClick={() => void save()}>{saving ? '保存中…' : '保存并锁定项目阶段'}</button>
      {dirty ? <span>有未保存修改；保存后必须重新计算。</span> : null}
    </div>
    {notice ? <div className="rebarGateMessage pass">{notice}</div> : null}
    {error ? <div className="rebarGateMessage fail">{error}</div> : null}
    {issues.length ? <><h4>这些资料在哪里补齐</h4><details className="constructionStageIssues" open={issues.some((item) => item.severity === 'fail')}><summary>阶段校验：{issues.length} 项</summary>{issues.map((item, index) => <article key={`${item.code}-${item.stageId ?? index}`} className={item.severity}><strong>{item.code}</strong><span>{item.message}</span><em>{item.action}</em></article>)}</details></> : <div className="rebarGateMessage pass">设计控制工况标高、构件引用和最终开挖覆盖有效。</div>}

    {expanded ? <div className="constructionStageList">
      <label className="constructionCaseName">设计工况组名称<input value={draft.name} onChange={(event) => updateCase({ name: event.target.value })} /></label>
      {draft.stages.map((stage, index) => <details key={stage.id} className="constructionStageCard" open={index === 0}>
        <summary><span><b>{index + 1}</b><strong>{stage.name}</strong><em>{stageTypeLabels[stage.stageType]} · EL {stage.excavationElevation.toFixed(2)}m</em></span><small>激活 {stage.activeSupportIds.length} · 退出 {stage.deactivatedSupportIds.length}</small></summary>
        <div className="constructionStageFields">
          <label>阶段名称<input value={stage.name} onChange={(event) => updateStage(index, { name: event.target.value })} /></label>
          <label>阶段类型<select value={stage.stageType} onChange={(event) => updateStage(index, { stageType: event.target.value as ConstructionStageType })}>{Object.entries(stageTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>开挖标高（m）<input type="number" step="0.1" value={stage.excavationElevation} onChange={(event) => updateStage(index, { excavationElevation: finite(event.target.value, stage.excavationElevation) })} /></label>
          <label>设计分区<input value={stage.zone ?? ''} onChange={(event) => updateStage(index, { zone: event.target.value })} /></label>
          <label>坑外水位（m）<input type="number" step="0.1" value={stage.groundwaterLevelOutside ?? ''} onChange={(event) => updateStage(index, { groundwaterLevelOutside: event.target.value === '' ? undefined : finite(event.target.value) })} /></label>
          <label>坑内水位（m）<input type="number" step="0.1" value={stage.groundwaterLevelInside ?? ''} onChange={(event) => updateStage(index, { groundwaterLevelInside: event.target.value === '' ? undefined : finite(event.target.value) })} /></label>
          <label>设计超载上限（kPa）<input type="number" min="0" step="1" value={stage.surcharge} onChange={(event) => updateStage(index, { surcharge: finite(event.target.value, stage.surcharge) })} /></label>
          <label className="stageSupportSelector">已激活支撑<select multiple size={Math.min(7, Math.max(3, supportOptions.length))} value={stage.activeSupportIds} onChange={(event) => updateStage(index, { activeSupportIds: selectedValues(event) })}>{supportOptions.map((item) => <option key={item.id} value={item.id}>{supportLabel.get(item.id)}</option>)}</select><small>Ctrl/Cmd 可多选；应包含此前仍在工作的支撑。</small></label>
          {(stage.stageType === 'replacement' || stage.stageType === 'support_removal') ? <label className="stageSupportSelector">本阶段退出支撑<select multiple size={Math.min(7, Math.max(3, supportOptions.length))} value={stage.deactivatedSupportIds} onChange={(event) => updateStage(index, { deactivatedSupportIds: selectedValues(event) })}>{supportOptions.map((item) => <option key={item.id} value={item.id}>{supportLabel.get(item.id)}</option>)}</select></label> : null}
          {(stage.stageType === 'replacement' || stage.stageType === 'support_removal' || stage.stageType === 'bottom_slab') ? <label className="replacementAction">换撑/拆撑生效条件<textarea value={stage.replacementAction ?? ''} onChange={(event) => updateStage(index, { replacementAction: event.target.value })} placeholder="如：B2 楼板达到 100% 设计强度，连接验收合格并完成轴力转换后拆除 L3" /></label> : null}
        </div>
        <footer><button type="button" className="secondary" disabled={index === 0} onClick={() => moveStage(index, -1)}>上移</button><button type="button" className="secondary" disabled={index === draft.stages.length - 1} onClick={() => moveStage(index, 1)}>下移</button><button type="button" className="secondary danger" disabled={draft.stages.length <= 1} onClick={() => removeStage(index)}>删除阶段</button></footer>
      </details>)}
      <button type="button" className="secondary addConstructionStage" onClick={addStage}>新增设计控制工况</button>
    </div> : null}

    <details className="constructionStageInputGuide"><summary>控制参数来源与责任边界</summary><div>{workspace.inputGuide.map((item) => <article key={item.field}><strong>{item.label}</strong><span>{item.location}</span><p>{item.action}</p><footer>{item.provider} · {item.designStageAvailable ? '设计阶段可提供' : '需专项/施工资料'}</footer></article>)}</div></details>
  </section>;
}
