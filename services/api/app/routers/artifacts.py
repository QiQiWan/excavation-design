from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.storage.artifact_store import ProjectArtifactStore
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects", tags=["project-artifacts"])


def _public_ref(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in ref.items()
        if key not in {"relativePath"}
    }


@router.get("/{project_id}/artifacts")
def list_project_artifacts(
    project_id: str,
    kind: str | None = Query(default=None),
    repo: ProjectRepository = Depends(get_repository),
) -> dict[str, Any]:
    if repo.revision(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    refs = repo.store.list_artifacts(project_id)
    if kind:
        refs = [item for item in refs if str(item.get("kind")) == kind]
    return {
        "projectId": project_id,
        "artifactCount": len(refs),
        "storedBytes": sum(int(item.get("storedBytes") or 0) for item in refs),
        "logicalBytes": sum(int(item.get("logicalBytes") or 0) for item in refs),
        "artifacts": [_public_ref(item) for item in refs],
    }


@router.get("/{project_id}/artifacts/{artifact_id}")
def get_project_artifact(
    project_id: str,
    artifact_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict[str, Any]:
    ref = repo.store.get_artifact(project_id, artifact_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return _public_ref(ref)


@router.get("/{project_id}/artifacts/{artifact_id}/download")
def download_project_artifact(
    project_id: str,
    artifact_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> Response:
    ref = repo.store.get_artifact(project_id, artifact_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        ProjectArtifactStore().resolve(ref)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="Artifact file is missing") from exc
    relative = str(ref.get("relativePath") or "")
    metadata = dict(ref.get("metadata") or {})
    filename = str(metadata.get("originalFilename") or f"{ref.get('kind', 'dataset')}-{artifact_id}")
    content_type = str(ref.get("contentType") or "application/octet-stream")
    content_encoding = str(ref.get("contentEncoding") or "").strip()
    headers = {
        "X-Accel-Redirect": f"/protected-artifacts/{quote(relative, safe='/')}",
        "Content-Type": content_type,
        "Content-Disposition": f'attachment; filename="{filename.replace(chr(34), "")}"',
        "Cache-Control": "private, no-store",
    }
    if content_encoding:
        headers["Content-Encoding"] = content_encoding
    return Response(
        status_code=200,
        headers=headers,
    )


@router.get("/{project_id}/calculation-results/{result_id}/stage-chunks")
def list_calculation_stage_chunks(
    project_id: str,
    result_id: str,
    repo: ProjectRepository = Depends(get_repository),
) -> dict[str, Any]:
    refs = [
        item for item in repo.store.list_artifacts(project_id)
        if str(item.get("kind")) == "calculation-stage-results"
        and str((item.get("metadata") or {}).get("resultId")) == result_id
    ]
    refs.sort(key=lambda item: int((item.get("metadata") or {}).get("chunkIndex") or 0))
    return {
        "projectId": project_id,
        "resultId": result_id,
        "chunkCount": len(refs),
        "recordCount": sum(int((item.get("metadata") or {}).get("recordCount") or 0) for item in refs),
        "chunks": [_public_ref(item) for item in refs],
    }


@router.get("/{project_id}/calculation-results/{result_id}/stage-chunks/{chunk_index}")
def get_calculation_stage_chunk(
    project_id: str,
    result_id: str,
    chunk_index: int,
    repo: ProjectRepository = Depends(get_repository),
) -> Response:
    refs = [
        item for item in repo.store.list_artifacts(project_id)
        if str(item.get("kind")) == "calculation-stage-results"
        and str((item.get("metadata") or {}).get("resultId")) == result_id
        and int((item.get("metadata") or {}).get("chunkIndex") or 0) == chunk_index
    ]
    if not refs:
        raise HTTPException(status_code=404, detail="Calculation stage chunk not found")
    values = ProjectArtifactStore().read_json(refs[0])
    # Chunk size is bounded at persistence time.  Returning one chunk avoids
    # loading the complete multi-stage result in the API process.
    return Response(
        content=json.dumps(values, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"Cache-Control": "private, no-store", "X-PitGuard-Dataset-Chunk": str(chunk_index)},
    )
