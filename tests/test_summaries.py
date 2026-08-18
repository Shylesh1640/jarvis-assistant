"""Tests for ``jarvis.memory.summaries.maybe_summarize``.

The Ollama summarizer, the Chroma ingest, and the persistence layer are
all stubbed so the summarizer's threshold + dedup logic is the only
thing under test.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.memory import summaries as s_mod
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests


@pytest.fixture
def fresh_db(monkeypatch):
    # Force a SQLite-zero-DSN path and make the module think persistence is up.
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


def _seed_messages(session_id: str, n_pairs: int = 10) -> None:
    for i in range(n_pairs):
        repos.messages.add(session_id, role="user", content=f"u{i}")
        repos.messages.add(session_id, role="assistant", content=f"a{i}")


def test_does_not_summarize_below_threshold(fresh_db, monkeypatch):
    # threshold = 10 turns by default -> 20 messages. Give it 6 (3 pairs).
    _seed_messages("s1", n_pairs=3)
    called = MagicMock()
    monkeypatch.setattr(s_mod, "_summarize_text", lambda msgs: called())
    called.assert_not_called()
    assert s_mod.maybe_summarize("s1") is None


def test_summarizes_when_threshold_crossed(fresh_db, monkeypatch):
    _seed_messages("s1", n_pairs=10)  # 20 messages -> 1 batch (20 / 20 = 1)
    monkeypatch.setattr(
        s_mod, "_summarize_text", lambda msgs: "summary bullets..."
    )
    # store_summary mirrors the summary into Chroma; stub the collection so
    # the test doesn't touch a real vector store.
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "jarvis.memory.memory_store.get_collection", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "jarvis.memory.store.get_embedding_function",
        lambda: MagicMock(embed_documents=lambda texts: [[0.1]]),
    )
    result = s_mod.maybe_summarize("s1")
    assert result == "summary bullets..."
    assert repos.summaries.count_for_session("s1") == 1
    assert repos.summaries.latest_for_session("s1").summary == "summary bullets..."


def test_dedupes_when_already_summarized(fresh_db, monkeypatch):
    _seed_messages("s1", n_pairs=10)
    repos.summaries.add("s1", summary="prior summary")
    monkeypatch.setattr(s_mod, "_summarize_text", lambda msgs: "would summarize")
    assert s_mod.maybe_summarize("s1") is None


def test_summarize_text_swallows_llm_failure(fresh_db, monkeypatch):
    def _boom(msgs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(s_mod, "get_general_model", _boom)
    assert s_mod._summarize_text([{"role": "user", "content": "hi"}]) == ""


def test_maybe_summarize_swallows_create_all_failure(monkeypatch):
    def _create_all_boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(s_mod, "create_all", _create_all_boom)
    # Should not raise even though DB init fails.
    assert s_mod.maybe_summarize("s1") is None
