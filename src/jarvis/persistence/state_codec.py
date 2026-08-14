"""Serialise LangGraph state to JSON-safe dicts and back.

A paused-approval snapshot contains LangChain ``BaseMessage`` objects inside
``state["messages"]``. SQLAlchemy JSON columns cannot store those directly,
so we round-trip them through ``langchain_core.load`` (``dumpd``/``loads``).
Any value that can't be serialised is replaced with a best-effort string so a
single exotic object never breaks approval persistence.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.load import dumpd, loads
from langchain_core.messages import BaseMessage

logger = logging.getLogger("jarvis.persistence.state_codec")


def state_to_json(state: dict[str, Any]) -> dict[str, Any]:
    """Convert *state* into a JSON-serialisable dict (never raises)."""
    out: dict[str, Any] = {}
    for key, value in state.items():
        if key == "messages":
            out[key] = [_message_to_json(m) for m in (value or [])]
        elif isinstance(value, dict):
            out[key] = _plain(value)
        elif isinstance(value, list):
            out[key] = [_plain(v) if isinstance(v, dict) else v for v in value]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            # Unknown object (e.g. a pydantic model): keep it usable.
            out[key] = str(value)
    return out


def state_from_json(data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a graph-run-able state dict from a stored snapshot."""
    state: dict[str, Any] = dict(data or {})
    messages = state.get("messages")
    if isinstance(messages, list):
        state["messages"] = [
            _message_from_json(m) for m in messages if isinstance(m, dict)
        ]
    return state


def _message_to_json(msg: Any) -> Any:
    if isinstance(msg, BaseMessage):
        try:
            return dumpd(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not serialise message of type %s: %s", type(msg).__name__, exc)
            return {"type": "text", "text": str(getattr(msg, "content", msg))}
    return _plain(msg)


def _message_from_json(data: dict[str, Any]) -> Any:
    if data.get("type") == "text" and "text" in data:
        return data["text"]
    try:
        import json

        return loads(json.dumps(data))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not deserialise message snapshot: %s", exc)
        return data


def _plain(value: dict[str, Any]) -> dict[str, Any]:
    """Recursively coerce *value* to JSON-safe primitives (best effort)."""
    out: dict[str, Any] = {}
    for k, v in value.items():
        if isinstance(v, dict):
            out[k] = _plain(v)
        elif isinstance(v, list):
            out[k] = [_plain(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


__all__ = ["state_to_json", "state_from_json"]
