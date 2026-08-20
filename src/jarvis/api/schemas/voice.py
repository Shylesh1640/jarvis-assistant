"""Request schemas for the /voice API (Phase 8)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceSynthesize(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)