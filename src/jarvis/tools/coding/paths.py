"""Workspace path guard for the coding toolset.

``resolve_in_workspace(path)`` resolves *path* relative to
``settings.workspace_dir`` and refuses anything that would escape that
root (via absolute paths or ``..`` traversal). Every write/exec tool
runs its input through this before touching the filesystem; the guard
also forbids the workspace root itself from being a target for write/
delete operations.

``is_sensitive_filename(name)`` flags secret-bearing names (``.env``,
private keys, credentials/secrets files) so read/write tools can refuse
them before touching disk.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.config.settings import settings


class WorkspaceError(ValueError):
    """Raised when a path escapes the configured workspace root."""


# Filename patterns that are treated as sensitive and refused by read/write
# tools regardless of location: ``.env`` / ``.env.*``, private-key material,
# and credentials/secrets stores.
_SENSITIVE_NAMES = re.compile(
    r"(^|[/\\])(\.env(\.|$)|id_rsa(\.|$)|id_dsa(\.|$)|id_ecdsa(\.|$)"
    r"|id_ed25519(\.|$)|credentials(\.|$)|secrets?(\.|$))",
    re.IGNORECASE,
)
_SENSITIVE_SUFFIXES = (".pem", ".key", ".ppk", ".p12", ".pfx", ".keystore")


def is_sensitive_filename(name: str) -> bool:
    """Return ``True`` when *name* matches a sensitive/secret file pattern."""
    if not name:
        return False
    if _SENSITIVE_NAMES.search(name):
        return True
    return name.lower().endswith(_SENSITIVE_SUFFIXES)


def workspace_root() -> Path:
    """Return the resolved workspace root, creating it if missing."""
    root = Path(settings.workspace_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_in_workspace(path: str, *, must_exist: bool = False) -> Path:
    """Resolve *path* inside the workspace root.

    The input may be relative (resolved against the workspace root) or
    absolute — but an absolute path must already be *inside* the root.
    Anything that escapes via ``..`` is rejected with ``WorkspaceError``.

    When ``must_exist`` is True, the resolved path must exist on disk.
    """
    root = workspace_root()
    candidate = Path(path)

    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspaceError(
            f"Path {path!r} escapes the workspace root {root}"
        ) from exc

    if must_exist and not resolved.exists():
        raise WorkspaceError(f"Path does not exist: {resolved}")

    return resolved
