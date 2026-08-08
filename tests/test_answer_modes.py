"""Tests for the answer-style suffixes (teaching / architecture / code)."""
from __future__ import annotations

from jarvis.orchestration.context_window import style_reasoning_suffixes


def test_default_style_adds_no_suffix():
    assert style_reasoning_suffixes({"answer_style": "default"}) == ""


def test_teaching_style_suffix():
    s = style_reasoning_suffixes({"answer_style": "teaching"})
    assert "teaching" in s.lower()
    assert "Concept" in s


def test_architecture_style_suffix():
    s = style_reasoning_suffixes({"answer_style": "architecture"})
    assert "systems-architecture" in s
    assert "trade-offs" in s


def test_code_style_suffix():
    s = style_reasoning_suffixes({"answer_style": "code"})
    assert "code" in s.lower()


def test_concise_and_detailed():
    assert "concise" in style_reasoning_suffixes({"answer_style": "concise"})
    assert "detailed" in style_reasoning_suffixes({"answer_style": "detailed"})


def test_show_reasoning_adds_prefix():
    s = style_reasoning_suffixes({"answer_style": "default", "show_reasoning": True})
    assert s.startswith(" Begin your reply")


def test_unknown_style_is_ignored():
    assert style_reasoning_suffixes({"answer_style": "banana"}) == ""