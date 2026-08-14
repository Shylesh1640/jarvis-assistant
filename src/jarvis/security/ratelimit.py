"""Per-session rate limiting for the compute-heavy write endpoints.

``rate_limited`` is called at the top of ``POST /chat`` and the ``/tasks``
write routes with the parsed session id (falling back to the client IP). It
returns ``True`` on success and raises a structured 429 :class:`APIError`
with ``Retry-After`` when the session exceeds ``rate_limit_per_minute``
requests.

The counter is a thread-safe sliding window keyed by session id (or IP).
``rate_limit_per_minute = 0`` disables limiting entirely.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request

from jarvis.api.errors import APIError
from jarvis.config.settings import settings

# Sliding-window width, seconds.
_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding-window per-key rate limiter. Thread-safe."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(0, per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Return ``(allowed, seconds_to_wait_if_not)``."""
        if self.per_minute <= 0:
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - _WINDOW_SECONDS:
                q.popleft()
            if len(q) >= self.per_minute:
                wait = _WINDOW_SECONDS - (now - q[0])
                return False, max(0.0, wait)
            q.append(now)
            return True, 0.0

    def get_hits(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - _WINDOW_SECONDS:
                q.popleft()
            return len(q)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def reload_limiter() -> RateLimiter:
    """Rebuild the limiter from current settings (used by tests/startup)."""
    global _limiter
    _limiter = RateLimiter(settings.rate_limit_per_minute)
    return _limiter


_limiter = RateLimiter(settings.rate_limit_per_minute)


def rate_limited(request: Request, session_id: str | None) -> None:
    """Enforce the per-session/IP limit; raises :class:`APIError` on 429."""
    key = f"session:{session_id}" if session_id else _ip_key(request)
    allowed, wait = _limiter.check(key)
    if not allowed:
        retry_after = int(wait) + 1
        raise APIError(
            429,
            "rate_limited",
            "Too many requests. Please wait before trying again.",
            retry_after_seconds=retry_after,
            suggested_action="Wait a moment, then retry.",
            headers={"Retry-After": str(retry_after)},
        )


def _ip_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


__all__ = ["RateLimiter", "rate_limited", "reload_limiter"]