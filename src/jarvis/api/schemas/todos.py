"""Request/response schemas for the /todos API (Phase 8)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TodoCreate(BaseModel):
    session_id: str = "default"
    session_token: str | None = None
    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = Field(None, max_length=4000)
    priority: str = "medium"  # low | medium | high
    due_at: datetime | None = None  # ISO-8601
    source_request_id: str | None = None


class TodoUpdate(BaseModel):
    session_id: str = "default"
    session_token: str | None = None
    title: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = Field(None, max_length=4000)
    priority: str | None = None  # low | medium | high
    due_at: datetime | None = None  # ISO-8601
    status: str | None = None  # open | in_progress | completed | cancelled