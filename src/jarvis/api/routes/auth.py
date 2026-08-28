"""Authentication routes (Phase 11/12).

* ``POST   /auth/login``     — authenticate user and create session
* ``POST   /auth/logout``    — logout (revoke session token)
* ``POST   /auth/refresh``   — refresh session token
* ``POST   /auth/2fa/enroll``       — start 2FA enrollment
* ``POST   /auth/2fa/verify-enrollment`` — verify 2FA enrollment
* ``POST   /auth/2fa/disable``      — disable 2FA
* ``POST   /auth/2fa/regenerate-recovery-codes`` — regenerate recovery codes
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request, Response

from jarvis.api.errors import APIError
from jarvis.api.schemas.users import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    TwoFAEnrollResponse,
    TwoFAVerifyEnrollRequest,
    TwoFADisableRequest,
    TwoFARegenerateRecoveryRequest,
)
from jarvis.config.settings import settings
from jarvis.persistence import create_all, repos
from jarvis.security.password_hasher import verify_password
from jarvis.security.session_auth import ensure_session_context, issue_token, revoke_token, rotate_token
from jarvis.security.two_factor import (
    decrypt_2fa_secret,
    encrypt_2fa_secret,
    generate_recovery_codes,
    generate_totp_secret,
    get_totp_uri,
    hash_recovery_code,
    verify_totp,
)

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
    """Authenticate a user and create a session.

    If the user has 2FA enabled, returns requires_2fa=True and a temporary
    session for the 2FA verification step. Client must then call
    /auth/2fa/verify-login with the TOTP code.
    """
    _require_user_management()
    _ensure_db()

    user = repos.users.get_by_email(payload.email)
    if not user:
        raise APIError(401, "invalid_credentials", "Invalid email or password.")

    if not user.is_active:
        raise APIError(403, "account_deactivated", "This account has been deactivated.")

    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Invalid email or password.")

    # Check if 2FA is enabled and required
    if user.totp_enabled:
        # Check if we have a valid remember-device token
        if payload.remember_device:
            # Note: remember-device logic would go here
            pass

        # Create a temporary session for 2FA verification
        temp_session_id = f"2fa_{uuid.uuid4().hex}"
        temp_token = issue_token(temp_session_id, user_id=user.user_id)

        # Store minimal user info in the temp session for 2FA verification
        repos.sessions.get_or_create(temp_session_id, user_id=user.user_id)

        # Update last login
        repos.users.update_last_login(user.user_id)

        user_response = {
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "role_id": user.role_id,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "totp_enabled": True,
        }

        return LoginResponse(
            user=user_response,
            session_id=temp_session_id,
            session_token=temp_token,
            requires_2fa=True,
        )

    # No 2FA required - create full session
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
        "totp_enabled": False,
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


# ---------------------------------------------------------------------------
# 2FA Routes
# ---------------------------------------------------------------------------


@router.post("/2fa/enroll", response_model=TwoFAEnrollResponse)
def auth_2fa_enroll(
    session_id: str = "default",
    session_token: str | None = None,
) -> TwoFAEnrollResponse:
    """Start 2FA enrollment by generating a secret and QR code."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id, session_token)

    user = repos.users.get(session_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    if user.totp_enabled:
        raise APIError(400, "2fa_already_enabled", "2FA is already enabled for this account.")

    # Generate new secret and recovery codes
    secret = generate_totp_secret()
    recovery_codes = generate_recovery_codes(settings.two_factor_recovery_codes_count)

    # Store encrypted secret and hashed recovery codes (but not enabled yet)
    encrypted_secret = encrypt_2fa_secret(secret)
    hashed_recovery = [hash_recovery_code(code) for code in recovery_codes]

    repos.users.update(
        session_id,
        totp_secret=encrypted_secret,
        recovery_codes=hashed_recovery,
    )

    qr_uri = get_totp_uri(secret, user.email or user.user_id)

    return TwoFAEnrollResponse(
        secret=secret,
        qr_code_uri=qr_uri,
        recovery_codes=recovery_codes,
    )


@router.post("/2fa/verify-enrollment")
def auth_2fa_verify_enrollment(
    payload: TwoFAVerifyEnrollRequest,
    session_id: str = "default",
    session_token: str | None = None,
) -> dict:
    """Verify the first TOTP code to complete 2FA enrollment."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id, session_token)

    user = repos.users.get(session_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    if not user.totp_secret:
        raise APIError(400, "enrollment_not_started", "2FA enrollment not started.")

    # Verify the code
    try:
        secret = decrypt_2fa_secret(user.totp_secret)
    except Exception as exc:  # noqa: BLE001
        raise APIError(500, "secret_decrypt_failed", "Failed to decrypt 2FA secret.") from exc

    if not verify_totp(secret, payload.code):
        raise APIError(400, "invalid_code", "Invalid 2FA code.")

    # Enable 2FA
    repos.users.update(
        session_id,
        totp_enabled=True,
        last_totp_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    return {"verified": True, "message": "2FA enabled successfully."}


@router.post("/2fa/disable")
def auth_2fa_disable(
    payload: TwoFADisableRequest,
    session_id: str = "default",
    session_token: str | None = None,
) -> dict:
    """Disable 2FA for the account."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id, session_token)

    user = repos.users.get(session_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    if not user.totp_enabled:
        raise APIError(400, "2fa_not_enabled", "2FA is not enabled for this account.")

    # Verify password
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Invalid password.")

    # Verify 2FA code
    if payload.code:
        try:
            secret = decrypt_2fa_secret(user.totp_secret)
        except Exception as exc:  # noqa: BLE001
            raise APIError(500, "secret_decrypt_failed", "Failed to decrypt 2FA secret.") from exc

        if not verify_totp(secret, payload.code):
            raise APIError(400, "invalid_code", "Invalid 2FA code.")

    # Disable 2FA
    repos.users.update(
        session_id,
        totp_enabled=False,
        totp_secret=None,
        recovery_codes=[],
        last_totp_at=None,
    )

    return {"disabled": True, "message": "2FA disabled successfully."}


@router.post("/2fa/regenerate-recovery-codes")
def auth_2fa_regenerate_recovery_codes(
    payload: TwoFARegenerateRecoveryRequest,
    session_id: str = "default",
    session_token: str | None = None,
) -> dict:
    """Regenerate recovery codes (invalidates old ones)."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id, session_token)

    user = repos.users.get(session_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    if not user.totp_enabled:
        raise APIError(400, "2fa_not_enabled", "2FA is not enabled for this account.")

    # Verify password
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise APIError(401, "invalid_credentials", "Invalid password.")

    # Verify 2FA code
    try:
        secret = decrypt_2fa_secret(user.totp_secret)
    except Exception as exc:  # noqa: BLE001
        raise APIError(500, "secret_decrypt_failed", "Failed to decrypt 2FA secret.") from exc

    if not verify_totp(secret, payload.code):
        raise APIError(400, "invalid_code", "Invalid 2FA code.")

    # Generate new recovery codes
    recovery_codes = generate_recovery_codes(settings.two_factor_recovery_codes_count)
    hashed_recovery = [hash_recovery_code(code) for code in recovery_codes]

    repos.users.update(
        session_id,
        recovery_codes=hashed_recovery,
    )

    return {
        "regenerated": True,
        "recovery_codes": recovery_codes,
        "message": "Recovery codes regenerated. Old codes are now invalid.",
    }


@router.post("/2fa/verify-login")
def auth_2fa_verify_login(
    payload: TwoFAVerifyEnrollRequest,  # Reuse the same schema (code field)
    session_id: str = "default",
    session_token: str | None = None,
) -> LoginResponse:
    """Verify 2FA code during login (second step after password)."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id, session_token)

    user = repos.users.get(session_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    if not user.totp_enabled:
        raise APIError(400, "2fa_not_enabled", "2FA is not enabled for this account.")

    # Verify the TOTP code
    try:
        secret = decrypt_2fa_secret(user.totp_secret)
    except Exception as exc:  # noqa: BLE001
        raise APIError(500, "secret_decrypt_failed", "Failed to decrypt 2FA secret.") from exc

    if not verify_totp(secret, payload.code):
        raise APIError(400, "invalid_code", "Invalid 2FA code.")

    # Update last TOTP timestamp
    repos.users.update(
        session_id,
        last_totp_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )

    # Create full session
    session_id = uuid.uuid4().hex
    session_token = issue_token(session_id, user_id=user.user_id)
    repos.users.update_last_login(user.user_id)

    user_response = {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "role_id": user.role_id,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "totp_enabled": user.totp_enabled,
    }

    return LoginResponse(user=user_response, session_id=session_id, session_token=session_token, requires_2fa=False)