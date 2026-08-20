"""External connector abstraction (Phase 8).

Connectors let the assistant reach a configured external service (issue
tracker, notes app, home-automation hub, ...). Each connector is a
:class:`Connector` implementation registered in :data:`CONNECTORS` and
instantiated from a row in the ``CONNECTORS_CONFIG_PATH`` JSON file.

Safety rules enforced here and by the routes:
* connectors are **off by default** (``CONNECTORS_ENABLED=false``);
* responses never include the raw ``config`` dict (it may hold credentials);
* execute is treated as a write — the route requires ``?confirm=1`` and the
  tool is approval-gated;
* nothing is ever logged about what a connector did beyond its id.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jarvis.config.settings import settings

logger = logging.getLogger("jarvis.connectors")

# Connector *type* name -> implementation class.
CONNECTORS: dict[str, type["Connector"]] = {}


class ConnectorConfig(BaseModel):
    """One configured connector (from the connectors JSON file)."""

    id: str = Field(..., min_length=1, max_length=128)
    name: str = Field("", max_length=200)
    description: str = Field("", max_length=1000)
    # Registry key that maps to a Connector implementation.
    type: str = Field(..., min_length=1, max_length=128)
    # Implementation-specific options. MAY contain credentials — sanitised
    # before it ever reaches a response.
    config: dict = Field(default_factory=dict)
    enabled: bool = True


@runtime_checkable
class Connector(Protocol):
    """The operations a connector must implement."""

    def health_check(self) -> dict:
        """Return {"ok": bool, "detail": str}; never includes credentials."""
        ...

    def execute(self, input: dict) -> dict:
        """Run the connector with a caller-supplied payload."""
        ...


def register_connector(name: str, cls: type[Connector]) -> None:
    """Register a connector implementation under a config-file type name."""
    CONNECTORS[name] = cls


def _config_file() -> Path | None:
    path = settings.connectors_config_path
    if not path:
        return None
    return Path(path)


def load_connectors_config() -> list[ConnectorConfig]:
    """Load configured connectors from disk; [] when missing/invalid."""
    path = _config_file()
    if path is None or not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("connectors", []) if isinstance(raw, dict) else raw
        return [ConnectorConfig.model_validate(row) for row in rows if isinstance(row, dict)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load connectors config %s: %s", path, exc)
        return []


def _sanitize(config: ConnectorConfig) -> dict:
    """Public metadata for a connector — never includes the raw config."""
    return {
        "id": config.id,
        "name": config.name,
        "description": config.description,
        "type": config.type,
        "enabled": config.enabled,
    }


def list_connectors() -> list[dict]:
    """Sanitised metadata for every configured connector."""
    return [_sanitize(c) for c in load_connectors_config() if c.enabled]


def get_connector(connector_id: str):
    """Resolve an enabled connector's instance + config, or None.

    None when connectors are disabled, the id is unknown, the type is not
    registered, or the connector is disabled.
    """
    if not settings.connectors_enabled:
        return None
    for config in load_connectors_config():
        if config.id != connector_id or not config.enabled:
            continue
        cls = CONNECTORS.get(config.type)
        if cls is None:
            return None
        try:
            return cls(config.config, settings)
        except Exception:  # noqa: BLE001
            return None
    return None


def not_configured_message() -> str:
    """Structured, user-actionable reason connectors are unavailable."""
    if not settings.connectors_enabled:
        return (
            "Connectors are not configured: set CONNECTORS_ENABLED=true to "
            "enable external connectors."
        )
    return (
        "Connectors are not configured: define the connector in "
        f"{settings.connectors_config_path}."
    )


__all__ = [
    "CONNECTORS",
    "Connector",
    "ConnectorConfig",
    "get_connector",
    "list_connectors",
    "load_connectors_config",
    "not_configured_message",
    "register_connector",
]