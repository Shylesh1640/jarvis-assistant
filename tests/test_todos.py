"""Tests for Phase 8 :: local tasks & reminders.

Covers the TodoRow/TodoRepo persistence, the /todos routes (session
isolation, filters, status transitions, validation), the LangChain tools
(create/list/complete/update/delete + risk classification + registry
binding), and the background reminder worker (due-soon firing, dedupe,
no external sends).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.guardrails import risk as risk_module
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.tools import registry
from jarvis.todos.domain import (
    is_valid_todo_transition,
    normalize_due_at,
    todo_to_dict,
)


@pytest.fixture
def fresh_db(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


def _mk(priority="medium", **kwargs):
    due = kwargs.pop("due_at", None)
    row = repos.todos.create(
        kwargs.pop("todo_id", "t" + datetime.now().strftime("%f")),
        kwargs.pop("session_id", "default"),
        title=kwargs.pop("title", "Buy milk"),
        description=kwargs.pop("description", None),
        priority=priority,
        due_at=due,
    )
    return row


# ---------------------------------------------------------------------------
# Domain rules
# ---------------------------------------------------------------------------


def test_status_transitions():
    assert is_valid_todo_transition("open", "in_progress")
    assert is_valid_todo_transition("open", "completed")
    assert is_valid_todo_transition("open", "cancelled")
    assert is_valid_todo_transition("in_progress", "completed")
    assert is_valid_todo_transition("in_progress", "cancelled")
    assert not is_valid_todo_transition("completed", "open")
    assert not is_valid_todo_transition("cancelled", "in_progress")
    assert not is_valid_todo_transition("open", "open")


def test_normalize_due_at():
    dt = normalize_due_at("2026-08-21T09:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    naive = normalize_due_at("2026-08-21T09:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert normalize_due_at(None) is None
    with pytest.raises(ValueError):
        normalize_due_at("not-a-date")


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------


def test_todo_repo_crud_and_scoping(fresh_db):
    repos.todos.create("t1", "default", title="One", priority="high")
    repos.todos.create("t2", "other", title="Two")
    assert repos.todos.get("default", "t1").todo_id == "t1"
    assert repos.todos.get("other", "t1") is None  # cross-session blocked
    assert repos.todos.get("default", "missing") is None

    rows = repos.todos.list_for_session("default")
    assert [r.todo_id for r in rows] == ["t1"]

    updated = repos.todos.update("default", "t1", title="One! ", status="in_progress")
    assert updated.title == "One! "
    assert updated.status == "in_progress"

    assert repos.todos.soft_delete("default", "t1") is True
    assert repos.todos.get("default", "t1") is None
    assert repos.todos.soft_delete("default", "t1") is False
    assert [r.todo_id for r in repos.todos.list_for_session("default")] == []


def test_todo_repo_status_and_completed_at(fresh_db):
    _mk(todo_id="t1")
    done = repos.todos.set_status("default", "t1", "completed")
    assert done.status == "completed"
    assert done.completed_at is not None
    reopened = repos.todos.update("default", "t1", status="in_progress")
    assert reopened.completed_at is None  # completed_at cleared when leaving


def test_todo_repo_filters(fresh_db):
    now = datetime.now(timezone.utc)
    repos.todos.create("a", "default", title="soon", due_at=now + timedelta(minutes=5))
    repos.todos.create("b", "default", title="later", due_at=now + timedelta(days=2))
    repos.todos.create("c", "default", title="done")
    repos.todos.set_status("default", "c", "completed")
    due_soon = repos.todos.list_for_session("default", due_before=now + timedelta(hours=1))
    assert {r.todo_id for r in due_soon} == {"a"}
    completed = repos.todos.list_for_session("default", status="completed")
    assert [r.todo_id for r in completed] == ["c"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_todo_route_roundtrip(client):
    created = client.post("/todos", json={"title": "Ship Phase 8", "priority": "high"}).json()
    assert created["status"] == "open"
    tid = created["todo_id"]

    got = client.get(f"/todos/{tid}").json()
    assert got["title"] == "Ship Phase 8"

    listed = client.get("/todos").json()
    assert listed["count"] == 1
    assert listed["items"][0]["todo_id"] == tid

    completed = client.post(f"/todos/{tid}/complete").json()
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None

    deleted = client.delete(f"/todos/{tid}").json()
    assert deleted["deleted"] == tid
    assert client.get(f"/todos/{tid}").status_code == 404


def test_todo_route_session_isolation(client):
    client.post("/todos", json={"session_id": "sA", "title": "A task"})
    client.post("/todos", json={"session_id": "sB", "title": "B task"})
    listed_a = client.get("/todos", params={"session_id": "sA"}).json()
    assert [i["title"] for i in listed_a["items"]] == ["A task"]
    missing = client.get("/todos/nope", params={"session_id": "sB"})
    assert missing.status_code == 404


def test_todo_route_validation(client):
    r = client.post("/todos", json={"title": ""})
    assert r.status_code == 422
    r = client.post("/todos", json={"title": "x" * 300})
    assert r.status_code == 422
    r = client.post("/todos", json={"title": "ok", "priority": "urgent"})
    assert r.status_code == 422

    created = client.post("/todos", json={"title": "work"}).json()
    tid = created["todo_id"]
    bad_status = client.patch(f"/todos/{tid}", json={"status": "urgent"})
    assert bad_status.status_code == 422
    # mark in_progress then completed (valid forward chain)
    client.patch(f"/todos/{tid}", json={"status": "in_progress"})
    done = client.patch(f"/todos/{tid}", json={"status": "completed"}).json()
    assert done["status"] == "completed"
    # completed is terminal — no backward transition allowed
    r = client.patch(f"/todos/{tid}", json={"status": "open"})
    assert r.status_code == 422


def test_todo_route_due_filters(client):
    now = datetime.now(timezone.utc)
    client.post("/todos", json={"title": "soon", "due_at": (now + timedelta(minutes=5)).isoformat()})
    client.post("/todos", json={"title": "later", "due_at": (now + timedelta(days=2)).isoformat()})
    due_before = client.get("/todos", params={"due_before": (now + timedelta(hours=1)).isoformat()}).json()
    assert [i["title"] for i in due_before["items"]] == ["soon"]
    status_open = client.get("/todos", params={"status": "open"}).json()
    assert status_open["count"] == 2


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_todo_tools(fresh_db):
    from jarvis.tools.general.todos import (
        complete_todo,
        create_todo,
        delete_todo,
        list_todos,
        update_todo,
    )

    out = create_todo.invoke({"title": "Buy milk", "due_at": "2026-08-21T09:00:00Z"})
    assert "Created todo" in out
    tid = out.split(" ")[2].rstrip(":")

    listing = list_todos.invoke({"session_id": "default"})
    assert "Buy milk" in listing

    assert "Updated" in update_todo.invoke({"todo_id": tid, "status": "in_progress"})
    assert "Completed" in complete_todo.invoke({"todo_id": tid})
    assert "already completed" in complete_todo.invoke({"todo_id": tid})
    r = update_todo.invoke({"todo_id": tid, "status": "in_progress"})
    assert "Error" in r  # completed is terminal

    assert "Deleted" in delete_todo.invoke({"todo_id": tid})
    assert "not found" in delete_todo.invoke({"todo_id": tid})


def test_todo_tool_validation_and_scoping(fresh_db):
    from jarvis.tools.general.todos import create_todo, list_todos

    assert "Error" in create_todo.invoke({"title": "   "})
    assert "Error" in create_todo.invoke({"title": "x" * 500})
    assert "invalid priority" in create_todo.invoke({"title": "x", "priority": "urgent"})
    assert "Error: invalid due_at" in create_todo.invoke({"title": "x", "due_at": "nope"})
    assert "No todos found." in list_todos.invoke({"session_id": "other"})
    assert "No todos found." in list_todos.invoke({"session_id": "other", "status": "open"})
    create_todo.invoke({"title": "scoped", "session_id": "other"})
    assert "scoped" in list_todos.invoke({"session_id": "other"})
    assert "No todos found." in list_todos.invoke({"session_id": "default"})


def test_todo_tool_risk_and_registry(fresh_db):
    assert risk_module.check_tool_risk("list_todos", {}) == "low"
    assert risk_module.check_tool_risk("create_todo", {}) == "medium"
    assert risk_module.check_tool_risk("complete_todo", {}) == "medium"
    assert risk_module.check_tool_risk("update_todo", {}) == "medium"
    assert risk_module.check_tool_risk("delete_todo", {}) == "high"

    names = {t.name for t in registry.GENERAL_TOOLS}
    assert "list_todos" in names
    gated = {t.name for t in registry.APPROVAL_GATED_TOOLS}
    assert {"create_todo", "complete_todo", "update_todo", "delete_todo"} <= gated


# ---------------------------------------------------------------------------
# Reminder worker
# ---------------------------------------------------------------------------


def _reminder_todo(session_id="default", **kwargs):
    now = datetime.now(timezone.utc)
    due = kwargs.get("due_at", now + timedelta(minutes=10))
    return repos.todos.create(
        kwargs.get("todo_id", "r" + datetime.now().strftime("%f")),
        session_id,
        title=kwargs.get("title", "Remind me"),
        due_at=due,
    )


def test_reminder_scan_fires_once(fresh_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.todo_reminder_lookahead_minutes", 60)
    from jarvis.tasks.reminders import scan_once

    _reminder_todo("s1", title="Pay rent", due_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    result = scan_once()
    assert result["fired"] == 1
    assert result["sessions"] == 1

    # The reminder was written into the session as an assistant message.
    msgs = repos.messages.tail("s1", limit=5)
    assert any("Pay rent" in (m.get("content") or "") and m.get("role") == "assistant" for m in msgs)

    # Second scan must not re-fire (dedupe via last_reminded_at).
    assert scan_once()["fired"] == 0


def test_reminder_scan_skips_non_due(fresh_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.todo_reminder_lookahead_minutes", 30)
    from jarvis.tasks.reminders import scan_once

    now = datetime.now(timezone.utc)
    _reminder_todo("s1", title="Far future", due_at=now + timedelta(days=3))
    _reminder_todo("s1", title="Past due", due_at=now - timedelta(hours=1))
    assert scan_once()["fired"] == 0


def test_reminder_scan_skips_deleted_and_completed(fresh_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.todo_reminder_lookahead_minutes", 60)
    from jarvis.tasks.reminders import scan_once

    now = datetime.now(timezone.utc)
    a = _reminder_todo("s1", title="Deleted", due_at=now + timedelta(minutes=5))
    repos.todos.soft_delete("s1", a.todo_id)
    b = _reminder_todo("s1", title="Completed", due_at=now + timedelta(minutes=5))
    repos.todos.set_status("s1", b.todo_id, "completed")
    _reminder_todo("s1", title="Active", due_at=now + timedelta(minutes=5))
    assert scan_once()["fired"] == 1


def test_reminder_scan_grouped_by_session(fresh_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.todo_reminder_lookahead_minutes", 60)
    from jarvis.tasks.reminders import scan_once

    now = datetime.now(timezone.utc)
    _reminder_todo("s1", title="One", due_at=now + timedelta(minutes=5))
    _reminder_todo("s2", title="Two", due_at=now + timedelta(minutes=5))
    result = scan_once()
    assert result["fired"] == 2
    assert result["sessions"] == 2


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_todo_to_dict_shape(fresh_db):
    row = repos.todos.create("t1", "default", title="shape", due_at=datetime.now(timezone.utc))
    d = todo_to_dict(row)
    for key in (
        "todo_id",
        "session_id",
        "title",
        "description",
        "status",
        "priority",
        "due_at",
        "created_at",
        "updated_at",
        "completed_at",
        "source_request_id",
    ):
        assert key in d