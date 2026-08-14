"""Structured, machine-readable error responses.

Every HTTP error the API emits with this module carries the shape::

    {
      "error": "ollama_unavailable",
      "message": "Ollama is not reachable...",
      "retry_after_seconds": 10,
      "suggested_action": "Restart Ollama or run as a background task."
    }

``retry_after_seconds`` and ``suggested_action`` are optional. The global
exception handler in ``jarvis.api.main`` converts :class:`APIError` into a
JSON response and catches every other exception as a structured 500, so
clients never see a bare ``{"detail": ...}`` from Jarvis code.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse


class APIError(HTTPException):
    """An error with a stable machine-readable ``error`` code."""

    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        suggested_action: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.error = error
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.suggested_action = suggested_action


def build_error_body(
    status_code: int,
    error: str,
    message: str,
    *,
    retry_after_seconds: int | None = None,
    suggested_action: str | None = None,
) -> dict:
    body: dict = {
        "error": error,
        "message": message,
    }
    if retry_after_seconds is not None:
        body["retry_after_seconds"] = retry_after_seconds
    if suggested_action:
        body["suggested_action"] = suggested_action
    return body


def api_error_to_json(exc: APIError) -> JSONResponse:
    if exc.retry_after_seconds is not None:
        headers = dict(exc.headers or {})
        headers.setdefault("Retry-After", str(exc.retry_after_seconds))
    else:
        headers = exc.headers
    body = build_error_body(
        exc.status_code,
        exc.error,
        exc.detail or exc.message,
        retry_after_seconds=exc.retry_after_seconds,
        suggested_action=exc.suggested_action,
    )
    status = exc.status_code or 400
    return JSONResponse(status_code=status, content=body, headers=headers)


def unexpected_error_to_json(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=build_error_body(
            500,
            "internal_error",
            f"Unexpected server error ({exc.__class__.__name__}). Check the backend logs.",
            suggested_action="Check the backend logs and retry.",
        ),
    )


def unsupported_error_to_json(status_code: int, exc: APIError) -> JSONResponse:
    """Convert a legacy ``HTTPException`` raised with a plain detail string."""
    return JSONResponse(
        status_code=status_code,
        content=build_error_body(
            status_code,
            "request_failed",
            exc.detail or str(exc),
            suggested_action="See the error message and retry.",
        ),
    )


__all__ = [
    "APIError",
    "build_error_body",
    "api_error_to_json",
    "unexpected_error_to_json",
]