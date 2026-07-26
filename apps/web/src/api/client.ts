import { requestActivityEvents } from '../app/GlobalRequestProgress';
import type { ExcavationModel, GeologicalModel, ImportResult, Project, ProjectSummary, RetainingSystem, CalculationResult, CalculationCase, ConstructionStageWorkspace, VtuMesh, CheckResult, AssuranceResult, ConstructionObstacle, RebarIfcVisualization, PitTask, IssueCenterResult, CalculationTraceResult, RebarDetailingResult, RebarDesignScheme, DrawingSetManifest, BenchmarkCaseSpec, BenchmarkRunResult, CadTemplateConfig, AdvancedEngineeringSuite, MonitoringRecord, DrawingRevision, DrawingRuleSet, DrawingRuleValidation, DrawingRuleOptimization, StandardsProcessMatrix, OnlineDocumentation, IndustrialReadinessResult, MonitoringControlResult, BusinessWorkflowOverview, DesignControlStage, DesignScenario, ConstructionPlanStage, FieldExecutionSnapshot, DeviationEvent, DesignCoreOverview, ParameterGovernance, ExternalCollaborationRecord, DesignReviewRequestRecord } from '../types/domain';

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
  responseIdleTimeoutMs?: number;
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
  if (/\/api\/projects\/[^/]+\?profile=(shell|workspace)/.test(path)) return '正在打开项目工作区';
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
  if (path.includes('?profile=shell') || path.includes('?profile=workspace')) return 1800;
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

function formatTransferBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(0)} KB`;
  return `${(value / 1048576).toFixed(value >= 10485760 ? 1 : 2)} MB`;
}

async function readResponsePayload<T>(
  response: Response,
  controller: AbortController,
  activityId: string,
  idleTimeoutMs: number,
): Promise<T> {
  if (response.status === 204) return undefined as T;
  const contentType = String(response.headers.get('Content-Type') ?? '').toLowerCase();
  if (!response.body) {
    dispatchActivity(requestActivityEvents.phase, { id: activityId, phase: '正在解析返回数据', progress: 96 });
    return (contentType.includes('json') ? response.json() : response.text()) as Promise<T>;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const textParts: string[] = [];
  const explicitLength = Math.max(0, Number(response.headers.get('X-PitGuard-Response-Uncompressed-Bytes') ?? 0));
  const contentEncoding = String(response.headers.get('Content-Encoding') ?? '').toLowerCase();
  const declaredLength = explicitLength || (contentEncoding ? 0 : Math.max(0, Number(response.headers.get('Content-Length') ?? 0)));
  let received = 0;
  let idleTimer = 0;
  const resetIdleTimer = () => {
    window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => controller.abort(), idleTimeoutMs);
  };

  try {
    resetIdleTimer();
    while (true) {
      const { done, value } = await reader.read();
      resetIdleTimer();
      if (done) break;
      if (!value?.byteLength) continue;
      textParts.push(decoder.decode(value, { stream: true }));
      received += value.byteLength;
      const ratio = declaredLength > 0 ? Math.min(1, received / declaredLength) : 0;
      const progress = declaredLength > 0 ? 42 + ratio * 52 : Math.min(94, 42 + Math.log2(Math.max(2, received / 65536 + 1)) * 8);
      const sizeText = declaredLength > 0
        ? `${formatTransferBytes(received)} / ${formatTransferBytes(declaredLength)}`
        : formatTransferBytes(received);
      dispatchActivity(requestActivityEvents.phase, {
        id: activityId,
        phase: `正在接收数据 ${sizeText}`,
        progress: Math.min(96, progress),
      });
    }
  } finally {
    window.clearTimeout(idleTimer);
    reader.releaseLock();
  }

  dispatchActivity(requestActivityEvents.phase, {
    id: activityId,
    phase: `正在解析数据 ${formatTransferBytes(received)}`,
    progress: 97,
  });
  textParts.push(decoder.decode());
  const text = textParts.join('');
  if (!contentType.includes('json')) return text as T;
  return JSON.parse(text) as T;
}

async function executeRequest<T>(path: string, init: RequestOptions, activityId: string, activity: Required<Pick<RequestActivityOptions, 'label' | 'expectedMs' | 'blocking' | 'quiet'>>): Promise<T> {
  const method = String(init.method ?? 'GET').toUpperCase();
  const timeoutMs = Math.max(1000, init.timeoutMs ?? 30000);
  const idleTimeoutMs = Math.max(4000, init.responseIdleTimeoutMs ?? Math.min(timeoutMs, 20000));
  const retryCount = Math.max(0, Math.min(init.retryCount ?? (method === 'GET' ? 1 : 0), 3));
  const externalSignal = init.signal;
  const { timeoutMs: _timeoutMs, timeoutMessage, retryCount: _retryCount, responseIdleTimeoutMs: _idleTimeoutMs, activity: _activity, signal: _signal, ...fetchInit } = init;
  const headers = new Headers(fetchInit.headers);
  headers.set('X-PitGuard-Client-Request-Id', activityId);
  headers.set('Accept', headers.get('Accept') || 'application/json');
  const transientStatuses = new Set([408, 425, 429, 500, 502, 503, 504]);

  for (let attempt = 0; attempt <= retryCount; attempt += 1) {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    const abortFromExternal = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', abortFromExternal, { once: true });
    }
    try {
      dispatchActivity(requestActivityEvents.phase, {
        id: activityId,
        phase: attempt ? `服务器暂时繁忙，正在第 ${attempt} 次自动重试` : '正在连接服务器',
        progress: attempt ? 10 : 6,
      });
      const response = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...fetchInit, headers, signal: controller.signal });
      if (transientStatuses.has(response.status) && attempt < retryCount) {
        await response.body?.cancel().catch(() => undefined);
        window.clearTimeout(timeout);
        externalSignal?.removeEventListener('abort', abortFromExternal);
        const retryAfter = Number(response.headers.get('Retry-After') ?? 0);
        const backoff = retryAfter > 0 ? retryAfter * 1000 : Math.min(2400, 300 * (2 ** attempt) + Math.random() * 180);
        await new Promise((resolve) => window.setTimeout(resolve, backoff));
        continue;
      }
      dispatchActivity(requestActivityEvents.phase, { id: activityId, phase: '服务器已响应，正在接收数据', progress: 38 });
      const payload = await readResponsePayload<unknown>(response, controller, activityId, idleTimeoutMs);
      if (!response.ok) {
        if (response.status === 401 && path !== '/api/auth/login' && path !== '/api/auth/bootstrap' && path !== '/api/auth/status') {
          window.dispatchEvent(new CustomEvent('pitguard:unauthorized', { detail: { requestPath: path } }));
        }
        let message = `${response.status} ${response.statusText}`;
        if (payload && typeof payload === 'object') message = formatStructuredApiError(response.status, response.statusText, payload);
        else if (typeof payload === 'string' && payload.trim()) message = payload.trim().slice(0, 1000);
        const serverRequestId = response.headers.get('X-PitGuard-Request-Id');
        const httpError = new Error(serverRequestId ? `${message}（请求追踪号：${serverRequestId}）` : message) as Error & { nonRetryable?: boolean };
        httpError.nonRetryable = true;
        throw httpError;
      }
      dispatchActivity(requestActivityEvents.phase, { id: activityId, phase: '数据已就绪', progress: 99 });
      return payload as T;
    } catch (error) {
      if (externalSignal?.aborted && !timedOut) throw new Error('请求已取消。');
      if (timedOut) throw new Error(timeoutMessage ?? `请求超过 ${Math.round(timeoutMs / 1000)} 秒，已停止等待；页面其他功能仍可继续使用。`);
      if ((error as Error & { nonRetryable?: boolean })?.nonRetryable) throw error;
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new Error(`数据流连续 ${Math.round(idleTimeoutMs / 1000)} 秒没有进展，已中止本次读取。`);
      }
      if (attempt < retryCount) {
        const backoff = Math.min(2400, 300 * (2 ** attempt) + Math.random() * 180);
        await new Promise((resolve) => window.setTimeout(resolve, backoff));
        continue;
      }
      throw new Error(error instanceof Error ? error.message : '网络请求失败');
    } finally {
      window.clearTimeout(timeout);
      externalSignal?.removeEventListener('abort', abortFromExternal);
    }
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
  // GET can safely share an in-flight response. Mutations may carry different
  // files or project revisions even when URL and serialized body appear equal.
  const deduplicate = options.activity?.deduplicate ?? method === 'GET';
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
      if (method !== 'GET') responseCache.clear();
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
  systemReadiness: () => request<Record<string, unknown>>('/api/system/readiness', { timeoutMs: 6000, retryCount: 1, activity: { quiet: true, cacheTtlMs: 0, deduplicate: true } }),
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
  getProject: (id: string, signal?: AbortSignal) => request<Project>(`/api/projects/${id}?profile=shell`, { signal, timeoutMs: 45000, responseIdleTimeoutMs: 15000, timeoutMessage: '项目工作区 45 秒内未加载完成。页面已停止等待，请检查云端带宽、项目工作区大小和后端慢请求日志。', activity: { quiet: true, cacheTtlMs: 0, deduplicate: true } }),
  getProjectStorageHealth: (id: string, signal?: AbortSignal) => request<Record<string, unknown>>(`/api/projects/${id}/storage-health`, { signal, timeoutMs: 15000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 5000, deduplicate: true } }),
  getCoreDesignStatus: (id: string, stage?: 'basis' | 'input' | 'scheme' | 'calculation' | 'reinforcement' | 'deliverables', detail: 'summary' | 'stage' | 'full' = stage ? 'stage' : 'summary', signal?: AbortSignal) => { const query = new URLSearchParams({ detail }); if (stage) query.set('stage', stage); return request<Record<string, any>>(`/api/projects/${id}/design/core-status?${query.toString()}`, { signal, timeoutMs: detail === 'full' || stage === 'calculation' ? 60000 : 30000, responseIdleTimeoutMs: 18000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 5000, deduplicate: true } }); },
  listProjectArtifacts: (id: string, kind?: string) => request<{ projectId: string; artifactCount: number; storedBytes: number; logicalBytes: number; artifacts: { artifactId: string; kind: string; logicalBytes?: number; storedBytes?: number; itemCount?: number; available?: boolean; metadata?: Record<string, unknown> }[] }>(`/api/projects/${id}/artifacts${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`, { timeoutMs: 30000, retryCount: 0 }),
  projectArtifactDownloadUrl: (id: string, artifactId: string) => `${API_BASE}/api/projects/${id}/artifacts/${artifactId}/download`,
  getCalculationStageChunks: (id: string, resultId: string) => request<Record<string, unknown>>(`/api/projects/${id}/calculation-results/${resultId}/stage-chunks`, { timeoutMs: 15000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 0, deduplicate: true } }),
  getCalculationStageChunk: (id: string, resultId: string, chunkIndex: number) => request<Record<string, unknown>[]>(`/api/projects/${id}/calculation-results/${resultId}/stage-chunks/${chunkIndex}`, { timeoutMs: 15000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 0, deduplicate: true } }),
  deleteProject: (id: string) => request<{ deleted: boolean; projectId: string; projectName: string; deletedTaskCount: number; deletedArtifactCount: number }>(`/api/projects/${id}`, { method: 'DELETE', activity: { label: '正在删除项目', expectedMs: 1400 } }).then((result) => { invalidateApiCache('GET:/api/projects'); return result; }),
  updateProject: (id: string, payload: Partial<Project>, expectedRevision?: number, actor = 'web-user') => request<Project>(`/api/projects/${id}/workspace${expectedRevision == null ? `?actor=${encodeURIComponent(actor)}` : `?expectedRevision=${expectedRevision}&actor=${encodeURIComponent(actor)}`}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), activity: { label: '正在保存项目修改', expectedMs: 1600 } }).then((project) => { invalidateApiCache(`GET:/api/projects/${id}`); invalidateApiCache('GET:/api/projects'); return project; }),
  getStorageRevision: (id: string) => request<{ projectId: string; revision: number }>(`/api/projects/${id}/storage-revision`),
  listStorageRevisions: (id: string, limit = 50) => request<Record<string, unknown>[]>(`/api/projects/${id}/storage-revisions?limit=${limit}`),
  listAuditEvents: (id: string, limit = 100) => request<Record<string, unknown>[]>(`/api/projects/${id}/audit-events?limit=${limit}`),
  restoreStorageRevision: (id: string, revision: number, actor = 'web-user') => request<Project>(`/api/projects/${id}/storage-revisions/${revision}/restore?actor=${encodeURIComponent(actor)}`, { method: 'POST' }),
  getGeometryConsistency: (id: string) => request<Record<string, unknown>>(`/api/projects/${id}/geometry-consistency`),
  getProjectDashboard: (id: string, mode = 'balanced') => request<Record<string, unknown>>(`/api/projects/${id}/dashboard?mode=${mode}`),
  getDesignSchemeLedger: (id: string, mode = 'balanced') => request<Record<string, unknown>>(`/api/projects/${id}/design-scheme-ledger?mode=${mode}`),
  getDesignCoreBundle: (id: string) => request<{ schema: string; overview: DesignCoreOverview; parameters: ParameterGovernance; rules: Record<string, any>; schemes: Record<string, any>; reinforcement: Record<string, any>; delivery: Record<string, any>; collaboration: { records: ExternalCollaborationRecord[]; reviewRequests: DesignReviewRequestRecord[]; boundary: string } }>(`/api/projects/${id}/design-core/bundle`, { timeoutMs: 20000, retryCount: 0, activity: { label: '正在加载设计主流程与质量证据', cacheTtlMs: 0 } }),
  getDesignCoreOverview: (id: string) => request<DesignCoreOverview>(`/api/projects/${id}/design-core`, { timeoutMs: 15000, retryCount: 0, activity: { label: '正在加载设计质量与追溯证据', cacheTtlMs: 0 } }),
  getParameterGovernance: (id: string) => request<ParameterGovernance>(`/api/projects/${id}/design-core/parameters`, { timeoutMs: 15000, activity: { label: '正在加载参数来源与确认状态', cacheTtlMs: 0 } }),
  confirmDesignParameters: (id: string, updates: Record<string, unknown>[]) => request<Record<string, unknown>>(`/api/projects/${id}/design-core/parameters/confirm`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ updates }) }),
  getDesignRuleEvidence: (id: string) => request<Record<string, any>>(`/api/projects/${id}/design-core/rules`),
  getSchemeSearchAssurance: (id: string) => request<Record<string, any>>(`/api/projects/${id}/design-core/schemes`),
  getMemberEnvelopes: (id: string) => request<Record<string, any>>(`/api/projects/${id}/design-core/member-envelopes`),
  getReinforcementClosure: (id: string) => request<Record<string, any>>(`/api/projects/${id}/design-core/reinforcement-closure`),
  getDeliveryQuality: (id: string) => request<Record<string, any>>(`/api/projects/${id}/design-core/delivery-quality`),
  createDesignSnapshot: (id: string, purpose = 'internal_review') => request<Record<string, any>>(`/api/projects/${id}/design-core/design-snapshots`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ purpose }) }),
  getDesignCollaboration: (id: string) => request<{ records: ExternalCollaborationRecord[]; reviewRequests: DesignReviewRequestRecord[]; boundary: string }>(`/api/projects/${id}/design-core/collaboration`),
  addDesignCollaboration: (id: string, record: ExternalCollaborationRecord) => request<Record<string, unknown>>(`/api/projects/${id}/design-core/collaboration`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(record) }),
  getBusinessWorkflow: (id: string) => request<BusinessWorkflowOverview>(`/api/projects/${id}/workflow`, { timeoutMs: 15000, retryCount: 0, activity: { label: '正在加载设计、施工与现场责任门禁', cacheTtlMs: 0 } }),
  migrateLegacyDesignStages: (id: string, force = false) => request<Record<string, unknown>>(`/api/projects/${id}/workflow/migrate-legacy-stages?force=${force ? 'true' : 'false'}`, { method: 'POST' }),
  getDesignControlStages: (id: string) => request<{ semanticType: string; stages: DesignControlStage[]; validation: Record<string, unknown>; responsibility: string; excludedFields: string[] }>(`/api/projects/${id}/workflow/design-control-stages`),
  saveDesignControlStages: (id: string, stages: DesignControlStage[]) => request<Record<string, unknown>>(`/api/projects/${id}/workflow/design-control-stages`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(stages) }),
  generateDesignScenarios: (id: string) => request<{ suite: Record<string, unknown>; scenarios: DesignScenario[] }>(`/api/projects/${id}/workflow/design-scenarios/generate`, { method: 'POST' }),
  getDesignScenarios: (id: string) => request<{ scenarios: DesignScenario[]; envelope: Record<string, unknown> }>(`/api/projects/${id}/workflow/design-scenarios`),
  updateDesignScenarioApproval: (id: string, scenarioIds: string[], approvalStatus: 'draft' | 'approved' | 'rejected' = 'approved', enabled?: boolean) => request<{ approval: Record<string, unknown>; scenarios: DesignScenario[] }>(`/api/projects/${id}/workflow/design-scenarios/approval`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenarioIds, approvalStatus, enabled }) }),
  executeApprovedDesignScenarios: (id: string, scenarioIds?: string[], maxScenarios = 12) => request<PitTask>(`/api/projects/${id}/workflow/design-scenarios/execute`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenarioIds: scenarioIds ?? [], maxScenarios }) }),
  buildDesignScenarioEnvelope: (id: string) => request<Record<string, unknown>>(`/api/projects/${id}/workflow/design-scenarios/envelope`, { method: 'POST' }),
  getConstructionPlanStages: (id: string) => request<{ stages: (ConstructionPlanStage & { compliance?: Record<string, unknown> })[]; responsibility: string }>(`/api/projects/${id}/workflow/construction-plan-stages`),
  saveConstructionPlanStage: (id: string, stage: ConstructionPlanStage) => request<Record<string, unknown>>(`/api/projects/${id}/workflow/construction-plan-stages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(stage) }),
  addFieldExecutionSnapshot: (id: string, snapshot: FieldExecutionSnapshot) => request<Record<string, unknown>>(`/api/projects/${id}/workflow/field-snapshots`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapshot) }),
  getDeviationEvents: (id: string) => request<{ events: DeviationEvent[]; openCount: number }>(`/api/projects/${id}/workflow/deviations`),
  getIntegratedRetainingCandidates: (id: string, mode = 'balanced', maxCandidates = 8) => request<Record<string, any>>(`/api/projects/${id}/expert-design/integrated-candidates?mode=${mode}&maxCandidates=${maxCandidates}`),
  applyIntegratedRetainingCandidate: (id: string, candidateId: string, mode = 'balanced', recalculate = true) => request<Record<string, any>>(`/api/projects/${id}/expert-design/apply-integrated-candidate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidateId, mode, recalculate }) }),
  importBoreholes: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<ImportResult>(`/api/projects/${projectId}/boreholes/import-csv`, { method: 'POST', body: form });
  },
  importBoreholesTask: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<PitTask>(`/api/projects/${projectId}/boreholes/import-task`, {
      method: 'POST',
      body: form,
      timeoutMs: 120000,
      timeoutMessage: '地勘文件上传 120 秒内未完成。文件不会重复提交，请检查网络和 API 日志。',
      activity: { label: '正在上传地勘文件', expectedMs: 2500, cacheTtlMs: 0 },
    });
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
  getDesignWorkspaceBootstrap: (projectId: string, refresh = false, signal?: AbortSignal) => request<Record<string, any>>(`/api/projects/${projectId}/design/workspace-bootstrap${refresh ? '?refresh=true' : ''}`, { signal, timeoutMs: 30000, responseIdleTimeoutMs: 15000, retryCount: 0, activity: { label: '正在装配围护设计工作区', expectedMs: 2500, quiet: true, cacheTtlMs: refresh ? 0 : 15000, deduplicate: true } }),
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
  getLatestCalculationEvidenceForViewer: (projectId: string) => request<{ projectId: string; evidence: Record<string, unknown>; result?: CalculationResult }>(`/api/projects/${projectId}/calculation/latest-evidence`, { timeoutMs: 30000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 0, deduplicate: true } }),
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
  getEngineeringEvidenceStatus: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/engineering-evidence`),
  attachEngineeringEvidence: (projectId: string, payload: { domain: 'borehole' | 'groundwater' | 'construction_stage'; objectIds: string[]; file: File; revision?: string; observedAt?: string }) => {
    const form = new FormData();
    form.append('domain', payload.domain);
    form.append('object_ids', payload.objectIds.join(','));
    if (payload.revision) form.append('revision', payload.revision);
    if (payload.observedAt) form.append('observed_at', payload.observedAt);
    form.append('file', payload.file);
    return request<Record<string, any>>(`/api/projects/${projectId}/advanced/engineering-evidence/attach`, { method: 'POST', body: form });
  },
  verifyEngineeringEvidence: (projectId: string, payload: { domain: 'borehole' | 'groundwater' | 'construction_stage'; objectIds: string[]; actor: string; professionalCredential: Record<string, unknown>; digitalSignatureHash: string }) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/engineering-evidence/verify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  getConcaveTransferDetailing: (projectId: string) => request<Record<string, any>>(`/api/projects/${projectId}/design/concave-transfer-detailing`),
  approveConcaveTransferDetailing: (projectId: string, payload: Record<string, unknown>) => request<Record<string, any>>(`/api/projects/${projectId}/design/concave-transfer-detailing`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  transitionReview: (projectId: string, payload: { role: string; actor: string; action: string; comment?: string; professionalCredential?: Record<string, unknown>; digitalSignatureHash?: string }) => request<Record<string, any>>(`/api/projects/${projectId}/advanced/review/transition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
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
  getRebarIfcVisualization: (projectId: string, maxBars = 2400, focusHostId?: string) => request<RebarIfcVisualization>(`/api/projects/${projectId}/export/ifc-rebar-visualization?max_bars=${maxBars}${focusHostId ? `&focus_host_id=${encodeURIComponent(focusHostId)}` : ''}`),
  getRebarDetailing: (projectId: string, mode = 'balanced') => request<RebarDetailingResult>(`/api/projects/${projectId}/rebar/detailing?mode=${mode}`),
  getDeepDetailing: (projectId: string, mode = 'balanced', signal?: AbortSignal) => request<Record<string, any>>(`/api/projects/${projectId}/rebar/deep-detailing?mode=${mode}`, { signal, timeoutMs: 45000, responseIdleTimeoutMs: 15000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 5000, deduplicate: true } }),
  getRebarDesignScheme: (projectId: string, mode = 'balanced', detail: 'summary' | 'full' = 'summary', signal?: AbortSignal) => request<RebarDesignScheme>(`/api/projects/${projectId}/rebar/design-scheme?mode=${mode}&detail=${detail}&checkLimit=${detail === 'full' ? 500 : 60}`, { signal, timeoutMs: detail === 'full' ? 90000 : 45000, responseIdleTimeoutMs: 18000, retryCount: 0, activity: { quiet: true, cacheTtlMs: detail === 'full' ? 0 : 5000, deduplicate: true } }),
  getRebarDesignChecks: (projectId: string, mode = 'balanced', offset = 0, limit = 5000, signal?: AbortSignal) => request<{ projectId: string; mode: string; offset: number; limit: number; total: number; returned: number; hasMore: boolean; rows: Record<string, unknown>[] }>(`/api/projects/${projectId}/rebar/design-scheme/checks?mode=${mode}&offset=${offset}&limit=${limit}`, { signal, timeoutMs: 90000, responseIdleTimeoutMs: 18000, retryCount: 0, activity: { label: '正在加载完整配筋复核列表', blocking: false, quiet: false, cacheTtlMs: 0, deduplicate: true } }),
  applyRebarDesignScheme: (projectId: string, mode = 'balanced', recalculate = true) => request<{ projectId: string; mode: string; scheme: RebarDesignScheme; retainingSystem: RetainingSystem; recalculated?: boolean; recalculationQueued?: boolean; calculationTask?: PitTask }>(`/api/projects/${projectId}/rebar/apply-design-scheme`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, recalculate }) }),
  getDrawingSetManifest: (projectId: string, signal?: AbortSignal) => request<DrawingSetManifest>(`/api/projects/${projectId}/export/drawings-manifest`, { signal, timeoutMs: 45000, responseIdleTimeoutMs: 15000, retryCount: 0, activity: { quiet: true, cacheTtlMs: 5000, deduplicate: true } }),
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
