from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import logging
import os
import sqlite3
import shutil
import sys

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import advanced, artifacts, assurance, auth, benchmarks, boreholes, cad_template, calculation, design, drawing_rules, excavation, expert_design, export, geology, industrial, issues, projects, rebar, standards, tasks, wall_optimization
from app.rules.registry import list_rules
from app.version import SOFTWARE_VERSION, version_manifest
from app.services.unit_registry import unit_registry
from app.services.runtime_observability import runtime_observability
from app.services.runtime_resource_policy import adaptive_resource_policy, mb
from app.services.access_control import AccessIdentity, public_access_allowed, required_role, resolve_identity, role_allows, security_status
from app.storage.database import DEFAULT_DB_PATH
from app.storage.repository import ProjectRepository, get_repository
from app.tasks.manager import task_manager

logger = logging.getLogger("pitguard.performance")

app = FastAPI(
    title="PitGuard BIM Designer API",
    version=SOFTWARE_VERSION,
    description="PitGuard V3.37.0 progressive design, adaptive resource governance and workspace-first delivery.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("PITGUARD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_access_control(request: Request, call_next):
    identity = resolve_identity(request)
    if identity is None and public_access_allowed(request.url.path):
        identity = AccessIdentity(actor="anonymous-health", role="viewer", authenticated=False, key_id=None)
    if identity is None:
        return JSONResponse(status_code=401, content={"detail": "未登录、会话已过期或 API 密钥无效"})
    required = required_role(request.method, request.url.path)
    if not role_allows(identity.role, required):
        return JSONResponse(status_code=403, content={"detail": f"Role {identity.role} cannot perform this operation; required role: {required}"})
    request.state.pitguard_identity = identity
    response = await call_next(request)
    response.headers["X-PitGuard-Actor"] = identity.actor
    response.headers["X-PitGuard-Role"] = identity.role
    return response


@app.middleware("http")
async def observe_http_requests(request: Request, call_next):
    started = perf_counter()
    runtime_observability.begin()
    status_code = 500
    request_id = request.headers.get("X-PitGuard-Client-Request-Id") or f"srv-{uuid4().hex[:12]}"
    try:
        response = await call_next(request)
        status_code = response.status_code
        elapsed_ms = (perf_counter() - started) * 1000.0
        response.headers["X-PitGuard-Request-Id"] = request_id
        response.headers["X-PitGuard-Duration-Ms"] = f"{elapsed_ms:.1f}"
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        if elapsed_ms >= float(os.getenv("PITGUARD_SLOW_REQUEST_MS", "1200")):
            logger.warning("slow request id=%s method=%s path=%s status=%s duration_ms=%.1f", request_id, request.method, request.url.path, status_code, elapsed_ms)
        return response
    finally:
        runtime_observability.record(
            request.url.path,
            status_code,
            (perf_counter() - started) * 1000.0,
            slow_threshold_ms=float(os.getenv("PITGUARD_SLOW_REQUEST_MS", "1200")),
        )

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(artifacts.router)
app.include_router(standards.router)
app.include_router(benchmarks.router)
app.include_router(cad_template.router)
app.include_router(drawing_rules.router)
app.include_router(tasks.router)
app.include_router(assurance.router)
app.include_router(boreholes.router)
app.include_router(geology.router)
app.include_router(excavation.router)
app.include_router(design.router)
app.include_router(calculation.router)
app.include_router(export.router)
app.include_router(issues.router)
app.include_router(rebar.router)
app.include_router(wall_optimization.router)
app.include_router(expert_design.router)
app.include_router(advanced.router)
app.include_router(industrial.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pitguard-api"}


@app.get("/health/live")
def health_live() -> dict[str, str]:
    """Process-only liveness probe; never touches the project database."""
    return {"status": "alive", "service": "pitguard-api"}


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    """Small unauthenticated readiness probe for Nginx/systemd monitoring."""
    db_path = Path(os.getenv("PITGUARD_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    try:
        with sqlite3.connect(db_path, timeout=1.0) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("SELECT 1").fetchone()
        payload = {"status": "ready", "service": "pitguard-api", "database": True}
        return JSONResponse(status_code=200, content=payload)
    except Exception as exc:
        payload = {"status": "not_ready", "service": "pitguard-api", "database": False, "error": str(exc)[:240]}
        return JSONResponse(status_code=503, content=payload)


@app.get("/api/system/diagnostics")
def system_diagnostics() -> dict:
    required_modules = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "pydantic": "pydantic",
        "multipart": "python-multipart",
        "numpy": "numpy",
        "shapely": "shapely",
        "docx": "python-docx",
        "openpyxl": "openpyxl",
        "matplotlib": "matplotlib",
        "meshio": "meshio",
    }
    modules = []
    for import_name, package_name in required_modules.items():
        available = util.find_spec(import_name) is not None
        version = None
        if available:
            try:
                version = metadata.version(package_name)
            except metadata.PackageNotFoundError:
                version = "installed"
        modules.append({
            "importName": import_name,
            "packageName": package_name,
            "available": available,
            "version": version,
        })
    db_path = os.getenv("PITGUARD_DB_PATH")
    return {
        "service": "pitguard-api",
        "version": app.version,
        **version_manifest(),
        "pythonVersion": sys.version.split()[0],
        "databaseConfigured": bool(db_path),
        "databaseDirectoryExists": bool(db_path and Path(db_path).expanduser().parent.exists()),
        "missingModules": [item["packageName"] for item in modules if not item["available"]],
        "modules": modules,
    }


@app.get("/api/system/resource-policy")
def system_resource_policy() -> dict:
    policy = adaptive_resource_policy(role=os.getenv("PITGUARD_PROCESS_ROLE", "api"))
    return {
        **policy,
        "effectiveTotalMB": mb(policy.get("effectiveTotalBytes")),
        "effectiveAvailableMB": mb(policy.get("effectiveAvailableBytes")),
        "reserveMB": mb(policy.get("reserveBytes")),
        "apiFullLoadLimitMB": mb(policy.get("apiFullLoadLimitBytes")),
        "workspaceLimitMB": mb(policy.get("workspaceLimitBytes")),
        "workerSoftLimitMB": mb(policy.get("workerSoftLimitBytes")),
        "workerHardLimitMB": mb(policy.get("workerHardLimitBytes")),
        "diskTotalMB": mb(policy.get("diskTotalBytes")),
        "diskFreeMB": mb(policy.get("diskFreeBytes")),
        "diskReserveMB": mb(policy.get("diskReserveBytes")),
        "diskUsableMB": mb(policy.get("diskUsableBytes")),
        "cpuLoadPercent": round(float(policy.get("cpuLoadRatio") or 0.0) * 100.0, 1),
    }


@app.get("/api/system/metrics")
def system_metrics() -> dict:
    return {
        "http": runtime_observability.snapshot(),
        "tasks": task_manager.metrics(),
        "version": version_manifest(),
    }


@app.get("/api/system/readiness")
def system_readiness() -> dict:
    db_path = Path(os.getenv("PITGUARD_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    db_ok = False
    db_error = None
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=3.0) as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception as exc:
        db_error = str(exc)
    diagnostics = system_diagnostics()
    missing = list(diagnostics.get("missingModules") or [])
    task_metrics = task_manager.metrics()
    disk_total = disk_free = 0
    try:
        disk = shutil.disk_usage(db_path.parent)
        disk_total, disk_free = int(disk.total), int(disk.free)
    except OSError:
        pass
    disk_free_ratio = disk_free / disk_total if disk_total else 0.0
    process_memory = float(task_metrics.get("processMemoryMb") or 0.0)
    memory_limit = float(task_metrics.get("memorySoftLimitMb") or 0.0)
    memory_ratio = process_memory / memory_limit if memory_limit > 0 else 0.0
    status_counts = dict(task_metrics.get("statusCounts") or {})
    degraded_reasons: list[str] = []
    blocking_reasons: list[str] = []
    if disk_total and (disk_free < 1024 ** 3 or disk_free_ratio < 0.05):
        degraded_reasons.append("数据库所在磁盘剩余空间低于 1 GiB 或 5%")
    if disk_total and disk_free < 256 * 1024 ** 2:
        blocking_reasons.append("数据库所在磁盘剩余空间低于 256 MiB")
    if memory_ratio >= 0.80:
        degraded_reasons.append("API 进程内存已超过软限制的 80%")
    if memory_ratio >= 0.98:
        blocking_reasons.append("API 进程内存接近软限制")
    if int(status_counts.get("queued") or 0) > 3:
        degraded_reasons.append("后台任务排队超过 3 个")
    ready = db_ok and not missing and not blocking_reasons
    status = "not_ready" if not ready else "degraded" if degraded_reasons else "ready"
    return {
        "status": status,
        "ready": ready,
        "degraded": bool(degraded_reasons),
        "degradedReasons": degraded_reasons,
        "blockingReasons": blocking_reasons,
        "database": {"path": str(db_path), "available": db_ok, "error": db_error},
        "storage": {"totalBytes": disk_total, "freeBytes": disk_free, "freeRatio": round(disk_free_ratio, 6) if disk_total else None},
        "memory": {"processMb": process_memory, "softLimitMb": memory_limit, "ratio": round(memory_ratio, 6) if memory_limit else None},
        "missingModules": missing,
        "tasks": task_metrics,
        "security": security_status(),
        "backup": {"directory": str(Path(os.getenv("PITGUARD_BACKUP_DIR", db_path.parent / "backups"))), "retention": max(1, int(os.getenv("PITGUARD_BACKUP_RETENTION", "20")))},
        "version": version_manifest(),
    }


@app.get("/api/system/security")
def system_security() -> dict:
    return security_status()


@app.post("/api/system/backup")
def create_system_backup(repo: ProjectRepository = Depends(get_repository)) -> dict:
    return repo.store.backup()


@app.get("/api/system/backups")
def list_system_backups(limit: int = 20, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return {"backups": repo.store.list_backups(limit=limit)}


@app.get("/api/system/units")
def system_units() -> dict:
    return unit_registry()


@app.get("/api/rules")
def rules() -> dict:
    return {"rules": list_rules(), "professionalReviewRequired": True}
