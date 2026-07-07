import { render, screen } from '@testing-library/react';
import ExcavationEditor from './ExcavationEditor';
import type { Project } from '../types/domain';

const project: Project = {
  id: 'project-cad',
  name: 'CAD test',
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: { safetyGrade: '二级', environmentGrade: '一般', groundwaterLevel: -1.5, surcharge: 20, minimumSegmentLength: 0.5, ruleSet: 'test' },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: []
};

describe('ExcavationEditor CAD-like shell', () => {
  it('renders CAD-like controls for drawing workflow', () => {
    render(<ExcavationEditor project={project} onSaved={() => undefined} />);
    expect(screen.getByText('基坑轮廓 CAD-like 编辑器')).toBeInTheDocument();
    expect(screen.getByText('网格吸附')).toBeInTheDocument();
    expect(screen.getByText('正交约束')).toBeInTheDocument();
    expect(screen.getByText('撤销')).toBeInTheDocument();
    expect(screen.getByText('适配视图')).toBeInTheDocument();
    expect(screen.getByText('导入 DXF')).toBeInTheDocument();
    expect(screen.getByText('选中边偏移')).toBeInTheDocument();
    expect(screen.getByText('添加障碍')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('10,20 / RECT 0 0 60 30 / RAMP 30 15 10 20')).toBeInTheDocument();
  });
});
