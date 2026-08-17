"""Tests for platform diagnostics (Docker + WSL).

Mocks shutil.which / subprocess.run so nothing ever invokes the real
docker or wsl CLI. Also asserts the .wslconfig probe never leaks content.
"""
from __future__ import annotations

import jarvis.models.platform_diagnostics as pd


class _P:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_which(monkeypatch, results):
    """results: dict mapping command name -> Path-or-None."""
    monkeypatch.setattr(pd.shutil, "which", lambda name: results.get(name))


def _patch_run(monkeypatch, fake):
    monkeypatch.setattr(pd.subprocess, "run", lambda *a, **k: fake)


# ---------------------------------------------------------------------------
# docker CLI / daemon
# ---------------------------------------------------------------------------


def test_docker_cli_available_true(monkeypatch):
    _patch_which(monkeypatch, {"docker": "/usr/bin/docker"})
    assert pd.docker_cli_available() is True


def test_docker_cli_available_false(monkeypatch):
    _patch_which(monkeypatch, {})
    assert pd.docker_cli_available() is False


def test_daemon_reachable_success(monkeypatch):
    _patch_which(monkeypatch, {"docker": "/usr/bin/docker"})
    _patch_run(monkeypatch, _P(0, "Client:\n Server Version: 27.1\n"))
    ok, warns = pd.docker_daemon_reachable()
    assert ok is True
    assert warns == []


def test_daemon_reachable_cli_missing(monkeypatch):
    _patch_which(monkeypatch, {})
    ok, warns = pd.docker_daemon_reachable()
    assert ok is False
    assert any("not found" in w for w in warns)


def test_daemon_reachable_nonzero_exit(monkeypatch):
    _patch_which(monkeypatch, {"docker": "/usr/bin/docker"})
    _patch_run(monkeypatch, _P(1, "", "Cannot connect to the Docker daemon"))
    ok, warns = pd.docker_daemon_reachable()
    assert ok is False
    assert any("daemon" in w for w in warns)


def test_daemon_reachable_timeout(monkeypatch):
    import subprocess

    _patch_which(monkeypatch, {"docker": "/usr/bin/docker"})

    def _hang(*a, **k):
        raise subprocess.TimeoutExpired(["docker", "info"], timeout=5)

    monkeypatch.setattr(pd.subprocess, "run", _hang)
    ok, warns = pd.docker_daemon_reachable()
    assert ok is False
    assert any("timed out" in w for w in warns)


# ---------------------------------------------------------------------------
# containers / disk usage
# ---------------------------------------------------------------------------


def test_containers_parsed(monkeypatch):
    _patch_which(monkeypatch, {"docker": "/usr/bin/docker"})
    out = "jarvis-postgres\tUp 5 minutes\tpostgres:16-alpine\njarvis-backend\tUp 5 minutes\tjarvis-backend:latest\n"
    _patch_run(monkeypatch, _P(0, out))
    rows, warns = pd.get_docker_containers()
    assert len(rows) == 2
    assert rows[0]["name"] == "jarvis-postgres"
    assert rows[0]["image"] == "postgres:16-alpine"
    assert warns == []


def test_containers_cli_missing(monkeypatch):
    _patch_which(monkeypatch, {})
    rows, warns = pd.get_docker_containers()
    assert rows == []
    assert any("not found" in w for w in warns)


def test_disk_usage_parsed(monkeypatch):
    _patch_which(monkeypatch, {"docker": "/usr/bin/docker"})
    out = (
        "Images\t3\t2\t1.2GB\t300MB\n"
        "Containers\t4\t2\t50MB\t20MB\n"
        "Local Volumes\t1\t1\t500MB\t0B\n"
        "Build Cache\t0\t0\t0B\t0B\n"
    )
    _patch_run(monkeypatch, _P(0, out))
    usage, warns = pd.get_docker_disk_usage()
    assert usage["Images"]["total"] == "3"
    assert usage["Containers"]["active"] == "2"
    assert warns == []


def test_disk_usage_cli_missing(monkeypatch):
    _patch_which(monkeypatch, {})
    usage, warns = pd.get_docker_disk_usage()
    assert usage == {}
    assert any("not found" in w for w in warns)


# ---------------------------------------------------------------------------
# WSL probes
# ---------------------------------------------------------------------------


