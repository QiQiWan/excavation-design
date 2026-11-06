import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FrozenSectionPanel from './FrozenSectionPanel';

const api = vi.hoisted(() => ({
  getSectionExampleV2: vi.fn(), getA3Reference: vi.fn(), saveSectionReferenceContract: vi.fn(), saveSectionReferenceCurves: vi.fn(), compareSectionReference: vi.fn(), getSectionCurves: vi.fn(),
  getSectionExample: vi.fn(), freezeSection: vi.fn(), analyzeSectionSnapshot: vi.fn(),
  getSectionResult: vi.fn(), getLatestSectionResult: vi.fn(), downloadSectionPackage: vi.fn(),
}));
vi.mock('../api/client', () => ({ api }));

const snapshotHash = 'a'.repeat(64);
const analysisHash = 'b'.repeat(64);
const resultHash = 'c'.repeat(64);
const input = {
  schema_version: 'pitguard.frozen-section/1', section_id: 'VERIFY-PILE-01', name: '二维排桩阶段分析验证例',
  purpose: 'verification_fixture', comparison_reference: 'uploaded_A3_3', source_project_id: null,
  datum: { label: '验证模型地面 z=0', wall_top_elevation_m: 0, elevation_system: 'local' },
  pile: { diameter_m: 0.7, spacing_m: 0.9, length_m: 10, elastic_modulus_kpa: 30000000, inertia_factor: 1, toe_boundary: 'free' },
  soil_effective_width_m: 0.9,
  soil_layers: [{ id: 'L1', name: '验证土层', top_m: 0, bottom_m: 20 }],
  supports: [{ id: 'STRUT-1', depth_m: 1.625, kind: 'elastic', stiffness_kn_per_m: 29070 }],
  stages: ['initial', 'excavate', 'install', 'excavate', 'install', 'remove'].map((event, index) => ({
    id: `S${index}`, name: `验证阶段 ${index}`, event, excavation_depth_m: index === 0 ? 0 : index < 3 ? 2.05 : 4.9,
    water_inside_depth_m: index < 3 ? 2.55 : 5.4, water_outside_depth_m: 1.5, surcharge_kpa: 0,
    install_support_ids: index === 2 ? ['STRUT-1'] : [], remove_support_ids: index === 5 ? ['STRUT-1'] : [],
  })),
  analysis: { soil_method: 'two_sided_elastoplastic_winkler_v1', pressure_method: 'effective_rankine_bounds_k0_v1', max_element_size_m: 0.125, design_effect_factor: 1.25 },
  reinforcement: { bar_count: 16, bar_diameter_mm: 18, bar_axis_cover_mm: 59 }, evidence: [], limitations: [],
};
const snapshot = { schemaVersion: 'pitguard.frozen-section/1', snapshotHash, analysisHash, input };
const result = {
  schemaVersion: 'pitguard.section-result/1', sectionId: input.section_id, snapshotHash, analysisHash, resultHash,
  algorithmVersion: 'two-sided-epp-section/1.0', discreteInputHash: 'd'.repeat(64),
  status: 'complete', engineeringStatus: 'review_required', formalDesignReady: false,
  stages: input.stages.map((stage, index) => ({
    id: stage.id, name: stage.name, event: stage.event, status: 'success',
    maxAbsDisplacementMm: index === 0 ? 0 : 12.34, maxAbsMomentKnM: 206.7, maxAbsShearKn: 120.6,
    supports: [{ id: 'STRUT-1', depthM: 1.625, active: index >= 2 && index < 5,
      status: index < 2 ? 'not_installed' : index === 5 ? 'removed' : 'active',
      reactionCharacteristicKn: index >= 2 && index < 5 ? 37.2 : null }],
  })),
  envelope: { complete: true, governing: {
    displacementAbs: { valueMm: 12.34, stageId: 'S5', depthM: 1 },
    momentAbs: { valueKnM: 206.7, stageId: 'S3', depthM: 3 },
    shearAbs: { valueKn: 120.6, stageId: 'S3', depthM: 2 },
  } },
  comparisons: {
    hand: { status: 'passed', scope: '解析梁与支撑子问题' },
    independent: { status: 'unavailable', reason: '未安装独立求解器' },
    mesh: { status: 'failed', reason: '网格差异超过固定容差' },
    original: { status: 'incomplete', sameConditionsConfirmed: false, reason: '原项目条件未齐', missingConditions: ['原生模型', '桩端边界'] },
  },
};

