"""Retry helper for transient failures.

Transient failures (a model server still starting, a timeout) deserve a
bounded retry with backoff; permanent ones (missing model, OOM) must fail
fast. ``retry_transient`` retries only the exception types passed in
``retryable`` and gives up immediately on anything else.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger("jarvis.utils.retry")

T = TypeVar("T")


def retry_transient(
    fn: Callable[[], T],
    *,
    attempts: int,
    backoff_seconds: float = 1.0,
    retryable: tuple[type[BaseException], ...],
    what: str = "operation",
) -> T:
    """Call ``fn()`` retrying *retryable* exceptions up to *attempts* times.

    The sleep between attempts grows linearly: attempt 2 sleeps
    ``backoff_seconds``, attempt 3 sleeps ``2 * backoff_seconds``, etc.
    Non-retryable exceptions propagate immediately.
    """
    tries = max(1, attempts)
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except retryable:  # noqa: PERF203 — forgiving capture for log below
            if attempt >= tries:
                raise
            delay = backoff_seconds * attempt
            logger.warning(
                "%s failed (attempt %d/%d) — retrying in %.1fs",
                what, attempt, tries, delay,
            )
            time.sleep(delay)


__all__ = ["retry_transient"]