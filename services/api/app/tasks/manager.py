from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4
import traceback
import hashlib
import shutil

from app.calculation.engine import build_default_construction_cases, run_calculation, run_candidate_comparison_for_project, run_single_candidate_calculation
from app.drawings.cad_export import export_construction_cad_package, export_construction_svg_package
from app.drawings.formal_issue import export_formal_drawing_package
from app.drawing_rules import evaluate_drawing_issue_gate
from app.ifc.exporter import export_simplified_ifc
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility, validate_ifc_file
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

EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"

TaskStatus = str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self._executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="pitguard-task")
        for raw in self._store.list(limit=500):
            task = TaskRecord.from_dict(raw)
            if task.status in {"queued", "running"}:
                task.status = "interrupted"
                task.current_step = "服务重启导致任务中断，可重新提交"
                task.error = task.error or "Task interrupted by service restart"
                task.finished_at = _now()
                task.updated_at = task.finished_at
                task.logs.append(f"[{_now()}] 服务启动时检测到未完成任务，已标记为 interrupted。")
                self._store.upsert(task.as_dict(include_logs=True))
            self._tasks[task.id] = task

    def submit(self, project_id: str, operation: str, payload: dict[str, Any] | None = None) -> TaskRecord:
        payload = payload or {}
        task = TaskRecord(
            id=f"task-{uuid4().hex[:12]}",
            project_id=project_id,
            operation=operation,
            title=self._title_for(operation),
        )
        with self._lock:
            self._tasks[task.id] = task
            self._append_log(task, f"任务已创建：{task.title}")
            self._persist(task)
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

    def list(self, project_id: str | None = None) -> list[TaskRecord]:
        with self._lock:
            records = list(self._tasks.values())
        if project_id:
            records = [task for task in records if task.project_id == project_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, task_id: str) -> TaskRecord | None:
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
                future = self._futures.pop(task_id, None)
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
            future = self._futures.get(task_id)
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
        try:
            self._set(task, status="running", progress=2, current_step="启动任务")
            if task.operation == "candidate_scheme_calculation":
                self._append_log(task, "候选方案采用只读项目快照并行计算；写回结果时使用短时项目锁。")
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

    def _execute_operation(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        if task.operation == "calculation_full":
            result = self._run_calculation_full(task, payload)
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
        else:
            raise ValueError(f"Unsupported task operation: {task.operation}")
        return result

    def _repo(self) -> ProjectRepository:
        return ProjectRepository()

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
        top_n = int(payload.get("topN") or payload.get("top_n") or 3)
        if project.retaining_system and project.retaining_system.support_layout_repair and project.retaining_system.support_layout_repair.candidates:
            self._stage(task, 76, f"执行前 {top_n} 个候选方案完整比选")
            comparison = run_candidate_comparison_for_project(project, top_n=top_n)
            latest = project.calculation_results[-1]
            latest.report_diagram_data = dict(latest.report_diagram_data or {})
            latest.report_diagram_data["candidateFullCalculationComparison"] = comparison
            if latest.support_layout_repair:
                latest.support_layout_repair.candidate_full_calculations = comparison
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
        }.get(operation, operation)

    def _stage(self, task: TaskRecord, progress: int, step: str) -> None:
        self._check_cancel(task)
        self._set(task, progress=progress, current_step=step)
        self._append_log(task, step)

    def _check_cancel(self, task: TaskRecord) -> None:
        if task.cancel_requested:
            raise RuntimeError("Task cancellation requested")

    def _persist(self, task: TaskRecord) -> None:
        self._store.upsert(task.as_dict(include_logs=True))

    def _set(self, task: TaskRecord, **patch: Any) -> None:
        with self._lock:
            for key, value in patch.items():
                setattr(task, key, value)
            task.updated_at = _now()
            self._persist(task)

    def _append_log(self, task: TaskRecord, message: str) -> None:
        with self._lock:
            task.logs.append(f"[{_now()}] {message}")
            if len(task.logs) > 500:
                task.logs = task.logs[-500:]
            task.updated_at = _now()
            self._persist(task)


task_manager = TaskManager()
