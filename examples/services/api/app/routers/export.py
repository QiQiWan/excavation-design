from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/export", tags=["export"])

EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"


def _lazy_call(module: str, name: str, *args, **kwargs):
    """Load export-only dependencies after an export request reaches a worker.

    DXF/IFC/DOCX/XLSX stacks are intentionally excluded from the always-on API
    import graph. This keeps login, health and project-list endpoints available
    even if an optional exporter is missing or a worker is under memory pressure.
    """
    return getattr(import_module(module), name)(*args, **kwargs)


def build_drawing_set_manifest(*args, **kwargs):
    return _lazy_call("app.drawings.cad_export", "build_drawing_set_manifest", *args, **kwargs)


def export_construction_cad_package(*args, **kwargs):
    return _lazy_call("app.drawings.cad_export", "export_construction_cad_package", *args, **kwargs)


def export_construction_svg_package(*args, **kwargs):
    return _lazy_call("app.drawings.cad_export", "export_construction_svg_package", *args, **kwargs)


def export_formal_drawing_package(*args, **kwargs):
    return _lazy_call("app.drawings.formal_issue", "export_formal_drawing_package", *args, **kwargs)


def evaluate_drawing_issue_gate(*args, **kwargs):
    return _lazy_call("app.drawing_rules", "evaluate_drawing_issue_gate", *args, **kwargs)


def review_status(*args, **kwargs):
    return _lazy_call("app.services.review_workflow", "review_status", *args, **kwargs)


def export_simplified_ifc(*args, **kwargs):
    return _lazy_call("app.ifc.exporter", "export_simplified_ifc", *args, **kwargs)


def build_rebar_ifc_visualization(*args, **kwargs):
    return _lazy_call("app.ifc.rebar_visualization", "build_rebar_ifc_visualization", *args, **kwargs)


def evaluate_ifc_model_compatibility(*args, **kwargs):
    return _lazy_call("app.quality.ifc_compatibility", "evaluate_ifc_model_compatibility", *args, **kwargs)


def validate_ifc_file(*args, **kwargs):
    return _lazy_call("app.quality.ifc_compatibility", "validate_ifc_file", *args, **kwargs)


def export_docx_report(*args, **kwargs):
    return _lazy_call("app.reports.docx_report", "export_docx_report", *args, **kwargs)


def export_wall_length_redundancy_report(*args, **kwargs):
    return _lazy_call("app.services.wall_length_optimizer", "export_wall_length_redundancy_report", *args, **kwargs)


def export_design_scheme_ledger(*args, **kwargs):
    return _lazy_call("app.services.design_scheme_ledger", "export_design_scheme_ledger", *args, **kwargs)


def build_rebar_design_scheme(*args, **kwargs):
    return _lazy_call("app.services.rebar_scheme_optimizer", "build_rebar_design_scheme", *args, **kwargs)


def export_rebar_detailing_package(*args, **kwargs):
    return _lazy_call("app.services.rebar_export", "export_rebar_detailing_package", *args, **kwargs)


def export_coordinated_delivery_package(*args, **kwargs):
    return _lazy_call("app.services.delivery_package", "export_coordinated_delivery_package", *args, **kwargs)


def evaluate_delivery_release_readiness(*args, **kwargs):
    return _lazy_call("app.services.delivery_release", "evaluate_delivery_release_readiness", *args, **kwargs)


