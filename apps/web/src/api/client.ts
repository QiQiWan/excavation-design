import { requestActivityEvents } from '../app/GlobalRequestProgress';
import type { ExcavationModel, GeologicalModel, ImportResult, Project, ProjectSummary, RetainingSystem, CalculationResult, CalculationCase, ConstructionStageWorkspace, VtuMesh, CheckResult, AssuranceResult, ConstructionObstacle, RebarIfcVisualization, PitTask, IssueCenterResult, CalculationTraceResult, RebarDetailingResult, RebarDesignScheme, DrawingSetManifest, BenchmarkCaseSpec, BenchmarkRunResult, CadTemplateConfig, AdvancedEngineeringSuite, MonitoringRecord, DrawingRevision, DrawingRuleSet, DrawingRuleValidation, DrawingRuleOptimization, StandardsProcessMatrix, OnlineDocumentation, IndustrialReadinessResult, MonitoringControlResult } from '../types/domain';

const CONFIGURED_API_BASE = import.meta.env.VITE_API_BASE_URL;
const API_BASE = CONFIGURED_API_BASE !== undefined
  ? CONFIGURED_API_BASE
  : (import.meta.env.DEV ? 'http://127.0.0.1:8002' : '');

type RequestActivityOptions = {
  label?: string;
  expectedMs?: number;
  blocking?: boolean;
  quiet?: boolean;
  cacheTtlMs?: number;
  deduplicate?: boolean;
};

type RequestOptions = RequestInit & {
  timeoutMs?: number;
  timeoutMessage?: string;
  retryCount?: number;
  activity?: RequestActivityOptions;
};

type CacheEntry = { expiresAt: number; value: unknown };
const responseCache = new Map<string, CacheEntry>();
const inFlight = new Map<string, Promise<unknown>>();

function requestLabel(path: string, method: string): string {
  if (path.includes('/auth/login')) return '正在登录系统';
  if (path.includes('/auth/logout')) return '正在退出登录';
  if (path.includes('/auth/bootstrap') || path.includes('/auth/status') || path.includes('/auth/me')) return '正在验证登录状态';
  if (path === '/api/projects' && method === 'GET') return '正在刷新项目列表';
  if (path === '/api/projects' && method === 'POST') return '正在创建项目';
  if (/\/api\/projects\/[^/]+\?profile=workspace/.test(path)) return '正在打开项目工作区';
  if (/\/api\/projects\/[^/]+/.test(path) && ['PUT', 'PATCH'].includes(method)) return '正在保存项目修改';
  if (/\/api\/projects\/[^/]+/.test(path) && method === 'DELETE') return '正在删除项目';
  if (path.includes('/tasks') && method === 'POST') return '正在提交后台任务';
  if (path.includes('/import-')) return '正在上传并解析文件';
  if (path.includes('/export/')) return '正在准备工程成果';
  if (method === 'GET') return '正在加载数据';
  return '正在处理操作';
}

function expectedDuration(path: string, method: string): number {
  if (path.includes('/auth/')) return 1600;
  if (path === '/api/projects' && method === 'GET') return 900;
  if (path === '/api/projects' && method === 'POST') return 1600;
  if (path.includes('?profile=workspace')) return 1800;
  if (['PUT', 'PATCH', 'DELETE'].includes(method)) return 2200;
  if (path.includes('/tasks/')) return 700;
  return method === 'GET' ? 1200 : 2500;
}

function cacheTtl(path: string): number {
  if (path === '/api/auth/bootstrap' || path === '/api/auth/status') return 1800;
  if (path === '/api/system/diagnostics') return 60000;
  if (path === '/api/system/units') return 300000;
  if (path === '/api/projects') return 1200;
  return 0;
}

function requestKey(path: string, method: string, body: BodyInit | null | undefined): string {
  const bodyKey = typeof body === 'string' ? body : body ? '[binary]' : '';
  return `${method}:${path}:${bodyKey}`;
}


