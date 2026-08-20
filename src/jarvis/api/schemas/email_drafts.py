"""Request/response schemas for the /email-drafts API (Phase 8)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class EmailDraftCreate(BaseModel):
    session_id: str = "default"
    session_token: str | None = None
    subject: str = Field(..., min_length=1, max_length=256)
    recipients: list[str] = Field(..., min_length=1, max_length=50)
    body: str | None = Field(None, max_length=20000)
    from_address: str | None = Field(None, max_length=256)
    source_request_id: str | None = None


class EmailDraftUpdate(BaseModel):
    session_id: str = "default"
    session_token: str | None = None
    subject: str | None = Field(None, min_length=1, max_length=256)
    recipients: list[str] | None = Field(None, min_length=1, max_length=50)
    body: str | None = Field(None, max_length=20000)
    from_address: str | None = Field(None, max_length=256)


class EmailDraftSend(BaseModel):
    session_id: str = "default"
    session_token: str | None = None