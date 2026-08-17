"""Tests for runtime-mode resolution and capabilities.

Pure logic — no subprocess, no network. Monkeys runtime_mode/postgres_dsn
on the shared settings singleton and passes docker_reachable explicitly.
"""
from __future__ import annotations

from jarvis.config.runtime_capabilities import (
    RUNTIME_MODES,
    get_runtime_capabilities,
    resolve_runtime_mode,
)
from jarvis.config.settings import settings


def test_valid_modes_are_resolved(monkeypatch):
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://u:p@h/db")
    assert resolve_runtime_mode("local") == "local"
    assert resolve_runtime_mode("docker") == "docker"
    assert resolve_runtime_mode("auto", docker_reachable=True) == "docker"
    assert resolve_runtime_mode("auto", docker_reachable=False) == "local"


def test_invalid_mode_falls_back_to_local():
    assert resolve_runtime_mode("banana") == "local"
    assert resolve_runtime_mode("") == "local"


def test_auto_requires_postgres_to_pick_docker(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "auto")
    monkeypatch.setattr(settings, "postgres_dsn", "")
    assert resolve_runtime_mode("auto", docker_reachable=True) == "local"


def test_auto_prefers_local_without_docker(monkeypatch):
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://u:p@h/db")
    assert resolve_runtime_mode("auto", docker_reachable=False) == "local"
    assert resolve_runtime_mode("auto", docker_reachable=True) == "docker"


def test_runtime_modes_contains_expected_values():
    assert set(RUNTIME_MODES) == {"local", "docker", "auto"}


def test_capabilities_local_defaults(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "local")
    monkeypatch.setattr(settings, "postgres_dsn", "")
    caps = get_runtime_capabilities(docker_reachable=False)
    assert caps["runtime_mode"] == "local"
    assert caps["database_backend"] == "sqlite"
    assert caps["vector_store_backend"] == "chroma_embedded"
    assert caps["task_backend"] == "in_process"
    assert caps["docker_required"] is False
    assert caps["docker_detected"] is False
    assert caps["warnings"] == []


def test_capabilities_local_warns_when_docker_running(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "local")
    monkeypatch.setattr(settings, "postgres_dsn", "")
    caps = get_runtime_capabilities(docker_reachable=True)
    assert caps["docker_detected"] is True
    assert any("RUNTIME_MODE=local" in w for w in caps["warnings"])


def test_capabilities_docker_required(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "docker")
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://u:p@h/db")
    caps = get_runtime_capabilities(docker_reachable=True)
    assert caps["runtime_mode"] == "docker"
    assert caps["database_backend"] == "postgresql"
    assert caps["docker_required"] is True
    assert caps["docker_detected"] is True
    assert caps["warnings"] == []


def test_capabilities_docker_without_postgres_warns(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "docker")
    monkeypatch.setattr(settings, "postgres_dsn", "")
    caps = get_runtime_capabilities(docker_reachable=True)
    assert caps["database_backend"] == "sqlite"
    assert caps["docker_required"] is True
    assert any("POSTGRES_DSN" in w for w in caps["warnings"])


def test_capabilities_docker_daemon_down_warns(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "docker")
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://u:p@h/db")
    caps = get_runtime_capabilities(docker_reachable=False)
    assert caps["docker_required"] is True
    assert caps["docker_detected"] is False
    assert any("not reachable" in w for w in caps["warnings"])


def test_capabilities_local_with_postgres_warns(monkeypatch):
    monkeypatch.setattr(settings, "runtime_mode", "local")
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://u:p@h/db")
    caps = get_runtime_capabilities(docker_reachable=False)
    assert caps["database_backend"] == "postgresql"
    assert any("POSTGRES_DSN" in w for w in caps["warnings"])