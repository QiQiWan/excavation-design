from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.storage.repository import ProjectRepository, get_repository
from app.schemas.domain import Borehole, Stratum

router = APIRouter(prefix="/api/projects/{project_id}", tags=["boreholes"])


@router.post("/boreholes/import-csv")
async def import_boreholes_csv(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    from app.services.borehole_import import parse_borehole_rows, read_csv_bytes
    project = repo.require(project_id)
    rows = read_csv_bytes(await file.read())
    result = parse_borehole_rows(rows, source_file=file.filename)
    if result.success:
        project.boreholes = result.boreholes
        project.strata = result.strata
        project.messages.append(f"Imported {result.borehole_count} boreholes from {file.filename}")
        repo.save(project)
    return result.as_response()


@router.post("/boreholes/import-excel")
async def import_boreholes_excel(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    from app.services.borehole_import import parse_borehole_rows, read_excel_bytes
    project = repo.require(project_id)
    rows = read_excel_bytes(await file.read())
    result = parse_borehole_rows(rows, source_file=file.filename)
    if result.success:
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
            data.update(payload)
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
