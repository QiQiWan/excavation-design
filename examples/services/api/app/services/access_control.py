from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
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

SESSION_COOKIE_NAME = "pitguard_session"
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 240_000


@dataclass(frozen=True)
class AccessIdentity:
    actor: str
    role: str
    authenticated: bool
    key_id: str | None = None
    username: str | None = None
    auth_mode: str = "api_key"

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "role": self.role,
            "authenticated": self.authenticated,
            "keyId": self.key_id,
            "username": self.username,
            "authMode": self.auth_mode,
        }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("Password cannot be empty")
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = _b64url_decode(salt_raw)
        expected = _b64url_decode(digest_raw)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def configured_users() -> dict[str, dict[str, str]]:
    raw = os.getenv("PITGUARD_USERS", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    users: dict[str, dict[str, str]] = {}
    for username, value in payload.items():
        if not isinstance(value, dict):
            continue
        normalized = str(username).strip()
        role = str(value.get("role") or "viewer").strip().lower()
        password_hash = str(value.get("passwordHash") or value.get("password_hash") or "").strip()
        if not normalized or role not in ROLE_RANK or not password_hash:
            continue
        users[normalized] = {
            "passwordHash": password_hash,
            "role": role,
            "actor": str(value.get("actor") or normalized).strip(),
            "userId": str(value.get("userId") or value.get("user_id") or normalized).strip(),
        }
    return users


def authenticate_user(username: str, password: str) -> AccessIdentity | None:
    users = configured_users()
    record = users.get(username.strip())
    if not record or not verify_password(password, record["passwordHash"]):
        # Keep timing broadly similar for missing users.
        if not record:
            verify_password(password, hash_password("invalid-user-dummy-password", iterations=20_000, salt=b"pitguard-dummy-salt"))
        return None
    return AccessIdentity(
        actor=record["actor"],
        role=record["role"],
        authenticated=True,
        key_id=record["userId"],
        username=username.strip(),
        auth_mode="session",
    )


def _session_secret() -> bytes:
    secret = os.getenv("PITGUARD_SESSION_SECRET", "").strip()
    if not secret:
        # Only used in local-development mode. Production readiness reports the
        # missing secret when users are configured.
        secret = "pitguard-local-development-session-secret"
    return secret.encode("utf-8")


def session_ttl_seconds() -> int:
    try:
        return max(900, min(7 * 24 * 60 * 60, int(os.getenv("PITGUARD_SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS)))))
    except ValueError:
        return DEFAULT_SESSION_TTL_SECONDS


def issue_session_token(identity: AccessIdentity) -> str:
    now = int(time.time())
    payload = {
        "sub": identity.username or identity.actor,
        "actor": identity.actor,
        "role": identity.role,
        "uid": identity.key_id,
        "iat": now,
        "exp": now + session_ttl_seconds(),
        "nonce": secrets.token_hex(8),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = _b64url_encode(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def identity_from_session_token(token: str | None) -> AccessIdentity | None:
    if not token or "." not in token:
        return None
    body, signature = token.split(".", 1)
    expected = _b64url_encode(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        role = str(payload.get("role") or "").lower()
        if role not in ROLE_RANK:
            return None
        return AccessIdentity(
            actor=str(payload.get("actor") or payload.get("sub") or "session-user"),
            role=role,
            authenticated=True,
            key_id=str(payload.get("uid") or payload.get("sub") or "session-user"),
            username=str(payload.get("sub") or ""),
            auth_mode="session",
        )
    except Exception:
        return None


def configured_api_keys() -> dict[str, AccessIdentity]:
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
            identities[token] = AccessIdentity(actor=actor, role=role, authenticated=True, key_id=key_id, auth_mode="api_key")
        return identities
    for index, item in enumerate(raw.split(";"), start=1):
        parts = [part.strip() for part in item.split(":", 2)]
        if not parts or not parts[0]:
            continue
        token = parts[0]
        role = (parts[1] if len(parts) > 1 else "viewer").lower()
        actor = parts[2] if len(parts) > 2 and parts[2] else f"api-key-{index}"
        if role in ROLE_RANK:
            identities[token] = AccessIdentity(actor=actor, role=role, authenticated=True, key_id=f"key-{index}", auth_mode="api_key")
    return identities


def access_control_enabled() -> bool:
    return bool(configured_api_keys() or configured_users())


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
    users = configured_users()
    if not keys and not users:
        return AccessIdentity(actor="local-development", role="admin", authenticated=False, key_id="local-dev", auth_mode="local")
    token = extract_api_key(request)
    if token and token in keys:
        return keys[token]
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        return identity_from_session_token(session_token)
    return None


def public_access_allowed(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return normalized in {
        "/health", "/health/live", "/health/ready",
        "/api/auth/login", "/api/auth/logout", "/api/auth/status",
    }


def required_role(method: str, path: str) -> str:
    normalized = path.rstrip("/") or "/"
    if public_access_allowed(normalized):
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
    users = configured_users()
    roles: dict[str, int] = {}
    for identity in identities.values():
        roles[identity.role] = roles.get(identity.role, 0) + 1
    for user in users.values():
        roles[user["role"]] = roles.get(user["role"], 0) + 1
    enabled = bool(identities or users)
    session_secret_configured = bool(os.getenv("PITGUARD_SESSION_SECRET", "").strip())
    return {
        "enabled": enabled,
        "mode": "session_login_and_api_key_rbac" if users else ("api_key_rbac" if identities else "local_development_unprotected"),
        "configuredIdentityCount": len(identities) + len(users),
        "configuredUserCount": len(users),
        "configuredApiKeyCount": len(identities),
        "roleCounts": roles,
        "supportedRoles": list(ROLE_RANK),
        "sessionCookie": SESSION_COOKIE_NAME,
        "sessionTtlSeconds": session_ttl_seconds(),
        "sessionSecretConfigured": session_secret_configured,
        "productionReady": bool(users and session_secret_configured),
        "productionRecommendation": "Configure PITGUARD_USERS and PITGUARD_SESSION_SECRET; keep API keys only for automation integrations.",
    }
