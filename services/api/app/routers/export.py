from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.drawings.cad_export import build_drawing_set_manifest, export_construction_cad_package, export_construction_svg_package
from app.drawings.formal_issue import export_formal_drawing_package
from app.services.review_workflow import review_status
from app.ifc.exporter import export_simplified_ifc
from app.ifc.rebar_visualization import build_rebar_ifc_visualization
from app.quality.ifc_compatibility import evaluate_ifc_model_compatibility, validate_ifc_file
from app.reports.docx_report import export_docx_report
from app.storage.repository import ProjectRepository, get_repository
from app.services.wall_length_optimizer import export_wall_length_redundancy_report
from app.services.design_scheme_ledger import export_design_scheme_ledger
from app.services.rebar_scheme_optimizer import build_rebar_design_scheme

router = APIRouter(prefix="/api/projects/{project_id}/export", tags=["export"])

EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"


def _export_ifc_with_check(project, mode: str) -> tuple[Path, object]:
    precheck = evaluate_ifc_model_compatibility(project)
    path = export_simplified_ifc(project, EXPORT_DIR, export_mode=mode)
    file_check = validate_ifc_file(path, base=precheck)
    sidecar = path.with_suffix(".ifc_check.json")
    sidecar.write_text(json.dumps(file_check.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
    return path, file_check


@router.api_route("/ifc", methods=["GET", "POST"])
def export_ifc(
    project_id: str,
    mode: Literal["coordination_light", "analysis_model", "design_detailed", "construction_visual"] = Query("design_detailed", description="IFC export mode: coordination_light, analysis_model, construction_visual or design_detailed"),
    repo: ProjectRepository = Depends(get_repository),
) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, mode)
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.api_route("/ifc-light", methods=["GET", "POST"])
def export_ifc_light(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "coordination_light")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")




@router.api_route("/ifc-analysis", methods=["GET", "POST"])
def export_ifc_analysis(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "analysis_model")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.api_route("/ifc-construction-visual", methods=["GET", "POST"])
def export_ifc_construction_visual(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "construction_visual")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.api_route("/ifc-detailed", methods=["GET", "POST"])
def export_ifc_detailed(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path, _ = _export_ifc_with_check(project, "design_detailed")
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@router.api_route("/ifc-check", methods=["GET", "POST"])
def export_ifc_check(
    project_id: str,
    mode: Literal["coordination_light", "analysis_model", "design_detailed", "construction_visual"] = Query("design_detailed"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    _, result = _export_ifc_with_check(project, mode)
    return result.model_dump(mode="json", by_alias=True)


@router.api_route("/ifc-rebar-visualization", methods=["GET", "POST"])
def export_ifc_rebar_visualization(
    project_id: str,
    max_bars: int = Query(950, ge=50, le=2000, description="Maximum sampled bars returned for browser visualization"),
    repo: ProjectRepository = Depends(get_repository),
) -> dict:
    project = repo.require(project_id)
    return build_rebar_ifc_visualization(project, max_bars=max_bars)


@router.api_route("/drawings-cad", methods=["GET", "POST"])
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
    approval_required = bool(project.design_settings.require_formal_approval_for_construction)
    if issue_mode == "construction" and (not can_issue or (approval_required and not approval.get("approvalValid"))):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "仍有工程阻断项或正式审签尚未生效，只能导出审查版 CAD。",
                "diagnostics": scheme.get("diagnostics"),
                "review": approval,
                "approvalRequired": approval_required,
            },
        )
    path = export_construction_cad_package(project, EXPORT_DIR, scope=scope, rebar_mode=rebar_mode, issue_mode=issue_mode)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.get("/drawings-manifest")
def get_drawings_manifest(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return build_drawing_set_manifest(repo.require(project_id))


@router.api_route("/drawings-svg", methods=["GET", "POST"])
def export_drawings_svg(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_construction_svg_package(project, EXPORT_DIR)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.api_route("/report", methods=["GET", "POST"])
def export_report(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_docx_report(project, EXPORT_DIR)
    return FileResponse(path=path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.api_route("/json", methods=["GET", "POST"])
def export_json(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{project.id}.json"
    path.write_text(json.dumps(project.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2), encoding="utf-8")
    return FileResponse(path=path, filename=path.name, media_type="application/json")

@router.api_route("/design-scheme-ledger", methods=["GET", "POST"])
def export_design_scheme(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_design_scheme_ledger(project, EXPORT_DIR, mode=mode)
    return FileResponse(path=path, filename=path.name, media_type="application/json")


@router.api_route("/wall-length-redundancy", methods=["GET", "POST"])
def export_wall_length_redundancy(project_id: str, mode: str = Query("balanced", pattern="^(conservative|balanced|economic)$"), repo: ProjectRepository = Depends(get_repository)) -> FileResponse:
    project = repo.require(project_id)
    path = export_wall_length_redundancy_report(project, EXPORT_DIR, mode=mode)
    return FileResponse(path=path, filename=path.name, media_type="application/json")



@router.api_route("/formal-drawing-package", methods=["GET", "POST"])
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
    if issue_mode == "construction" and (
        not bool((scheme.get("diagnostics") or {}).get("canIssueConstructionDrawings"))
        or (project.design_settings.require_formal_approval_for_construction and not approval.get("approvalValid"))
        or current_revision is None
    ):
        raise HTTPException(status_code=409, detail={
            "message": "正式图纸包发行条件未满足：需通过配筋闸门、当前快照四级审签，并建立绑定当前快照的施工版修订记录。",
            "review": approval, "constructionRevisionValid": current_revision is not None,
            "diagnostics": scheme.get("diagnostics"),
        })
    path = export_formal_drawing_package(project, EXPORT_DIR, issue_mode=issue_mode, rebar_mode=rebar_mode)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")
