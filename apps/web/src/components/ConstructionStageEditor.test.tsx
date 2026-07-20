import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import ConstructionStageEditor from './ConstructionStageEditor';
import { api } from '../api/client';
import type { Project } from '../types/domain';

const project = {
  id: 'stage-confirmation-project', name: '施工路径确认测试', location: '测试场地',
  createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: { safetyGrade: '二级', environmentGrade: '严格', groundwaterLevel: -1.5, surcharge: 20, minimumSegmentLength: 0.5, ruleSet: 'test' },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
  excavation: { id: 'EX', name: '基坑', topElevation: 0, bottomElevation: -10, depth: 10, outline: { closed: true, points: [{ x: 0, y: 0 }, { x: 20, y: 0 }, { x: 20, y: 20 }] }, segments: [] },
} as unknown as Project;

const recommendedWorkspace = {
  projectId: project.id, saved: false,
  case: {
    id: 'recommended-case', name: '系统推荐分步开挖与换撑工况', source: 'auto_default', locked: false,
    stages: [{ id: 'S1', name: '开挖并安装第一道支撑', excavationElevation: -3, activeSupportIds: [], deactivatedSupportIds: [], activeSupportLevels: [], transferredSupportLevels: [], stageType: 'support_installation', zone: 'Z1', surcharge: 20 }],
  },
  summary: { source: 'auto_default', locked: false, stageCount: 1, supportCount: 0, validationStatus: 'pass', failCount: 0, warningCount: 0 },
  validation: { status: 'pass', valid: true, failCount: 0, warningCount: 0, stageCount: 1, issues: [] },
  supportOptions: [], inputGuide: [],
} as any;

describe('ConstructionStageEditor', () => {
  it('lets the engineer adopt an unchanged recommended path as the formal calculation input', async () => {
    vi.spyOn(api, 'getConstructionStages').mockResolvedValue(recommendedWorkspace);
    vi.spyOn(api, 'saveConstructionStages').mockResolvedValue({ ...recommendedWorkspace, saved: true } as any);
    const changed = vi.fn();
    render(<ConstructionStageEditor project={project} onChanged={changed} />);

    const adopt = await screen.findByRole('button', { name: '采用推荐阶段并锁定' });
    expect(adopt).toBeEnabled();
    expect(screen.getByText('当前只是系统建议，尚未成为计算输入')).toBeInTheDocument();
    fireEvent.click(adopt);

    await waitFor(() => expect(api.saveConstructionStages).toHaveBeenCalledWith(project.id, expect.objectContaining({ id: 'recommended-case' })));
    await waitFor(() => expect(changed).toHaveBeenCalled());
  });
});
