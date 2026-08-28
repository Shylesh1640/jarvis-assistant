"""Repository API.

Thin, synchronous helpers the route layer calls. Each repo owns one table
and opens its own short session via ``get_session()`` so callers don't
have to manage session lifetimes. (Session-per-operation is fine for a
local assistant; switch to a request-scoped session only if this becomes
a hot path.)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select

from jarvis.persistence.engine import get_session
from jarvis.persistence.models import (
    ApprovalRow,
    CloudUsageRow,
    EmailDraftRow,
    FeedbackRow,
    MessageRow,
    RoleRow,
    SessionRow,
    SummaryRow,
    TaskRow,
    TodoRow,
    UserRow,
)
from jarvis.security.token_hasher import new_session_token, verify_token


class SessionRepo:
    def get_or_create(self, session_id: str, *, user_id: str | None = None) -> SessionRow:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                row = SessionRow(
                    id=session_id,
                    user_id=user_id,
                    token=_new_token(),
                    last_active_at=datetime.now(timezone.utc),
                )
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
        """Return the session's bearer token, creating the session if needed.

        The token is stored *hashed* at rest; the plaintext is kept only in
        the process-local issuance cache so a repeated ``GET
        /sessions/{id}/token`` returns the same token. After a backend
        restart the plaintext is gone and the token is rotated (the old
        token — still validated by its hash — stops being returned).
        Legacy plaintext tokens from pre-Phase 6 rows are lazily hashed on
        first touch and their plaintext cleared.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                row = SessionRow(
                    id=session_id,
                    user_id=user_id,
                    last_active_at=now,
                )
                s.add(row)
                s.flush()
            if row.token and not row.token_hash:
                # Lazy migration of a legacy plaintext token.
                self._persist_token(row, row.token, s, now=now)
            cached = _token_cache.get(session_id)
            if cached:
                return cached
            token = new_session_token()
            self._persist_token(row, token, s, now=now)
            _token_cache.set(session_id, token)
            return token

    def is_token_valid(self, session_id: str, token: str | None) -> bool:
        """True when *token* matches the stored token for *session_id*.

        Enforces revocation and absolute expiry; verifies against the hash
        (or, during the one-time lazy migration, the legacy plaintext).
        """
        from datetime import datetime, timezone

        if not token:
            return False
        now = datetime.now(timezone.utc)
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                return False
            if row.token_revoked_at is not None:
                return False
            if row.token_expires_at is not None and _aware(row.token_expires_at) < now:
                return False
            stored = row.token_hash or row.token
            if not stored:
                return False
            if not verify_token(token, stored):
                return False
            if row.token and not row.token_hash:
                self._persist_token(row, token, s, now=now)
            _token_cache.set(session_id, token)
            return True

    def rotate_token(self, session_id: str) -> str | None:
        """Force a new token for *session_id* (old one stops validating).

        Returns the new plaintext, or None when the session does not exist.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                return None
            token = new_session_token()
            self._persist_token(row, token, s, now=now)
            _token_cache.set(session_id, token)
            return token

    def revoke_token(self, session_id: str) -> bool:
        """Revoke the session's token (it stops validating immediately)."""
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                return False
            row.token_revoked_at = datetime.now(timezone.utc)
            s.flush()
            _token_cache.remove(session_id)
            return True

    def token_status(self, session_id: str) -> dict | None:
        """Structured token metadata for the UI (never the token itself)."""
        from datetime import datetime, timedelta, timezone

        from jarvis.config.settings import settings

        row = self.get(session_id)
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        expires = row.token_expires_at
        rotated = row.token_rotated_at
        rotation_hours = settings.session_token_rotation_hours
        rotation_due = (
            rotated is not None
            and rotation_hours > 0
            and _aware(rotated) + timedelta(hours=rotation_hours) <= now
        )
        return {
            "has_token": bool(row.token_hash or row.token),
            "hash_scheme": row.token_hash_scheme or ("plaintext" if row.token else None),
            "created_at": _iso(row.token_created_at),
            "expires_at": _iso(expires),
            "rotated_at": _iso(rotated),
            "revoked_at": _iso(row.token_revoked_at),
            "expired": expires is not None and _aware(expires) < now,
            "rotation_due": rotation_due,
        }

    def _persist_token(self, row: SessionRow, token: str, s, *, now) -> None:
        """Hash *token* into the row and clear any legacy plaintext."""
        from jarvis.config.settings import settings
        from jarvis.security.token_hasher import hash_token

        scheme = settings.session_token_hash_scheme
        row.token_hash = hash_token(token, scheme)
        row.token_hash_scheme = scheme
        row.token_created_at = now
        row.token_rotated_at = now
        row.token_expires_at = self._expiry(now)
        row.token_revoked_at = None
        row.token = None
        s.flush()

    @staticmethod
    def _expiry(now) -> object | None:
        from datetime import timedelta

        from jarvis.config.settings import settings

        ttl = settings.session_token_ttl_hours
        if ttl > 0:
            return now + timedelta(hours=ttl)
        return None

    def message_count(self, session_id: str) -> int:
        """Number of messages stored for *session_id* (for session metadata)."""
        with get_session() as s:
            return s.scalar(
                select(func.count())
                .select_from(MessageRow)
                .where(MessageRow.session_id == session_id)
            ) or 0

    def purge_inactive(self, ttl_days: int, now=None) -> int:
        """Delete sessions with no activity in the last *ttl_days* days.

        Returns the number of rows deleted (cascades to their messages /
        summaries / approvals via FK). ``ttl_days <= 0`` disables cleanup.
        """
        from datetime import datetime, timedelta, timezone

        if ttl_days <= 0:
            return 0
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=ttl_days)
        with get_session() as s:
            rows = s.scalars(
                select(SessionRow).where(
                    SessionRow.last_active_at.is_not(None),
                    SessionRow.last_active_at < cutoff,
                )
            ).all()
            for row in rows:
                s.delete(row)
                _token_cache.remove(row.id)
            s.flush()
            return len(rows)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _aware(value) -> object:
    """Normalise a datetime possibly returned naive (SQLite) to UTC-aware."""
    from datetime import timezone

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class _TokenCache:
    """Process-local plaintext-token cache.

    Bounded (drops everything on overflow — rare for a local assistant) and
    thread-safe. The DB only ever holds hashes; the plaintext lives here so
    ``GET /sessions/{id}/token`` can re-return the current token within the
    process lifetime. On restart the cache is empty and the token rotates.
    """

    def __init__(self, max_size: int = 2000) -> None:
        import threading

        self._data: dict[str, str] = {}
        self._lock = threading.Lock()
        self._max = max_size

    def get(self, session_id: str) -> str | None:
        with self._lock:
            return self._data.get(session_id)

    def set(self, session_id: str, token: str) -> None:
        with self._lock:
            if len(self._data) >= self._max:
                self._data.clear()
            self._data[session_id] = token

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)


