from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.services.access_control import (
    SESSION_COOKIE_NAME,
    authenticate_user,
    configured_users,
    issue_session_token,
    resolve_identity,
    security_status,
    session_ttl_seconds,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


def _secure_cookie() -> bool:
    value = os.getenv("PITGUARD_COOKIE_SECURE", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


@router.get("/status")
def auth_status(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    status = security_status()
    return {
        "loginRequired": bool(configured_users()),
        "mode": status["mode"],
        "sessionTtlSeconds": status["sessionTtlSeconds"],
    }


@router.post("/login")
def login(payload: LoginPayload, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    identity = authenticate_user(payload.username, payload.password)
    if identity is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_session_token(identity)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=session_ttl_seconds(),
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )
    return {"authenticated": True, "identity": identity.as_dict(), "expiresInSeconds": session_ttl_seconds()}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", secure=_secure_cookie(), samesite="lax")
    return {"authenticated": False}


@router.get("/me")
def me(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    identity = resolve_identity(request)
    if identity is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return {"authenticated": True, "identity": identity.as_dict()}
