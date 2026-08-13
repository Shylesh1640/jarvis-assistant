"""Tests for jarvis.orchestration.model_selector.select_model.

Phase 4: complexity-aware dynamic model selection. These tests construct
state dicts directly and assert the returned model name follows the
(intent, complexity) routing matrix documented in model_selector.py.
"""
from types import SimpleNamespace

from jarvis.orchestration.model_selector import select_model


def _settings(**overrides) -> SimpleNamespace:
    """Build a settings-like object with deterministic defaults.

    We use SimpleNamespace rather than the real Settings class so tests
    don't read the actual .env file (which can vary per dev machine).
    """
    defaults = dict(
        general_model="qwen3:8b",
        strong_local_model="qwen3:14b",
        coding_model="qwen2.5-coder:7b-q5_K_M",
        coding_model_small="qwen2.5-coder:7b-q5_K_M",
        use_strong_local=True,
        complex_models=["anthropic/claude-opus-4.1", "openai/gpt-5.5"],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# general intent
# ---------------------------------------------------------------------------

def test_general_easy_uses_small_general():
    s = _settings()
    state = {"intent": "general", "complexity": "easy"}
    assert select_model(state, s) == "qwen3:8b"


def test_general_medium_uses_strong_local_when_enabled():
    s = _settings()
    state = {"intent": "general", "complexity": "medium"}
    assert select_model(state, s) == "qwen3:14b"


def test_general_difficult_uses_strong_local_when_enabled():
    s = _settings()
    state = {"intent": "general", "complexity": "difficult"}
    assert select_model(state, s) == "qwen3:14b"


def test_general_medium_falls_back_to_small_when_strong_disabled():
    s = _settings(use_strong_local=False)
    state = {"intent": "general", "complexity": "medium"}
    assert select_model(state, s) == "qwen3:8b"


def test_general_difficult_falls_back_to_small_when_strong_disabled():
    s = _settings(use_strong_local=False)
    state = {"intent": "general", "complexity": "difficult"}
    assert select_model(state, s) == "qwen3:8b"


# ---------------------------------------------------------------------------
# coding intent
# ---------------------------------------------------------------------------

def test_coding_easy_uses_small_coder():
    s = _settings()
    state = {"intent": "coding", "complexity": "easy"}
    assert select_model(state, s) == "qwen2.5-coder:7b-q5_K_M"


def test_coding_medium_uses_strong_coder():
    s = _settings()
    state = {"intent": "coding", "complexity": "medium"}
    assert select_model(state, s) == "qwen2.5-coder:7b-q5_K_M"


def test_coding_difficult_uses_strong_coder():
    s = _settings()
    state = {"intent": "coding", "complexity": "difficult"}
    assert select_model(state, s) == "qwen2.5-coder:7b-q5_K_M"


def test_coding_easy_falls_back_when_small_unset():
    # coding_model_small is "" or None -> fall back to coding_model
    s = _settings(coding_model_small="")
    state = {"intent": "coding", "complexity": "easy"}
    assert select_model(state, s) == "qwen2.5-coder:7b-q5_K_M"


# ---------------------------------------------------------------------------
# complex intent
# ---------------------------------------------------------------------------

def test_complex_uses_first_cloud_model():
    s = _settings()
    state = {"intent": "complex", "complexity": "difficult"}
    assert select_model(state, s) == "anthropic/claude-opus-4.1"


def test_complex_falls_back_to_general_when_no_cloud():
    s = _settings(complex_models=[])
    state = {"intent": "complex", "complexity": "difficult"}
    assert select_model(state, s) == "qwen3:8b"


# ---------------------------------------------------------------------------
# defaults / defensive
# ---------------------------------------------------------------------------

def test_missing_intent_defaults_to_general_easy():
    s = _settings()
    assert select_model({}, s) == "qwen3:8b"


def test_unknown_intent_falls_back_to_general():
    s = _settings()
    state = {"intent": "weird", "complexity": "easy"}
    # unknown intent falls through to the defensive fallback
    assert select_model(state, s) == "qwen3:8b"
