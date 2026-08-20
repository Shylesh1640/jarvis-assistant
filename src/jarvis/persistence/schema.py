"""Schema versioning and additive migrations (Phase 7).

Replaces the ad-hoc ``ensure_schema`` column patching with a versioned,
idempotent migration runner:

  * a ``schema_version`` table records which schema versions have been applied;
  * ``apply_migrations`` applies every missing migration in ascending order,
    each inside its own transaction;
  * every migration is additive and safe to run on a fresh or existing
    database — existing rows are never rewritten, dropped or truncated.

``engine.create_all`` still creates tables via SQLAlchemy metadata and then
calls ``apply_migrations`` (through ``engine.ensure_schema``), so nothing
outside this module changes behaviour.
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("jarvis.persistence.schema")

SCHEMA_VERSION_TABLE = "schema_version"
LATEST_SCHEMA_VERSION = 1

# Additive migration for the `sessions` table (Phase 6 token security). Moved
# here from ``engine.ensure_schema`` so it is tracked by the versioning.
_NEW_SESSION_COLUMNS: dict[str, dict[str, str]] = {
    "token_hash": {"sqlite": "VARCHAR(512)", "postgresql": "VARCHAR(512)"},
    "token_hash_scheme": {"sqlite": "VARCHAR(16)", "postgresql": "VARCHAR(16)"},
    "token_created_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
    "token_expires_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
    "token_rotated_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
    "token_revoked_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
}


# ---------------------------------------------------------------------------
# migration registry
# ---------------------------------------------------------------------------


def _migration_v1(engine: Engine) -> None:
    """Add the token-security columns to ``sessions`` (idempotent)."""
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("sessions")}
    dialect = engine.dialect.name
    added: list[str] = []
    for col, types in _NEW_SESSION_COLUMNS.items():
        if col in existing:
            continue
        ddl_type = types.get(dialect) or types["sqlite"]
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {ddl_type}"))
        added.append(col)
    if added:
        logger.info("Schema migration v1: added %s to sessions", ", ".join(added))


MIGRATIONS: dict[int, Callable[[Engine], None]] = {
    1: _migration_v1,
}


# ---------------------------------------------------------------------------
# version bookkeeping
# ---------------------------------------------------------------------------


def _ensure_version_table(engine: Engine) -> None:
    if SCHEMA_VERSION_TABLE in inspect(engine).get_table_names():
        return
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    f"CREATE TABLE {SCHEMA_VERSION_TABLE} "
                    "(version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
                )
            )
        else:
            conn.execute(
                text(
                    f"CREATE TABLE {SCHEMA_VERSION_TABLE} "
                    "(version INTEGER PRIMARY KEY, applied_at DATETIME)"
                )
            )


def current_schema_version(engine: Engine | None = None) -> int:
    """Highest recorded schema version; 0 when the table is absent/empty."""
    if engine is None:
        from jarvis.persistence.engine import engine_from_settings

        engine = engine_from_settings()
    try:
        _ensure_version_table(engine)
        with engine.connect() as conn:
            row = conn.execute(text(f"SELECT MAX(version) FROM {SCHEMA_VERSION_TABLE}")).scalar()
        return int(row or 0)
    except Exception:  # noqa: BLE001 — best-effort introspection, never crash
        logger.warning("current_schema_version: introspection failed", exc_info=True)
        return 0


def _record_version(engine: Engine, version: int) -> None:
    from datetime import datetime, timezone

    # naive UTC avoids the deprecated sqlite3 aware-datetime adapter
    applied_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO {SCHEMA_VERSION_TABLE} (version, applied_at) VALUES (:v, :at)"),
            {"v": version, "at": applied_at},
        )


# ---------------------------------------------------------------------------
# runner + validation
# ---------------------------------------------------------------------------


def apply_migrations(engine: Engine | None = None) -> list[int]:
    """Apply every unapplied migration in order; returns applied versions.

    Idempotent: migrations whose version is already recorded are skipped, and
    each migration is applied in its own transaction.
    """
    if engine is None:
        from jarvis.persistence.engine import engine_from_settings

        engine = engine_from_settings()
    _ensure_version_table(engine)
    current = current_schema_version(engine)
    applied: list[int] = []
    for version in range(current + 1, LATEST_SCHEMA_VERSION + 1):
        migration = MIGRATIONS.get(version)
        if migration is None:
            logger.warning("No migration registered for schema version %s", version)
            continue
        try:
            migration(engine)
            _record_version(engine, version)
            applied.append(version)
            logger.info("Schema migration applied: version %s", version)
        except Exception:  # noqa: BLE001 — a failing migration must not crash startup
            logger.error("Schema migration v%s failed; continuing", version, exc_info=True)
            raise
    return applied


def validate_schema(engine: Engine | None = None) -> tuple[bool, list[str]]:
    """Return ``(ok, messages)`` describing schema-vs-code consistency."""
    if engine is None:
        from jarvis.persistence.engine import engine_from_settings

        engine = engine_from_settings()
    messages: list[str] = []
    try:
        _ensure_version_table(engine)
        current = current_schema_version(engine)
    except Exception as exc:  # noqa: BLE001
        return False, [f"schema validation failed: {exc.__class__.__name__}"]

    if current >= LATEST_SCHEMA_VERSION:
        messages.append(f"schema up to date (version {current})")
        return True, messages

    missing = list(range(current + 1, LATEST_SCHEMA_VERSION + 1))
    messages.append(f"schema version {current} is behind code (missing {missing})")
    return False, messages


__all__ = [
    "LATEST_SCHEMA_VERSION",
    "MIGRATIONS",
    "SCHEMA_VERSION_TABLE",
    "apply_migrations",
    "current_schema_version",
    "validate_schema",
]