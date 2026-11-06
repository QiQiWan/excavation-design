import { useState } from 'react';
import { api } from '../api/client';
import type { ImportResult, PitTask, Project } from '../types/domain';
import { waitForTaskWithHealth } from '../utils/taskPolling';

export default function BoreholeImport({ project, onImported }: { project: Project; onImported: () => void | Promise<void> }) {
  const [result, setResult] = useState<ImportResult | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [task, setTask] = useState<PitTask | undefined>();
  const [busy, setBusy] = useState(false);

  async function handleFile(file?: File) {
    if (!file || busy) return;
    setBusy(true);
    setResult(undefined);
    setError(undefined);
    try {
      const created = await api.importBoreholesTask(project.id, file);
      setTask(created);
      const finished = await waitForTaskWithHealth(created, setTask, { timeoutMs: 20 * 60 * 1000 });
      if (finished.status !== 'success') throw new Error(finished.error || `地勘解析未完成：${finished.status}`);
      const imported = finished.result?.importResult as ImportResult | undefined;
      if (!imported) throw new Error('地勘任务已结束，但没有返回解析结果。请查看 worker.log。');
      setResult(imported);
      if (imported.success) await onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!task || !['queued', 'running'].includes(task.status)) return;
    try {
      setTask(await api.cancelTask(task.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const taskActive = Boolean(task && ['queued', 'running'].includes(task.status));

  return (
    <div className="card">
      <h3>地质钻孔导入</h3>
      <p className="muted">支持 CSV、XLSX 和 XLSM。上传后由独立 worker 解析，页面保持可响应；旧地质模型和旧计算证据会按工程版本规则失效。</p>
      <input aria-label="地质钻孔文件" type="file" accept=".csv,.xlsx,.xlsm" disabled={busy} onChange={(event) => void handleFile(event.target.files?.[0])} />
      {task ? <div className="importTaskProgress" aria-live="polite">
        <div><strong>{task.currentStep || task.title}</strong><span>{Math.max(0, Math.min(100, Number(task.progress || 0)))}%</span></div>
        <progress max={100} value={Math.max(0, Math.min(100, Number(task.progress || 0)))} />
        {taskActive ? <button type="button" className="secondary compactButton" onClick={() => void cancel()}>取消导入</button> : null}
      </div> : null}
      {error && <div className="error">{error}</div>}
      {result && (
        <div className={result.success ? 'successNotice' : 'error'}>
          <p>{result.success ? '导入完成' : '数据校验未通过'}：钻孔 {result.boreholeCount}；分层 {result.layerCount}；地层 {result.stratumCount}</p>
          {result.errors.map((item) => <div key={item}>{item}</div>)}
          {result.warnings.map((item) => <div key={item} className="warning">{item}</div>)}
        </div>
      )}
      <h4>钻孔表</h4>
      <table className="table">
        <thead><tr><th>编号</th><th>x</th><th>y</th><th>孔深</th><th>层数</th></tr></thead>
        <tbody>
          {project.boreholes.map((bh) => <tr key={bh.id}><td>{bh.code}</td><td>{bh.x}</td><td>{bh.y}</td><td>{bh.depth}</td><td>{bh.layers.length}</td></tr>)}
          {project.boreholes.length === 0 && <tr><td colSpan={5}>未导入钻孔</td></tr>}
        </tbody>
      </table>
      <h4>地层参数</h4>
      <table className="table">
        <thead><tr><th>编号</th><th>名称</th><th>γ</th><th>c</th><th>φ</th><th>E</th></tr></thead>
        <tbody>
          {project.strata.map((s) => <tr key={s.id}><td>{s.code}</td><td>{s.name}</td><td>{s.parameters.unitWeight ?? '-'}</td><td>{s.parameters.cohesion ?? '-'}</td><td>{s.parameters.frictionAngle ?? '-'}</td><td>{s.parameters.elasticModulus ?? '-'}</td></tr>)}
          {project.strata.length === 0 && <tr><td colSpan={6}>未导入地层</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
