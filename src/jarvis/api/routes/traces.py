"""Read-only access to recent request traces (Streamlit debug panel)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from jarvis.observability.trace import recent_trace_dicts

router = APIRouter(prefix="/traces", tags=["observability"])


@router.get("/recent")
def recent_traces(limit: int = Query(50, ge=1, le=256)) -> dict:
    """Return the most recent request traces (oldest first within the window).

    Each entry has the documented trace shape: request_id, session_id,
    intent, complexity, selected_model, path_used, tools_used, risk_level,
    approval_status, duration_ms, fallback_used, error.
    """
    return {"traces": recent_trace_dicts(limit=limit)}