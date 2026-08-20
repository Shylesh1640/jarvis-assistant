"""Request/response schemas for the /calendar API (Phase 8)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CalendarEventCreate(BaseModel):
    summary: str = Field(..., min_length=1, max_length=500)
    start: datetime
    end: datetime
    calendar_id: str | None = None  # falls back to CALENDAR_DEFAULT_CALENDAR_ID
    description: str | None = Field(None, max_length=4000)
    location: str | None = Field(None, max_length=500)


class CalendarEventUpdate(BaseModel):
    summary: str | None = Field(None, min_length=1, max_length=500)
    start: datetime | None = None
    end: datetime | None = None
    calendar_id: str | None = None
    description: str | None = Field(None, max_length=4000)
    location: str | None = Field(None, max_length=500)