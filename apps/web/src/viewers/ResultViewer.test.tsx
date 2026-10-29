import { render, screen } from '@testing-library/react';
import type { Project } from '../types/domain';
import ResultViewer from './ResultViewer';

vi.mock('./WallCloud3DViewer', () => ({ default: () => <div /> }));

function projectWithHealth(value: unknown): Project {
  return {
    id: 'health-values', name: 'health-values', calculationResults: [{
      id: 'health-result', stageResults: [], governingValues: {}, checks: [],
      numericalHealth: {
        status: 'manual_review', summaryBasis: 'standard',
        maximumScaledConditionNumber: value, maximumRelativeResidual: value,
        fallbackCount: value, blockedSystemCount: value,
        reactionIteration: { status: 'manual_review', iterationCount: value },
      },
    }],
  } as unknown as Project;
}

function metricValue(label: string) {
  return screen.getByText(label).closest('.metricLine')!.querySelector('b')!;
}

it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])('keeps unknown numerical-health evidence unavailable (%s)', (value) => {
  render(<ResultViewer project={projectWithHealth(value)} density="compact" coreMode />);
  expect(metricValue('最大尺度化条件数')).toHaveTextContent('未计算');
  expect(metricValue('最大平衡残差')).toHaveTextContent('未计算');
  expect(metricValue('回退 / 阻断矩阵')).toHaveTextContent('未计算 / 未计算');
  expect(metricValue('反力迭代')).toHaveTextContent('未计算');
});

it('preserves actual zero condition, residual, and iteration counts', () => {
  render(<ResultViewer project={projectWithHealth(0)} density="compact" coreMode />);
  expect(metricValue('最大尺度化条件数')).toHaveTextContent('0.000e+0');
  expect(metricValue('最大平衡残差')).toHaveTextContent('0.000e+0');
  expect(metricValue('回退 / 阻断矩阵')).toHaveTextContent('0 / 0');
  expect(metricValue('反力迭代')).toHaveTextContent('0轮');
});

it('identifies standard-action summary values separately from both-basis status', () => {
  render(<ResultViewer project={projectWithHealth(12.5)} density="compact" coreMode />);
  expect(screen.getByText('数值稳定性（标准作用摘要）')).toBeInTheDocument();
  expect(screen.getByText('状态包含标准与设计作用求解。')).toBeInTheDocument();
  expect(metricValue('最大尺度化条件数')).toHaveTextContent('1.250e+1');
});
