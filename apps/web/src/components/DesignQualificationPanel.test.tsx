import { render, screen, waitFor } from '@testing-library/react';
import DesignQualificationPanel from './DesignQualificationPanel';
import type { Project } from '../types/domain';

const project = {
  id: 'qualification-project', name: '资格矩阵', createdAt: '', updatedAt: '2026-07-15',
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: {}, boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
} as unknown as Project;

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    status: 'blocked', interactionMode: 'diagnostic', workspaceProfileRequired: true,
    candidateGenerationAllowed: true, calculationAllowed: false, formalIssueAllowed: false,
    gates: [
      { code: 'Q-STORAGE', title: '项目数据工作集', status: 'warning', message: '完整快照过大', blocks: ['interactive_full_load'], recommendedAction: '运行压缩' },
      { code: 'Q-COORD-GEO', title: '坐标与地质覆盖', status: 'manual_review', message: '坐标需确认', blocks: ['formal_issue'], evidence: { coordinateAlignment: { requiresConfirmation: true, message: '范围无交叠', centerOffsetM: 141.4, overlapRatio: 0, scaleRatio: 1.4, suggestedTranslation: { dx: 100, dy: 100 } } } },
    ],
    systemOptions: { shapeClassification: 'orthogonal_concave_corridor', decisionBoundary: '体系先行', options: [
      { id: 'SYS-1', family: 'zoned_direct', title: '分区墙—墙对撑与显式转接区', priority: 1, recommended: true, generationMode: 'preliminary', candidateReadiness: 'candidate_generation_ready', automaticGenerationAvailable: true, prerequisites: ['分区'], hardBoundaries: ['显式转接'], nextAction: '生成候选' },
      { id: 'SYS-2', family: 'center_island', title: '中心岛法或留土分区施工', priority: 2, generationMode: 'system_selection_required', candidateReadiness: 'system_definition_required', automaticGenerationAvailable: false, prerequisites: [], hardBoundaries: [], nextAction: '先定义模型' },
    ] },
    nextActions: [],
  }), { status: 200 })));
});

afterEach(() => vi.unstubAllGlobals());

it('shows storage, coordinate and system-level decisions without pretending diagnostic cards are designs', async () => {
  render(<DesignQualificationPanel project={project} runTask={vi.fn(async () => undefined)} />);
  await waitFor(() => expect(screen.getByText('诊断与体系选择模式')).toBeInTheDocument());
  expect(screen.getByText('无需压缩')).toBeInTheDocument();
  expect(screen.getByText('坐标关系需要确认')).toBeInTheDocument();
  expect(screen.getByText(/分区墙—墙对撑与显式转接区/)).toBeInTheDocument();
  expect(screen.getByText(/中心岛法或留土分区施工/)).toBeInTheDocument();
  expect(screen.getByText('按该体系生成候选')).toBeInTheDocument();
});
