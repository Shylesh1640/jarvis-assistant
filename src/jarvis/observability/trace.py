"""Per-request tracing.

Each chat or task request creates a lightweight ``Trace`` and logs a
structured one-line summary at the end. Intermediate ``trace_event``
calls append to a ring buffer so a caller can dump the full timeline
for debugging. Traces are intentionally in-memory and per-process —
they're a developer aid, durable logs belong elsewhere.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque

logger = logging.getLogger("jarvis.trace")

# Cap to keep memory bounded under load.
_MAX_TRACES = 256
_recent: Deque["Trace"] = deque(maxlen=_MAX_TRACES)


@dataclass
class Trace:
    session_id: str
    approved: bool
    started: float = field(default_factory=time.perf_counter)
    events: list[tuple[float, str, dict[str, Any]]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0


def new_trace(*, session_id: str, approved: bool = False) -> Trace:
    tr = Trace(session_id=session_id, approved=approved)
    _recent.append(tr)
    return tr


def trace_event(tr: Trace, label: str, **extra: Any) -> None:
    rel = (time.perf_counter() - tr.started) * 1000.0
    tr.events.append((rel, label, extra))


def finish_trace(tr: Trace, *, result: dict | None = None) -> None:
    trace_event(tr, "finish")
    intent = (result or {}).get("intent")
    complexity = (result or {}).get("complexity")
    path = (result or {}).get("selected_path")
    model = (result or {}).get("selected_model")
    risk = (result or {}).get("risk_level")
    approval_required = bool((result or {}).get("approval_required"))
    tools = list((result or {}).get("tools_used", []))
    logger.info(
        "trace | session=%s | duration_ms=%.1f | intent=%s | complexity=%s | "
        "path=%s | model=%s | risk=%s | approval=%s | tools=%s",
        tr.session_id,
        tr.duration_ms,
        intent,
        complexity,
        path,
        model,
        risk,
        approval_required,
        ",".join(tools) or "-",
    )


def recent_traces() -> list[Trace]:
    return list(_recent)


def clear_traces() -> None:
    _recent.clear()


__all__ = [
    "Trace",
    "new_trace",
    "trace_event",
    "finish_trace",
    "recent_traces",
    "clear_traces",
]
