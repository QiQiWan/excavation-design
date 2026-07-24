from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Condition, Event, Thread
from typing import Any, Callable
from uuid import uuid4
import traceback
import hashlib
import json
import shutil
import os
import gc
import ctypes
import time
from threading import Timer
try:
    import resource
except ImportError:  # pragma: no cover - Windows compatibility
    resource = None  # type: ignore[assignment]
from contextlib import contextmanager, nullcontext

from app.schemas.domain import Project
from app.storage.repository import ProjectRepository
from app.storage.task_store import SQLiteTaskStore
from app.version import SOFTWARE_VERSION
from app.services.calculation_resource_estimator import estimate_calculation_resources
from app.services.runtime_resource_policy import adaptive_resource_policy, mb
from app.services.system_resources import physical_memory_bytes, process_effective_memory_bytes, process_memory_counters, process_rss_bytes
from app.services.runtime_diagnostics import append_event, memory_event

HEAVY_TASK_CONCURRENCY_ENV = "PITGUARD_HEAVY_TASK_CONCURRENCY"
TASK_MEMORY_SOFT_LIMIT_ENV = "PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB"


EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"

TaskStatus = str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 262144) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _process_memory_mb() -> float:
    return round(float(process_effective_memory_bytes()) / 1048576.0, 2)


def _process_rss_mb() -> float:
    return round(float(process_rss_bytes()) / 1048576.0, 2)


def _system_available_memory_mb() -> float:
    _total, available = physical_memory_bytes()
    return round(float(available or 0) / 1048576.0, 2)


def _pid_alive(pid: int | str | None) -> bool:
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if value == os.getpid():
        return True
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _compact_rebar_scheme_for_recalculation(scheme: dict[str, Any], mode: str) -> dict[str, Any]:
    """Keep only the audit contract needed while a section recalculation runs.

    Full wall/support/node schemes can contain thousands of checks and are
    expensive to serialize repeatedly.  The actual section and reinforcement
    changes have already been applied to the structural objects, so the interim
    project only needs a compact trace until the updated force envelope is ready.
    """
    diagnostics = dict(scheme.get("diagnostics") or {})
    deepening_gate = dict(diagnostics.get("deepeningGate") or {})
    summary = dict(scheme.get("summary") or {})
    return {
        "projectId": scheme.get("projectId"),
        "mode": str(scheme.get("mode") or mode),
        "status": "recalculating",
        "method": scheme.get("method"),
        "interim": True,
        "requiresRecalculation": True,
        "summary": {
            "checkCount": int(summary.get("checkCount") or len(scheme.get("checks") or [])),
            "failCount": int(summary.get("failCount") or 0),
            "warningCount": int(summary.get("warningCount") or 0),
            "supportSchemeCount": len(scheme.get("supportSchemes") or []),
            "wallZoneCount": len(scheme.get("wallZones") or []),
            "beamNodeSchemeCount": len(scheme.get("beamNodeSchemes") or []),
        },
        "diagnostics": {
            "sectionChangeCount": int(diagnostics.get("sectionChangeCount") or 0),
            "supportRebarContract": scheme.get("supportRebarContractSummary"),
            "canApply": bool(diagnostics.get("canApply")),
            "deepeningGate": {
                "status": deepening_gate.get("status"),
                "blockerCount": deepening_gate.get("blockerCount"),
                "warningCount": deepening_gate.get("warningCount"),
            },
        },
    }


def _release_process_memory() -> None:
    gc.collect()
    try:
        libc = ctypes.CDLL("libc.so.6")
        trim = getattr(libc, "malloc_trim", None)
        if trim is not None:
            trim(0)
    except (OSError, AttributeError):
        pass


