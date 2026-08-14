"""Session endpoints: durable session metadata + bearer tokens."""
from __future__ import annotations

from fastapi import APIRouter, Query

from jarvis.api.errors import APIError
from jarvis.persistence import repos
from jarvis.security.session_auth import issue_token

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}/token")
def session_token(session_id: str) -> dict:
    """Create the session (if new) and return its bearer token.

    The token is stable for the life of the session and must be sent as
    ``session_token`` on /chat and /tasks requests when
    ``REQUIRE_SESSION_TOKEN=true``.
    """
    token = issue_token(session_id)
    return {"session_id": session_id, "session_token": token}


@router.get("/{session_id}")
def session_info(session_id: str) -> dict:
    row = repos.sessions.get(session_id)
    if row is None:
        raise APIError(404, "session_not_found", "Session not found.")
    return {
        "session_id": row.id,
        "user_id": row.user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_active_at": row.last_active_at.isoformat() if row.last_active_at else None,
        "has_token": bool(row.token),
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
            }
            for r in rows
        ]
    }