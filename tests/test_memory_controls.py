"""Tests for Phase 5 conversation memory: controls, evicted-turn summaries,
secrets exclusion, and the /memory routes.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.guardrails.output_guard import redact_output
from jarvis.memory import memory_store as ms_mod
from jarvis.memory import summaries as s_mod
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
def no_chroma(monkeypatch):
    """Stub the Chroma collection + embedding fn so tests never touch a store."""
    monkeypatch.setattr(ms_mod, "get_collection", lambda: MagicMock())
    monkeypatch.setattr(
        "jarvis.memory.store.get_embedding_function",
        lambda: MagicMock(embed_documents=lambda texts: [[0.1]]),
    )


def _seed_messages(session_id: str, n_pairs: int = 10) -> None:
    for i in range(n_pairs):
        repos.messages.add(session_id, role="user", content=f"u{i}")
        repos.messages.add(session_id, role="assistant", content=f"a{i}")


# ---------------------------------------------------------------------------
# memory_store controls
# ---------------------------------------------------------------------------

def test_list_get_export_roundtrip(fresh_db, no_chroma):
    _seed_messages("s1", n_pairs=3)
    sid = ms_mod.store_summary("s1", "bullet summary one", from_message_id=1, to_message_id=2)
    assert sid is not None
    items = ms_mod.list_session_memory("s1")
    assert len(items) == 1
    assert items[0]["summary"] == "bullet summary one"

    got = ms_mod.get_memory(sid)
    assert got["session_id"] == "s1"

    md = ms_mod.export_session_memory("s1")
    assert "# Conversation memory" in md
    assert "bullet summary one" in md


def test_export_empty(fresh_db):
    md = ms_mod.export_session_memory("nope")
    assert "(nothing stored yet)" in md


def test_delete_memory(fresh_db, no_chroma):
    _seed_messages("s1", n_pairs=3)
    sid = ms_mod.store_summary("s1", "to be deleted")
    assert ms_mod.delete_memory(sid) is True
    assert ms_mod.list_session_memory("s1") == []


def test_delete_memory_missing(fresh_db):
    assert ms_mod.delete_memory(9999) is False


def test_clear_session_memory(fresh_db, no_chroma):
    _seed_messages("s1", n_pairs=3)
    _seed_messages("s2", n_pairs=3)
    ms_mod.store_summary("s1", "one")
    ms_mod.store_summary("s1", "two")
    ms_mod.store_summary("s2", "other")
    assert ms_mod.clear_session_memory("s1") == 2
    assert ms_mod.list_session_memory("s1") == []
    assert len(ms_mod.list_session_memory("s2")) == 1


# ---------------------------------------------------------------------------
# Secrets exclusion in summaries
# ---------------------------------------------------------------------------

def test_summarize_text_redacts_secrets(fresh_db, monkeypatch):
    captured: dict = {}

    def _fake_llm(prompt):
        captured["prompt"] = prompt
        return MagicMock(content="safe summary")

    monkeypatch.setattr(s_mod, "get_general_model", lambda **kw: MagicMock(invoke=_fake_llm))
    out = s_mod._summarize_text([
        {"role": "user", "content": "my api key is sk-1234567890abcdefghij"},
        {"role": "assistant", "content": "user:email me at a@b.com"},
    ])
    assert out == "safe summary"
    # The raw secret and email must never reach the summarizer prompt.
    assert "sk-1234567890" not in captured["prompt"]
    assert "a@b.com" not in captured["prompt"]
    assert "[redacted-token]" in captured["prompt"]


def test_redact_output_guard_integration():
    assert "sk-1234567890abcdefghij" not in redact_output("key=sk-1234567890abcdefghij")


# ---------------------------------------------------------------------------
# Evicted-turn summarization
# ---------------------------------------------------------------------------

def test_maybe_summarize_evicted_noop_below_window(fresh_db, monkeypatch):
    # Only 4 messages; history_max_turns=20 -> nothing evicted.
    _seed_messages("s1", n_pairs=2)
    monkeypatch.setattr(s_mod, "_summarize_text", lambda msgs: "sum")
    assert s_mod.maybe_summarize_evicted("s1") is None
    assert repos.summaries.count_for_session("s1") == 0


def test_maybe_summarize_evicted_covers_dropped_turns(fresh_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.history_max_turns", 2)
    # 10 pairs = 20 messages; window keeps 4 (2 turns) -> 16 evicted.
    _seed_messages("s1", n_pairs=10)
    monkeypatch.setattr(s_mod, "_summarize_text", lambda msgs: "evicted summary")
    monkeypatch.setattr(ms_mod, "get_collection", lambda: MagicMock())
    monkeypatch.setattr(
        "jarvis.memory.store.get_embedding_function",
        lambda: MagicMock(embed_documents=lambda texts: [[0.1]]),
    )
    out = s_mod.maybe_summarize_evicted("s1")
    assert out == "evicted summary"
    assert repos.summaries.count_for_session("s1") == 1


def test_maybe_summarize_evicted_dedupes(fresh_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.history_max_turns", 2)
    _seed_messages("s1", n_pairs=10)
    # First pass stores a summary covering the evicted range.
    repos.summaries.add("s1", summary="prior", to_message_id=16)
    monkeypatch.setattr(s_mod, "_summarize_text", lambda msgs: "again")
    # Newest evicted message id (17) <= latest summary's to_message_id (16)?
    # 17 > 16 -> not covered -> summarizes again. Use a cover-all summary.
    repos.summaries.delete(1)
    latest = repos.summaries.latest_for_session("s1")
    assert latest is None
    # Add a summary covering everything (to_message_id=20) -> no-op.
    repos.summaries.add("s1", summary="prior", to_message_id=20)
    assert s_mod.maybe_summarize_evicted("s1") is None


# ---------------------------------------------------------------------------
# /memory routes
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


def test_memory_route_list_and_export(client, fresh_db, no_chroma, monkeypatch):
    monkeypatch.setattr(
        "jarvis.api.routes.memory.list_session_memory",
        lambda sid, limit=50: [
            {"id": 1, "session_id": "default", "summary": "s1", "created_at": None},
        ],
    )
    r = client.get("/memory")
    assert r.status_code == 200
    assert r.json()["items"][0]["summary"] == "s1"

    r = client.get("/memory/export")
    assert r.status_code == 200
    assert r.json()["markdown"].startswith("# Conversation memory")


def test_memory_delete_requires_confirmation(client, fresh_db, no_chroma):
    _seed_messages("default", n_pairs=3)
    ms_mod.store_summary("default", "keep me")
    r = client.delete("/memory/1")
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_required"
    # Still present.
    assert len(ms_mod.list_session_memory("default")) == 1


def test_memory_delete_with_confirmation(client, fresh_db, no_chroma):
    _seed_messages("default", n_pairs=3)
    sid = ms_mod.store_summary("default", "remove me")
    r = client.delete(f"/memory/{sid}?confirm=1")
    assert r.status_code == 200
    assert ms_mod.list_session_memory("default") == []


def test_memory_delete_missing_404(client, fresh_db):
    r = client.delete("/memory/424242?confirm=1")
    assert r.status_code == 404


def test_memory_clear_requires_confirmation(client, fresh_db, no_chroma):
    _seed_messages("default", n_pairs=3)
    ms_mod.store_summary("default", "one")
    r = client.delete("/memory")
    assert r.status_code == 400
    assert len(ms_mod.list_session_memory("default")) == 1


def test_memory_clear_with_confirmation(client, fresh_db, no_chroma):
    _seed_messages("default", n_pairs=3)
    ms_mod.store_summary("default", "one")
    ms_mod.store_summary("default", "two")
    r = client.delete("/memory?confirm=1")
    assert r.status_code == 200
    assert r.json()["cleared"] == 2
    assert ms_mod.list_session_memory("default") == []
