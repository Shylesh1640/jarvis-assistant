"""ORM models.

JSON columns are used for structured blobs (tool calls, tool results,
graph state) so we don't need a forest of join tables for a local-first
assistant. Postgres stores these as JSONB; SQLite uses TEXT via the
generic JSON type — SQLAlchemy handles the dialect difference for us.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jarvis.persistence.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Optional user identifier reserved for future auth.
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Per-session bearer token used to prevent cross-session access when
    # ``settings.require_session_token`` is enabled.
    token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Phase 6 :: token security. The token is stored *hashed* at rest
    # (token_hash + scheme); `token` holds a legacy plaintext only until the
    # one-time lazy migration on first successful validation. The plaintext
    # is kept in memory (issuance cache) so GET /sessions/{id}/token can
    # re-return it within the process lifetime; after a restart it rotates.
    token_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_hash_scheme: Mapped[str | None] = mapped_column(String(16), nullable=True)
    token_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Absolute expiry (SESSION_TOKEN_TTL_HOURS); None = never expires.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when the token is revoked via POST /sessions/{id}/revoke.
    token_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Last activity so the UI can show which sessions are live and a future
    # cleanup job can expire dormant sessions.
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    messages: Mapped[list["MessageRow"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageRow.id",
    )
    summaries: Mapped[list["SummaryRow"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SummaryRow.id",
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    # Path / model badges for assistant messages.
    path_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tools_used: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sources: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[SessionRow] = relationship(back_populates="messages")


class SummaryRow(Base):
    """Periodic summarization of a conversation, also mirrored to Chroma."""

    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    # The summary text; retrievable as a LangChain Document later.
    summary: Mapped[str] = mapped_column(Text)
    # Range of message ids covered.
    from_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[SessionRow] = relationship(back_populates="summaries")


class TaskRow(Base):
    """Background-job record surfaced through the /tasks endpoint."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    # queued | running | waiting_for_approval | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # Human-readable progress marker surfaced to the polling UI, e.g.
    # "running tests…", "writing file…".
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Approval snapshot surfaced when status == "waiting_for_approval".
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_tool_calls: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRow(Base):
    """Durable pending-approval record.

    Unlike the old in-memory cache, this row survives a backend restart so a
    paused approval can be resumed (or expired by TTL) even after the process
    died. ``state`` carries the JSON-serialised graph state needed to resume
    the exact stored tool call(s) — never a different action.
    """

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # approval_id
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    # Display fields from the highest-risk pending tool call.
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Full structured list of tool calls awaiting approval: [{name, args}].
    tool_calls: Mapped[list[Any]] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    pending_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The full LangGraph state, serialised to JSON. Carries tool_calls,
    # messages-as-dicts, intent, etc. so resume works after a restart.
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # pending | approved | denied | expired | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)


class FeedbackRow(Base):
    """User feedback on an assistant reply.

    Phase 6 :: Feedback quality. The UI offers a quick thumbs-up / thumbs-down
    (plus optional comment) on each reply. The row records the exact reply
    that was rated so ``jarvis-evaluate`` can review what the model actually
    produced. ``score`` is one of: good / bad / unclear.
    """

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    # The user's question that produced this reply.
    question: Mapped[str] = mapped_column(Text)
    # The exact assistant reply that was rated.
    answer: Mapped[str] = mapped_column(Text)
    # good | bad | unclear
    score: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Context for review: which path/model produced the reply.
    path_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CloudUsageRow(Base):
    """Persistent cloud-spend record (Phase 6 cost tracking).

    One row per completed cloud call; the cost is an estimate from the
    pricing config + the response's real token usage. Used to enforce the
    per-session and daily budgets across restarts and to power
    ``GET /cost``.
    """

    __tablename__ = "cloud_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # UTC date (YYYY-MM-DD) used for the daily-budget rollover.
    day: Mapped[str] = mapped_column(String(10), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(256))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
