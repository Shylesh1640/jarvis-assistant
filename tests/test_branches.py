"""Tests for the Phase 4 dynamic-model-selection wiring inside branches.

We monkeypatch the live `settings` singleton so branches use deterministic
model names regardless of the developer's local .env. ChatOllama is
replaced by the fake from conftest.py so no model server is contacted.
"""
from __future__ import annotations

import pytest

from jarvis.orchestration import branches as branches_mod
from jarvis.orchestration.branches import (
    run_coding_branch,
    run_complex_branch,
    run_general_branch,
)


# ---------------------------------------------------------------------------
# Branch -> model name expectations for each (intent, complexity) cell
# ---------------------------------------------------------------------------

_DEFAULTS = dict(
    general_model="qwen3:8b",
    strong_local_model="qwen3:14b",
    coding_model="qwen3-coder:30b",
    coding_model_small="qwen2.5-coder:7b",
    use_strong_local=True,
    # Backing field for the `complex_models` property (comma-separated).
    complex_model_chain="anthropic/claude-opus-4.1",
)


@pytest.fixture
def configured_settings(monkeypatch):
    """Pin the live settings singleton to deterministic values."""
    for name, value in _DEFAULTS.items():
        # Skip fields that are computed properties (no setter).
        if not hasattr(branches_mod.settings.__class__, name) or not isinstance(
            getattr(branches_mod.settings.__class__, name), property
        ):
            monkeypatch.setattr(branches_mod.settings, name, value, raising=True)
    # Make the API key non-empty so OpenRouter paths are considered configured.
    import jarvis.models.openrouter_client as orc
    monkeypatch.setattr(orc.settings, "openrouter_api_key", "fake-key", raising=True)
    return branches_mod.settings


def _state(user_input: str, **extra) -> dict:
    base = {"user_input": user_input, "history": [], "fallback_count": 0}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# General branch
# ---------------------------------------------------------------------------

def test_general_branch_uses_small_model_for_easy(configured_settings, fake_ollama):
    state = _state("hi", intent="general", complexity="easy")
    state["messages"] = []
    run_general_branch(state)
    assert state["selected_model"] == "qwen3:8b"
    assert state["selected_path"] == "general"
    assert state["final_response"].startswith("[fake response from qwen3:8b")
    assert fake_ollama.instances[-1].model == "qwen3:8b"


def test_general_branch_uses_strong_local_for_difficult(configured_settings, fake_ollama):
    state = _state(
        "Please explain in great detail how a transformer model works "
        "from the linear algebra foundations up to attention heads and "
        "positional encoding and the residual stream shearing through the "
        "whole stack of encoder and decoder layers in this architecture.",
        intent="general", complexity="difficult",
    )
    state["messages"] = []
    run_general_branch(state)
    assert state["selected_model"] == "qwen3:14b"
    assert fake_ollama.instances[-1].model == "qwen3:14b"


# ---------------------------------------------------------------------------
# Coding branch
# ---------------------------------------------------------------------------

def test_coding_branch_uses_small_coder_for_easy(configured_settings, fake_ollama):
    state = _state("write a function to reverse a string",
                   intent="coding", complexity="easy")
    run_coding_branch(state)
    assert state["selected_model"] == "qwen2.5-coder:7b"
    assert state["selected_path"] == "coding"
    assert fake_ollama.instances[-1].model == "qwen2.5-coder:7b"


def test_coding_branch_uses_strong_coder_for_difficult(configured_settings, fake_ollama):
    state = _state(
        "refactor this large service into clean architecture and add tests",
        intent="coding", complexity="difficult",
    )
    run_coding_branch(state)
    assert state["selected_model"] == "qwen3-coder:30b"
    assert fake_ollama.instances[-1].model == "qwen3-coder:30b"


# ---------------------------------------------------------------------------
# Complex branch (with mocked cloud client)
# ---------------------------------------------------------------------------

def test_complex_branch_uses_cloud_chain(configured_settings, monkeypatch):
    state = _state("design an AI-powered traffic light system",
                   intent="complex", complexity="difficult")

    def _fake_run(messages):
        return ("[cloud response]", "anthropic/claude-opus-4.1")

    monkeypatch.setattr(branches_mod, "run_complex_with_fallback", _fake_run)
    run_complex_branch(state)
    assert state["selected_path"] == "complex"
    assert state["selected_model"] == "anthropic/claude-opus-4.1"
    assert state["final_response"] == "[cloud response]"


def test_complex_branch_falls_back_to_general_on_cloud_failure(
    configured_settings, monkeypatch, fake_ollama,
):
    state = _state("design something huge", intent="complex", complexity="difficult")

    def _raise(messages):
        raise RuntimeError("cloud down")

    monkeypatch.setattr(branches_mod, "run_complex_with_fallback", _raise)
    run_complex_branch(state)

    # Fallback landed on general branch -> selected_path flipped to "general"
    assert state["selected_path"] == "general"
    assert state["fallback_count"] == 1
    assert fake_ollama.instances[-1].model == "qwen3:8b"
