"""Routes for conversation memory management.

Phase 5 :: Conversation memory quality — the UI-facing controls:

* ``GET /memory`` — list the stored summaries for a session.
* ``GET /memory/{id}`` — a single summary.
* ``GET /memory/export`` — download the session memory as Markdown.
* ``DELETE /memory/{id}?confirm=1`` — delete one summary (destructive, so
  a confirmation flag is required).
* ``DELETE /memory?confirm=1`` — clear all memory for a session.

All destructive operations require ``confirm=1`` to prevent accidental
deletion from a UI that renders the panel eagerly.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.memory.memory_store import (
    clear_session_memory,
    delete_memory,
    export_session_memory,
    get_memory,
    list_session_memory,
)
from jarvis.security.session_auth import ensure_session_context

logger = logging.getLogger("jarvis.api.memory")

router = APIRouter(prefix="/memory", tags=["memory"])


def _session_id(session_id: str | None, session_token: str | None) -> str:
    sid = session_id or "default"
    # Token validation is config-only; when disabled this is a no-op.
    ensure_session_context(sid, session_token)
    return sid


@router.get("")
def memory_list(session_id: str | None = None, session_token: str | None = None) -> dict:
    sid = _session_id(session_id, session_token)
    return {"session_id": sid, "items": list_session_memory(sid)}


@router.get("/export")
def memory_export(session_id: str | None = None, session_token: str | None = None) -> dict:
    sid = _session_id(session_id, session_token)
    return {"session_id": sid, "markdown": export_session_memory(sid)}


@router.get("/{summary_id}")
def memory_get(summary_id: int, session_id: str | None = None, session_token: str | None = None) -> dict:
    _session_id(session_id, session_token)
    item = get_memory(summary_id)
    if item is None:
        raise APIError(404, "memory_not_found", "Memory summary not found.")
    return item


@router.delete("/{summary_id}")
def memory_delete(
    summary_id: int,
    session_id: str | None = None,
    session_token: str | None = None,
    confirm: bool = False,
) -> dict:
    _session_id(session_id, session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to delete this memory summary.",
        )
    if not delete_memory(summary_id):
        raise APIError(404, "memory_not_found", "Memory summary not found.")
    return {"deleted": summary_id}


@router.delete("")
def memory_clear(
    session_id: str | None = None,
    session_token: str | None = None,
    confirm: bool = False,
) -> dict:
    sid = _session_id(session_id, session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to clear all memory for this session.",
        )
    removed = clear_session_memory(sid)
    return {"cleared": removed}