"""Tests for per-session bearer tokens and rate limiting."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.config.settings import settings
from jarvis.observability import clear_traces
from jarvis.persistence import create_all
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.security import ratelimit as ratelimit_module
from jarvis.security.session_auth import issue_token


@pytest.fixture
def client(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr(routes.chat, "jarvis_graph", _StubGraph())
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    clear_traces()
    yield TestClient(app)


class _StubGraph:
    def invoke(self, state, config=None):
        state["final_response"] = "ok"
        state["selected_path"] = "general"
        state["selected_model"] = "qwen3:8b"
        state.setdefault("tools_used", [])
        state.setdefault("sources", [])
        return state


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def test_token_issuance_creates_session(client):
    token = issue_token("tok-session")
    assert token
    r = client.get("/sessions/tok-session/token")
    assert r.status_code == 200
    assert r.json()["session_token"] == token


def test_enforcement_rejects_missing_token(client, monkeypatch):
    monkeypatch.setattr(settings, "require_session_token", True)
    r = client.post(
        "/chat",
        json={"message": "hello", "session_id": "sec-session", "approved": False},
    )
    assert r.status_code == 403
    assert r.json()["error"] == "invalid_session_token"


def test_enforcement_accepts_valid_token(client, monkeypatch):
    monkeypatch.setattr(settings, "require_session_token", True)
    token = issue_token("sec-session")
    r = client.post(
        "/chat",
        json={
            "message": "hello",
            "session_id": "sec-session",
            "session_token": token,
            "approved": False,
        },
    )
    assert r.status_code == 200


def test_enforcement_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setattr(settings, "require_session_token", True)
    issue_token("sec-session")
    r = client.post(
        "/chat",
        json={
            "message": "hello",
            "session_id": "sec-session",
            "session_token": "deadbeef",
            "approved": False,
        },
    )
    assert r.status_code == 403


def test_token_rejected_for_other_session(client, monkeypatch):
    monkeypatch.setattr(settings, "require_session_token", True)
    token_a = issue_token("session-a")
    r = client.post(
        "/chat",
        json={
            "message": "hello",
            "session_id": "session-b",
            "session_token": token_a,
            "approved": False,
        },
    )
    assert r.status_code == 403


def test_tasks_route_enforces_token(client, monkeypatch):
    monkeypatch.setattr(settings, "require_session_token", True)
    r = client.post("/tasks", json={"description": "design a logo", "session_id": "tsk"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_unit():
    limiter = ratelimit_module.RateLimiter(per_minute=2)
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True
    allowed, wait = limiter.check("k")
    assert allowed is False
    assert wait > 0
    # Different key unaffected.
    assert limiter.check("other")[0] is True


def test_zero_per_minute_disables(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    ratelimit_module.reload_limiter()
    for _ in range(5):
        r = client.post(
            "/chat",
            json={"message": "ping", "session_id": "rl-session", "approved": False},
        )
        assert r.status_code == 200


def test_chat_route_429s_when_limited(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    ratelimit_module.reload_limiter()
    client.post(
        "/chat", json={"message": "a", "session_id": "rl-2", "approved": False}
    )
    client.post(
        "/chat", json={"message": "b", "session_id": "rl-2", "approved": False}
    )
    r = client.post(
        "/chat", json={"message": "c", "session_id": "rl-2", "approved": False}
    )
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "rate_limited"
    assert r.headers.get("retry-after") is not None


def test_rate_limit_keyed_away_from_session(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    ratelimit_module.reload_limiter()
    client.post(
        "/chat", json={"message": "a", "session_id": "rl-3", "approved": False}
    )
    # A different session id has its own counter.
    r = client.post(
        "/chat", json={"message": "b", "session_id": "rl-4", "approved": False}
    )
    assert r.status_code == 200