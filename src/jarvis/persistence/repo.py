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
    ApprovalRow,
    MessageRow,
    SessionRow,
    SummaryRow,
    TaskRow,
)


class SessionRepo:
    def get_or_create(self, session_id: str, *, user_id: str | None = None) -> SessionRow:
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                row = SessionRow(id=session_id, user_id=user_id, token=_new_token())
                s.add(row)
                s.flush()
            elif user_id and not row.user_id:
                row.user_id = user_id
                s.flush()
            return row

    def get(self, session_id: str) -> SessionRow | None:
        with get_session() as s:
            return s.get(SessionRow, session_id)

    def list(self, limit: int = 100) -> list[SessionRow]:
        with get_session() as s:
            return list(
                s.scalars(
                    select(SessionRow).order_by(desc(SessionRow.last_active_at)).limit(limit)
                ).all()
            )

    def touch(self, session_id: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is not None:
                row.updated_at = datetime.now(timezone.utc)
                row.last_active_at = datetime.now(timezone.utc)
                s.flush()

    def ensure_token(self, session_id: str, *, user_id: str | None = None) -> str:
        """Return the session's bearer token, creating the session if needed."""
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                row = SessionRow(id=session_id, user_id=user_id, token=_new_token())
                s.add(row)
                s.flush()
            if not row.token:
                row.token = _new_token()
                s.flush()
            return row.token

    def is_token_valid(self, session_id: str, token: str | None) -> bool:
        """True when *token* matches the stored token for *session_id*."""
        if not token:
            return False
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None or not row.token:
                return False
            return token == row.token


def _new_token() -> str:
    import secrets

    return secrets.token_hex(16)


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
    """Durable pending-approval store.

    A row is written when the graph pauses for approval and survives a
    backend restart. ``get_pending`` / ``pop_pending`` are the resume
    accessors; ``set_status`` transitions rows through
    pending/approved/denied/expired/cancelled; ``purge_expired`` enforces
    the TTL.
    """

    def create(
        self,
        approval_id: str,
        *,
        session_id: str,
        state: dict,
        expires_at,
        tool_name: str | None = None,
        arguments: dict | None = None,
        tool_calls: list | None = None,
        risk_level: str = "low",
        pending_action: str | None = None,
    ) -> ApprovalRow:
        from datetime import datetime, timezone

        if isinstance(expires_at, str):
            dt = datetime.fromisoformat(expires_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            expires_at = dt
        with get_session() as s:
            row = ApprovalRow(
                id=approval_id,
                session_id=session_id,
                tool_name=tool_name,
                arguments=arguments,
                tool_calls=tool_calls or [],
                risk_level=risk_level,
                pending_action=pending_action,
                state=state,
                expires_at=expires_at,
                status="pending",
            )
            s.add(row)
            s.flush()
            return row

    def get(self, approval_id: str) -> ApprovalRow | None:
        with get_session() as s:
            return s.get(ApprovalRow, approval_id)

    def get_pending(self, session_id: str) -> ApprovalRow | None:
        with get_session() as s:
            return s.scalars(
                select(ApprovalRow)
                .where(
                    ApprovalRow.session_id == session_id,
                    ApprovalRow.status == "pending",
                )
                .order_by(desc(ApprovalRow.created_at))
                .limit(1)
            ).first()

    def get_expired(self, session_id: str) -> ApprovalRow | None:
        """Return the latest non-resolvable row for *session_id*.

        Used so a resume can report ``expired`` (410) instead of a generic
        "no pending approval" after a TTL sweep already flipped the row.
        """
        with get_session() as s:
            return s.scalars(
                select(ApprovalRow)
                .where(
                    ApprovalRow.session_id == session_id,
                    ApprovalRow.status == "expired",
                )
                .order_by(desc(ApprovalRow.created_at))
                .limit(1)
            ).first()

    def pop_pending(self, session_id: str) -> ApprovalRow | None:
        """Atomically return and delete the pending row for *session_id*."""
        row = self.get_pending(session_id)
        if row is None:
            return None
        self.delete(row.id)
        return row

    def delete(self, approval_id: str) -> None:
        with get_session() as s:
            row = s.get(ApprovalRow, approval_id)
            if row is not None:
                s.delete(row)
                s.flush()

    def set_status(self, approval_id: str, status: str) -> None:
        with get_session() as s:
            row = s.get(ApprovalRow, approval_id)
            if row is not None:
                row.status = status
                s.flush()

    def cancel_all_for_session(self, session_id: str) -> None:
        """Mark any pending approval for *session_id* as cancelled."""

        with get_session() as s:
            rows = s.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.session_id == session_id,
                    ApprovalRow.status == "pending",
                )
            ).all()
            for row in rows:
                row.status = "cancelled"
            s.flush()

    def purge_expired(self, now=None) -> int:
        """Mark every pending row past its expiry as ``expired``.

        Returns the number of rows updated. Intended to be called
        periodically (startup + a background TTL sweep).
        """
        from datetime import datetime, timezone

        if now is None:
            now = datetime.now(timezone.utc)
        with get_session() as s:
            rows = s.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.status == "pending",
                    ApprovalRow.expires_at < now,
                )
            ).all()
            for row in rows:
                row.status = "expired"
            return len(rows)


