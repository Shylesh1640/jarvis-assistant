"""Tests for the guarded ``run_shell`` tool."""
import pytest

from jarvis.tools.coding.shell import _summarize_lines, is_safe_command, run_shell


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(ws)
    )
    return ws


def _allow(commands: str, monkeypatch) -> None:
    monkeypatch.setattr(
        "jarvis.tools.coding.shell.settings.shell_allowed_commands", commands
    )


# ---------------------------------------------------------------------------
# is_safe_command
# ---------------------------------------------------------------------------


def test_safe_allowed_command(monkeypatch):
    _allow("ls,git,pytest", monkeypatch)
    ok, reason = is_safe_command("git status")
    assert ok is True
    assert reason is None


def test_blocked_destructive_pattern(monkeypatch):
    _allow("rm", monkeypatch)
    ok, reason = is_safe_command("rm -rf /")
    assert ok is False
    assert "destructive" in reason


def test_command_not_in_allowlist(monkeypatch):
    _allow("ls", monkeypatch)
    ok, reason = is_safe_command("curl http://x")
    assert ok is False
    assert "allowlist" in reason


def test_empty_command_rejected():
    ok, reason = is_safe_command("")
    assert ok is False
    assert "empty" in reason


def test_blocked_patterns_table():
    for cmd in [
        "sudo something",
        "shutdown -h now",
        "dd if=/dev/zero",
        "mkfs.ext4 /dev/sda",
        "DROP TABLE users",
        "TRUNCATE foo",
    ]:
        ok, _ = is_safe_command(cmd)
        assert ok is False, cmd


# ---------------------------------------------------------------------------
# run_shell integration
# ---------------------------------------------------------------------------


def test_run_shell_executes_allowed_command(workspace, monkeypatch):
    _allow("echo", monkeypatch)
    out = run_shell.invoke({"command": "echo hello-jarvis"})
    assert "hello-jarvis" in out
    assert "exit=0" in out


def test_run_shell_refuses_blocked_command(workspace, monkeypatch):
    _allow("echo,rm", monkeypatch)
    out = run_shell.invoke({"command": "rm -rf ."})
    assert out.startswith("Error")
    assert "destructive" in out or "blocked" in out


def test_run_shell_refuses_disallowed_head(workspace, monkeypatch):
    _allow("ls", monkeypatch)
    out = run_shell.invoke({"command": "python -c 'print(1)'"})
    assert out.startswith("Error")
    assert "allowlist" in out


def test_summarize_lines_helper():
    assert _summarize_lines(["a", "b", "c"], limit=2) == "a\nb\n..."
