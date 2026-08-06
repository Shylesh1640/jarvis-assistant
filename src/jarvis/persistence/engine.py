"""Engine + session factory.

Picks Postgres (psycopg) when ``settings.postgres_dsn`` is set, otherwise
falls back to a local SQLite file so the assistant works without Docker.
``create_all`` is idempotent and safe to call from anywhere; tests use a
shared in-memory SQLite URL.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jarvis.config.settings import settings


class Base(DeclarativeBase):
    """Shared declarative base for all Jarvis ORM models."""


_engine_lock = threading.Lock()
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, future=True, connect_args=connect_args)


def engine_from_settings() -> Engine:
    """Lazily build and cache the global engine from settings."""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            url = settings.postgres_dsn or f"sqlite:///{settings.sqlite_path}"
            _engine = _build_engine(url)
            _SessionLocal = sessionmaker(bind=_engine, autoflush=False, future=True)
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
    """Yield a Session and commit/rollback/close around it."""
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


def reset_engine_for_tests(url: str = "sqlite:///:memory:") -> Engine:
    """Used by tests to swap in a fresh in-memory SQLite per process."""
    global _engine, _SessionLocal
    with _engine_lock:
        _engine = _build_engine(url)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, future=True)
    return _engine
