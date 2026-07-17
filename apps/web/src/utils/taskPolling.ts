import { api } from '../api/client';
import type { PitTask } from '../types/domain';

const TERMINAL = new Set(['success', 'failed', 'cancelled', 'interrupted']);

function ageMs(value?: string) {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? Math.max(0, Date.now() - parsed) : 0;
}

export async function waitForTaskWithHealth(
  initial: PitTask,
  onUpdate: (task: PitTask) => void,
  options: { timeoutMs?: number; staleHeartbeatMs?: number } = {},
): Promise<PitTask> {
  let task = initial;
  const started = Date.now();
  const timeoutMs = options.timeoutMs ?? 40 * 60 * 1000;
  const staleHeartbeatMs = options.staleHeartbeatMs ?? 75_000;
  let failures = 0;
  let lastHealthCheck = 0;

  while (!TERMINAL.has(task.status)) {
    const elapsed = Date.now() - started;
    if (elapsed > timeoutMs) {
      throw new Error(`任务轮询超过 ${Math.round(timeoutMs / 60000)} 分钟。计算worker会按硬超时独立终止，请查看 worker.log。`);
    }
    const delay = document.hidden ? 5000 : Math.min(3500, 900 + Math.floor(elapsed / 120000) * 350);
    await new Promise((resolve) => window.setTimeout(resolve, delay));
    try {
      task = await api.getTask(task.id);
      failures = 0;
      onUpdate(task);
    } catch (reason) {
      failures += 1;
      if (failures >= 8) {
        const message = reason instanceof Error ? reason.message : String(reason);
        throw new Error(`连续 8 次无法读取任务状态：${message}。任务不会重复提交，请检查 API 与 worker 日志。`);
      }
      continue;
    }

    const heartbeatAge = ageMs(task.heartbeatAt || task.updatedAt);
    const queuedAge = ageMs(task.createdAt);
    const shouldCheckWorker = (
      task.status === 'running' && heartbeatAge > staleHeartbeatMs
    ) || (
      task.status === 'queued' && queuedAge > 45_000
    );
    if (shouldCheckWorker && Date.now() - lastHealthCheck > 15_000) {
      lastHealthCheck = Date.now();
      const metrics = await api.getTaskMetrics().catch(() => undefined);
      const heartbeat = metrics?.workerHeartbeat as Record<string, unknown> | undefined;
      if (heartbeat && heartbeat.healthy === false) {
        throw new Error('计算worker心跳已失效。API仍可使用，但当前计算进程已经退出；请查看 runtime/worker.log 后重试。');
      }
    }
  }
  return task;
}
