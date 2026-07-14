from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, BoundedSemaphore
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
from contextlib import nullcontext

from app.calculation.engine import build_default_construction_cases, run_calculation, run_candidate_comparison_for_project, run_single_candidate_calculation
from app.drawings.cad_export import export_construction_cad_package, export_construction_svg_package
from app.drawings.formal_issue import export_formal_drawing_package
from app.drawing_rules import evaluate_drawing_issue_gate
from app.ifc.exporter import export_simplified_ifc
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility, validate_ifc_file
from app.quality.formal_gate import build_formal_report_gate
from app.reports.docx_report import export_docx_report
from app.storage.repository import ProjectRepository
from app.storage.task_store import SQLiteTaskStore
from app.version import SOFTWARE_VERSION, version_manifest
from app.services.calculation_trace import build_calculation_trace
from app.services.issue_center import build_issue_center
from app.services.benchmark_cases import export_benchmark_package
from app.services.rebar_detailing import build_rebar_detailing
from app.services.rebar_export import export_rebar_detailing_package
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme
from app.services.wall_length_optimizer import export_wall_length_redundancy_report, mark_wall_length_recalculated
from app.services.calculation_state import mark_calculation_state_current
from app.services.design_scheme_ledger import export_design_scheme_ledger
from app.services.review_workflow import review_status
from app.services.delivery_package import export_coordinated_delivery_package
from app.services.industrial_readiness import run_industrial_closure
from app.services.support_layout_repair import auto_repair_support_layout
from app.services.calculation_state import invalidate_calculation_state
from app.geology.model_builder import ensure_geological_model_covers_excavation

EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"

