"""Tests for the Streamlit markdown exporter."""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def app_module() -> types.ModuleType:
    """Load only ``export_conversation_to_markdown`` from streamlit_app.py.

    Parsing the file with ``ast`` and exec'ing just that function avoids
    importing Streamlit (which needs a running script context) while still
    testing the real source.
    """
    src = Path("streamlit_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "export_conversation_to_markdown":
            fn = node
            break
    assert fn is not None, "export_conversation_to_markdown not found in streamlit_app.py"

    module = types.ModuleType("streamlit_app")
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "streamlit_app.py", "exec"), module.__dict__)
    return module


def test_export_contains_roles(app_module):
    md = app_module.export_conversation_to_markdown([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    assert "## User" in md
    assert "## Assistant" in md
    assert "hi" in md and "hello" in md


def test_export_includes_metadata_line(app_module):
    md = app_module.export_conversation_to_markdown([
        {"role": "assistant", "content": "answer",
         "path": "coding", "model": "qwen2.5-coder:7b", "tools_used": ["edit_file"]},
    ])
    assert "path: `coding`" in md
    assert "model: `qwen2.5-coder:7b`" in md
    assert "tools: edit_file" in md


def test_export_empty_returns_header(app_module):
    md = app_module.export_conversation_to_markdown([])
    assert md.startswith("# Jarvis Conversation Export")