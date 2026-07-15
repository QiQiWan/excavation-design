import { render, screen, waitFor } from '@testing-library/react';
import ProgressiveDesignPanel from './ProgressiveDesignPanel';
import type { Project } from '../types/domain';

const project = {
  id: 'progressive-project', name: '渐进式设计', createdAt: '', updatedAt: '2026-07-15',
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: {}, boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
} as unknown as Project;

const session = {
  projectId: project.id,
  config: {
    currentStage: 'geometry_context', sessionVersion: 1, dirtyFromStage: 'geometry_context',
    decisions: { coordinateMode: 'confirm_before_formal_issue', geologyPolicy: 'expand_with_extrapolation_gate' },
    constraints: {}, resourcePolicy: {}, confirmedStages: [],
  },
  stages: [
    {
      code: 'geometry_context', index: 1, title: '轮廓、坐标与设计域确认', purpose: '确认设计域。',
      status: 'attention', summary: '坐标关系待确认。', nextAction: '确认坐标与地质覆盖策略。',
      choices: [
        { field: 'coordinateMode', value: 'confirm_before_formal_issue', label: '方案阶段暂存，发行前确认', recommended: true },
        { field: 'geologyPolicy', value: 'expand_with_extrapolation_gate', label: '允许受控外扩', recommended: true },
      ],
    },
    { code: 'support_system_strategy', index: 4, title: '支撑结构体系选择', purpose: '确定体系。', status: 'ready', summary: '待选择。', nextAction: '选择体系。', choices: [] },
  ],
  currentStage: 'geometry_context', recommendedStage: 'geometry_context', progress: 0,
  resourcePolicy: { effectiveAvailableBytes: 8 * 1024 ** 3, apiFullLoadLimitBytes: 512 * 1024 ** 2, workerSoftLimitBytes: 4 * 1024 ** 3, workerHardLimitBytes: 5 * 1024 ** 3, recommendedHeavyConcurrency: 1 },
  qualification: { calculationAllowed: false }, configurationTraceHash: '1234567890abcdef',
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(session), { status: 200 })));
});

afterEach(() => vi.unstubAllGlobals());

it('groups progressive decisions by engineering meaning and exposes configuration traceability', async () => {
  render(<ProgressiveDesignPanel project={project} runTask={vi.fn(async () => undefined)} />);
  await waitFor(() => expect(screen.getByText('先确认设计意图，再逐级增加模型复杂度')).toBeInTheDocument());
  expect(screen.getByText('坐标处理')).toBeInTheDocument();
  expect(screen.getByText('地质覆盖策略')).toBeInTheDocument();
  expect(screen.getByText('方案阶段暂存，发行前确认')).toBeInTheDocument();
  expect(screen.getByText('允许受控外扩')).toBeInTheDocument();
  expect(screen.getByText(/配置追踪 1234567890ab/)).toBeInTheDocument();
});
