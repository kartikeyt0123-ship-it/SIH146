"""Login, logout and auth status."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth

router = APIRouter()


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def _client(request: Request) -> str:
    """Identify the caller for rate limiting.

    Behind a platform proxy the socket address is the proxy, so the forwarded address is
    preferred when present. This is for rate limiting only - it is never treated as
    evidence of identity.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    """Whether authentication is on, and whether this caller is signed in."""
    enabled = auth.auth_enabled()
    token = request.cookies.get(auth.COOKIE_NAME)
    demo = auth.public_demo_mode() and not enabled
    return {
        "auth_required": enabled,
        "authenticated": (not enabled) or auth.verify_token(token),
        "session_hours": round(auth.SESSION_TTL_SECONDS / 3600, 1),
        # The interface shows a standing notice in this mode. An open instance should
        # say so on every screen, not only to whoever configured it.
        "public_demo": demo,
        "public_demo_notice": (
            "Open demonstration instance. Anyone with this link can see the cases here "
            "and upload their own. Do not put real investigation data on it."
        ) if demo else None,
    }


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    if not auth.auth_enabled():
        return {"authenticated": True,
                "note": "This instance runs without authentication (local use)."}

    client = _client(request)
    if auth.rate_limited(client):
        raise HTTPException(
            status_code=429,
            detail={"code": "TOO_MANY_ATTEMPTS",
                    "message": f"Too many failed attempts. Try again in "
                               f"{auth.retry_after(client)} seconds."},
        )

    if not auth.check_password(payload.password):
        auth.record_failure(client)
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_PASSWORD", "message": "Incorrect password."},
        )

    auth.clear_failures(client)
    token = auth.issue_token()

    # Mark the cookie Secure only when the connection actually is HTTPS. Hardcoding it
    # would break every plain-HTTP deployment silently: the browser would accept the
    # cookie and then never send it back, so login would appear to succeed and the next
    # request would still be rejected. uvicorn runs with proxy_headers=True, so this
    # reads the original scheme through a platform's TLS-terminating proxy.
    https = request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https")
    response.set_cookie(
        auth.COOKIE_NAME, token, max_age=auth.SESSION_TTL_SECONDS,
        httponly=True, samesite="lax", secure=https, path="/",
    )
    return {"authenticated": True,
            "session_hours": round(auth.SESSION_TTL_SECONDS / 3600, 1)}


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"authenticated": False}
