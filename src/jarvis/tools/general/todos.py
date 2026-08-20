"""LangChain tools for local tasks & reminders (Phase 8).

Session-scoped: every tool defaults to the ``"default"`` session (the same
session the UI uses) and can target another session via an explicit
``session_id`` argument. All writes go through ``repos.todos`` — local-only,
session-isolated, no external calls, no secrets in output.
"""
from __future__ import annotations

import uuid

from langchain_core.tools import tool

from jarvis.persistence.repo import repos
from jarvis.todos.domain import (
    TODO_PRIORITIES,
    TODO_STATUSES,
    is_valid_todo_transition,
    normalize_due_at,
)

_MAX_TITLE = 256
_INVALID_PRIORITY = f"invalid priority; must be one of {list(TODO_PRIORITIES)}"
_INVALID_STATUS = f"invalid status; must be one of {list(TODO_STATUSES)}"


@tool
def list_todos(
    session_id: str = "default",
    status: str | None = None,
    priority: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    limit: int = 50,
) -> str:
    """List the user's to-do items for a session.

    Optional filters: ``status`` (open|in_progress|completed|cancelled),
    ``priority`` (low|medium|high), and ISO-8601 ``due_before``/``due_after``
    bounds. Read-only and safe.
    """
    if status and status not in TODO_STATUSES:
        return _INVALID_STATUS
    if priority and priority not in TODO_PRIORITIES:
        return _INVALID_PRIORITY
    rows = repos.todos.list_for_session(
        session_id,
        status=status,
        priority=priority,
        due_before=normalize_due_at(due_before),
        due_after=normalize_due_at(due_after),
        limit=max(1, min(limit, 200)),
    )
    if not rows:
        return "No todos found."
    lines = []
    for r in rows:
        line = f"[{r.todo_id}] ({r.status}/{r.priority}) {r.title}"
        if r.due_at:
            line += f" — due {r.due_at.isoformat()}"
        lines.append(line)
    return "\n".join(lines)


@tool
def create_todo(
    title: str,
    session_id: str = "default",
    description: str | None = None,
    priority: str = "medium",
    due_at: str | None = None,
    source_request_id: str | None = None,
) -> str:
    """Create a to-do item for the user's session.

    ``title`` is required (max 256 chars). ``due_at`` is an ISO-8601 string,
    e.g. ``"2026-08-21T09:00:00Z"``. ``priority`` is low|medium|high.
    """
    if not title or not title.strip():
        return "Error: a title is required."
    if len(title) > _MAX_TITLE:
        return f"Error: title must be {_MAX_TITLE} characters or fewer."
    if priority not in TODO_PRIORITIES:
        return _INVALID_PRIORITY
    try:
        due = normalize_due_at(due_at)
    except ValueError as exc:
        return f"Error: invalid due_at ({exc}). Use ISO-8601 like 2026-08-21T09:00:00Z."
    row = repos.todos.create(
        uuid.uuid4().hex,
        session_id,
        title=title.strip(),
        description=description,
        priority=priority,
        due_at=due,
        source_request_id=source_request_id,
    )
    return f"Created todo {row.todo_id}: {row.title} (status={row.status})."


@tool
def complete_todo(todo_id: str, session_id: str = "default") -> str:
    """Mark a to-do item completed (stamps completed_at)."""
    row = repos.todos.get(session_id, todo_id)
    if row is None:
        return f"Error: todo {todo_id} not found in session {session_id}."
    if row.status == "completed":
        return f"Todo {todo_id} is already completed."
    if not is_valid_todo_transition(row.status, "completed"):
        return f"Error: cannot complete a todo in status '{row.status}'."
    updated = repos.todos.set_status(session_id, todo_id, "completed")
    return f"Completed todo {todo_id}: {updated.title}"


@tool
def update_todo(
    todo_id: str,
    session_id: str = "default",
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    due_at: str | None = None,
    status: str | None = None,
) -> str:
    """Update a to-do item's fields (title, description, priority, due_at, status)."""
    row = repos.todos.get(session_id, todo_id)
    if row is None:
        return f"Error: todo {todo_id} not found in session {session_id}."
    if title is not None and not title.strip():
        return "Error: title cannot be empty."
    if title is not None and len(title) > _MAX_TITLE:
        return f"Error: title must be {_MAX_TITLE} characters or fewer."
    if priority is not None and priority not in TODO_PRIORITIES:
        return _INVALID_PRIORITY
    if status is not None and status not in TODO_STATUSES:
        return _INVALID_STATUS
    if status is not None and not is_valid_todo_transition(row.status, status):
        return f"Error: cannot move todo from '{row.status}' to '{status}'."
    try:
        due = normalize_due_at(due_at)
    except ValueError as exc:
        return f"Error: invalid due_at ({exc}). Use ISO-8601 like 2026-08-21T09:00:00Z."
    repos.todos.update(
        session_id,
        todo_id,
        title=title.strip() if title else None,
        description=description,
        priority=priority,
        due_at=due,
        status=status,
    )
    return f"Updated todo {todo_id}."


@tool
def delete_todo(todo_id: str, session_id: str = "default") -> str:
    """Delete (soft-delete) a to-do item. Reversible."""
    if not repos.todos.soft_delete(session_id, todo_id):
        return f"Error: todo {todo_id} not found in session {session_id}."
    return f"Deleted todo {todo_id}."


__all__ = [
    "list_todos",
    "create_todo",
    "complete_todo",
    "update_todo",
    "delete_todo",
]