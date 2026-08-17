"""Tests for RAG / selected-text / context caps in context_window.

Verifies:
- RAG context block is capped to rag_context_token_cap tokens.
- Selected-text snippet is capped to selected_text_token_cap tokens.
- System message is always preserved.
- Current user message is always preserved.
- Truncation appends an ellipsis marker.
"""
from jarvis.config.settings import settings
from jarvis.orchestration.context_window import (
    SYSTEM_PROMPT,
    build_final_messages,
    estimate_tokens,
    format_retrieved_context,
    frame_user_message,
)


def test_rag_context_capped(monkeypatch):
    monkeypatch.setattr(settings, "rag_context_token_cap", 50)
    big = "alpha beta gamma delta " * 200
    out = format_retrieved_context(big)
    assert "<<<RETRIEVED CONTEXT>>>" in out
    assert "[truncated]" in out
    assert estimate_tokens(out) < estimate_tokens(big)


def test_rag_context_uncapped_when_cap_zero(monkeypatch):
    monkeypatch.setattr(settings, "rag_context_token_cap", 0)
    big = "alpha " * 5000
    out = format_retrieved_context(big)
    assert "[truncated]" not in out


def test_rag_context_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "rag_context_token_cap", 50)
    assert format_retrieved_context("") == ""


def test_selected_text_capped(monkeypatch):
    monkeypatch.setattr(settings, "selected_text_token_cap", 30)
    huge_selection = "word " * 500
    out = frame_user_message("explain this", huge_selection)
    assert "[truncated]" in out
    assert "explain this" in out


def test_selected_text_uncapped_when_cap_zero(monkeypatch):
    monkeypatch.setattr(settings, "selected_text_token_cap", 0)
    huge = "word " * 500
    out = frame_user_message("q", huge)
    assert "[truncated]" not in out


def test_system_message_always_present():
    msgs = build_final_messages({"user_input": "hi", "history": [], "retrieved_context": ""})
    assert msgs[0].content == SYSTEM_PROMPT
    assert msgs[-1].content == "hi"


def test_current_user_message_always_preserved():
    state = {
        "user_input": "the current question",
        "history": [{"role": "user", "content": "x" * 5000} for _ in range(40)],
        "retrieved_context": "",
    }
    msgs = build_final_messages(state)
    assert msgs[-1].content == "the current question"


def test_total_context_bounded_by_caps(monkeypatch):
    monkeypatch.setattr(settings, "rag_context_token_cap", 100)
    monkeypatch.setattr(settings, "selected_text_token_cap", 50)
    state = {
        "user_input": "q",
        "history": [],
        "retrieved_context": "ctx word " * 500,
        "selected_text": "sel " * 500,
    }
    msgs = build_final_messages(state)
    total = sum(estimate_tokens(getattr(m, "content", "")) for m in msgs)
    assert total < 1000
