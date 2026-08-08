"""Tests for the read-only ``git_diff`` tool."""
from __future__ import annotations

from unittest.mock import patch

from jarvis.tools.coding.git_diff import _validate_flags, git_diff


def test_validate_flags_accepts_allowed_set():
    allowed, err = _validate_flags("--stat --name-only --cached --numstat -U3")
    assert err is None
    assert allowed == ["--stat", "--name-only", "--cached", "--numstat", "-U3"]


def test_validate_flags_rejects_unsupported():
    allowed, err = _validate_flags("--ignore-space-change")
    assert allowed == []
    assert "Unsupported git diff flag" in err


def test_validate_flags_rejects_bare_argument():
    allowed, err = _validate_flags("HEAD")
    assert allowed == []
    assert "Unsupported git diff flag" in err


def test_git_diff_returns_diff_output():
    proc = type("Proc", (), {"returncode": 0, "stdout": "--- a/f\n+++ b/f\n", "stderr": ""})()
    with patch("jarvis.tools.coding.git_diff.subprocess.run", return_value=proc):
        out = git_diff.invoke({})
    assert "--- a/f" in out


def test_git_diff_clean_tree_message():
    proc = type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    with patch("jarvis.tools.coding.git_diff.subprocess.run", return_value=proc):
        out = git_diff.invoke({})
    assert out == "(no changes — working tree is clean)"


def test_git_diff_not_a_repository():
    proc = type("Proc", (), {"returncode": 128, "stdout": "", "stderr": "fatal: not a git repository"})()
    with patch("jarvis.tools.coding.git_diff.subprocess.run", return_value=proc):
        out = git_diff.invoke({})
    assert "not a git repository" in out


def test_git_diff_truncates_large_output():
    big = "x" * 9000
    proc = type("Proc", (), {"returncode": 0, "stdout": big, "stderr": ""})()
    with patch("jarvis.tools.coding.git_diff.subprocess.run", return_value=proc):
        out = git_diff.invoke({})
    assert len(out) <= 9000
    assert "truncated" in out


def test_git_diff_timeout_returns_error():
    with patch(
        "jarvis.tools.coding.git_diff.subprocess.run",
        side_effect=TimeoutError("timed out"),
    ):
        out = git_diff.invoke({})
    assert "timed out" in out


def test_git_diff_missing_binary_returns_error():
    with patch(
        "jarvis.tools.coding.git_diff.subprocess.run",
        side_effect=FileNotFoundError("git"),
    ):
        out = git_diff.invoke({})
    assert "not installed" in out
