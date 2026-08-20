"""Engine + session factory.

Picks Postgres (psycopg) when ``settings.postgres_dsn`` is set, otherwise
falls back to a local SQLite file so the assistant works without Docker.
``create_all`` is idempotent and safe to call from anywhere; tests use a
shared in-memory SQLite URL.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager, nullcontext
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from jarvis.config.settings import settings


class Base(DeclarativeBase):
    """Shared declarative base for all Jarvis ORM models."""


_engine_lock = threading.Lock()
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None

# Guards the shared single connection used by in-memory SQLite (StaticPool).
# The task executor and request threads hit the same connection, so all
# access is serialised for that engine type to avoid cursor corruption.
_shared_conn_lock = threading.Lock()
_is_static_pool = False


def _build_engine(url: str) -> Engine:
    global _is_static_pool
    _is_static_pool = False
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # An in-memory SQLite DB is per-connection; a StaticPool makes every
        # connection (including background task worker threads) share the
        # same underlying in-memory database.
        if url.startswith("sqlite:///:memory:") or url == "sqlite://":
            _is_static_pool = True
            return create_engine(
                url,
                future=True,
                connect_args=connect_args,
                poolclass=StaticPool,
            )
    return create_engine(url, future=True, connect_args=connect_args)


def engine_from_settings() -> Engine:
    """Lazily build and cache the global engine from settings."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            url = settings.postgres_dsn or f"sqlite:///{settings.sqlite_path}"
            _engine = _build_engine(url)
            # Keep loaded attributes populated after the session closes so
            # repo methods can return ORM instances safely.
            _SessionLocal = sessionmaker(
                bind=_engine, autoflush=False, future=True, expire_on_commit=False
            )
    return _engine


def _ensure_session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        engine_from_settings()
    assert _SessionLocal is not None
    return _SessionLocal


def SessionLocal() -> sessionmaker[Session]:  # noqa: N802 - mimic SQLAlchemy API
    return _ensure_session_factory()


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a Session and commit/rollback/close around it.

    In-memory SQLite uses a single shared StaticPool connection that the
    task executor and request threads both hit, so access is serialised
    with ``_shared_conn_lock`` to avoid cursor corruption. Other engines
    (file SQLite, Postgres) get a per-connection pool and no lock.
    """
    lock = _shared_conn_lock if _is_static_pool else nullcontext()
    with lock:
        factory = _ensure_session_factory()
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def create_all() -> None:
    """Create every table if it doesn't already exist."""
    import jarvis.persistence.models  # noqa: F401 — register models on Base

    Base.metadata.create_all(engine_from_settings())
    ensure_schema()


# Phase 6 :: additive schema migration for the `sessions` table. ``create_all``
# does not alter existing tables, so new columns added by newer code versions
# are applied here with a plain ALTER TABLE. Only columns that are missing are
# added; existing rows keep their values (token security migrates lazily on
# first use).
_NEW_SESSION_COLUMNS: dict[str, dict[str, str]] = {
    "token_hash": {"sqlite": "VARCHAR(512)", "postgresql": "VARCHAR(512)"},
    "token_hash_scheme": {"sqlite": "VARCHAR(16)", "postgresql": "VARCHAR(16)"},
    "token_created_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
    "token_expires_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
    "token_rotated_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
    "token_revoked_at": {"sqlite": "DATETIME", "postgresql": "TIMESTAMP"},
}


def ensure_schema() -> None:
    """Additively migrate the schema to match the current models (idempotent)."""
    import logging

    from sqlalchemy import inspect, text

    logger = logging.getLogger("jarvis.persistence.engine")
    eng = engine_from_settings()
    insp = inspect(eng)
    try:
        if "sessions" not in insp.get_table_names():
            return
        existing = {c["name"] for c in insp.get_columns("sessions")}
    except Exception:  # noqa: BLE001 — best-effort migration, never crash startup
        logger.warning("ensure_schema: could not inspect sessions table", exc_info=True)
        return
    dialect = eng.dialect.name
    added: list[str] = []
    for col, types in _NEW_SESSION_COLUMNS.items():
        if col in existing:
            continue
        ddl_type = types.get(dialect) or types["sqlite"]
        try:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {ddl_type}"))
            added.append(col)
        except Exception:  # noqa: BLE001
            logger.warning("ensure_schema: failed to add column %s", col, exc_info=True)
    if added:
        logger.info("Schema migration: added %s to sessions", ", ".join(added))


def reset_engine_for_tests(url: str = "sqlite:///:memory:") -> Engine:
    """Used by tests to swap in a fresh in-memory SQLite per process."""
    global _engine, _SessionLocal
    with _engine_lock:
        _engine = _build_engine(url)
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, future=True, expire_on_commit=False
        )
    return _engine
