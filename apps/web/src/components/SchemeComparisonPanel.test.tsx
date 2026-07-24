import { render, screen, waitFor } from '@testing-library/react';
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
    expect(screen.getAllByText('完整计算状态').length).toBeGreaterThan(0);
    expect(screen.getAllByText('待计算').length).toBeGreaterThan(0);
    expect(screen.queryByText('4,500 kN')).not.toBeInTheDocument();
    expect(screen.getByText('适应窗口')).toBeInTheDocument();
    expect(screen.getByText(/滚轮缩放/)).toBeInTheDocument();
  });

  it('hides stale full-calculation values after topology changes', () => {
    const staleProject = {
      ...project,
      advancedEngineering: {
        calculationState: {
          requiresRecalculation: true,
          reason: 'support topology changed',
        },
      },
      retainingSystem: {
        ...project.retainingSystem,
        supportLayoutRepair: {
          ...project.retainingSystem?.supportLayoutRepair,
          candidates: project.retainingSystem?.supportLayoutRepair?.candidates?.map((candidate, index) => ({
            ...candidate,
            fullCalculation: index === 0 ? { maxDisplacement: 503.95, failCount: 33 } : {},
          })),
        },
      },
      calculationResults: [{
        id: 'stale-result', projectId: project.id, caseId: 'old-case',
        governingValues: { maxDisplacement: 503.95 },
        checkSummary: { fail: 33 },
        supportLayoutRepair: { candidateFullCalculations: [{ candidateId: 'A', maxDisplacement: 503.95, failCount: 33 }] },
      }],
    } as unknown as Project;
    render(<SchemeComparisonPanel project={staleProject} compact />);
    expect(screen.getByText('旧结果已失效')).toBeInTheDocument();
    expect(screen.getByText(/当前支撑拓扑已变更/)).toBeInTheDocument();
    expect(screen.queryByText('503.95 mm')).not.toBeInTheDocument();
    expect(screen.queryByText('Fail 33')).not.toBeInTheDocument();
  });

  it('loads missing candidate geometry through the lightweight preview endpoint', async () => {
    const lightweight = {
      ...project,
      retainingSystem: {
        ...project.retainingSystem,
        supportLayoutRepair: {
          ...project.retainingSystem?.supportLayoutRepair,
          candidates: project.retainingSystem?.supportLayoutRepair?.candidates?.map((candidate) => ({ ...candidate, planGeometry: {} })),
        },
      },
    } as unknown as Project;
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      projectId: project.id, source: 'preview_cache', previews: ['A', 'B', 'C'].map((candidateId, index) => ({
        candidateId, rank: index + 1, planGeometry: {
          outline: [{ x: 0, y: 0 }, { x: 20, y: 0 }, { x: 20, y: 10 }, { x: 0, y: 10 }],
          supports: [{ id: `${candidateId}-S1`, start: { x: 5, y: 0 }, end: { x: 5, y: 10 }, role: 'main_strut' }], columns: [],
        },
      })),
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<SchemeComparisonPanel project={lightweight} compact />);
    await waitFor(() => expect(container.querySelectorAll('.schemeLine').length).toBeGreaterThanOrEqual(3));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/design/candidate-previews'), expect.anything());
    expect(screen.queryByText('方案几何尚未写入工作区')).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it('shows distinct controlled-block diagnostics but disables adoption and full calculation', () => {
    const controlled = {
      ...project,
      retainingSystem: {
        ...project.retainingSystem,
        supportLayoutRepair: {
          candidates: project.retainingSystem?.supportLayoutRepair?.candidates?.map((candidate, index) => ({
            ...candidate,
            hardConstraints: { passed: false },
            variableSummary: {
              ...candidate.variableSummary,
              capabilityOutcome: 'controlled_block',
              formalSchemeEligible: false,
              minimumGeometryDeltaToSelected: index === 0 ? 1 : 0.25,
              alternativeSystemRecommendations: ['环梁/环撑体系'],
              shapeDiagnostics: { classification: 'slender_stepped_strip' },
            },
          })),
        },
      },
    } as unknown as Project;
    render(<SchemeComparisonPanel project={controlled} compact />);
    expect(screen.getByText(/实际几何不同的诊断试案/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '诊断试案不可完整计算' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '诊断试案不可采用' })).toBeDisabled();
  });


  it('renders a formal concave transfer candidate with calculation and delivery states separated', () => {
    const transferProject = {
      ...project,
      retainingSystem: {
        ...project.retainingSystem,
        supportLayoutRepair: {
          candidateState: 'formal_ready',
          formalCandidateCount: 3,
          comparisonEligibility: { state: 'formal_ready', formalCandidateCount: 3, comparisonAllowed: true },
          candidates: project.retainingSystem?.supportLayoutRepair?.candidates?.map((candidate, index) => ({
            ...candidate,
            hardConstraints: { passed: true, blockingCategories: [] },
            variableSummary: {
              ...candidate.variableSummary,
              topologyFamily: 'ring_radial',
              schemeLabel: ['紧凑型异形闭合内环梁', '均衡型异形闭合内环梁', '延伸型异形闭合内环梁'][index],
              transferSystemTemplate: ['compact_elbow_ring', 'junction_hub_frame', 'ring_chord_frame'][index],
              transferSystemAudit: {
                required: true,
                templateLabel: ['紧凑型异形闭合内环梁', '均衡型异形闭合内环梁', '延伸型异形闭合内环梁'][index],
                calculationReady: true,
                officialIssueReady: false,
              },
              formalSchemeEligible: true,
            },
            planGeometry: {
              outline: [{ x: 0, y: 0 }, { x: 20, y: 0 }, { x: 20, y: 10 }, { x: 0, y: 10 }],
              supports: [{ id: `RS-${index}`, start: { x: 0, y: 5 }, end: { x: 5, y: 5 }, role: 'ring_strut' }],
              columns: [],
              transferBeams: [{ id: `TR-${index}`, code: `TR-${index}`, points: [{ x: 5, y: 3 }, { x: 15, y: 3 }] }],
              transferZones: [{ id: 'TZ-1', outline: [{ x: 5, y: 3 }, { x: 15, y: 3 }, { x: 15, y: 7 }, { x: 5, y: 7 }] }],
            },
          })),
        },
      },
    } as unknown as Project;
    const { container } = render(<SchemeComparisonPanel project={transferProject} compact />);
    expect(screen.getByText('A / B / C 支撑方案比选')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完整计算 A/B/C' })).toBeEnabled();
    expect(screen.getByText(/计算资格 通过/)).toBeInTheDocument();
    expect(screen.getByText(/正式出图 待节点深化/)).toBeInTheDocument();
    expect(container.querySelectorAll('polyline.schemeTransferBeam').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('.schemeTransferZone').length).toBeGreaterThan(0);
  });

  it('reports the inspected candidate so the quality-plan viewer follows the selected card', async () => {
    const onSelectCandidate = vi.fn();
    render(<SchemeComparisonPanel project={project} compact onSelectCandidate={onSelectCandidate} />);
    await waitFor(() => expect(onSelectCandidate).toHaveBeenCalled());
    const calls = onSelectCandidate.mock.calls;
    expect(calls[calls.length - 1]?.[0]?.id).toBe('A');
  });

});
