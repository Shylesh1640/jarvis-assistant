"""Tests for Phase 8 :: CLI (jarvis-todo, jarvis-calendar, jarvis-email).

Exercises the argparse CLIs directly (no subprocess) against the in-memory
DB and a mock calendar/email provider. Writes are confirmed via ``--yes`` so
the tests never block on stdin.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jarvis.calendar import CALENDAR_PROVIDERS
from jarvis.calendar.base import register_provider as register_calendar_provider
from jarvis.cli import calendar as calendar_cli
from jarvis.cli import email as email_cli
from jarvis.cli import todo as todo_cli
from jarvis.email import EMAIL_PROVIDERS
from jarvis.email.base import register_provider as register_email_provider
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests


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
    from tests.test_calendar import MockCalendarProvider

    register_calendar_provider("mock", MockCalendarProvider)
    MockCalendarProvider.reset()
    monkeypatch.setattr("jarvis.config.settings.settings.calendar_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.calendar_provider", "mock")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.calendar_default_calendar_id", "primary"
    )
    yield
    CALENDAR_PROVIDERS.pop("mock", None)


@pytest.fixture
def email_on(monkeypatch):
    from tests.test_email_drafts import MockEmailProvider

    register_email_provider("mock", MockEmailProvider)
    MockEmailProvider.reset()
    monkeypatch.setattr("jarvis.config.settings.settings.email_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.email_provider", "mock")
    monkeypatch.setattr("jarvis.config.settings.settings.email_default_from", "me@example.com")
    yield
    EMAIL_PROVIDERS.pop("mock", None)


# ---------------------------------------------------------------------------
# jarvis-todo
# ---------------------------------------------------------------------------


def test_todo_cli_roundtrip(fresh_db):
    assert todo_cli.main(["add", "Buy milk", "--priority", "high", "--yes"]) == 0
    todos = repos.todos.list_for_session("default")
    assert len(todos) == 1
    assert todos[0].priority == "high"

    assert todo_cli.main(["list"]) == 0
    assert todo_cli.main(["complete", todos[0].todo_id, "--yes"]) == 0
    assert repos.todos.get("default", todos[0].todo_id).status == "completed"

    assert todo_cli.main(["delete", todos[0].todo_id, "--yes"]) == 0
    assert repos.todos.get("default", todos[0].todo_id) is None


def test_todo_cli_validation_and_scoping(fresh_db):
    assert todo_cli.main(["add", "   ", "--yes"]) == 2
    assert todo_cli.main(["add", "x", "--priority", "urgent", "--yes"]) == 2
    assert todo_cli.main(["add", "x", "--due", "nope", "--yes"]) == 2
    assert todo_cli.main(["complete", "missing", "--yes"]) == 1
    assert todo_cli.main(["delete", "missing", "--yes"]) == 1

    assert todo_cli.main(["add", "scoped", "--session", "s9", "--yes"]) == 0
    assert len(repos.todos.list_for_session("s9")) == 1
    assert len(repos.todos.list_for_session("default")) == 0


def test_todo_cli_list_filters(fresh_db):
    todo_cli.main(["add", "Open", "--yes"])
    assert todo_cli.main(["list", "--status", "open"]) == 0
    assert todo_cli.main(["list", "--status", "completed"]) == 0
    assert todo_cli.main(["list", "--status", "bogus"]) == 2


# ---------------------------------------------------------------------------
# jarvis-calendar
# ---------------------------------------------------------------------------


def test_calendar_cli_not_configured(fresh_db):
    assert calendar_cli.main(["list"]) == 1


def test_calendar_cli_roundtrip(fresh_db, calendar_on):
    start = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (datetime.now() + timedelta(days=1, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert calendar_cli.main(["add", "Sync", "--start", start, "--end", end, "--yes"]) == 0
    assert calendar_cli.main(["list"]) == 0
    assert calendar_cli.main(["add", "Bad", "--start", end, "--end", start, "--yes"]) == 2


# ---------------------------------------------------------------------------
# jarvis-email
# ---------------------------------------------------------------------------


def test_email_cli_roundtrip(fresh_db):
    assert email_cli.main(["draft", "--subject", "Hello", "--recipients", "a@b.com,c@d.com"]) == 0
    drafts = repos.email_drafts.list_for_session("default")
    assert len(drafts) == 1
    assert drafts[0].recipients == ["a@b.com", "c@d.com"]

    assert email_cli.main(["list"]) == 0
    assert email_cli.main(["send", drafts[0].draft_id, "--yes"]) == 1  # no provider

    assert email_cli.main(["draft", "--subject", "x", "--recipients", "nope"]) == 2


def test_email_cli_send_with_provider(fresh_db, email_on):
    from tests.test_email_drafts import MockEmailProvider

    email_cli.main(["draft", "--subject", "Sends", "--recipients", "a@b.com"])
    drafts = repos.email_drafts.list_for_session("default")
    assert email_cli.main(["send", drafts[0].draft_id, "--yes"]) == 0
    assert MockEmailProvider.sent[0]["subject"] == "Sends"
    assert repos.email_drafts.get("default", drafts[0].draft_id).status == "sent"