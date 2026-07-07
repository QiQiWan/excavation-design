from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4
import traceback

from app.calculation.engine import build_default_construction_cases, run_calculation, run_candidate_comparison_for_project
from app.drawings.cad_export import export_construction_cad_package, export_construction_svg_package
from app.ifc.exporter import export_simplified_ifc
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility, validate_ifc_file
from app.reports.docx_report import export_docx_report
from app.storage.repository import ProjectRepository
from app.services.calculation_trace import build_calculation_trace
from app.services.issue_center import build_issue_center
from app.services.benchmark_cases import export_benchmark_package
from app.services.rebar_detailing import build_rebar_detailing

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
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pitguard-task")

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
            future = self._executor.submit(self._run_task, task.id, payload)
            self._futures[task.id] = future
        return task

    def list(self, project_id: str | None = None) -> list[TaskRecord]:
        with self._lock:
            records = list(self._tasks.values())
        if project_id:
            records = [task for task in records if task.project_id == project_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

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
            return task

    def _run_task(self, task_id: str, payload: dict[str, Any]) -> None:
        task = self.get(task_id)
        if not task:
            return
        try:
            self._set(task, status="running", progress=2, current_step="启动任务")
            if task.operation == "calculation_full":
                result = self._run_calculation_full(task, payload)
            elif task.operation == "candidate_comparison":
                result = self._run_candidate_comparison(task, payload)
            elif task.operation.startswith("export_ifc"):
                result = self._run_ifc_export(task, payload)
            elif task.operation == "export_report":
                result = self._run_report_export(task)
            elif task.operation == "export_drawings_cad":
                result = self._run_cad_export(task)
            elif task.operation == "export_drawings_svg":
                result = self._run_svg_export(task)
            elif task.operation == "export_json":
                result = self._run_json_export(task)
            elif task.operation == "export_trace":
                result = self._run_trace_export(task)
            elif task.operation == "export_issue_report":
                result = self._run_issue_report_export(task)
            elif task.operation == "export_rebar_detailing":
                result = self._run_rebar_detailing_export(task)
            elif task.operation == "export_benchmark_cases":
                result = self._run_benchmark_export(task)
            elif task.operation == "full_delivery":
                result = self._run_full_delivery(task, payload)
            else:
                raise ValueError(f"Unsupported task operation: {task.operation}")
            if task.cancel_requested:
                self._set(task, status="cancelled", progress=task.progress, current_step="任务已取消", finished_at=_now())
                return
            self._set(task, status="success", progress=100, current_step="任务完成", result=result, finished_at=_now())
            self._append_log(task, "任务完成。")
        except Exception as exc:  # pragma: no cover - defensive task boundary
            self._set(task, status="failed", error=str(exc), current_step="任务失败", finished_at=_now())
            self._append_log(task, traceback.format_exc(limit=8))

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

    def _run_cad_export(self, task: TaskRecord) -> dict[str, Any]:
        project = self._repo().require(task.project_id)
        self._stage(task, 22, "生成 DXF 图纸和钢筋表")
        path = export_construction_cad_package(project, EXPORT_DIR)
        return self._file_result(path, "application/zip")

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
        path = EXPORT_DIR / f"{project.id}_issue_center_v2_5_0.json"
        import json
        path.write_text(json.dumps(build_issue_center(project), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")

    def _run_rebar_detailing_export(self, task: TaskRecord) -> dict[str, Any]:
        project = self._repo().require(task.project_id)
        self._stage(task, 28, "生成钢筋施工详图深化 JSON")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"{project.id}_rebar_shop_detailing_v2_5_0.json"
        import json
        path.write_text(json.dumps(build_rebar_detailing(project), ensure_ascii=False, indent=2), encoding="utf-8")
        return self._file_result(path, "application/json")

    def _run_benchmark_export(self, task: TaskRecord) -> dict[str, Any]:
        self._stage(task, 20, "运行公开论文典型基坑规范算法回归算例")
        path = export_benchmark_package(EXPORT_DIR, repo=None, persist=False)
        return self._file_result(path, "application/zip")

    def _run_full_delivery(self, task: TaskRecord, payload: dict[str, Any]) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        self._stage(task, 6, "执行完整计算")
        outputs["calculation"] = self._run_calculation_full(task, payload)
        self._stage(task, 32, "生成施工图可视化 IFC")
        outputs["ifcConstructionVisual"] = self._run_ifc_export(task, {"mode": "construction_visual"})
        self._stage(task, 46, "生成正式 CAD 图纸集")
        outputs["cad"] = self._run_cad_export(task)
        self._stage(task, 58, "生成 SVG 图纸包")
        outputs["svg"] = self._run_svg_export(task)
        self._stage(task, 70, "生成 DOCX 计算书")
        outputs["report"] = self._run_report_export(task)
        self._stage(task, 80, "生成完整 JSON、追溯链和问题清单")
        outputs["json"] = self._run_json_export(task)
        outputs["trace"] = self._run_trace_export(task)
        outputs["issues"] = self._run_issue_report_export(task)
        outputs["rebarDetailing"] = self._run_rebar_detailing_export(task)
        self._stage(task, 92, "压缩完整交付包")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        bundle_dir = EXPORT_DIR / f"{task.project_id}_full_delivery_v2_5_0"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        import json, shutil, zipfile
        manifest = {
            "projectId": task.project_id,
            "packageVersion": "2.5.0",
            "softwareModuleCompletion": 100,
            "outputs": outputs,
            "officialIssueBoundary": "Software deliverable loop is complete. Project-specific sealed issue still requires professional review and company signing workflow.",
        }
        (bundle_dir / "delivery_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for name, item in outputs.items():
            file_path = item.get("filePath") if isinstance(item, dict) else None
            if file_path:
                src = Path(str(file_path))
                if src.exists():
                    dst = bundle_dir / f"{name}_{src.name}"
                    shutil.copy2(src, dst)
        zip_path = EXPORT_DIR / f"{task.project_id}_full_delivery_v2_5_0.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in bundle_dir.iterdir():
                if file.is_file():
                    zf.write(file, arcname=file.name)
        result = self._file_result(zip_path, "application/zip", {"projectId": task.project_id, "outputs": outputs, "refreshProject": True, "softwareModuleCompletion": 100})
        return result

    def _file_result(self, path: Path, media_type: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {"filePath": str(path), "filename": path.name, "mediaType": media_type, "sizeBytes": path.stat().st_size if path.exists() else 0}
        if extra:
            result.update(extra)
        return result

    def _title_for(self, operation: str) -> str:
        return {
            "calculation_full": "一键计算校核",
            "candidate_comparison": "候选方案 A/B/C 完整比选",
            "export_ifc_light": "导出 IFC 轻量协调版",
            "export_ifc_analysis": "导出 IFC 分析模型版",
            "export_ifc_construction_visual": "导出 IFC 施工图可视化版",
            "export_ifc_detailed": "导出 IFC 语义详细版",
            "export_ifc": "导出 IFC",
            "export_report": "导出 DOCX 计算书",
            "export_drawings_cad": "导出 CAD 图纸包",
            "export_drawings_svg": "导出 SVG 图纸包",
            "export_json": "导出 JSON 数据",
            "export_trace": "导出计算追溯链",
            "export_issue_report": "导出问题清单与完成度评估",
            "export_rebar_detailing": "导出钢筋施工详图深化 JSON",
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

    def _set(self, task: TaskRecord, **patch: Any) -> None:
        with self._lock:
            for key, value in patch.items():
                setattr(task, key, value)
            task.updated_at = _now()

    def _append_log(self, task: TaskRecord, message: str) -> None:
        with self._lock:
            task.logs.append(f"[{_now()}] {message}")
            task.updated_at = _now()


task_manager = TaskManager()
