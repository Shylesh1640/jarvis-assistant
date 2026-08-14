"""Tests for durable pending approvals surviving a "backend restart".

Simulates a restart by clearing the in-memory caches and proving the resume
path rebuilds state from the ``approvals`` table.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.guardrails.output_guard import redact_output
from jarvis.observability import clear_traces
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests


@pytest.fixture
def client(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr(routes.chat, "jarvis_graph", _StubGraph())
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    routes.chat._db_ready = False
    clear_traces()
    yield TestClient(app)


class _StubGraph:
    def invoke(self, state, config=None):
        state["final_response"] = "performed after approval"
        state["selected_path"] = "coding"
        state["selected_model"] = "qwen3:8b"
        state.setdefault("tools_used", [])
        state.setdefault("sources", [])
        state["approval_required"] = False
        state["pending_action"] = None
        state["pending_tool_calls"] = []
        return state


def _pending_state(approval_id: str = "appv-restart-1") -> dict:
    return {
        "user_input": "write the file",
        "approval_id": approval_id,
        "approval_required": True,
        "approval_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "risk_level": "medium",
        "pending_action": "write_file -> C:/tmp/x.txt",
        "pending_tool_calls": [
            {"name": "write_file", "args": {"file_path": "C:/tmp/x.txt", "content": "hi"}}
        ],
        "session_id": "restart-session",
    }


def test_resume_rebuilds_from_db_after_restart(client):
    # Phase 1: a pending approval was persisted to the DB (as if the old
    # backend had stored it before going down).
    repos.approvals.create(
        _pending_state()["approval_id"],
        session_id="restart-session",
        state=_pending_state(),
        expires_at=_pending_state()["approval_expires_at"],
        tool_name="write_file",
        arguments={"file_path": "C:/tmp/x.txt", "content": "hi"},
        pending_action=_pending_state()["pending_action"],
        risk_level="medium",
    )

    # "Restart": in-memory caches are empty, but the DB row survives.
    routes.chat._pending_approvals.clear()

    r = client.post(
        "/chat",
        json={
            "message": "ignored",
            "session_id": "restart-session",
            "approved": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["response"] == "performed after approval"
    assert body["approval_required"] is False


def test_resume_from_db_marks_row_approved(client):
    repos.approvals.create(
        "appv-restart-2",
        session_id="restart-session",
        state=_pending_state("appv-restart-2"),
        expires_at=_pending_state("appv-restart-2")["approval_expires_at"],
    )
    routes.chat._pending_approvals.clear()

    client.post(
        "/chat",
        json={
            "message": "ignored",
            "session_id": "restart-session",
            "approved": True,
        },
    )

    row = repos.approvals.get("appv-restart-2")
    assert row is not None
    assert row.status == "approved"


def test_expired_approval_in_db_is_rejected(client):
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    repos.approvals.create(
        "appv-restart-expired",
        session_id="restart-session",
        state={**_pending_state("appv-restart-expired"), "approval_expires_at": past},
        expires_at=past,
    )
    routes.chat._pending_approvals.clear()

    r = client.post(
        "/chat",
        json={
            "message": "ignored",
            "session_id": "restart-session",
            "approved": True,
        },
    )
    assert r.status_code == 410
    assert r.json()["error"] == "approval_expired"


def test_fresh_message_cancels_durable_pending(client):
    repos.approvals.create(
        "appv-restart-3",
        session_id="restart-session",
        state=_pending_state(),
        expires_at=_pending_state()["approval_expires_at"],
    )
    client.post(
        "/chat",
        json={
            "message": "brand new question",
            "session_id": "restart-session",
            "approved": False,
        },
    )
    row = repos.approvals.get("appv-restart-3")
    assert row is not None
    assert row.status == "cancelled"


def test_purge_expired_marks_rows(client):
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    repos.approvals.create(
        "appv-purge-1",
        session_id="s1",
        state=_pending_state(),
        expires_at=past,
    )
    repos.approvals.create(
        "appv-purge-2",
        session_id="s2",
        state=_pending_state(),
        expires_at=_pending_state()["approval_expires_at"],
    )
    n = repos.approvals.purge_expired()
    assert n == 1
    assert repos.approvals.get("appv-purge-1").status == "expired"
    assert repos.approvals.get("appv-purge-2").status == "pending"


def test_database_history_rebuilds_after_restart(client):
    repos.sessions.get_or_create("hist-session")
    repos.messages.add(
        "hist-session",
        role="user",
        content="remember this",
        path_used="general",
        model_used="qwen3:8b",
    )
    repos.messages.add(
        "hist-session",
        role="assistant",
        content="remembered",
        path_used="general",
        model_used="qwen3:8b",
    )

    # "Restart": in-memory session cache is gone.
    routes.chat._sessions.clear()

    r = client.post(
        "/chat",
        json={
            "message": "what did I say?",
            "session_id": "hist-session",
            "approved": False,
        },
    )
    assert r.status_code == 200
    # The route rebuilds history from the DB and passes it to the graph;
    # our stub ignores it, so we verify indirectly that the cache now holds
    # the rebuilt history (2 from DB + the 2 appended for this turn).
    assert len(routes.chat._sessions["hist-session"]) == 4
    assert routes.chat._sessions["hist-session"][0]["content"] == "remember this"


def test_redacted_response_stored_in_db_history(client):
    repos.approvals.create(
        "appv-redact",
        session_id="restart-session",
        state=_pending_state(),
        expires_at=_pending_state()["approval_expires_at"],
    )
    routes.chat._pending_approvals.clear()

    client.post(
        "/chat",
        json={
            "message": "ignored",
            "session_id": "restart-session",
            "approved": True,
        },
    )

    history = repos.messages.history("restart-session")
    stored = [m["content"] for m in history]
    assert any(c == redact_output("performed after approval") for c in stored)