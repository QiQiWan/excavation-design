import type { Project } from '../types/domain';
import Engineering3DViewer from './Engineering3DViewer';

export default function GeologyViewer({ project }: { project: Project }) {
  const surfaces = project.geologicalModel?.surfaces ?? [];
  const mesh = project.geologicalModel?.vtuMesh;
  return (
    <div>
      <Engineering3DViewer project={project} focus="geology" />
      <div className="card">
        <h3>地质模型与 VTU 摘要</h3>
        <table className="table">
          <thead><tr><th>地层</th><th>界面</th><th>网格</th><th>置信度</th></tr></thead>
          <tbody>
            {surfaces.map((surface, index) => <tr key={`${surface.stratumCode}-${surface.surfaceType}-${index}`}><td>{surface.stratumCode}</td><td>{surface.surfaceType}</td><td>{surface.grid.xValues.length} x {surface.grid.yValues.length}</td><td>{surface.confidence}</td></tr>)}
            {surfaces.length === 0 && <tr><td colSpan={4}>尚未生成地质模型</td></tr>}
          </tbody>
        </table>
        <h4>VTU 网格</h4>
        {mesh ? (
          <div>
            <p className="small">点：{mesh.summary?.pointCount ?? mesh.points?.length ?? 0}；单元：{mesh.summary?.cellCount ?? mesh.cellBlocks?.length ?? 0}；类型：{mesh.summary?.cellTypes?.join(', ') || '-'}</p>
            <p className="small">识别字段：{mesh.detectedFields?.join(', ') || '-'}</p>
            <p className="small">建议映射：{JSON.stringify(mesh.suggestedMapping ?? {})}</p>
            {mesh.warnings?.map((item) => <div key={item} className="warning">{item}</div>)}
          </div>
        ) : <p className="small">未导入 VTU。</p>}
        {project.geologicalModel?.warnings.map((item) => <div key={item} className="warning">{item}</div>)}
      </div>
    </div>
  );
}
