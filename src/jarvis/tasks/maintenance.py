"""Periodic maintenance sweeper.

Runs TTL cleanups on a background timer so expired approvals and dormant
sessions cannot accumulate indefinitely:

* ``repos.approvals.purge_expired`` — flip pending approvals past their
  TTL to ``expired`` so a stale resume reports 410.
* ``repos.approvals.delete_expired_older_than`` — hard-delete expired rows
  beyond the retention window.
* ``repos.sessions.purge_inactive`` — drop sessions inactive past
  ``SESSION_TTL_DAYS``.

``start_sweeper`` / ``stop_sweeper`` manage a single daemon thread.
When ``MAINTENANCE_SWEEP_INTERVAL <= 0`` the thread never starts and only
the startup sweep runs.
"""
from __future__ import annotations

import logging
import threading

from jarvis.config.settings import settings
from jarvis.persistence.repo import repos

logger = logging.getLogger("jarvis.maintenance")

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


def sweep_once() -> dict:
    """Run one maintenance pass; returns per-cleanup row counts."""
    counts: dict = {"approvals_expired": 0, "approvals_deleted": 0, "sessions_deleted": 0}
    try:
        counts["approvals_expired"] = repos.approvals.purge_expired()
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval TTL sweep failed: %s", exc)
    try:
        counts["approvals_deleted"] = repos.approvals.delete_expired_older_than(
            settings.expired_approval_retention_hours
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("expired-approval deletion failed: %s", exc)
    try:
        counts["sessions_deleted"] = repos.sessions.purge_inactive(settings.session_ttl_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inactive-session purge failed: %s", exc)
    if any(counts.values()):
        logger.info("Maintenance sweep: %s", counts)
    return counts


def _loop() -> None:
    logger.info(
        "Maintenance sweeper started (interval=%ds, session_ttl=%dd)",
        settings.maintenance_sweep_interval,
        settings.session_ttl_days,
    )
    while not _stop_event.wait(settings.maintenance_sweep_interval):
        sweep_once()
    logger.info("Maintenance sweeper stopped")


def start_sweeper() -> None:
    """Start the background sweeper thread (idempotent; no-op if disabled)."""
    global _thread
    if settings.maintenance_sweep_interval <= 0:
        logger.info("Maintenance sweeper disabled")
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(
            target=_loop, name="jarvis-maintenance-sweep", daemon=True
        )
        _thread.start()


def stop_sweeper() -> None:
    """Signal the sweeper thread to stop and wait briefly for it."""
    global _thread
    _stop_event.set()
    with _lock:
        if _thread is not None and _thread.is_alive():
            _thread.join(timeout=2.0)
        _thread = None


__all__ = ["sweep_once", "start_sweeper", "stop_sweeper"]