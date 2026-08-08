"""Tests for the ``list_directory`` workspace tool."""
from __future__ import annotations

import pytest

from jarvis.tools.coding import list_directory


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    """Point ``resolve_in_workspace`` at a controlled temp dir."""
    import jarvis.tools.coding.list_directory as ld

    def _resolve(path, *, must_exist=True):
        from jarvis.tools.coding.paths import WorkspaceError

        target = (tmp_path / path).resolve()
        root = tmp_path.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise WorkspaceError(f"Path escapes workspace: {path}") from None
        if must_exist and not target.exists():
            raise WorkspaceError(f"No such file or directory: {path}")
        return target

    monkeypatch.setattr(ld, "resolve_in_workspace", _resolve)
    return tmp_path


def test_list_directory_mixed_entries(fake_workspace):
    (fake_workspace / "subdir").mkdir()
    (fake_workspace / "a.txt").write_text("a", encoding="utf-8")
    (fake_workspace / "B.py").write_text("b", encoding="utf-8")
    out = list_directory.list_directory.invoke({})
    assert out.startswith("d/")
    assert "f a.txt" in out
    assert "f B.py" in out


def test_list_directory_prefix_distinguishes_dirs_and_files(fake_workspace):
    (fake_workspace / "alpha").mkdir()
    (fake_workspace / "beta").write_text("x", encoding="utf-8")
    out = list_directory.list_directory.invoke({})
    lines = out.splitlines()
    assert any(line.startswith("d/") for line in lines)
    assert any(line.startswith("f ") for line in lines)


def test_list_directory_nonexistent_path_returns_error(fake_workspace):
    out = list_directory.list_directory.invoke({"path": "nope"})
    assert out.startswith("Error:")


def test_list_directory_file_not_directory_returns_error(fake_workspace):
    (fake_workspace / "file.txt").write_text("x", encoding="utf-8")
    out = list_directory.list_directory.invoke({"path": "file.txt"})
    assert "not a directory" in out


def test_list_directory_escape_attempt_returns_error(fake_workspace):
    out = list_directory.list_directory.invoke({"path": ".."})
    assert out.startswith("Error:")