TaskStatus = str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 32) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _process_memory_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("VmRSS:"):
                    return round(float(line.split()[1]) / 1024.0, 2)
    except OSError:
        pass
    if resource is not None:
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(float(value) / 1024.0, 2)
    return 0.0


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

    def as_dict(self, include_logs: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "projectId": self.project_id,
            "operation": self.operation,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "currentStep": self.current_step,
            "result": self.result,
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
            data["logs"] = list(self.logs)
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
        self._worker_count = _env_int("PITGUARD_TASK_WORKERS", 2, 1, 8)
        self._heavy_concurrency = _env_int("PITGUARD_HEAVY_TASK_CONCURRENCY", 1, 1, 2)
        self._memory_soft_limit_mb = _env_int("PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB", 5600, 1024, 131072)
        self._task_timeout_seconds = _env_int("PITGUARD_TASK_TIMEOUT_SECONDS", 1800, 60, 86400)
        self._heavy_semaphore = BoundedSemaphore(self._heavy_concurrency)
        self._executor = (
            ThreadPoolExecutor(max_workers=self._worker_count, thread_name_prefix="pitguard-task")
            if self._execution_mode == "embedded" else None
        )
        self._heavy_operations = {
            "calculation_full", "candidate_comparison", "candidate_scheme_calculation", "support_layout_optimization",
            "full_delivery", "industrial_closure", "export_rebar_detailing",
            "export_ifc_detailed", "export_ifc_construction_visual", "export_coordinated_delivery",
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


    def _enforce_memory_budget(self, task: TaskRecord, stage: str) -> None:
        """Fail a heavy task cleanly before the operating system OOM-kills the API."""
        rss = _process_memory_mb()
        if rss <= self._memory_soft_limit_mb:
            return
        _release_process_memory()
        rss_after = _process_memory_mb()
        self._append_log(
            task,
            f"{stage}检测到内存压力：RSS {rss_after:.2f} MB，软上限 {self._memory_soft_limit_mb} MB。",
        )
        if rss_after > self._memory_soft_limit_mb:
            raise RuntimeError(
                f"服务器内存不足，已在{stage}前受控终止任务（RSS {rss_after:.0f} MB > "
                f"{self._memory_soft_limit_mb} MB）。请关闭并行候选计算、清理旧结果或提高实例内存。"
            )

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
        project = self._repo().require(project_id)
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
            "latestUpdatedAt": max((task.updated_at for task in records), default=None),
            "completedCount": len(completed),
            "processResidentMemoryMB": _process_memory_mb(),
        }

    def list(self, project_id: str | None = None) -> list[TaskRecord]:
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
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.cancel_requested = True
            self._append_log(task, "已请求取消。当前原型会在阶段边界检查取消状态。")
            future = self._futures.get(task_id) if self._executor is not None else None
            if future and future.cancel():
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
        heavy_guard = self._heavy_semaphore if task.operation in self._heavy_operations else nullcontext()
        memory_before = _process_memory_mb()
        try:
            self._set(task, status="running", progress=2, current_step="启动任务")
            self._append_log(task, f"任务内存基线 {memory_before:.2f} MB；重任务并发上限 {self._heavy_concurrency}。")
            with heavy_guard:
                if task.operation in self._heavy_operations:
                    self._append_log(task, "已进入重计算内存闸门，避免多个完整计算/导出同时占用内存。")
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
            self._set(task, status=status, error=str(exc), current_step="任务已取消" if status == "cancelled" else "任务失败", finished_at=_now())
            self._append_log(task, traceback.format_exc(limit=8))
        finally:
            _release_process_memory()
            memory_after = _process_memory_mb()
            self._append_log(task, f"任务结束内存 {memory_after:.2f} MB；已执行 Python GC 与 malloc_trim。")

    def recover_external_worker(self) -> int:
        if self._execution_mode != "worker":
            return 0
        return self._store.mark_running_interrupted("External calculation worker restarted before task completion")

    def run_worker_forever(self, poll_seconds: float = 1.0) -> None:
        if self._execution_mode != "worker":
            raise RuntimeError("PITGUARD_TASK_EXECUTION_MODE must be 'worker' for the worker daemon")
        self.recover_external_worker()
        while True:
            raw = self._store.claim_next()
            if raw is None:
                time.sleep(max(0.2, float(poll_seconds)))
                continue
            task = TaskRecord.from_dict(raw)
            with self._lock:
                self._tasks[task.id] = task
            timed_out = {"value": False}

            def terminate_worker() -> None:
                timed_out["value"] = True
                raw_task = self._store.get(task.id) or task.as_dict(include_logs=True)
                raw_task["status"] = "interrupted"
                raw_task["error"] = f"Task exceeded hard timeout of {self._task_timeout_seconds} seconds"
                raw_task["currentStep"] = "任务超时，计算工作进程将重启"
                raw_task["updatedAt"] = _now()
                raw_task["finishedAt"] = raw_task["updatedAt"]
                logs = list(raw_task.get("logs") or [])
                logs.append(f"[{_now()}] 超过硬超时 {self._task_timeout_seconds}s，终止独立工作进程以保护API服务。")
                raw_task["logs"] = logs[-500:]
                self._store.upsert(raw_task)
                os._exit(124)

            timer = Timer(self._task_timeout_seconds, terminate_worker)
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
                return

    def _execute_operation(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        if task.operation == "calculation_full":
            result = self._run_calculation_full(task, payload)
        elif task.operation == "support_layout_optimization":
            result = self._run_support_layout_optimization(task, payload)
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
        else:
            raise ValueError(f"Unsupported task operation: {task.operation}")
        return result

    def _repo(self) -> ProjectRepository:
        return ProjectRepository()

    def _run_support_layout_optimization(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        repo = self._repo()
        project = repo.require(task.project_id)
        if project.excavation is None:
            raise ValueError("Project has no excavation")
        self._stage(task, 10, "检查地质模型覆盖与平面类型")
        ensure_geological_model_covers_excavation(project)
        self._stage(task, 28, "按平面类型生成受力可闭合的支撑候选")
        result = auto_repair_support_layout(
            project,
            objective_weights=dict(payload.get("objectiveWeights") or payload.get("objective_weights") or {}),
            preset=str(payload.get("preset") or "balanced"),
        )
        self._stage(task, 82, "执行零非法交叉、墙—墙传力与围檩跨审查")
        invalidate_calculation_state(
            project,
            reason="support optimization candidate set regenerated by isolated worker",
            rebuild_cases=True,
        )
        repo.save(project)
        return {
            "projectId": project.id,
            "candidateCount": len(result.candidates or []),
            "status": result.status,
            "selectedCandidateId": result.selected_candidate_id,
            "refreshProject": True,
        }

    def _run_calculation_full(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        repo = self._repo()
        project = repo.require(task.project_id)
        self._stage(task, 12, "生成施工工况")
        project.calculation_cases = build_default_construction_cases(project)
        repo.save(project)
        self._check_cancel(task)

        self._stage(task, 48, "运行结构、围檩、支撑与稳定计算")
        case = project.calculation_cases[-1] if project.calculation_cases else None
        result = run_calculation(project, case)
        project.calculation_results.append(result)
        mark_calculation_state_current(project, result.id)
        mark_wall_length_recalculated(project, result.id)
        repo.save(project)
        self._check_cancel(task)

        comparison: list[dict[str, Any]] = []
        top_n = max(0, min(3, int(payload.get("topN") if payload.get("topN") is not None else payload.get("top_n") or 0)))
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
            repo.save(project)
        else:
            self._append_log(task, "未发现候选方案，跳过 A/B/C 完整比选。")
        self._stage(task, 92, "刷新项目成果和审查状态")
        return {"projectId": project.id, "calculationResultId": project.calculation_results[-1].id, "candidateComparisonCount": len(comparison), "refreshProject": True}

    def _run_candidate_comparison(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        repo = self._repo()
        project = repo.require(task.project_id)
        top_n = int(payload.get("topN") or payload.get("top_n") or 3)
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
        repo = self._repo()
        project = repo.require(task.project_id)
        candidate_id = str(payload.get("candidateId") or "")
        candidate_index = int(payload.get("candidateIndex") or 0)
        use_cache = bool(payload.get("useCache", True))
        repair = project.retaining_system.support_layout_repair if project.retaining_system else None
        candidate = next((item for item in (repair.candidates if repair else []) if item.id == candidate_id), None)
        if candidate is None:
            raise ValueError(f"Candidate not found: {candidate_id}")
        self._stage(task, 16, f"读取方案 {candidate_index + 1} 几何与计算输入")
        project_snapshot = project.model_copy(deep=True)
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
            "projectId": task.project_id, "candidateId": candidate_id, "candidateIndex": candidate_index,
            "cacheHit": bool(result.get("cacheHit")), "inputHash": result.get("inputHash"),
            "result": result, "refreshProject": True,
        }

    def _run_ifc_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
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
        project = self._repo().require(task.project_id)
        self._stage(task, 22, "汇总计算书章节和图表")
        path = export_docx_report(project, EXPORT_DIR)
        return self._file_result(path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def _run_cad_export(self, task: TaskRecord, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成计算追溯链 JSON")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"{project.id}_calculation_trace.json"
        import json
        path.write_text(json.dumps(build_calculation_trace(project), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")

    def _run_issue_report_export(self, task: TaskRecord) -> dict[str, Any]:
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成问题清单和完成度评估 JSON")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"{project.id}_issue_center_v{SOFTWARE_VERSION.replace('.', '_')}.json"
        import json
        path.write_text(json.dumps(build_issue_center(project), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")


    def _run_wall_length_redundancy_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        project = self._repo().require(task.project_id)
        mode = str(payload.get("mode") or "balanced")
        self._stage(task, 28, "生成围护墙设计长度冗余优化报告")
        path = export_wall_length_redundancy_report(project, EXPORT_DIR, mode=mode)
        return self._file_result(path, "application/json")

    def _run_design_scheme_ledger_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        project = self._repo().require(task.project_id)
        mode = str(payload.get("mode") or "balanced")
        self._stage(task, 30, "生成方案快照与交付闸门台账")
        path = export_design_scheme_ledger(project, EXPORT_DIR, mode=mode)
        return self._file_result(path, "application/json")



    def _run_formal_drawing_export(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
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
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成钢筋加工深化 ZIP（XLSX/CSV/JSON/使用说明）")
        path = export_rebar_detailing_package(project, EXPORT_DIR, mode="balanced")
        return self._file_result(path, "application/zip", {"packageType": "rebar_detailing", "humanReadablePrimary": "rebar_detailing_schedules.xlsx"})

    def _run_benchmark_export(self, task: TaskRecord) -> dict[str, Any]:
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
            "calculation_full": "一键计算校核",
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
        }.get(operation, operation)

    def _stage(self, task: TaskRecord, progress: int, step: str) -> None:
        self._check_cancel(task)
        self._set(task, progress=progress, current_step=step)
        self._append_log(task, step)

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
