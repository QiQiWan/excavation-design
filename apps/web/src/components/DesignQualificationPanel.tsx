import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Project } from '../types/domain';

type Gate = {
  code: string;
  title: string;
  status: string;
  message: string;
  blocks?: string[];
  evidence?: Record<string, any>;
  recommendedAction?: string;
};

type SystemOption = {
  id: string;
  family: string;
  title: string;
  priority: number;
  recommended?: boolean;
  generationMode: string;
  candidateReadiness: string;
  automaticGenerationAvailable?: boolean;
  prerequisites?: string[];
  hardBoundaries?: string[];
  nextAction?: string;
};

type Qualification = {
  status: string;
  interactionMode: 'normal' | 'degraded' | 'diagnostic';
  workspaceProfileRequired?: boolean;
  workspaceHealthy?: boolean;
  compactionRecommended?: boolean;
  candidateGenerationAllowed?: boolean;
  calculationAllowed?: boolean;
  formalIssueAllowed?: boolean;
  gates: Gate[];
  systemOptions?: {
    shapeClassification?: string;
    shapeArchetype?: string;
    recognitionConfidence?: number;
    controlledBlock?: boolean;
    options?: SystemOption[];
    decisionBoundary?: string;
  };
  nextActions?: { priority: number; gateCode: string; title: string; action: string }[];
};


const generationModeText: Record<string, string> = {
  automatic: '自动生成',
  automatic_subject_to_full_check: '自动生成·完整复核',
  preliminary: '初步生成·转接复核',
  system_selection_required: '需定义结构体系',
  manual_model_required: '需专项计算模型',
};

const readinessText: Record<string, string> = {
  candidate_generation_ready: '可生成候选',
  diagnostic_only: '仅诊断',
  system_definition_required: '待定义模型',
};

const blockText: Record<string, string> = {
  interactive_full_load: 'API 全量加载',
  candidate_generation: '候选生成',
  calculation: '完整计算',
  detailing_release: '深化发行',
  formal_issue: '正式发行',
};
const statusText: Record<string, string> = {
  pass: '通过',
  warning: '预警',
  manual_review: '需复核',
  fail: '阻断',
  blocked: '阻断',
};

