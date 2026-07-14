import type { ExcavationModel, GeologicalModel, ImportResult, Project, ProjectSummary, RetainingSystem, CalculationResult, VtuMesh, CheckResult, AssuranceResult, ConstructionObstacle, RebarIfcVisualization, PitTask, IssueCenterResult, CalculationTraceResult, RebarDetailingResult, RebarDesignScheme, DrawingSetManifest, BenchmarkCaseSpec, BenchmarkRunResult, CadTemplateConfig, AdvancedEngineeringSuite, MonitoringRecord, DrawingRevision, DrawingRuleSet, DrawingRuleValidation, DrawingRuleOptimization, StandardsProcessMatrix, OnlineDocumentation, IndustrialReadinessResult, MonitoringControlResult } from '../types/domain';

const CONFIGURED_API_BASE = import.meta.env.VITE_API_BASE_URL;
const API_BASE = CONFIGURED_API_BASE !== undefined
  ? CONFIGURED_API_BASE
  : (import.meta.env.DEV ? 'http://127.0.0.1:8002' : '');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120000);
  const externalSignal = init?.signal;
  const abortFromExternal = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener('abort', abortFromExternal, { once: true });
  }
  const signal = controller.signal;
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...init, signal });
  } catch (error) {
    if (controller.signal.aborted) throw new Error('请求超过 120 秒，已取消。请检查后台任务或网络状态。');
    throw error;
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener('abort', abortFromExternal);
  }
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/auth/login' && path !== '/api/auth/status') {
      window.dispatchEvent(new CustomEvent('pitguard:unauthorized', { detail: { requestPath: path } }));
    }
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      // ignore JSON parse failures
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export type AuthIdentity = { actor: string; role: string; authenticated: boolean; keyId?: string; username?: string; authMode?: string };

