"""Tests for the Phase 'select text -> ask follow-up' feature.

Covers the prompt-construction helper `branches._build_user_message` so
we can verify it frames the question correctly with and without a
selected snippet. ChatOllama is mocked via conftest.py so no model
server is needed.
"""
from jarvis.orchestration.branches import _build_user_message


def test_no_selection_returns_input_unchanged():
    state = {"user_input": "what is an async generator", "selected_text": ""}
    assert _build_user_message(state) == "what is an async generator"


def test_missing_selected_text_field_treated_as_none():
    state = {"user_input": "explain"}
    assert _build_user_message(state) == "explain"


def test_whitespace_only_selection_is_ignored():
    state = {"user_input": "explain again", "selected_text": "   \n  \t  "}
    assert _build_user_message(state) == "explain again"


def test_selection_framed_with_snippet():
    state = {
        "user_input": "what does this line do exactly?",
        "selected_text": "yield from generator",
    }
    out = _build_user_message(state)
    assert "yield from generator" in out
    assert "what does this line do exactly?" in out
    # Must clearly signal that the snippet is the focus of the question.
    assert "selected the following text" in out
    assert "follow-up" in out
    # The snippet should be quoted/fenced so the model doesn't mistake it
    # for the user's own words.
    assert '"""\nyield from generator\n"""' in out


def test_selection_is_stripped_before_inlining():
    state = {
        "user_input": "and why?",
        "selected_text": "   return x + 1   \n",
    }
    out = _build_user_message(state)
    # The leading/trailing whitespace is gone, the code line is intact.
    assert "return x + 1" in out
    assert "   return x + 1   " not in out


def test_user_input_with_special_chars_preserved():
    state = {
        "user_input": 'what about `**kwargs` here?',
        "selected_text": "def f(**kwargs): pass",
    }
    out = _build_user_message(state)
    assert "what about `**kwargs` here?" in out
    assert "def f(**kwargs): pass" in out
