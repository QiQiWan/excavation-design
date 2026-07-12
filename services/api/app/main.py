from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import advanced, assurance, benchmarks, boreholes, cad_template, calculation, design, drawing_rules, excavation, export, geology, issues, projects, rebar, standards, tasks, wall_optimization
from app.rules.registry import list_rules
from app.version import SOFTWARE_VERSION, version_manifest
from app.services.unit_registry import unit_registry

app = FastAPI(
    title="PitGuard BIM Designer API",
    version=SOFTWARE_VERSION,
    description="PitGuard V3.11 integrated P0-P2 engineering assurance, standards-process traceability, reinforced online documentation and deliverable packaging.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("PITGUARD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
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
app.include_router(advanced.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pitguard-api"}


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


@app.get("/api/system/units")
def system_units() -> dict:
    return unit_registry()


@app.get("/api/rules")
def rules() -> dict:
    return {"rules": list_rules(), "professionalReviewRequired": True}
