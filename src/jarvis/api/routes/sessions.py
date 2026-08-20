"""Session endpoints: durable session metadata + bearer tokens."""
from __future__ import annotations

from fastapi import APIRouter, Query

from jarvis.api.errors import APIError
from jarvis.persistence import repos
from jarvis.security.session_auth import issue_token, revoke_token, rotate_token

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}/token")
def session_token(session_id: str) -> dict:
    """Create the session (if new) and return its bearer token.

    The token must be sent as ``session_token`` on /chat and /tasks
    requests when ``REQUIRE_SESSION_TOKEN=true``.

    Security note: tokens are stored *hashed* at rest and the plaintext is
    kept only in the backend's in-memory issuance cache, so this endpoint
    returns the same token for the life of the process. After a backend
    restart the token is rotated (fetch once, then keep it safe). Use
    ``POST /sessions/{id}/rotate-token`` to rotate on demand and
    ``POST /sessions/{id}/revoke`` to invalidate immediately.
    """
    token = issue_token(session_id)
    return {"session_id": session_id, "session_token": token}


@router.post("/{session_id}/rotate-token")
def rotate_session_token(session_id: str) -> dict:
    """Force a new token; the previous token stops validating immediately."""
    token = rotate_token(session_id)
    if token is None:
        raise APIError(404, "session_not_found", "Session not found.")
    return {"session_id": session_id, "session_token": token}


@router.post("/{session_id}/revoke")
def revoke_session_token(session_id: str) -> dict:
    """Revoke the session's token; it stops validating immediately."""
    if not revoke_token(session_id):
        raise APIError(404, "session_not_found", "Session not found.")
    return {"session_id": session_id, "revoked": True}


@router.get("/{session_id}")
def session_info(session_id: str) -> dict:
    row = repos.sessions.get(session_id)
    if row is None:
        raise APIError(404, "session_not_found", "Session not found.")
    status = repos.sessions.token_status(session_id) or {}
    return {
        "session_id": row.id,
        "user_id": row.user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_active_at": row.last_active_at.isoformat() if row.last_active_at else None,
        "has_token": bool(status.get("has_token")),
        "token_hash_scheme": status.get("hash_scheme"),
        "token_created_at": status.get("created_at"),
        "token_expires_at": status.get("expires_at"),
        "token_rotated_at": status.get("rotated_at"),
        "token_revoked_at": status.get("revoked_at"),
        "token_expired": bool(status.get("expired")),
        "token_rotation_due": bool(status.get("rotation_due")),
        "message_count": repos.sessions.message_count(session_id),
    }


@router.get("")
def list_sessions(limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = repos.sessions.list(limit=limit)
    return {
        "sessions": [
            {
                "session_id": r.id,
                "user_id": r.user_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "last_active_at": r.last_active_at.isoformat() if r.last_active_at else None,
                "message_count": repos.sessions.message_count(r.id),
            }
            for r in rows
        ]
    }