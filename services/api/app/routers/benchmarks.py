from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.services.benchmark_cases import export_benchmark_package, list_benchmark_cases, run_all_benchmarks, run_benchmark_case
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"


@router.get("")
def list_cases() -> dict:
    return {"benchmarkVersion": "2.3.0", "cases": list_benchmark_cases()}


@router.post("/run")
def run_cases(case_id: str | None = Query(default=None, alias="caseId"), persist: bool = False, repo: ProjectRepository = Depends(get_repository)) -> dict:
    if case_id:
        return run_benchmark_case(case_id, repo=repo, persist=persist)
    return run_all_benchmarks(repo=repo, persist=persist)


@router.post("/export-package")
@router.get("/export-package", include_in_schema=False)
def export_package(persist: bool = False, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    path = export_benchmark_package(EXPORT_DIR, repo=repo, persist=persist)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")
