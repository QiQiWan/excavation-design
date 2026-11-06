import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DrawingRuleSetPanel from './DrawingRuleSetPanel';
import type { DrawingRuleSet, Project } from '../types/domain';

const rules: DrawingRuleSet = {
  schemaVersion: '1.0', id: 'pitguard-balanced', name: '平衡型', version: '3.4.0', preset: 'balanced', ruleSetHash: 'abc123',
  parameters: { defaultPaperSize: 'A1', maximumSheetCount: 80, usablePaperRatio: 0.82, includePerWallElevations: true, includeEmptyQualitySheets: false, includeLegacyCompatibilitySheets: true },
  objectiveWeights: { coverage: .32, readability: .25, constructability: .2, compactness: .13, consistency: .1 },
  sheetRules: [{ id: 'S00', enabled: true, sheetNo: 'S-00', title: '总平面图', category: 'global_plan', scope: 'general', renderer: 'master_plan', file: '10_plans/S-00.dxf' }],
};
const preview = { projectId: 'p1', softwareVersion: '3.4.0', sheetCount: 1, supportLevels: [], categories: { global_plan: 1 }, sheets: [{ sheetNo: 'S-00', title: '总平面图', category: 'global_plan', scale: '1:200', renderer: 'master_plan', file: '10_plans/S-00.dxf' }], packageFolders: [], issueBoundary: 'review', decisions: [{ included: true }], overflowSheets: [] };
const project = { id: 'p1', name: 'Test', createdAt: '', updatedAt: '', unitSystem: {} as any, coordinateSystem: {} as any, designSettings: {} as any, boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [] } as Project;

describe('DrawingRuleSetPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/drawing-rules/presets')) return Promise.resolve(new Response(JSON.stringify({ schemaVersion: '1.0', presets: [{ id: 'balanced', name: '平衡型', description: 'default', parameters: {}, objectiveWeights: {}, ruleCount: 1 }, { id: 'compact', name: '紧凑型', parameters: {}, objectiveWeights: {}, ruleCount: 1 }] }), { status: 200 }));
      if (url.endsWith('/api/projects/p1/drawing-rules')) return Promise.resolve(new Response(JSON.stringify({ ruleSet: rules, validation: { valid: true, errors: [], warnings: [] } }), { status: 200 }));
      if (url.includes('/drawing-rules/preview')) return Promise.resolve(new Response(JSON.stringify(preview), { status: 200 }));
      if (url.endsWith('/drawing-rules/optimize')) return Promise.resolve(new Response(JSON.stringify({ projectId: 'p1', baseRuleSetHash: 'abc123', candidateCount: 1, recommendedCandidateId: 'c1', method: 'weighted', boundary: '', candidates: [{ candidateId: 'c1', rank: 1, preset: 'compact', paperSize: 'A1', score: 92.5, metrics: { coverage: 100, readability: 95, constructability: 88, compactness: 80 }, sheetCount: 12, overflowCount: 0, ruleSet: { ...rules, id: 'compact', name: '紧凑型', preset: 'compact' }, planSummary: {} }] }), { status: 200 }));
      return Promise.resolve(new Response('{}', { status: 200 }));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows configurable drawing rules and optimization candidates', async () => {
    render(<DrawingRuleSetPanel project={project} />);
    expect(await screen.findByText('规则有效')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '出图规则集' })).toBeInTheDocument();
    expect(screen.getByText('总平面图')).toBeInTheDocument();
    fireEvent.click(screen.getByText('自动优化规则'));
    await waitFor(() => expect(screen.getByText('规则优化候选')).toBeInTheDocument());
    expect(screen.getByText('92.5')).toBeInTheDocument();
    expect(screen.getByText(/12 张/)).toBeInTheDocument();
  });
});
