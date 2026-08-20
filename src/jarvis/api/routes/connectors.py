"""Routes for external connectors (Phase 8).

* ``GET    /connectors``                  — list enabled connectors (sanitised)
* ``GET    /connectors/{id}``             — one connector + health check
* ``POST   /connectors/{id}/execute?confirm=1`` — run a connector (write; needs
  confirm — never auto-executed)

Responses never include the raw ``config`` dict (credentials stay out of
responses and logs). With connectors disabled or unconfigured the routes
return a structured ``503 connector_not_configured`` response.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.errors import APIError
from jarvis.connectors import (
    get_connector,
    list_connectors,
    not_configured_message,
)
from jarvis.config.settings import settings
from jarvis.persistence import create_all
from jarvis.security.session_auth import ensure_session_context

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectorExecutePayload(BaseModel):
    input: dict = Field(default_factory=dict)
    session_id: str = "default"
    session_token: str | None = None


def _require_enabled_or_503() -> None:
    if not settings.connectors_enabled:
        raise APIError(503, "connector_not_configured", not_configured_message())


def _connector_or_503(connector_id: str):
    _require_enabled_or_503()
    instance = get_connector(connector_id)
    if instance is None:
        raise APIError(503, "connector_not_configured", not_configured_message())
    return instance


@router.get("")
def connectors_list(
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    ensure_session_context(session_id or "default", session_token)
    _require_enabled_or_503()
    try:
        create_all()
    except Exception:  # noqa: BLE001
        pass
    return {"items": list_connectors(), "count": len(list_connectors())}


@router.get("/{connector_id}")
def connectors_get(
    connector_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    ensure_session_context(session_id or "default", session_token)
    instance = _connector_or_503(connector_id)
    from jarvis.connectors import load_connectors_config

    metadata = None
    for c in load_connectors_config():
        if c.id == connector_id:
            metadata = {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "type": c.type,
                "enabled": c.enabled,
            }
            break
    health = {}
    try:
        health = instance.health_check()
    except Exception as exc:  # noqa: BLE001
        health = {"ok": False, "detail": f"health check failed ({exc.__class__.__name__})"}
    return {"connector": metadata, "health": health}


@router.post("/{connector_id}/execute")
def connectors_execute(
    connector_id: str,
    payload: ConnectorExecutePayload,
    confirm: bool = False,
) -> dict:
    ensure_session_context(payload.session_id, payload.session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to execute this connector.",
        )
    instance = _connector_or_503(connector_id)
    try:
        result = instance.execute(payload.input)
    except Exception as exc:  # noqa: BLE001
        raise APIError(
            502,
            "connector_execution_failed",
            f"Connector execution failed ({exc.__class__.__name__}). Check the backend logs.",
        ) from exc
    return {"connector_id": connector_id, "result": result}