class TaskRepo:
    def get(self, task_id: str) -> TaskRow | None:
        with get_session() as s:
            return s.get(TaskRow, task_id)

    def create(
        self, task_id: str, *, description: str, session_id: str | None = None
    ) -> TaskRow:
        with get_session() as s:
            row = TaskRow(
                id=task_id, session_id=session_id, description=description, status="queued"
            )
            s.add(row)
            s.flush()
            return row

    def list_for_session(
        self, session_id: str, limit: int = 20
    ) -> list[TaskRow]:
        with get_session() as s:
            return list(
                s.scalars(
                    select(TaskRow)
                    .where(TaskRow.session_id == session_id)
                    .order_by(desc(TaskRow.created_at))
                    .limit(limit)
                ).all()
            )

    def mark_queued(self, task_id: str) -> None:
        self._set_status(task_id, "queued")

    def mark_running(self, task_id: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "running"
                row.started_at = row.started_at or datetime.now(timezone.utc)
                s.flush()

    def mark_waiting_for_approval(
        self,
        task_id: str,
        *,
        approval_id: str | None,
        pending_action: str | None,
        pending_tool_calls: list | None,
    ) -> None:
        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "waiting_for_approval"
                row.approval_id = approval_id
                row.pending_action = pending_action
                row.pending_tool_calls = pending_tool_calls or []
                s.flush()

    def mark_done(self, task_id: str, result: str | None) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "completed"
                row.result = result
                row.stage = None
                row.finished_at = datetime.now(timezone.utc)
                s.flush()

    def mark_failed(self, task_id: str, error: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "failed"
                row.error = error
                row.stage = None
                row.finished_at = datetime.now(timezone.utc)
                s.flush()

    def mark_cancelled(self, task_id: str, error: str | None = None) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = "cancelled"
                row.error = error
                row.stage = None
                row.finished_at = datetime.now(timezone.utc)
                s.flush()

    def update_stage(self, task_id: str, stage: str | None) -> None:
        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.stage = stage
                s.flush()

    def recover_stale(self, *statuses: str) -> int:
        """Fail tasks left in a non-terminal state by a previous process.

        Returns the number of rows updated. Called once at startup so a
        backend restart never leaves a task perpetually ``running`` or
        ``waiting_for_approval``.
        """
        from datetime import datetime, timezone

        terminal = ("completed", "failed", "cancelled")
        targets = [st for st in (statuses or ("queued", "running", "waiting_for_approval")) if st not in terminal]
        with get_session() as s:
            rows = s.scalars(
                select(TaskRow).where(TaskRow.status.in_(targets))
            ).all()
            for row in rows:
                row.status = "failed"
                row.error = "Interrupted by a backend restart."
                row.stage = None
                row.finished_at = datetime.now(timezone.utc)
            return len(rows)

    def _set_status(self, task_id: str, status: str) -> None:
        with get_session() as s:
            row = s.get(TaskRow, task_id)
            if row is not None:
                row.status = status
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
