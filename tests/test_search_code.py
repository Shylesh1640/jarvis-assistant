"""Tests for the ``search_code`` tool."""
import pytest

from jarvis.tools.general.search_code import search_code


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (ws / "b.py").write_text("def bar():\n    return foo() + 2\n", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "c.md").write_text("# Title\nsome text here\n", encoding="utf-8")
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(ws)
    )
    return ws


def test_search_finds_matches(workspace):
    out = search_code.invoke({"pattern": "def (foo|bar)", "path": "."})
    assert "a.py:1:" in out
    assert "b.py:1:" in out


def test_search_no_matches(workspace):
    out = search_code.invoke({"pattern": "ZZZnope", "path": "."})
    assert "No matches" in out


def test_search_empty_pattern_error(workspace):
    out = search_code.invoke({"pattern": "", "path": "."})
    assert out.startswith("Error")


def test_search_invalid_regex_error(workspace):
    out = search_code.invoke({"pattern": "(unclosed", "path": "."})
    assert out.startswith("Error")
    assert "regex" in out


def test_search_scope_to_single_file(workspace):
    out = search_code.invoke({"pattern": "foo", "path": "a.py"})
    assert "a.py:1:" in out
    assert "b.py" not in out


def test_search_path_escape_rejected(workspace, tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "x.py").write_text("foo\n")
    out = search_code.invoke({"pattern": "foo", "path": str(outside)})
    assert out.startswith("Error")


def test_search_truncates_at_limit(workspace):
    (workspace / "big.py").write_text("x = 1\n" * 100, encoding="utf-8")
    out = search_code.invoke({"pattern": "x = 1", "path": "."})
    assert "truncated" in out
