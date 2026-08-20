"""Tests for Phase 8 :: calendar provider abstraction.

Covers the provider registry + resolution, the /calendar routes (structured
"not configured" responses, confirm-gated writes, validation), the LangChain
tools, and risk/registry wiring. All tests use a mock provider — no network.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.calendar import CALENDAR_PROVIDERS, CalendarEvent, get_provider
from jarvis.calendar.base import register_provider
from jarvis.guardrails import risk as risk_module
from jarvis.persistence import create_all
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.tools import registry


class MockCalendarProvider:
    """In-memory fake used by the tests; no credentials, no network."""

    events: dict[str, CalendarEvent] = {}

    def __init__(self, settings=None) -> None:
        self._settings = settings

    @classmethod
    def reset(cls) -> None:
        cls.events = {}

    def health_check(self) -> dict:
        return {"ok": True, "detail": "mock provider"}

    def list_calendars(self) -> list[dict]:
        return [{"calendar_id": "primary", "summary": "Primary", "timezone": "UTC"}]

    def list_events(self, *, start=None, end=None, calendar_id=None):
        items = []
        for eid, event in self.events.items():
            if calendar_id and event.calendar_id != calendar_id:
                continue
            if start and event.end and event.end < start:
                continue
            if end and event.start and event.start > end:
                continue
            items.append(event)
        return sorted(items, key=lambda e: e.start or datetime.min)

    def create_event(self, calendar_id, event):
        eid = f"evt{len(self.events) + 1}"
        event.event_id = eid
        event.calendar_id = calendar_id or event.calendar_id
        self.events[eid] = event
        return eid

    def update_event(self, event_id, event):
        existing = self.events.get(event_id)
        if existing is None:
            return event_id
        if event.summary:
            existing.summary = event.summary
        if event.start:
            existing.start = event.start
        if event.end:
            existing.end = event.end
        if event.description is not None:
            existing.description = event.description
        if event.location is not None:
            existing.location = event.location
        return event_id

    def delete_event(self, event_id):
        return self.events.pop(event_id, None) is not None


@pytest.fixture
def fresh_db(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


@pytest.fixture
def calendar_on(monkeypatch):
    register_provider("mock", MockCalendarProvider)
    MockCalendarProvider.reset()
    monkeypatch.setattr("jarvis.config.settings.settings.calendar_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.calendar_provider", "mock")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.calendar_default_calendar_id", "primary"
    )
    yield
    CALENDAR_PROVIDERS.pop("mock", None)


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Registry / resolution
# ---------------------------------------------------------------------------


def test_provider_disabled_by_default(fresh_db):
    from jarvis.calendar import not_configured_message

    assert get_provider() is None
    assert "not configured" in not_configured_message()


def test_provider_resolution(calendar_on):
    provider = get_provider()
    assert provider is not None
    assert isinstance(provider, MockCalendarProvider)


def test_provider_unknown_name(monkeypatch, fresh_db):
    monkeypatch.setattr("jarvis.config.settings.settings.calendar_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.calendar_provider", "ghost")
    assert get_provider() is None


# ---------------------------------------------------------------------------
# Routes — not configured
# ---------------------------------------------------------------------------


def test_calendar_routes_not_configured(client):
    r = client.get("/calendar/calendars")
    assert r.status_code == 503
    assert r.json()["error"] == "calendar_not_configured"
    r = client.post("/calendar/events", json={"summary": "x", "start": "2026-08-21T10:00:00Z", "end": "2026-08-21T11:00:00Z"}, params={"confirm": 1})
    assert r.status_code == 503
    assert r.json()["error"] == "calendar_not_configured"


# ---------------------------------------------------------------------------
# Routes — configured
# ---------------------------------------------------------------------------


def test_calendar_routes_write_need_confirm(client, calendar_on):
    payload = {
        "summary": "Meeting",
        "start": "2026-08-21T10:00:00Z",
        "end": "2026-08-21T11:00:00Z",
    }
    r = client.post("/calendar/events", json=payload)
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_required"


def test_calendar_route_roundtrip(client, calendar_on):
    payload = {
        "summary": "Sprint review",
        "start": "2026-08-21T10:00:00Z",
        "end": "2026-08-21T11:00:00Z",
        "description": "demo",
    }
    created = client.post("/calendar/events", json=payload, params={"confirm": 1})
    assert created.status_code == 200
    eid = created.json()["event_id"]

    listed = client.get("/calendar/events").json()
    assert listed["count"] == 1
    assert listed["items"][0]["summary"] == "Sprint review"

    updated = client.patch(
        f"/calendar/events/{eid}",
        json={"summary": "Sprint review (moved)"},
        params={"confirm": 1},
    )
    assert updated.status_code == 200

    # end <= start rejected
    bad = client.post(
        "/calendar/events",
        json={"summary": "bad", "start": "2026-08-21T11:00:00Z", "end": "2026-08-21T10:00:00Z"},
        params={"confirm": 1},
    )
    assert bad.status_code == 422

    deleted = client.delete(f"/calendar/events/{eid}", params={"confirm": 1})
    assert deleted.status_code == 200
    assert client.delete(f"/calendar/events/{eid}", params={"confirm": 1}).status_code == 404


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_calendar_tools_not_configured(fresh_db):
    from jarvis.tools.general.calendar import create_event, list_events

    out = list_events.invoke({})
    assert "not configured" in out
    out = create_event.invoke(
        {"summary": "x", "start": "2026-08-21T10:00:00Z", "end": "2026-08-21T11:00:00Z"}
    )
    assert "not configured" in out


def test_calendar_tools_roundtrip(fresh_db, calendar_on):
    from jarvis.tools.general.calendar import (
        create_event,
        delete_event,
        list_events,
        update_event,
    )

    out = create_event.invoke(
        {"summary": "Standup", "start": "2026-08-21T10:00:00Z", "end": "2026-08-21T10:30:00Z"}
    )
    assert "Created calendar event" in out
    eid = out.split(" ")[3].rstrip(":")

    listing = list_events.invoke({})
    assert "Standup" in listing

    assert "Updated" in update_event.invoke({"event_id": eid, "summary": "Standup daily"})
    assert "Standup daily" in list_events.invoke({})

    assert "Deleted" in delete_event.invoke({"event_id": eid})
    assert "not found" in delete_event.invoke({"event_id": eid})


def test_calendar_tool_validation(fresh_db, calendar_on):
    from jarvis.tools.general.calendar import create_event

    assert "Error" in create_event.invoke(
        {"summary": "  ", "start": "2026-08-21T10:00:00Z", "end": "2026-08-21T11:00:00Z"}
    )
    assert "Error" in create_event.invoke(
        {"summary": "x", "start": "nope", "end": "2026-08-21T11:00:00Z"}
    )
    assert "Error" in create_event.invoke(
        {"summary": "x", "start": "2026-08-21T11:00:00Z", "end": "2026-08-21T10:00:00Z"}
    )


def test_calendar_tool_risk_and_registry(fresh_db):
    assert risk_module.check_tool_risk("list_calendars", {}) == "low"
    assert risk_module.check_tool_risk("list_events", {}) == "low"
    assert risk_module.check_tool_risk("create_event", {}) == "medium"
    assert risk_module.check_tool_risk("update_event", {}) == "medium"
    assert risk_module.check_tool_risk("delete_event", {}) == "high"

    general = {t.name for t in registry.GENERAL_TOOLS}
    assert {"list_calendars", "list_events"} <= general
    gated = {t.name for t in registry.APPROVAL_GATED_TOOLS}
    assert {"create_event", "update_event", "delete_event"} <= gated