const createdBlobs: Blob[] = [];
const downloads: { name: string; href: string }[] = [];
async function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsText(blob);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  createdBlobs.length = 0;
  downloads.length = 0;
  api.getSectionExample.mockResolvedValue(structuredClone(input));
  api.freezeSection.mockResolvedValue(structuredClone(snapshot));
  api.analyzeSectionSnapshot.mockResolvedValue(structuredClone(result));
  api.getLatestSectionResult.mockResolvedValue(structuredClone(result));
  api.downloadSectionPackage.mockResolvedValue(new Blob(['ZIP'], { type: 'application/zip' }));
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: vi.fn((blob: Blob) => {
    createdBlobs.push(blob); return `blob:section-${createdBlobs.length}`;
  }) });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push({ name: this.download, href: this.href });
  });
});
afterEach(() => vi.restoreAllMocks());

async function loadAndFreeze() {
  fireEvent.click(screen.getByRole('button', { name: '加载合成验证例' }));
  await screen.findByText(input.name);
  fireEvent.click(screen.getByRole('button', { name: '冻结输入' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeEnabled());
}

describe('FrozenSectionPanel', () => {
  it('runs and downloads using only the server-returned snapshot and result identities', async () => {
    render(<FrozenSectionPanel />);
    expect(api.getSectionExample).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
    await loadAndFreeze();
    expect(api.freezeSection).toHaveBeenCalledWith(input);
    expect(screen.getByText('合成验证例')).toBeInTheDocument();
    expect(within(screen.getByRole('table', { name: '输入阶段' })).getAllByRole('row')).toHaveLength(7);
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    await screen.findByText('12.34 mm');
    expect(api.analyzeSectionSnapshot).toHaveBeenCalledWith(snapshotHash);
    expect(within(screen.getByRole('article', { name: '最大绝对位移' })).getByText(/S5/)).toBeInTheDocument();
    expect(screen.getByText(/工程复核待完成/)).toHaveTextContent('review_required');
    expect(within(screen.getByRole('table', { name: '阶段计算结果' })).getAllByRole('row')).toHaveLength(7);
    expect(within(screen.getByRole('article', { name: '独立求解器比较' })).getByText('工具不可用')).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '原项目比较' })).getByText('条件未齐')).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: '网格比较' })).getByText('未达到容差')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '保存冻结输入' }));
    expect(JSON.parse(await blobText(createdBlobs[0]))).toEqual(snapshot);
    expect(downloads[0].name).toContain(snapshotHash.slice(0, 12));
    fireEvent.click(screen.getByRole('button', { name: '下载同源成果包' }));
    await waitFor(() => expect(api.downloadSectionPackage).toHaveBeenCalledWith(resultHash));
    await waitFor(() => expect(downloads).toHaveLength(2));
    expect(downloads[1].name).toContain(resultHash.slice(0, 12));
    expect(createdBlobs[1].type).toBe('application/zip');
  });

  it('immediately invalidates snapshot, results and downloads even during an incomplete JSON edit', async () => {
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    await screen.findByText('12.34 mm');
    fireEvent.click(screen.getByText('原始 JSON 编辑'));
    fireEvent.change(screen.getByLabelText('完整剖面输入 JSON'), { target: { value: '{' } });
    expect(screen.queryByText('12.34 mm')).not.toBeInTheDocument();
    expect(screen.queryByText(resultHash)).not.toBeInTheDocument();
    expect(screen.queryByText(snapshotHash)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存冻结输入' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下载同源成果包' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('JSON');
  });

  it('keeps missing and null values uncalculated and does not replace them with zeros', async () => {
    const incomplete = structuredClone(result) as Record<string, any>;
    incomplete.status = 'failed';
    incomplete.envelope = { complete: false, governing: { displacementAbs: null, momentAbs: null } };
    incomplete.stages[2] = { id: 'S2', name: '验证阶段 2', status: 'failed', failureReason: '本阶段未收敛', maxAbsDisplacementMm: null, supports: [{ id: 'STRUT-1', active: true, status: 'not_calculated', reactionCharacteristicKn: null }] };
    incomplete.comparisons.hand = null;
    api.analyzeSectionSnapshot.mockResolvedValue(incomplete);
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    await screen.findByText('本阶段未收敛');
    for (const name of ['最大绝对位移', '最大绝对弯矩', '最大绝对剪力']) {
      expect(within(screen.getByRole('article', { name })).getByText('未计算')).toBeInTheDocument();
    }
    const row = within(screen.getByRole('table', { name: '阶段计算结果' })).getByRole('row', { name: /S2/ });
    expect(within(row).getAllByText('未计算').length).toBeGreaterThanOrEqual(3);
    expect(row).not.toHaveTextContent('0.00');
    expect(within(screen.getByRole('article', { name: '手算比较' })).getByText('未计算')).toBeInTheDocument();
  });

  it('discards a late analysis response after the input was edited', async () => {
    let resolveAnalysis!: (value: unknown) => void;
    api.analyzeSectionSnapshot.mockImplementation(() => new Promise((resolve) => { resolveAnalysis = resolve; }));
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    fireEvent.click(screen.getByText('原始 JSON 编辑'));
    fireEvent.change(screen.getByLabelText('完整剖面输入 JSON'), { target: { value: JSON.stringify({ ...input, name: '修改后的输入' }) } });
    await act(async () => resolveAnalysis(result));
    expect(screen.queryByText('12.34 mm')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下载同源成果包' })).toBeDisabled();
    expect(screen.getByText('修改后的输入')).toBeInTheDocument();
  });

  it('imports a snapshot as editable input and asks the server to establish its current identity', async () => {
    render(<FrozenSectionPanel />);
    const imported = { ...snapshot, snapshotHash: 'f'.repeat(64) };
    const file = new File([JSON.stringify(imported)], 'frozen.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: async () => JSON.stringify(imported) });
    fireEvent.change(screen.getByLabelText('导入剖面输入 JSON'), { target: { files: [file] } });
    await screen.findByText(input.name);
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
    expect(screen.queryByText(imported.snapshotHash)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '冻结输入' }));
    await waitFor(() => expect(api.freezeSection).toHaveBeenCalledWith(input));
    await waitFor(() => expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeEnabled());
  });

  it('makes server validation errors visible without claiming a frozen input', async () => {
    api.freezeSection.mockRejectedValue(new Error('土层必须覆盖桩底'));
    render(<FrozenSectionPanel />);
    fireEvent.click(screen.getByRole('button', { name: '加载合成验证例' }));
    await screen.findByText(input.name);
    fireEvent.click(screen.getByRole('button', { name: '冻结输入' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('土层必须覆盖桩底');
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
  });

  it('rejects a result with identities different from the frozen server input', async () => {
    api.analyzeSectionSnapshot.mockResolvedValue({ ...result, analysisHash: 'e'.repeat(64) });
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('身份');
    expect(screen.queryByText('12.34 mm')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载同源成果包' })).toBeDisabled();
  });

  it('keeps the frozen identity after an analysis timeout and downloads the explicitly recovered saved result', async () => {
    const savedHash = 'e'.repeat(64);
    api.analyzeSectionSnapshot.mockRejectedValue(new Error('分析等待超过 180 秒。计算可能仍在执行，可恢复该冻结输入最近已保存结果。'));
    api.getLatestSectionResult.mockResolvedValue({ ...result, resultHash: savedHash });
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('计算可能仍在执行');
    expect(screen.getByText(snapshotHash)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存冻结输入' })).toBeEnabled();
    expect(api.analyzeSectionSnapshot).toHaveBeenCalledTimes(1);
    expect(api.getLatestSectionResult).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '恢复已保存结果' }));
    await screen.findByText('12.34 mm');
    expect(api.getLatestSectionResult).toHaveBeenCalledWith(snapshotHash);
    expect(screen.getByRole('status')).toHaveTextContent(/已恢复.*最近.*保存/);
    expect(screen.getByRole('status')).not.toHaveTextContent('阶段计算已完成');
    expect(api.analyzeSectionSnapshot).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: '恢复已保存结果' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下载同源成果包' }));
    await waitFor(() => expect(api.downloadSectionPackage).toHaveBeenCalledWith(savedHash));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0].name).toContain(savedHash.slice(0, 12));
  });

  it.each(['snapshotHash', 'analysisHash', 'sectionId'] as const)('rejects a saved result whose %s differs from the frozen identity', async (field) => {
    api.getLatestSectionResult.mockResolvedValue({ ...result, [field]: 'different-identity' });
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '恢复已保存结果' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('身份');
    expect(screen.queryByText('12.34 mm')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载同源成果包' })).toBeDisabled();
    expect(screen.getByText(snapshotHash)).toBeInTheDocument();
    expect(api.analyzeSectionSnapshot).not.toHaveBeenCalled();
  });

  it('discards a late saved-result response after the input is edited', async () => {
    let resolveSaved!: (value: unknown) => void;
    api.getLatestSectionResult.mockImplementation(() => new Promise((resolve) => { resolveSaved = resolve; }));
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '恢复已保存结果' }));
    fireEvent.click(screen.getByText('原始 JSON 编辑'));
    fireEvent.change(screen.getByLabelText('完整剖面输入 JSON'), { target: { value: JSON.stringify({ ...input, name: '恢复期间修改的输入' }) } });
    await act(async () => resolveSaved(result));
    expect(screen.queryByText('12.34 mm')).not.toBeInTheDocument();
    expect(screen.queryByText(snapshotHash)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复已保存结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载同源成果包' })).toBeDisabled();
    expect(screen.getByText('恢复期间修改的输入')).toBeInTheDocument();
  });

  it('keeps recovery errors visible without automatically retrying or starting another analysis', async () => {
    api.getLatestSectionResult.mockRejectedValue(new Error('该冻结输入尚无已保存结果'));
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '恢复已保存结果' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('尚无已保存结果');
    expect(screen.getByRole('button', { name: '恢复已保存结果' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '下载同源成果包' })).toBeDisabled();
    expect(api.getLatestSectionResult).toHaveBeenCalledTimes(1);
    expect(api.analyzeSectionSnapshot).not.toHaveBeenCalled();
  });
});


