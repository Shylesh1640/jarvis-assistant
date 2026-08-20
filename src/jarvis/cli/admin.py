"""Admin CLI (Phase 7) — read-only operational status.

Usage::

    jarvis-admin status           deployment profile, schema, backup status
    jarvis-admin check            deployment validation summary

Read-only: never starts/stops services, never writes data, never deletes
anything, and never prints secrets.
"""
from __future__ import annotations

import argparse
import sys

from jarvis.backup import list_backups
from jarvis.config.deployment import deployment_capability_report, validate_deployment
from jarvis.config.runtime_capabilities import resolve_runtime_mode
from jarvis.config.settings import settings
from jarvis.models.platform_diagnostics import docker_cli_available, get_docker_containers
from jarvis.persistence.schema import LATEST_SCHEMA_VERSION, current_schema_version, validate_schema


def _status() -> int:
    report = deployment_capability_report()
    print("Jarvis operational status")
    print("=" * 40)
    print(f"  Deployment profile : {report['deployment_profile']}")
    print(f"  Runtime mode       : {resolve_runtime_mode()}")
    print(f"  Debug enabled      : {report['debug_enabled']}")
    print(f"  Public exposure safe: {report['public_exposure_safe']}")
    print(f"  Cloud budgeted     : {report['cloud_budget_enforced']}")
    print(f"  Backups configured : {report['database_backup_configured']}")

    ok, messages = validate_schema()
    print("  Schema version     : "
          f"{current_schema_version()} / {LATEST_SCHEMA_VERSION} "
          f"({'OK' if ok else 'BEHIND'})")
    for m in messages:
        print(f"      {m}")

    warnings = validate_deployment()
    if warnings:
        print("  Deployment warnings:")
        for w in warnings:
            print(f"      [WARN] {w}")
    else:
        print("  Deployment warnings: none")

    backup_root = settings.backup_dir
    backups = list_backups(backup_root=backup_root)
    print(f"  Backups            : {len(backups)} in {backup_root}")
    if backups:
        newest = backups[0]
        print(f"      newest: {newest['name']} ({newest['created_at'] or 'unknown'})")

    if docker_cli_available():
        containers, _ = get_docker_containers()
        running = sorted({c["name"] for c in containers})
        print(f"  Docker containers  : {', '.join(running) if running else '(none)'}")

    print("=" * 40)
    if report["warnings"]:
        print("Status: OK with warnings (see above).")
        return 0
    print("Status: OK.")
    return 0


def _check() -> int:
    print("Jarvis deployment check")
    print("=" * 40)
    warnings = validate_deployment()
    if not warnings:
        print("Deployment configuration is valid.")
        return 0
    for w in warnings:
        print(f"  [WARN] {w}")
    return 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-admin", description="Jarvis admin tooling (read-only).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show operational status.")
    sub.add_parser("check", help="Validate the deployment configuration.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "status":
        return _status()
    if args.command == "check":
        return _check()
    print(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())