@dataclass
class TaskRecord:
    id: str
    project_id: str
    operation: str
    title: str
    status: TaskStatus = "queued"
    progress: int = 0
    current_step: str = "等待执行"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    finished_at: str | None = None
    cancel_requested: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    parent_task_id: str | None = None
    heartbeat_at: str | None = None
    deduplication_key: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRecord":
        return cls(
            id=str(data["id"]),
            project_id=str(data.get("projectId", data.get("project_id", ""))),
            operation=str(data.get("operation", "")),
            title=str(data.get("title", data.get("operation", "task"))),
            status=str(data.get("status", "queued")),
            progress=int(data.get("progress", 0) or 0),
            current_step=str(data.get("currentStep", data.get("current_step", "等待执行"))),
            logs=list(data.get("logs") or []),
            result=data.get("result"),
            error=data.get("error"),
            created_at=str(data.get("createdAt", data.get("created_at", _now()))),
            updated_at=str(data.get("updatedAt", data.get("updated_at", _now()))),
            finished_at=data.get("finishedAt", data.get("finished_at")),
            cancel_requested=bool(data.get("cancelRequested", data.get("cancel_requested", False))),
            payload=dict(data.get("payload") or {}),
            attempt=int(data.get("attempt", 1) or 1),
            parent_task_id=data.get("parentTaskId", data.get("parent_task_id")),
            heartbeat_at=data.get("heartbeatAt", data.get("heartbeat_at")),
            deduplication_key=data.get("deduplicationKey", data.get("deduplication_key")),
        )

    def as_dict(self, include_logs: bool = False, include_result: bool = True, log_limit: int = 80) -> dict[str, Any]:
        data = {
            "id": self.id,
            "projectId": self.project_id,
            "operation": self.operation,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "currentStep": self.current_step,
            "result": self.result if include_result else None,
            "error": self.error,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "finishedAt": self.finished_at,
            "cancelRequested": self.cancel_requested,
            "payload": dict(self.payload),
            "attempt": self.attempt,
            "parentTaskId": self.parent_task_id,
            "heartbeatAt": self.heartbeat_at,
            "deduplicationKey": self.deduplication_key,
        }
        if include_logs:
            data["logs"] = list(self.logs[-max(1, log_limit):])
        return data


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._futures: dict[str, Future] = {}
        self._lock = RLock()
        self._project_locks: dict[str, RLock] = {}
        self._store = SQLiteTaskStore()
        self._execution_mode = str(os.getenv("PITGUARD_TASK_EXECUTION_MODE", "embedded") or "embedded").strip().lower()
        if self._execution_mode not in {"embedded", "external", "worker"}:
            self._execution_mode = "embedded"
        startup_policy = adaptive_resource_policy(role="worker" if self._execution_mode == "worker" else "api")
        self._worker_count = _env_int("PITGUARD_TASK_WORKERS", 2, 1, 8)
        self._heavy_concurrency = max(1, min(3, int(startup_policy.get("recommendedHeavyConcurrency") or 1)))
        self._memory_soft_limit_mb = max(192, int(mb(startup_policy.get("workerSoftLimitBytes"))))
        self._task_timeout_seconds = _env_int("PITGUARD_TASK_TIMEOUT_SECONDS", 1800, 60, 86400)
        self._resource_watch_interval_seconds = _env_int("PITGUARD_RESOURCE_WATCH_INTERVAL_SECONDS", 1, 1, 30)
        self._worker_rss_hard_limit_mb = max(256, int(mb(startup_policy.get("workerHardLimitBytes"))))
        self._system_memory_reserve_mb = max(256, int(mb(startup_policy.get("reserveBytes"))))
        default_heartbeat = Path(os.getenv("PITGUARD_DB_PATH", str(Path(__file__).resolve().parents[2] / "pitguard.sqlite3"))).with_name("worker-heartbeat.json")
        self._worker_heartbeat_path = Path(os.getenv("PITGUARD_WORKER_HEARTBEAT_PATH", str(default_heartbeat)))
        self._worker_stale_seconds = _env_int("PITGUARD_WORKER_STALE_SECONDS", 45, 15, 600)
        self._worker_queue_stale_seconds = _env_int("PITGUARD_WORKER_QUEUE_STALE_SECONDS", 60, 20, 1800)
        self._last_external_reconcile_monotonic = 0.0
        self._heavy_condition = Condition(RLock())
        self._heavy_active = 0
        self._executor = (
            ThreadPoolExecutor(max_workers=self._worker_count, thread_name_prefix="pitguard-task")
            if self._execution_mode == "embedded" else None
        )
        self._heavy_operations = {
            "calculation_full", "calculation_recovery", "calculation_closure_action", "calculation_optimize_search", "calculation_auto_close", "candidate_comparison", "candidate_scheme_calculation", "support_layout_optimization",
            "adopt_support_candidate", "core_design", "rebar_design", "formal_adverse_scenarios", "design_scenario_envelope", "p3_detailing_closure",
            "full_delivery", "industrial_closure", "export_rebar_detailing",
            "export_ifc_detailed", "export_ifc_construction_visual", "export_coordinated_delivery",
            "storage_compaction",
        }
        for raw in self._store.list(limit=500):
            task = TaskRecord.from_dict(raw)
            # Embedded mode loses its executor on API restart.  In external mode
            # queued records belong to the dedicated worker and must remain
            # queued while the HTTP process is rebuilt.
            if self._execution_mode == "embedded" and task.status in {"queued", "running"}:
                task.status = "interrupted"
                task.current_step = "服务重启导致任务中断，可重新提交"
                task.error = task.error or "Task interrupted by service restart"
                task.finished_at = _now()
                task.updated_at = task.finished_at
                task.logs.append(f"[{_now()}] 服务启动时检测到未完成任务，已标记为 interrupted。")
                self._store.upsert(task.as_dict(include_logs=True))
            self._tasks[task.id] = task

    @contextmanager
    def _dynamic_heavy_guard(self, task: TaskRecord):
        """Admit heavy work using current, not startup-only, memory headroom.

        The configured/startup concurrency is an administrative maximum.  The
        live policy may reduce it to one while other services consume memory and
        may raise it again after headroom recovers.  This prevents three A/B/C
        workers from passing the same preflight instant and overcommitting RAM.
        """
        waited = False
        while True:
            policy = adaptive_resource_policy(role="worker")
            allowed = max(1, min(self._heavy_concurrency, int(policy.get("recommendedHeavyConcurrency") or 1)))
            with self._heavy_condition:
                if self._heavy_active < allowed:
                    self._heavy_active += 1
                    break
                if task.cancel_requested:
                    raise RuntimeError("Task cancelled while waiting for the adaptive heavy-task admission gate")
                if not waited:
                    self._append_log(
                        task,
                        f"当前资源策略允许 {allowed} 个重任务并发；已有 {self._heavy_active} 个，任务进入资源等待队列。",
                    )
                    waited = True
                self._heavy_condition.wait(timeout=1.0)
        try:
            yield policy
        finally:
            with self._heavy_condition:
                self._heavy_active = max(0, self._heavy_active - 1)
                self._heavy_condition.notify_all()

    def _write_worker_heartbeat(self, status: str, task_id: str | None = None) -> None:
        if self._execution_mode != "worker":
            return
        payload = {
            "status": status,
            "taskId": task_id,
            "updatedAt": _now(),
            "pid": os.getpid(),
            "rssMb": _process_rss_mb(),
            "effectiveProcessMemoryMb": _process_memory_mb(),
            "systemAvailableMemoryMb": _system_available_memory_mb(),
        }
        try:
            self._worker_heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._worker_heartbeat_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self._worker_heartbeat_path)
        except OSError:
            pass

    def _worker_heartbeat_snapshot(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._worker_heartbeat_path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(str(payload.get("updatedAt")))
            age = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())
            payload["ageSeconds"] = round(age, 1)
            payload["healthy"] = age <= 20.0
            return payload
        except Exception:
            return {"status": "unknown", "healthy": False, "ageSeconds": None}


    @staticmethod
    def _timestamp_age_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
        except (TypeError, ValueError):
            return None

    def _reconcile_external_task_health(self, *, force: bool = False) -> None:
        if self._execution_mode != "external":
            return
        now_monotonic = time.monotonic()
        if not force and now_monotonic - self._last_external_reconcile_monotonic < 5.0:
            return
        self._last_external_reconcile_monotonic = now_monotonic
        heartbeat = self._worker_heartbeat_snapshot()
        worker_healthy = bool(heartbeat.get("healthy"))
        for raw in self._store.list(limit=500):
            status = str(raw.get("status") or "")
            if status not in {"queued", "running"}:
                continue
            task_id = str(raw.get("id") or "")
            if not task_id:
                continue
            if status == "running":
                age = self._timestamp_age_seconds(raw.get("heartbeatAt") or raw.get("updatedAt"))
                if age is not None and age > float(self._worker_stale_seconds):
                    self._store.mark_interrupted(
                        task_id,
                        f"计算worker心跳已中断 {age:.0f} 秒。任务已停止等待，请检查 worker.log 后重试。",
                        "计算worker失联，任务已中断",
                    )
            elif not worker_healthy:
                age = self._timestamp_age_seconds(raw.get("createdAt") or raw.get("updatedAt"))
                if age is not None and age > float(self._worker_queue_stale_seconds):
                    self._store.mark_interrupted(
                        task_id,
                        f"任务排队 {age:.0f} 秒仍未检测到可用计算worker。请使用一键启动脚本同时启动 API、worker 和前端。",
                        "未检测到计算worker，任务已中断",
                    )

    def ensure_worker_available(self) -> None:
        if self._execution_mode != "external":
            return
        self._reconcile_external_task_health(force=True)
        heartbeat = self._worker_heartbeat_snapshot()
        if bool(heartbeat.get("healthy")):
            return
        raise RuntimeError(
            "独立计算worker未运行或心跳已失效。为防止计算占满API进程，当前不会退回嵌入式执行。"
            "请通过 start-windows.ps1、start-linux-dev.sh 或生产服务同时启动 worker。"
        )

    def _mark_worker_resource_abort(self, task_id: str, reason: str, exit_code: int = 137) -> None:
        raw_task = self._store.get(task_id)
        if raw_task is not None:
            now = _now()
            raw_task["status"] = "interrupted"
            raw_task["error"] = reason
            raw_task["currentStep"] = "资源保护闸门终止计算，API服务保持在线"
            raw_task["updatedAt"] = now
            raw_task["finishedAt"] = now
            logs = list(raw_task.get("logs") or [])
            logs.append(f"[{now}] {reason}")
            raw_task["logs"] = logs[-500:]
            self._store.upsert(raw_task)
        self._write_worker_heartbeat("resource_abort", task_id)
        os._exit(exit_code)

    def _start_resource_watchdog(self, task: TaskRecord) -> tuple[Event, Thread] | None:
        if self._execution_mode != "worker" or task.operation not in self._heavy_operations:
            return None
        stop_event = Event()

        def monitor() -> None:
            consecutive_low_memory = 0
            last_heartbeat = 0.0
            while not stop_event.wait(float(self._resource_watch_interval_seconds)):
                rss = _process_memory_mb()
                available = _system_available_memory_mb()
                now_monotonic = time.monotonic()
                if now_monotonic - last_heartbeat >= 10.0:
                    raw_task = self._store.get(task.id)
                    if raw_task is not None and raw_task.get("status") == "running":
                        now = _now()
                        raw_task["heartbeatAt"] = now
                        raw_task["updatedAt"] = now
                        self._store.upsert(raw_task)
                    self._write_worker_heartbeat("running", task.id)
                    last_heartbeat = now_monotonic
                runtime_policy = adaptive_resource_policy(role="worker")
                counters = process_memory_counters()
                append_event(
                    "worker-memory",
                    "watchdog-sample",
                    taskId=task.id,
                    projectId=task.project_id,
                    operation=task.operation,
                    currentStep=task.current_step,
                    progress=task.progress,
                    rssMb=round(float(counters.get("rssBytes") or 0) / 1048576.0, 2),
                    privateMb=round(float(counters.get("privateBytes") or 0) / 1048576.0, 2),
                    effectiveMemoryMb=rss,
                    peakRssMb=round(float(counters.get("peakRssBytes") or 0) / 1048576.0, 2),
                    systemAvailableMb=available,
                )
                hard_limit_mb = max(256.0, mb(runtime_policy.get("workerHardLimitBytes")))
                reserve_mb = max(128.0, mb(runtime_policy.get("reserveBytes")))
                self._worker_rss_hard_limit_mb = int(hard_limit_mb)
                self._system_memory_reserve_mb = int(reserve_mb)
                if rss > hard_limit_mb:
                    self._mark_worker_resource_abort(
                        task.id,
                        f"计算worker有效内存达到 {rss:.0f} MB，超过当前动态硬上限 {hard_limit_mb:.0f} MB，已终止当前计算进程。",
                    )
                if available > 0 and available < reserve_mb:
                    consecutive_low_memory += 1
                else:
                    consecutive_low_memory = 0
                if consecutive_low_memory >= 2:
                    self._mark_worker_resource_abort(
                        task.id,
                        f"服务器可用内存仅 {available:.0f} MB，低于当前动态保留值 {reserve_mb:.0f} MB，已优先终止计算worker。",
                    )

        thread = Thread(target=monitor, name=f"pitguard-resource-watch-{task.id}", daemon=True)
        thread.start()
        return stop_event, thread

    def _resource_preflight(self, task: TaskRecord, project: Any, *, candidate_count: int = 0) -> dict[str, Any]:
        estimate = estimate_calculation_resources(project, candidate_count=candidate_count)
        self._append_log(
            task,
            "计算资源预估："
            f"峰值约 {estimate['estimatedPeakMemoryMb']} MB / worker上限 {estimate['workerMemoryMaxMb']} MB；"
            f"风险等级 {estimate['status']}。",
        )
        if not estimate.get("calculationAllowed", True):
            recommendations = "；".join(estimate.get("recommendations") or [])
            raise RuntimeError(f"计算规模超过当前worker安全预算，已受控阻断。{recommendations}")
        return estimate

    def _enforce_memory_budget(self, task: TaskRecord, stage: str) -> None:
        """Fail a heavy task cleanly before the operating system OOM-kills the API."""
        runtime_policy = adaptive_resource_policy(role="worker")
        soft_limit_mb = max(192.0, mb(runtime_policy.get("workerSoftLimitBytes")))
        self._memory_soft_limit_mb = int(soft_limit_mb)
        rss = _process_memory_mb()
        if rss <= soft_limit_mb:
            return
        _release_process_memory()
        rss_after = _process_memory_mb()
        self._append_log(
            task,
            f"{stage}检测到内存压力：有效内存 {rss_after:.2f} MB，当前动态软上限 {soft_limit_mb:.0f} MB。",
        )
        if rss_after > soft_limit_mb:
            raise RuntimeError(
                f"服务器内存不足，已在{stage}前受控终止任务（有效内存 {rss_after:.0f} MB > "
                f"动态软上限 {soft_limit_mb:.0f} MB）。系统将保留已完成步骤，可改为逐方案计算或增加worker资源。"
            )

    def _memory_checkpoint(self, task: TaskRecord, label: str) -> None:
        """Release completed-stage objects before hydrating the next full project."""
        before = _process_memory_mb()
        _release_process_memory()
        after = _process_memory_mb()
        self._append_log(task, f"{label}内存检查：有效内存 {before:.1f} -> {after:.1f} MB。")
        memory_event(
            "task-lifecycle",
            "memory-checkpoint",
            taskId=task.id,
            projectId=task.project_id,
            operation=task.operation,
            stage=label,
            beforeEffectiveMb=before,
            afterEffectiveMb=after,
        )
        if self._execution_mode == "worker":
            self._enforce_memory_budget(task, label)

    @staticmethod
    def _deduplication_key(project_id: str, operation: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{project_id}|{operation}|{canonical}".encode("utf-8")).hexdigest()

    def _refresh_record(self, task_id: str) -> TaskRecord | None:
        raw = self._store.get(task_id)
        if raw is None:
            return None
        task = TaskRecord.from_dict(raw)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def submit(
        self,
        project_id: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        attempt: int = 1,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        payload = dict(payload or {})
        deduplication_key = self._deduplication_key(project_id, operation, payload)
        # Double clicks and network retries must not start multiple dense solves.
        for active in self.list(project_id=project_id):
            if active.status in {"queued", "running"} and active.deduplication_key == deduplication_key:
                return active
        task = TaskRecord(
            id=f"task-{uuid4().hex[:12]}",
            project_id=project_id,
            operation=operation,
            title=self._title_for(operation),
            payload=payload,
            attempt=max(1, int(attempt)),
            parent_task_id=parent_task_id,
            deduplication_key=deduplication_key,
        )
        with self._lock:
            self._tasks[task.id] = task
            self._append_log(task, f"任务已创建：{task.title}")
            self._persist(task)
            if self._executor is not None:
                future = self._executor.submit(self._run_task, task.id, payload)
                self._futures[task.id] = future
        return task

    def submit_candidate_batch(self, project_id: str, top_n: int = 3, use_cache: bool = True) -> list[TaskRecord]:
        # Task orchestration belongs to the lightweight API path. Candidate IDs,
        # ranks and preview geometry are part of the bounded workspace projection;
        # the dedicated worker hydrates the full project only when each task runs.
        # This keeps the A/B/C button usable for projects whose logical snapshot is
        # hundreds of megabytes.
        project = self._repo().require_workspace(project_id)
        repair = project.retaining_system.support_layout_repair if project.retaining_system else None
        candidates = list((repair.candidates if repair else [])[: max(1, min(top_n, 3))])
        if not candidates:
            raise ValueError("No support-layout candidates are available. Generate A/B/C candidates first.")
        tasks: list[TaskRecord] = []
        for index, candidate in enumerate(candidates):
            tasks.append(self.submit(project_id, "candidate_scheme_calculation", {
                "candidateId": candidate.id, "candidateIndex": index, "useCache": use_cache,
            }))
        return tasks

    def retry(self, task_id: str) -> TaskRecord | None:
        original = self.get(task_id)
        if original is None:
            return None
        with self._lock:
            if original.status in {"queued", "running"}:
                raise ValueError("A queued or running task cannot be retried.")
            payload = dict(original.payload)
            project_id = original.project_id
            operation = original.operation
            attempt = original.attempt + 1
        retried = self.submit(
            project_id,
            operation,
            payload,
            attempt=attempt,
            parent_task_id=original.id,
        )
        self._append_log(retried, f"由任务 {original.id} 重试，当前为第 {attempt} 次尝试。")
        return retried

    def metrics(self) -> dict[str, Any]:
        records = self.list() if self._execution_mode in {"external", "worker"} else list(self._tasks.values())
        statuses = {status: sum(task.status == status for task in records) for status in (
            "queued", "running", "success", "failed", "cancelled", "interrupted"
        )}
        completed = [task for task in records if task.finished_at]
        success = statuses.get("success", 0)
        terminal = sum(statuses.get(key, 0) for key in ("success", "failed", "cancelled", "interrupted"))
        return {
            "taskCount": len(records),
            "statusCounts": statuses,
            "terminalCount": terminal,
            "successRate": round(success / terminal, 4) if terminal else None,
            "retryCount": sum(task.attempt > 1 for task in records),
            "activeProjectCount": len({task.project_id for task in records if task.status in {"queued", "running"}}),
            "processMemoryMb": _process_memory_mb(),
            "memorySoftLimitMb": self._memory_soft_limit_mb,
            "taskExecutionMode": self._execution_mode,
            "taskTimeoutSeconds": self._task_timeout_seconds,
            "workerCount": self._worker_count if self._execution_mode == "embedded" else 0,
            "heavyTaskConcurrency": self._heavy_concurrency,
            "workerRssHardLimitMb": self._worker_rss_hard_limit_mb,
            "systemMemoryReserveMb": self._system_memory_reserve_mb,
            "systemAvailableMemoryMb": _system_available_memory_mb(),
            "latestUpdatedAt": max((task.updated_at for task in records), default=None),
            "completedCount": len(completed),
            "processResidentMemoryMB": _process_memory_mb(),
            "workerHeartbeat": self._worker_heartbeat_snapshot() if self._execution_mode == "external" else None,
        }

    def list(self, project_id: str | None = None) -> list[TaskRecord]:
        if self._execution_mode == "external":
            self._reconcile_external_task_health()
        if self._execution_mode in {"external", "worker"}:
            records = [TaskRecord.from_dict(raw) for raw in self._store.list(project_id=project_id, limit=500)]
            with self._lock:
                for task in records:
                    self._tasks[task.id] = task
        else:
            with self._lock:
                records = list(self._tasks.values())
            if project_id:
                records = [task for task in records if task.project_id == project_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, task_id: str) -> TaskRecord | None:
        if self._execution_mode == "external":
            self._reconcile_external_task_health()
        if self._execution_mode in {"external", "worker"}:
            return self._refresh_record(task_id)
        with self._lock:
            return self._tasks.get(task_id)

    def delete_project_records(self, project_id: str) -> dict[str, Any]:
        """Cancel and remove task records/files belonging to a deleted project."""
        removed_files: list[str] = []
        with self._lock:
            task_ids = [task_id for task_id, task in self._tasks.items() if task.project_id == project_id]
            for task_id in task_ids:
                task = self._tasks.get(task_id)
                if task is None:
                    continue
                task.cancel_requested = True
                future = self._futures.pop(task_id, None) if self._executor is not None else None
                if future is not None:
                    future.cancel()
                file_path = str((task.result or {}).get("filePath") or "")
                if file_path:
                    path = Path(file_path)
                    try:
                        resolved = path.resolve()
                        export_root = EXPORT_DIR.resolve()
                        if resolved.exists() and (resolved == export_root or export_root in resolved.parents):
                            if resolved.is_file():
                                resolved.unlink()
                                removed_files.append(str(resolved))
                            elif resolved.is_dir():
                                shutil.rmtree(resolved)
                                removed_files.append(str(resolved))
                    except OSError:
                        pass
                self._tasks.pop(task_id, None)
            self._project_locks.pop(project_id, None)
        persisted = self._store.delete_by_project(project_id)
        return {
            "deletedTaskCount": max(len(task_ids), persisted),
            "deletedArtifactCount": len(removed_files),
            "deletedArtifacts": removed_files,
        }

    def cancel(self, task_id: str) -> TaskRecord | None:
        task = self.get(task_id) if self._execution_mode in {"external", "worker"} else self._tasks.get(task_id)
        if not task:
            return None
        with self._lock:
            task.cancel_requested = True
            self._append_log(task, "已请求取消。计算worker将在当前阶段边界停止，并保留已完成步骤。")
            future = self._futures.get(task_id) if self._executor is not None else None
            if task.status == "queued" and self._execution_mode in {"external", "worker"}:
                task.status = "cancelled"
                task.progress = max(task.progress, 1)
                task.current_step = "排队任务已取消"
                task.finished_at = _now()
                task.updated_at = task.finished_at
                self._persist(task)
            elif future and future.cancel():
                task.status = "cancelled"
                task.progress = max(task.progress, 1)
                task.current_step = "任务已取消"
                task.finished_at = _now()
                task.updated_at = task.finished_at
                self._persist(task)
            return task

    def _run_task(self, task_id: str, payload: dict[str, Any]) -> None:
        task = self.get(task_id)
        if not task:
            return
        with self._lock:
            project_lock = self._project_locks.setdefault(task.project_id, RLock())
        heavy_guard = self._dynamic_heavy_guard(task) if task.operation in self._heavy_operations else nullcontext()
        memory_before = _process_memory_mb()
        memory_event(
            "task-lifecycle",
            "task-start",
            taskId=task.id,
            projectId=task.project_id,
            operation=task.operation,
            memoryBaselineMb=memory_before,
            payloadKeys=sorted(payload.keys()),
        )
        watchdog = self._start_resource_watchdog(task)
        failure_type: str | None = None
        failure_message: str | None = None
        failure_traceback_tail: list[str] = []
        try:
            self._set(task, status="running", progress=2, current_step="启动任务")
            self._append_log(task, f"任务内存基线 {memory_before:.2f} MB；重任务并发上限 {self._heavy_concurrency}。")
            with heavy_guard:
                if task.operation in self._heavy_operations:
                    live_policy = adaptive_resource_policy(role="worker")
                    self._append_log(
                        task,
                        "已进入自适应重计算内存闸门；"
                        f"当前建议并发 {live_policy.get('recommendedHeavyConcurrency', 1)}，"
                        f"可用内存 {mb(live_policy.get('effectiveAvailableBytes')):.1f} MB。",
                    )
                    self._enforce_memory_budget(task, "重任务启动")
                if task.operation == "candidate_scheme_calculation":
                    self._append_log(task, "候选方案采用只读项目快照计算；写回结果时使用短时项目锁。")
                    result = self._execute_operation(task, payload)
                else:
                    with project_lock:
                        self._append_log(task, "已获得项目级执行锁，同一项目写任务将串行执行。")
                        result = self._execute_operation(task, payload)
            if task.cancel_requested:
                self._set(task, status="cancelled", progress=task.progress, current_step="任务已取消", finished_at=_now())
                return
            self._set(task, status="success", progress=100, current_step="任务完成", result=result, finished_at=_now())
            self._append_log(task, "任务完成。")
        except Exception as exc:  # pragma: no cover - defensive task boundary
            status = "cancelled" if task.cancel_requested else "failed"
            failure_type = type(exc).__name__
            failure_message = str(exc)
            trace = traceback.format_exc(limit=12)
            failure_traceback_tail = [line for line in trace.strip().splitlines()[-12:] if line.strip()]
            self._set(task, status=status, error=str(exc), current_step="任务已取消" if status == "cancelled" else "任务失败", finished_at=_now())
            self._append_log(task, trace)
            memory_event(
                "task-lifecycle",
                "task-error",
                taskId=task.id,
                projectId=task.project_id,
                operation=task.operation,
                status=status,
                errorType=failure_type,
                errorMessage=failure_message,
                currentStep=task.current_step,
                tracebackTail=failure_traceback_tail,
            )
        finally:
            if watchdog is not None:
                watchdog[0].set()
                watchdog[1].join(timeout=1.0)
            _release_process_memory()
            memory_after = _process_memory_mb()
            self._append_log(task, f"任务结束有效内存 {memory_after:.2f} MB；已执行 Python GC 与 malloc_trim。")
            memory_event(
                "task-lifecycle",
                "task-finish",
                taskId=task.id,
                projectId=task.project_id,
                operation=task.operation,
                status=task.status,
                memoryBaselineMb=memory_before,
                memoryAfterMb=memory_after,
                memoryDeltaMb=round(memory_after - memory_before, 2),
                errorType=failure_type,
                errorMessage=failure_message,
                tracebackTail=failure_traceback_tail,
            )

    def recover_external_worker(self) -> int:
        """Recover only the task owned by a dead prior worker process.

        The supervisor intentionally launches a fresh process after every task.
        A normal ``completed`` heartbeat must therefore never invalidate other
        running task rows.  Recovery is targeted by heartbeat task id and PID,
        with a conservative stale-row sweep as a final safety net.
        """
        if self._execution_mode != "worker":
            return 0
        heartbeat = self._worker_heartbeat_snapshot()
        status = str(heartbeat.get("status") or "unknown").lower()
        task_id = str(heartbeat.get("taskId") or "").strip()
        previous_pid = heartbeat.get("pid")
        recovered = 0
        active_ids: set[str] = set()
        if status in {"running", "starting"} and task_id:
            if _pid_alive(previous_pid):
                active_ids.add(task_id)
            else:
                if self._store.mark_interrupted(
                    task_id,
                    "External calculation worker exited before task completion",
                    "独立计算worker异常退出，当前任务已中断，可重新提交",
                ):
                    recovered += 1
        stale_seconds = max(float(self._worker_stale_seconds), 60.0)
        recovered += self._store.mark_stale_running_interrupted(
            "External calculation worker heartbeat became stale",
            stale_seconds=stale_seconds,
            exclude_task_ids=active_ids,
        )
        return recovered

    def run_worker_forever(self, poll_seconds: float = 1.0) -> None:
        if self._execution_mode != "worker":
            raise RuntimeError("PITGUARD_TASK_EXECUTION_MODE must be 'worker' for the worker daemon")
        self.recover_external_worker()
        self._write_worker_heartbeat("starting")
        last_idle_heartbeat = 0.0
        while True:
            raw = self._store.claim_next()
            if raw is None:
                now_monotonic = time.monotonic()
                if now_monotonic - last_idle_heartbeat >= 5.0:
                    self._write_worker_heartbeat("idle")
                    last_idle_heartbeat = now_monotonic
                time.sleep(max(0.2, float(poll_seconds)))
                continue
            task = TaskRecord.from_dict(raw)
            self._write_worker_heartbeat("running", task.id)
            with self._lock:
                self._tasks[task.id] = task
            timed_out = {"value": False}
            operation_timeout_seconds = self._task_timeout_seconds
            if task.operation == "borehole_import":
                operation_timeout_seconds = min(
                    self._task_timeout_seconds,
                    _env_int("PITGUARD_BOREHOLE_IMPORT_TASK_TIMEOUT_SECONDS", 600, 60, 3600),
                )
            elif task.operation == "rebar_design":
                # Reinforcement may include one or more stiffness-feedback
                # recalculations.  Give it a dedicated ceiling while retaining
                # the watchdog and isolated-process protection.
                operation_timeout_seconds = max(
                    self._task_timeout_seconds,
                    _env_int("PITGUARD_REBAR_TASK_TIMEOUT_SECONDS", 3600, 300, 14400),
                )

            def terminate_worker() -> None:
                timed_out["value"] = True
                raw_task = self._store.get(task.id) or task.as_dict(include_logs=True)
                raw_task["status"] = "interrupted"
                raw_task["error"] = f"Task exceeded hard timeout of {operation_timeout_seconds} seconds"
                raw_task["currentStep"] = "任务超时，独立工作进程将重启"
                raw_task["updatedAt"] = _now()
                raw_task["finishedAt"] = raw_task["updatedAt"]
                logs = list(raw_task.get("logs") or [])
                logs.append(f"[{_now()}] 超过任务硬超时 {operation_timeout_seconds}s，终止独立工作进程以保护 API 服务。")
                if task.operation == "borehole_import":
                    logs.append(f"[{_now()}] 地勘文件解析超时：请清除 Excel 多余行列格式、拆分超大文件或改用 CSV 后重新导入。")
                raw_task["logs"] = logs[-500:]
                self._store.upsert(raw_task)
                os._exit(124)

            timer = Timer(operation_timeout_seconds, terminate_worker)
            timer.daemon = True
            timer.start()
            try:
                self._run_task(task.id, dict(task.payload))
            finally:
                if not timed_out["value"]:
                    timer.cancel()
            if str(os.getenv("PITGUARD_WORKER_EXIT_AFTER_TASK", "true")).strip().lower() in {"1", "true", "yes", "on"}:
                # A fresh OS process for every engineering task is the strongest
                # protection against NumPy/Matplotlib allocator retention and
                # third-party native leaks. systemd restarts the worker and the
                # API process remains continuously available.
                self._write_worker_heartbeat("completed", task.id)
                return

    def _execute_operation(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        if task.operation == "borehole_import":
            result = self._run_borehole_import(task, payload)
        elif task.operation == "core_design":
            result = self._run_core_design(task, payload)
        elif task.operation == "calculation_full":
            result = self._run_calculation_full(task, payload)
        elif task.operation == "calculation_recovery":
            result = self._run_calculation_recovery(task, payload)
        elif task.operation == "calculation_closure_action":
            result = self._run_calculation_closure_action(task, payload)
        elif task.operation in {"calculation_optimize_search", "calculation_auto_close"}:
            result = self._run_calculation_optimization_search(task, payload)
        elif task.operation == "rebar_design":
            result = self._run_rebar_design(task, payload)
        elif task.operation == "formal_adverse_scenarios":
            result = self._run_formal_adverse_scenarios(task, payload)
        elif task.operation == "design_scenario_envelope":
            result = self._run_design_scenario_envelope(task, payload)
        elif task.operation == "p3_detailing_closure":
            result = self._run_p3_detailing_closure(task, payload)
        elif task.operation == "support_layout_optimization":
            result = self._run_support_layout_optimization(task, payload)
        elif task.operation == "adopt_support_candidate":
            result = self._run_adopt_support_candidate(task, payload)
        elif task.operation == "candidate_comparison":
            result = self._run_candidate_comparison(task, payload)
        elif task.operation == "candidate_scheme_calculation":
            result = self._run_candidate_scheme_calculation(task, payload)
        elif task.operation.startswith("export_ifc"):
            result = self._run_ifc_export(task, payload)
        elif task.operation == "export_report":
            result = self._run_report_export(task)
        elif task.operation == "export_drawings_cad":
            result = self._run_cad_export(task, payload)
        elif task.operation == "export_drawings_svg":
            result = self._run_svg_export(task)
        elif task.operation == "export_formal_drawings":
            result = self._run_formal_drawing_export(task, payload)
        elif task.operation == "export_coordinated_delivery":
            result = self._run_coordinated_delivery_export(task, payload)
        elif task.operation == "export_json":
            result = self._run_json_export(task)
        elif task.operation == "export_trace":
            result = self._run_trace_export(task)
        elif task.operation == "export_issue_report":
            result = self._run_issue_report_export(task)
        elif task.operation == "export_rebar_detailing":
            result = self._run_rebar_detailing_export(task)
        elif task.operation == "export_wall_length_redundancy":
            result = self._run_wall_length_redundancy_export(task, payload)
        elif task.operation == "export_design_scheme_ledger":
            result = self._run_design_scheme_ledger_export(task, payload)
        elif task.operation == "export_benchmark_cases":
            result = self._run_benchmark_export(task)
        elif task.operation == "full_delivery":
            result = self._run_full_delivery(task, payload)
        elif task.operation == "industrial_closure":
            result = self._run_industrial_closure(task, payload)
        elif task.operation == "storage_compaction":
            result = self._run_storage_compaction(task, payload)
        else:
            raise ValueError(f"Unsupported task operation: {task.operation}")
        return result

    def _repo(self) -> ProjectRepository:
        return ProjectRepository()

    def _run_borehole_import(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.borehole_import_workflow import execute_borehole_import

        repo = self._repo()
        self._stage(task, 3, "准备隔离解析地勘文件")

        def progress(value: int, message: str) -> None:
            self._stage(task, max(4, min(98, int(value))), message)

        result = execute_borehole_import(task.project_id, payload, repo, progress=progress)
        imported = dict(result.get("importResult") or {})
        if not bool(imported.get("success")):
            errors = "；".join(str(item) for item in list(imported.get("errors") or [])[:8])
            self._append_log(task, f"地勘文件解析完成但未写入工程：{errors or '数据校验未通过'}")
        return result

    def _attempt_legacy_topology_recovery(self, repo: ProjectRepository, project: Any, task: TaskRecord | None = None, *, force: bool = False) -> dict[str, Any]:
        """Regenerate and adopt a qualified topology when legacy candidates block calculation.

        V3.48 and earlier could leave diagnostic-only A/B/C rows in the workspace
        while the current retaining system contained no support members.  Merely
        upgrading the application did not invalidate those rows, so the
        calculation gate remained blocked even though the current generator can
        produce a qualified stepped-strip scheme.  Recovery is bounded and runs
        only inside the isolated calculation worker.
        """
        from app.geology.model_builder import ensure_geological_model_covers_excavation
        from app.services.calculation_state import invalidate_calculation_state
        from app.services.design_service import auto_diaphragm_wall
        from app.services.support_layout_optimizer import SUPPORT_CANDIDATE_CONTRACT_VERSION
        from app.services.support_layout_repair import auto_repair_support_layout

        ret = getattr(project, "retaining_system", None)
        repair = getattr(ret, "support_layout_repair", None) if ret else None
        candidates = list(getattr(repair, "candidates", None) or [])
        candidate_versions = {
            str((getattr(item, "variable_summary", None) or {}).get("candidateContractVersion") or "legacy")
            for item in candidates
        }
        selected_id = str(getattr(repair, "selected_candidate_id", None) or "") if repair else ""
        selected = next((item for item in candidates if str(getattr(item, "id", "") or "") == selected_id), None)
        selected_passed = bool(selected and (getattr(selected, "hard_constraints", None) or {}).get("passed"))
        current_support_count = len(getattr(ret, "supports", None) or []) if ret else 0
        stale_contract = bool(candidates) and candidate_versions != {SUPPORT_CANDIDATE_CONTRACT_VERSION}
        missing_current_scheme = current_support_count == 0 or not selected_passed
        if not (force or stale_contract or missing_current_scheme):
            return {
                "attempted": False,
                "reason": "current candidate contract and selected topology are already valid",
                "candidateContractVersions": sorted(candidate_versions),
            }
        if not getattr(project, "excavation", None):
            return {"attempted": False, "reason": "missing excavation"}

        if task is not None:
            self._stage(task, 6, "检测到旧候选或缺失当前支撑体系，正在执行有界拓扑恢复")
        ensure_geological_model_covers_excavation(project)
        if project.retaining_system is None or not project.retaining_system.diaphragm_walls:
            project.retaining_system = auto_diaphragm_wall(project.excavation, project.retaining_system, project.design_settings)
        runtime_cap = max(6, min(24, int(os.getenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "12"))))

        def progress(index: int, total: int, family: str) -> None:
            if task is not None:
                self._stage(task, 6 + int(18 * max(0, min(index, total)) / max(total, 1)), f"拓扑恢复候选 {index}/{total} · {family}")

        result = auto_repair_support_layout(
            project,
            preset="balanced",
            max_candidates=3,
            search_config={
                "coreMode": True,
                "maxTrials": runtime_cap,
                "candidatePoolLimit": 6,
                "maxSupportElements": 800,
                "requireDiverseSchemes": True,
                "enableConcaveTransferTemplates": True,
                "concaveTransferTemplates": ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"],
                "recoveryReason": "legacy_or_missing_current_topology",
            },
            progress_callback=progress,
        )
        recovered = bool(result.selected_candidate_id)
        if recovered:
            invalidate_calculation_state(
                project,
                reason="V3.51 bounded adaptive topology recovery replaced legacy diagnostic candidates before calculation",
                rebuild_cases=True,
            )
        repo.save(
            project,
            action="task.calculation.topology_recovery",
            summary=(
                f"Recovered support topology using candidate {result.selected_candidate_id}"
                if recovered else "Topology recovery completed without a formal candidate"
            ),
        )
        memory_event(
            "candidate-geometry",
            "calculation-topology-recovery",
            projectId=project.id,
            taskId=getattr(task, "id", None),
            attempted=True,
            recovered=recovered,
            oldCandidateContractVersions=sorted(candidate_versions),
            candidateContractVersion=SUPPORT_CANDIDATE_CONTRACT_VERSION,
            selectedCandidateId=result.selected_candidate_id,
            candidateCount=len(result.candidates or []),
            supportCount=len(project.retaining_system.supports or []) if project.retaining_system else 0,
            columnCount=len(project.retaining_system.columns or []) if project.retaining_system else 0,
        )
        return {
            "attempted": True,
            "recovered": recovered,
            "selectedCandidateId": result.selected_candidate_id,
            "candidateCount": len(result.candidates or []),
            "candidateContractVersions": sorted(candidate_versions),
        }

    def _assert_calculation_qualified(self, repo: ProjectRepository, project: Any, task: TaskRecord | None = None) -> dict[str, Any]:
        from app.services.calculation_blocker_recovery import (
            apply_safe_input_recovery,
            build_resolution_plan,
            persist_recovery_state,
        )
        from app.services.design_qualification import build_design_qualification
        from app.services.support_layout import normalize_existing_support_wall_connections

        if not bool(project.design_settings.design_basis_confirmed):
            raise ValueError(
                "完整计算已阻断：请先在‘设计基准’中确认工程等级、基坑安全等级、场地条件、"
                "规范体系和荷载组合。"
            )
        topology_repair = normalize_existing_support_wall_connections(project)
        if bool(topology_repair.get("changed")):
            from app.services.calculation_state import invalidate_calculation_state
            invalidate_calculation_state(
                project,
                reason="legacy support wall bearing semantics normalized before calculation qualification",
                rebuild_cases=True,
            )
            repo.save(
                project,
                action="task.calculation.normalize_support_wall_bearings",
                summary=(
                    f"Recovered direction-aware wall/wale bearings for "
                    f"{int(topology_repair.get('changedSupportCount') or 0)} supports before qualification"
                ),
            )
            memory_event(
                "candidate-geometry",
                "legacy-support-bearing-normalized",
                projectId=project.id,
                changedSupportCount=int(topology_repair.get("changedSupportCount") or 0),
                changedSupportCodes=list(topology_repair.get("changedSupportCodes") or [])[:80],
                unresolvedSupportCodes=list(topology_repair.get("unresolvedSupportCodes") or [])[:80],
                targetClearanceM=topology_repair.get("targetClearanceM"),
            )

        def qualify() -> dict[str, Any]:
            return build_design_qualification(
                project,
                storage_info=repo.store.get_payload_info(project.id),
                topology_detail="full",
            )

        before = qualify()
        if bool(before.get("calculationAllowed")):
            return before

        if task is not None:
            self._stage(task, 4, "计算门禁未通过，正在执行安全的前置自修复")
        input_recovery = apply_safe_input_recovery(
            project,
            before,
            progress=(lambda progress, message: self._stage(task, max(4, min(18, progress)), message)) if task is not None else None,
        )
        if bool(input_recovery.get("changed")):
            repo.save(
                project,
                action="task.calculation.safe_input_recovery",
                summary="Applied bounded wall mapping and geological coverage recovery before calculation",
            )

        after_inputs = qualify()
        calculation_blockers = [
            gate for gate in after_inputs.get("gates") or []
            if "calculation" in (gate.get("blocks") or [])
        ]
        blocker_codes = {str(gate.get("code") or "") for gate in calculation_blockers}
        topology_recovery: dict[str, Any] = {"attempted": False}
        if "Q-TOPOLOGY" in blocker_codes:
            topology_recovery = self._attempt_legacy_topology_recovery(repo, project, task, force=True)

        after = qualify()
        recovery_state = persist_recovery_state(
            project,
            before=before,
            after=after,
            recovery={
                "inputRecovery": input_recovery,
                "topologyRecovery": topology_recovery,
            },
        )
        repo.save(
            project,
            action="task.calculation.blocker_recovery",
            summary=(
                "Calculation preflight recovered automatically"
                if bool(after.get("calculationAllowed"))
                else "Calculation preflight still requires engineering intervention"
            ),
        )
        if bool(after.get("calculationAllowed")):
            if task is not None:
                self._append_log(task, "前置阻断已自动修复并重新通过设计资格门禁，继续执行正式计算。")
            return after

        plan = build_resolution_plan(after)
        blockers = []
        for gate in after.get("gates") or []:
            if "calculation" not in (gate.get("blocks") or []):
                continue
            evidence = gate.get("evidence") or {}
            detail = ""
            if gate.get("code") == "Q-TOPOLOGY":
                categories = evidence.get("currentHardFailureCategories") or evidence.get("candidateBlockingCategories") or []
                if categories:
                    detail = f"（控制类别：{', '.join(map(str, categories))}）"
            blockers.append(f"{gate.get('title')}: {gate.get('message')}{detail}")
        manual_actions = [
            str(item.get("manualAction") or "")
            for item in list(plan.get("actions") or [])
            if item.get("manualAction")
        ]
        detail = "；".join(blockers) or "当前设计资格未允许启动完整计算。"
        action_text = "；".join(manual_actions[:4])
        raise ValueError(
            "完整计算已由设计资格门禁阻断。系统已完成可安全自动执行的几何、地质覆盖和拓扑修复，但仍有未关闭问题："
            + detail
            + (f" 处理方法：{action_text}" if action_text else "")
            + "。可在计算验算页点击‘自动诊断、修复并复算’查看结构化修复结果。"
        )

    def _run_core_design(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the minimum dependable design chain.

        The core path intentionally avoids industrial maturity suites, benchmark
        cases, monitoring calibration, multi-profile IFC and repeated automatic
        A/B/C full calculations.  It keeps up to three topology/spacing alternatives,
        adopts one qualified scheme for the current calculation and applies one
        reinforcement scheme.  Full A/B/C comparison remains an explicit task.
        """
        repo = self._repo()
        # Orchestration only needs the bounded workspace projection.  Keeping a
        # fully hydrated project alive while the optimization and solver each
        # hydrate their own copy previously doubled peak memory on large jobs.
        workspace_project = repo.require_workspace(task.project_id)
        if not bool(workspace_project.design_settings.design_basis_confirmed):
            raise ValueError(
                "请先确认设计基准，包括工程等级、基坑安全等级、场地复杂程度、荷载组合和材料设计取值。"
            )
        if workspace_project.excavation is None:
            raise ValueError("请先录入闭合基坑轮廓、坑顶标高和坑底标高。")
        if not workspace_project.strata and not workspace_project.boreholes:
            raise ValueError("请先导入地层或钻孔数据。")

        self._stage(task, 6, "校核核心输入与地质覆盖")
        self._resource_preflight(task, workspace_project, candidate_count=0)
        self._check_cancel(task)
        del workspace_project
        self._memory_checkpoint(task, "核心输入检查完成")

        # Reuse the proven topology generator, but cap the search at three
        # candidates.  The selected scheme is written back before calculation.
        design_payload = {
            "preset": str(payload.get("preset") or "balanced"),
            "topologyFamily": payload.get("topologyFamily"),
            "maxCandidates": max(1, min(3, int(payload.get("maxCandidates") or 3))),
            "objectiveWeights": dict(payload.get("objectiveWeights") or {}),
            "searchConfig": {
                **dict(payload.get("searchConfig") or {}),
                "coreMode": True,
                "requireDiverseSchemes": bool(dict(payload.get("searchConfig") or {}).get("requireDiverseSchemes", True)),
                "enableConcaveTransferTemplates": bool(dict(payload.get("searchConfig") or {}).get("enableConcaveTransferTemplates", True)),
                "concaveTransferTemplates": list(dict(payload.get("searchConfig") or {}).get("concaveTransferTemplates") or ["compact_elbow_ring", "junction_hub_frame", "ring_chord_frame"]),
            },
        }
        self._stage(task, 18, "生成围护墙与最多三个可施工支撑候选")
        design_result = self._run_support_layout_optimization(task, design_payload)
        self._check_cancel(task)
        self._memory_checkpoint(task, "候选方案生成完成")
        if not design_result.get("selectedCandidateId"):
            raise ValueError(
                "未生成通过硬约束的正式围护支撑方案。系统已保留真实几何不同的诊断候选，"
                "请调整结构体系、分区或支撑约束后重新生成；诊断候选不会自动进入计算和配筋。"
            )

        self._stage(task, 46, "执行当前采用方案的施工阶段计算")
        calculation_result = self._run_calculation_full(task, {"topN": 0})
        self._check_cancel(task)
        self._memory_checkpoint(task, "施工阶段计算完成")

        self._stage(task, 82, "按当前内力包络完成墙、围檩和支撑配筋")
        from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme
        # Calculation stage envelopes are externalized after persistence.  A
        # reinforcement task must hydrate the authoritative latest result;
        # loading only the compact project made a valid calculation look as if
        # it had no construction-stage data and caused a false "structure not
        # closed" gate.
        project = repo.require_with_latest_calculation(task.project_id)
        rebar_mode = str(payload.get("rebarMode") or "balanced")
        if rebar_mode not in {"conservative", "balanced", "economic"}:
            rebar_mode = "balanced"
        rebar_scheme = apply_rebar_design_scheme(project, mode=rebar_mode)
        rebar_recalculation_count = 0
        while bool(rebar_scheme.get("requiresRecalculation")) and rebar_recalculation_count < 3:
            rebar_recalculation_count += 1
            self._stage(task, 86 + min(rebar_recalculation_count, 3), f"第 {rebar_recalculation_count} 轮截面补强—复算—重新配筋")
            repo.save(project, action="task.core_design.rebar_section_update", summary="Applied reinforcement-driven section changes before core recalculation")
            calculation_result = self._run_calculation_full(task, {"topN": 0})
            project = repo.require_with_latest_calculation(task.project_id)
            rebar_scheme = apply_rebar_design_scheme(project, mode=rebar_mode)
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["coreDesign"] = {
            "status": "completed",
            "candidateCount": int(design_result.get("candidateCount") or 0),
            "selectedCandidateId": design_result.get("selectedCandidateId"),
            "calculationResultId": calculation_result.get("calculationResultId"),
            "rebarMode": rebar_mode,
            "rebarRecalculatedAfterSectionChange": rebar_recalculation_count > 0,
            "rebarRecalculationCount": rebar_recalculation_count,
            "supportRebarContract": rebar_scheme.get("supportRebarContractSummary"),
            "workflow": ["input", "scheme", "calculation", "reinforcement", "deliverables"],
        }
        repo.save(
            project,
            action="task.core_design",
            summary="Core retaining design, staged calculation and reinforcement completed",
        )
        self._stage(task, 96, "汇总核心结果与交付资格")
        return {
            "projectId": project.id,
            "design": design_result,
            "calculation": calculation_result,
            "rebar": {
                "mode": rebar_mode,
                "status": rebar_scheme.get("status"),
                "checkCount": len(rebar_scheme.get("checks") or []),
            },
            "refreshProject": True,
            "coreFlow": True,
        }

    def _run_rebar_design(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        """Generate or apply a reinforcement scheme in the isolated worker.

        Reinforcement drafting is intentionally separated from construction
        drawing eligibility.  Engineering warnings remain visible in the
        returned scheme; only missing calculation evidence or an invalid
        structural model blocks the operation.
        """
        from app.services.deepening_readiness import calculation_readiness
        from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme, build_rebar_design_scheme

        repo = self._repo()
        project = repo.require_with_latest_calculation(task.project_id)
        if not bool(project.design_settings.design_basis_confirmed):
            raise ValueError("请先确认设计基准和材料设计取值，再生成配筋方案。")
        if project.retaining_system is None:
            raise ValueError("当前项目尚未生成围护结构体系。")
        calculation_gate = calculation_readiness(project)
        if not calculation_gate.get("valid") and bool(payload.get("recalculate", True)):
            self._stage(task, 8, "当前计算证据缺失或已失效，先自动补算当前方案")
            # Do not keep a fully hydrated project and its externalized result
            # envelope alive while the calculation engine hydrates a second
            # working copy.  On large projects this duplication was sufficient
            # to terminate the native worker on macOS.
            project_id = project.id
            del project
            _release_process_memory()
            self._memory_checkpoint(task, "配筋前补算：释放旧工程快照")
            self._run_calculation_full(task, {"topN": 0})
            project = repo.require_with_latest_calculation(project_id)
            calculation_gate = calculation_readiness(project)
        if not calculation_gate.get("valid"):
            details = "；".join(str(item) for item in list(calculation_gate.get("messages") or [])[:4])
            raise ValueError(
                "配筋入口校验未通过：" + details
                + "。请运行当前方案完整计算，确认计算合同与当前设计快照一致，并关闭计算质量硬失败。"
            )
        mode = str(payload.get("mode") or payload.get("rebarMode") or "balanced")
        if mode not in {"conservative", "balanced", "economic"}:
            mode = "balanced"
        apply_scheme = bool(payload.get("apply", True))
        recalculate = bool(payload.get("recalculate", True))
        append_event("rebar-task", "task-start", taskId=task.id, projectId=project.id, mode=mode, apply=apply_scheme, recalculate=recalculate)
        self._stage(task, 12, "读取当前计算包络与构件截面")
        self._enforce_memory_budget(task, "配筋深化")
        self._stage(task, 34, "生成围护墙、围檩、支撑和节点配筋草案")
        scheme = apply_rebar_design_scheme(project, mode=mode) if apply_scheme else build_rebar_design_scheme(project, mode=mode)
        self._check_cancel(task)
        recalculation_count = 0
        while apply_scheme and bool(scheme.get("requiresRecalculation")) and recalculate and recalculation_count < 3:
            recalculation_count += 1
            # Section changes alter stiffness, axial-force distribution and node
            # bearing. Persist the first-pass proposal, rerun the adopted scheme,
            # then regenerate reinforcement against the updated envelope. This
            # avoids the former dead-end where clicking "深化配筋" immediately
            # invalidated calculation results and left the page unusable.
            self._stage(task, 48 + recalculation_count * 10, f"第 {recalculation_count} 轮应用截面补强并重新计算")
            project.advanced_engineering = dict(project.advanced_engineering or {})
            project.advanced_engineering["rebarDesignState"] = {"mode": mode, "status": "recalculating_after_section_change", "checkCount": len(scheme.get("checks") or []), "requiresRecalculation": True}
            section_change_count = int((scheme.get("diagnostics") or {}).get("sectionChangeCount") or 0)
            # The full applied scheme can contain several thousand checks.  Keep
            # the structural changes on the member objects, but persist only a
            # compact audit record during the expensive recalculation.
            project.retaining_system.rebar_design_scheme = _compact_rebar_scheme_for_recalculation(scheme, mode)
            repo.save(project, action="task.rebar_design.section_update", summary="Applied reinforcement-driven section updates before recalculation")
            append_event("rebar-task", "section-recalculation-start", taskId=task.id, projectId=project.id, sectionChangeCount=section_change_count)
            project_id = project.id
            scheme = {}
            del project
            _release_process_memory()
            self._memory_checkpoint(task, f"第 {recalculation_count} 轮配筋方案压缩后")
            self._run_calculation_full(task, {"topN": 0})
            project = repo.require_with_latest_calculation(project_id)
            self._stage(task, 76, "按更新后内力包络重新生成并应用配筋")
            scheme = apply_rebar_design_scheme(project, mode=mode)
            append_event("rebar-task", "section-recalculation-complete", taskId=task.id, projectId=project.id, iteration=recalculation_count, remainingSectionChangeCount=int((scheme.get("diagnostics") or {}).get("sectionChangeCount") or 0))
        self._check_cancel(task)
        self._stage(task, 84, "执行承载力、构造和可施工性检查")
        if apply_scheme:
            project.advanced_engineering = dict(project.advanced_engineering or {})
            project.advanced_engineering["rebarDesignState"] = {
                "mode": mode,
                "status": scheme.get("status"),
                "checkCount": len(scheme.get("checks") or []),
                "requiresRecalculation": bool(scheme.get("requiresRecalculation")),
                "recalculatedAfterSectionChange": recalculation_count > 0,
                "recalculationCount": recalculation_count,
                "supportRebarContract": scheme.get("supportRebarContractSummary"),
                "deepeningGate": {
                    "status": (scheme.get("diagnostics") or {}).get("deepeningGate", {}).get("status"),
                    "blockerCount": (scheme.get("diagnostics") or {}).get("deepeningGate", {}).get("blockerCount"),
                    "warningCount": (scheme.get("diagnostics") or {}).get("deepeningGate", {}).get("warningCount"),
                },
            }
            repo.save(
                project,
                action="task.rebar_design",
                summary=f"Applied {mode} reinforcement scheme in isolated worker",
            )
            # Re-read the just-persisted canonical project together with its
            # externalized latest stage evidence, then rebuild the gate once.
            # This prevents the worker response and the API page from exposing a
            # stale pre-save CALCULATION_NOT_CURRENT diagnosis.
            project = repo.require_with_latest_calculation(project.id)
            final_scheme = build_rebar_design_scheme(project, mode=mode, scheme_applied_override=True)
            project.retaining_system.rebar_design_scheme = final_scheme
            repo.save(
                project,
                action="task.rebar_design.finalize_contract",
                summary="Rebuilt reinforcement gate from authoritative persisted calculation evidence",
            )
            scheme = final_scheme
        self._memory_checkpoint(task, "配筋深化完成")
        self._stage(task, 94, "汇总配筋结果与出图资格")
        diagnostics = dict(scheme.get("diagnostics") or {})
        deepening_gate = dict(diagnostics.get("deepeningGate") or {})
        return {
            "projectId": project.id,
            "mode": mode,
            "applied": apply_scheme,
            "status": scheme.get("status"),
            "checkCount": len(scheme.get("checks") or []),
            "failCount": int((scheme.get("summary") or {}).get("failCount") or 0),
            "warningCount": int((scheme.get("summary") or {}).get("warningCount") or 0),
            "canIssueConstructionDrawings": bool(diagnostics.get("canIssueConstructionDrawings")),
            "canEnterDetailing": bool(deepening_gate.get("canEnterDetailing")),
            "canRunP3": bool(deepening_gate.get("canRunP3")),
            "deepeningGate": {
                "status": deepening_gate.get("status"),
                "blockerCount": deepening_gate.get("blockerCount"),
                "warningCount": deepening_gate.get("warningCount"),
                "blockers": list(deepening_gate.get("blockers") or [])[:12],
                "nextActions": list(deepening_gate.get("nextActions") or [])[:12],
            },
            "requiresRecalculation": bool(scheme.get("requiresRecalculation")),
            "recalculatedAfterSectionChange": recalculation_count > 0 if apply_scheme else False,
            "recalculationCount": recalculation_count if apply_scheme else 0,
            "supportRebarContract": scheme.get("supportRebarContractSummary"),
            "refreshProject": apply_scheme,
        }

    def _run_formal_adverse_scenarios(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.adverse_scenario_execution import run_formal_adverse_scenario_suite
        from app.storage.artifact_store import ProjectArtifactStore

        repo = self._repo()
        project = repo.require(task.project_id)
        self._assert_calculation_qualified(repo, project, task)
        if not project.calculation_results:
            raise ValueError("请先完成当前采用方案的施工阶段计算，再执行正式不利工况复算。")
        codes = [str(item) for item in list(payload.get("codes") or project.design_settings.formal_adverse_scenario_codes)]
        self._stage(task, 6, "准备正式不利工况复算")
        self._resource_preflight(task, project, candidate_count=0)

        def report(progress: int, message: str) -> None:
            self._stage(task, max(8, min(86, int(progress))), message)
            self._enforce_memory_budget(task, message)

        suite = run_formal_adverse_scenario_suite(project, codes, progress=report)
        full_results = list(suite.pop("fullResults", []) or [])
        self._stage(task, 88, "外部化不利工况完整计算结果")
        artifact = ProjectArtifactStore().write_json(
            project.id,
            "formal-adverse-scenarios",
            full_results,
            metadata={
                "scenarioCount": len(full_results),
                "requestedCodes": codes,
                "softwareVersion": SOFTWARE_VERSION,
            },
        )
        suite["artifact"] = artifact
        suite["calculatedAt"] = _now()
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["formalAdverseScenarioSuite"] = suite
        latest = project.calculation_results[-1]
        latest.report_diagram_data = dict(latest.report_diagram_data or {})
        latest.report_diagram_data["formalAdverseScenarioSuite"] = suite
        repo.save(
            project,
            action="task.formal_adverse_scenarios",
            summary=f"Completed {len(full_results)} formal adverse-scenario reruns",
        )
        append_event(
            "analysis-scenarios", "formal_rerun_completed",
            projectId=project.id, taskId=task.id,
            scenarioCount=int((suite.get("summary") or {}).get("scenarioCount") or 0),
            failedExecutionCount=int((suite.get("summary") or {}).get("failedExecutionCount") or 0),
            controllingScenarioCode=(suite.get("summary") or {}).get("controllingScenarioCode"),
            minimumSafetyFactor=(suite.get("summary") or {}).get("minimumSafetyFactor"),
            artifactBytes=int((artifact or {}).get("sizeBytes") or 0),
        )
        self._memory_checkpoint(task, "正式不利工况复算完成")
        self._stage(task, 96, "汇总控制工况与安全系数")
        return {
            "projectId": project.id,
            "scenarioCount": int((suite.get("summary") or {}).get("scenarioCount") or 0),
            "failedExecutionCount": int((suite.get("summary") or {}).get("failedExecutionCount") or 0),
            "controllingScenarioCode": (suite.get("summary") or {}).get("controllingScenarioCode"),
            "minimumSafetyFactor": (suite.get("summary") or {}).get("minimumSafetyFactor"),
            "artifact": artifact,
            "refreshProject": True,
        }

    def _run_design_scenario_envelope(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.workflow_v381 import build_scenario_envelope, run_design_scenario_suite
        from app.storage.artifact_store import ProjectArtifactStore

        repo = self._repo()
        project = repo.require(task.project_id)
        self._assert_calculation_qualified(repo, project, task)
        if not project.calculation_results:
            raise ValueError("请先完成当前采用方案的基准施工阶段计算，再执行设计包络情景复算。")
        scenario_ids = [str(item) for item in list(payload.get("scenarioIds") or [])]
        max_scenarios = max(1, min(int(payload.get("maxScenarios") or 12), 50))
        self._stage(task, 6, "准备已批准设计情景的隔离复算")
        self._resource_preflight(task, project, candidate_count=0)

        def report(progress: int, message: str) -> None:
            self._stage(task, max(8, min(88, int(progress))), message)
            self._enforce_memory_budget(task, message)

        suite = run_design_scenario_suite(
            project,
            scenario_ids or None,
            max_scenarios=max_scenarios,
            progress=report,
        )
        full_results = list(suite.pop("fullResults", []) or [])
        self._stage(task, 90, "外部化设计包络情景完整结果")
        artifact = ProjectArtifactStore().write_json(
            project.id,
            "design-scenario-envelope",
            full_results,
            metadata={
                "scenarioCount": len(full_results),
                "requestedScenarioIds": suite.get("requestedScenarioIds"),
                "softwareVersion": SOFTWARE_VERSION,
            },
        )
        suite["artifact"] = artifact
        suite["calculatedAt"] = _now()
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["designScenarioExecution"] = suite
        envelope = build_scenario_envelope(project)
        latest = project.calculation_results[-1]
        latest.report_diagram_data = dict(latest.report_diagram_data or {})
        latest.report_diagram_data["designScenarioExecution"] = suite
        latest.report_diagram_data["designScenarioEnvelope"] = envelope
        repo.save(
            project,
            action="task.design_scenario_envelope",
            summary=f"Completed {len(full_results)} approved design-scenario reruns",
        )
        append_event(
            "analysis-scenarios", "design_envelope_completed",
            projectId=project.id, taskId=task.id,
            completedCount=int((suite.get("summary") or {}).get("completedCount") or 0),
            failedExecutionCount=int((suite.get("summary") or {}).get("failedExecutionCount") or 0),
            pendingFormalScenarioCount=len(envelope.get("pendingFormalScenarioCodes") or []),
            artifactBytes=int((artifact or {}).get("sizeBytes") or 0),
        )
        self._memory_checkpoint(task, "设计包络情景复算完成")
        self._stage(task, 97, "汇总控制情景与构件包络")
        return {
            "projectId": project.id,
            "completedCount": int((suite.get("summary") or {}).get("completedCount") or 0),
            "failedExecutionCount": int((suite.get("summary") or {}).get("failedExecutionCount") or 0),
            "skippedByLimitCount": int((suite.get("summary") or {}).get("skippedByLimitCount") or 0),
            "pendingFormalScenarioCount": len(envelope.get("pendingFormalScenarioCodes") or []),
            "envelopeStatus": envelope.get("status"),
            "artifact": artifact,
            "refreshProject": True,
        }

    def _run_p3_detailing_closure(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.p3_detailing_closure import build_p3_detailing_closure
        from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
        from app.storage.artifact_store import ProjectArtifactStore

        repo = self._repo()
        # P3 must hydrate the authoritative stage-result chunks.  Loading the
        # compact canonical project alone makes a valid externalized calculation
        # appear to have no stage evidence and creates a false
        # CALCULATION_NOT_CURRENT blocker.
        project = repo.require_with_latest_calculation(task.project_id)
        if not bool(project.design_settings.design_basis_confirmed):
            raise ValueError("请先确认设计基准。")
        if project.retaining_system is None or not project.calculation_results:
            raise ValueError("请先完成围护方案和当前施工阶段计算。")
        if not project.retaining_system.rebar_design_scheme:
            raise ValueError("请先生成并应用配筋方案。")
        mode = str(payload.get("mode") or "balanced")
        if mode not in {"conservative", "balanced", "economic"}:
            mode = "balanced"

        def _entry_contract(current_project: Project) -> tuple[dict[str, Any], dict[str, Any]]:
            scheme = build_rebar_design_scheme(current_project, mode=mode, scheme_applied_override=True)
            gate = dict((scheme.get("diagnostics") or {}).get("deepeningGate") or {})
            return scheme, gate

        entry_scheme, entry_gate = _entry_contract(project)
        groups = list(entry_gate.get("blockers") or [])
        auto_repairable = bool(groups) and all(
            bool(row.get("autoResolvable"))
            or str(row.get("reasonCode") or "") in {"CALCULATION_NOT_CURRENT", "SECTION_CHANGE_RECALCULATION_REQUIRED"}
            for row in groups
        )
        if not entry_gate.get("canRunP3") and auto_repairable:
            self._stage(task, 5, "P3 前自动恢复当前计算合同并重新闭合配筋")
            project_id = project.id
            del project
            _release_process_memory()
            # Reuse the bounded reinforcement closure.  It hydrates the latest
            # calculation, recalculates only when required, reapplies the scheme
            # and persists the final authoritative contract.
            self._run_rebar_design(task, {"mode": mode, "apply": True, "recalculate": True})
            project = repo.require_with_latest_calculation(project_id)
            entry_scheme, entry_gate = _entry_contract(project)
            groups = list(entry_gate.get("blockers") or [])
        if not entry_gate.get("canRunP3"):
            details = "；".join(
                f"{row.get('title')} {row.get('count')} 项（{row.get('message') or row.get('requiredAction')}）"
                for row in groups[:4]
            )
            raise ValueError(
                f"P3 深化入口仍有 {int(entry_gate.get('blockerCount') or 0)} 个阻断：{details or '请查看配筋深化入口诊断'}。"
            )
        top_nodes = max(1, min(20, int(payload.get("topNodeCount") or 8)))
        self._stage(task, 5, "准备企业节点与钢筋深化闭环")
        self._resource_preflight(task, project, candidate_count=0)

        def report(progress: int, message: str) -> None:
            self._stage(task, max(6, min(88, int(progress))), message)
            self._enforce_memory_budget(task, message)

        closure = build_p3_detailing_closure(project, mode=mode, progress=report, top_node_count=top_nodes)
        compact = dict(closure.get("compact") or {})
        full = dict(closure.get("full") or {})
        self._stage(task, 90, "外部化逐根钢筋、节点子模型和碰撞数据")
        artifact = ProjectArtifactStore().write_json(
            project.id,
            "p3-detailing-closure",
            full,
            metadata={
                "mode": mode,
                "status": compact.get("status"),
                "softwareVersion": SOFTWARE_VERSION,
                "nodeSubmodelCount": int((compact.get("summary") or {}).get("nodeSubmodelCount") or 0),
            },
        )
        compact["artifact"] = artifact
        compact["calculatedAt"] = _now()
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["p3DetailingClosure"] = compact
        project.retaining_system.rebar_design_scheme = dict(project.retaining_system.rebar_design_scheme or {})
        project.retaining_system.rebar_design_scheme["p3DetailingClosure"] = compact
        repo.save(
            project,
            action="task.p3_detailing_closure",
            summary="Completed enterprise node, reinforcement and spatial coordination closure",
        )
        append_event(
            "rebar-detailing", "p3_closure_completed",
            projectId=project.id, taskId=task.id, mode=mode,
            status=compact.get("status"), summary=compact.get("summary"),
            blockingGroups=list(compact.get("blockingGroups") or [])[:12],
            warningGroups=list(compact.get("warningGroups") or [])[:12],
            resolutionGuide=list(compact.get("resolutionGuide") or [])[:12],
            artifactBytes=int((artifact or {}).get("sizeBytes") or 0),
        )
        self._memory_checkpoint(task, "P3节点与钢筋深化闭环完成")
        self._stage(task, 97, "汇总节点、套筒、锚固与碰撞校核")
        return {
            "projectId": project.id,
            "mode": mode,
            "status": compact.get("status"),
            "summary": compact.get("summary"),
            "artifact": artifact,
            "refreshProject": True,
        }

    def _run_support_layout_optimization(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.support_layout_repair import auto_repair_support_layout
        from app.services.calculation_state import invalidate_calculation_state
        from app.geology.model_builder import ensure_geological_model_covers_excavation
        from app.services.design_service import auto_diaphragm_wall
        repo = self._repo()
        project = repo.require(task.project_id)
        if not bool(getattr(project.design_settings, "design_basis_confirmed", False)):
            raise ValueError("请先确认工程等级、基坑安全等级、规范体系和荷载组合，再生成围护方案。")
        if project.excavation is None:
            raise ValueError("Project has no excavation")
        self._resource_preflight(task, project, candidate_count=0)
        self._stage(task, 10, "检查地质模型覆盖与平面类型")
        ensure_geological_model_covers_excavation(project)
        if project.retaining_system is None or not project.retaining_system.diaphragm_walls:
            project.retaining_system = auto_diaphragm_wall(project.excavation, project.retaining_system, project.design_settings)
        self._stage(task, 28, "按渐进式设计配置生成受力可闭合的支撑候选")
        from app.services.progressive_design import task_payload_from_progressive_config
        session_payload = task_payload_from_progressive_config(
            repo.store.get_progressive_design_config(task.project_id)
        )
        effective_payload = dict(session_payload)
        effective_payload.update({key: value for key, value in payload.items() if value is not None})
        search_config = dict(effective_payload.get("searchConfig") or {})
        requested_trials = int(search_config.get("maxTrials") or 0)
        product_mode = str(os.getenv("PITGUARD_PRODUCT_MODE", "core") or "core").strip().lower()
        core_product = product_mode != "full"
        runtime_cap = max(6, min(24, int(os.getenv("PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT", "12"))))
        if core_product:
            search_config["coreMode"] = True
            search_config["maxTrials"] = min(requested_trials or runtime_cap, runtime_cap)
            search_config["candidatePoolLimit"] = min(
                int(search_config.get("candidatePoolLimit") or 6),
                6,
            )
            search_config["maxSupportElements"] = min(
                int(search_config.get("maxSupportElements") or 800),
                800,
            )
        memory_event(
            "candidate-search",
            "search-budget-resolved",
            taskId=task.id,
            projectId=project.id,
            productMode=product_mode,
            requestedMaxTrials=requested_trials or None,
            effectiveMaxTrials=int(search_config.get("maxTrials") or runtime_cap),
            candidatePoolLimit=int(search_config.get("candidatePoolLimit") or 6),
            maxSupportElements=int(search_config.get("maxSupportElements") or 800),
        )
        def report_candidate_progress(index: int, total: int, family: str) -> None:
            progress = 28 + int(42 * max(0, min(index, total)) / max(total, 1))
            self._stage(task, progress, f"生成支撑候选 {index}/{total} · {family}")

        result = auto_repair_support_layout(
            project,
            objective_weights=dict(effective_payload.get("objectiveWeights") or effective_payload.get("objective_weights") or {}),
            preset=str(effective_payload.get("preset") or "balanced"),
            topology_family=(str(effective_payload.get("topologyFamily") or effective_payload.get("topology_family") or "").strip() or None),
            max_candidates=max(1, min(8, int(effective_payload.get("maxCandidates") or 5))),
            search_config=search_config,
            progress_callback=report_candidate_progress,
        )
        self._stage(task, 82, "执行零非法交叉、墙—墙传力与围檩跨审查")
        if result.selected_candidate_id:
            invalidate_calculation_state(
                project,
                reason="support optimization candidate set regenerated and a qualified scheme was selected by isolated worker",
                rebuild_cases=True,
            )
        from app.services.support_scheme_designer_audit import audit_support_scheme_designer
        project.retaining_system.layout_summary = dict(project.retaining_system.layout_summary or {})
        project.retaining_system.layout_summary["schemeDesignerAudit"] = audit_support_scheme_designer(project)
        repo.save(project)
        return {
            "projectId": project.id,
            "candidateCount": len(result.candidates or []),
            "status": result.status,
            "selectedCandidateId": result.selected_candidate_id,
            "progressiveDesignConfigApplied": effective_payload,
            "refreshProject": True,
        }

    def _run_adopt_support_candidate(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.support_layout_repair import adopt_support_layout_candidate
        repo = self._repo()
        candidate_id = str(payload.get("candidateId") or payload.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("缺少候选方案 ID。")
        self._stage(task, 12, "读取候选摘要并清理历史重复数据")
        project = repo.require(task.project_id)
        self._enforce_memory_budget(task, "采用候选方案")
        self._stage(task, 42, "按候选参数重建围护支撑体系")
        result = adopt_support_layout_candidate(project, candidate_id)
        # Engineering quality may still be ``fail`` or ``warning`` after a
        # candidate is applied. That is a design conclusion, not an execution
        # failure. Treat the task as operationally successful once the selected
        # candidate is present in the rebuilt system; only missing/rebuild errors
        # should fail the worker task.
        applied_candidate_id = str(getattr(result, "selected_candidate_id", "") or "")
        if applied_candidate_id != candidate_id:
            raise ValueError(result.summary)
        self._stage(task, 76, "保存采用方案并失效旧计算结果")
        repo.save(
            project,
            action="task.adopt_support_candidate",
            summary=f"Adopted support candidate {candidate_id} using bounded reconstruction",
        )
        self._memory_checkpoint(task, "候选方案采用完成")
        return {
            "projectId": project.id,
            "candidateId": candidate_id,
            "status": result.status,
            "summary": result.summary,
            "refreshProject": True,
        }

    def _run_calculation_recovery(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        """Run bounded preflight repair, then force the verification-strengthening loop.

        Unrecoverable survey-coordinate or specialist-system decisions are
        returned as structured intervention items instead of leaving the user
        with a generic failed-task message.
        """
        from app.services.calculation_blocker_recovery import build_resolution_plan
        from app.services.design_qualification import build_design_qualification

        repo = self._repo()
        project = repo.require(task.project_id)
        self._stage(task, 2, "读取当前设计资格与阻断证据")
        try:
            qualification = self._assert_calculation_qualified(repo, project, task)
        except ValueError as exc:
            project = repo.require(task.project_id)
            qualification = build_design_qualification(
                project,
                storage_info=repo.store.get_payload_info(project.id),
                topology_detail="full",
            )
            plan = build_resolution_plan(qualification)
            self._stage(task, 96, "已完成自动修复，剩余问题需要工程确认")
            return {
                "projectId": project.id,
                "status": "needs_intervention",
                "message": str(exc),
                "qualification": qualification,
                "resolutionPlan": plan,
                "recoveryState": dict((project.advanced_engineering or {}).get("calculationBlockerRecovery") or {}),
                "refreshProject": True,
            }

        self._append_log(task, "计算前置门禁已关闭，进入验算—优化—再验算闭环。")
        calculation_payload = dict(payload)
        calculation_payload["forceClosure"] = True
        calculation_payload.setdefault("closureStrategy", "balanced")
        calculation_payload.setdefault("closureMaxIterations", 4)
        result = self._run_calculation_full(task, calculation_payload)
        result["status"] = "calculated"
        result["qualification"] = qualification
        project = repo.require(task.project_id)
        result["recoveryState"] = dict((project.advanced_engineering or {}).get("calculationBlockerRecovery") or {})
        return result

    def _run_calculation_closure_action(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.construction_stages import select_calculation_case_for_run
        from app.services.intelligent_design_closure import apply_intervention_action, run_intelligent_design_closure
        from app.services.calculation_state import mark_calculation_state_current
        from app.services.wall_length_optimizer import mark_wall_length_recalculated
        from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme

        repo = self._repo()
        project = repo.require_for_calculation(task.project_id)
        self._assert_calculation_qualified(repo, project, task)
        action_id = str(payload.get("actionId") or "").strip()
        if not action_id:
            raise ValueError("缺少 actionId。")
        strategy = str(payload.get("strategy") or "") or None
        if action_id.startswith("run-strategy:"):
            strategy = action_id.split(":", 1)[1]
        previous_result = project.calculation_results[-1] if project.calculation_results else None
        previous_closure = dict((previous_result.design_iteration_summary or {}).get("intelligentDesignClosure") or {}) if previous_result else {}
        requested_source_result_id = str(payload.get("sourceResultId") or "").strip()
        if previous_result and requested_source_result_id and requested_source_result_id != previous_result.id:
            self._append_log(
                task,
                f"界面建议来自历史结果 {requested_source_result_id}；已切换为当前结果 {previous_result.id}，并按当前持久化构件值执行相对增量。",
            )
        self._stage(task, 18, "从当前持久化截面应用相对加固措施")
        applied = apply_intervention_action(project, action_id, payload.get("value"))
        case, stage_selection = select_calculation_case_for_run(project)
        self._stage(task, 42, "重新运行验算—优化—再验算闭环")
        result, closure = run_intelligent_design_closure(
            project,
            case,
            auto_repair=not bool(stage_selection.get("preserved")),
            strategy=strategy,
            max_iterations=payload.get("maxIterations") or 4,
        )
        result.design_iteration_summary = dict(result.design_iteration_summary or {})
        result.design_iteration_summary["constructionStageSelection"] = stage_selection
        project.calculation_results.append(result)
        mark_calculation_state_current(project, result.id)
        mark_wall_length_recalculated(project, result.id)

        rebar_summary: dict[str, Any] | None = None
        rebar_recalculation_count = 0
        if bool(payload.get("regenerateRebar", True)) and project.retaining_system is not None:
            self._stage(task, 74, "按更新后的结构内力重新生成配筋")
            saved_scheme = project.retaining_system.rebar_design_scheme
            saved_mode = saved_scheme.get("mode") if isinstance(saved_scheme, dict) else None
            mode = str(payload.get("rebarMode") or saved_mode or "balanced")
            if mode not in {"conservative", "balanced", "economic"}:
                mode = "balanced"
            scheme = apply_rebar_design_scheme(project, mode=mode)
            while bool(scheme.get("requiresRecalculation")) and rebar_recalculation_count < 2:
                rebar_recalculation_count += 1
                self._stage(task, 78 + rebar_recalculation_count * 6, f"配筋截面调整后第 {rebar_recalculation_count} 轮复算")
                result, closure = run_intelligent_design_closure(
                    project,
                    case,
                    auto_repair=not bool(stage_selection.get("preserved")),
                    strategy=strategy,
                    max_iterations=payload.get("maxIterations") or 4,
                )
                result.design_iteration_summary = dict(result.design_iteration_summary or {})
                result.design_iteration_summary["constructionStageSelection"] = stage_selection
                project.calculation_results.append(result)
                mark_calculation_state_current(project, result.id)
                mark_wall_length_recalculated(project, result.id)
                scheme = apply_rebar_design_scheme(project, mode=mode)
            rebar_summary = {
                "mode": mode,
                "status": scheme.get("status"),
                "checkCount": len(scheme.get("checks") or []),
                "requiresRecalculation": bool(scheme.get("requiresRecalculation")),
                "recalculationCount": rebar_recalculation_count,
            }

        closure = dict(closure)
        closure["previousResultId"] = previous_result.id if previous_result else None
        closure["previousClosure"] = {
            key: previous_closure.get(key)
            for key in ("status", "hardFailCount", "structuralFailCount", "quantitativeOpenCount", "calculationClosed")
        }
        closure["lastAppliedAction"] = applied
        closure["appliedInterventions"] = [applied]
        closure["appliedInterventionCount"] = 1
        closure["designChanged"] = bool(applied.get("changed", True))
        previous_governing = previous_result.governing_values.model_dump(mode="json") if previous_result else None
        current_governing = result.governing_values.model_dump(mode="json")
        input_changed = previous_result is None or (
            previous_result.input_snapshot_hash != result.input_snapshot_hash
            or previous_result.adopted_design_snapshot_hash != result.adopted_design_snapshot_hash
        )
        numerical_result_changed = previous_result is None or (
            previous_result.result_hash != result.result_hash
            or previous_governing != current_governing
            or previous_result.check_summary != result.check_summary
        )
        closure["inputChanged"] = input_changed
        closure["numericalResultChanged"] = numerical_result_changed
        closure["resultChanged"] = bool(input_changed or numerical_result_changed)
        closure["resultChangeEvidence"] = {
            "previousInputSnapshotHash": previous_result.input_snapshot_hash if previous_result else None,
            "currentInputSnapshotHash": result.input_snapshot_hash,
            "previousDesignSnapshotHash": previous_result.adopted_design_snapshot_hash if previous_result else None,
            "currentDesignSnapshotHash": result.adopted_design_snapshot_hash,
            "previousResultHash": previous_result.result_hash if previous_result else None,
            "currentResultHash": result.result_hash,
            "previousGoverningValues": previous_governing,
            "currentGoverningValues": current_governing,
        }
        closure["rebarRegenerated"] = rebar_summary is not None
        result.design_iteration_summary = dict(result.design_iteration_summary or {})
        result.design_iteration_summary["intelligentDesignClosure"] = closure
        result.report_diagram_data = dict(result.report_diagram_data or {})
        result.report_diagram_data["intelligentDesignClosure"] = closure
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["lastCalculationClosureAction"] = {
            "actionId": action_id,
            "applied": applied,
            "previousResultId": previous_result.id if previous_result else None,
            "newResultId": result.id,
            "resultChanged": closure["resultChanged"],
            "rebar": rebar_summary,
        }
        repo.save(
            project,
            action="task.calculation.intelligent_closure_action",
            summary=f"Applied relative intelligent closure action {action_id} and recalculated",
        )
        self._stage(task, 96, "加固措施、重新验算和配筋刷新已完成")
        return {
            "projectId": project.id,
            "calculationResultId": result.id,
            "appliedAction": applied,
            "closureSummary": closure,
            "rebar": rebar_summary,
            "status": "calculated" if closure.get("calculationClosed") else "needs_intervention",
            "message": (
                f"{applied.get('objectCode') or action_id} 已由 {applied.get('before')} 调整为 {applied.get('after')}，并完成重新计算。"
                if applied.get("changed", True)
                else "当前构件未产生可继续的自动调整。"
            ),
            "refreshProject": True,
        }

    def _run_calculation_optimization_search(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        """Evaluate bounded strengthening portfolios and publish the best design."""
        from app.services.construction_stages import select_calculation_case_for_run
        from app.services.calculation_state import mark_calculation_state_current
        from app.services.wall_length_optimizer import mark_wall_length_recalculated
        from app.services.intelligent_design_optimizer import run_bounded_optimization_search
        from app.services.intelligent_design_closure import run_intelligent_design_closure
        from app.services.rebar_scheme_optimizer import apply_rebar_design_scheme

        from app.services.workflow_v381 import repair_design_control_support_references

        repo = self._repo()
        project = repo.require(task.project_id)
        self._stage(task, 2, "检查设计资格并修复可审计的前置阻断")
        self._assert_calculation_qualified(repo, project, task)
        project = repo.require(task.project_id)
        self._stage(task, 5, "按支撑层语义恢复施工阶段与换撑路径")
        stage_repair = repair_design_control_support_references(
            project,
            allow_standard_transfer_rebuild=True,
        )
        if stage_repair.get("changed"):
            repo.save(
                project,
                action="task.calculation.repair_design_control_supports",
                summary=(
                    f"Remapped {stage_repair.get('automaticStageCount', 0)} stages and "
                    f"rebuiltTransfer={stage_repair.get('transferSequenceRebuilt', False)}"
                ),
            )
            self._append_log(
                task,
                f"已自动恢复 {stage_repair.get('automaticStageCount', 0)} 个施工阶段；"
                f"换撑语义映射 {stage_repair.get('automaticTransferStageCount', 0)} 个；"
                f"标准换撑序列重建={stage_repair.get('transferSequenceRebuilt', False)}。",
            )
        formal_transfer_review_required = bool(stage_repair.get("manualRequired"))
        self._resource_preflight(task, project, candidate_count=0)
        try:
            case, stage_selection = select_calculation_case_for_run(project)
        except ValueError as exc:
            # Do not discard the entire optimization task when a frozen or
            # user-owned transfer-stage decision remains unresolved. Continue
            # with a current-topology screening case, publish real calculation
            # evidence, and keep the formal-delivery gate blocked.
            from app.calculation.engine import build_default_construction_cases
            case = build_default_construction_cases(project)[0]
            stage_selection = {
                "source": "current_topology_transfer_screening",
                "preserved": False,
                "caseId": case.id,
                "stageCount": len(case.stages),
                "formalTransferReviewRequired": True,
                "formalStageError": str(exc),
                "supportReferenceRepair": stage_repair,
            }
            formal_transfer_review_required = True
            self._append_log(
                task,
                "正式设计控制工况仍有冻结/人工换撑决策；本轮继续采用当前拓扑的保守标准换撑工况进行筛查计算，正式交付保持阻断。",
            )
        if stage_selection.get("source") == "auto_default":
            project.calculation_cases = [case]
        max_candidates = max(2, min(int(payload.get("maxCandidates") or 7), 7))
        max_iterations = max(1, min(int(payload.get("maxIterations") or 6), 10))
        rebar_mode = str(payload.get("rebarMode") or "balanced")
        if rebar_mode not in {"conservative", "balanced", "economic"}:
            rebar_mode = "balanced"

        self._stage(task, 12, "冻结当前施工阶段与设计快照")
        self._check_cancel(task)
        self._stage(task, 18, f"生成并评估 {max_candidates} 个体系—截面联合候选")
        def optimization_progress(done: int, total: int, candidate: dict[str, Any]) -> None:
            partial = max(0.0, min(float(candidate.get("candidateProgress") or 0.0), 0.99))
            fraction = (float(done) + partial) / max(float(total), 1.0)
            progress = 18 + int(58 * fraction)
            label = str(candidate.get("profileLabel") or candidate.get("profileId") or f"候选 {done + 1}")
            status = str(candidate.get("status") or "running")
            if status == "running":
                state = "计算中"
                ordinal = min(done + 1, total)
            elif status == "evaluated":
                state = "完成"
                ordinal = done
            else:
                state = "失败"
                ordinal = done
            self._stage(task, progress, f"优化候选 {ordinal}/{total}：{label} {state}")

        result, search = run_bounded_optimization_search(
            project,
            case,
            max_candidates=max_candidates,
            max_iterations=max_iterations,
            progress_callback=optimization_progress,
            cancel_callback=lambda: self._check_cancel(task),
        )
        self._check_cancel(task)
        # The selected candidate may add a support level or regenerate every
        # support ID. Rebuild the canonical calculation case before any rebar
        # feedback calculation so the final result cannot fall back to the
        # pre-search stage contract. A frozen/user-owned transfer path may still
        # be formally stale after candidate adoption; continue with the same
        # current-topology screening contract used during candidate evaluation.
        try:
            case, stage_selection = select_calculation_case_for_run(project)
        except ValueError as exc:
            from app.calculation.engine import build_default_construction_cases
            case = build_default_construction_cases(project)[0]
            stage_selection = {
                "source": "current_topology_transfer_screening",
                "preserved": False,
                "caseId": case.id,
                "stageCount": len(case.stages),
                "formalTransferReviewRequired": True,
                "formalStageError": str(exc),
                "supportReferenceRepair": search.get("designControlSupportRepair") or stage_repair,
            }
            formal_transfer_review_required = True
            self._append_log(
                task,
                "候选采用后正式换撑工况仍需人工确认；配筋反馈复算继续使用当前拓扑筛查工况，正式交付保持阻断。",
            )
        if stage_selection.get("source") == "auto_default":
            project.calculation_cases = [case]
        self._append_log(
            task,
            f"优化搜索完成：评估 {search.get('evaluatedCandidateCount', 0)} 个候选，"
            f"可闭合 {search.get('feasibleCandidateCount', 0)} 个，选择 {search.get('selectedProfile')}。",
        )

        result.design_iteration_summary = dict(result.design_iteration_summary or {})
        result.design_iteration_summary["constructionStageSelection"] = stage_selection
        project.calculation_results.append(result)
        mark_calculation_state_current(project, result.id)
        mark_wall_length_recalculated(project, result.id)

        rebar_summary: dict[str, Any] | None = None
        recalc_count = 0
        if bool(payload.get("applyRebar", True)):
            self._stage(task, 78, "按最优计算包络更新配筋方案")
            scheme = apply_rebar_design_scheme(project, mode=rebar_mode)
            while bool(scheme.get("requiresRecalculation")) and recalc_count < 2:
                recalc_count += 1
                self._stage(task, 80 + recalc_count * 5, f"配筋引起截面变化，第 {recalc_count} 轮复算")
                follow_result, follow_closure = run_intelligent_design_closure(
                    project,
                    case,
                    auto_repair=not bool(stage_selection.get("preserved")),
                    strategy=str(search.get("selectedStrategy") or "balanced"),
                    max_iterations=max_iterations,
                )
                follow_result.design_iteration_summary = dict(follow_result.design_iteration_summary or {})
                follow_result.design_iteration_summary["constructionStageSelection"] = stage_selection
                follow_result.design_iteration_summary["optimizationSearch"] = search
                follow_result.report_diagram_data = dict(follow_result.report_diagram_data or {})
                follow_result.report_diagram_data["optimizationSearch"] = search
                project.calculation_results.append(follow_result)
                result = follow_result
                mark_calculation_state_current(project, result.id)
                mark_wall_length_recalculated(project, result.id)
                scheme = apply_rebar_design_scheme(project, mode=rebar_mode)
                search["postRebarClosure"] = {
                    key: follow_closure.get(key)
                    for key in ("status", "calculationClosed", "structuralClosed", "hardFailCount", "quantitativeOpenCount")
                }
            rebar_summary = {
                "mode": rebar_mode,
                "status": scheme.get("status"),
                "checkCount": len(scheme.get("checks") or []),
                "requiresRecalculation": bool(scheme.get("requiresRecalculation")),
                "recalculationCount": recalc_count,
                "supportRebarContract": scheme.get("supportRebarContractSummary"),
            }

        final_closure = dict((result.design_iteration_summary or {}).get("intelligentDesignClosure") or {})
        calculation_closed = bool(final_closure.get("calculationClosed"))
        final_stage_repair = dict(search.get("designControlSupportRepair") or {})
        formal_transfer_review_required = bool(
            formal_transfer_review_required or final_stage_repair.get("manualRequired")
        )
        formal_closed = bool(calculation_closed and not formal_transfer_review_required)
        final_reason_codes: list[str] = []
        for row in list(final_closure.get("remainingChecks") or []):
            code = str(row.get("ruleId") or row.get("code") or row.get("category") or "UNRESOLVED_CHECK")
            if code not in final_reason_codes:
                final_reason_codes.append(code)
        for row in list(final_closure.get("remainingReviewItems") or []):
            code = str(row.get("code") or row.get("ruleId") or "MANUAL_REVIEW_REQUIRED")
            if code not in final_reason_codes:
                final_reason_codes.append(code)
        if formal_transfer_review_required and "DESIGN_CONTROL_TRANSFER_PATH_REVIEW" not in final_reason_codes:
            final_reason_codes.append("DESIGN_CONTROL_TRANSFER_PATH_REVIEW")
        outcome_status = (
            "closed"
            if formal_closed
            else "calculated_pending_transfer_review"
            if calculation_closed
            else "cannot_close"
        )
        search["status"] = outcome_status
        search["transferPathRecovery"] = {
            "formalReviewRequired": formal_transfer_review_required,
            "initialRepair": stage_repair,
            "finalRepair": final_stage_repair,
            "calculationCaseSource": stage_selection.get("source"),
        }
        search["closureOutcome"] = {
            "status": outcome_status,
            "closed": formal_closed,
            "calculationClosed": calculation_closed,
            "formalTransferReviewRequired": formal_transfer_review_required,
            "reasonCodes": final_reason_codes,
            "manualItems": final_stage_repair.get("manualItems") or stage_repair.get("manualItems") or [],
            "message": (
                "已获得满足当前计算闸门和正式施工阶段合同的闭合方案。"
                if formal_closed
                else "结构筛查计算已闭合，但冻结或用户自定义的换撑传力路径仍需确认，正式交付保持阻断。"
                if calculation_closed
                else "在当前自动优化边界内无法计算闭合；已保留最优改进并列出剩余控制项。"
            ),
        }
        search["finalClosure"] = {
            key: final_closure.get(key)
            for key in (
                "status", "strategy", "executedIterations", "calculationClosed", "structuralClosed",
                "hardFailCount", "structuralFailCount", "quantitativeOpenCount", "reviewCount",
                "reserveShortfallCount", "remainingChecks", "remainingReviewItems", "interventionOptions",
            )
        }
        result.design_iteration_summary = dict(result.design_iteration_summary or {})
        result.design_iteration_summary["optimizationSearch"] = search
        result.report_diagram_data = dict(result.report_diagram_data or {})
        result.report_diagram_data["optimizationSearch"] = search
        project.advanced_engineering = dict(project.advanced_engineering or {})
        project.advanced_engineering["calculationOptimizationSearch"] = search
        repo.save(
            project,
            action="task.calculation.optimize_search",
            summary=f"Evaluated {search.get('evaluatedCandidateCount', 0)} optimization candidates and adopted {search.get('selectedProfile')}",
        )
        self._stage(
            task,
            96,
            "一键闭合完成" if calculation_closed else "自动优化达到边界，当前方案无法计算闭合",
        )
        return {
            "projectId": project.id,
            "calculationResultId": result.id,
            "status": (
                "calculated"
                if formal_closed
                else "needs_manual_input"
                if calculation_closed
                else "cannot_close"
            ),
            "message": (
                "一键计算、优化和复算已完成，当前方案实现正式计算闭合。"
                if formal_closed
                else "结构筛查已计算闭合；请确认冻结或用户自定义的换撑传力路径后解除正式交付阻断。"
                if calculation_closed
                else "自动搜索已完成，但在当前允许的体系与截面调整边界内无法计算闭合。请按剩余控制项进行专项设计。"
            ),
            "closureOutcome": search.get("closureOutcome"),
            "optimizationSearch": search,
            "closureSummary": final_closure,
            "rebar": rebar_summary,
            "refreshProject": True,
        }

    def _run_calculation_full(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.calculation.engine import run_calculation, run_candidate_comparison_for_project
        from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility
        from app.quality.formal_gate import build_formal_report_gate
        from app.services.construction_stages import select_calculation_case_for_run
        from app.services.calculation_state import mark_calculation_state_current
        from app.services.wall_length_optimizer import mark_wall_length_recalculated
        from app.services.intelligent_design_closure import apply_intervention_action, run_intelligent_design_closure
        repo = self._repo()
        project = repo.require(task.project_id)
        self._assert_calculation_qualified(repo, project, task)
        project = repo.require(task.project_id)
        from app.services.workflow_v381 import repair_design_control_support_references
        stage_reference_repair = repair_design_control_support_references(project)
        if stage_reference_repair.get("changed"):
            repo.save(
                project,
                action="task.calculation_full.repair_design_control_supports",
                summary=f"Remapped stale support references in {stage_reference_repair.get('automaticStageCount', 0)} regular stages",
            )
        if stage_reference_repair.get("manualRequired"):
            raise ValueError("换撑/拆撑设计控制工况包含已失效支撑引用，请先确认专项传力路径。")
        requested_top_n = max(0, min(3, int(payload.get("topN") if payload.get("topN") is not None else payload.get("top_n") or 0)))
        resource_estimate = self._resource_preflight(task, project, candidate_count=requested_top_n)
        if resource_estimate.get("safeModeRequired") and requested_top_n > 0:
            self._append_log(task, "资源预估要求安全模式：当前任务只计算已采用方案，A/B/C请逐个提交。")
            requested_top_n = 0
        self._stage(task, 12, "校验并冻结施工工况")
        case, stage_selection = select_calculation_case_for_run(project)
        if stage_selection.get("source") == "auto_default":
            project.calculation_cases = [case]
            self._append_log(task, f"已按当前支撑标高生成 {len(case.stages)} 个推荐施工阶段。")
        else:
            self._append_log(task, f"已保留用户锁定的施工阶段：{len(case.stages)} 个阶段，未被默认工况覆盖。")
        self._check_cancel(task)

        self._stage(task, 48, "运行结构、围檩、支撑与稳定计算")
        closure: dict[str, Any] | None = None
        force_closure = bool(payload.get("forceClosure"))
        if project.design_settings.auto_intelligent_design_closure_enabled or force_closure:
            result, closure = run_intelligent_design_closure(
                project,
                case,
                auto_repair=not bool(stage_selection.get("preserved")),
                strategy=str(payload.get("closureStrategy") or project.design_settings.intelligent_closure_strategy),
                max_iterations=payload.get("closureMaxIterations"),
            )
            self._append_log(
                task,
                f"智能设计闭环执行 {closure.get('executedIterations', 0)} 轮："
                f"结构闭合={closure.get('structuralClosed')}，剩余定量项={closure.get('quantitativeOpenCount', 0)}。",
            )
            # When the bounded member-sizing loop still leaves a safe,
            # monotonic intervention (for example an unlocked wall-toe
            # deepening or support-section optimization), apply a small number
            # of those actions and run one more verification cycle. This closes
            # the common "calculation finished with FAIL but no repair" gap
            # while preserving manual control over survey coordinates, soil
            # parameters, groundwater assumptions and locked construction
            # stages.
            auto_interventions = bool(payload.get("autoInterventions", force_closure))
            if auto_interventions and not bool(closure.get("calculationClosed")):
                maximum_actions = max(1, min(int(payload.get("maxAutomaticInterventions") or 3), 6))
                applied_interventions: list[dict[str, Any]] = []
                used_action_ids: set[str] = set()
                for option in list(closure.get("interventionOptions") or []):
                    if len(applied_interventions) >= maximum_actions:
                        break
                    action_id = str(option.get("actionId") or "")
                    if not action_id or action_id in used_action_ids:
                        continue
                    if option.get("automaticAllowed") is False:
                        continue
                    if action_id.startswith(("manual:", "run-strategy:")):
                        continue
                    try:
                        applied = apply_intervention_action(project, action_id)
                    except ValueError as exc:
                        self._append_log(task, f"自动干预 {action_id} 未应用：{exc}")
                        continue
                    used_action_ids.add(action_id)
                    applied_interventions.append({
                        "actionId": action_id,
                        "label": option.get("label"),
                        "reason": option.get("reason"),
                        "result": applied,
                    })
                if applied_interventions:
                    pre_intervention_closure = {
                        key: closure.get(key)
                        for key in (
                            "status", "strategy", "executedIterations", "calculationClosed",
                            "structuralClosed", "hardFailCount", "quantitativeOpenCount", "reviewCount",
                        )
                    }
                    self._stage(task, 62, f"应用 {len(applied_interventions)} 项安全加固措施并再次验算")
                    result, followup_closure = run_intelligent_design_closure(
                        project,
                        case,
                        auto_repair=not bool(stage_selection.get("preserved")),
                        strategy=str(payload.get("closureStrategy") or project.design_settings.intelligent_closure_strategy),
                        max_iterations=payload.get("closureMaxIterations"),
                    )
                    followup_closure = dict(followup_closure)
                    followup_closure["preInterventionClosure"] = pre_intervention_closure
                    followup_closure["automaticInterventionsApplied"] = applied_interventions
                    followup_closure["explicitAutomaticInterventionCount"] = len(applied_interventions)
                    followup_closure["automaticInterventionCount"] = int(followup_closure.get("automaticInterventionCount") or 0) + len(applied_interventions)
                    closure = followup_closure
                    result.design_iteration_summary = dict(result.design_iteration_summary or {})
                    result.design_iteration_summary["intelligentDesignClosure"] = closure
                    result.report_diagram_data = dict(result.report_diagram_data or {})
                    result.report_diagram_data["intelligentDesignClosure"] = closure
                    self._append_log(
                        task,
                        f"二阶段闭环完成：自动干预 {len(applied_interventions)} 项，"
                        f"结构闭合={closure.get('structuralClosed')}，硬失败={closure.get('hardFailCount', 0)}。",
                    )
        else:
            result = run_calculation(
                project,
                case,
                auto_repair=not bool(stage_selection.get("preserved")),
            )
        result.design_iteration_summary = dict(result.design_iteration_summary or {})
        result.design_iteration_summary["constructionStageSelection"] = stage_selection
        project.calculation_results.append(result)
        mark_calculation_state_current(project, result.id)
        mark_wall_length_recalculated(project, result.id)
        self._check_cancel(task)

        comparison: list[dict[str, Any]] = []
        top_n = requested_top_n
        if top_n > 0 and project.retaining_system and project.retaining_system.support_layout_repair and project.retaining_system.support_layout_repair.candidates:
            self._stage(task, 76, f"执行前 {top_n} 个候选方案完整比选")
            comparison = run_candidate_comparison_for_project(project, top_n=top_n)
            latest = project.calculation_results[-1]
            latest.report_diagram_data = dict(latest.report_diagram_data or {})
            latest.report_diagram_data["candidateFullCalculationComparison"] = comparison
            if latest.support_layout_repair:
                latest.support_layout_repair.candidate_full_calculations = comparison
            latest.formal_report_gate = build_formal_report_gate(
                project,
                latest.support_layout_quality,
                evaluate_ifc_model_compatibility(project),
                latest_result=latest,
            )
        elif top_n == 0:
            self._append_log(task, "核心模式仅计算当前采用方案，未启动 A/B/C 完整比选。")
        else:
            self._append_log(task, "当前没有可用于完整比选的候选方案。")
        # Persist one immutable project revision per completed calculation task.
        # Intermediate construction-case and result saves used to duplicate a
        # multi-megabyte project blob two or three times and amplify SQLite/WAL
        # memory pressure while the worker was still holding the solver arrays.
        repo.save(project)
        self._stage(task, 92, "刷新项目成果和审查状态")
        return {
            "projectId": project.id,
            "calculationResultId": project.calculation_results[-1].id,
            "candidateComparisonCount": len(comparison),
            "closureSummary": closure,
            "refreshProject": True,
        }

    def _run_candidate_comparison(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.calculation.engine import run_candidate_comparison_for_project
        from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility
        from app.quality.formal_gate import build_formal_report_gate
        repo = self._repo()
        project = repo.require(task.project_id)
        self._assert_calculation_qualified(repo, project, task)
        top_n = int(payload.get("topN") or payload.get("top_n") or 3)
        estimate = self._resource_preflight(task, project, candidate_count=top_n)
        if estimate.get("safeModeRequired"):
            top_n = 1
            self._append_log(task, "资源安全模式已将批量候选完整计算限制为逐个执行。")
        self._stage(task, 25, "读取候选方案")
        comparison = run_candidate_comparison_for_project(project, top_n=top_n)
        self._stage(task, 72, "写入 A/B/C 比选结果")
        if project.calculation_results:
            latest = project.calculation_results[-1]
            latest.report_diagram_data = dict(latest.report_diagram_data or {})
            latest.report_diagram_data["candidateFullCalculationComparison"] = comparison
            if latest.support_layout_repair:
                latest.support_layout_repair.candidate_full_calculations = comparison
            latest.formal_report_gate = build_formal_report_gate(
                project,
                latest.support_layout_quality,
                evaluate_ifc_model_compatibility(project),
                latest_result=latest,
            )
        repo.save(project)
        return {"projectId": project.id, "candidateComparisonCount": len(comparison), "refreshProject": True}

    def _run_candidate_scheme_calculation(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.calculation.engine import run_single_candidate_calculation
        repo = self._repo()
        project = repo.require(task.project_id)
        self._assert_calculation_qualified(repo, project, task)
        self._resource_preflight(task, project, candidate_count=1)
        candidate_id = str(payload.get("candidateId") or "")
        candidate_index = int(payload.get("candidateIndex") or 0)
        use_cache = bool(payload.get("useCache", True))
        repair = project.retaining_system.support_layout_repair if project.retaining_system else None
        candidate = next((item for item in (repair.candidates if repair else []) if item.id == candidate_id), None)
        if candidate is None:
            raise ValueError(f"Candidate not found: {candidate_id}")
        self._stage(task, 16, f"读取方案 {candidate_index + 1} 几何与计算输入")
        project_snapshot = project.model_copy(deep=False)
        project_snapshot.calculation_results = []
        project_snapshot.calculation_cases = []
        self._stage(task, 34, "检查候选计算缓存")
        result = run_single_candidate_calculation(
            project_snapshot, candidate.model_copy(deep=True), index=candidate_index, use_cache=use_cache
        )
        self._stage(task, 84, "写回候选计算结果")
        with self._lock:
            project_lock = self._project_locks.setdefault(task.project_id, RLock())
        with project_lock:
            current = repo.require(task.project_id)
            current_repair = current.retaining_system.support_layout_repair if current.retaining_system else None
            if current_repair:
                for item in current_repair.candidates:
                    if item.id == candidate_id:
                        item.full_calculation = result
                        break
                rows = [dict(row) for row in (current_repair.candidate_full_calculations or []) if str(row.get("candidateId")) != candidate_id]
                rows.append(result)
                rows.sort(key=lambda row: str(row.get("schemeLabel") or "Z"))
                from app.calculation.engine import _rank_full_candidate_calculations
                _rank_full_candidate_calculations(rows)
                current_repair.candidate_full_calculations = rows
                current.retaining_system.layout_summary = dict(current.retaining_system.layout_summary or {})
                current.retaining_system.layout_summary["candidateFullCalculationComparison"] = rows
                if current.calculation_results:
                    latest = current.calculation_results[-1]
                    latest.report_diagram_data = dict(latest.report_diagram_data or {})
                    latest.report_diagram_data["candidateFullCalculationComparison"] = rows
                    if latest.support_layout_repair:
                        latest.support_layout_repair.candidate_full_calculations = rows
                repo.save(current)
        self._stage(task, 96, "刷新方案排名和推荐状态")
        return {
            "projectId": task.project_id,
            "candidateId": candidate_id,
            "candidateIndex": candidate_index,
            "cacheHit": bool(result.get("cacheHit")),
            "inputHash": result.get("inputHash"),
            "status": result.get("status"),
            "schemeLabel": result.get("schemeLabel"),
            "governingValues": dict(result.get("governingValues") or {}),
            "checkSummary": dict(result.get("checkSummary") or {}),
            "refreshProject": True,
        }

    def _run_ifc_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.ifc.exporter import export_simplified_ifc
        from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility, validate_ifc_file
        mode = payload.get("mode")
        if not mode:
            mode = {
                "export_ifc_light": "coordination_light",
                "export_ifc_analysis": "analysis_model",
                "export_ifc_construction_visual": "construction_visual",
                "export_ifc_detailed": "design_detailed",
                "export_ifc": "design_detailed",
            }.get(task.operation, "design_detailed")
        repo = self._repo()
        project = repo.require(task.project_id)
        self._stage(task, 18, f"执行 IFC 预检查：{mode}")
        precheck = evaluate_ifc_model_compatibility(project)
        self._stage(task, 58, "生成 IFC 文件")
        path = export_simplified_ifc(project, EXPORT_DIR, export_mode=str(mode))
        self._stage(task, 82, "执行 IFC 文件级兼容性检查")
        file_check = validate_ifc_file(path, base=precheck)
        sidecar = path.with_suffix(".ifc_check.json")
        import json
        sidecar.write_text(json.dumps(file_check.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/octet-stream", {"ifcCheckPath": str(sidecar), "ifcStatus": file_check.status, "ifcScore": file_check.score})

    def _run_report_export(self, task: TaskRecord) -> dict[str, Any]:
        from app.reports.docx_report import export_docx_report
        project = self._repo().require(task.project_id)
        self._stage(task, 22, "汇总计算书章节和图表")
        path = export_docx_report(project, EXPORT_DIR)
        return self._file_result(path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def _run_cad_export(self, task: TaskRecord, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from app.drawings.cad_export import export_construction_cad_package
        from app.drawing_rules import evaluate_drawing_issue_gate
        from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
        from app.services.review_workflow import review_status
        payload = payload or {}
        project = self._repo().require(task.project_id)
        scope = str(payload.get("scope") or "full")
        rebar_mode = str(payload.get("rebarMode") or payload.get("rebar_mode") or "balanced")
        requested_issue_mode = str(payload.get("issueMode") or payload.get("issue_mode") or "auto")
        scheme = build_rebar_design_scheme(project, mode=rebar_mode)
        can_issue = bool((scheme.get("diagnostics") or {}).get("canIssueConstructionDrawings"))
        approval = review_status(project)
        current_revision = next((r for r in reversed(project.drawing_revisions) if r.issue_status == "construction" and r.snapshot_hash == approval.get("currentSnapshotHash")), None)
        construction_gate = evaluate_drawing_issue_gate(project, issue_mode="construction", engineering_gate_allowed=can_issue, approval=approval, current_revision_valid=current_revision is not None)
        if requested_issue_mode == "auto":
            issue_mode = "construction" if construction_gate["allowed"] else "review"
        elif requested_issue_mode in {"review", "construction"}:
            issue_mode = requested_issue_mode
        else:
            raise ValueError(f"Unsupported CAD issue mode: {requested_issue_mode}")
        selected_gate = construction_gate if issue_mode == "construction" else evaluate_drawing_issue_gate(project, issue_mode="review", engineering_gate_allowed=can_issue, approval=approval, current_revision_valid=current_revision is not None)
        if not selected_gate["allowed"]:
            raise ValueError("当前出图规则集的施工版发行条件未满足：" + "; ".join(str(x.get("message")) for x in selected_gate.get("reasons", [])))
        mode_text = "施工图复核版" if issue_mode == "construction" else "审查版"
        self._stage(task, 22, f"生成 {scope} DXF 图纸、分区配筋和材料表（{mode_text}）")
        path = export_construction_cad_package(project, EXPORT_DIR, scope=scope, rebar_mode=rebar_mode, issue_mode=issue_mode)
        return self._file_result(path, "application/zip", {"scope": scope, "rebarMode": rebar_mode, "issueMode": issue_mode, "canIssueConstructionDrawings": can_issue, "drawingIssueGate": selected_gate})

    def _run_svg_export(self, task: TaskRecord) -> dict[str, Any]:
        from app.drawings.cad_export import export_construction_svg_package
        project = self._repo().require(task.project_id)
        self._stage(task, 22, "生成 SVG 图纸包")
        path = export_construction_svg_package(project, EXPORT_DIR)
        return self._file_result(path, "application/zip")

    def _run_json_export(self, task: TaskRecord) -> dict[str, Any]:
        project = self._repo().require(task.project_id)
        self._stage(task, 25, "写出项目 JSON")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"{project.id}.json"
        import json
        path.write_text(json.dumps(project.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")


    def _run_trace_export(self, task: TaskRecord) -> dict[str, Any]:
        from app.services.calculation_trace import build_calculation_trace
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成计算追溯链 JSON")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"{project.id}_calculation_trace.json"
        import json
        path.write_text(json.dumps(build_calculation_trace(project), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")

    def _run_issue_report_export(self, task: TaskRecord) -> dict[str, Any]:
        from app.services.issue_center import build_issue_center
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成问题清单和完成度评估 JSON")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"{project.id}_issue_center_v{SOFTWARE_VERSION.replace('.', '_')}.json"
        import json
        path.write_text(json.dumps(build_issue_center(project), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")


    def _run_wall_length_redundancy_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.wall_length_optimizer import export_wall_length_redundancy_report
        project = self._repo().require(task.project_id)
        mode = str(payload.get("mode") or "balanced")
        self._stage(task, 28, "生成围护墙设计长度冗余优化报告")
        path = export_wall_length_redundancy_report(project, EXPORT_DIR, mode=mode)
        return self._file_result(path, "application/json")

    def _run_design_scheme_ledger_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.design_scheme_ledger import export_design_scheme_ledger
        project = self._repo().require(task.project_id)
        mode = str(payload.get("mode") or "balanced")
        self._stage(task, 30, "生成方案快照与交付闸门台账")
        path = export_design_scheme_ledger(project, EXPORT_DIR, mode=mode)
        return self._file_result(path, "application/json")



    def _run_formal_drawing_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.drawings.formal_issue import export_formal_drawing_package
        from app.drawing_rules import evaluate_drawing_issue_gate
        from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
        from app.services.review_workflow import review_status
        project = self._repo().require(task.project_id)
        issue_mode = str(payload.get("issueMode") or payload.get("issue_mode") or "review")
        rebar_mode = str(payload.get("rebarMode") or payload.get("rebar_mode") or "balanced")
        scheme = build_rebar_design_scheme(project, mode=rebar_mode)
        approval = review_status(project)
        current_revision = next((r for r in reversed(project.drawing_revisions) if r.issue_status == "construction" and r.snapshot_hash == approval.get("currentSnapshotHash")), None)
        issue_gate = evaluate_drawing_issue_gate(project, issue_mode=issue_mode, engineering_gate_allowed=bool((scheme.get("diagnostics") or {}).get("canIssueConstructionDrawings")), approval=approval, current_revision_valid=current_revision is not None)
        if not issue_gate["allowed"]:
            raise ValueError("正式图纸包发行条件未满足：" + "; ".join(str(x.get("message")) for x in issue_gate.get("reasons", [])))
        self._stage(task, 24, "生成 CAD、批量 PDF、修订台账和工程闭环索引")
        path = export_formal_drawing_package(project, EXPORT_DIR, issue_mode=issue_mode, rebar_mode=rebar_mode)
        return self._file_result(path, "application/zip", {"issueMode": issue_mode, "refreshProject": True, "drawingIssueGate": issue_gate})

    def _run_coordinated_delivery_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.delivery_package import export_coordinated_delivery_package
        project = self._repo().require(task.project_id)
        issue_mode = str(payload.get("issueMode") or payload.get("issue_mode") or "review")
        rebar_mode = str(payload.get("rebarMode") or payload.get("rebar_mode") or "balanced")
        include_ifc = bool(payload.get("includeIfcProfiles", payload.get("include_ifc_profiles", True)))
        self._stage(task, 20, "生成施工图、批量PDF和逐图质量报告")
        path = export_coordinated_delivery_package(
            project, EXPORT_DIR, issue_mode=issue_mode, rebar_mode=rebar_mode, include_ifc_profiles=include_ifc
        )
        return self._file_result(path, "application/zip", {
            "packageType": "coordinated_delivery", "issueMode": issue_mode, "rebarMode": rebar_mode, "refreshProject": True
        })

    def _run_rebar_detailing_export(self, task: TaskRecord) -> dict[str, Any]:
        from app.services.rebar_export import export_rebar_detailing_package
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成钢筋加工深化 ZIP（XLSX/CSV/JSON/使用说明）")
        path = export_rebar_detailing_package(project, EXPORT_DIR, mode="balanced")
        return self._file_result(path, "application/zip", {"packageType": "rebar_detailing", "humanReadablePrimary": "rebar_detailing_schedules.xlsx"})

    def _run_benchmark_export(self, task: TaskRecord) -> dict[str, Any]:
        from app.services.benchmark_cases import export_benchmark_package
        self._stage(task, 20, "运行公开论文典型基坑规范算法回归算例")
        path = export_benchmark_package(EXPORT_DIR, repo=None, persist=False)
        return self._file_result(path, "application/zip")

    def _run_full_delivery(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        self._stage(task, 5, "执行完整计算、候选方案比较和设计闭环")
        calculation = self._run_calculation_full(task, payload)
        self._check_cancel(task)
        self._stage(task, 72, "生成图纸、IFC、计算书、钢筋深化与审计索引")
        package = self._run_coordinated_delivery_export(task, {
            "issueMode": str(payload.get("issueMode") or "review"),
            "rebarMode": str(payload.get("rebarMode") or "balanced"),
            "includeIfcProfiles": bool(payload.get("includeIfcProfiles", True)),
        })
        package["calculation"] = calculation
        package["fullFlow"] = True
        return package

    def _run_industrial_closure(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        from app.calculation.engine import run_candidate_comparison_for_project
        from app.services.industrial_readiness import run_industrial_closure
        repo = self._repo()
        project = repo.require(task.project_id)
        if not project.calculation_results:
            self._append_log(task, "工业闭环缺少当前计算，先执行完整计算与候选比选。")
            self._run_calculation_full(task, {"topN": int(payload.get("topN") or 0)})
            project = repo.require(task.project_id)
        repair = project.retaining_system.support_layout_repair if project.retaining_system else None
        valid_rows = [row for row in (repair.candidate_full_calculations if repair else []) if row.get("status") not in {"failed", "error"}]
        if repair and repair.candidates and len(valid_rows) < min(3, len(repair.candidates)):
            self._stage(task, 78, "补齐 A/B/C 候选方案独立计算")
            comparison = run_candidate_comparison_for_project(project, top_n=min(3, len(repair.candidates)))
            repair.candidate_full_calculations = comparison
        self._stage(task, 88, "执行 P0-P3 工业资格、深化与监测闭环评估")
        readiness = run_industrial_closure(project)
        repo.save(
            project,
            action="task.industrial_closure",
            summary=f"P0-P3 industrial closure task completed: {readiness.get('status')}",
        )
        return {"projectId": project.id, "readiness": readiness, "refreshProject": True}

    def _run_storage_compaction(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        repo = self._repo()
        self._stage(task, 12, "读取项目存储索引，不在 API 进程中反序列化完整工程对象")
        before = repo.store.get_payload_info(task.project_id)
        if before is None:
            raise ValueError(f"Project not found: {task.project_id}")
        self._stage(task, 38, "外部化地质曲面、计算结果、候选完整计算和钢筋重型数据")
        result = repo.store.compact_project_storage(
            task.project_id,
            include_revisions=bool(payload.get("includeRevisions") or payload.get("include_revisions")),
        )
        self._stage(task, 82, "重建受限工作区投影并校验存储体积")
        after = repo.store.get_payload_info(task.project_id) or result.get("after") or {}
        repo.store.append_audit(
            task.project_id,
            action="project.storage_compaction",
            summary="Project heavy payload externalized and workspace projection rebuilt",
            actor="task-worker",
            metadata={
                "beforePayloadBytes": int((before or {}).get("payloadBytes") or 0),
                "afterPayloadBytes": int((after or {}).get("payloadBytes") or 0),
                "beforeWorkspaceBytes": int((before or {}).get("workspaceBytes") or 0),
                "afterWorkspaceBytes": int((after or {}).get("workspaceBytes") or 0),
            },
        )
        return {
            "projectId": task.project_id,
            "before": before,
            "after": after,
            "compaction": result,
            "refreshProject": True,
        }

    def _file_result(self, path: Path, media_type: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        digest = None
        size = path.stat().st_size if path.exists() else 0
        if path.exists():
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        result = {"filePath": str(path), "filename": path.name, "mediaType": media_type, "sizeBytes": size, "sha256": digest}
        if extra:
            result.update(extra)
        return result

    def _title_for(self, operation: str) -> str:
        return {
            "borehole_import": "解析并导入地质钻孔",
            "core_design": "核心设计：方案、计算与配筋",
            "rebar_design": "配筋深化与构造校核",
            "formal_adverse_scenarios": "正式不利工况专项复算",
            "design_scenario_envelope": "设计允许域情景包络复算",
            "p3_detailing_closure": "企业节点与钢筋深化闭环",
            "adopt_support_candidate": "采用支撑候选方案",
            "calculation_full": "一键计算校核",
            "calculation_recovery": "自动诊断、修复并复算",
            "calculation_closure_action": "应用加固措施并重新验算",
            "calculation_optimize_search": "一键多目标优化并复算",
            "calculation_auto_close": "一键计算、优化并闭合",
            "support_layout_optimization": "按平面类型优化水平支撑候选",
            "candidate_comparison": "候选方案 A/B/C 完整比选",
            "candidate_scheme_calculation": "单个候选方案完整计算",
            "export_ifc_light": "导出 IFC 轻量协调版",
            "export_ifc_analysis": "导出 IFC 分析模型版",
            "export_ifc_construction_visual": "导出 IFC 施工图可视化版",
            "export_ifc_detailed": "导出 IFC 语义详细版",
            "export_ifc": "导出 IFC",
            "export_report": "导出 DOCX 计算书",
            "export_drawings_cad": "导出 CAD 图纸包",
            "export_drawings_svg": "导出 SVG 图纸包",
            "export_formal_drawings": "导出正式图纸发行包",
            "export_coordinated_delivery": "导出协同成果交付包",
            "export_json": "导出 JSON 数据",
            "export_trace": "导出计算追溯链",
            "export_issue_report": "导出问题清单与完成度评估",
            "export_rebar_detailing": "导出钢筋加工深化 ZIP",
            "export_wall_length_redundancy": "导出围护墙设计长度冗余优化报告",
            "export_design_scheme_ledger": "导出方案快照与交付闸门台账",
            "export_benchmark_cases": "导出公开论文典型基坑回归算例包",
            "full_delivery": "全流程计算与成果生成",
            "industrial_closure": "P0-P3 工业闭环计算与资格评估",
            "storage_compaction": "压缩项目存储与重建工作区",
        }.get(operation, operation)

    def _stage(self, task: TaskRecord, progress: int, step: str) -> None:
        self._check_cancel(task)
        safe_progress = max(int(task.progress or 0), max(0, min(99, int(progress))))
        self._set(task, progress=safe_progress, current_step=step)
        self._append_log(task, step)
        memory_event(
            "task-lifecycle",
            "stage",
            taskId=task.id,
            projectId=task.project_id,
            operation=task.operation,
            progress=safe_progress,
            stage=step,
        )

    def _check_cancel(self, task: TaskRecord) -> None:
        if self._execution_mode in {"external", "worker"}:
            raw = self._store.get(task.id)
            if raw is not None and bool(raw.get("cancelRequested")):
                task.cancel_requested = True
        if task.cancel_requested:
            raise RuntimeError("Task cancellation requested")

    def _persist(self, task: TaskRecord) -> None:
        self._store.upsert(task.as_dict(include_logs=True))

    def _set(self, task: TaskRecord, **patch: Any) -> None:
        with self._lock:
            for key, value in patch.items():
                setattr(task, key, value)
            task.updated_at = _now()
            if task.status == "running":
                task.heartbeat_at = task.updated_at
            self._persist(task)

    def _append_log(self, task: TaskRecord, message: str) -> None:
        with self._lock:
            task.logs.append(f"[{_now()}] {message}")
            if len(task.logs) > 500:
                task.logs = task.logs[-500:]
            task.updated_at = _now()
            if task.status == "running":
                task.heartbeat_at = task.updated_at
            self._persist(task)


task_manager = TaskManager()
