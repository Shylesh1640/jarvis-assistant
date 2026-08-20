"""Tests for Phase 7 schema versioning + migrations."""
from __future__ import annotations

from sqlalchemy import inspect, text

from jarvis.persistence.engine import create_all, engine_from_settings, reset_engine_for_tests
from jarvis.persistence.schema import (
    LATEST_SCHEMA_VERSION,
    SCHEMA_VERSION_TABLE,
    apply_migrations,
    current_schema_version,
    validate_schema,
)


def _fresh_engine():
    reset_engine_for_tests("sqlite:///:memory:")
    return engine_from_settings()


def test_create_all_records_schema_version():
    eng = _fresh_engine()
    create_all()
    assert SCHEMA_VERSION_TABLE in inspect(eng).get_table_names()
    assert current_schema_version(eng) == LATEST_SCHEMA_VERSION


def test_apply_migrations_is_idempotent():
    eng = _fresh_engine()
    create_all()
    assert apply_migrations(eng) == []
    assert apply_migrations(eng) == []


def test_validate_schema_ok_when_current():
    eng = _fresh_engine()
    create_all()
    ok, messages = validate_schema(eng)
    assert ok is True
    assert any("up to date" in m for m in messages)


def test_validate_schema_detects_missing_version():
    eng = _fresh_engine()
    create_all()
    with eng.begin() as conn:
        conn.execute(text(f"DELETE FROM {SCHEMA_VERSION_TABLE}"))
    ok, messages = validate_schema(eng)
    assert ok is False
    assert any("behind" in m for m in messages)


def test_stale_db_migrated_to_current():
    eng = _fresh_engine()
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE sessions (id INTEGER PRIMARY KEY, title VARCHAR)"))
    applied = apply_migrations(eng)
    assert 1 in applied
    assert current_schema_version(eng) == LATEST_SCHEMA_VERSION
    columns = {c["name"] for c in inspect(eng).get_columns("sessions")}
    assert "token_hash" in columns
    assert "token_expires_at" in columns


def test_validate_schema_fresh_db_ok():
    eng = _fresh_engine()
    create_all()
    ok, _ = validate_schema(eng)
    assert ok is True