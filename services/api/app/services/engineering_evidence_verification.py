from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Literal

from app.schemas.domain import Borehole, ConstructionStage, GroundwaterRecord, ProfessionalCredential, Project
from app.services.professional_credential_registry import verify_professional_credential
from app.storage.artifact_store import ProjectArtifactStore, append_project_artifact_ref

EvidenceDomain = Literal["borehole", "groundwater", "construction_stage"]
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")

_ALLOWED_LICENSES: dict[str, set[str]] = {
    "borehole": {"registered_geotechnical_engineer", "registered_civil_engineer"},
    "groundwater": {"registered_geotechnical_engineer", "registered_civil_engineer"},
    "construction_stage": {"registered_structural_engineer", "registered_civil_engineer"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_sha256(value: Any) -> bool:
    return bool(_SHA256.fullmatch(str(value or "").strip()))


def valid_signature_hash(value: Any) -> bool:
    return valid_sha256(value)


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _object_payload(domain: EvidenceDomain, item: Borehole | GroundwaterRecord | ConstructionStage) -> dict[str, Any]:
    payload = item.model_dump(mode="json", by_alias=True)
    if domain == "borehole":
        payload.pop("sourceVerified", None)
        # Groundwater records have their own evidence object and authorized
        # verification cycle. Their later approval must not invalidate the
        # investigation geometry/layer signature of the parent borehole.
        payload.pop("waterLevels", None)
    elif domain == "groundwater":
        payload.pop("quality", None)
        payload.pop("verifiedBy", None)
    else:
        payload.pop("approvedBy", None)
        payload.pop("approvedAt", None)
        payload.pop("dataStatus", None)
    return payload


def engineering_evidence_object_hash(domain: EvidenceDomain, item: Borehole | GroundwaterRecord | ConstructionStage) -> str:
    return _canonical_hash(_object_payload(domain, item))


def _iter_domain_objects(project: Project, domain: EvidenceDomain) -> Iterable[tuple[str, Any]]:
    if domain == "borehole":
        for item in project.boreholes:
            yield item.id, item
        return
    if domain == "groundwater":
        for borehole in project.boreholes:
            for record in borehole.water_levels:
                yield record.id, record
        return
    for case in project.calculation_cases:
        for stage in case.stages:
            yield stage.id, stage


def _lookup_objects(project: Project, domain: EvidenceDomain, object_ids: list[str]) -> list[Any]:
    wanted = {str(item).strip() for item in object_ids if str(item).strip()}
    objects = [item for object_id, item in _iter_domain_objects(project, domain) if object_id in wanted]
    missing = sorted(wanted - {item.id for item in objects})
    if missing:
        raise ValueError(f"Engineering evidence objects not found: {', '.join(missing)}")
    if not objects:
        raise ValueError("At least one engineering evidence object is required.")
    return objects


def _artifact_index(project: Project) -> dict[str, dict[str, Any]]:
    storage = dict((project.advanced_engineering or {}).get("artifactStorage") or {})
    return {
        str(item.get("artifactId")): dict(item)
        for item in list(storage.get("artifacts") or [])
        if isinstance(item, dict) and item.get("artifactId")
    }


def source_artifact_current(project: Project, artifact_id: str | None, source_sha256: str | None) -> bool:
    if not artifact_id or not valid_sha256(source_sha256):
        return False
    row = _artifact_index(project).get(str(artifact_id))
    if not row or str(row.get("sha256") or "").casefold() != str(source_sha256).casefold():
        return False
    try:
        ProjectArtifactStore().resolve(row)
    except FileNotFoundError:
        return False
    return True


def attach_engineering_evidence(
    project: Project,
    *,
    domain: EvidenceDomain,
    object_ids: list[str],
    filename: str,
    content: bytes,
    content_type: str | None = None,
    revision: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    if domain not in _ALLOWED_LICENSES:
        raise ValueError(f"Unsupported engineering evidence domain: {domain}")
    raw = bytes(content)
    if not raw:
        raise ValueError("Engineering evidence file is empty.")
    if len(raw) > 100 * 1024 * 1024:
        raise ValueError("Engineering evidence file exceeds the 100 MB limit.")
    objects = _lookup_objects(project, domain, object_ids)
    ref = ProjectArtifactStore().write_bytes(
        project.id,
        "engineering-source-evidence",
        raw,
        filename=filename,
        content_type=content_type,
        metadata={"domain": domain, "objectIds": [item.id for item in objects], "revision": revision},
    )
    append_project_artifact_ref(
        project,
        ref,
        storage_key=f"engineering-evidence:{domain}:{ref['sha256']}:{','.join(sorted(item.id for item in objects))}",
    )
    for item in objects:
        if domain == "borehole":
            item.source_file = filename
            item.source_file_sha256 = ref["sha256"]
            item.source_artifact_id = ref["artifactId"]
            item.source_document_revision = revision
            item.source_verified = False
        elif domain == "groundwater":
            item.source_file = filename
            item.source_file_sha256 = ref["sha256"]
            item.source_artifact_id = ref["artifactId"]
            if observed_at:
                item.observed_at = observed_at
            item.quality = "provisional"
            item.verified_by = None
        else:
            item.source_document = filename
            item.source_document_sha256 = ref["sha256"]
            item.source_artifact_id = ref["artifactId"]
            item.approved_by = None
            item.approved_at = None
            item.data_status = "draft"
    advanced = dict(project.advanced_engineering or {})
    history = list(advanced.get("engineeringEvidenceAttachmentHistory") or [])
    history.append({
        "domain": domain,
        "objectIds": [item.id for item in objects],
        "artifactId": ref["artifactId"],
        "sha256": ref["sha256"],
        "filename": filename,
        "revision": revision,
        "attachedAt": _now(),
    })
    advanced["engineeringEvidenceAttachmentHistory"] = history[-200:]
    project.advanced_engineering = advanced
    return {
        "status": "attached",
        "domain": domain,
        "objectIds": [item.id for item in objects],
        "artifact": {key: value for key, value in ref.items() if key != "relativePath"},
        "verificationRequired": True,
    }


def _source_metadata_complete(project: Project, domain: EvidenceDomain, item: Any) -> bool:
    if domain == "borehole":
        return bool(
            item.source_file
            and valid_sha256(item.source_file_sha256)
            and source_artifact_current(project, item.source_artifact_id, item.source_file_sha256)
        )
    if domain == "groundwater":
        return bool(
            item.source_file
            and valid_sha256(item.source_file_sha256)
            and item.observed_at
            and source_artifact_current(project, item.source_artifact_id, item.source_file_sha256)
        )
    return bool(
        item.source_document
        and valid_sha256(item.source_document_sha256)
        and source_artifact_current(project, item.source_artifact_id, item.source_document_sha256)
    )


def verify_engineering_evidence(
    project: Project,
    *,
    domain: EvidenceDomain,
    object_ids: list[str],
    actor: str,
    credential: ProfessionalCredential | dict[str, Any] | None,
    digital_signature_hash: str,
) -> dict[str, Any]:
    if domain not in _ALLOWED_LICENSES:
        raise ValueError(f"Unsupported engineering evidence domain: {domain}")
    actor = str(actor or "").strip()
    if not actor:
        raise ValueError("Engineering evidence verification requires an actor.")
    if not valid_signature_hash(digital_signature_hash):
        raise ValueError("Engineering evidence verification requires a 64-character SHA-256 signature hash.")
    verification = verify_professional_credential(credential)
    if not verification.get("verified"):
        raise ValueError("Professional credential verification failed: " + str(verification.get("reason") or "unknown"))
    trusted = ProfessionalCredential(**dict(verification["credential"]))
    if trusted.license_type not in _ALLOWED_LICENSES[domain]:
        raise ValueError(f"Credential type {trusted.license_type} is not authorized for {domain} evidence.")
    if trusted.holder_name.strip().casefold() != actor.casefold():
        raise ValueError("Evidence verifier does not match the trusted credential holder.")
    objects = _lookup_objects(project, domain, object_ids)
    incomplete = [item.id for item in objects if not _source_metadata_complete(project, domain, item)]
    if incomplete:
        raise ValueError("Evidence source file, immutable artifact, SHA-256 or timestamp is incomplete for: " + ", ".join(incomplete))
    verified_at = _now()
    records: list[dict[str, Any]] = []
    for item in objects:
        if domain == "borehole":
            item.source_verified = True
            source_sha = item.source_file_sha256
            artifact_id = item.source_artifact_id
        elif domain == "groundwater":
            item.quality = "verified"
            item.verified_by = actor
            source_sha = item.source_file_sha256
            artifact_id = item.source_artifact_id
        else:
            item.data_status = "verified"
            item.approved_by = actor
            item.approved_at = verified_at
            source_sha = item.source_document_sha256
            artifact_id = item.source_artifact_id
        records.append({
            "domain": domain,
            "objectId": item.id,
            "objectHash": engineering_evidence_object_hash(domain, item),
            "sourceArtifactId": artifact_id,
            "sourceSha256": source_sha,
            "actor": actor,
            "professionalCredential": trusted.model_dump(mode="json", by_alias=True),
            "digitalSignatureHash": digital_signature_hash.lower(),
            "verifiedAt": verified_at,
        })
    advanced = dict(project.advanced_engineering or {})
    history = list(advanced.get("engineeringEvidenceVerificationHistory") or [])
    history.extend(records)
    advanced["engineeringEvidenceVerificationHistory"] = history[-500:]
    project.advanced_engineering = advanced
    return {
        "status": "pass",
        "domain": domain,
        "verifiedObjectCount": len(records),
        "objectIds": [row["objectId"] for row in records],
        "credential": {
            "licenseType": trusted.license_type,
            "holderName": trusted.holder_name,
            "licenseNumberMasked": "***" + trusted.license_number[-4:],
            "verificationReference": trusted.verification_reference,
        },
        "verifiedAt": verified_at,
    }


def engineering_evidence_verification_status(
    project: Project,
    domain: EvidenceDomain,
    item: Borehole | GroundwaterRecord | ConstructionStage,
) -> dict[str, Any]:
    current_hash = engineering_evidence_object_hash(domain, item)
    history = list((project.advanced_engineering or {}).get("engineeringEvidenceVerificationHistory") or [])
    matches = [
        dict(row) for row in history
        if isinstance(row, dict)
        and row.get("domain") == domain
        and row.get("objectId") == item.id
        and row.get("objectHash") == current_hash
    ]
    row = matches[-1] if matches else None
    if not row:
        return {"status": "missing", "verified": False, "objectHash": current_hash, "reason": "current_verification_record_missing"}
    credential = verify_professional_credential(dict(row.get("professionalCredential") or {}))
    signature_valid = valid_signature_hash(row.get("digitalSignatureHash"))
    source_current = source_artifact_current(project, row.get("sourceArtifactId"), row.get("sourceSha256"))
    verified = bool(credential.get("verified") and signature_valid and source_current)
    return {
        "status": "pass" if verified else "fail",
        "verified": verified,
        "objectHash": current_hash,
        "verifiedAt": row.get("verifiedAt"),
        "actor": row.get("actor"),
        "credentialVerification": credential,
        "signatureValid": signature_valid,
        "sourceArtifactCurrent": source_current,
        "reason": None if verified else "credential_signature_or_source_artifact_invalid",
    }


def project_engineering_evidence_summary(project: Project) -> dict[str, Any]:
    domains: dict[str, Any] = {}
    for domain in ("borehole", "groundwater", "construction_stage"):
        rows = []
        for object_id, item in _iter_domain_objects(project, domain):
            status = engineering_evidence_verification_status(project, domain, item)
            rows.append({"objectId": object_id, **status})
        domains[domain] = {
            "objectCount": len(rows),
            "verifiedCount": sum(bool(row.get("verified")) for row in rows),
            "allVerified": bool(rows) and all(bool(row.get("verified")) for row in rows),
            "objects": rows,
        }
    return {
        "schema": "pitguard-engineering-evidence-verification-summary-v1",
        "status": "pass" if all(value["allVerified"] for value in domains.values()) else "blocked",
        "domains": domains,
    }