def _export_ifc_with_check(project, mode: str) -> tuple[Path, object]:
    precheck = evaluate_ifc_model_compatibility(project)
    path = export_simplified_ifc(project, EXPORT_DIR, export_mode=mode)
    file_check = validate_ifc_file(path, base=precheck)
    sidecar = path.with_suffix(".ifc_check.json")
    sidecar.write_text(json.dumps(file_check.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
    return path, file_check


@router.post("/ifc")
@router.get("/ifc", include_in_schema=False)
def export_ifc(
    project_id: str,
    mode: Literal["coordination_light", "analysis_model", "design_detailed", "construction_visual"] = Query("design_detailed", description="IFC export mode: coordination_light, analysis_model, construction_visual or design_detailed"),
    repo: ProjectRepository = Depends(get_repository),
) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, mode)
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.post("/ifc-light")
@router.get("/ifc-light", include_in_schema=False)
def export_ifc_light(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "coordination_light")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")




@router.post("/ifc-analysis")
@router.get("/ifc-analysis", include_in_schema=False)
def export_ifc_analysis(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "analysis_model")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.post("/ifc-construction-visual")
@router.get("/ifc-construction-visual", include_in_schema=False)
def export_ifc_construction_visual(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "construction_visual")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.post("/ifc-detailed")
@router.get("/ifc-detailed", include_in_schema=False)
def export_ifc_detailed(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "design_detailed")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.post("/ifc-check")
@router.get("/ifc-check", include_in_schema=False)
def export_ifc_check(
    project_id: str,
    mode: Literal["coordination_light", "analysis_model", "design_detailed", "construction_visual"] = Query("design_detailed"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    _, result = _export_ifc_with_check(project, mode)
    return result.model_dump(mode="json", by_alias=True)


@router.post("/ifc-rebar-visualization")
@router.get("/ifc-rebar-visualization", include_in_schema=False)
def export_ifc_rebar_visualization(
    project_id: str,
    max_bars: int = Query(2400, ge=50, le=5000, description="Maximum sampled bars returned for browser visualization"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    return build_rebar_ifc_visualization(project, max_bars=max_bars)


@router.post("/drawings-cad")
@router.get("/drawings-cad", include_in_schema=False)
def export_drawings_cad(
    project_id: str,
    scope: Literal["full", "general", "rebar", "details"] = Query("full"),
    rebar_mode: Literal["conservative", "balanced", "economic"] = Query("balanced"),
    issue_mode: Literal["review", "construction"] = Query("review"),
    repo: ProjectRepository = Depends(get_repository),
) -> FileResponse:
    project = repo.require(project_id)
    scheme = build_rebar_design_scheme(project, mode=rebar_mode)
    can_issue = bool((scheme.get("diagnostics") or {}).get("canIssueConstructionDrawings"))
    approval = review_status(project)
    current_revision = next((r for r in reversed(project.drawing_revisions) if r.issue_status == "construction" and r.snapshot_hash == approval.get("currentSnapshotHash")), None)
    issue_gate = evaluate_drawing_issue_gate(
        project, issue_mode=issue_mode, engineering_gate_allowed=can_issue, approval=approval, current_revision_valid=current_revision is not None
    )
    if not issue_gate["allowed"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "当前出图规则集的施工版发行条件未满足，只能导出审查版 CAD。",
                "diagnostics": scheme.get("diagnostics"),
                "review": approval,
                "drawingIssueGate": issue_gate,
            },
        )
    path = export_construction_cad_package(project, EXPORT_DIR, scope=scope, rebar_mode=rebar_mode, issue_mode=issue_mode)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.get("/drawings-manifest")
def get_drawings_manifest(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_drawing_set_manifest(repo.require(project_id))


@router.post("/drawings-svg")
@router.get("/drawings-svg", include_in_schema=False)
def export_drawings_svg(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_construction_svg_package(project, EXPORT_DIR)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")




@router.post("/rebar-detailing-package")
@router.get("/rebar-detailing-package", include_in_schema=False)
def export_rebar_detailing_zip(
    project_id: str,
    mode: Literal["conservative", "balanced", "economic"] = Query("balanced"),
    repo: ProjectRepository = Depends(get_repository),
) -> FileResponse:
    project = repo.require(project_id)
    path = export_rebar_detailing_package(project, EXPORT_DIR, mode=mode)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.post("/report")
@router.get("/report", include_in_schema=False)
def export_report(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_docx_report(project, EXPORT_DIR)
    return FileResponse(path=path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.post("/json")
@router.get("/json", include_in_schema=False)
def export_json(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{project.id}.json"
    path.write_text(json.dumps(project.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
    return FileResponse(path=path, filename=path.name, media_type="application/json")

@router.post("/design-scheme-ledger")
@router.get("/design-scheme-ledger", include_in_schema=False)
def export_design_scheme(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_design_scheme_ledger(project, EXPORT_DIR, mode=mode)
    return FileResponse(path=path, filename=path.name, media_type="application/json")


@router.post("/wall-length-redundancy")
@router.get("/wall-length-redundancy", include_in_schema=False)
def export_wall_length_redundancy(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_wall_length_redundancy_report(project, EXPORT_DIR, mode=mode)
    return FileResponse(path=path, filename=path.name, media_type="application/json")



@router.post("/formal-drawing-package")
@router.get("/formal-drawing-package", include_in_schema=False)
def export_formal_drawings(
    project_id: str,
    issue_mode: Literal["review", "construction"] = Query("review"),
    rebar_mode: Literal["conservative", "balanced", "economic"] = Query("balanced"),
    repo: ProjectRepository = Depends(get_repository),
) -> FileResponse:
    project = repo.require(project_id)
    scheme = build_rebar_design_scheme(project, mode=rebar_mode)
    approval = review_status(project)
    current_revision = next((r for r in reversed(project.drawing_revisions) if r.issue_status == "construction" and r.snapshot_hash == approval.get("currentSnapshotHash")), None)
    issue_gate = evaluate_drawing_issue_gate(
        project,
        issue_mode=issue_mode,
        engineering_gate_allowed=bool((scheme.get("diagnostics") or {}).get("canIssueConstructionDrawings")),
        approval=approval,
        current_revision_valid=current_revision is not None,
    )
    if not issue_gate["allowed"]:
        raise HTTPException(status_code=409, detail={
            "message": "正式图纸包发行条件未满足。",
            "review": approval, "constructionRevisionValid": current_revision is not None,
            "diagnostics": scheme.get("diagnostics"), "drawingIssueGate": issue_gate,
        })
    path = export_formal_drawing_package(project, EXPORT_DIR, issue_mode=issue_mode, rebar_mode=rebar_mode)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.get("/release-readiness")
def get_release_readiness(
    project_id: str,
    issue_mode: Literal["review", "construction"] = Query("review"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    return evaluate_delivery_release_readiness(repo.require(project_id), issue_mode=issue_mode)


@router.post("/coordinated-delivery-package")
@router.get("/coordinated-delivery-package", include_in_schema=False)
def export_coordinated_delivery(
    project_id: str,
    issue_mode: Literal["review", "construction"] = Query("review"),
    rebar_mode: Literal["conservative", "balanced", "economic"] = Query("balanced"),
    include_ifc_profiles: bool = Query(True),
    repo: ProjectRepository = Depends(get_repository),
) -> FileResponse:
    project = repo.require(project_id)
    try:
        path = export_coordinated_delivery_package(
            project, EXPORT_DIR, issue_mode=issue_mode, rebar_mode=rebar_mode, include_ifc_profiles=include_ifc_profiles
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc),
            "releaseReadiness": evaluate_delivery_release_readiness(project, issue_mode=issue_mode),
        }) from exc
    return FileResponse(path=path, filename=path.name, media_type="application/zip")
