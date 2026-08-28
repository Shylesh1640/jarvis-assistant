"""Authentication routes (Phase 11).

* ``POST   /auth/login``     — authenticate user and create session
* ``POST   /auth/logout``    — logout (revoke session token)
* ``POST   /auth/refresh``   — refresh session token
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request, Response

from jarvis.api.errors import APIError
from jarvis.api.schemas.users import LoginRequest, LoginResponse, RefreshRequest
from jarvis.config.settings import settings
from jarvis.persistence import create_all, repos
from jarvis.security.password_hasher import verify_password
from jarvis.security.session_auth import ensure_session_context, issue_token, revoke_token, rotate_token

logger = logging.getLogger("jarvis.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_user_management() -> None:
    if not settings.user_management_enabled:
        raise APIError(
            503,
            "user_management_not_configured",
            "User management is not configured: set USER_MANAGEMENT_ENABLED=true to enable.",
        )


def _ensure_db() -> None:
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in auth route: %s", exc)


@router.post("/login")
def auth_login(payload: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Authenticate a user and create a session."""
    _require_user_management()
    _ensure_db()

    user = repos.users.get_by_email(payload.email)
    if not user:
        raise APIError(401, "invalid_credentials", "Invalid email or password.")

    if not user.is_active:
        raise APIError(403, "account_deactivated", "This account has been deactivated.")

    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Invalid email or password.")

    # Create a new session for the user
    session_id = uuid.uuid4().hex
    session_token = issue_token(session_id, user_id=user.user_id)

    # Update last login
    repos.users.update_last_login(user.user_id)

    # Set session cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=settings.jarvis_force_https,
        samesite="lax",
        max_age=settings.session_token_ttl_hours * 3600 if settings.session_token_ttl_hours > 0 else None,
    )

    # Build user response
    user_response = {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "role_id": user.role_id,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }

    return LoginResponse(user=user_response, session_id=session_id, session_token=session_token)


@router.post("/logout")
def auth_logout(
    session_id: str = "default",
    session_token: str | None = None,
    response: Response = None,
) -> dict:
    """Logout by revoking the session token."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id, session_token)

    revoke_token(session_id)

    if response:
        response.delete_cookie(key="session_token", httponly=True, samesite="lax")

    return {"logged_out": True, "session_id": session_id}


@router.post("/refresh")
def auth_refresh(payload: RefreshRequest, response: Response) -> dict:
    """Refresh a session token."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(payload.session_id, payload.session_token)

    new_token = rotate_token(payload.session_id)
    if new_token is None:
        raise APIError(404, "session_not_found", "Session not found.")

    if response:
        response.set_cookie(
            key="session_token",
            value=new_token,
            httponly=True,
            secure=settings.jarvis_force_https,
            samesite="lax",
            max_age=settings.session_token_ttl_hours * 3600 if settings.session_token_ttl_hours > 0 else None,
        )

    return {"session_id": payload.session_id, "session_token": new_token}