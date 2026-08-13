"""End-to-end verification that dynamic model selection is fully wired.

The three branches (general / coding / complex) all funnel through
``select_model(state, settings)`` and stash ``selected_model`` plus
``selection_reason`` in state. These tests assert each branch actually
calls the selector and that the resulting model matches the documented
routing matrix. ChatOllama is stubbed by conftest so no model server
is contacted.
"""
from __future__ import annotations

import pytest

from jarvis.orchestration import branches as branches_mod
from jarvis.orchestration.branches import (
    run_coding_branch,
    run_complex_branch,
    run_general_branch,
)


_DEFAULTS = dict(
    general_model="qwen3:8b",
    strong_local_model="qwen3:14b",
    coding_model="qwen2.5-coder:7b-q5_K_M",
    coding_model_small="qwen2.5-coder:7b-q5_K_M",
    use_strong_local=True,
    complex_model_chain="anthropic/claude-opus-4.1",
)


@pytest.fixture
def configured_settings(monkeypatch):
    for name, value in _DEFAULTS.items():
        if not hasattr(branches_mod.settings.__class__, name) or not isinstance(
            getattr(branches_mod.settings.__class__, name), property
        ):
            monkeypatch.setattr(branches_mod.settings, name, value, raising=True)
    return branches_mod.settings


def _state(user_input: str, **extra) -> dict:
    base = {"user_input": user_input, "history": [], "fallback_count": 0}
    base.update(extra)
    return base


def test_general_branch_records_model_and_reason(configured_settings, fake_ollama):
    state = _state("hi", intent="general", complexity="easy")
    state["messages"] = []
    run_general_branch(state)
    assert state["selected_model"] == "qwen3:8b"
    assert "general branch" in state["selection_reason"]


def test_general_branch_picks_strong_local_for_difficult(configured_settings, fake_ollama):
    state = _state(
        "explain transformers in great detail covering every layer and "
        "head and the residual stream shearing through the whole stack of "
        "encoder and decoder layers in this giant architecture deeply.",
        intent="general", complexity="difficult",
    )
    state["messages"] = []
    run_general_branch(state)
    assert state["selected_model"] == "qwen3:14b"
    assert state["selection_reason"] == "general branch using qwen3:14b"


def test_coding_branch_records_model_and_reason(configured_settings, fake_ollama):
    state = _state("write a function", intent="coding", complexity="easy")
    run_coding_branch(state)
    assert state["selected_model"] == "qwen2.5-coder:7b-q5_K_M"
    assert "coding branch" in state["selection_reason"]


def test_complex_branch_records_model_and_reason(configured_settings, monkeypatch):
    state = _state("design a thing", intent="complex", complexity="difficult")
    monkeypatch.setattr(
        branches_mod, "run_complex_with_fallback",
        lambda messages: ("[cloud]", "anthropic/claude-opus-4.1"),
    )
    run_complex_branch(state)
    assert state["selected_model"] == "anthropic/claude-opus-4.1"
    assert "complex branch" in state["selection_reason"]


def test_dynamic_selection_logs_model_picked(configured_settings, fake_ollama, caplog):
    import logging

    state = _state("hi", intent="general", complexity="easy")
    state["messages"] = []
    with caplog.at_level(logging.INFO, logger="jarvis.orchestration.model_selector"):
        run_general_branch(state)
    assert any("Model selected: qwen3:8b" in r.message for r in caplog.records)