const v2Input = { ...input, schema_version: 'pitguard.frozen-section/2', reference_contract_hash: null,
  method_extensions: { layer_stress_modes: [{ layer_id: 'L1', mode: 'total_combined', parameter_basis: 'total', basis: '测试总应力依据' }],
    surface_loads: [{ id: 'Q1', kind: 'rectangle', q_kpa: 30, near_m: 4.3, far_m: 16.3, along_start_m: -32.5, along_end_m: 32.5, basis: '显式假设居中' }],
    stage_options: [{ stage_id: 'S1', active_load_ids: ['Q1'], soil_history_action: 'carry_plastic_history_v1', history_basis: '测试继承依据' }] } };
const contractHash = '1'.repeat(64), bundleHash = '2'.repeat(64);
const reference = { contract: { contractHash, criticalConditionIds: ['E'], parameters: [
  { id: 'E', identity: '桩弹性模量', originalValue: null, value: null, unit: 'kPa', status: 'unconfirmed', requiresConfirmation: true },
  { id: 'D', identity: '桩径', originalValue: .7, value: .7, unit: 'm', status: 'original_fact' },
  { id: 'toe', identity: '桩端深度', originalValue: 7.9, value: 7.9, unit: 'm', status: 'derived_interpretation' },
] }, assessment: { missingConditions: [{ id: 'E', identity: '桩弹性模量', reason: 'original_condition_not_provided' }] }, curveTemplate: { stages: [] } };
async function loadV2() {
  api.getSectionExampleV2.mockResolvedValue(v2Input);
  fireEvent.click(screen.getByRole('button', { name: '加载 v2 方法验证例' }));
  await screen.findByText(input.name);
}
async function uploadReference() {
  api.getA3Reference.mockResolvedValue(reference);
  fireEvent.click(screen.getByText('原项目条件与数值曲线'));
  fireEvent.click(screen.getByRole('button', { name: '加载 A3-3 条件资料' }));
  await screen.findByText('桩弹性模量');
  api.saveSectionReferenceCurves.mockResolvedValue({ bundleHash, contractHash });
  const file = new File(['{}'], 'curves.json');
  Object.defineProperty(file, 'text', { value: async () => '{}' });
  fireEvent.change(screen.getByLabelText('上传原项目数值曲线 JSON'), { target: { files: [file] } });
  await screen.findByText(bundleHash);
}

