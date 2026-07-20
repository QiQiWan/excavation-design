import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import CoreProjectWorkspace from './CoreProjectWorkspace';
import { api } from '../api/client';
import type { Project } from '../types/domain';

const project: Project = {
  id: 'project-core', name: '核心工作台项目', location: '测试场地',
  createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: { safetyGrade: '二级', environmentGrade: '严格', groundwaterLevel: -1.5, surcharge: 20, minimumSegmentLength: 0.5, ruleSet: 'core' },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
};

describe('CoreProjectWorkspace', () => {
  it('shows three guided phases by default and keeps six technical decisions in professional flow', async () => {
    vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValue({
      nextStage: 'basis', designBasis: { confirmed: false, parameters: [], loadCombinations: [], standards: [], blockers: ['设计基准尚未确认'] }, summary: { failCount: 0, warningCount: 0 }, storage: { workspaceBytes: 1024, externalBytes: 0 },
      stages: [
        { key: 'basis', title: '设计基准', status: 'active', message: '待确认' },
        { key: 'input', title: '工程输入', status: 'active', message: '待完善' },
        { key: 'scheme', title: '围护方案', status: 'pending', message: '待输入' },
        { key: 'calculation', title: '计算验算', status: 'pending', message: '待方案' },
        { key: 'reinforcement', title: '配筋深化', status: 'pending', message: '待计算' },
        { key: 'deliverables', title: '成果交付', status: 'pending', message: '待配筋' },
      ],
    } as any);
    render(<CoreProjectWorkspace project={project} onBack={() => undefined} onProjectChange={() => undefined} />);
    await waitFor(() => expect(screen.getByText('快速方案')).toBeInTheDocument());
    expect(screen.getByText('计算与优化')).toBeInTheDocument();
    expect(screen.getByText('配筋与交付')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '专业流程' }));
    await waitFor(() => expect(screen.getByRole('navigation', { name: '专业设计流程' })).toBeInTheDocument());
    expect(screen.getAllByText('设计基准').length).toBeGreaterThan(0);
    expect(screen.getByText('工程输入')).toBeInTheDocument();
    expect(screen.getByText('围护方案')).toBeInTheDocument();
    expect(screen.getByText('计算验算')).toBeInTheDocument();
    expect(screen.getByText('配筋深化')).toBeInTheDocument();
    expect(screen.getByText('成果交付')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '专业视图' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '精简视图' })).toBeInTheDocument();
    expect(screen.queryByText('P0-P3 工业闭环')).not.toBeInTheDocument();
    expect(screen.queryByText('系统可靠性')).not.toBeInTheDocument();
  });
});

it('keeps the current stage after a completed task without showing a completion banner', async () => {
  vi.restoreAllMocks();
  const readyProject = {
    ...project,
    designSettings: { ...project.designSettings, designBasisConfirmed: true, bearingCapacityKpa: 180 },
    boreholes: [{ id: 'BH1', code: 'BH1', x: 5, y: 5, elevation: 0, layers: [] }],
    excavation: { id: 'EX1', name: 'pit', topElevation: 0, bottomElevation: -10, depth: 10, outline: { closed: true, points: [{ x: 0, y: 0 }, { x: 30, y: 0 }, { x: 30, y: 20 }, { x: 0, y: 20 }] }, segments: [] },
  } as any;
  const schemeStatus = {
    nextStage: 'scheme', nextAction: '生成围护方案', designBasis: { confirmed: true, parameters: [], loadCombinations: [], standards: [], blockers: [] }, summary: {}, storage: {},
    stages: [
      { key: 'basis', title: '设计基准', status: 'complete', message: '已确认' },
      { key: 'input', title: '工程输入', status: 'complete', message: '已完成' },
      { key: 'scheme', title: '围护方案', status: 'active', message: '待生成' },
      { key: 'calculation', title: '计算验算', status: 'pending', message: '待方案' },
      { key: 'reinforcement', title: '配筋深化', status: 'pending', message: '待计算' },
      { key: 'deliverables', title: '成果交付', status: 'pending', message: '待配筋' },
    ],
  } as any;
  const calculationStatus = { ...schemeStatus, nextStage: 'calculation', nextAction: '进入计算验算' } as any;
  vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValueOnce(schemeStatus).mockResolvedValue(calculationStatus);
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([] as any);
  vi.spyOn(api, 'createTask').mockResolvedValue({ id: 'task-1', projectId: readyProject.id, operation: 'support_layout_optimization', title: '生成 A/B/C 围护方案', status: 'success', progress: 100, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() } as any);
  vi.spyOn(api, 'getProject').mockResolvedValue(readyProject);
  render(<CoreProjectWorkspace project={readyProject} onBack={() => undefined} onProjectChange={() => undefined} />);
  await waitFor(() => expect(screen.getByRole('heading', { name: '围护方案' })).toBeInTheDocument());
  screen.getByRole('button', { name: '生成/更新 A/B/C' }).click();
  await waitFor(() => expect(api.getProject).toHaveBeenCalled());
  expect(screen.getByRole('heading', { name: '围护方案' })).toBeInTheDocument();
  expect(screen.queryByText('当前步骤已更新')).not.toBeInTheDocument();
  expect(screen.queryByText(/系统不会自动切换页面/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '专业流程' }));
  await waitFor(() => expect(screen.getByRole('navigation', { name: '专业设计流程' })).toBeInTheDocument());
  expect(screen.getByRole('button', { name: '4计算验算待方案' })).toBeInTheDocument();
});
