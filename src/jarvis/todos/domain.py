"""Domain rules for the local tasks & reminders system (Phase 8).

Shared by the API routes, the LangChain tools, and the background reminder
worker so status transitions, priority/status enums, due-time normalisation
and response serialisation behave identically everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

TODO_STATUSES = ("open", "in_progress", "completed", "cancelled")
TODO_PRIORITIES = ("low", "medium", "high")

# Forward-only lifecycle: open -> in_progress -> completed | cancelled.
# A terminal state never moves again.
_TODO_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "completed", "cancelled"}),
    "in_progress": frozenset({"completed", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


def is_valid_todo_transition(current: str, new: str) -> bool:
    """True when moving a todo from *current* to *new* status is allowed."""
    return new in _TODO_TRANSITIONS.get(current, frozenset())


def normalize_due_at(value: str | datetime | None) -> datetime | None:
    """Coerce a user-supplied due time to an aware UTC datetime.

    Accepts an ISO-8601 string (naive timestamps are assumed UTC) or an
    already-parsed datetime. Returns ``None`` for a missing value.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def todo_to_dict(row) -> dict:
    """Serialize a TodoRow to the API response shape (no secrets)."""
    return {
        "todo_id": row.todo_id,
        "session_id": row.session_id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "priority": row.priority,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "source_request_id": row.source_request_id,
    }


__all__ = [
    "TODO_PRIORITIES",
    "TODO_STATUSES",
    "is_valid_todo_transition",
    "normalize_due_at",
    "todo_to_dict",
]