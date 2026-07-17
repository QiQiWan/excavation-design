import { fireEvent, render, screen } from '@testing-library/react';
import { CoreStandardGuidance, StabilityDistributionVisual, VerificationSafetyPanel } from './CoreEngineeringVisuals';

describe('Core engineering presentation', () => {
  it('shows the standard and the step-specific focus without a long documentation panel', () => {
    render(<CoreStandardGuidance standards={[{
      code: 'JGJ 120-2012',
      name: '建筑基坑支护技术规程',
      focus: '支护结构与稳定性验算',
      levelLabel: '基坑主控规程',
    }]} />);
    expect(screen.getByText('JGJ 120-2012')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看对应关系' }));
    expect(screen.getByText('支护结构与稳定性验算')).toBeInTheDocument();
  });

  it('distinguishes known code limits from factors that still need limit confirmation', () => {
    render(<StabilityDistributionVisual distribution={{
      factors: [
        { code: 'uplift', label: '承压水突涌', value: 1.18, limit: 1.10, marginRatio: 1.073, status: 'warning', standard: 'JGJ 120-2012' },
        { code: 'overall', label: '整体稳定', value: null, limit: null, marginRatio: null, status: 'manual_review', evidenceState: 'missing_input', message: '缺少土层强度参数', nextAction: '补齐后重新计算', missingInputDetails: [{ code: 'soil_strength', label: '地层强度参数', stageLabel: '勘察阶段', provider: '岩土勘察', designStageAvailable: true, action: '录入 c、φ。' }], standard: 'JGJ 120-2012' },
      ],
      summary: { count: 2, calculatedCount: 1, pendingCount: 1, controllingLabel: '承压水突涌', minimumMarginRatio: 1.073, averageMarginRatio: 1.073, warningCount: 1 },
    }} />);
    expect(screen.getByText('稳定与水控制完整验算目录')).toBeInTheDocument();
    expect(screen.getByText(/1.18 \/ 1.10/)).toBeInTheDocument();
    expect(screen.getByText('缺资料')).toBeInTheDocument();
    expect(screen.getByText('1 项待补资料/计算')).toBeInTheDocument();
    fireEvent.click(screen.getByText('查看缺失资料与补齐方法'));
    expect(screen.getByText('地层强度参数')).toBeInTheDocument();
  });
  it('shows strength stiffness and stability safety factors in one professional matrix', () => {
    render(<VerificationSafetyPanel distribution={{
      records: [
        { id: '1', label: '围护墙抗弯承载力', category: 'strength', designValue: 800, limitValue: 1200, safetyFactor: 1.5, utilization: 0.667, status: 'pass', standard: 'GB/T 50010' },
        { id: '2', label: '围檩挠度', category: 'stiffness', designValue: 8, limitValue: 12, safetyFactor: 1.5, utilization: 0.667, status: 'pass', standard: 'JGJ 120' },
        { id: '3', label: '坑底抗隆起稳定', category: 'stability', designValue: 1.2, limitValue: 1.1, safetyFactor: 1.091, utilization: 0.917, status: 'warning', standard: 'JGJ 120' },
      ],
      summary: { strength: { count: 1, minimumSafetyFactor: 1.5, failCount: 0, warningCount: 0 }, stiffness: { count: 1, minimumSafetyFactor: 1.5, failCount: 0, warningCount: 0 }, stability: { count: 1, minimumSafetyFactor: 1.091, failCount: 0, warningCount: 1 }, overall: { controllingLabel: '坑底抗隆起稳定', warningCount: 1 } },
    }} />);
    expect(screen.getByText('基坑工程完整验算矩阵')).toBeInTheDocument();
    expect(screen.getByText('围护墙抗弯承载力')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /水控制/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /施工性/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /稳定性/ }));
    expect(screen.getByText('坑底抗隆起稳定')).toBeInTheDocument();
  });

  it('shows design-stage missing-input ownership and expandable per-wall evidence', () => {
    render(<VerificationSafetyPanel distribution={{
      records: [{ id: 'system-1', label: '体系强度控制', category: 'strength', status: 'manual_review', evidenceState: 'missing_input', message: '缺少墙体配筋', missingInputDetails: [{ code: 'rebar', label: '墙体配筋方案', stageLabel: '设计阶段', provider: '结构设计', designStageAvailable: true, action: '生成并应用配筋方案。' }], nextAction: '完成配筋后重算。' }],
      missingInputSummary: [{ code: 'rebar', label: '墙体配筋方案', stageLabel: '设计阶段', provider: '结构设计', designStageAvailable: true, action: '生成并应用配筋方案。', affectedCheckCount: 4, affectedChecks: ['墙体抗弯', '墙体抗剪'] }],
      wallObjects: [{ wallId: 'wall-1', wallCode: 'DW-S1-001', wallTypeLabel: '地下连续墙', thicknessM: 1, topElevationM: 0, bottomElevationM: -22, status: 'warning', summary: { calculatedCount: 1, failCount: 0, reviewCount: 1 }, checks: [{ id: 'wall-check-1', label: 'DW-S1-001 抗弯承载力', category: 'strength', designValue: 800, limitValue: 1200, safetyFactor: 1.5, status: 'pass', evidenceState: 'calculated', stageResults: [{ id: 'stage-check-1', label: '工况一', category: 'strength', status: 'pass' }] }] }],
      summary: { strength: { count: 1, failCount: 0, warningCount: 1 }, overall: { catalogCount: 51, warningCount: 1, wallObjectCount: 1 } },
    }} />);
    expect(screen.getByText(/缺资料闭合清单：1 类资料/)).toBeInTheDocument();
    expect(screen.getAllByText('设计阶段可提供')).toHaveLength(2);
    expect(screen.getByText(/逐墙展开验算结果：1 个地下连续墙对象/)).toBeInTheDocument();
    expect(screen.getByText('DW-S1-001')).toBeInTheDocument();
    expect(screen.getByText('DW-S1-001 抗弯承载力')).toBeInTheDocument();
  });

});
