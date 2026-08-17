"""Tests for the guarded ``run_shell`` tool."""
import pytest

from jarvis.tools.coding.shell import is_safe_command, run_shell


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


def test_metacharacters_blocked_even_for_allowed_head(monkeypatch):
    _allow("ls,pytest,npm,echo", monkeypatch)
    for cmd in [
        "ls; echo hi",
        "ls && whoami",
        "ls || echo hi",
        "ls | grep foo",
        "pytest &",
        "echo $(whoami)",
        "echo `${whoami}`",
        "echo ${HOME}",
        "cat < /etc/passwd",
        "echo ok\necho pwned",
        "git status ; git push",
    ]:
        ok, reason = is_safe_command(cmd)
        assert ok is False, cmd
        assert "metacharacters" in reason


def test_redirect_to_storage_is_an_allowlisted_use(monkeypatch):
    _allow("echo", monkeypatch)
    ok, reason = is_safe_command("echo done > out.txt")
    assert ok is True, reason


def test_run_shell_refuses_metachar_chain(workspace, monkeypatch):
    _allow("echo", monkeypatch)
    out = run_shell.invoke({"command": "echo hi; echo pwned"})
    assert out.startswith("Error")
    assert "metacharacters" in out


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


# ---------------------------------------------------------------------------
# Prefix matching: multi-token allowlist entries grant only that prefix
# ---------------------------------------------------------------------------


def test_multi_token_entry_allows_exact_prefix(monkeypatch):
    _allow("python -m pytest", monkeypatch)
    ok, reason = is_safe_command("python -m pytest tests/test_x.py")
    assert ok is True, reason


def test_multi_token_entry_refuses_different_head_args(monkeypatch):
    _allow("python -m pytest", monkeypatch)
    ok, reason = is_safe_command("python -c 'print(1)'")
    assert ok is False
    assert "allowlist" in reason


def test_multi_token_entry_refuses_partial_prefix(monkeypatch):
    _allow("python -m pytest", monkeypatch)
    ok, reason = is_safe_command("python -m")
    assert ok is False
    assert "allowlist" in reason


def test_single_token_entry_still_allows_subcommands(monkeypatch):
    _allow("git", monkeypatch)
    ok, reason = is_safe_command("git status")
    assert ok is True, reason


def test_default_allowlist_refuses_arbitrary_python(monkeypatch):
    from jarvis.config.settings import settings

    _allow(settings.shell_allowed_commands, monkeypatch)
    ok, reason = is_safe_command("python -c 'print(1)'")
    assert ok is False
    assert "allowlist" in reason
    ok, reason = is_safe_command("python -m pytest")
    assert ok is True, reason


def test_prefix_match_is_case_insensitive(monkeypatch):
    _allow("GIT", monkeypatch)
    ok, reason = is_safe_command("git status")
    assert ok is True, reason
