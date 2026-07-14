from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import MonitoringRecord
from app.services.advanced_suite import build_advanced_engineering_suite
from app.services.collision_service import evaluate_model_collisions
from app.services.monitoring_calibration import calibrate_from_monitoring, monitoring_control_summary, monitoring_summary
from app.services.node_local_analysis import evaluate_node_local_response
from app.services.review_workflow import review_status, transition_review
from app.services.serviceability_service import evaluate_long_term_serviceability
from app.services.coordination_optimizer import build_coordination_optimization, apply_coordination_candidate
from app.services.node_submodel import build_node_submodels
from app.services.crane_logistics import optimize_cage_crane_logistics
from app.services.support_topology_graph import analyze_support_topology, apply_topology_enhancements, preview_topology_enhancements
from app.drawings.formal_issue import create_drawing_revision
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/advanced", tags=["advanced-engineering"])


_RECORD_TYPE_ALIASES = {
    "wall_displacement": "wall_displacement", "wall": "wall_displacement", "墙体位移": "wall_displacement",
    "support_axial_force": "support_axial_force", "support": "support_axial_force", "支撑轴力": "support_axial_force",
    "groundwater": "groundwater", "water": "groundwater", "地下水位": "groundwater",
    "settlement": "settlement", "沉降": "settlement",
}


