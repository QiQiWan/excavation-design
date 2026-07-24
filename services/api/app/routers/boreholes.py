from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException

from app.storage.repository import ProjectRepository, get_repository
from app.schemas.domain import Borehole, Stratum
from app.storage.artifact_store import ProjectArtifactStore, append_project_artifact_ref
from app.tasks.manager import task_manager
from app.services.borehole_import_workflow import import_staging_root

from pathlib import Path
from uuid import uuid4
import hashlib
import os

router = APIRouter(prefix="/api/projects/{project_id}", tags=["boreholes"])


@router.post("/boreholes/import-task")
async def import_boreholes_task(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    """Stage an uploaded borehole file and parse it in the isolated worker.

    The API process only streams and hashes the upload.  Excel/CSV parsing,
    evidence persistence and project revision writes run in the task worker, so
    a slow workbook cannot freeze the interactive workspace or hit the normal
    request timeout.
    """
    if repo.revision(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    filename = Path(file.filename or "boreholes.csv").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xlsm"}:
        raise HTTPException(status_code=415, detail="仅支持 CSV、XLSX 或 XLSM 钻孔文件。")
    maximum = max(1024 * 1024, int(os.getenv("PITGUARD_BOREHOLE_IMPORT_MAX_BYTES", str(50 * 1024 * 1024))))
    staging = import_staging_root() / f"{project_id}-{uuid4().hex}{suffix}"
    size = 0
    digest = hashlib.sha256()
    try:
        with staging.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(status_code=413, detail=f"钻孔文件超过 {maximum / 1048576:.0f} MB 上限。")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="钻孔文件为空。")
        task_manager.ensure_worker_available()
        task = task_manager.submit(
            project_id=project_id,
            operation="borehole_import",
            payload={
                "stagingPath": str(staging),
                "originalFilename": filename,
                "contentType": file.content_type or "application/octet-stream",
                "importType": suffix.lstrip("."),
                "sizeBytes": size,
                "sha256": digest.hexdigest(),
            },
        )
        return task.as_dict(include_logs=True)
    except HTTPException:
        staging.unlink(missing_ok=True)
        raise
    except RuntimeError as exc:
        staging.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/boreholes/import-csv")
async def import_boreholes_csv(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
    project = repo.require(project_id)
    raw = await file.read()
    rows = read_csv_bytes(raw)
    result = parse_borehole_rows(rows, source_file=file.filename)
    if result.success:
        ref = ProjectArtifactStore().write_bytes(
            project.id,
            "engineering-source-evidence",
            raw,
            filename=file.filename or "boreholes.csv",
            content_type=file.content_type or "text/csv",
            metadata={"domain": "borehole", "importType": "csv"},
        )
        append_project_artifact_ref(project, ref, storage_key=f"borehole-import:{ref['sha256']}")
        for borehole in result.boreholes:
            borehole.source_file_sha256 = ref["sha256"]
            borehole.source_artifact_id = ref["artifactId"]
            borehole.source_verified = False
            for record in borehole.water_levels:
                record.source_file = file.filename
                record.source_file_sha256 = ref["sha256"]
                record.source_artifact_id = ref["artifactId"]
                record.quality = "provisional"
                record.verified_by = None
        project.boreholes = result.boreholes
        project.strata = result.strata
        project.messages.append(f"Imported {result.borehole_count} boreholes from {file.filename}")
        repo.save(project)
    return result.as_response()


@router.post("/boreholes/import-excel")
async def import_boreholes_excel(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    from app.services.borehole_import import parse_borehole_rows, read_excel_bytes
    project = repo.require(project_id)
    raw = await file.read()
    rows = read_excel_bytes(raw)
    result = parse_borehole_rows(rows, source_file=file.filename)
    if result.success:
        ref = ProjectArtifactStore().write_bytes(
            project.id,
            "engineering-source-evidence",
            raw,
            filename=file.filename or "boreholes.xlsx",
            content_type=file.content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            metadata={"domain": "borehole", "importType": "excel"},
        )
        append_project_artifact_ref(project, ref, storage_key=f"borehole-import:{ref['sha256']}")
        for borehole in result.boreholes:
            borehole.source_file_sha256 = ref["sha256"]
            borehole.source_artifact_id = ref["artifactId"]
            borehole.source_verified = False
            for record in borehole.water_levels:
                record.source_file = file.filename
                record.source_file_sha256 = ref["sha256"]
                record.source_artifact_id = ref["artifactId"]
                record.quality = "provisional"
                record.verified_by = None
        project.boreholes = result.boreholes
        project.strata = result.strata
        project.messages.append(f"Imported {result.borehole_count} boreholes from {file.filename}")
        repo.save(project)
    return result.as_response()


@router.get("/boreholes", response_model=list[Borehole])
def list_boreholes(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> list[Borehole]:
    return repo.require(project_id).boreholes


@router.put("/boreholes/{borehole_id}", response_model=Borehole)
def update_borehole(project_id: str, borehole_id: str, payload: dict, repo: ProjectRepository = Depends(get_repository)) -> Borehole:
    project = repo.require(project_id)
    for idx, borehole in enumerate(project.boreholes):
        if borehole.id == borehole_id:
            data = borehole.model_dump(mode="json", by_alias=True)
            sanitized = dict(payload)
            # Trust fields can only be set by the dedicated evidence-verification
            # workflow. Any engineering edit invalidates the previous signature.
            for key in ("sourceVerified", "source_verified"):
                sanitized.pop(key, None)
            if "waterLevels" in sanitized and isinstance(sanitized["waterLevels"], list):
                water_rows = []
                for row in sanitized["waterLevels"]:
                    item = dict(row) if isinstance(row, dict) else row
                    if isinstance(item, dict):
                        item["quality"] = "provisional"
                        item.pop("verifiedBy", None)
                        item.pop("verified_by", None)
                    water_rows.append(item)
                sanitized["waterLevels"] = water_rows
            data.update(sanitized)
            data["sourceVerified"] = False
            project.boreholes[idx] = Borehole.model_validate(data)
            repo.save(project)
            return project.boreholes[idx]
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"Borehole not found: {borehole_id}")


@router.get("/strata", response_model=list[Stratum])
def list_strata(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> list[Stratum]:
    return repo.require(project_id).strata


@router.put("/strata/{stratum_id}", response_model=Stratum)
def update_stratum(project_id: str, stratum_id: str, payload: dict, repo: ProjectRepository = Depends(get_repository)) -> Stratum:
    project = repo.require(project_id)
    for idx, stratum in enumerate(project.strata):
        if stratum.id == stratum_id or stratum.code == stratum_id:
            data = stratum.model_dump(mode="json", by_alias=True)
            data.update(payload)
            project.strata[idx] = Stratum.model_validate(data)
            repo.save(project)
            return project.strata[idx]
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"Stratum not found: {stratum_id}")
