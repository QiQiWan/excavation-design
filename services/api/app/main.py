from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import assurance, benchmarks, boreholes, cad_template, calculation, design, excavation, export, geology, issues, projects, rebar, tasks
from app.rules.registry import list_rules

app = FastAPI(
    title="PitGuard BIM Designer API",
    version="2.5.0",
    description="V2.5.0 normative-algorithm backend with completed multi-view issue highlighting, validated enterprise CAD templates, shop-detailing rebar geometry, cage segmentation, lifting, splice, cover and signoff workflows.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(benchmarks.router)
app.include_router(cad_template.router)
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
        "pythonExecutable": sys.executable,
        "pythonVersion": sys.version.split()[0],
        "workingDirectory": os.getcwd(),
        "databasePath": db_path,
        "databaseDirectoryExists": bool(db_path and Path(db_path).expanduser().parent.exists()),
        "missingModules": [item["packageName"] for item in modules if not item["available"]],
        "modules": modules,
    }


@app.get("/api/rules")
def rules() -> dict:
    return {"rules": list_rules(), "professionalReviewRequired": True}
