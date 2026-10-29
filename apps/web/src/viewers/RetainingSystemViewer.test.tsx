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

it('shows uncalculated wall capacity when only the design moment exists', () => {
  const withMoment = { ...project, retainingSystem: { ...project.retainingSystem, diaphragmWalls: [{ id: 'W1', segmentId: 'SEG1', panelCode: 'W1', thickness: 0.8, bottomElevation: -15, reinforcement: [], designResults: { maxMomentDesign: 100 } }] } } as unknown as Project;
  const { container } = render(<RetainingSystemViewer project={withMoment} />);
  const wallRow = container.querySelector('table tbody tr')!;
  expect(wallRow.children[4]).toHaveTextContent('100.00');
  expect(wallRow.children[5]).toHaveTextContent('未计算');
  expect(wallRow.children[5]).not.toHaveTextContent('118');
});

it('does not show previous adopted capacity as verified evidence after verify-only calculation', () => {
  const previous = { ...project, calculationResults: [{ id: 'verify', calculationMode: 'verify' }], retainingSystem: { ...project.retainingSystem, diaphragmWalls: [{ id: 'W1', segmentId: 'SEG1', panelCode: 'W1', thickness: 0.8, bottomElevation: -15, reinforcement: [], designResults: { maxMomentDesign: 100, momentCapacity: 250, checkStatus: 'pass' } }] } } as unknown as Project;
  const { container } = render(<RetainingSystemViewer project={previous} />);
  const wallRow = container.querySelector('table tbody tr')!;
  expect(wallRow.children[5]).toHaveTextContent('未计算');
  expect(wallRow.children[7]).not.toHaveTextContent('pass');
});

it('labels current result-only reinforcement and capacity as unadopted suggestions', () => {
  const wall = { id: 'W1', segmentId: 'SEG1', panelCode: 'W1', thickness: 0.8, bottomElevation: -15, reinforcement: [], designResults: { maxMomentDesign: 100, momentCapacity: 300, checkStatus: 'pass' } };
  const proposal = { ...project.retainingSystem, diaphragmWalls: [wall] };
  const resultProject = { ...project, calculationResults: [{ id: 'verify', calculationMode: 'verify', derivedDesign: { status: 'proposal_only', adopted: false, retainingSystem: proposal } }] } as unknown as Project;
  const savedInput = JSON.stringify(resultProject.retainingSystem);
  const { container } = render(<RetainingSystemViewer project={resultProject} resultEvidenceCurrent />);
  expect(screen.getByText('复算需求与配筋建议（未采用）')).toBeInTheDocument();
  const wallRow = container.querySelector('table tbody tr')!;
  expect(wallRow.children[5]).toHaveTextContent('300.00');
  expect(wallRow.children[7]).toHaveTextContent('配筋建议（未采用）');
  expect(JSON.stringify(resultProject.retainingSystem)).toBe(savedInput);
});
