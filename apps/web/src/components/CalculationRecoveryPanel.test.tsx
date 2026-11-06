import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import CalculationRecoveryPanel from './CalculationRecoveryPanel';

const { diagnose } = vi.hoisted(() => ({ diagnose: vi.fn(async () => ({ checkSummary: { fail: 0 } })) }));
vi.mock('../api/client', () => ({ api: { diagnoseAndRepairCalculation: diagnose } }));

const project: any = {
  id: 'project-lshape',
  retainingSystem: { supports: [] },
  calculationResults: [{
    id: 'calc-old',
    checkSummary: { fail: 3, warning: 8 },
    calculationContractId: 'calc-contract-test-v324',
    inputSnapshotHash: 'a'.repeat(64),
    adoptedDesignSnapshotHash: 'b'.repeat(64),
    resultHash: 'c'.repeat(64),
    calculationAssurance: {
      status: 'manual_review',
      stageCoverage: { expected: 8, actual: 8, complete: true },
      numericalQuality: { maxConditionNumber: 1200000, maxRelativeResidual: 1e-10, fallbackCount: 0 },
      independentCheck: { maxWallDisplacementRelativeDifference: 0.31, supportReconciliationWarningCount: 1, supportReconciliationManualReviewCount: 0 },
      traceability: { coverage: 1 },
      issues: [{ code: 'RESULT-INDEPENDENT-CHECK', title: '独立计算路径复核', status: 'manual_review', message: '位移差异需要人工复核。', requiredAction: '复核墙体支点和单位。' }],
    },
    designIterationSummary: {
      calculationDiagnostics: {
        rootCauses: [{ code: 'UNRESTRAINED_CONCAVE_RETURN_WALL', title: '凹角回墙缺少直接支撑', severity: 'fail', description: 'S4 未形成直接传力路径。', recommendedAction: '增补局部次对撑。' }],
      },
    },
  }],
};

describe('CalculationRecoveryPanel', () => {
  it('shows the root cause and runs one-click repair', async () => {
    const runStep = vi.fn(async (_label: string, action: () => Promise<unknown>): Promise<void> => { await action(); });
    render(<CalculationRecoveryPanel project={project} runStep={runStep} />);
    expect(screen.getByText('凹角回墙缺少直接支撑')).toBeInTheDocument();
    expect(screen.getByText('工业计算质量包')).toBeInTheDocument();
    expect(screen.getByText('需要复核')).toBeInTheDocument();
    expect(screen.getByText('8 / 8')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '诊断并执行强度闭环' }));
    expect(runStep).toHaveBeenCalled();
  });
});
