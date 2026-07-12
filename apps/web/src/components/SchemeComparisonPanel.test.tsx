import { render, screen } from '@testing-library/react';
import SchemeComparisonPanel from './SchemeComparisonPanel';
import type { Project } from '../types/domain';

const project = {
  id: 'scheme-project',
  name: '方案比选',
  location: '测试',
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: { safetyGrade: '二级', environmentGrade: '严格', groundwaterLevel: -1.5, surcharge: 20, minimumSegmentLength: 0.5, ruleSet: 'test' },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
  retainingSystem: {
    diaphragmWalls: [], waleBeams: [], supports: [], columns: [], supportNodes: [],
    supportLayoutRepair: {
      candidates: [
        { id: 'A', rank: 1, score: 92, supportCount: 12, columnCount: 4, maxSpanLength: 20, axialPeakProxy: 4500, failCount: 0, warningCount: 2, variableSummary: { topologyFamily: 'hybrid_diagonal' }, planGeometry: { outline: [{x:0,y:0},{x:20,y:0},{x:20,y:10},{x:0,y:10}], supports: [] } },
        { id: 'B', rank: 2, score: 88, supportCount: 14, columnCount: 6, maxSpanLength: 22, axialPeakProxy: 4800, failCount: 0, warningCount: 3, variableSummary: { topologyFamily: 'direct_grid' }, planGeometry: { outline: [{x:0,y:0},{x:20,y:0},{x:20,y:10},{x:0,y:10}], supports: [] } },
        { id: 'C', rank: 3, score: 80, supportCount: 16, columnCount: 5, maxSpanLength: 18, axialPeakProxy: 5100, failCount: 0, warningCount: 4, variableSummary: { topologyFamily: 'bidirectional_grid' }, planGeometry: { outline: [{x:0,y:0},{x:20,y:0},{x:20,y:10},{x:0,y:10}], supports: [] } },
      ]
    }
  }
} as unknown as Project;

describe('SchemeComparisonPanel', () => {
  it('keeps whole-scheme A/B/C comparison visible with explicit units', () => {
    render(<SchemeComparisonPanel project={project} compact />);
    expect(screen.getByText('A / B / C 支撑方案比选')).toBeInTheDocument();
    expect(screen.getByText('方案 A')).toBeInTheDocument();
    expect(screen.getByText('方案 B')).toBeInTheDocument();
    expect(screen.getByText('方案 C')).toBeInTheDocument();
    expect(screen.getAllByText('最长跨度（m）').length).toBeGreaterThan(0);
    expect(screen.getAllByText('最大轴力（kN）').length).toBeGreaterThan(0);
    expect(screen.getByText('适应窗口')).toBeInTheDocument();
    expect(screen.getByText(/滚轮缩放/)).toBeInTheDocument();
  });
});
