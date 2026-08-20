"""Database admin CLI (Phase 7).

Usage::

    jarvis-db status        show current + target schema version
    jarvis-db migrate       apply pending migrations
    jarvis-db check         validate schema consistency

All commands are additive/idempotent; nothing here drops, truncates or
rewrites user data.
"""
from __future__ import annotations

import argparse
import sys

from jarvis.persistence.engine import create_all, engine_from_settings
from jarvis.persistence.schema import (
    LATEST_SCHEMA_VERSION,
    apply_migrations,
    current_schema_version,
    validate_schema,
)


def _status() -> int:
    current = current_schema_version()
    print(f"Schema version: {current} (code target: {LATEST_SCHEMA_VERSION})")
    ok, messages = validate_schema()
    for m in messages:
        print(f"  {'OK' if ok else 'CHECK'}: {m}")
    return 0 if ok else 1


def _migrate() -> int:
    print("Creating tables if missing...")
    create_all()
    print("Applying pending migrations...")
    applied = apply_migrations(engine_from_settings())
    if applied:
        print(f"Applied schema migrations: {applied}")
    else:
        print("No migrations to apply (schema up to date).")
    current = current_schema_version()
    ok, messages = validate_schema()
    for m in messages:
        print(f"  {m}")
    return 0 if ok else 1


def _check() -> int:
    ok, messages = validate_schema()
    for m in messages:
        print(f"  {'OK' if ok else 'CHECK'}: {m}")
    return 0 if ok else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-db", description="Jarvis database admin (additive only).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show schema version.")
    sub.add_parser("migrate", help="Apply pending migrations.")
    sub.add_parser("check", help="Validate schema consistency.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "status":
        return _status()
    if args.command == "migrate":
        return _migrate()
    if args.command == "check":
        return _check()
    print(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())