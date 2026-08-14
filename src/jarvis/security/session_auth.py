"""Per-session bearer-token auth.

Sessions carry a random token issued via ``GET /sessions/{session_id}/token``.
When ``settings.require_session_token`` is enabled, every /chat and /tasks
request must present that token so clients cannot touch another session's
state (history, pending approvals, tasks).

With enforcement disabled the helpers are still called — the session row is
created/touched and a token is issued — so enabling the flag later is purely
a config change.
"""
from __future__ import annotations

from jarvis.api.errors import APIError
from jarvis.config.settings import settings
from jarvis.persistence import repos


def issue_token(session_id: str, *, user_id: str | None = None) -> str:
    """Create the session if needed and return its bearer token."""
    return repos.sessions.ensure_token(session_id, user_id=user_id)


def is_valid_token(session_id: str, token: str | None) -> bool:
    return repos.sessions.is_token_valid(session_id, token)


def ensure_session_context(session_id: str, token: str | None) -> None:
    """Ensure the session exists and, when required, that *token* is valid.

    Session persistence is best-effort: if the DB is unavailable the app
    continues in its pre-existing in-memory mode and only token *enforcement*
    is skipped. When ``require_session_token`` is enabled and tokens cannot
    be validated the request is rejected (fail-safe).
    """
    import logging

    db_ok = False
    try:
        repos.sessions.get_or_create(session_id)
        repos.sessions.touch(session_id)
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jarvis.api.routes.chat").warning(
            "Session persistence unavailable: %s", exc
        )
    if settings.require_session_token:
        valid = db_ok and is_valid_token(session_id, token)
        if not valid:
            raise APIError(
                403,
                "invalid_session_token",
                "A valid session token is required. Fetch one via "
                "GET /sessions/{session_id}/token.",
                suggested_action="Call GET /sessions/{session_id}/token and retry with the returned token.",
            )


__all__ = ["issue_token", "is_valid_token", "ensure_session_context"]