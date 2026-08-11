"""Tests for the secure ``read_file`` tool.

Covers workspace confinement, sensitive-file rejection, size/character
limits, and graceful error strings. The workspace is a temp dir so no real
source tree is touched.
"""
from __future__ import annotations

import os

import pytest

from jarvis.tools.coding.file_ops import read_file
from jarvis.tools.coding.paths import is_sensitive_filename


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.txt").write_text("hello world", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "nested.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(ws)
    )
    monkeypatch.setattr(
        "jarvis.tools.coding.file_ops.settings.max_read_file_bytes", 1_000_000
    )
    monkeypatch.setattr(
        "jarvis.tools.coding.file_ops.settings.max_read_file_chars", 100_000
    )
    return ws


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_read_file_valid_workspace_file(workspace):
    out = read_file.invoke({"file_path": "hello.txt"})
    assert out == "hello world"


def test_read_file_valid_nested_file(workspace):
    out = read_file.invoke({"file_path": "sub/nested.py"})
    assert out == "x = 1\n"


def test_read_file_absolute_path_inside_workspace(workspace):
    out = read_file.invoke({"file_path": str(workspace / "hello.txt")})
    assert out == "hello world"


# ---------------------------------------------------------------------------
# path traversal / escapes
# ---------------------------------------------------------------------------


def test_read_file_dotdot_traversal_rejected(workspace):
    out = read_file.invoke({"file_path": "../hello.txt"})
    assert out.startswith("Error")
    assert "escapes" in out


def test_read_file_absolute_external_path_rejected(workspace, tmp_path):
    outside = tmp_path / "secret_outside.txt"
    outside.write_text("nope", encoding="utf-8")
    out = read_file.invoke({"file_path": str(outside)})
    assert out.startswith("Error")
    assert "escapes" in out


def test_read_file_symlink_escape_rejected(workspace, tmp_path):
    target = tmp_path / "outside_target.txt"
    target.write_text("leak", encoding="utf-8")
    link = workspace / "evil_link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlink on this platform: {exc}")
    out = read_file.invoke({"file_path": "evil_link.txt"})
    assert out.startswith("Error")
    assert "escapes" in out


# ---------------------------------------------------------------------------
# sensitive files
# ---------------------------------------------------------------------------

_SENSITIVE_CASES = ["", ".env", ".env.local", ".env.example"]


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.example"])
def test_is_sensitive_filename_dotenv(name):
    assert is_sensitive_filename(name)


@pytest.mark.parametrize("name", ["id_rsa", "id_rsa.pub", "mykey.pem", "key.p12"])
def test_is_sensitive_filename_private_keys(name):
    assert is_sensitive_filename(name)


@pytest.mark.parametrize("name", ["credentials.json", "secrets.yaml", "secret.txt"])
def test_is_sensitive_filename_credentials(name):
    assert is_sensitive_filename(name)


def test_is_sensitive_filename_allows_normal_files():
    assert not is_sensitive_filename("app.py")
    assert not is_sensitive_filename("notes.md")
    assert not is_sensitive_filename("subfolder/config.py")


def test_read_file_rejects_dotenv(workspace):
    (workspace / ".env").write_text("OPENROUTER_API_KEY=sk-super-secret", encoding="utf-8")
    out = read_file.invoke({"file_path": ".env"})
    assert out.startswith("Error")
    assert "sensitive" in out.lower()
    assert "sk-super-secret" not in out


def test_read_file_rejects_private_key(workspace):
    (workspace / "id_rsa").write_text("PRIVATE KEY MATERIAL", encoding="utf-8")
    out = read_file.invoke({"file_path": "id_rsa"})
    assert out.startswith("Error")
    assert "sensitive" in out.lower()


# ---------------------------------------------------------------------------
# size / character limits
# ---------------------------------------------------------------------------


def test_read_file_oversized_rejected(workspace, monkeypatch):
    monkeypatch.setattr(
        "jarvis.tools.coding.file_ops.settings.max_read_file_bytes", 10
    )
    (workspace / "big.txt").write_text("this file is far too large", encoding="utf-8")
    out = read_file.invoke({"file_path": "big.txt"})
    assert out.startswith("Error")
    assert "10-byte limit" in out


def test_read_file_output_truncated(workspace, monkeypatch):
    monkeypatch.setattr(
        "jarvis.tools.coding.file_ops.settings.max_read_file_chars", 5
    )
    (workspace / "long.txt").write_text("abcdefghijklmnop", encoding="utf-8")
    out = read_file.invoke({"file_path": "long.txt"})
    assert out.startswith("abcde")
    assert "truncated" in out
    assert "abcdefghijklmnop" not in out


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_read_file_missing_file_returns_error(workspace):
    out = read_file.invoke({"file_path": "does_not_exist.txt"})
    assert out.startswith("Error")
    assert "does not exist" in out


def test_read_file_permission_error(workspace, monkeypatch):
    from pathlib import Path

    def _boom_read_text(self, encoding="utf-8"):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_text", _boom_read_text)
    out = read_file.invoke({"file_path": "hello.txt"})
    assert out.startswith("Error")


def test_read_file_empty_result_ok(workspace):
    (workspace / "empty.txt").write_text("", encoding="utf-8")
    out = read_file.invoke({"file_path": "empty.txt"})
    assert out == ""