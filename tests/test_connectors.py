"""Tests for Phase 8 :: external connectors.

Covers the connector registry + config loading, the /connectors routes
(sanitised responses — no credentials leaked, confirm-gated execute,
structured "not configured"), the LangChain tools, and risk/registry wiring.
Uses a mock connector and a temp config file — no external services.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.connectors import CONNECTORS, get_connector, list_connectors
from jarvis.connectors.base import register_connector
from jarvis.guardrails import risk as risk_module
from jarvis.persistence import create_all
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.tools import registry


class MockConnector:
    calls: list[dict] = []

    def __init__(self, config=None, settings=None) -> None:
        self.config = config or {}
        self._settings = settings

    @classmethod
    def reset(cls) -> None:
        cls.calls = []

    def health_check(self) -> dict:
        return {"ok": True, "detail": "mock connector"}

    def execute(self, input: dict) -> dict:
        self.calls.append(input)
        return {"echo": input, "repo": self.config.get("repo")}


@pytest.fixture
def fresh_db(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


@pytest.fixture
def connectors_on(tmp_path, monkeypatch):
    register_connector("mock", MockConnector)
    MockConnector.reset()
    monkeypatch.setattr("jarvis.config.settings.settings.connectors_enabled", True)
    config_file = tmp_path / "connectors.json"
    config_file.write_text(
        json.dumps(
            {
                "connectors": [
                    {
                        "id": "notes",
                        "name": "Notes",
                        "description": "Read/write notes",
                        "type": "mock",
                        "config": {"repo": "org/repo", "token": "super-secret"},
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("jarvis.config.settings.settings.connectors_config_path", str(config_file))
    yield
    CONNECTORS.pop("mock", None)


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Config / resolution
# ---------------------------------------------------------------------------


def test_connectors_disabled_by_default(fresh_db):
    assert list_connectors() == []
    assert get_connector("notes") is None
    from jarvis.connectors import not_configured_message

    assert "not configured" in not_configured_message()


def test_connector_resolution(connectors_on):
    items = list_connectors()
    assert len(items) == 1
    assert items[0]["id"] == "notes"
    assert "config" not in items[0]
    assert "token" not in json.dumps(items)  # credentials never leak
    assert get_connector("notes") is not None
    assert get_connector("missing") is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_connector_routes_not_configured(client):
    r = client.get("/connectors")
    assert r.status_code == 503
    assert r.json()["error"] == "connector_not_configured"
    r = client.post(
        "/connectors/notes/execute",
        json={"input": {"x": 1}},
        params={"confirm": 1},
    )
    assert r.status_code == 503
    assert r.json()["error"] == "connector_not_configured"


def test_connector_routes_roundtrip(client, connectors_on):
    listed = client.get("/connectors").json()
    assert listed["count"] == 1
    assert listed["items"][0]["id"] == "notes"
    assert "config" not in listed["items"][0]
    assert "token" not in json.dumps(listed)

    got = client.get("/connectors/notes").json()
    assert got["connector"]["id"] == "notes"
    assert got["health"]["ok"] is True

    # execute requires confirm
    r = client.post("/connectors/notes/execute", json={"input": {"op": "list"}})
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_required"

    r = client.post(
        "/connectors/notes/execute",
        json={"input": {"op": "list"}},
        params={"confirm": 1},
    )
    assert r.status_code == 200
    assert r.json()["result"]["repo"] == "org/repo"
    assert MockConnector.calls == [{"op": "list"}]


def test_connector_routes_unknown_id(client, connectors_on):
    r = client.get("/connectors/ghost")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_connector_tools_not_configured(fresh_db):
    from jarvis.tools.general.connectors import list_connectors, run_connector

    assert "not configured" in list_connectors.invoke({})
    assert "not configured" in run_connector.invoke({"connector_id": "notes", "input": {}})


def test_connector_tools_roundtrip(fresh_db, connectors_on):
    from jarvis.tools.general.connectors import list_connectors, run_connector

    listing = list_connectors.invoke({})
    assert "notes" in listing
    assert "token" not in listing  # credentials never leak

    result = run_connector.invoke({"connector_id": "notes", "input": {"op": "add"}})
    assert "org/repo" in result
    assert MockConnector.calls == [{"op": "add"}]

    missing = run_connector.invoke({"connector_id": "ghost", "input": {}})
    assert "not configured" in missing


def test_connector_tool_risk_and_registry(fresh_db):
    assert risk_module.check_tool_risk("list_connectors", {}) == "low"
    assert risk_module.check_tool_risk("run_connector", {}) == "high"

    general = {t.name for t in registry.GENERAL_TOOLS}
    assert "list_connectors" in general
    gated = {t.name for t in registry.APPROVAL_GATED_TOOLS}
    assert "run_connector" in gated