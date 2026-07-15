import { useState } from 'react';
import { api } from '../api/client';
import type { ImportResult, Project } from '../types/domain';

export default function BoreholeImport({ project, onImported }: { project: Project; onImported: () => void }) {
  const [result, setResult] = useState<ImportResult | undefined>();
  const [error, setError] = useState<string | undefined>();

  async function handleFile(file?: File) {
    if (!file) return;
    try {
      setError(undefined);
      const imported = await api.importBoreholes(project.id, file);
      setResult(imported);
      if (imported.success) onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="card">
      <h3>钻孔 CSV 导入</h3>
      <input aria-label="钻孔 CSV" type="file" accept=".csv" onChange={(event) => handleFile(event.target.files?.[0])} />
      {error && <div className="error">{error}</div>}
      {result && (
        <div>
          <p>success={String(result.success)}；钻孔 {result.boreholeCount}；层数 {result.layerCount}；地层 {result.stratumCount}</p>
          {result.errors.map((item) => <div key={item} className="error">{item}</div>)}
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
