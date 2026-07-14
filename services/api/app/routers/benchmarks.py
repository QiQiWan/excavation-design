from __future__ import annotations

from pathlib import Path
import os

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse

from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"


def _block_synchronous_benchmark_in_production() -> None:
    if str(os.getenv("PITGUARD_TASK_EXECUTION_MODE", "embedded")).strip().lower() == "external":
        raise HTTPException(
            status_code=409,
            detail="生产环境基准算例必须通过后台 export_benchmark_cases 任务执行，避免多进程占满 API 服务。",
        )


@router.get("")
def list_cases() -> dict:
    from app.services.benchmark_cases import list_benchmark_cases
    return {"benchmarkVersion": "2.3.0", "cases": list_benchmark_cases()}


@router.post("/run")
def run_cases(case_id: str | None = Query(default=None, alias="caseId"), persist: bool = False, repo: ProjectRepository = Depends(get_repository)) -> dict:
    _block_synchronous_benchmark_in_production()
    from app.services.benchmark_cases import run_all_benchmarks, run_benchmark_case
    if case_id:
        return run_benchmark_case(case_id, repo=repo, persist=persist)
    return run_all_benchmarks(repo=repo, persist=persist)


@router.post("/export-package")
@router.get("/export-package", include_in_schema=False)
def export_package(persist: bool = False, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    _block_synchronous_benchmark_in_production()
    from app.services.benchmark_cases import export_benchmark_package
    path = export_benchmark_package(EXPORT_DIR, repo=repo, persist=persist)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")