export default function DesignQualificationPanel({
  project,
  runTask,
}: {
  project: Project;
  runTask: (title: string, operation: 'storage_compaction' | 'support_layout_optimization', payload?: Record<string, unknown>) => Promise<void>;
}) {
  const [data, setData] = useState<Qualification | null>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    let alive = true;
    setError(undefined);
    api.getDesignQualification(project.id)
      .then((value) => { if (alive) setData(value as Qualification); })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [project.id, project.updatedAt, project.retainingSystem?.supportLayoutRepair?.selectedCandidateId]);

  if (error) return <section className="designQualificationPanel errorPanel"><strong>设计资格读取失败</strong><p>{error}</p></section>;
  if (!data) return <section className="designQualificationPanel loadingPanel"><strong>正在建立设计资格矩阵…</strong></section>;

  const options = data.systemOptions?.options ?? [];
  const coordinateGate = data.gates.find((item) => item.code === 'Q-COORD-GEO');
  const coordinateEvidence = coordinateGate?.evidence?.coordinateAlignment ?? {};
  const storageGate = data.gates.find((item) => item.code === 'Q-STORAGE');
  const modeTitle = data.interactionMode === 'diagnostic' ? '诊断与体系选择模式' : data.interactionMode === 'degraded' ? '受限工作区模式' : '标准设计模式';

  return <section className={`designQualificationPanel mode-${data.interactionMode}`}>
    <div className="designQualificationHeader">
      <div>
        <span className="sectionKicker">设计资格总控</span>
        <h3>{modeTitle}</h3>
        <p>系统先判定数据、几何、坐标、地质、支撑体系和计算证据资格，再开放候选计算与正式交付。</p>
      </div>
      <div className="qualificationPermissionRow">
        <span className={data.candidateGenerationAllowed ? 'permission pass' : 'permission blocked'}>候选 {data.candidateGenerationAllowed ? '允许' : '阻断'}</span>
        <span className={data.calculationAllowed ? 'permission pass' : 'permission blocked'}>计算 {data.calculationAllowed ? '允许' : '阻断'}</span>
        <span className={data.formalIssueAllowed ? 'permission pass' : 'permission blocked'}>发行 {data.formalIssueAllowed ? '允许' : '阻断'}</span>
      </div>
    </div>

    <div className="qualificationGateGrid">
      {data.gates.map((gate) => <article key={gate.code} className={`qualificationGate gate-${gate.status}`}>
        <div><strong>{gate.title}</strong><span>{statusText[gate.status] ?? gate.status}</span></div>
        <p>{gate.message}</p>
        {gate.blocks?.length ? <small>阻断：{gate.blocks.map((item) => blockText[item] ?? item).join('、')}</small> : <small>当前未阻断后续流程</small>}
      </article>)}
    </div>

    {data.workspaceProfileRequired && <div className={`qualificationActionBanner ${data.workspaceHealthy ? 'info' : 'warning'}`}>
      <div><strong>工作区优先模式</strong><span>{storageGate?.message} {storageGate?.recommendedAction}</span></div>
      {data.compactionRecommended ? <button type="button" onClick={() => void runTask('正在压缩项目存储并重建工作区', 'storage_compaction', { includeRevisions: false })}>优化项目存储</button> : <span className="statusTag pass">无需压缩</span>}
    </div>}

    {coordinateEvidence.requiresConfirmation && <div className="coordinateAuditPanel">
      <div><strong>坐标关系需要确认</strong><p>{String(coordinateEvidence.message ?? coordinateGate?.message ?? '')}</p></div>
      <dl>
        <div><dt>中心偏移</dt><dd>{Number(coordinateEvidence.centerOffsetM ?? 0).toFixed(2)} m</dd></div>
        <div><dt>范围交叠率</dt><dd>{(Number(coordinateEvidence.overlapRatio ?? 0) * 100).toFixed(1)}%</dd></div>
        <div><dt>尺度比</dt><dd>{Number(coordinateEvidence.scaleRatio ?? 0).toFixed(2)}</dd></div>
        <div><dt>建议平移预览</dt><dd>dx={Number(coordinateEvidence.suggestedTranslation?.dx ?? 0).toFixed(2)} m，dy={Number(coordinateEvidence.suggestedTranslation?.dy ?? 0).toFixed(2)} m</dd></div>
      </dl>
      <p className="small">平移建议只用于人工核对，不会自动改动工程坐标。</p>
    </div>}

    <div className="systemOptionSection">
      <div className="systemOptionHeader">
        <div><h4>体系级候选</h4><p>以下候选来自统一平面分类与结构体系目录，覆盖规则平面、一般凸多边形、凹形分区、井筒及含障碍工程。</p></div>
        <span>{String(data.systemOptions?.shapeClassification ?? data.systemOptions?.shapeArchetype ?? '未识别')}</span>
      </div>
      <div className="systemOptionGrid">
        {options.slice(0, 6).map((option) => <article key={option.id} className={`systemOptionCard ${option.recommended ? 'recommended' : ''}`}>
          <div className="systemOptionTitle"><strong>{option.priority}. {option.title}</strong>{option.recommended ? <em>优先</em> : null}</div>
          <p>{option.nextAction}</p>
          <div className="systemOptionMeta"><span>{generationModeText[option.generationMode] ?? option.generationMode}</span><span>{readinessText[option.candidateReadiness] ?? option.candidateReadiness}</span></div>
          <details><summary>前提与硬边界</summary>
            <strong>前提</strong><ul>{(option.prerequisites ?? []).map((item, index) => <li key={`pre-${index}`}>{item}</li>)}</ul>
            <strong>硬边界</strong><ul>{(option.hardBoundaries ?? []).map((item, index) => <li key={`hard-${index}`}>{item}</li>)}</ul>
          </details>
          {option.automaticGenerationAvailable ? <button type="button" onClick={() => void runTask(`正在按${option.title}生成候选`, 'support_layout_optimization', { preset: 'clean_support_layout', topologyFamily: option.family })}>按该体系生成候选</button> : <button type="button" className="secondary" disabled>需先定义体系模型</button>}
        </article>)}
      </div>
      <p className="small boundaryNote">{data.systemOptions?.decisionBoundary}</p>
    </div>

    {data.nextActions?.length ? <details className="qualificationNextActions"><summary>查看按优先级排列的修复动作（{data.nextActions.length}）</summary><ol>{data.nextActions.map((item) => <li key={`${item.gateCode}-${item.priority}`}><strong>{item.title}</strong>：{item.action}</li>)}</ol></details> : null}
  </section>;
}
