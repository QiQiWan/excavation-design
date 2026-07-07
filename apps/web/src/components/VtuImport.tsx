import { useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../types/domain';

export default function VtuImport({ project, onImported }: { project: Project; onImported: () => void }) {
  const [message, setMessage] = useState<string | undefined>();
  const [error, setError] = useState<string | undefined>();
  async function handle(file?: File) {
    if (!file) return;
    try {
      setError(undefined);
      const mesh = await api.importVtu(project.id, file);
      setMessage(`VTU 导入完成：${mesh?.summary?.pointCount ?? mesh?.points?.length ?? 0} 点，${mesh?.summary?.cellCount ?? mesh?.cellBlocks?.length ?? 0} 单元。`);
      onImported();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="card">
      <h3>VTU 网格导入</h3>
      <input aria-label="VTU 网格" type="file" accept=".vtu,.xml" onChange={(event) => handle(event.target.files?.[0])} />
      {message && <div className="warning">{message}</div>}
      {error && <div className="error">{error}</div>}
      <p className="small">支持 ASCII XML 和 inline base64 binary DataArray；安装 meshio 后可增强解析 appended/binary/压缩 VTU。</p>
    </div>
  );
}