_token_cache = _TokenCache()


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

    def get(self, summary_id: int) -> SummaryRow | None:
        with get_session() as s:
            return s.get(SummaryRow, summary_id)

    def list_for_session(self, session_id: str, limit: int = 50) -> list[SummaryRow]:
        with get_session() as s:
            return list(
                s.scalars(
                    select(SummaryRow)
                    .where(SummaryRow.session_id == session_id)
                    .order_by(desc(SummaryRow.id))
                    .limit(limit)
                ).all()
            )

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

    def delete(self, summary_id: int) -> bool:
        with get_session() as s:
            row = s.get(SummaryRow, summary_id)
            if row is None:
                return False
            s.delete(row)
            s.flush()
            return True

    def delete_all_for_session(self, session_id: str) -> int:
        with get_session() as s:
            rows = s.scalars(
                select(SummaryRow).where(SummaryRow.session_id == session_id)
            ).all()
            for row in rows:
                s.delete(row)
            s.flush()
            return len(rows)


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

    def delete_expired_older_than(self, retention_hours: int, now=None) -> int:
        """Hard-delete ``expired`` rows past *retention_hours*.

        Returns the number of rows deleted. Keeps the table bounded: rows
        flipped by a TTL sweep linger only long enough to surface a 410 on
        a stale resume (``get_expired``), then are physically removed.
        """
        from datetime import datetime, timedelta, timezone

        if now is None:
            now = datetime.now(timezone.utc)
        if retention_hours <= 0:
            cutoff = now
        else:
            cutoff = now - timedelta(hours=retention_hours)
        with get_session() as s:
            rows = s.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.status == "expired",
                    ApprovalRow.expires_at < cutoff,
                )
            ).all()
            for row in rows:
                s.delete(row)
            s.flush()
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