def _first(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _float_or_none(value: str | None) -> float | None:
    return None if value in {None, ""} else float(value)


def _monitoring_record_from_row(row: dict[str, str], source: str) -> MonitoringRecord:
    kind_raw = _first(row, "record_type", "recordType", "type", "类型") or ""
    kind = _RECORD_TYPE_ALIASES.get(kind_raw.strip().casefold()) or _RECORD_TYPE_ALIASES.get(kind_raw.strip())
    if not kind:
        raise ValueError(f"unsupported record type: {kind_raw}")
    value_raw = _first(row, "measured_value", "measuredValue", "value", "监测值")
    unit = _first(row, "unit", "单位") or ("kN" if kind == "support_axial_force" else "m" if kind == "groundwater" else "mm")
    if value_raw is None:
        raise ValueError("measured value is required")
    quality_raw = (_first(row, "quality", "质量") or "verified").lower()
    quality = quality_raw if quality_raw in {"verified", "provisional", "rejected"} else "provisional"
    payload = {
        "record_type": kind, "object_id": _first(row, "object_id", "objectId", "对象ID"),
        "object_code": _first(row, "object_code", "objectCode", "对象编号"),
        "stage_id": _first(row, "stage_id", "stageId", "阶段ID"),
        "measured_value": float(value_raw), "unit": unit,
        "elevation": _float_or_none(_first(row, "elevation", "标高")),
        "x": _float_or_none(_first(row, "x", "X")), "y": _float_or_none(_first(row, "y", "Y")),
        "quality": quality, "source": source, "note": _first(row, "note", "备注"),
    }
    timestamp = _first(row, "timestamp", "time", "时间")
    if timestamp:
        payload["timestamp"] = timestamp
    return MonitoringRecord(**payload)


class MonitoringPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    records: list[MonitoringRecord]


class ReviewPayload(BaseModel):
    role: Literal["designer", "checker", "reviewer", "approver"]
    actor: str = Field(min_length=1, max_length=80)
    action: Literal["submit", "accept", "reject", "reopen", "approve"]
    comment: str | None = Field(default=None, max_length=500)


class RevisionPayload(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    sheet_numbers: list[str] = Field(default_factory=list)
    author: str = Field(default="AI-DRAFT", min_length=1, max_length=80)
    issue_status: Literal["review", "construction", "superseded"] = "review"


class CoordinationApplyPayload(BaseModel):
    issue_id: str = Field(min_length=1, max_length=120)
    candidate_id: str = Field(min_length=1, max_length=180)
    mode: Literal["conservative", "balanced", "economic"] = "balanced"


@router.get("/suite")
def suite(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    result = build_advanced_engineering_suite(project, mode)
    project.advanced_engineering["latestSuite"] = result
    repo.save(project)
    return result


@router.get("/serviceability")
def serviceability(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_long_term_serviceability(repo.require(project_id), mode)


@router.get("/topology")
def topology(project_id: str, preview: bool = True, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    return preview_topology_enhancements(project) if preview else analyze_support_topology(project)


@router.post("/topology/apply")
def topology_apply(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    result = apply_topology_enhancements(project)
    repo.save(project)
    return result


@router.get("/collisions")
def collisions(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_model_collisions(repo.require(project_id), mode)


@router.get("/node-local")
def node_local(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_node_local_response(repo.require(project_id))


@router.get("/coordination-optimization")
def coordination_optimization(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return build_coordination_optimization(repo.require(project_id), mode=mode)


@router.post("/coordination-optimization/apply")
def coordination_optimization_apply(
    project_id: str,
    payload: CoordinationApplyPayload = Body(...),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    try:
        result = apply_coordination_candidate(project, payload.issue_id, payload.candidate_id, mode=payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repo.save(project)
    return result


@router.get("/node-submodels")
def node_submodels(
    project_id: str,
    top_n: int = Query(8, ge=1, le=20),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return build_node_submodels(repo.require(project_id), top_n=top_n)


@router.get("/crane-logistics")
def crane_logistics(
    project_id: str,
    mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return optimize_cage_crane_logistics(repo.require(project_id), mode=mode)


@router.get("/monitoring")
def monitoring(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return monitoring_summary(repo.require(project_id))


@router.get("/monitoring/control")
def monitoring_control(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return monitoring_control_summary(repo.require(project_id))


@router.get("/monitoring/digital-twin")
def monitoring_digital_twin(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    control = monitoring_control_summary(repo.require(project_id))
    return {
        "projectId": project_id,
        "digitalTwin": control.get("digitalTwin"),
        "highestLevel": control.get("highestLevel"),
        "alerts": control.get("alerts"),
        "series": control.get("series"),
        "thresholdPolicy": control.get("thresholdPolicy"),
    }


@router.post("/monitoring/records")
def add_monitoring_records(project_id: str, payload: MonitoringPayload = Body(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    project.monitoring_records.extend(payload.records)
    repo.save(project)
    return monitoring_summary(project)






@router.get("/monitoring/template.csv")
def monitoring_csv_template() -> Response:
    content = (
        "record_type,object_code,stage_id,timestamp,measured_value,unit,elevation,x,y,quality,note\n"
        "wall_displacement,DW-S1-001,,2026-07-11T08:00:00+08:00,12.5,mm,-8.0,,,verified,example\n"
        "support_axial_force,GS-L3-2,,2026-07-11T08:00:00+08:00,8500,kN,,,,verified,example\n"
        "groundwater,,,2026-07-11T08:00:00+08:00,-1.8,m,,,,verified,example\n"
    )
    return Response(
        content="\ufeff" + content, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=pitguard_monitoring_template.csv"},
    )

@router.post("/monitoring/import-csv")
async def import_monitoring_csv(project_id: str, file: UploadFile = File(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Monitoring CSV exceeds the 5 MB limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Monitoring CSV must use UTF-8 encoding.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="Monitoring CSV has no header row.")
    records: list[MonitoringRecord] = []
    errors: list[dict[str, str | int]] = []
    for row_number, row in enumerate(reader, start=2):
        normalized = {str(key).strip(): ("" if value is None else str(value).strip()) for key, value in row.items() if key is not None}
        if not any(normalized.values()):
            continue
        try:
            records.append(_monitoring_record_from_row(normalized, file.filename or "monitoring.csv"))
        except (TypeError, ValueError) as exc:
            errors.append({"row": row_number, "message": str(exc)})
    if not records:
        raise HTTPException(status_code=422, detail={"message": "No valid monitoring records found.", "errors": errors[:50]})
    project = repo.require(project_id)
    project.monitoring_records.extend(records)
    project.advanced_engineering["monitoringImport"] = {
        "filename": file.filename, "importedCount": len(records), "errorCount": len(errors),
    }
    repo.save(project)
    return {**monitoring_summary(project), "importedCount": len(records), "errorCount": len(errors), "errors": errors[:50]}

@router.post("/monitoring/calibrate")
def calibrate(project_id: str, apply: bool = Query(False), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    run = calibrate_from_monitoring(project, apply=apply)
    repo.save(project)
    return run.model_dump(mode="json", by_alias=True)


@router.get("/review")
def get_review(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return review_status(repo.require(project_id))


@router.post("/review/transition")
def review_transition(project_id: str, payload: ReviewPayload = Body(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    try:
        result = transition_review(project, payload.role, payload.actor, payload.action, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.save(project)
    return result


@router.get("/revisions")
def revisions(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> list[dict]:
    return [x.model_dump(mode="json", by_alias=True) for x in repo.require(project_id).drawing_revisions]


@router.post("/revisions")
def add_revision(project_id: str, payload: RevisionPayload = Body(...), repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    if payload.issue_status == "construction" and not review_status(project).get("approvalValid"):
        raise HTTPException(status_code=409, detail="Construction revision requires a valid four-level approval for the current design snapshot.")
    item = create_drawing_revision(project, payload.description, payload.sheet_numbers, payload.author, payload.issue_status)
    repo.save(project)
    return item.model_dump(mode="json", by_alias=True)