function formatStructuredApiError(status: number, statusText: string, payload: unknown): string {
  const root = payload && typeof payload === 'object' ? payload as Record<string, any> : {};
  const detail = root.detail && typeof root.detail === 'object' ? root.detail as Record<string, any> : root;
  if (String(detail.code ?? '') === 'PROJECT_FULL_LOAD_BLOCKED') {
    const payloadMb = Number(detail.payloadBytes ?? 0) / 1048576;
    const limitMb = Number(detail.limitBytes ?? 0) / 1048576;
    const sizeText = payloadMb > 0 && limitMb > 0 ? `（${payloadMb.toFixed(1)} MB / 限值 ${limitMb.toFixed(1)} MB）` : '';
    const needsCompaction = Boolean(detail.compactionRecommended);
    return `完整快照当前不进入 API 进程${sizeText}。网页继续使用轻量工作区，完整计算与导出由独立 worker 按实时内存余量执行${needsCompaction ? '；当前工作区或历史数据存在冗余，建议运行“优化项目存储”' : ''}。`;
  }
  if (String(detail.code ?? '') === 'CONSTRUCTION_STAGE_VALIDATION_FAILED') {
    const issues = Array.isArray(detail.validation?.issues) ? detail.validation.issues : [];
    const actions = issues
      .filter((item: Record<string, any>) => String(item.severity) === 'fail')
      .slice(0, 6)
      .map((item: Record<string, any>, index: number) => `${index + 1}. ${String(item.message ?? item.code)}；${String(item.action ?? '请修正后重试')}`);
    return `[CONSTRUCTION_STAGE_VALIDATION_FAILED] ${String(detail.message ?? '施工阶段存在硬错误，未保存。')}${actions.length ? ` ${actions.join(' ')}` : ''}`;
  }
  const message = typeof root.detail === 'string'
    ? root.detail
    : String(detail.message ?? root.message ?? `${status} ${statusText}`);
  const recommendation = detail.recommendation ?? root.recommendation;
  const code = detail.code ?? root.code;
  return `${code ? `[${String(code)}] ` : ''}${message}${recommendation ? `；建议：${String(recommendation)}` : ''}`;
}

function dispatchActivity(name: string, detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

async function executeRequest<T>(path: string, init: RequestOptions, activityId: string, activity: Required<Pick<RequestActivityOptions, 'label' | 'expectedMs' | 'blocking' | 'quiet'>>): Promise<T> {
  const method = String(init.method ?? 'GET').toUpperCase();
  const timeoutMs = Math.max(1000, init.timeoutMs ?? 30000);
  const retryCount = Math.max(0, Math.min(init.retryCount ?? (method === 'GET' ? 1 : 0), 3));
  const externalSignal = init.signal;
  const { timeoutMs: _timeoutMs, timeoutMessage, retryCount: _retryCount, activity: _activity, signal: _signal, ...fetchInit } = init;
  const headers = new Headers(fetchInit.headers);
  headers.set('X-PitGuard-Client-Request-Id', activityId);
  const transientStatuses = new Set([429, 502, 503, 504]);

  for (let attempt = 0; attempt <= retryCount; attempt += 1) {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    const abortFromExternal = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', abortFromExternal, { once: true });
    }
    let response: Response;
    try {
      dispatchActivity(requestActivityEvents.phase, { id: activityId, phase: attempt ? `服务器暂时繁忙，正在第 ${attempt} 次自动重试` : '正在连接服务器' });
      response = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...fetchInit, headers, signal: controller.signal });
      if (transientStatuses.has(response.status) && attempt < retryCount) {
        window.clearTimeout(timeout);
        externalSignal?.removeEventListener('abort', abortFromExternal);
        await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
        continue;
      }
      dispatchActivity(requestActivityEvents.phase, { id: activityId, phase: '正在接收并整理数据' });
    } catch (error) {
      window.clearTimeout(timeout);
      externalSignal?.removeEventListener('abort', abortFromExternal);
      if (externalSignal?.aborted && !timedOut) throw new Error('请求已取消。');
      if (timedOut) throw new Error(timeoutMessage ?? `请求超过 ${Math.round(timeoutMs / 1000)} 秒，后端可能正在恢复或不可用。`);
      if (attempt < retryCount) {
        await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
        continue;
      }
      throw new Error(error instanceof Error ? error.message : '网络请求失败');
    } finally {
      window.clearTimeout(timeout);
      externalSignal?.removeEventListener('abort', abortFromExternal);
    }
    if (!response.ok) {
      if (response.status === 401 && path !== '/api/auth/login' && path !== '/api/auth/bootstrap' && path !== '/api/auth/status') {
        window.dispatchEvent(new CustomEvent('pitguard:unauthorized', { detail: { requestPath: path } }));
      }
      let message = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        message = formatStructuredApiError(response.status, response.statusText, data);
      } catch {
        // Keep the HTTP status when the proxy returned HTML/plain text.
      }
      const serverRequestId = response.headers.get('X-PitGuard-Request-Id');
      throw new Error(serverRequestId ? `${message}（请求追踪号：${serverRequestId}）` : message);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  throw new Error('请求重试次数已耗尽。');
}

