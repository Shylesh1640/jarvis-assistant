"""Tests for ``jarvis.tools.coding.paths`` (workspace path guard)."""
from pathlib import Path

import pytest

from jarvis.tools.coding.paths import (
    WorkspaceError,
    resolve_in_workspace,
    workspace_root,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "file.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(ws)
    )
    return ws


def test_workspace_root_created_when_missing(tmp_path, monkeypatch):
    target = tmp_path / "fresh"
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(target)
    )
    root = workspace_root()
    assert root.exists()
    assert root == target.resolve()


def test_resolve_relative_under_root(workspace):
    resolved = resolve_in_workspace("sub/file.txt", must_exist=True)
    assert resolved == (workspace / "sub" / "file.txt").resolve()


def test_resolve_absolute_inside_root(workspace):
    abs_path = workspace / "sub" / "file.txt"
    resolved = resolve_in_workspace(str(abs_path), must_exist=True)
    assert resolved == abs_path.resolve()


def test_resolve_absolute_outside_root_rejected(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_in_workspace(str(outside))


def test_resolve_dot_dot_traversal_rejected(workspace):
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_in_workspace("../escape.txt")


def test_resolve_must_exist_missing_raises(workspace):
    with pytest.raises(WorkspaceError, match="does not exist"):
        resolve_in_workspace("nope.txt", must_exist=True)


def test_resolve_nonexistent_no_must_exist_ok(workspace):
    resolved = resolve_in_workspace("newfile.txt")
    assert resolved == (workspace / "newfile.txt").resolve()
