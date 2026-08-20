"""Tests for Phase 7 admin/db CLIs."""
from __future__ import annotations

from jarvis.config.settings import settings
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.persistence.schema import LATEST_SCHEMA_VERSION, current_schema_version


def _db_cli(argv):
    from jarvis.cli.db import main

    return main(argv)


def test_db_migrate_brings_schema_current(capsys):
    reset_engine_for_tests("sqlite:///:memory:")
    assert _db_cli(["status"]) == 1
    assert "0" in capsys.readouterr().out

    assert _db_cli(["migrate"]) == 0
    assert current_schema_version() == LATEST_SCHEMA_VERSION

    assert _db_cli(["status"]) == 0
    assert _db_cli(["check"]) == 0


def test_db_migrate_is_idempotent(capsys):
    reset_engine_for_tests("sqlite:///:memory:")
    assert _db_cli(["migrate"]) == 0
    out = capsys.readouterr().out
    assert "No migrations to apply" in out
    assert _db_cli(["migrate"]) == 0


def test_admin_status_no_secrets(monkeypatch, capsys):
    from jarvis.cli.admin import main

    reset_engine_for_tests("sqlite:///:memory:")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-super-secret-key")
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://user:hunter2@db:5432/jarvis")

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Deployment profile" in out
    assert "sk-super-secret-key" not in out
    assert "hunter2" not in out
    assert "db:5432" not in out


def test_admin_check_flags_bad_production(monkeypatch, capsys):
    from jarvis.cli.admin import main

    monkeypatch.setattr(settings, "deployment_profile", "production")
    monkeypatch.setattr(settings, "jarvis_allowed_origins", "")
    monkeypatch.setattr(settings, "jarvis_force_https", False)
    monkeypatch.setattr(settings, "require_session_token", False)
    monkeypatch.setattr(settings, "jarvis_backup_enabled", False)
    monkeypatch.setattr(settings, "jarvis_expose_traces", True)

    assert main(["check"]) == 1
    out = capsys.readouterr().out
    assert "[WARN]" in out