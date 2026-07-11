import { fireEvent, render, screen } from '@testing-library/react';
import RebarDesignPanel from './RebarDesignPanel';
import type { Project } from '../types/domain';

const project: Project = {
  id: 'project-rebar-test', name: '配筋施工图测试', location: '测试场地',
  createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  unitSystem: { length: 'm', force: 'kN', stress: 'kPa', angle: 'degree' },
  coordinateSystem: { type: 'local', originX: 0, originY: 0, originZ: 0 },
  designSettings: { safetyGrade: '二级', environmentGrade: '严格', groundwaterLevel: -1.5, surcharge: 20, minimumSegmentLength: 0.5, ruleSet: 'test' },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
  retainingSystem: { id: 'retaining-test', type: 'diaphragm_wall_internal_support', warnings: [], diaphragmWalls: [], crownBeams: [], waleBeams: [], supports: [], supportNodes: [], columns: [] }
};

const scheme = {
  projectId: project.id, mode: 'balanced', status: 'warning', method: 'test',
  wallZones: [{ zoneId: 'WZ-01', hostCode: 'DW-01', zoneType: 'support_node_zone', topElevation: 0, bottomElevation: -2, faces: [{ face: 'inner', token: 'HRB400 D25@150' }, { face: 'outer', token: 'HRB400 D22@150' }], horizontalDistribution: { token: 'D16@150' }, status: 'warning', drawingRefs: ['R-02', 'D-04'] }],
  supportSchemes: [], beamNodeSchemes: [],
  checks: [{ checkId: 'C-01', category: 'wall_reinforcement', hostCode: 'DW-01', status: 'warning', message: '复核机械连接', recommendedAction: '检查接头错开比例。' }],
  summary: { wallZoneCount: 1, supportSchemeCount: 0, failCount: 0, warningCount: 1 },
  drawingIndex: { 'R-02': 'Wall elevation' }, limitations: [],
  diagnostics: {
    calculation: { status: 'pass', valid: true, messages: ['配筋使用最新施工阶段内力包络。'] },
    supportTopology: { status: 'pass', message: '各墙面均有直接传力路径。', secondaryGridSupportCount: 2, maxCornerTributaryWidthM: 9.75 },
    categoryStatusCounts: { wall_reinforcement: { pass: 0, warning: 1, manual_review: 0, fail: 0 } },
    failureReasons: {}, actions: [{ id: 'REVIEW_WARNINGS', priority: 3, label: '处理复核项', description: '复核构造。' }],
    canApply: true, canIssueConstructionDrawings: false, exportMode: 'review', reviewWatermarkRequired: true, sectionChangeCount: 0,
    headline: '仍有复核项，当前输出审查版图纸。'
  }
};
const manifest = {
  projectId: project.id, softwareVersion: '3.2.0', sheetCount: 2, supportLevels: [], categories: {}, packageFolders: [], issueBoundary: 'review',
  sheets: [{ sheetNo: 'R-02', title: '地下连续墙分区配筋立面图', category: 'rebar_elevation', scale: '1:100', file: 'R-02.dxf' }]
};

vi.stubGlobal('fetch', vi.fn((url: string) => {
  if (url.includes('/rebar/design-scheme')) return Promise.resolve(new Response(JSON.stringify(scheme), { status: 200 }));
  if (url.includes('/drawings-manifest')) return Promise.resolve(new Response(JSON.stringify(manifest), { status: 200 }));
  return Promise.resolve(new Response('{}', { status: 200 }));
}));

describe('RebarDesignPanel', () => {
  it('renders guided diagnosis and review-only CAD gate', async () => {
    render(<RebarDesignPanel project={project} onApplied={() => undefined} />);
    expect(await screen.findByText('配筋设计与施工图')).toBeInTheDocument();
    expect(await screen.findByText('仍有复核项，当前输出审查版图纸。')).toBeInTheDocument();
    expect(screen.getByText('下载审查版图纸')).toHaveAttribute('href', expect.stringContaining('issue_mode=review'));
    expect(screen.getByText('1 校验计算')).toBeInTheDocument();
    expect(screen.getByText('DW-01')).toBeInTheDocument();
  });

  it('supports problem-first filtering and drawing scope downloads', async () => {
    render(<RebarDesignPanel project={project} onApplied={() => undefined} />);
    await screen.findByText('配筋设计与施工图');
    fireEvent.click(screen.getByText('图纸目录'));
    expect(await screen.findByText('地下连续墙分区配筋立面图')).toBeInTheDocument();
    fireEvent.click(screen.getByText('分专业下载'));
    expect(screen.getByText('节点大样')).toHaveAttribute('href', expect.stringContaining('scope=details'));
  });
});
