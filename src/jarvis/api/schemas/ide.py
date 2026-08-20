"""Request schemas for the /ide API (Phase 8)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class IdeCommand(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000)


class IdeOpenFile(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


class IdeSearchFiles(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=500)
    path: str | None = Field(None, max_length=1000)


class IdeRunTests(BaseModel):
    pass