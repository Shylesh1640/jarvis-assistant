"""Per-request tracing.

Each chat or task request creates a lightweight ``Trace`` and logs a
structured one-line summary at the end. Intermediate ``trace_event``
calls append to a ring buffer so a caller can dump the full timeline
for debugging. Traces are kept in a bounded in-memory ring (a developer
aid) and are exposed read-only through ``GET /traces/recent`` for the
Streamlit debug panel.

A finished trace can be rendered as the documented JSON shape::

    {
      "request_id": "...",
      "session_id": "...",
      "timestamp": "...",
      "intent": "coding",
      "complexity": "medium",
      "selected_model": "qwen2.5-coder:7b",
      "path_used": "coding",
      "tools_used": ["search_code", "read_file"],
      "risk_level": "low",
      "approval_status": "not_required",
      "duration_ms": 1234,
      "fallback_used": false,
      "error": null
    }
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque

logger = logging.getLogger("jarvis.trace")

# Cap to keep memory bounded under load.
_MAX_TRACES = 256
_recent: Deque["Trace"] = deque(maxlen=_MAX_TRACES)


@dataclass
class Trace:
    session_id: str
    approved: bool
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started: float = field(default_factory=time.perf_counter)
    events: list[tuple[float, str, dict[str, Any]]] = field(default_factory=list)

    # Populated by finish_trace for the /traces/recent payload.
    intent: str | None = None
    complexity: str | None = None
    selected_model: str | None = None
    path_used: str | None = None
    tools_used: list[str] = field(default_factory=list)
    risk_level: str | None = None
    approval_status: str | None = None
    fallback_used: bool = False
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        """Render the trace as the documented JSON payload."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": self.intent,
            "complexity": self.complexity,
            "selected_model": self.selected_model,
            "path_used": self.path_used,
            "tools_used": list(self.tools_used),
            "risk_level": self.risk_level,
            "approval_status": self.approval_status,
            "duration_ms": round(self.duration_ms, 1),
            "fallback_used": self.fallback_used,
            "error": self.error,
        }


def new_trace(*, session_id: str, approved: bool = False) -> Trace:
    tr = Trace(session_id=session_id, approved=approved)
    _recent.append(tr)
    return tr


def trace_event(tr: Trace, label: str, **extra: Any) -> None:
    rel = (time.perf_counter() - tr.started) * 1000.0
    tr.events.append((rel, label, extra))


def _approval_status_from(result: dict | None, tr: Trace) -> str:
    if (result or {}).get("approval_required"):
        return "required"
    if tr.approved or (result or {}).get("approved"):
        return "approved"
    return "not_required"


def finish_trace(tr: Trace, *, result: dict | None = None) -> None:
    trace_event(tr, "finish")
    result = result or {}
    tr.intent = result.get("intent")
    tr.complexity = result.get("complexity")
    tr.path_used = result.get("selected_path")
    tr.selected_model = result.get("selected_model")
    tr.tools_used = list(result.get("tools_used", []))
    tr.risk_level = result.get("risk_level")
    tr.approval_status = _approval_status_from(result, tr)
    tr.fallback_used = bool(result.get("fallback_used"))
    tr.error = result.get("error_state")

    approval = tr.approval_status or "not_required"
    logger.info(
        "trace | request=%s session=%s duration_ms=%.1f intent=%s complexity=%s "
        "path=%s model=%s risk=%s approval=%s fallback=%s tools=%s error=%s",
        tr.request_id,
        tr.session_id,
        tr.duration_ms,
        tr.intent,
        tr.complexity,
        tr.path_used,
        tr.selected_model,
        tr.risk_level,
        approval,
        tr.fallback_used,
        ",".join(tr.tools_used) or "-",
        tr.error or "-",
    )


def recent_traces() -> list[Trace]:
    return list(_recent)


def recent_trace_dicts(limit: int = 50) -> list[dict[str, Any]]:
    traces = list(_recent)
    if limit:
        traces = traces[-limit:]
    return [t.to_dict() for t in traces]


def clear_traces() -> None:
    _recent.clear()


__all__ = [
    "Trace",
    "new_trace",
    "trace_event",
    "finish_trace",
    "recent_traces",
    "recent_trace_dicts",
    "clear_traces",
]