export const api = {
  authStatus: () => request<{ loginRequired: boolean; mode: string; sessionTtlSeconds: number }>('/api/auth/status'),
  login: (username: string, password: string) => request<{ authenticated: boolean; identity: AuthIdentity; expiresInSeconds: number }>('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) }),
  me: () => request<{ authenticated: boolean; identity: AuthIdentity }>('/api/auth/me'),
  logout: () => request<{ authenticated: boolean }>('/api/auth/logout', { method: 'POST' }),
  health: () => request<{ status: string; service: string }>('/health'),
  systemMetrics: () => request<Record<string, unknown>>('/api/system/metrics'),
  systemReadiness: () => request<Record<string, unknown>>('/api/system/readiness'),
  diagnostics: () => request<{ version: string; softwareVersion?: string; algorithmVersion?: string; ruleSetVersion?: string; exportSchemaVersion?: string; pythonVersion: string; databaseConfigured?: boolean; missingModules: string[]; modules: { importName: string; packageName: string; available: boolean; version?: string }[] }>('/api/system/diagnostics'),
  units: () => request<Record<string, any>>('/api/system/units'),
  getStandardsMatrix: () => request<StandardsProcessMatrix>('/api/standards/process-matrix'),
  getProjectStandardsMatrix: (projectId: string) => request<StandardsProcessMatrix>(`/api/projects/${projectId}/standards/process-matrix`),
  getDocumentation: () => request<OnlineDocumentation>('/api/documentation'),
  listProjects: () => request<ProjectSummary[]>('/api/projects'),
  createProject: (payload: { name: string; location?: string }) => request<Project>('/api/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  }),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  deleteProject: (id: string) => request<{ deleted: boolean; projectId: string; projectName: string; deletedTaskCount: number; deletedArtifactCount: number }>(`/api/projects/${id}`, { method: 'DELETE' }),
  updateProject: (id: string, payload: Partial<Project>, expectedRevision?: number, actor = 'web-user') => request<Project>(`/api/projects/${id}${expectedRevision == null ? `?actor=${encodeURIComponent(actor)}` : `?expectedRevision=${expectedRevision}&actor=${encodeURIComponent(actor)}`}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  getStorageRevision: (id: string) => request<{ projectId: string; revision: number }>(`/api/projects/${id}/storage-revision`),
  listStorageRevisions: (id: string, limit = 50) => request<Record<string, unknown>[]>(`/api/projects/${id}/storage-revisions?limit=${limit}`),
  listAuditEvents: (id: string, limit = 100) => request<Record<string, unknown>[]>(`/api/projects/${id}/audit-events?limit=${limit}`),
  restoreStorageRevision: (id: string, revision: number, actor = 'web-user') => request<Project>(`/api/projects/${id}/storage-revisions/${revision}/restore?actor=${encodeURIComponent(actor)}`, { method: 'POST' }),
  getGeometryConsistency: (id: string) => request<Record<string, unknown>>(`/api/projects/${id}/geometry-consistency`),
  getProjectDashboard: (id: string, mode = 'balanced') => request<Record<string, unknown>>(`/api/projects/${id}/dashboard?mode=${mode}`),
  getDesignSchemeLedger: (id: string, mode = 'balanced') => request<Record<string, unknown>>(`/api/projects/${id}/design-scheme-ledger?mode=${mode}`),
  getIntegratedRetainingCandidates: (id: string, mode = 'balanced', maxCandidates = 8) => request<Record<string, any>>(`/api/projects/${id}/expert-design/integrated-candidates?mode=${mode}&maxCandidates=${maxCandidates}`),
  applyIntegratedRetainingCandidate: (id: string, candidateId: string, mode = 'balanced', recalculate = true) => request<Record<string, any>>(`/api/projects/${id}/expert-design/apply-integrated-candidate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId, mode, recalculate }) }),
  importBoreholes: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<ImportResult>(`/api/projects/${projectId}/boreholes/import-csv`, { method: 'POST', body: form });
  },
  buildGeology: (projectId: string) => request<GeologicalModel>(`/api/projects/${projectId}/geology/build-model`, { method: 'POST' }),
  importVtu: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<VtuMesh>(`/api/projects/${projectId}/geology/import-vtu`, { method: 'POST', body: form });
  },
  createExcavation: (projectId: string, payload: { name: string; topElevation: number; bottomElevation: number; outline: { closed: boolean; points: {x:number;y:number}[] }; obstacles?: ConstructionObstacle[]; drawingLayers?: Record<string, unknown>[]; supportAxisOffset?: number; basementWallOffset?: number; explicitPlacement?: boolean; centeredOnGeology?: boolean; placementNote?: string; area?: number; perimeter?: number }) =>
    request<ExcavationModel>(`/api/projects/${projectId}/excavation`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  autoWall: (projectId: string) => request<RetainingSystem>(`/api/projects/${projectId}/design/auto-diaphragm-wall`, { method: 'POST' }),
  autoSupports: (projectId: string) => request<RetainingSystem>(`/api/projects/${projectId}/design/auto-supports`, { method: 'POST' }),
  importSupportLayoutCsv: (projectId: string, file: File, replace = true) => { const form = new FormData(); form.append('file', file); return request<Record<string, any>>(`/api/projects/${projectId}/design/import-support-layout?replace=${replace}`, { method: 'POST', body: form }); },
  autoRepairSupports: (projectId: string) => request<unknown>(`/api/projects/${projectId}/design/auto-repair-supports`, { method: 'POST' }),
  optimizeSupports: (projectId: string, payload?: { objectiveWeights?: Record<string, number>; preset?: string }) => request<unknown>(`/api/projects/${projectId}/design/optimize-supports`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload ?? {}) }),
  adoptSupportCandidate: (projectId: string, candidateId: string) => request<unknown>(`/api/projects/${projectId}/design/adopt-support-candidate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId }) }),
  lockSupportLines: (projectId: string, supportIds: string[], locked = true, reason?: string) => request<unknown>(`/api/projects/${projectId}/design/lock-support-lines`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ supportIds, locked, reason }) }),
  setSupportOptimizationLocks: (projectId: string, payload: { supportIds?: string[]; lockItems?: Record<string, unknown>[]; levelIndices?: number[]; obstacleIds?: string[]; locked?: boolean; reason?: string; replace?: boolean }) => request<unknown>(`/api/projects/${projectId}/design/lock-support-lines`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  buildCases: (projectId: string) => request<unknown[]>(`/api/projects/${projectId}/calculation/build-cases`, { method: 'POST' }),
  runCalculation: (projectId: string) => request<CalculationResult>(`/api/projects/${projectId}/calculation/run`, { method: 'POST' }),
  diagnoseAndRepairCalculation: (projectId: string) => request<Record<string, unknown>>(`/api/projects/${projectId}/calculation/diagnose-and-repair`, { method: 'POST' }),
  calculationAssurance: (projectId: string) => request<Record<string, unknown>>(`/api/projects/${projectId}/calculation/assurance`),
  releaseReadiness: (projectId: string, issueMode: 'review' | 'construction' = 'review') => request<Record<string, unknown>>(`/api/projects/${projectId}/export/release-readiness?issue_mode=${issueMode}`),
  runCandidateComparison: (projectId: string, topN = 3) => request<Record<string, unknown>[]>(`/api/projects/${projectId}/calculation/run-candidate-comparison?top_n=${topN}`, { method: 'POST' }),
  getChecks: (projectId: string) => request<{ checks: CheckResult[]; professionalReviewRequired: boolean }>(`/api/projects/${projectId}/calculation/checks`),
  getCalculationTrace: (projectId: string) => request<CalculationTraceResult>(`/api/projects/${projectId}/calculation/trace`),
  getWallLengthRedundancy: (projectId: string, mode = 'balanced') => request<Record<string, unknown>>(`/api/projects/${projectId}/wall-optimization/length-redundancy?mode=${mode}`),
  getExpertDesignReview: (projectId: string, mode = 'balanced') => request<Record<string, any>>(`/api/projects/${projectId}/expert-design/review?mode=${mode}`),
  getExpertDesignPipeline: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/expert-design/pipeline`),
  applyExpertVerticalWallLength: (projectId: string, candidateId: string, mode = 'balanced', recalculate = true) => request<Record<string, any>>(`/api/projects/${projectId}/expert-design/apply-vertical-wall-length`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId, mode, recalculate }) }),
  applyWallLengthCandidate: (projectId: string, candidateId: string, mode = 'balanced') => request<Record<string, unknown>>(`/api/projects/${projectId}/wall-optimization/apply-length-candidate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId, mode }) }),
  getAdvancedSuite: (projectId: string, mode = 'balanced') => request<AdvancedEngineeringSuite>(`/api/projects/${projectId}/advanced/suite?mode=${mode}`),
  getAdvancedTopology: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/topology?preview=true`),
  getCoordinationOptimization: (projectId: string, mode = 'balanced') => request<Record<string, any>>(`/api/projects/${projectId}/advanced/coordination-optimization?mode=${mode}`),
  applyCoordinationCandidate: (projectId: string, issueId: string, candidateId: string, mode = 'balanced') => request<Record<string, any>>(`/api/projects/${projectId}/advanced/coordination-optimization/apply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ issue_id: issueId, candidate_id: candidateId, mode }) }),
  getNodeSubmodels: (projectId: string, topN = 8) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/node-submodels?top_n=${topN}`),
  getCraneLogistics: (projectId: string, mode = 'balanced') => request<Record<string, any>>(`/api/projects/${projectId}/advanced/crane-logistics?mode=${mode}`),
  applyAdvancedTopology: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/topology/apply`, { method: 'POST' }),
  addMonitoringRecords: (projectId: string, records: MonitoringRecord[]) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/monitoring/records`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ records }) }),
  importMonitoringCsv: (projectId: string, file: File) => { const form = new FormData(); form.append('file', file); return request<Record<string, any>>(`/api/projects/${projectId}/advanced/monitoring/import-csv`, { method: 'POST', body: form }); },
  monitoringTemplateUrl: (projectId: string) => `${API_BASE}/api/projects/${projectId}/advanced/monitoring/template.csv`,
  calibrateMonitoring: (projectId: string, apply = false) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/monitoring/calibrate?apply=${apply}`, { method: 'POST' }),
  transitionReview: (projectId: string, payload: { role: string; actor: string; action: string; comment?: string }) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/review/transition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  addDrawingRevision: (projectId: string, payload: { description: string; sheetNumbers?: string[]; author: string; issueStatus?: string }) => request<DrawingRevision>(`/api/projects/${projectId}/advanced/revisions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  formalDrawingPackageUrl: (projectId: string, issueMode: 'review' | 'construction' = 'review', rebarMode: 'conservative' | 'balanced' | 'economic' = 'balanced') => `${API_BASE}/api/projects/${projectId}/export/formal-drawing-package?issue_mode=${issueMode}&rebar_mode=${rebarMode}`,
  coordinatedDeliveryPackageUrl: (projectId: string, issueMode: 'review' | 'construction' = 'review', rebarMode: 'conservative' | 'balanced' | 'economic' = 'balanced') => `${API_BASE}/api/projects/${projectId}/export/coordinated-delivery-package?issue_mode=${issueMode}&rebar_mode=${rebarMode}&include_ifc_profiles=true`,
  rebarDetailingPackageUrl: (projectId: string, mode: 'conservative' | 'balanced' | 'economic' = 'balanced') => `${API_BASE}/api/projects/${projectId}/export/rebar-detailing-package?mode=${mode}`,
  getAssurance: (projectId: string) => request<AssuranceResult>(`/api/projects/${projectId}/assurance/gap-analysis`),
  getIndustrialReadiness: (projectId: string, includeDetailing = false, runQualification = false) => request<IndustrialReadinessResult>(`/api/projects/${projectId}/industrial/readiness?includeDetailing=${includeDetailing}&runQualification=${runQualification}`),
  runIndustrialQualification: (projectId: string) => request<Record<string, unknown>>(`/api/projects/${projectId}/industrial/qualification`, { method: 'POST' }),
  runIndustrialClosure: (projectId: string) => request<IndustrialReadinessResult>(`/api/projects/${projectId}/industrial/closure`, { method: 'POST' }),
  getMonitoringControl: (projectId: string) => request<MonitoringControlResult>(`/api/projects/${projectId}/advanced/monitoring/control`),
  createTask: (projectId: string, operation: string, payload?: Record<string, unknown>) => request<PitTask>(`/api/projects/${projectId}/tasks`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation, payload: payload ?? {} }) }),
  createCandidateComparisonBatch: (projectId: string, topN = 3, useCache = true) => request<{ projectId: string; taskCount: number; tasks: PitTask[] }>(`/api/projects/${projectId}/tasks/candidate-comparison-batch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topN, useCache }) }),
  getTask: (taskId: string) => request<PitTask>(`/api/tasks/${taskId}`),
  cancelTask: (taskId: string) => request<PitTask>(`/api/tasks/${taskId}/cancel`, { method: 'POST' }),
  retryTask: (taskId: string) => request<PitTask>(`/api/tasks/${taskId}/retry`, { method: 'POST' }),
  getTaskMetrics: () => request<Record<string, unknown>>('/api/task-metrics'),
  taskDownloadUrl: (taskId: string) => `${API_BASE}/api/tasks/${taskId}/download`,
  listProjectTasks: (projectId: string) => request<PitTask[]>(`/api/projects/${projectId}/tasks`),
  getIssueCenter: (projectId: string) => request<IssueCenterResult>(`/api/projects/${projectId}/issues`),
  getIfcCheck: (projectId: string) => request<unknown>(`/api/projects/${projectId}/export/ifc-check`, { method: 'POST' }),
  getRebarIfcVisualization: (projectId: string, maxBars = 2400) => request<RebarIfcVisualization>(`/api/projects/${projectId}/export/ifc-rebar-visualization?max_bars=${maxBars}`),
  getRebarDetailing: (projectId: string, mode = 'balanced') => request<RebarDetailingResult>(`/api/projects/${projectId}/rebar/detailing?mode=${mode}`),
  getDeepDetailing: (projectId: string, mode = 'balanced') => request<Record<string, any>>(`/api/projects/${projectId}/rebar/deep-detailing?mode=${mode}`),
  getRebarDesignScheme: (projectId: string, mode = 'balanced') => request<RebarDesignScheme>(`/api/projects/${projectId}/rebar/design-scheme?mode=${mode}`),
  applyRebarDesignScheme: (projectId: string, mode = 'balanced', recalculate = true) => request<{ projectId: string; mode: string; scheme: RebarDesignScheme; retainingSystem: RetainingSystem; recalculated?: boolean }>(`/api/projects/${projectId}/rebar/apply-design-scheme`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, recalculate }) }),
  getDrawingSetManifest: (projectId: string) => request<DrawingSetManifest>(`/api/projects/${projectId}/export/drawings-manifest`),
  listDrawingRulePresets: () => request<{ schemaVersion: string; presets: { id: string; name: string; description?: string; parameters: Record<string, any>; objectiveWeights: Record<string, number>; ruleCount: number }[] }>(`/api/drawing-rules/presets`),
  getDrawingRules: (projectId: string) => request<{ ruleSet: DrawingRuleSet; validation: DrawingRuleValidation }>(`/api/projects/${projectId}/drawing-rules`),
  getDrawingIntelligence: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/drawing-rules/intelligence`),
  updateDrawingRules: (projectId: string, payload: DrawingRuleSet) => request<{ ruleSet: DrawingRuleSet; validation: DrawingRuleValidation; preview: DrawingSetManifest }>(`/api/projects/${projectId}/drawing-rules`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  validateDrawingRules: (projectId: string, payload: DrawingRuleSet) => request<DrawingRuleValidation & { normalized: DrawingRuleSet; preview?: DrawingSetManifest }>(`/api/projects/${projectId}/drawing-rules/validate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  previewDrawingRules: (projectId: string, scope: 'full' | 'general' | 'rebar' | 'details' = 'full') => request<DrawingSetManifest>(`/api/projects/${projectId}/drawing-rules/preview?scope=${scope}`),
  optimizeDrawingRules: (projectId: string, payload?: Record<string, unknown>) => request<DrawingRuleOptimization>(`/api/projects/${projectId}/drawing-rules/optimize`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload ?? {}) }),
  applyDrawingRulePreset: (projectId: string, preset: string) => request<{ ruleSet: DrawingRuleSet; preview: DrawingSetManifest; warnings: { path: string; message: string }[] }>(`/api/projects/${projectId}/drawing-rules/apply-preset/${encodeURIComponent(preset)}`, { method: 'POST' }),
  applyDrawingRuleCandidate: (projectId: string, candidateId: string, ruleSet?: DrawingRuleSet, optimization?: Record<string, unknown>) => request<{ applied: boolean; candidate: any; preview: DrawingSetManifest }>(`/api/projects/${projectId}/drawing-rules/apply-candidate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId, ruleSet, optimization: optimization ?? {} }) }),
  getCadTemplate: (projectId: string) => request<CadTemplateConfig>(`/api/projects/${projectId}/cad-template`),
  updateCadTemplate: (projectId: string, payload: Partial<CadTemplateConfig>) => request<CadTemplateConfig>(`/api/projects/${projectId}/cad-template`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  getCadTemplateValidation: (projectId: string) => request<Record<string, unknown>>(`/api/projects/${projectId}/cad-template/validation`),
  locateIssue: (projectId: string, issueId: string) => request<Record<string, unknown>>(`/api/projects/${projectId}/issues/locate/${issueId}`),
  wallLengthRedundancyReportUrl: (projectId: string, mode = 'balanced') => `${API_BASE}/api/projects/${projectId}/export/wall-length-redundancy?mode=${mode}`,
  designSchemeLedgerReportUrl: (projectId: string, mode = 'balanced') => `${API_BASE}/api/projects/${projectId}/export/design-scheme-ledger?mode=${mode}`,
  listBenchmarks: () => request<{ benchmarkVersion: string; cases: BenchmarkCaseSpec[] }>('/api/benchmarks'),
  runBenchmarks: (caseId?: string, persist = true) => request<BenchmarkRunResult>(`/api/benchmarks/run${caseId ? `?caseId=${encodeURIComponent(caseId)}&persist=${persist}` : `?persist=${persist}`}`, { method: 'POST' }),
  benchmarkPackageUrl: () => `${API_BASE}/api/benchmarks/export-package`,
  exportUrl: (projectId: string, kind: 'ifc' | 'ifc-light' | 'ifc-analysis' | 'ifc-construction-visual' | 'ifc-detailed' | 'drawings-cad' | 'drawings-svg' | 'report' | 'json' | 'design-scheme-ledger' | 'rebar-detailing-package' | 'coordinated-delivery-package') => `${API_BASE}/api/projects/${projectId}/export/${kind}`,
  cadPackageUrl: (projectId: string, scope: 'full' | 'general' | 'rebar' | 'details' = 'full', rebarMode: 'conservative' | 'balanced' | 'economic' = 'balanced', issueMode: 'review' | 'construction' = 'review') => `${API_BASE}/api/projects/${projectId}/export/drawings-cad?scope=${scope}&rebar_mode=${rebarMode}&issue_mode=${issueMode}`, 
  ifcCheckUrl: (projectId: string, mode: 'coordination_light' | 'analysis_model' | 'construction_visual' | 'design_detailed' = 'design_detailed') => `${API_BASE}/api/projects/${projectId}/export/ifc-check?mode=${mode}`
};
