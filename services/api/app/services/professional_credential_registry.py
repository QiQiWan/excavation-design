from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from app.schemas.domain import ProfessionalCredential


def credential_registry_path() -> Path:
    configured = str(os.getenv("PITGUARD_PROFESSIONAL_CREDENTIAL_REGISTRY") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4] / "runtime" / "security" / "verified-professional-credentials.json"


def _normalize(value: Any) -> str:
    return str(value or "").strip().casefold()


def _load_registry() -> tuple[dict[str, Any], str | None]:
    path = credential_registry_path()
    if not path.exists():
        return {"schema": "pitguard-professional-credential-registry-v1", "credentials": []}, "registry_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"schema": "pitguard-professional-credential-registry-v1", "credentials": []}, f"registry_invalid:{exc}"
    if not isinstance(payload, dict) or not isinstance(payload.get("credentials"), list):
        return {"schema": "pitguard-professional-credential-registry-v1", "credentials": []}, "registry_schema_invalid"
    return payload, None


def verify_professional_credential(claim: ProfessionalCredential | dict[str, Any] | None) -> dict[str, Any]:
    if claim is None:
        return {"status": "fail", "verified": False, "reason": "credential_missing", "registryPath": str(credential_registry_path())}
    try:
        candidate = claim if isinstance(claim, ProfessionalCredential) else ProfessionalCredential(**dict(claim))
    except Exception as exc:
        return {"status": "fail", "verified": False, "reason": f"credential_invalid:{exc}", "registryPath": str(credential_registry_path())}
    registry, registry_error = _load_registry()
    if registry_error:
        return {
            "status": "fail",
            "verified": False,
            "reason": registry_error,
            "registryPath": str(credential_registry_path()),
            "credential": candidate.model_dump(mode="json", by_alias=True),
        }
    matched: dict[str, Any] | None = None
    for row in registry.get("credentials", []):
        if not isinstance(row, dict):
            continue
        if (
            _normalize(row.get("licenseType")) == _normalize(candidate.license_type)
            and _normalize(row.get("licenseNumber")) == _normalize(candidate.license_number)
            and _normalize(row.get("holderName")) == _normalize(candidate.holder_name)
            and _normalize(row.get("jurisdiction") or "CN") == _normalize(candidate.jurisdiction or "CN")
        ):
            matched = row
            break
    if not matched:
        return {
            "status": "fail",
            "verified": False,
            "reason": "credential_not_found_in_trusted_registry",
            "registryPath": str(credential_registry_path()),
            "credential": candidate.model_dump(mode="json", by_alias=True),
        }
    valid_until = str(matched.get("validUntil") or "").strip()
    expired = False
    if valid_until:
        try:
            expired = date.fromisoformat(valid_until[:10]) < date.today()
        except ValueError:
            return {
                "status": "fail", "verified": False, "reason": "registry_valid_until_invalid",
                "registryPath": str(credential_registry_path()),
            }
    verified = str(matched.get("status") or "").strip().lower() == "verified" and not expired
    trusted = ProfessionalCredential(
        licenseType=candidate.license_type,
        licenseNumber=candidate.license_number,
        holderName=candidate.holder_name,
        jurisdiction=candidate.jurisdiction,
        organization=matched.get("organization") or candidate.organization,
        validUntil=valid_until or candidate.valid_until,
        verified=verified,
        verificationSource=matched.get("verificationSource") or "trusted-local-registry",
        verificationReference=matched.get("verificationReference") or matched.get("registryRecordId"),
    )
    return {
        "status": "pass" if verified else "fail",
        "verified": verified,
        "reason": None if verified else ("credential_expired" if expired else "registry_record_not_verified"),
        "registryPath": str(credential_registry_path()),
        "registryRecordId": matched.get("registryRecordId"),
        "credential": trusted.model_dump(mode="json", by_alias=True),
    }
