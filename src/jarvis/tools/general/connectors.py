"""LangChain tools for external connectors (Phase 8).

``list_connectors`` is a safe read. ``run_connector`` executes a configured
connector against an external service — it is approval-gated (high risk) and,
without connectors enabled/config, returns a structured "not configured"
response and never touches the outside world.
"""
from __future__ import annotations

from langchain_core.tools import tool

from jarvis.connectors import get_connector as _resolve_connector
from jarvis.connectors import list_connectors as _list_connectors
from jarvis.connectors import not_configured_message


@tool
def list_connectors() -> str:
    """List enabled external connectors (ids + names). Read-only and safe."""
    items = _list_connectors()
    if not items:
        return not_configured_message()
    return "\n".join(f"[{c['id']}] {c['name']} — {c['description']}" for c in items)


@tool
def run_connector(connector_id: str, input: dict) -> str:
    """Execute a configured external connector with the given JSON input.

    Requires approval. Fails with a structured message (never a network call)
    when connectors are disabled or the connector is not configured.
    """
    instance = _resolve_connector(connector_id)
    if instance is None:
        return not_configured_message()
    try:
        result = instance.execute(input or {})
    except Exception as exc:  # noqa: BLE001
        return f"Error: connector execution failed ({exc.__class__.__name__})."
    return f"Connector {connector_id} returned: {result}"


__all__ = ["list_connectors", "run_connector"]