class FeedbackRepo:
    """Store user ratings of assistant replies (good / bad / unclear)."""

    def add(
        self,
        session_id: str,
        *,
        question: str,
        answer: str,
        score: str,
        comment: str | None = None,
        path_used: str | None = None,
        model_used: str | None = None,
    ) -> int:
        with get_session() as s:
            row = FeedbackRow(
                session_id=session_id,
                question=question,
                answer=answer,
                score=score,
                comment=comment,
                path_used=path_used,
                model_used=model_used,
            )
            s.add(row)
            s.flush()
            return row.id

    def list(self, limit: int = 200) -> list[FeedbackRow]:
        with get_session() as s:
            return list(
                s.scalars(
                    select(FeedbackRow).order_by(desc(FeedbackRow.id)).limit(limit)
                ).all()
            )

    def list_for_session(self, session_id: str, limit: int = 200) -> list[FeedbackRow]:
        with get_session() as s:
            return list(
                s.scalars(
                    select(FeedbackRow)
                    .where(FeedbackRow.session_id == session_id)
                    .order_by(desc(FeedbackRow.id))
                    .limit(limit)
                ).all()
            )

    def count(self) -> int:
        with get_session() as s:
            return s.scalar(select(func.count()).select_from(FeedbackRow)) or 0

    def delete(self, feedback_id: int) -> bool:
        with get_session() as s:
            row = s.get(FeedbackRow, feedback_id)
            if row is None:
                return False
            s.delete(row)
            s.flush()
            return True

    def delete_all(self) -> int:
        with get_session() as s:
            rows = s.scalars(select(FeedbackRow)).all()
            for row in rows:
                s.delete(row)
            s.flush()
            return len(rows)


class CloudUsageRepo:
    """Persistent cloud-spend records (Phase 6)."""

    def add(
        self,
        *,
        day: str,
        session_id: str | None,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_usd: float,
    ) -> int:
        with get_session() as s:
            row = CloudUsageRow(
                day=day,
                session_id=session_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )
            s.add(row)
            s.flush()
            return row.id

    def sum_for_session(self, session_id: str) -> float:
        with get_session() as s:
            return float(
                s.scalar(
                    select(func.sum(CloudUsageRow.estimated_cost_usd)).where(
                        CloudUsageRow.session_id == session_id
                    )
                )
                or 0.0
            )

    def sum_for_day(self, day: str) -> float:
        with get_session() as s:
            return float(
                s.scalar(
                    select(func.sum(CloudUsageRow.estimated_cost_usd)).where(
                        CloudUsageRow.day == day
                    )
                )
                or 0.0
            )

    def count_for_day(self, day: str) -> int:
        with get_session() as s:
            return s.scalar(
                select(func.count())
                .select_from(CloudUsageRow)
                .where(CloudUsageRow.day == day)
            ) or 0

    def recent(self, limit: int = 50) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(CloudUsageRow)
                .order_by(desc(CloudUsageRow.id))
                .limit(limit)
            ).all()
            return [
                {
                    "day": r.day,
                    "session_id": r.session_id,
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "estimated_cost_usd": round(r.estimated_cost_usd, 6),
                    "created_at": _iso(r.created_at),
                }
                for r in rows
            ]


