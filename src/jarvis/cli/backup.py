"""Backup / restore CLI (Phase 7).

Usage::

    jarvis-backup create [--dir ROOT] [--include-documents]
    jarvis-backup list [--dir ROOT]
    jarvis-backup verify [--dir ROOT] [BACKUP_ID]
    jarvis-backup delete [--dir ROOT] BACKUP_ID
    jarvis-verify-backup [PATH]

Backups are timestamped folders under ``settings.backup_dir`` (or ``--dir``).
The tooling never deletes backups automatically; ``delete`` is explicit and
refuses anything outside the backup root.
"""
from __future__ import annotations

import argparse
import sys

from jarvis.backup import backup_dir_path, create_backup, delete_backup, list_backups, verify_backup
from jarvis.config.settings import settings


def _print_result(result) -> None:
    print(f"[{'OK' if result.ok else 'FAIL'}] {result.message}")
    for w in result.warnings:
        print(f"  [WARN] {w}")
    for f in result.files:
        print(f"  file: {f}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-backup", description="Jarvis backup tooling.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=None, help="Backup root directory (default: settings.backup_dir).")
    sub = parser.add_subparsers(dest="command", required=True)

    create_p = sub.add_parser("create", parents=[common], help="Create a new backup.")
    create_p.add_argument("--include-documents", action="store_true", help="Include source document files.")
    sub.add_parser("list", parents=[common], help="List existing backups (newest first).")
    verify = sub.add_parser("verify", parents=[common], help="Verify a backup by id or --path.")
    verify.add_argument("backup_id", nargs="?", default=None, help="Backup folder name.")
    verify.add_argument("--path", default=None, help="Full path to the backup folder.")
    delete = sub.add_parser("delete", parents=[common], help="Delete a single backup explicitly.")
    delete.add_argument("backup_id", help="Backup folder name to delete.")
    return parser.parse_args(argv)


def _root(args) -> str:
    return args.dir or backup_dir_path().__str__()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cmd = args.command

    if cmd == "create":
        if not settings.jarvis_backup_enabled:
            print("[WARN] JARVIS_BACKUP_ENABLED=false — backups are not enabled for this deployment.")
        result = create_backup(backup_root=args.dir, include_documents=args.include_documents)
        _print_result(result)
        print(f"Backup at: {result.path}")
        return 0 if result.ok else 1

    if cmd == "list":
        entries = list_backups(backup_root=args.dir)
        if not entries:
            print(f"No backups found in {_root(args)}")
            return 0
        print(f"Backups in {_root(args)}:")
        for e in entries:
            kb = e["size_bytes"] / 1024
            print(f"  {e['name']}  {e['created_at'] or 'unknown'}  {kb:.1f} KB  {e['file_count']} files")
            for w in e["warnings"]:
                print(f"      [WARN] {w}")
        return 0

    if cmd == "verify":
        if args.path:
            result = verify_backup(args.path)
        elif args.backup_id:
            result = verify_backup(f"{_root(args)}/{args.backup_id}")
        else:
            entries = list_backups(backup_root=args.dir)
            if not entries:
                print("No backups to verify.")
                return 1
            result = verify_backup(entries[0]["path"])
        _print_result(result)
        return 0 if result.ok else 1

    if cmd == "delete":
        result = delete_backup(args.backup_id, backup_root=args.dir)
        _print_result(result)
        return 0 if result.ok else 1

    print(f"Unknown command: {cmd}")
    return 2


def verify_latest_main(argv: list[str] | None = None) -> int:
    """Entrypoint for `jarvis-verify-backup`: verifies the newest backup."""
    args = argparse.ArgumentParser(prog="jarvis-verify-backup", description="Verify the newest Jarvis backup.")
    args.add_argument("path", nargs="?", default=None, help="Backup folder path (default: newest).")
    ns = args.parse_args(argv)
    if ns.path:
        result = verify_backup(ns.path)
    else:
        entries = list_backups()
        if not entries:
            print("No backups to verify.")
            return 1
        result = verify_backup(entries[0]["path"])
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())