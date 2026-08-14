"""Structured JSON logging setup.

Jarvis already emits machine-parseable one-line records for the four log
streams operators care about, each prefixed so it can be filtered:

* ``model_request | branch=... model=... duration_ms=...``  (models)
* ``coding tool ...`` / ``tool ...``                          (tool calls)
* ``approval ...`` / ``Approval gate ...``                    (approvals)
* ``trace | ...`` and ``... error ...``                       (errors/tracing)

``setup_logging()`` swaps the stream handler for a JSON one-liner when
``settings.json_logs_enabled`` is true, so every log record (including the
prefixes above) becomes a single parseable JSON object. Disabled by default
so the console stays human-readable.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from jarvis.config.settings import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging() -> None:
    """Configure root logging; a no-op unless ``JSON_LOGS_ENABLED`` is true."""
    root = logging.getLogger()
    formatter: logging.Formatter = JsonFormatter() if settings.json_logs_enabled else None
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            if formatter is not None:
                handler.setFormatter(formatter)
            break
    else:
        handler = logging.StreamHandler()
        if formatter is not None:
            handler.setFormatter(formatter)
        root.addHandler(handler)


__all__ = ["JsonFormatter", "setup_logging"]