"""Core backup tooling (Phase 7).

``create_backup`` snapshots the assistant's state into a timestamped folder
under ``settings.backup_dir``:

  * ``manifest.json``  — metadata + sha256 checksums (never contains secrets)
  * ``jarvis.db``      — consistent SQLite snapshot (via the sqlite backup API)
  * ``vector_store/``  — the embedded ChromaDB directory tree
  * ``docs/``          — source document files (only when include_documents)

Design rules honoured here:

  * Backups are **never deleted automatically**. ``delete_backup`` requires an
    explicit id and refuses anything outside ``backup_dir``.
  * No secrets are ever written to the manifest or file names: no DSNs, API
    keys, session tokens or passwords. The manifest carries tool/version
    metadata and checksums only.
  * Postgres is backed up best-effort via ``pg_dump`` when the CLI is present;
    the file-based backup always works and is the local-mode default.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from jarvis.config.settings import settings

BACKUP_PREFIX = "backup_"
MANIFEST_NAME = "manifest.json"
BACKUP_VERSION = 1

_FORBIDDEN_MANIFEST_KEYS = (
    "dsn",
    "token",
    "secret",
    "password",
    "api_key",
    "openrouter",
)


@dataclass
class BackupResult:
    """Result of a backup/verification run."""

    path: str
    ok: bool
    message: str
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checksums: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def backup_dir_path() -> Path:
    """Absolute path of the backups root directory."""
    return Path(settings.backup_dir).resolve()


def _new_backup_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = root / f"{BACKUP_PREFIX}{stamp}"
    n = 1
    while path.exists():
        path = root / f"{BACKUP_PREFIX}{stamp}_{n}"
        n += 1
    return path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_sqlite(src: Path, dest: Path, warnings: list[str]) -> bool:
    """Consistent SQLite copy via the backup API; falls back to file copy."""
    if not src.exists():
        return False
    try:
        src_conn = sqlite3.connect(str(src))
        try:
            dest_conn = sqlite3.connect(str(dest))
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()
        finally:
            src_conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"sqlite backup API failed ({exc.__class__.__name__}); using file copy")
        try:
            shutil.copy2(src, dest)
            return True
        except OSError:
            return False


def _copy_tree(src: Path, dest: Path, warnings: list[str]) -> list[Path]:
    """Recursively copy a directory, skipping Chroma lock/tmp files."""
    copied: list[Path] = []
    if not src.is_dir():
        return copied
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        name = item.name.lower()
        if name in ("lock", "lockfile") or name.startswith("."):
            continue
        rel = item.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item, target)
            copied.append(target)
        except OSError as exc:
            warnings.append(f"could not copy {item}: {exc.__class__.__name__}")
    return copied


def create_backup(
    backup_root: str | None = None,
    include_documents: bool | None = None,
) -> BackupResult:
    """Create a new backup and return the result.

    *backup_root* overrides ``settings.backup_dir``; *include_documents*
    overrides ``settings.backup_include_documents``.
    """
    root = Path(backup_root).resolve() if backup_root else backup_dir_path()
    root.mkdir(parents=True, exist_ok=True)
    include = settings.backup_include_documents if include_documents is None else include_documents

    target = _new_backup_path(root)
    target.mkdir(parents=True)
    checksums: dict[str, str] = {}
    warnings: list[str] = []
    files: list[str] = []

    if settings.postgres_dsn:
        dump = shutil.which("pg_dump")
        if dump:
            db_file = target / "jarvis.db"
            import subprocess

            dsn = settings.postgres_dsn
            try:
                with open(db_file, "wb") as out:
                    subprocess.run(
                        [dump, "--no-password", "--format=custom", dsn],
                        stdout=out,
                        check=False,
                        timeout=120,
                    )
                if db_file.stat().st_size > 0:
                    files.append(str(db_file))
                    checksums[str(db_file)] = _sha256(db_file)
                else:
                    warnings.append("pg_dump produced an empty output; database not backed up")
                    db_file.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"pg_dump failed ({exc.__class__.__name__}); database not backed up")
                db_file.unlink(missing_ok=True)
        else:
            warnings.append("POSTGRES_DSN is set but `pg_dump` is missing; database not backed up")
    else:
        db_src = Path(settings.sqlite_path)
        if db_src.exists():
            db_dest = target / "jarvis.db"
            if _snapshot_sqlite(db_src, db_dest, warnings):
                files.append(str(db_dest))
                checksums[str(db_dest)] = _sha256(db_dest)
            else:
                warnings.append(f"could not back up SQLite file {db_src}")
        else:
            warnings.append(f"no SQLite file at {db_src}; database not backed up")

    vector_src = Path(settings.vector_db_path)
    if vector_src.is_dir():
        vector_dest = target / "vector_store"
        copied = _copy_tree(vector_src, vector_dest, warnings)
        for p in copied:
            files.append(str(p))
            checksums[str(p)] = _sha256(p)
    else:
        warnings.append(f"vector store not found at {vector_src}; skipped")

    if include:
        docs_src = Path(settings.docs_folder)
        if docs_src.is_dir():
            docs_dest = target / "docs"
            copied = _copy_tree(docs_src, docs_dest, warnings)
            for p in copied:
                files.append(str(p))
                checksums[str(p)] = _sha256(p)
        else:
            warnings.append(f"docs folder not found at {docs_src}; skipped")

    manifest = {
        "backup_version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "deployment_profile": settings.deployment_profile,
        "include_documents": include,
        "file_count": len(files),
        "files": files,
        "checksums": checksums,
        "warnings": warnings,
    }
    manifest_path = target / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    ok = True
    message = "backup created"
    if warnings:
        ok = False
        message = "backup created with warnings"
    return BackupResult(
        path=str(target),
        ok=ok,
        message=message,
        files=files,
        warnings=warnings,
        checksums=checksums,
    )


def verify_backup(backup_path: str | Path) -> BackupResult:
    """Verify a backup folder: manifest, file presence, checksums, DB health."""
    target = Path(backup_path).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    manifest_file = target / MANIFEST_NAME
    if not manifest_file.is_file():
        return BackupResult(path=str(target), ok=False, message="missing manifest.json", warnings=["not a jarvis backup"])

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return BackupResult(path=str(target), ok=False, message=f"unreadable manifest: {exc.__class__.__name__}")

    if manifest.get("backup_version") != BACKUP_VERSION:
        errors.append("unsupported backup version")

    recorded = manifest.get("files") or []
    for rel in recorded:
        f = Path(rel)
        if not f.is_absolute() or target not in f.parents:
            errors.append(f"manifest entry outside backup: {rel}")
            continue
        if not f.is_file():
            errors.append(f"missing file: {f.name}")
            continue
        expected = (manifest.get("checksums") or {}).get(rel)
        if expected:
            actual = _sha256(f)
            if actual != expected:
                errors.append(f"checksum mismatch: {f.name}")

    db_file = target / "jarvis.db"
    if db_file.is_file():
        try:
            conn = sqlite3.connect(str(db_file))
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                if integrity[0] != "ok":
                    errors.append(f"sqlite integrity: {integrity[0]}")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            errors.append(f"sqlite unreadable: {exc.__class__.__name__}")
    else:
        warnings.append("no jarvis.db in backup (Postgres/pg_dump or none present)")

    vector_dir = target / "vector_store"
    if not vector_dir.is_dir():
        warnings.append("no vector_store directory in backup")

    ok = not errors
    message = "backup OK" if ok else "; ".join(errors)
    return BackupResult(
        path=str(target),
        ok=ok,
        message=message,
        files=recorded,
        warnings=warnings,
        checksums=dict(manifest.get("checksums") or {}),
    )


def list_backups(backup_root: str | None = None) -> list[dict]:
    """List backups under the backup root (newest first). Never mutates."""
    root = Path(backup_root).resolve() if backup_root else backup_dir_path()
    if not root.is_dir():
        return []
    entries: list[dict] = []
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith(BACKUP_PREFIX):
            continue
        created = None
        warnings = []
        manifest_file = child / MANIFEST_NAME
        if manifest_file.is_file():
            try:
                m = json.loads(manifest_file.read_text(encoding="utf-8"))
                created = m.get("created_at")
                warnings = m.get("warnings") or []
            except (OSError, json.JSONDecodeError):
                pass
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "created_at": created,
                "file_count": sum(1 for _ in child.rglob("*") if _.is_file()),
                "size_bytes": sum(_.stat().st_size for _ in child.rglob("*") if _.is_file()),
                "warnings": warnings,
            }
        )
    entries.sort(key=lambda e: e["name"], reverse=True)
    return entries


def delete_backup(backup_id: str, backup_root: str | None = None) -> BackupResult:
    """Explicitly delete a single backup by id.

    Only directories inside the backup root matching the backup naming pattern
    may be deleted — anything else (or a path traversal attempt) is refused.
    """
    root = Path(backup_root).resolve() if backup_root else backup_dir_path()
    candidate = (root / backup_id).resolve()
    if root not in candidate.parents or candidate == root:
        return BackupResult(path=str(candidate), ok=False, message="refusing to delete outside backup root")
    if not candidate.name.startswith(BACKUP_PREFIX):
        return BackupResult(path=str(candidate), ok=False, message="not a jarvis backup")
    if not candidate.is_dir():
        return BackupResult(path=str(candidate), ok=False, message="backup not found")
    try:
        shutil.rmtree(candidate)
        return BackupResult(path=str(candidate), ok=True, message="backup deleted")
    except OSError as exc:
        return BackupResult(path=str(candidate), ok=False, message=f"delete failed: {exc.__class__.__name__}")


__all__ = [
    "BackupResult",
    "backup_dir_path",
    "create_backup",
    "delete_backup",
    "list_backups",
    "verify_backup",
]