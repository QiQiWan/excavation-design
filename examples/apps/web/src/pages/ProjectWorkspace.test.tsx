import { render, screen } from '@testing-library/react';
import ProjectWorkspace from './ProjectWorkspace';
import type { Project } from '../types/domain';

const project: Project = {
  id: 'project-test',
  name: '流程重构测试项目',
  location: '测试场地',
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: {
    safetyGrade: '二级',
    environmentGrade: '严格',
    groundwaterLevel: -1.5,
    surcharge: 20,
    minimumSegmentLength: 0.5,
    ruleSet: 'jgj120_gbt50010_gb50007_gb50009_v0_2'
  },
  boreholes: [],
  strata: [],
  calculationCases: [],
  calculationResults: [],
  messages: []
};

describe('ProjectWorkspace workflow shell', () => {
  it('renders engineering workflow steps instead of a tab-only workspace', () => {
    render(<ProjectWorkspace project={project} onBack={() => undefined} onProjectChange={() => undefined} />);
    expect(screen.getByText('工程流程')).toBeInTheDocument();
    expect(screen.getAllByText('项目设置').length).toBeGreaterThan(0);
    expect(screen.getAllByText('地勘资料').length).toBeGreaterThan(0);
    expect(screen.getAllByText('三维地质模型').length).toBeGreaterThan(0);
    expect(screen.getAllByText('BIM 与计算书').length).toBeGreaterThan(0);
  });
});

it('exposes compact navigation, keyboard command and long-term design settings', () => {
  render(<ProjectWorkspace project={project} onBack={() => undefined} onProjectChange={() => undefined} />);
  expect(screen.getByRole('button', { name: /命令 Ctrl\+K/ })).toBeInTheDocument();
  expect(screen.getByText('长期效应、抗裂与监测控制')).toBeInTheDocument();
  expect(screen.getByLabelText('设计使用年限（年）')).toBeInTheDocument();
  expect(screen.getByText('施工图包必须完成四级批准')).toBeInTheDocument();
});
