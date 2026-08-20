"""Routes for local tasks & reminders (Phase 8).

* ``POST   /todos``              — create a todo for a session
* ``GET    /todos``              — list (session-scoped) with filters
* ``GET    /todos/{todo_id}``    — one todo
* ``PATCH  /todos/{todo_id}``    — update title/description/priority/due/status
* ``DELETE /todos/{todo_id}``    — soft delete (reversible; no confirm needed)
* ``POST   /todos/{todo_id}/complete`` — mark completed (stamps completed_at)

Session isolation: every handler calls ``ensure_session_context`` and all repo
calls are scoped by ``session_id`` — a caller can never see or mutate another
session's todos.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from jarvis.api.errors import APIError
from jarvis.api.schemas.todos import TodoCreate, TodoUpdate
from jarvis.persistence import create_all, repos
from jarvis.security.session_auth import ensure_session_context
from jarvis.todos.domain import (
    TODO_PRIORITIES,
    TODO_STATUSES,
    is_valid_todo_transition,
    normalize_due_at,
    todo_to_dict,
)

logger = logging.getLogger("jarvis.api.todos")

router = APIRouter(prefix="/todos", tags=["todos"])


class TodoComplete(BaseModel):
    session_id: str = "default"
    session_token: str | None = None


def _ensure_db() -> None:
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in todos route: %s", exc)


def _sid(session_id: str | None, session_token: str | None) -> str:
    sid = session_id or "default"
    ensure_session_context(sid, session_token)
    return sid


@router.post("")
def todo_create(payload: TodoCreate) -> dict:
    ensure_session_context(payload.session_id, payload.session_token)
    if payload.priority not in TODO_PRIORITIES:
        raise APIError(
            422,
            "invalid_todo_priority",
            f"priority must be one of {list(TODO_PRIORITIES)}.",
        )
    _ensure_db()
    due_at = normalize_due_at(payload.due_at)
    row = repos.todos.create(
        uuid.uuid4().hex,
        payload.session_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        due_at=due_at,
        source_request_id=payload.source_request_id,
    )
    return todo_to_dict(row)


@router.get("")
def todo_list(
    session_id: str | None = None,
    session_token: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    sid = _sid(session_id, session_token)
    _ensure_db()
    if status and status not in TODO_STATUSES:
        raise APIError(
            422,
            "invalid_todo_status",
            f"status must be one of {list(TODO_STATUSES)}.",
        )
    if priority and priority not in TODO_PRIORITIES:
        raise APIError(
            422,
            "invalid_todo_priority",
            f"priority must be one of {list(TODO_PRIORITIES)}.",
        )
    rows = repos.todos.list_for_session(
        sid,
        status=status,
        priority=priority,
        due_before=normalize_due_at(due_before),
        due_after=normalize_due_at(due_after),
        limit=max(0, min(limit, 500)),
        offset=max(0, offset),
    )
    return {"items": [todo_to_dict(r) for r in rows], "count": len(rows)}


@router.get("/{todo_id}")
def todo_get(
    todo_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    sid = _sid(session_id, session_token)
    _ensure_db()
    row = repos.todos.get(sid, todo_id)
    if row is None:
        raise APIError(404, "todo_not_found", "Todo not found.")
    return todo_to_dict(row)


@router.patch("/{todo_id}")
def todo_update(todo_id: str, payload: TodoUpdate) -> dict:
    ensure_session_context(payload.session_id, payload.session_token)
    _ensure_db()
    if payload.priority is not None and payload.priority not in TODO_PRIORITIES:
        raise APIError(
            422,
            "invalid_todo_priority",
            f"priority must be one of {list(TODO_PRIORITIES)}.",
        )
    if payload.status is not None and payload.status not in TODO_STATUSES:
        raise APIError(
            422,
            "invalid_todo_status",
            f"status must be one of {list(TODO_STATUSES)}.",
        )
    row = repos.todos.get(payload.session_id, todo_id)
    if row is None:
        raise APIError(404, "todo_not_found", "Todo not found.")
    if payload.status is not None and not is_valid_todo_transition(row.status, payload.status):
        raise APIError(
            422,
            "invalid_todo_transition",
            f"Cannot move todo from '{row.status}' to '{payload.status}'.",
        )
    updated = repos.todos.update(
        payload.session_id,
        todo_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        due_at=normalize_due_at(payload.due_at),
        status=payload.status,
    )
    return todo_to_dict(updated)


@router.delete("/{todo_id}")
def todo_delete(
    todo_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    sid = _sid(session_id, session_token)
    _ensure_db()
    if not repos.todos.soft_delete(sid, todo_id):
        raise APIError(404, "todo_not_found", "Todo not found.")
    return {"deleted": todo_id}


@router.post("/{todo_id}/complete")
def todo_complete(todo_id: str, payload: TodoComplete | None = None) -> dict:
    payload = payload or TodoComplete()
    ensure_session_context(payload.session_id, payload.session_token)
    _ensure_db()
    row = repos.todos.get(payload.session_id, todo_id)
    if row is None:
        raise APIError(404, "todo_not_found", "Todo not found.")
    if row.status == "completed":
        return todo_to_dict(row)
    if not is_valid_todo_transition(row.status, "completed"):
        raise APIError(
            422,
            "invalid_todo_transition",
            f"Cannot complete a todo in status '{row.status}'.",
        )
    updated = repos.todos.set_status(payload.session_id, todo_id, "completed")
    return todo_to_dict(updated)