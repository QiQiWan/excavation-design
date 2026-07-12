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
    fireEvent.click(screen.getByRole('button', { name: '诊断并自动修复复算' }));
    expect(runStep).toHaveBeenCalled();
  });
});