describe('v2 methods and original reference review', () => {
  it('accepts v2 and rejects a freeze response with a mismatched schema', async () => {
    render(<FrozenSectionPanel />);
    await loadV2();
    fireEvent.click(screen.getByText('v2 方法与阶段假设'));
    expect(screen.getByText('水土合算 / 总应力')).toBeInTheDocument();
    expect(screen.getByText('显式假设居中')).toBeInTheDocument();
    api.freezeSection.mockResolvedValue(snapshot);
    fireEvent.click(screen.getByRole('button', { name: '冻结输入' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('身份');
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
  });

  it('binds a contract only to v2 and invalidates the result without inventing missing conditions', async () => {
    render(<FrozenSectionPanel />);
    await loadV2();
    api.freezeSection.mockResolvedValue({ ...snapshot, schemaVersion: v2Input.schema_version, input: v2Input });
    fireEvent.click(screen.getByRole('button', { name: '冻结输入' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    await screen.findByText('12.34 mm');
    await uploadReference();
    fireEvent.click(screen.getByRole('button', { name: '将条件合同绑定到 v2 输入' }));
    const updated=JSON.parse((screen.getByLabelText('完整剖面输入 JSON') as HTMLTextAreaElement).value);
    expect(updated.reference_contract_hash).toBe(contractHash);
    expect(updated.pile).toEqual(v2Input.pile);
    expect(screen.queryByText('12.34 mm')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '运行冻结剖面' })).toBeDisabled();
  });

  it('does not revive a comparison after editing while its request is pending', async () => {
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    await screen.findByText('12.34 mm');
    await uploadReference();
    expect(screen.getByRole('button', { name: '将条件合同绑定到 v2 输入' })).toBeDisabled();
    let finish!: (v: unknown) => void;
    api.compareSectionReference.mockImplementation(() => new Promise(resolve => { finish=resolve; }));
    fireEvent.click(screen.getByRole('button', { name: '比较原项目数值曲线' }));
    fireEvent.change(screen.getByLabelText('完整剖面输入 JSON'), { target: { value: '{' } });
    await act(async () => finish({ schemaVersion: 'pitguard.reference-comparison-record/1', snapshotHash, resultHash, contractHash, curveBundleHash: bundleHash, comparisonHash: 'late-report', report: { status: 'within_tolerance' } }));
    expect(screen.queryByText('late-report')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载原项目比较 JSON' })).not.toBeInTheDocument();
  });

  it('shows each engineering check without promoting pure bending or calculated to complete approval', async () => {
    api.analyzeSectionSnapshot.mockResolvedValue({ ...result, engineeringChecks: { checks: [
      { id: 'axial_flexural_interaction', status: 'pass', demand: 10, capacity: 20, units: 'kN·m' },
      { id: 'shear', status: 'fail', demand: 30, capacity: 20, units: 'kN' },
      { id: 'anchorage', status: 'missing_input', requiredInputs: ['length'] },
      { id: 'lap', status: 'outside_method_scope' }, { id: 'crack_width', status: 'calculated' },
    ] } });
    render(<FrozenSectionPanel />);
    await loadAndFreeze();
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    const table=await screen.findByRole('table', { name: '工程逐项检查' });
    for (const text of ['通过','失败','缺项','超范围','已计算，待判定']) expect(within(table).getByText(text)).toBeInTheDocument();
    expect(screen.getByText(/工程复核待完成/)).toBeInTheDocument();
  });
});


const comparisonRecord = { schemaVersion: 'pitguard.reference-comparison-record/1', snapshotHash, resultHash, contractHash, curveBundleHash: bundleHash, comparisonHash: '3'.repeat(64), report: { status: 'incomplete', numericalStatus: 'within_tolerance', sameConditionsVerified: false, blockers: ['original_condition_not_provided'] } };
async function readyForComparison() {
  render(<FrozenSectionPanel />);
  await loadAndFreeze();
  fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
  await screen.findByText('12.34 mm');
  await uploadReference();
}

describe('reference response identity and downloads', () => {
  it.each(['schemaVersion','snapshotHash','resultHash','contractHash','curveBundleHash'] as const)('rejects comparison with mismatched %s', async field => {
    await readyForComparison();
    api.compareSectionReference.mockResolvedValue({ ...comparisonRecord, [field]: 'wrong-identity' });
    fireEvent.click(screen.getByRole('button', { name: '比较原项目数值曲线' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('身份不一致');
    expect(screen.queryByRole('button', { name: '下载原项目比较 JSON' })).not.toBeInTheDocument();
  });

  it('downloads an independent comparison and result curves and invalidates the report on recomputation', async () => {
    await readyForComparison();
    api.compareSectionReference.mockResolvedValue(comparisonRecord);
    fireEvent.click(screen.getByRole('button', { name: '比较原项目数值曲线' }));
    await screen.findByText(comparisonRecord.comparisonHash);
    expect(api.compareSectionReference).toHaveBeenCalledWith(resultHash,{contract_hash:contractHash,curve_bundle_hash:bundleHash});
    fireEvent.click(screen.getByRole('button', { name: '下载原项目比较 JSON' }));
    expect(JSON.parse(await blobText(createdBlobs[0]))).toEqual(comparisonRecord);
    const curves={bundleHash:'4'.repeat(64),source:{sha256:resultHash},stages:[]};
    api.getSectionCurves.mockResolvedValue(curves);
    fireEvent.click(screen.getByRole('button', { name: '下载本结果数值曲线 JSON' }));
    await waitFor(()=>expect(createdBlobs).toHaveLength(2));
    expect(JSON.parse(await blobText(createdBlobs[1]))).toEqual(curves);
    expect(api.getSectionCurves).toHaveBeenCalledWith(resultHash);
    await waitFor(()=>expect(screen.getByRole('button',{name:'比较原项目数值曲线'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '运行冻结剖面' }));
    await screen.findByText('12.34 mm');
    expect(screen.queryByText(comparisonRecord.comparisonHash)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载原项目比较 JSON' })).not.toBeInTheDocument();
  });

  it('rejects a late comparison even if a new run returns the identical result hash', async () => {
    await readyForComparison();
    let finish!: (v:unknown)=>void;
    api.compareSectionReference.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    fireEvent.click(screen.getByRole('button',{name:'比较原项目数值曲线'}));
    fireEvent.click(screen.getByRole('button',{name:'运行冻结剖面'}));
    await screen.findByText('12.34 mm');
    await act(async()=>finish(comparisonRecord));
    expect(screen.queryByText(comparisonRecord.comparisonHash)).not.toBeInTheDocument();
  });

  it('rejects result curves bound to a different result hash', async () => {
    await readyForComparison();
    api.getSectionCurves.mockResolvedValue({bundleHash:'4'.repeat(64),source:{sha256:'wrong-result'}});
    fireEvent.click(screen.getByRole('button',{name:'下载本结果数值曲线 JSON'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('身份不一致');
    expect(createdBlobs).toHaveLength(0);
  });

  it('downloads unsealed editable conditions without deleting the original facts or replacing unknowns', async () => {
    await readyForComparison();
    fireEvent.click(screen.getByRole('button',{name:'下载条件 JSON'}));
    const saved=JSON.parse(await blobText(createdBlobs[0]));
    expect(saved.contractHash).toBeUndefined();
    expect(saved.parameters).toEqual(reference.contract.parameters);
    fireEvent.click(screen.getByRole('button',{name:'下载空曲线模板'}));
    expect(JSON.parse(await blobText(createdBlobs[1]))).toEqual(reference.curveTemplate);
    const confirmed={...reference,contract:{...reference.contract,contractHash:'5'.repeat(64)}};
    api.saveSectionReferenceContract.mockResolvedValue(confirmed);
    const file=new File([JSON.stringify(saved)],'confirmed.json');
    Object.defineProperty(file,'text',{value:async()=>JSON.stringify(saved)});
    fireEvent.change(screen.getByLabelText('上传已确认条件 JSON'),{target:{files:[file]}});
    await screen.findByText(confirmed.contract.contractHash);
    expect(api.saveSectionReferenceContract).toHaveBeenCalledWith(saved);
    expect(screen.queryByText(bundleHash)).not.toBeInTheDocument();
  });
});
