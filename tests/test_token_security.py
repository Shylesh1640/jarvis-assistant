"""Tests for Phase 6 session-token security (hashing, TTL, rotation, revoke)."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.config.settings import settings
from jarvis.observability import clear_traces
from jarvis.persistence import create_all
from jarvis.persistence import repos
from jarvis.persistence.engine import get_session, reset_engine_for_tests
from jarvis.security.session_auth import issue_token, is_valid_token, revoke_token, rotate_token
from jarvis.security.token_hasher import (
    hash_token,
    looks_hashed,
    new_session_token,
    verify_token,
)


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
# hasher unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["argon2", "bcrypt", "pbkdf2"])
def test_hash_verify_roundtrip(monkeypatch, scheme):
    monkeypatch.setattr(settings, "session_token_hash_scheme", scheme)
    token = new_session_token()
    stored = hash_token(token, scheme)
    assert verify_token(token, stored) is True
    assert verify_token(token + "x", stored) is False


def test_default_scheme_is_argon2():
    token = new_session_token()
    stored = hash_token(token)
    assert stored.startswith("$argon2id$")
    assert verify_token(token, stored) is True


def test_pbkdf2_format_is_self_describing():
    stored = hash_token("s3cret", "pbkdf2")
    assert stored.startswith("pbkdf2$")
    parts = stored.split("$")
    assert len(parts) == 4
    assert verify_token("s3cret", stored) is True


def test_verify_rejects_garbage():
    assert verify_token("x", None) is False
    assert verify_token("x", "not-a-hash-at-all$%%%") is False


def test_verify_legacy_plaintext_returns_true():
    assert verify_token("plain-token", "plain-token") is True
    assert verify_token("other", "plain-token") is False


def test_looks_hashed():
    assert looks_hashed("$argon2id$v=19$...") is True
    assert looks_hashed("$2b$12$abcdef") is True
    assert looks_hashed("pbkdf2$100000$a$b") is True
    assert looks_hashed("plaintext") is False
    assert looks_hashed(None) is False


# ---------------------------------------------------------------------------
# repo: storage, validation, TTL, rotation, revoke, migration
# ---------------------------------------------------------------------------


def test_token_stored_hashed_not_plaintext(client):
    token = issue_token("hash-session")
    row = repos.sessions.get("hash-session")
    assert row is not None
    assert row.token_hash is not None
    assert row.token is None
    assert verify_token(token, row.token_hash) is True
    assert looks_hashed(row.token_hash)


def test_repeated_issue_returns_same_token_within_process(client):
    token = issue_token("stable-session")
    assert issue_token("stable-session") == token


def test_valid_and_invalid_tokens(client):
    token = issue_token("valid-session")
    assert is_valid_token("valid-session", token) is True
    assert is_valid_token("valid-session", "wrong") is False
    assert is_valid_token("valid-session", None) is False


def test_expired_token_rejected(client, monkeypatch):
    token = issue_token("exp-session")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with _session_edit("exp-session") as row:
        row.token_expires_at = past
    assert is_valid_token("exp-session", token) is False


def test_zero_ttl_never_expires(client, monkeypatch):
    monkeypatch.setattr(settings, "session_token_ttl_hours", 0)
    token = issue_token("never-exp")
    row = repos.sessions.get("never-exp")
    assert row.token_expires_at is None
    assert is_valid_token("never-exp", token) is True


def test_rotation_invalidates_old_token(client):
    token = issue_token("rot-session")
    assert is_valid_token("rot-session", token) is True
    new_token = rotate_token("rot-session")
    assert new_token is not None
    assert new_token != token
    assert is_valid_token("rot-session", token) is False
    assert is_valid_token("rot-session", new_token) is True


def test_rotate_missing_session_returns_none(client):
    assert rotate_token("ghost") is None


def test_revocation_invalidates_token(client):
    token = issue_token("rev-session")
    assert is_valid_token("rev-session", token) is True
    assert revoke_token("rev-session") is True
    assert is_valid_token("rev-session", token) is False
    assert repos.sessions.token_status("rev-session")["revoked_at"] is not None


def test_revoke_missing_session_returns_false(client):
    assert revoke_token("ghost") is False


def test_legacy_plaintext_token_migrates_lazily(client):
    with _session_edit("legacy-session", create=True) as row:
        row.token = "legacy-plain"
        row.token_hash = None
    assert is_valid_token("legacy-session", "legacy-plain") is True
    row = repos.sessions.get("legacy-session")
    assert row.token_hash is not None
    assert row.token is None
    assert is_valid_token("legacy-session", "legacy-plain") is True


def test_token_status_reports_metadata(client):
    issue_token("status-session")
    status = repos.sessions.token_status("status-session")
    assert status["has_token"] is True
    assert status["hash_scheme"] == settings.session_token_hash_scheme
    assert status["created_at"] is not None
    assert status["expired"] is False
    assert status["revoked_at"] is None
    assert status["rotation_due"] is False
    assert "token" not in str(status) or "session_token" not in str(status)


def test_token_status_missing_session_is_none(client):
    assert repos.sessions.token_status("ghost") is None


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def test_token_route_creates_session_and_validates(client):
    r = client.get("/sessions/api-session/token")
    assert r.status_code == 200
    token = r.json()["session_token"]
    assert is_valid_token("api-session", token) is True


def test_rotate_route_invalidates_old(client):
    r1 = client.get("/sessions/api-rot/token")
    old = r1.json()["session_token"]
    r2 = client.post("/sessions/api-rot/rotate-token")
    assert r2.status_code == 200
    new = r2.json()["session_token"]
    assert new != old
    assert is_valid_token("api-rot", old) is False
    assert is_valid_token("api-rot", new) is True


def test_revoke_route(client):
    token = issue_token("api-rev")
    r = client.post("/sessions/api-rev/revoke")
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    assert is_valid_token("api-rev", token) is False


def test_rotate_revoke_missing_session_404(client):
    assert client.post("/sessions/ghost-rot/rotate-token").status_code == 404
    assert client.post("/sessions/ghost-rev/revoke").status_code == 404


def test_session_info_reports_token_metadata(client):
    issue_token("api-info")
    r = client.get("/sessions/api-info")
    assert r.status_code == 200
    body = r.json()
    assert body["has_token"] is True
    assert body["token_hash_scheme"] == settings.session_token_hash_scheme
    assert body["token_expired"] is False
    assert "session_token" not in body


def test_enforcement_still_works_with_hashed_token(client, monkeypatch):
    monkeypatch.setattr(settings, "require_session_token", True)
    token = issue_token("enforce-session")
    ok = client.post(
        "/chat",
        json={"message": "hi", "session_id": "enforce-session", "session_token": token},
    )
    assert ok.status_code == 200
    bad = client.post(
        "/chat",
        json={"message": "hi", "session_id": "enforce-session", "session_token": "nope"},
    )
    assert bad.status_code == 403


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _session_edit(session_id: str, create: bool = False):
    """Open the session row directly for mutation, then commit."""
    from jarvis.persistence.models import SessionRow

    with get_session() as s:
        row = s.get(SessionRow, session_id)
        if row is None and create:
            row = SessionRow(id=session_id)
            s.add(row)
            s.flush()
        yield row
        s.commit()