function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const options = init ?? {};
  const method = String(options.method ?? 'GET').toUpperCase();
  const key = requestKey(path, method, options.body);
  const ttl = options.activity?.cacheTtlMs ?? (method === 'GET' ? cacheTtl(path) : 0);
  if (ttl > 0) {
    const cached = responseCache.get(key);
    if (cached && cached.expiresAt > Date.now()) return Promise.resolve(cached.value as T);
  }
  const deduplicate = options.activity?.deduplicate ?? (method === 'GET' || ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method));
  const existing = deduplicate ? inFlight.get(key) : undefined;
  if (existing) return existing as Promise<T>;

  const activityId = `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const activity = {
    label: options.activity?.label ?? requestLabel(path, method),
    expectedMs: options.activity?.expectedMs ?? expectedDuration(path, method),
    blocking: options.activity?.blocking ?? method !== 'GET',
    quiet: options.activity?.quiet ?? (path.includes('/api/tasks/') && method === 'GET'),
  };
  const startedAt = Date.now();
  dispatchActivity(requestActivityEvents.start, { id: activityId, method, path, startedAt, ...activity });
  const promise = executeRequest<T>(path, options, activityId, activity)
    .then((value) => {
      if (ttl > 0) responseCache.set(key, { expiresAt: Date.now() + ttl, value });
      dispatchActivity(requestActivityEvents.end, { id: activityId, method, path, startedAt, ...activity, ok: true });
      return value;
    })
    .catch((error) => {
      dispatchActivity(requestActivityEvents.end, { id: activityId, method, path, startedAt, ...activity, ok: false, error: error instanceof Error ? error.message : String(error) });
      throw error;
    })
    .finally(() => { inFlight.delete(key); });
  if (deduplicate) inFlight.set(key, promise);
  return promise;
}

export function invalidateApiCache(prefix = '') {
  for (const key of responseCache.keys()) {
    if (!prefix || key.includes(prefix)) responseCache.delete(key);
  }
}


export type AuthIdentity = { actor: string; role: string; authenticated: boolean; keyId?: string; username?: string; authMode?: string };

export const api = {
  authBootstrap: async () => {
    const options = { timeoutMs: 5000, timeoutMessage: '登录服务 5 秒内未响应。系统已进入离线恢复页。', activity: { label: '正在恢复登录会话', expectedMs: 1000, cacheTtlMs: 0 } };
    const bootstrap = await request<{ loginRequired?: boolean; mode?: string; sessionTtlSeconds?: number; authenticated?: boolean; identity?: AuthIdentity }>('/api/auth/bootstrap', options);
    if (typeof bootstrap.loginRequired === 'boolean') return {
      loginRequired: bootstrap.loginRequired,
      mode: bootstrap.mode ?? 'session',
      sessionTtlSeconds: bootstrap.sessionTtlSeconds ?? 0,
      authenticated: Boolean(bootstrap.authenticated),
      identity: bootstrap.identity,
    };
    // Rolling-upgrade compatibility with pre-V3.32 API nodes.
    const status = await request<{ loginRequired: boolean; mode: string; sessionTtlSeconds: number }>('/api/auth/status', { ...options, activity: { ...options.activity, quiet: true, cacheTtlMs: 0 } });
    if (!status.loginRequired) return { ...status, authenticated: false, identity: undefined };
    try {
      const current = await request<{ authenticated: boolean; identity: AuthIdentity }>('/api/auth/me', { ...options, activity: { ...options.activity, quiet: true, cacheTtlMs: 0 } });
      return { ...status, authenticated: current.authenticated, identity: current.identity };
    } catch { return { ...status, authenticated: false, identity: undefined }; }
  },
  authStatus: () => request<{ loginRequired: boolean; mode: string; sessionTtlSeconds: number }>('/api/auth/status', { timeoutMs: 5000, timeoutMessage: '登录服务 5 秒内未响应。系统已进入离线恢复页。', activity: { cacheTtlMs: 0 } }),
  login: (username: string, password: string) => request<{ authenticated: boolean; identity: AuthIdentity; expiresInSeconds: number }>('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }), timeoutMs: 10000 }),
  me: () => request<{ authenticated: boolean; identity: AuthIdentity }>('/api/auth/me', { timeoutMs: 5000 }),
  logout: () => request<{ authenticated: boolean }>('/api/auth/logout', { method: 'POST', timeoutMs: 5000 }),
  health: () => request<{ status: string; service: string }>('/health', { timeoutMs: 4000 }),
  systemMetrics: () => request<Record<string, unknown>>('/api/system/metrics'),
  systemReadiness: () => request<Record<string, unknown>>('/api/system/readiness'),
  resourcePolicy: () => request<Record<string, unknown>>('/api/system/resource-policy', { timeoutMs: 5000 }),
  diagnostics: () => request<{ version: string; softwareVersion?: string; algorithmVersion?: string; ruleSetVersion?: string; exportSchemaVersion?: string; pythonVersion: string; databaseConfigured?: boolean; missingModules: string[]; modules: { importName: string; packageName: string; available: boolean; version?: string }[] }>('/api/system/diagnostics'),
  units: () => request<Record<string, any>>('/api/system/units'),
  getStandardsMatrix: () => request<StandardsProcessMatrix>('/api/standards/process-matrix'),
  getProjectStandardsMatrix: (projectId: string) => request<StandardsProcessMatrix>(`/api/projects/${projectId}/standards/process-matrix`),
  getDocumentation: () => request<OnlineDocumentation>('/api/documentation'),
  listProjects: (force = false) => { if (force) invalidateApiCache('GET:/api/projects'); return request<ProjectSummary[]>('/api/projects', { activity: { cacheTtlMs: force ? 0 : 1200, label: force ? '正在刷新项目列表' : '正在加载项目列表' } }); },
  createProject: (payload: { name: string; location?: string }) => request<Project>('/api/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), activity: { label: '正在创建项目', expectedMs: 1200 }
  }).then((project) => { invalidateApiCache('GET:/api/projects'); return project; }),
  getProject: (id: string) => request<Project>(`/api/projects/${id}?profile=workspace`, { timeoutMs: 20000, timeoutMessage: '项目工作区 20 秒内未加载完成。后端已阻止全量大对象进入 API；请检查项目存储健康状态。' }),
  getProjectStorageHealth: (id: string) => request<Record<string, unknown>>(`/api/projects/${id}/storage-health`, { timeoutMs: 15000, retryCount: 0 }),
  getCoreDesignStatus: (id: string) => request<Record<string, any>>(`/api/projects/${id}/design/core-status`, { timeoutMs: 15000, retryCount: 0 }),
  listProjectArtifacts: (id: string, kind?: string) => request<{ projectId: string; artifactCount: number; storedBytes: number; logicalBytes: number; artifacts: { artifactId: string; kind: string; logicalBytes?: number; storedBytes?: number; itemCount?: number; available?: boolean; metadata?: Record<string, unknown> }[] }>(`/api/projects/${id}/artifacts${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`, { timeoutMs: 30000, retryCount: 0 }),
  projectArtifactDownloadUrl: (id: string, artifactId: string) => `${API_BASE}/api/projects/${id}/artifacts/${artifactId}/download`,
  getCalculationStageChunks: (id: string, resultId: string) => request<Record<string, unknown>>(`/api/projects/${id}/calculation-results/${resultId}/stage-chunks`),
  getCalculationStageChunk: (id: string, resultId: string, chunkIndex: number) => request<Record<string, unknown>[]>(`/api/projects/${id}/calculation-results/${resultId}/stage-chunks/${chunkIndex}`, { timeoutMs: 15000 }),
  deleteProject: (id: string) => request<{ deleted: boolean; projectId: string; projectName: string; deletedTaskCount: number; deletedArtifactCount: number }>(`/api/projects/${id}`, { method: 'DELETE', activity: { label: '正在删除项目', expectedMs: 1400 } }).then((result) => { invalidateApiCache('GET:/api/projects'); return result; }),
  updateProject: (id: string, payload: Partial<Project>, expectedRevision?: number, actor = 'web-user') => request<Project>(`/api/projects/${id}/workspace${expectedRevision == null ? `?actor=${encodeURIComponent(actor)}` : `?expectedRevision=${expectedRevision}&actor=${encodeURIComponent(actor)}`}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), activity: { label: '正在保存项目修改', expectedMs: 1600 } }).then((project) => { invalidateApiCache(`GET:/api/projects/${id}`); invalidateApiCache('GET:/api/projects'); return project; }),
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
  getDesignWorkspaceBootstrap: (projectId: string, refresh = false) => request<Record<string, any>>(`/api/projects/${projectId}/design/workspace-bootstrap${refresh ? '?refresh=true' : ''}`, { timeoutMs: 30000, retryCount: 0, activity: { label: '正在装配围护设计工作区', expectedMs: 2500, cacheTtlMs: refresh ? 0 : 15000, deduplicate: true } }),
  getDesignQualification: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/design/qualification`, { timeoutMs: 30000, retryCount: 0 }),
  getProgressiveDesign: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/design/progressive`, { timeoutMs: 30000, retryCount: 0 }),
  updateProgressiveDesign: (projectId: string, payload: Record<string, unknown>) => request<Record<string, any>>(`/api/projects/${projectId}/design/progressive`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), activity: { label: '正在保存渐进式设计配置', expectedMs: 1000 } }),
  getSupportCandidatePreviews: (projectId: string, limit = 12) => request<{ projectId: string; source: string; previews: { candidateId?: string; rank?: number; planGeometry?: Record<string, any> }[] }>(`/api/projects/${projectId}/design/candidate-previews?limit=${Math.max(1, Math.min(limit, 20))}`, { timeoutMs: 12000 }),
  getSupportSystemOptions: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/design/system-options`, { timeoutMs: 8000 }),
  getPlanShapeDiagnostics: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/design/plan-shape-diagnostics`, { timeoutMs: 30000, retryCount: 0 }),
  getSupportDesignerAudit: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/design/support-designer-audit`, { timeoutMs: 60000, retryCount: 0 }),
  getSupportDeepDesign: (projectId: string, includeMembers = false) => request<Record<string, any>>(`/api/projects/${projectId}/design/support-deep-design?include_members=${includeMembers ? 'true' : 'false'}`, { timeoutMs: 60000, retryCount: 0 }),
  optimizeSupportDeepDesign: (projectId: string, maxIterations = 3) => request<Record<string, any>>(`/api/projects/${projectId}/design/support-deep-design/optimize`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ maxIterations }), activity: { label: '正在迭代支撑截面、稳定和临时立柱', expectedMs: 4500 } }),
  getCalculationResourceEstimate: (projectId: string, candidateCount = 0) => request<Record<string, any>>(`/api/projects/${projectId}/design/calculation-resource-estimate?candidate_count=${candidateCount}`, { timeoutMs: 30000, retryCount: 0 }),
  autoSupportsByShape: (projectId: string) => request<{ diagnostics: Record<string, any>; selectedTopologyFamily: string; retainingSystem: RetainingSystem }>(`/api/projects/${projectId}/design/auto-supports-by-shape`, { method: 'POST' }),
  importSupportLayoutCsv: (projectId: string, file: File, replace = true) => { const form = new FormData(); form.append('file', file); return request<Record<string, any>>(`/api/projects/${projectId}/design/import-support-layout?replace=${replace}`, { method: 'POST', body: form }); },
  autoRepairSupports: (projectId: string) => request<unknown>(`/api/projects/${projectId}/design/auto-repair-supports`, { method: 'POST' }),
  optimizeSupports: (projectId: string, payload?: { objectiveWeights?: Record<string, number>; preset?: string; topologyFamily?: string }) => request<unknown>(`/api/projects/${projectId}/design/optimize-supports`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload ?? {}) }),
  adoptSupportCandidate: (projectId: string, candidateId: string) => request<unknown>(`/api/projects/${projectId}/design/adopt-support-candidate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId }) }),
  lockSupportLines: (projectId: string, supportIds: string[], locked = true, reason?: string) => request<unknown>(`/api/projects/${projectId}/design/lock-support-lines`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ supportIds, locked, reason }) }),
  setSupportOptimizationLocks: (projectId: string, payload: { supportIds?: string[]; lockItems?: Record<string, unknown>[]; levelIndices?: number[]; obstacleIds?: string[]; locked?: boolean; reason?: string; replace?: boolean }) => request<unknown>(`/api/projects/${projectId}/design/lock-support-lines`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  buildCases: (projectId: string) => request<unknown[]>(`/api/projects/${projectId}/calculation/build-cases`, { method: 'POST' }),
  getConstructionStages: (projectId: string) => request<ConstructionStageWorkspace>(`/api/projects/${projectId}/calculation/construction-stages`, { activity: { cacheTtlMs: 0 } }),
  saveConstructionStages: (projectId: string, calculationCase: CalculationCase) => request<ConstructionStageWorkspace>(`/api/projects/${projectId}/calculation/construction-stages`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(calculationCase) }),
  resetConstructionStages: (projectId: string) => request<ConstructionStageWorkspace>(`/api/projects/${projectId}/calculation/construction-stages/reset`, { method: 'POST' }),
  getLatestCalculationEvidence: (projectId: string) => request<{ projectId: string; evidence: Record<string, unknown>; result?: CalculationResult }>(`/api/projects/${projectId}/calculation/latest-evidence`, { timeoutMs: 30000, retryCount: 1 }),
  runCalculation: (projectId: string) => request<CalculationResult>(`/api/projects/${projectId}/calculation/run`, { method: 'POST' }),
  diagnoseAndRepairCalculation: (projectId: string) => request<Record<string, unknown>>(`/api/projects/${projectId}/calculation/diagnose-and-repair`, { method: 'POST' }),
  applyCalculationClosureAction: (projectId: string, payload: { actionId: string; value?: unknown; strategy?: string; maxIterations?: number }) => request<Record<string, unknown>>(`/api/projects/${projectId}/calculation/intelligent-closure/action`, { method: 'POST', body: JSON.stringify(payload) }),
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
  getTask: (taskId: string) => request<PitTask>(`/api/tasks/${taskId}`, { timeoutMs: 10000, activity: { quiet: true, cacheTtlMs: 0 } }),
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
  applyRebarDesignScheme: (projectId: string, mode = 'balanced', recalculate = true) => request<{ projectId: string; mode: string; scheme: RebarDesignScheme; retainingSystem: RetainingSystem; recalculated?: boolean; recalculationQueued?: boolean; calculationTask?: PitTask }>(`/api/projects/${projectId}/rebar/apply-design-scheme`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, recalculate }) }),
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
