from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from fastapi import Request

ROLE_RANK = {
    "viewer": 10,
    "designer": 20,
    "checker": 30,
    "reviewer": 40,
    "approver": 50,
    "admin": 100,
}


@dataclass(frozen=True)
class AccessIdentity:
    actor: str
    role: str
    authenticated: bool
    key_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "role": self.role,
            "authenticated": self.authenticated,
            "keyId": self.key_id,
        }


def configured_api_keys() -> dict[str, AccessIdentity]:
    """Read API-key identities from PITGUARD_API_KEYS without exposing secrets.

    Supported forms:
      JSON: {"secret": {"role": "admin", "actor": "ops", "keyId": "ops-1"}}
      compact: secret:admin:ops;another:viewer:guest
    """
    raw = os.getenv("PITGUARD_API_KEYS", "").strip()
    if not raw:
        return {}
    identities: dict[str, AccessIdentity] = {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for index, (key, value) in enumerate(payload.items(), start=1):
            token = str(key).strip()
            if not token:
                continue
            if isinstance(value, str):
                role = value.strip().lower()
                actor = f"api-key-{index}"
                key_id = f"key-{index}"
            elif isinstance(value, dict):
                role = str(value.get("role") or "viewer").strip().lower()
                actor = str(value.get("actor") or value.get("name") or f"api-key-{index}").strip()
                key_id = str(value.get("keyId") or value.get("key_id") or f"key-{index}").strip()
            else:
                continue
            if role not in ROLE_RANK:
                continue
            identities[token] = AccessIdentity(actor=actor, role=role, authenticated=True, key_id=key_id)
        return identities
    for index, item in enumerate(raw.split(";"), start=1):
        parts = [part.strip() for part in item.split(":", 2)]
        if not parts or not parts[0]:
            continue
        token = parts[0]
        role = (parts[1] if len(parts) > 1 else "viewer").lower()
        actor = parts[2] if len(parts) > 2 and parts[2] else f"api-key-{index}"
        if role in ROLE_RANK:
            identities[token] = AccessIdentity(actor=actor, role=role, authenticated=True, key_id=f"key-{index}")
    return identities


def access_control_enabled() -> bool:
    return bool(configured_api_keys())


def extract_api_key(request: Request) -> str | None:
    direct = request.headers.get("X-PitGuard-Key")
    if direct:
        return direct.strip()
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def resolve_identity(request: Request) -> AccessIdentity | None:
    keys = configured_api_keys()
    if not keys:
        return AccessIdentity(actor="local-development", role="admin", authenticated=False, key_id="local-dev")
    token = extract_api_key(request)
    return keys.get(token or "")


def public_access_allowed(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return normalized in {"/health", "/docs", "/redoc", "/openapi.json", "/api/system/readiness"}


def required_role(method: str, path: str) -> str:
    normalized = path.rstrip("/") or "/"
    if normalized in {"/health", "/docs", "/redoc", "/openapi.json", "/api/system/readiness"}:
        return "viewer"
    if normalized.startswith("/api/system/backup"):
        return "admin"
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return "viewer"
    if "/advanced/review/" in normalized:
        return "checker"
    return "designer"


def role_allows(actual: str, required: str) -> bool:
    return ROLE_RANK.get(actual, -1) >= ROLE_RANK.get(required, 10_000)


def security_status() -> dict[str, Any]:
    identities = configured_api_keys()
    roles: dict[str, int] = {}
    for identity in identities.values():
        roles[identity.role] = roles.get(identity.role, 0) + 1
    return {
        "enabled": bool(identities),
        "mode": "api_key_rbac" if identities else "local_development_unprotected",
        "configuredIdentityCount": len(identities),
        "roleCounts": roles,
        "supportedRoles": list(ROLE_RANK),
        "header": "X-PitGuard-Key or Authorization: Bearer <key>",
        "productionRecommendation": "Set PITGUARD_API_KEYS and terminate TLS at the reverse proxy before external exposure.",
    }
