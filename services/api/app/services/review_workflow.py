from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.schemas.domain import ProfessionalCredential, Project, ReviewAction
from app.services.professional_credential_registry import verify_professional_credential

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _valid_signature_hash(value: Any) -> bool:
    return bool(_SHA256.fullmatch(str(value or "").strip()))


def project_snapshot_hash(project: Project) -> str:
    payload = project.model_dump(mode="json", by_alias=True, exclude={"review_workflow", "drawing_revisions", "updated_at", "messages", "monitoring_records", "calibration_runs"})
    # Review/verification records are evidence about the design snapshot and must
    # not change the snapshot they sign. Calculated engineering state remains in
    # the hash; only approval metadata and repeatable assurance summaries are removed.
    advanced = dict(payload.get("advancedEngineering") or {})
    for key in (
        "concaveTransferDetailingApproval",
        "concaveTransferDetailingApprovalHistory",
        "transferEngineeringDataAssurance",
    ):
        advanced.pop(key, None)
    payload["advancedEngineering"] = advanced
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def _active_cycle_actions(project: Project) -> list[ReviewAction]:
    actions = project.review_workflow.actions
    start = 0
    for index, item in enumerate(actions):
        if item.role == "designer" and item.action == "submit":
            start = index
    return actions[start:] if actions else []


def review_status(project: Project) -> dict[str, Any]:
    workflow = project.review_workflow
    current = project_snapshot_hash(project)
    stale = bool(workflow.approved_snapshot_hash and workflow.approved_snapshot_hash != current)
    status = "stale" if stale else workflow.status
    cycle = _active_cycle_actions(project)
    role_actors = {item.role: item.actor for item in cycle if item.action in {"submit", "accept", "approve"}}
    normalized = [actor.strip().casefold() for actor in role_actors.values() if actor.strip()]
    separated = len(normalized) == len(set(normalized))
    approver_actions = [item for item in cycle if item.role == "approver" and item.action == "approve"]
    approver_action = approver_actions[-1] if approver_actions else None
    approver_credential = verify_professional_credential(approver_action.credential if approver_action else None)
    registered_approver_valid = bool(
        approver_action
        and approver_credential.get("verified")
        and _valid_signature_hash(approver_action.digital_signature_hash)
    )
    return {
        "status": status, "currentRole": workflow.current_role, "actionCount": len(workflow.actions),
        "currentSnapshotHash": current, "approvedSnapshotHash": workflow.approved_snapshot_hash,
        "approvalValid": workflow.status == "approved" and not stale and separated,
        "registeredStructuralApproverValid": registered_approver_valid,
        "approverCredentialVerification": approver_credential,
        "requiredRoles": workflow.required_roles, "actions": [a.model_dump(mode="json", by_alias=True) for a in workflow.actions],
        "roleActors": role_actors, "separationOfDutiesValid": separated,
    }


def transition_review(
    project: Project,
    role: str,
    actor: str,
    action: str,
    comment: str | None = None,
    credential: ProfessionalCredential | dict[str, Any] | None = None,
    digital_signature_hash: str | None = None,
) -> dict[str, Any]:
    wf = project.review_workflow
    snapshot = project_snapshot_hash(project)
    actor = actor.strip() or role
    stale_approval = bool(wf.approved_snapshot_hash and wf.approved_snapshot_hash != snapshot)
    if stale_approval and role == "designer" and action == "submit":
        wf.status = "draft"
        wf.current_role = "designer"
        wf.approved_snapshot_hash = None
    if action == "reject" and not (comment or "").strip():
        raise ValueError("Reject action requires a review comment.")
    if action not in {"reopen"} and role != wf.current_role:
        raise ValueError(f"Current review role is {wf.current_role}, not {role}.")
    if role in {"checker", "reviewer", "approver"} and action in {"accept", "approve"}:
        actor_key = actor.casefold()
        for item in _active_cycle_actions(project):
            if item.action not in {"submit", "accept", "approve"} or item.role == role:
                continue
            if item.actor.strip().casefold() == actor_key:
                raise ValueError(f"Separation of duties violation: {actor} already acted as {item.role}.")
    transitions = {
        ("draft", "designer", "submit"): ("submitted", "checker"),
        ("rejected", "designer", "submit"): ("submitted", "checker"),
        ("submitted", "checker", "accept"): ("checked", "reviewer"),
        ("checked", "reviewer", "accept"): ("reviewed", "approver"),
        ("reviewed", "approver", "approve"): ("approved", "approver"),
    }
    if action == "reject" and role in {"checker", "reviewer", "approver"}:
        next_status, next_role = "rejected", "designer"
    elif action == "reopen" and role in {"designer", "checker", "reviewer", "approver"}:
        next_status, next_role = "draft", "designer"
    else:
        key = (wf.status, role, action)
        if key not in transitions:
            raise ValueError(f"Invalid review transition: {key}")
        next_status, next_role = transitions[key]
    verified_credential = None
    if credential is not None:
        verification = verify_professional_credential(credential)
        if not verification.get("verified"):
            raise ValueError(f"Professional credential verification failed: {verification.get('reason')}")
        verified_credential = ProfessionalCredential(**dict(verification["credential"]))
        if verified_credential.holder_name.strip().casefold() != actor.strip().casefold():
            raise ValueError("Review actor does not match the verified professional credential holder.")
    if role == "approver" and action == "approve":
        if verified_credential is None or verified_credential.license_type != "registered_structural_engineer":
            raise ValueError("Final approval requires a verified registered structural engineer credential.")
        if not _valid_signature_hash(digital_signature_hash):
            raise ValueError("Final approval requires a 64-character SHA-256 digital signature hash.")
    wf.actions.append(ReviewAction(
        role=role, actor=actor, action=action, comment=comment, snapshot_hash=snapshot,
        credential=verified_credential, digital_signature_hash=str(digital_signature_hash or "").strip() or None,
    ))
    wf.status = next_status
    wf.current_role = next_role
    wf.updated_at = wf.actions[-1].created_at
    if next_status == "approved":
        wf.approved_snapshot_hash = snapshot
    elif action in {"reject", "reopen"}:
        wf.approved_snapshot_hash = None
    return review_status(project)
