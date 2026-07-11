export default function DocsPage() {
  return (
    <main className="page docsPage">
      <section className="card docsHero">
        <h2>PitGuard 操作文档</h2>
        <p>按项目设计流程使用：先准备地勘资料，再建立地质模型和基坑轮廓，随后生成围护体系、支撑方案、计算校核和交付成果。</p>
      </section>
      <section className="stepGrid docsGrid">
        <div className="summaryPanel"><h3>1. 项目与地勘</h3><p>新建项目后导入钻孔 CSV。确认钻孔数量、地层参数和地下水位，再生成三维地质模型。</p></div>
        <div className="summaryPanel"><h3>2. 基坑轮廓</h3><p>使用 CAD-like 编辑器绘制或输入基坑轮廓、出土口、坡道和保护区。完成后生成围护墙、冠梁、围檩、支撑和立柱。</p></div>
        <div className="summaryPanel"><h3>3. 支撑优化</h3><p>通过候选方案比选查看支撑数量、立柱数量、跨长、出土通道避让和 A/B/C 完整计算结果。必要时锁定支撑、端点、支撑层或通道边界。</p></div>
        <div className="summaryPanel"><h3>4. 计算校核</h3><p>执行一键计算校核，查看内力、位移、稳定性、规范条文对比和计算追溯链。不合规项会进入问题清单。</p></div>
        <div className="summaryPanel"><h3>5. 问题定位</h3><p>在问题清单中点击问题，系统会定位到流程步骤、二维平面、三维构件、钢筋视图、内力图或 CAD 图纸。</p></div>
        <div className="summaryPanel"><h3>6. 成果导出</h3><p>常用成果包括 IFC 可视化模型、CAD 图纸包、DOCX 计算书和钢筋详图数据。更多成果在“更多导出”中展开。</p></div>
      </section>
      <section className="summaryPanel">
        <h3>状态含义</h3>
        <table className="table compactTable"><thead><tr><th>状态</th><th>含义</th><th>处理方式</th></tr></thead><tbody>
          <tr><td>合规</td><td>内置规范子集未发现超限。</td><td>保留计算追溯，进入成果整理。</td></tr>
          <tr><td>预警</td><td>接近限值或缺少完整专项参数。</td><td>复核参数、构造和项目控制指标。</td></tr>
          <tr><td>不合规</td><td>计算值超过限值或存在阻断项。</td><td>调整方案后重新计算。</td></tr>
          <tr><td>需复核</td><td>软件已生成数据，但需要工程师确认。</td><td>按企业审查流程复核。</td></tr>
        </tbody></table>
      </section>
      <div className="toolbar"><a className="buttonLink" href="/">返回项目列表</a></div>
    </main>
  );
}
