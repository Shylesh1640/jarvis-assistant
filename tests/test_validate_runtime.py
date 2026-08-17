"""Tests for the validate-runtime CLI helpers.

The CLI talks to Ollama/GPU over the network, so we only exercise the pure
helper functions here: local-storage checks, docker-mode checks, and DSN
parsing — all with mocks, no Docker/WSL/Ollama required.
"""
from __future__ import annotations

import sqlite3

import jarvis.cli.validate_runtime as vr
from jarvis.config.settings import settings


# ---------------------------------------------------------------------------
# DSN parsing (never exposes credentials)
# ---------------------------------------------------------------------------


def test_dsn_host_port_parsed():
    host, port = vr._dsn_host_port("postgresql+psycopg://jarvis:jarvis@db.internal:5433/jarvis")
    assert host == "db.internal"
    assert port == 5433


def test_dsn_host_port_default_port():
    host, port = vr._dsn_host_port("postgresql+psycopg://jarvis:jarvis@localhost/jarvis")
    assert host == "localhost"
    assert port == 5432


def test_dsn_host_port_garbage():
    host, port = vr._dsn_host_port("not a dsn at all")
    assert host is None


# ---------------------------------------------------------------------------
# Local storage checks
# ---------------------------------------------------------------------------


def test_local_storage_all_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "docs_folder", str(tmp_path / "docs"))
    failures = vr._check_local_storage()
    assert failures == 0
    assert (tmp_path / "docs").is_dir()


def test_local_storage_sqlite_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "docs_folder", str(tmp_path / "docs"))

    def _boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(sqlite3, "connect", _boom)
    failures = vr._check_local_storage()
    assert failures >= 1


def test_local_storage_chroma_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "docs_folder", str(tmp_path / "docs"))
    import chromadb

    monkeypatch.setattr(chromadb, "PersistentClient", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    failures = vr._check_local_storage()
    assert failures >= 1


# ---------------------------------------------------------------------------
# Docker-mode checks (read-only, mocked)
# ---------------------------------------------------------------------------


def test_docker_check_cli_missing(monkeypatch):
    monkeypatch.setattr(vr, "docker_cli_available", lambda: False)
    failures = vr._check_docker_runtime()
    assert failures >= 1


def test_docker_check_daemon_down(monkeypatch):
    monkeypatch.setattr(vr, "docker_cli_available", lambda: True)
    monkeypatch.setattr(vr, "docker_daemon_reachable", lambda: (False, ["daemon down"]))
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://jarvis:jarvis@localhost:5432/jarvis")
    failures = vr._check_docker_runtime()
    assert failures >= 1


def test_docker_check_missing_postgres_dsn(monkeypatch):
    monkeypatch.setattr(vr, "docker_cli_available", lambda: True)
    monkeypatch.setattr(vr, "docker_daemon_reachable", lambda: (True, []))
    monkeypatch.setattr(vr, "get_docker_containers", lambda: ([], []))
    monkeypatch.setattr(settings, "postgres_dsn", "")
    failures = vr._check_docker_runtime()
    assert failures >= 1


def test_docker_check_full_success(monkeypatch):
    monkeypatch.setattr(vr, "docker_cli_available", lambda: True)
    monkeypatch.setattr(vr, "docker_daemon_reachable", lambda: (True, []))
    monkeypatch.setattr(vr, "get_docker_containers", lambda: (
        [{"name": "jarvis-postgres"}, {"name": "jarvis-backend"}, {"name": "jarvis-frontend"}], []
    ))
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://jarvis:jarvis@localhost:5432/jarvis")

    class _CtxSocket:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(vr.socket, "create_connection", lambda *a, **k: _CtxSocket())
    failures = vr._check_docker_runtime()
    assert failures == 0