def test_wsl_info_runs_wsl2(monkeypatch):
    _patch_which(monkeypatch, {"wsl": r"C:\Windows\System32\wsl.exe"})
    out = (
        "  NAME                   STATE           VERSION\n"
        "* Ubuntu                 Running         2\n"
        "  docker-desktop         Stopped         2\n"
    )
    _patch_run(monkeypatch, _P(0, out))
    monkeypatch.setattr(pd.os.path, "expanduser", lambda p: r"C:\Users\test\\.wslconfig")
    monkeypatch.setattr(pd.os.path, "isfile", lambda p: False)
    info, warns = pd.get_wsl_info()
    assert info["available"] is True
    assert info["wsl2_enabled"] is True
    assert info["default_distro"] == "Ubuntu"
    assert len(info["distributions"]) == 2
    assert warns == []


def test_wsl_info_cli_missing(monkeypatch):
    _patch_which(monkeypatch, {})
    info, warns = pd.get_wsl_info()
    assert info["available"] is False
    assert any("not found" in w for w in warns)


def test_wsl_info_parse_single_column_tolerated(monkeypatch):
    """A row with fewer columns must not crash the parser."""
    rows = pd._parse_wsl_list("  NAME   STATE  VERSION\nnot-a-distro\n")
    assert rows == []


def test_wsl_info_ignores_header(monkeypatch):
    rows = pd._parse_wsl_list("  NAME  STATE  VERSION\n* Foo  Running  1\n")
    assert len(rows) == 1
    assert rows[0]["name"] == "Foo"
    assert rows[0]["version"] == 1


def test_wslconfig_presence_no_file(monkeypatch):
    monkeypatch.setattr(pd.os.path, "expanduser", lambda p: r"C:\Users\test\\.wslconfig")
    monkeypatch.setattr(pd.os.path, "isfile", lambda p: False)
    present, keys = pd._wslconfig_presence()
    assert present is False
    assert keys == {}


def test_wslconfig_presence_detects_keys_without_leaking_values(monkeypatch, tmp_path):
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("memory=8GB\nprocessors=4\nswap=2GB\n[wsl2]\nautoMemoryReclaim=gradual\n")
    import builtins

    monkeypatch.setattr(pd.os.path, "expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(pd.os.path, "isfile", lambda p: True)
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda *a, **k: real_open(*a, **k))
    present, keys = pd._wslconfig_presence()
    assert present is True
    assert keys["memory"] is True
    assert keys["processors"] is True
    assert keys["swap"] is True
    assert keys["autoMemoryReclaim"] is True
    # Values must never be exposed.
    assert "8GB" not in str(keys)


# ---------------------------------------------------------------------------
# aggregate block
# ---------------------------------------------------------------------------


def test_docker_wsl_diagnostics_block(monkeypatch):
    _patch_which(monkeypatch, {"docker": "/usr/bin/docker", "wsl": r"C:\Windows\System32\wsl.exe"})
    _patch_run(monkeypatch, _P(0, "jarvis-postgres\tUp\tpostgres:16-alpine\n"))
    monkeypatch.setattr(pd.os.path, "expanduser", lambda p: r"C:\Users\test\\.wslconfig")
    monkeypatch.setattr(pd.os.path, "isfile", lambda p: False)
    block = pd.get_docker_wsl_diagnostics()
    assert block["docker"]["cli_available"] is True
    assert block["docker"]["daemon_reachable"] is True
    assert block["docker"]["containers"][0]["name"] == "jarvis-postgres"
    assert block["wsl"]["available"] is True


def test_docker_wsl_diagnostics_nothing_installed(monkeypatch):
    _patch_which(monkeypatch, {})
    monkeypatch.setattr(pd.os.path, "expanduser", lambda p: r"C:\Users\test\\.wslconfig")
    monkeypatch.setattr(pd.os.path, "isfile", lambda p: False)
    block = pd.get_docker_wsl_diagnostics()
    assert block["docker"]["cli_available"] is False
    assert block["docker"]["daemon_reachable"] is False
    assert block["wsl"]["available"] is False
    assert any("not found" in w for w in block["docker"]["warnings"])
    assert any("not found" in w for w in block["wsl"]["warnings"])