"""Tests for the 'select text -> ask follow-up' feature.

Covers the prompt-construction helpers in
`jarvis.orchestration.context_window`: the state-aware
`build_user_message` wrapper and the underlying `frame_user_message`.
These verify the user's question is framed correctly with and without
a selected snippet. No model server is contacted.
"""

from jarvis.orchestration.context_window import build_user_message, frame_user_message


def test_no_selection_returns_input_unchanged():
    state = {"user_input": "what is an async generator", "selected_text": ""}
    assert build_user_message(state) == "what is an async generator"


def test_missing_selected_text_field_treated_as_none():
    state = {"user_input": "explain"}
    assert build_user_message(state) == "explain"


def test_whitespace_only_selection_is_ignored():
    state = {"user_input": "explain again", "selected_text": "   \n  \t  "}
    assert build_user_message(state) == "explain again"


def test_selection_framed_with_snippet():
    state = {
        "user_input": "what does this line do exactly?",
        "selected_text": "yield from generator",
    }
    out = build_user_message(state)
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
    out = build_user_message(state)
    # The leading/trailing whitespace is gone, the code line is intact.
    assert "return x + 1" in out
    assert "   return x + 1   " not in out


def test_user_input_with_special_chars_preserved():
    state = {
        "user_input": "what about `**kwargs` here?",
        "selected_text": "def f(**kwargs): pass",
    }
    out = build_user_message(state)
    assert "what about `**kwargs` here?" in out
    assert "def f(**kwargs): pass" in out


def test_frame_user_message_directly_no_selection():
    assert frame_user_message("hello", "") == "hello"
    assert frame_user_message("hello", "   ") == "hello"


def test_frame_user_message_directly_with_selection():
    out = frame_user_message("explain it", "x = 1")
    assert "x = 1" in out
    assert "explain it" in out
    assert '"""\nx = 1\n"""' in out
