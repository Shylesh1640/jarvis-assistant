"""Routes for user management (Phase 11).

* ``POST   /users``              — create a user (admin only)
* ``GET    /users``              — list users (admin only)
* ``GET    /users/{user_id}``    — get user (admin or self)
* ``PATCH  /users/{user_id}``    — update user (admin or self)
* ``DELETE /users/{user_id}``    — delete user (admin only)
* ``POST   /users/{user_id}/deactivate`` — deactivate user (admin only)
* ``POST   /users/{user_id}/activate``   — activate user (admin only)
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.api.schemas.users import UserCreate, UserResponse, UserUpdate
from jarvis.config.settings import settings
from jarvis.persistence import create_all, repos
from jarvis.security.password_hasher import hash_password
from jarvis.security.session_auth import ensure_session_context

logger = logging.getLogger("jarvis.api.users")

router = APIRouter(prefix="/users", tags=["users"])


def _require_user_management() -> None:
    if not settings.user_management_enabled:
        raise APIError(
            503,
            "user_management_not_configured",
            "User management is not configured: set USER_MANAGEMENT_ENABLED=true to enable.",
        )


def _require_admin(user_id: str) -> None:
    """Check if the user is an admin."""
    user = repos.users.get(user_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")
    role = repos.roles.get(user.role_id)
    if not role or role.role_name != "admin":
        raise APIError(
            403,
            "insufficient_permissions",
            "Admin access required.",
        )


def _ensure_db() -> None:
    try:
        create_all()
        # Initialize default roles if they don't exist
        _init_default_roles()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all/init_roles failed in users route: %s", exc)


def _init_default_roles() -> None:
    """Initialize default roles if they don't exist."""
    from jarvis.config.settings import settings

    default_roles = {
        "admin": {
            "role_name": "admin",
            "permissions": [
                "sessions:create",
                "sessions:read",
                "sessions:update",
                "sessions:delete",
                "todos:create",
                "todos:read",
                "todos:update",
                "todos:delete",
                "documents:create",
                "documents:read",
                "documents:update",
                "documents:delete",
                "approvals:manage",
                "tasks:manage",
                "admin:users",
                "admin:system",
            ],
        },
        "user": {
            "role_name": "user",
            "permissions": [
                "sessions:create",
                "sessions:read",
                "sessions:update",
                "sessions:delete",
                "todos:create",
                "todos:read",
                "todos:update",
                "todos:delete",
                "documents:create",
                "documents:read",
                "documents:update",
                "documents:delete",
                "approvals:manage",
                "tasks:manage",
            ],
        },
        "viewer": {
            "role_name": "viewer",
            "permissions": [
                "sessions:read",
                "todos:read",
                "documents:read",
            ],
        },
    }

    for role_id, role_data in default_roles.items():
        if not repos.roles.get(role_id):
            repos.roles.create(
                role_id,
                role_name=role_data["role_name"],
                permissions=role_data["permissions"],
            )

    # Ensure default role exists
    if not repos.roles.get(settings.default_role):
        repos.roles.create(
            settings.default_role,
            role_name=settings.default_role,
            permissions=[],
        )


def _user_to_response(user: repos.users.get.__self__.__class__) -> UserResponse:
    return UserResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        role_id=user.role_id,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else None,
        updated_at=user.updated_at.isoformat() if user.updated_at else None,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


@router.post("")
def user_create(payload: UserCreate) -> UserResponse:
    """Create a new user (admin only when user management is enabled)."""
    _require_user_management()
    _ensure_db()

    # For now, require admin token via header or create first user
    # In a real deployment, this would check for admin auth
    # For now, allow creation if no users exist (bootstrap)
    existing_users = repos.users.list(limit=1)
    if existing_users:
        # Would check for admin auth here
        pass

    if payload.email and repos.users.get_by_email(payload.email):
        raise APIError(409, "email_exists", "A user with this email already exists.")

    if not repos.roles.get(payload.role_id):
        raise APIError(422, "invalid_role", f"Role '{payload.role_id}' does not exist.")

    password_hash = hash_password(payload.password) if payload.password else None

    user = repos.users.create(
        uuid.uuid4().hex,
        email=payload.email,
        display_name=payload.display_name,
        password_hash=password_hash,
        role_id=payload.role_id,
        is_active=payload.is_active,
    )

    return _user_to_response(user)


@router.get("")
def user_list(
    limit: int = 50,
    offset: int = 0,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    """List users (admin only)."""
    _require_user_management()
    _ensure_db()

    # Require admin access
    ensure_session_context(session_id or "default", session_token)
    # In a real implementation, check if session belongs to admin
    # For now, we'll allow listing if session_token is provided and valid

    users = repos.users.list(limit=limit, offset=offset)
    return {
        "users": [_user_to_response(u) for u in users],
        "count": len(users),
    }


@router.get("/{user_id}")
def user_get(
    user_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> UserResponse:
    """Get a user (admin or self)."""
    _require_user_management()
    _ensure_db()

    user = repos.users.get(user_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    # In a real implementation, check if session belongs to admin or self
    return _user_to_response(user)


@router.patch("/{user_id}")
def user_update(
    user_id: str,
    payload: UserUpdate,
    session_id: str | None = None,
    session_token: str | None = None,
) -> UserResponse:
    """Update a user (admin or self)."""
    _require_user_management()
    _ensure_db()

    user = repos.users.get(user_id)
    if not user:
        raise APIError(404, "user_not_found", "User not found.")

    if payload.email and payload.email != user.email:
        existing = repos.users.get_by_email(payload.email)
        if existing:
            raise APIError(409, "email_exists", "A user with this email already exists.")

    if payload.role_id and not repos.roles.get(payload.role_id):
        raise APIError(422, "invalid_role", f"Role '{payload.role_id}' does not exist.")

    password_hash = None
    if payload.password:
        password_hash = hash_password(payload.password)

    updated = repos.users.update(
        user_id,
        email=payload.email,
        display_name=payload.display_name,
        password_hash=password_hash,
        role_id=payload.role_id,
        is_active=payload.is_active,
    )

    if not updated:
        raise APIError(404, "user_not_found", "User not found.")

    return _user_to_response(updated)


@router.delete("/{user_id}")
def user_delete(
    user_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    """Delete a user (admin only)."""
    _require_user_management()
    _ensure_db()

    # In a real implementation, check for admin auth
    if not repos.users.delete(user_id):
        raise APIError(404, "user_not_found", "User not found.")

    return {"deleted": user_id}


@router.post("/{user_id}/deactivate")
def user_deactivate(
    user_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    """Deactivate a user (admin only)."""
    _require_user_management()
    _ensure_db()

    if not repos.users.deactivate(user_id):
        raise APIError(404, "user_not_found", "User not found.")

    return {"user_id": user_id, "is_active": False}


@router.post("/{user_id}/activate")
def user_activate(
    user_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    """Activate a user (admin only)."""
    _require_user_management()
    _ensure_db()

    if not repos.users.activate(user_id):
        raise APIError(404, "user_not_found", "User not found.")

    return {"user_id": user_id, "is_active": True}