"""Background reminder worker for due-soon todos (Phase 8).

``scan_once`` finds active todos whose ``due_at`` falls within the lookahead
window ``[now, now + TODO_REMINDER_LOOKAHEAD_MINUTES]`` and have not been
reminded yet (``last_reminded_at IS NULL``), then inserts an assistant-style
message into the owning session (so the user sees it next time they chat) and
stamps ``last_reminded_at`` so it fires only once.

Reminders are *local-only*: they never send external emails/SMS/notifications
unless a future provider integration explicitly opts in. Nothing sensitive is
logged — only todo ids and titles.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from jarvis.config.settings import settings
from jarvis.persistence.repo import repos

logger = logging.getLogger("jarvis.reminders")

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


def scan_once(now: datetime | None = None) -> dict:
    """Run one reminder pass; returns a per-session count of fired reminders."""
    now = now or datetime.now(timezone.utc)
    lookahead = settings.todo_reminder_lookahead_minutes
    if lookahead <= 0:
        return {"fired": 0, "sessions": 0}
    due = repos.todos.due_soon(now, lookahead)
    fired = 0
    by_session: dict[str, list[dict]] = {}
    for row in due:
        title = row.title
        due_iso = row.due_at.isoformat() if row.due_at else ""
        by_session.setdefault(row.session_id, []).append((title, due_iso))
    for session_id, items in by_session.items():
        lines = []
        for title, due_iso in items:
            lines.append(f"- {title} (due {due_iso})")
        content = (
            "Reminder: the following todo(s) are due soon:\n"
            + "\n".join(lines)
            + "\n\nI can mark them complete or help you reschedule them."
        )
        try:
            repos.messages.add(session_id, role="assistant", content=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write reminder message for session %s: %s", session_id, exc)
            continue
        fired += len(items)
    for row in due:
        try:
            repos.todos.mark_reminded(row.todo_id, now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not mark todo %s as reminded: %s", row.todo_id, exc)
    if fired:
        logger.info("Reminder scan fired %d reminder(s)", fired)
    return {"fired": fired, "sessions": len(by_session)}


def _loop() -> None:
    logger.info(
        "Reminder worker started (interval=%ds, lookahead=%dm)",
        settings.todo_reminder_scan_interval_seconds,
        settings.todo_reminder_lookahead_minutes,
    )
    while not _stop_event.wait(settings.todo_reminder_scan_interval_seconds):
        try:
            scan_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reminder scan failed: %s", exc)
    logger.info("Reminder worker stopped")


def start_reminder_worker() -> None:
    """Start the background reminder thread (idempotent; no-op if disabled)."""
    global _thread
    if settings.todo_reminder_scan_interval_seconds <= 0:
        logger.info("Reminder worker disabled")
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, name="jarvis-reminders", daemon=True)
        _thread.start()


def stop_reminder_worker() -> None:
    """Signal the reminder thread to stop and wait briefly for it."""
    global _thread
    _stop_event.set()
    with _lock:
        if _thread is not None and _thread.is_alive():
            _thread.join(timeout=2.0)
        _thread = None


__all__ = ["scan_once", "start_reminder_worker", "stop_reminder_worker"]