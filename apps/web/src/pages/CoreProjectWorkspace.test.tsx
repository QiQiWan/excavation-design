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
  it('shows six core design decisions with design basis first', async () => {
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
    await waitFor(() => expect(screen.getAllByText('设计基准').length).toBeGreaterThan(0));
    expect(screen.getByText('工程输入')).toBeInTheDocument();
    expect(screen.getByText('围护方案')).toBeInTheDocument();
    expect(screen.getByText('计算验算')).toBeInTheDocument();
    expect(screen.getByText('配筋深化')).toBeInTheDocument();
    expect(screen.getByText('成果交付')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '专业视图' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '精简视图' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '质量与追溯' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '设计质量与追溯中心' })).not.toBeInTheDocument();
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
  expect(screen.getByRole('button', { name: '4计算验算待方案' })).toBeInTheDocument();
});

it('does not present unloaded checks as Fail zero or pass', () => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'getCoreDesignStatus').mockImplementation(() => new Promise(() => undefined));
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  const { container } = render(<CoreProjectWorkspace project={project} onBack={() => undefined} onProjectChange={() => undefined} />);
  expect(container.querySelector('.coreHeaderActions .coreGate.pass')).toBeNull();
  expect(screen.queryByText('Fail 0')).not.toBeInTheDocument();
  expect(screen.getByText('正在读取检查')).toBeInTheDocument();
});

it('requires explicit readiness before enabling reinforcement even when a result exists', async () => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValue({ nextStage: 'reinforcement', summary: { failCount: 0, warningCount: 0 } } as any);
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  vi.spyOn(api, 'getLatestCalculationEvidence').mockResolvedValue({} as any);
  const historical = { ...project, calculationResults: [{ id: 'old', checks: [], checkSummary: { fail: 0, warning: 0 } }] } as unknown as Project;
  render(<CoreProjectWorkspace project={historical} onBack={() => undefined} onProjectChange={() => undefined} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '保守' })).toBeDisabled());
  expect(screen.queryByText('Fail 0')).not.toBeInTheDocument();
});

it('submits verify-only recalculation separately from explicit optimization', async () => {
  vi.restoreAllMocks();
  const ready = { ...project, designSettings: { ...project.designSettings, designBasisConfirmed: true }, excavation: { id: 'EX', outline: { points: [], closed: true }, segments: [], topElevation: 0, bottomElevation: -10 }, retainingSystem: { diaphragmWalls: [], crownBeams: [], waleBeams: [], ringBeams: [], supports: [], columns: [], supportNodes: [], warnings: [] } } as unknown as Project;
  vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValue({ nextStage: 'calculation', summary: {}, designBasis: { confirmed: true } } as any);
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  vi.spyOn(api, 'getConstructionStages').mockResolvedValue({ stages: [], checks: [], summary: {} } as any);
  vi.spyOn(api, 'createTask').mockImplementation(() => new Promise(() => undefined));
  render(<CoreProjectWorkspace project={ready} onBack={() => undefined} onProjectChange={() => undefined} />);
  const button = await screen.findByRole('button', { name: '仅复算当前方案' });
  expect(screen.getByRole('button', { name: '优化并采用方案' })).toBeInTheDocument();
  button.click();
  await waitFor(() => expect(api.createTask).toHaveBeenCalledWith(project.id, 'calculation_full', { mode: 'verify', topN: 0 }));
});

it('shows stale backend evidence explicitly and does not use zero summary as passing checks', async () => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValue({ nextStage: 'calculation', summary: { failCount: 0, warningCount: 0 }, calculationReadiness: { valid: false, explicitlyStale: true, resultId: 'old', messages: ['输入已修改'] } } as any);
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  vi.spyOn(api, 'getLatestCalculationEvidence').mockResolvedValue({} as any);
  vi.spyOn(api, 'getConstructionStages').mockResolvedValue({ stages: [], checks: [], summary: {} } as any);
  const old = { ...project, calculationResults: [{ id: 'old', stageResults: [], governingValues: {}, checks: [], checkSummary: {} }] } as unknown as Project;
  const { container } = render(<CoreProjectWorkspace project={old} onBack={() => undefined} onProjectChange={() => undefined} />);
  await waitFor(() => expect(container.querySelector('.calculationEvidenceBanner')).toHaveTextContent('结果已过期'));
  expect(container.querySelector('.coreHeaderActions .coreGate.pass')).toBeNull();
  expect(screen.queryByText('Fail 0')).not.toBeInTheDocument();
  expect(screen.queryByText('当前已加载检查未发现超限')).not.toBeInTheDocument();
});

it('shows unavailable checks after a failed status request', async () => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'getCoreDesignStatus').mockRejectedValue(new Error('readiness unavailable'));
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  render(<CoreProjectWorkspace project={project} onBack={() => undefined} onProjectChange={() => undefined} />);
  await screen.findByText('检查读取失败');
  expect(screen.queryByText('Fail 0')).not.toBeInTheDocument();
});

it('keeps current failed engineering checks separate from calculation execution success', async () => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValue({ nextStage: 'calculation', summary: { failCount: 0, warningCount: 1 }, calculationReadiness: { valid: false, resultId: 'measured', contract: { current: true }, stageEvidenceComplete: true }, verificationDistribution: { records: [], summary: { overall: { failCount: 1, warningCount: 1 } } } } as any);
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  vi.spyOn(api, 'getLatestCalculationEvidence').mockResolvedValue({} as any);
  vi.spyOn(api, 'getConstructionStages').mockResolvedValue({ stages: [], checks: [], summary: {} } as any);
  const measured = { ...project, calculationResults: [{ id: 'measured', stageResults: [], governingValues: {}, checks: [{ ruleId: 'displacement', status: 'warning', message: '100mm > 40mm' }], checkSummary: { fail: 0, warning: 1 } }] } as unknown as Project;
  const { container } = render(<CoreProjectWorkspace project={measured} onBack={() => undefined} onProjectChange={() => undefined} />);
  await waitFor(() => expect(container.querySelector('.coreHeaderActions .coreGate.fail')).toHaveTextContent('Fail 1'));
  expect(container.querySelector('.calculationEvidenceBanner')).toHaveTextContent('当前计算已完成，工程检查未通过');
});

it('opens a separate frozen section panel without creating inputs from the current project', async () => {
  vi.restoreAllMocks();
  vi.spyOn(api, 'getCoreDesignStatus').mockResolvedValue({ nextStage: 'calculation', summary: {} } as any);
  vi.spyOn(api, 'listProjectTasks').mockResolvedValue([]);
  vi.spyOn(api, 'getConstructionStages').mockResolvedValue({ stages: [], checks: [], summary: {} } as any);
  const example = vi.spyOn(api, 'getSectionExample');
  const freeze = vi.spyOn(api, 'freezeSection');
  render(<CoreProjectWorkspace project={project} onBack={() => undefined} onProjectChange={() => undefined} />);
  const toggle = await screen.findByRole('button', { name: '剖面结果验证' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('region', { name: '冻结剖面验证面板' })).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(await screen.findByRole('region', { name: '冻结剖面验证面板' })).toBeInTheDocument();
  expect(example).not.toHaveBeenCalled();
  expect(freeze).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('region', { name: '冻结剖面验证面板' })).not.toBeInTheDocument();
});