class TodoRepo:
    """Session-scoped CRUD for the ``todos`` table (Phase 8).

    All methods scope on ``session_id`` so a caller can never read or mutate
    another session's todos. Rows are soft-deleted via ``deleted_at``; the
    default scoping excludes soft-deleted rows.
    """

    def create(
        self,
        todo_id: str,
        session_id: str,
        *,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        due_at=None,
        source_request_id: str | None = None,
    ) -> TodoRow:
        with get_session() as s:
            row = TodoRow(
                todo_id=todo_id,
                session_id=session_id,
                title=title,
                description=description,
                status="open",
                priority=priority,
                due_at=due_at,
                source_request_id=source_request_id,
            )
            s.add(row)
            s.flush()
            return row

    def get(self, session_id: str, todo_id: str) -> TodoRow | None:
        with get_session() as s:
            return s.scalar(
                select(TodoRow).where(
                    TodoRow.session_id == session_id,
                    TodoRow.todo_id == todo_id,
                    TodoRow.deleted_at.is_(None),
                )
            )

    def list_for_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        due_before=None,
        due_after=None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TodoRow]:
        with get_session() as s:
            query = select(TodoRow).where(
                TodoRow.session_id == session_id,
                TodoRow.deleted_at.is_(None),
            )
            if status:
                query = query.where(TodoRow.status == status)
            if priority:
                query = query.where(TodoRow.priority == priority)
            if due_before is not None:
                query = query.where(TodoRow.due_at <= due_before)
            if due_after is not None:
                query = query.where(TodoRow.due_at >= due_after)
            query = query.order_by(TodoRow.due_at.is_(None), desc(TodoRow.created_at))
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            return list(s.scalars(query).all())

    def update(
        self,
        session_id: str,
        todo_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        due_at=None,
        status: str | None = None,
    ) -> TodoRow | None:
        with get_session() as s:
            row = s.scalar(
                select(TodoRow).where(
                    TodoRow.session_id == session_id,
                    TodoRow.todo_id == todo_id,
                    TodoRow.deleted_at.is_(None),
                )
            )
            if row is None:
                return None
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description
            if priority is not None:
                row.priority = priority
            if due_at is not None:
                row.due_at = due_at
            if status is not None:
                _apply_todo_status(row, status)
            s.flush()
            return row

    def set_status(self, session_id: str, todo_id: str, status: str) -> TodoRow | None:
        with get_session() as s:
            row = s.scalar(
                select(TodoRow).where(
                    TodoRow.session_id == session_id,
                    TodoRow.todo_id == todo_id,
                    TodoRow.deleted_at.is_(None),
                )
            )
            if row is None:
                return None
            _apply_todo_status(row, status)
            s.flush()
            return row

    def soft_delete(self, session_id: str, todo_id: str) -> bool:
        """Soft-delete a todo (set ``deleted_at`` + status ``cancelled``)."""
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.scalar(
                select(TodoRow).where(
                    TodoRow.session_id == session_id,
                    TodoRow.todo_id == todo_id,
                    TodoRow.deleted_at.is_(None),
                )
            )
            if row is None:
                return False
            row.deleted_at = datetime.now(timezone.utc)
            if row.status not in ("completed", "cancelled"):
                row.status = "cancelled"
            s.flush()
            return True

    def due_soon(
        self,
        now,
        lookahead,
        *,
        limit: int = 200,
    ) -> list[TodoRow]:
        """Active todos due within ``[now, now + lookahead]`` that have not
        yet been reminded (``last_reminded_at IS NULL``)."""
        from datetime import timedelta

        window_end = now + timedelta(minutes=lookahead)
        with get_session() as s:
            return list(
                s.scalars(
                    select(TodoRow).where(
                        TodoRow.deleted_at.is_(None),
                        TodoRow.status.in_(("open", "in_progress")),
                        TodoRow.due_at.is_not(None),
                        TodoRow.due_at >= now,
                        TodoRow.due_at <= window_end,
                        TodoRow.last_reminded_at.is_(None),
                    )
                    .order_by(TodoRow.due_at)
                    .limit(limit)
                ).all()
            )

    def mark_reminded(self, todo_id: str, when) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(TodoRow, todo_id)
            if row is not None:
                row.last_reminded_at = when or datetime.now(timezone.utc)
                s.flush()


