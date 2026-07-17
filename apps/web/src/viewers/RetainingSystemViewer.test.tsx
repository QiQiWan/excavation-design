import { render, screen } from '@testing-library/react';
import type { Project, SupportLayoutOptimizationCandidate } from '../types/domain';
import RetainingSystemViewer from './RetainingSystemViewer';

vi.mock('./Engineering3DViewer', () => ({ default: () => <div data-testid="engineering-3d" /> }));

const candidate = {
  id: 'legacy-A', rank: 1, score: 47.84, supportCount: 2, columnCount: 1,
  maxSpanLength: 28, maxBaySpacing: 5, crossingCount: 0, junctionCount: 0,
  hardConstraints: { passed: false },
  variableSummary: { schemeLabel: '传统直对撑', capabilityOutcome: 'controlled_block', formalSchemeEligible: false },
  metrics: { supportCrossingCount: 0, internalJunctionCount: 0, maxBaySpacing: 5, maxSpanLength: 28 },
  planGeometry: {
    outline: [{ x: -20, y: -10 }, { x: 20, y: -10 }, { x: 20, y: 10 }, { x: -20, y: 10 }],
    supports: [
      { id: 'S1', code: 'S1', start: { x: -10, y: -10 }, end: { x: -10, y: 10 }, role: 'main_strut' },
      { id: 'S2', code: 'S2', start: { x: 10, y: -10 }, end: { x: 10, y: 10 }, role: 'corner_diagonal' },
    ],
    columns: [{ id: 'C1', code: 'C1', location: { x: 0, y: 0 }, supportCodes: ['S1', 'S2'] }],
  },
} as unknown as SupportLayoutOptimizationCandidate;

const project = {
  id: 'quality-preview', name: 'quality-preview', location: '',
  createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: { safetyGrade: '二级', environmentGrade: '一般', groundwaterLevel: -1, surcharge: 20, minimumSegmentLength: .5, ruleSet: 'test' },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
  excavation: { id: 'EX', name: 'EX', outline: { closed: true, points: candidate.planGeometry!.outline }, topElevation: 0, bottomElevation: -10, segments: [], obstacles: [], localPits: [], drawingLayers: [] },
  retainingSystem: {
    diaphragmWalls: [], crownBeams: [], waleBeams: [], ringBeams: [], supports: [], columns: [], supportNodes: [], warnings: [], replacementPath: [],
    supportLayoutRepair: { candidates: [candidate] },
  },
} as unknown as Project;

describe('RetainingSystemViewer candidate quality plan', () => {
  it('renders the inspected candidate instead of an empty current support system', () => {
    const { container } = render(<RetainingSystemViewer project={project} previewCandidate={candidate} />);
    expect(screen.getByText(/方案 A · 传统直对撑/)).toBeInTheDocument();
    expect(screen.getByText('诊断候选')).toBeInTheDocument();
    expect(container.querySelectorAll('.supportPlanSvg line').length).toBe(2);
    expect(screen.getByText('47.84')).toBeInTheDocument();
  });
});
