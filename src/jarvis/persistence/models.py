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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
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
    # pending | running | completed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PendingApprovalRow(Base):
    """Paused graph state awaiting an approval resume."""

    __tablename__ = "pending_approvals"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # The full LangGraph state, serialised to JSON. Carries tool_calls,
    # messages-as-dicts, intent, etc.
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    pending_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
