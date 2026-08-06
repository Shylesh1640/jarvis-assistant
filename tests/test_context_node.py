"""Tests for the build_context orchestration node (RAG layer).

The node lives in `jarvis.orchestration.context_node` and decides
whether to retrieve context based on whether the vector store has any
documents. We avoid talking to a real Chroma / Ollama by monkeypatching
`has_documents` and `query_context` from `jarvis.memory.retrieve`.
"""

from jarvis.orchestration import context_node as ctx_mod
from jarvis.orchestration.context_node import build_context


def test_build_context_skips_when_no_documents(monkeypatch):
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: False)
    captured: list[str] = []
    monkeypatch.setattr(
        ctx_mod,
        "query_context",
        lambda q, k, with_sources=False: captured.append(q) or (("", []) if with_sources else ""),
    )
    state = {"user_input": "anything", "selected_text": ""}
    out = build_context(state)
    assert out["retrieved_context"] == ""
    assert out["sources"] == []
    # No query should have been issued.
    assert captured == []


def test_build_context_retrieves_and_stores(monkeypatch):
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)

    def _stub_query(query, k, with_sources=False):
        ctx = f"[ctx for: {query}]"
        return (ctx, [{"source": "x", "chunk_id": "1", "doc": "..."}]) if with_sources else ctx

    monkeypatch.setattr(ctx_mod, "query_context", _stub_query)

    state = {"user_input": "what is RAG", "selected_text": ""}
    out = build_context(state)
    assert out["retrieved_context"].startswith("[ctx for: ")
    assert "what is RAG" in out["retrieved_context"]
    assert out["sources"] == [{"source": "x", "chunk_id": "1", "doc": "..."}]


def test_build_context_includes_selected_text_in_query(monkeypatch):
    """Follow-ups about a snippet should pull the snippet into the query."""
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)

    seen: list[str] = []

    def _capture(query, k, with_sources=False):
        seen.append(query)
        return (f"ctx for {query}", []) if with_sources else f"ctx for {query}"

    monkeypatch.setattr(ctx_mod, "query_context", _capture)

    state = {
        "user_input": "explain this part",
        "selected_text": "yield from generator",
    }
    build_context(state)
    assert len(seen) == 1
    # build_retrieval_query combines the snippet with the user question.
    assert "yield from generator" in seen[0]
    assert "explain this part" in seen[0]


def test_build_context_handles_no_input(monkeypatch):
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)
    monkeypatch.setattr(ctx_mod, "query_context", lambda q, k, with_sources=False: ("", []) if with_sources else "")
    # Empty user_input + empty selected_text -> build_retrieval_query is "".
    # build_context leaves retrieved_context empty.
    state = {"user_input": "", "selected_text": ""}
    out = build_context(state)
    assert out["retrieved_context"] == ""
    assert out["sources"] == []


def test_build_context_does_not_mutate_other_state_keys(monkeypatch):
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)
    monkeypatch.setattr(ctx_mod, "query_context", lambda q, k, with_sources=False: ("ctx", []) if with_sources else "ctx")
    state = {
        "user_input": "hi",
        "selected_text": "",
        "intent": "general",
        "history": [{"role": "user", "content": "hi"}],
    }
    out = build_context(state)
    # The node only writes retrieved_context + sources; existing keys are preserved.
    assert out["intent"] == "general"
    assert out["history"] == [{"role": "user", "content": "hi"}]
    assert out["retrieved_context"] == "ctx"
    assert out["sources"] == []
