"""Tests for write_file / edit_file coding tools."""
from pathlib import Path

import pytest

from jarvis.tools.coding.paths import WorkspaceError
from jarvis.tools.coding.write_ops import edit_file, write_file


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(ws)
    )
    return ws


def test_write_file_creates_file_with_content(workspace):
    out = write_file.invoke({"file_path": "notes.txt", "content": "hello world"})
    assert out.startswith("Wrote ")
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "hello world"


def test_write_file_creates_parent_dirs(workspace):
    out = write_file.invoke(
        {"file_path": "a/b/c.txt", "content": "deep"}
    )
    assert "Wrote" in out
    assert (workspace / "a" / "b" / "c.txt").read_text() == "deep"


def test_write_file_overwrites_existing(workspace):
    (workspace / "f.txt").write_text("old", encoding="utf-8")
    write_file.invoke({"file_path": "f.txt", "content": "new"})
    assert (workspace / "f.txt").read_text() == "new"


def test_write_file_rejects_escape(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    out = write_file.invoke({"file_path": str(outside), "content": "x"})
    assert out.startswith("Error")
    assert not outside.exists()
    assert "escapes" in out or "Error" in out


def test_edit_file_replaces_unique_anchor(workspace):
    (workspace / "f.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )
    out = edit_file.invoke(
        {
            "file_path": "f.py",
            "old_string": "return 1",
            "new_string": "return 42",
        }
    )
    assert "Edited" in out
    assert "return 42" in (workspace / "f.py").read_text()
    assert "return 1" not in (workspace / "f.py").read_text()


def test_edit_file_missing_anchor_error(workspace):
    (workspace / "f.py").write_text("x = 1\n")
    out = edit_file.invoke(
        {"file_path": "f.py", "old_string": "nope", "new_string": "y"}
    )
    assert out.startswith("Error")
    assert "not found" in out


def test_edit_file_multiple_anchors_error(workspace):
    (workspace / "f.py").write_text("dup\ndup\n")
    out = edit_file.invoke(
        {"file_path": "f.py", "old_string": "dup", "new_string": "x"}
    )
    assert out.startswith("Error")
    assert "2 times" in out


def test_edit_file_identical_strings_error(workspace):
    (workspace / "f.py").write_text("x = 1\n")
    out = edit_file.invoke(
        {"file_path": "f.py", "old_string": "x", "new_string": "x"}
    )
    assert out.startswith("Error")
    assert "identical" in out


def test_edit_file_missing_file_error(workspace):
    out = edit_file.invoke(
        {"file_path": "ghost.txt", "old_string": "a", "new_string": "b"}
    )
    assert out.startswith("Error")


def test_edit_file_path_escape_rejected(workspace, tmp_path):
    outside = tmp_path / "out.txt"
    outside.write_text("original", encoding="utf-8")
    out = edit_file.invoke(
        {"file_path": str(outside), "old_string": "original", "new_string": "hacked"}
    )
    assert out.startswith("Error")
    assert outside.read_text() == "original"
