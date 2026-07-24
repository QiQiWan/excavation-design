from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends

from app.services.statutory_workflow import evaluate_statutory_workflow, record_statutory_evidence
from app.storage.repository import ProjectRepository, get_repository

router = APIRouter(prefix="/api/projects/{project_id}/statutory", tags=["statutory-workflow"])


class EvidenceInput(BaseModel):
    evidence_type: str = Field(alias="evidenceType")
    artifact_id: str = Field(alias="artifactId")
    artifact_sha256: str = Field(alias="artifactSha256")
    verifier: str
    status: str = "verified"
    note: str | None = None


@router.get("")
def get_statutory_workflow(project_id: str, repo: ProjectRepository = Depends(get_repository)) -> dict:
    return evaluate_statutory_workflow(repo.require(project_id))


@router.post("/evidence")
def add_statutory_evidence(project_id: str, body: EvidenceInput, repo: ProjectRepository = Depends(get_repository)) -> dict:
    project = repo.require(project_id)
    row = record_statutory_evidence(
        project,
        evidence_type=body.evidence_type,
        artifact_id=body.artifact_id,
        artifact_sha256=body.artifact_sha256,
        verifier=body.verifier,
        status=body.status,
        note=body.note,
    )
    repo.save(project)
    return {"record": row, "workflow": evaluate_statutory_workflow(project)}