def _apply_todo_status(row: TodoRow, status: str) -> None:
    """Set a todo's status, enforcing the lifecycle and stamping timestamps."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    row.status = status
    if status == "completed":
        row.completed_at = row.completed_at or now
    else:
        row.completed_at = None


class EmailDraftRepo:
    """Session-scoped CRUD for the ``email_drafts`` table (Phase 8)."""

    def create(
        self,
        draft_id: str,
        session_id: str,
        *,
        subject: str,
        recipients: list[str],
        body: str | None = None,
        from_address: str | None = None,
        source_request_id: str | None = None,
    ) -> EmailDraftRow:
        with get_session() as s:
            row = EmailDraftRow(
                draft_id=draft_id,
                session_id=session_id,
                subject=subject,
                recipients=list(recipients or []),
                body=body,
                from_address=from_address,
                source_request_id=source_request_id,
            )
            s.add(row)
            s.flush()
            return row

    def get(self, session_id: str, draft_id: str) -> EmailDraftRow | None:
        with get_session() as s:
            return s.scalar(
                select(EmailDraftRow).where(
                    EmailDraftRow.session_id == session_id,
                    EmailDraftRow.draft_id == draft_id,
                )
            )

    def list_for_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EmailDraftRow]:
        with get_session() as s:
            query = select(EmailDraftRow).where(EmailDraftRow.session_id == session_id)
            if status:
                query = query.where(EmailDraftRow.status == status)
            query = query.order_by(desc(EmailDraftRow.created_at))
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            return list(s.scalars(query).all())

    def update(
        self,
        session_id: str,
        draft_id: str,
        *,
        subject: str | None = None,
        recipients: list[str] | None = None,
        body: str | None = None,
        from_address: str | None = None,
    ) -> EmailDraftRow | None:
        with get_session() as s:
            row = s.scalar(
                select(EmailDraftRow).where(
                    EmailDraftRow.session_id == session_id,
                    EmailDraftRow.draft_id == draft_id,
                )
            )
            if row is None:
                return None
            if subject is not None:
                row.subject = subject
            if recipients is not None:
                row.recipients = list(recipients)
            if body is not None:
                row.body = body
            if from_address is not None:
                row.from_address = from_address
            s.flush()
            return row

    def mark_sent(self, session_id: str, draft_id: str, when=None) -> EmailDraftRow | None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.scalar(
                select(EmailDraftRow).where(
                    EmailDraftRow.session_id == session_id,
                    EmailDraftRow.draft_id == draft_id,
                )
            )
            if row is None:
                return None
            row.status = "sent"
            row.sent_at = when or datetime.now(timezone.utc)
            s.flush()
            return row

    def delete(self, session_id: str, draft_id: str) -> bool:
        with get_session() as s:
            row = s.scalar(
                select(EmailDraftRow).where(
                    EmailDraftRow.session_id == session_id,
                    EmailDraftRow.draft_id == draft_id,
                )
            )
            if row is None:
                return False
            s.delete(row)
            s.flush()
            return True


class UserRepo:
    """User account management (Phase 11)."""

    def create(
        self,
        user_id: str,
        *,
        email: str | None = None,
        display_name: str,
        password_hash: str | None = None,
        role_id: str = "user",
        is_active: bool = True,
    ) -> UserRow:
        from datetime import datetime, timezone

        with get_session() as s:
            row = UserRow(
                user_id=user_id,
                email=email,
                display_name=display_name,
                password_hash=password_hash,
                role_id=role_id,
                is_active=is_active,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            s.add(row)
            s.flush()
            return row

    def get(self, user_id: str) -> UserRow | None:
        with get_session() as s:
            return s.get(UserRow, user_id)

    def get_by_email(self, email: str) -> UserRow | None:
        with get_session() as s:
            return s.scalar(select(UserRow).where(UserRow.email == email))

    def list(self, limit: int = 100, offset: int = 0) -> list[UserRow]:
        with get_session() as s:
            return list(
                s.scalars(
                    select(UserRow)
                    .order_by(UserRow.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                ).all()
            )

    def update(
        self,
        user_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
        password_hash: str | None = None,
        role_id: str | None = None,
        is_active: bool | None = None,
    ) -> UserRow | None:
        with get_session() as s:
            row = s.get(UserRow, user_id)
            if row is None:
                return None
            if email is not None:
                row.email = email
            if display_name is not None:
                row.display_name = display_name
            if password_hash is not None:
                row.password_hash = password_hash
            if role_id is not None:
                row.role_id = role_id
            if is_active is not None:
                row.is_active = is_active
            row.updated_at = datetime.now(timezone.utc)
            s.flush()
            return row

    def deactivate(self, user_id: str) -> bool:
        return self.update(user_id, is_active=False) is not None

    def activate(self, user_id: str) -> bool:
        return self.update(user_id, is_active=True) is not None

    def delete(self, user_id: str) -> bool:
        with get_session() as s:
            row = s.get(UserRow, user_id)
            if row is None:
                return False
            s.delete(row)
            s.flush()
            return True

    def update_last_login(self, user_id: str) -> None:
        from datetime import datetime, timezone

        with get_session() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                row.last_login_at = datetime.now(timezone.utc)
                s.flush()


class RoleRepo:
    """Role management (Phase 11)."""

    def create(
        self,
        role_id: str,
        *,
        role_name: str,
        permissions: list[str] | None = None,
    ) -> RoleRow:
        from datetime import datetime, timezone

        with get_session() as s:
            row = RoleRow(
                role_id=role_id,
                role_name=role_name,
                permissions=list(permissions or []),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            s.add(row)
            s.flush()
            return row

    def get(self, role_id: str) -> RoleRow | None:
        with get_session() as s:
            return s.get(RoleRow, role_id)

    def get_by_name(self, role_name: str) -> RoleRow | None:
        with get_session() as s:
            return s.scalar(select(RoleRow).where(RoleRow.role_name == role_name))

    def list(self, limit: int = 100) -> list[RoleRow]:
        with get_session() as s:
            return list(
                s.scalars(select(RoleRow).order_by(RoleRow.created_at.desc()).limit(limit)).all()
            )

    def update(
        self,
        role_id: str,
        *,
        role_name: str | None = None,
        permissions: list[str] | None = None,
    ) -> RoleRow | None:
        with get_session() as s:
            row = s.get(RoleRow, role_id)
            if row is None:
                return None
            if role_name is not None:
                row.role_name = role_name
            if permissions is not None:
                row.permissions = list(permissions)
            row.updated_at = datetime.now(timezone.utc)
            s.flush()
            return row

    def delete(self, role_id: str) -> bool:
        with get_session() as s:
            row = s.get(RoleRow, role_id)
            if row is None:
                return False
            s.delete(row)
            s.flush()
            return True


class _Repos:
    sessions = SessionRepo()
    messages = MessageRepo()
    summaries = SummaryRepo()
    approvals = ApprovalRepo()
    tasks = TaskRepo()
    feedback = FeedbackRepo()
    cloud_usage = CloudUsageRepo()
    todos = TodoRepo()
    email_drafts = EmailDraftRepo()
    users = UserRepo()
    roles = RoleRepo()


repos = _Repos


# Public re-exports
__all__ = [
    "SessionRepo",
    "MessageRepo",
    "SummaryRepo",
    "ApprovalRepo",
    "TaskRepo",
    "CloudUsageRepo",
    "TodoRepo",
    "EmailDraftRepo",
    "UserRepo",
    "RoleRepo",
    "repos",
]
