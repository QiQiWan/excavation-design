from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.tasks.manager import task_manager

router = APIRouter(tags=["tasks"])


class TaskCreateRequest(BaseModel):
    operation: str = Field(..., description="Task operation, e.g. calculation_full, export_report, full_delivery")
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/projects/{project_id}/tasks")
def create_project_task(project_id: str, body: TaskCreateRequest) -> dict:
    task = task_manager.submit(project_id=project_id, operation=body.operation, payload=body.payload)
    return task.as_dict(include_logs=True)


class CandidateBatchRequest(BaseModel):
    top_n: int = Field(default=3, ge=1, le=3, alias="topN")
    use_cache: bool = Field(default=True, alias="useCache")


@router.post("/api/projects/{project_id}/tasks/candidate-comparison-batch")
def create_candidate_comparison_batch(project_id: str, body: CandidateBatchRequest) -> dict:
    try:
        tasks = task_manager.submit_candidate_batch(project_id, top_n=body.top_n, use_cache=body.use_cache)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "projectId": project_id,
        "taskCount": len(tasks),
        "tasks": [task.as_dict(include_logs=True) for task in tasks],
    }


@router.get("/api/projects/{project_id}/tasks")
def list_project_tasks(project_id: str) -> list[dict]:
    return [task.as_dict(include_logs=False, include_result=False) for task in task_manager.list(project_id=project_id)]


@router.get("/api/tasks")
def list_tasks() -> list[dict]:
    return [task.as_dict(include_logs=False, include_result=False) for task in task_manager.list()]


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task.as_dict(include_logs=True)


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    task = task_manager.cancel(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task.as_dict(include_logs=True)


@router.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str) -> dict:
    try:
        task = task_manager.retry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task.as_dict(include_logs=True)


@router.get("/api/task-metrics")
def task_metrics() -> dict:
    return task_manager.metrics()


@router.get("/api/tasks/{task_id}/download")
def download_task_result(task_id: str) -> FileResponse:
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if task.status != "success" or not task.result or not task.result.get("filePath"):
        raise HTTPException(status_code=409, detail="Task has no downloadable file result yet.")
    path = Path(str(task.result["filePath"]))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Task output file no longer exists.")
    return FileResponse(path=path, filename=str(task.result.get("filename") or path.name), media_type=str(task.result.get("mediaType") or "application/octet-stream"))
