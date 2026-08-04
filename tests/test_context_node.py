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
    monkeypatch.setattr(ctx_mod, "query_context", lambda q, k: captured.append(q) or "")
    state = {"user_input": "anything", "selected_text": ""}
    out = build_context(state)
    assert out["retrieved_context"] == ""
    # No query should have been issued.
    assert captured == []


def test_build_context_retrieves_and_stores(monkeypatch):
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)

    def _stub_query(query: str, k: int) -> str:
        # Record the query so we can assert the selected_text made it in.
        return f"[ctx for: {query}]"

    monkeypatch.setattr(ctx_mod, "query_context", _stub_query)

    state = {"user_input": "what is RAG", "selected_text": ""}
    out = build_context(state)
    assert out["retrieved_context"].startswith("[ctx for: ")
    assert "what is RAG" in out["retrieved_context"]


def test_build_context_includes_selected_text_in_query(monkeypatch):
    """Follow-ups about a snippet should pull the snippet into the query."""
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)

    seen: list[str] = []

    def _capture(query: str, k: int) -> str:
        seen.append(query)
        return "ctx"

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
    monkeypatch.setattr(ctx_mod, "query_context", lambda q, k: "ctx")
    # Empty user_input + empty selected_text -> build_retrieval_query is "".
    # build_context leaves retrieved_context empty.
    state = {"user_input": "", "selected_text": ""}
    out = build_context(state)
    assert out["retrieved_context"] == ""


def test_build_context_does_not_mutate_other_state_keys(monkeypatch):
    monkeypatch.setattr(ctx_mod, "has_documents", lambda: True)
    monkeypatch.setattr(ctx_mod, "query_context", lambda q, k: "ctx")
    state = {
        "user_input": "hi",
        "selected_text": "",
        "intent": "general",
        "history": [{"role": "user", "content": "hi"}],
    }
    out = build_context(state)
    # The node only writes retrieved_context; existing keys are preserved.
    assert out["intent"] == "general"
    assert out["history"] == [{"role": "user", "content": "hi"}]
    assert out["retrieved_context"] == "ctx"
