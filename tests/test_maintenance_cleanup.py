"""Tests for periodic maintenance: session message counts, inactive-session
TTL cleanup, expired-approval hard delete, and the chat deny path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
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


def _expiry(minutes: int = 10) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _pending_state(approval_id: str) -> dict:
    return {
        "user_input": "write the file",
        "approval_id": approval_id,
        "approval_required": True,
        "approval_expires_at": _expiry(),
        "risk_level": "medium",
        "pending_action": "write_file -> C:/tmp/x.txt",
        "pending_tool_calls": [
            {"name": "write_file", "args": {"file_path": "C:/tmp/x.txt", "content": "hi"}}
        ],
        "session_id": "sweep-session",
    }


# ---------------------------------------------------------------------------
# Session metadata: message_count
# ---------------------------------------------------------------------------


def test_session_metadata_includes_message_count(client):
    repos.sessions.get_or_create("meta-session")
    repos.messages.add("meta-session", role="user", content="hello")
    repos.messages.add("meta-session", role="assistant", content="hi there")

    r = client.get("/sessions/meta-session")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "meta-session"
    assert body["created_at"] is not None
    assert body["last_active_at"] is not None
    assert body["message_count"] == 2


def test_empty_session_reports_zero_messages(client):
    repos.sessions.get_or_create("empty-session")
    r = client.get("/sessions/empty-session")
    assert r.status_code == 200
    assert r.json()["message_count"] == 0


def test_session_list_metadata(client):
    repos.sessions.get_or_create("list-session")
    repos.messages.add("list-session", role="user", content="x")
    r = client.get("/sessions")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    row = next((s for s in sessions if s["session_id"] == "list-session"), None)
    assert row is not None
    assert row["message_count"] == 1


# ---------------------------------------------------------------------------
# Inactive-session cleanup
# ---------------------------------------------------------------------------


def _stale_session(session_id: str, days_ago: int) -> None:
    repos.sessions.get_or_create(session_id)
    from jarvis.persistence.models import SessionRow
    from jarvis.persistence.engine import get_session

    with get_session() as s:
        row = s.get(SessionRow, session_id)
        row.last_active_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        s.flush()


def test_purge_inactive_removes_only_old_sessions():
    reset_engine_for_tests()
    create_all()
    _stale_session("old-session", days_ago=30)
    repos.sessions.get_or_create("fresh-session")  # active now

    n = repos.sessions.purge_inactive(ttl_days=7)
    assert n == 1
    assert repos.sessions.get("old-session") is None
    assert repos.sessions.get("fresh-session") is not None


def test_purge_inactive_disabled_when_zero():
    reset_engine_for_tests()
    create_all()
    _stale_session("very-old", days_ago=500)
    n = repos.sessions.purge_inactive(ttl_days=0)
    assert n == 0
    assert repos.sessions.get("very-old") is not None


def test_purge_inactive_cascades_messages():
    reset_engine_for_tests()
    create_all()
    _stale_session("cascade-session", days_ago=30)
    repos.messages.add("cascade-session", role="user", content="gone soon")

    repos.sessions.purge_inactive(ttl_days=7)
    from jarvis.persistence.models import MessageRow
    from jarvis.persistence.engine import get_session
    from sqlalchemy import select

    with get_session() as s:
        leftover = s.scalars(
            select(MessageRow).where(MessageRow.session_id == "cascade-session")
        ).all()
    assert leftover == []


def test_sweep_once_runs_all_cleanups(monkeypatch, client):
    from jarvis.tasks.maintenance import sweep_once

    reset_engine_for_tests()
    create_all()

    _stale_session("sweep-old", days_ago=30)
    repos.sessions.get_or_create("sweep-fresh")
    just_expired = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    ancient = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    repos.approvals.create(
        "sweep-just-expired",
        session_id="s1",
        state=_pending_state("sweep-just-expired"),
        expires_at=just_expired,
    )
    repos.approvals.create(
        "sweep-ancient",
        session_id="s1",
        state=_pending_state("sweep-ancient"),
        expires_at=ancient,
    )

    counts = sweep_once()

    # the just-expired pending row was flipped to "expired" but is still
    # within the retention window, so it survives
    assert repos.approvals.get("sweep-just-expired") is not None
    assert repos.approvals.get("sweep-just-expired").status == "expired"
    # the ancient row (also flipped to expired) is past retention -> deleted
    assert repos.approvals.get("sweep-ancient") is None
    assert counts["approvals_deleted"] == 1
    # the stale session was deleted, the fresh one kept
    assert repos.sessions.get("sweep-old") is None
    assert repos.sessions.get("sweep-fresh") is not None


# ---------------------------------------------------------------------------
# Expired-approval hard delete
# ---------------------------------------------------------------------------


def test_delete_expired_older_than_retention():
    reset_engine_for_tests()
    create_all()
    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    repos.approvals.create(
        "exp-old",
        session_id="x",
        state=_pending_state("exp-old"),
        expires_at=old,
    )

    n = repos.approvals.delete_expired_older_than(retention_hours=0)
    assert n == 0  # still pending; hard delete only touches expired rows


def test_delete_expired_older_than_skips_fresh():
    reset_engine_for_tests()
    create_all()
    past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    repos.approvals.create(
        "fx-1", session_id="x", state=_pending_state("fx-1"), expires_at=past
    )
    repos.approvals.purge_expired()  # flip fx-1 -> expired now

    n = repos.approvals.delete_expired_older_than(retention_hours=24)
    assert n == 0  # expired 2 minutes ago, within the 24h retention window
    assert repos.approvals.get("fx-1") is not None


def test_delete_expired_older_than_removes_old():
    reset_engine_for_tests()
    create_all()
    very_past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    repos.approvals.create(
        "ex-old", session_id="x", state=_pending_state("ex-old"), expires_at=very_past
    )
    repos.approvals.purge_expired()
    assert repos.approvals.get("ex-old").status == "expired"

    n = repos.approvals.delete_expired_older_than(retention_hours=24)
    assert n == 1
    assert repos.approvals.get("ex-old") is None


def test_sweeper_start_stop_roundtrip(monkeypatch, client):
    from jarvis.config.settings import settings
    from jarvis.tasks import maintenance

    monkeypatch.setattr(settings, "maintenance_sweep_interval", 1)
    maintenance.start_sweeper()
    try:
        assert maintenance._thread is not None
        assert maintenance._thread.is_alive()
    finally:
        maintenance.stop_sweeper()
    assert maintenance._thread is None


def test_sweeper_disabled_when_interval_zero(monkeypatch, client):
    from jarvis.config.settings import settings
    from jarvis.tasks import maintenance

    monkeypatch.setattr(settings, "maintenance_sweep_interval", 0)
    maintenance.start_sweeper()
    assert maintenance._thread is None