"""Routes for role management (Phase 11).

* ``POST   /roles``              — create a role (admin only)
* ``GET    /roles``              — list roles (admin only)
* ``GET    /roles/{role_id}``    — get role (admin only)
* ``PATCH  /roles/{role_id}``    — update role (admin only)
* ``DELETE /roles/{role_id}``    — delete role (admin only)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.api.schemas.users import RoleCreate, RoleResponse, RoleUpdate
from jarvis.config.settings import settings
from jarvis.persistence import create_all, repos
from jarvis.security.session_auth import ensure_session_context

logger = logging.getLogger("jarvis.api.roles")

router = APIRouter(prefix="/roles", tags=["roles"])


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
        logger.debug("create_all failed in roles route: %s", exc)


def _role_to_response(role: repos.roles.get.__self__.__class__) -> RoleResponse:
    return RoleResponse(
        role_id=role.role_id,
        role_name=role.role_name,
        permissions=list(role.permissions or []),
        created_at=role.created_at.isoformat() if role.created_at else None,
        updated_at=role.updated_at.isoformat() if role.updated_at else None,
    )


@router.post("")
def role_create(payload: RoleCreate) -> RoleResponse:
    """Create a new role (admin only)."""
    _require_user_management()
    _ensure_db()

    if repos.roles.get(payload.role_id):
        raise APIError(409, "role_exists", f"Role '{payload.role_id}' already exists.")

    role = repos.roles.create(
        payload.role_id,
        role_name=payload.role_name,
        permissions=payload.permissions,
    )

    return _role_to_response(role)


@router.get("")
def role_list(
    limit: int = 100,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    """List roles (admin only)."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id or "default", session_token)

    roles = repos.roles.list(limit=limit)
    return {"roles": [_role_to_response(r) for r in roles], "count": len(roles)}


@router.get("/{role_id}")
def role_get(
    role_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> RoleResponse:
    """Get a role (admin only)."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id or "default", session_token)

    role = repos.roles.get(role_id)
    if not role:
        raise APIError(404, "role_not_found", "Role not found.")

    return _role_to_response(role)


@router.patch("/{role_id}")
def role_update(
    role_id: str,
    payload: RoleUpdate,
    session_id: str | None = None,
    session_token: str | None = None,
) -> RoleResponse:
    """Update a role (admin only)."""
    _require_user_management()
    _ensure_db()

    ensure_session_context(session_id or "default", session_token)

    if payload.role_name and repos.roles.get_by_name(payload.role_name):
        raise APIError(409, "role_name_exists", f"Role name '{payload.role_name}' already exists.")

    updated = repos.roles.update(
        role_id,
        role_name=payload.role_name,
        permissions=payload.permissions,
    )

    if not updated:
        raise APIError(404, "role_not_found", "Role not found.")

    return _role_to_response(updated)


@router.delete("/{role_id}")
def role_delete(
    role_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    """Delete a role (admin only)."""
    _require_user_management()
    _ensure_db()

    if role_id in ("admin", "user", "viewer"):
        raise APIError(400, "cannot_delete_default_role", "Cannot delete default roles.")

    if not repos.roles.delete(role_id):
        raise APIError(404, "role_not_found", "Role not found.")

    return {"deleted": role_id}