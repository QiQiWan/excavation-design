import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import DesignBasisPanel from './DesignBasisPanel';

const project: any = {
  id: 'basis-p3', name: '设计基准说明测试', createdAt: '', updatedAt: '',
  designSettings: {
    projectGrade: '一级', excavationSafetyLevel: '一级', siteComplexity: '复杂', surroundingEnvironmentLevel: '高',
    loadCombinationPolicy: 'conservative', structuralAnalysisModel: 'engineering_spatial', defaultConcreteGrade: 'C40',
    defaultRebarGrade: 'HRB500', stabilityReserveRatio: 0.15, enterpriseLibraryId: 'pitguard_default',
    localStandardTemplateId: 'sensitive_environment_2026', groundwaterLevel: -2, surcharge: 20,
  },
  boreholes: [], strata: [], calculationCases: [], calculationResults: [], messages: [],
};

describe('DesignBasisPanel impact guidance', () => {
  it('links each compact impact summary to its design-basis controls', () => {
    render(<DesignBasisPanel project={project} basis={{ confirmed: false, parameters: [], standards: [], blockers: [], enterprise: { libraries: [], standardTemplates: [] } }} onSaved={() => undefined} />);
    expect(screen.getByText('最小设计任务书')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '需要时展开专业设置' }));
    expect(screen.getByText('参数影响')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /荷载与规范组合/ }));
    expect(screen.getByText(/直接形成土压力、水压力、堆载/)).toBeInTheDocument();
    expect(screen.getByText('γG')).toBeInTheDocument();
    fireEvent.focus(screen.getByLabelText('结构分析模型'));
    expect(screen.getByText(/控制墙和围檩开裂刚度/)).toBeInTheDocument();
    expect(screen.getByText('节点半刚性')).toBeInTheDocument();
  });
});
