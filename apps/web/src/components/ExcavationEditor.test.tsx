import { fireEvent, render, screen } from '@testing-library/react';
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
    expect(screen.getByPlaceholderText(/POLY 0,0/)).toBeInTheDocument();
  });

  it('accepts a complete polygon command for an actual project outline', () => {
    render(<ExcavationEditor project={project} onSaved={() => undefined} />);
    const input = screen.getByPlaceholderText(/POLY 0,0/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'POLY -115,-14; -99,-14; -99,-12; -39,-12; -39,-16.5; -13,-16.5; -13,-13; 98,-13; 98,-14.5; 115,-14.5; 115,14.5; 98,14.5; 98,13; -13,13; -13,16.5; -39,16.5; -39,12; -99,12; -99,14; -115,14 CLOSE' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('点数').parentElement).toHaveTextContent('20');
  });
});
