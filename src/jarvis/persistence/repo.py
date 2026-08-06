"""Repository API.

Thin, synchronous helpers the route layer calls. Each repo owns one table
and opens its own short session via ``get_session()`` so callers don't
have to manage session lifetimes. (Session-per-operation is fine for a
local assistant; switch to a request-scoped session only if this becomes
a hot path.)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, func, select

from jarvis.persistence.engine import get_session
from jarvis.persistence.models import (
    MessageRow,
    PendingApprovalRow,
    SessionRow,
    SummaryRow,
    TaskRow,
)


class SessionRepo:
    def get_or_create(self, session_id: str) -> SessionRow:
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                row = SessionRow(id=session_id)
                s.add(row)
                s.flush()
            return row

    def touch(self, session_id: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is not None:
                row.updated_at = datetime.now(timezone.utc)
                s.flush()


class MessageRepo:
    def history(self, session_id: str) -> list[dict[str, Any]]:
        with get_session() as s:
            rows = s.scalars(
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .order_by(MessageRow.id)
            ).all()
            return [self._to_dict(r) for r in rows]

    def add(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        path_used: str | None = None,
        model_used: str | None = None,
        tools_used: list | None = None,
        sources: list | None = None,
    ) -> int:
        with get_session() as s:
            row = MessageRow(
                session_id=session_id,
                role=role,
                content=content,
                path_used=path_used,
                model_used=model_used,
                tools_used=tools_used or [],
                sources=sources or [],
            )
            s.add(row)
            s.flush()
            return row.id

    def count_for_session(self, session_id: str) -> int:
        with get_session() as s:
            return s.scalar(
                select(func.count())
                .select_from(MessageRow)
                .where(MessageRow.session_id == session_id)
            ) or 0

    def tail(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        with get_session() as s:
            rows = s.scalars(
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .order_by(desc(MessageRow.id))
                .limit(limit)
            ).all()
            return [self._to_dict(r) for r in reversed(rows)]

    def _to_dict(self, row: MessageRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "pathUsed": row.path_used,
            "modelUsed": row.model_used,
            "toolsUsed": list(row.tools_used or []),
            "sources": list(row.sources or []),
        }


class SummaryRepo:
    def add(
        self,
        session_id: str,
        *,
        summary: str,
        from_message_id: int | None = None,
        to_message_id: int | None = None,
    ) -> int:
        with get_session() as s:
            row = SummaryRow(
                session_id=session_id,
                summary=summary,
                from_message_id=from_message_id,
                to_message_id=to_message_id,
            )
            s.add(row)
            s.flush()
            return row.id

    def latest_for_session(self, session_id: str) -> SummaryRow | None:
        with get_session() as s:
            return s.scalars(
                select(SummaryRow)
                .where(SummaryRow.session_id == session_id)
                .order_by(desc(SummaryRow.id))
                .limit(1)
            ).first()

    def count_for_session(self, session_id: str) -> int:
        with get_session() as s:
            return s.scalar(
                select(func.count())
                .select_from(SummaryRow)
                .where(SummaryRow.session_id == session_id)
            ) or 0


class ApprovalRepo:
    """Stores paused graph state so the API process can be restarted
    between an approval request and the user's response without losing it.
    """

    def put(self, session_id: str, state: dict, pending_action: str | None) -> None:
        with get_session() as s:
            s.merge(
                PendingApprovalRow(
                    session_id=session_id,
                    state=state,
                    pending_action=pending_action,
                )
            )

    def get(self, session_id: str) -> PendingApprovalRow | None:
        with get_session() as s:
            return s.get(PendingApprovalRow, session_id)

    def pop(self, session_id: str) -> PendingApprovalRow | None:
        with get_session() as s:
            row = s.get(PendingApprovalRow, session_id)
            if row is not None:
                s.delete(row)
                s.flush()
            return row

    def clear(self, session_id: str) -> None:
        with get_session() as s:
            row = s.get(PendingApprovalRow, session_id)
            if row is not None:
                s.delete(row)
                s.flush()


class TaskRepo:
    def get(self, task_id: str) -> TaskRow | None:
        with get_session() as s:
            return s.get(TaskRow, task_id)

    def create(
        self, task_id: str, *, description: str, session_id: str | None = None
    ) -> TaskRow:
        with get_session() as s:
            row = TaskRow(
                id=task_id, session_id=session_id, description=description, status="pending"
            )
            s.add(row)
            s.flush()
            return row

    def mark_running(self, task_id: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "running"
                row.started_at = datetime.now(timezone.utc)
                s.flush()

    def mark_done(self, task_id: str, result: str | None) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "completed"
                row.result = result
                row.finished_at = datetime.now(timezone.utc)
                s.flush()

    def mark_failed(self, task_id: str, error: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "failed"
                row.error = error
                row.finished_at = datetime.now(timezone.utc)
                s.flush()


class _Repos:
    sessions = SessionRepo()
    messages = MessageRepo()
    summaries = SummaryRepo()
    approvals = ApprovalRepo()
    tasks = TaskRepo()


repos = _Repos


# Public re-exports
__all__ = [
    "SessionRepo",
    "MessageRepo",
    "SummaryRepo",
    "ApprovalRepo",
    "TaskRepo",
    "repos",
]
