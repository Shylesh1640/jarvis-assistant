"""IDE integration executor (Phase 8).

Small, workspace-scoped operations an IDE (or the assistant) can ask for:

* ``execute_command`` — run a shell command with the workspace as cwd
* ``open_file``       — read a file *inside* the workspace (content capped)
* ``search_files``    — glob for files inside the workspace
* ``run_tests``       — run pytest in the workspace root

Every operation is bounded by ``IDE_WORKSPACE_ROOT``: any path that escapes
the root is rejected before touching the filesystem, and commands/tests run
with the workspace as their working directory. All routes require
``?confirm=1`` so nothing here ever runs without an explicit human action.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis.config.settings import settings

_MAX_READ_CHARS = 20000
_CMD_TIMEOUT_SECONDS = 60


class IDEUnconfiguredError(RuntimeError):
    """Raised when IDE integration is disabled or the workspace root is unset."""


class OutsideWorkspaceError(ValueError):
    """Raised when a requested path escapes the workspace root."""


def workspace_root() -> Path | None:
    if not settings.ide_integration_enabled or not settings.ide_workspace_root:
        return None
    return Path(settings.ide_workspace_root)


def require_workspace() -> Path:
    """The enabled, existing workspace root, else raise."""
    root = workspace_root()
    if root is None:
        raise IDEUnconfiguredError("IDE integration is not configured.")
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        raise IDEUnconfiguredError(f"IDE workspace root does not exist: {root}")
    return root


def resolve_in_workspace(target: str) -> Path:
    """Resolve *target* relative to the workspace; reject escapes."""
    root = require_workspace()
    candidate = (root / target).resolve()
    if not candidate.is_relative_to(root):
        raise OutsideWorkspaceError(f"Path escapes the workspace: {target}")
    return candidate


def execute_command(command: str) -> dict:
    """Run a command with the workspace as cwd. Returns status + output tail."""
    root = require_workspace()
    if not command.strip():
        return {"ok": False, "error": "Empty command."}
    try:
        result = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {_CMD_TIMEOUT_SECONDS}s."}
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "output": output[-_MAX_READ_CHARS:],
    }


def open_file(path: str) -> dict:
    """Read a workspace file (content capped to ``_MAX_READ_CHARS``)."""
    target = resolve_in_workspace(path)
    if not target.is_file():
        return {"ok": False, "error": f"Not a file in the workspace: {path}"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": f"Could not read file: {exc}"}
    return {
        "ok": True,
        "path": str(target),
        "relative_path": str(target.relative_to(require_workspace())),
        "size_bytes": len(content.encode("utf-8", "replace")),
        "content": content[: _MAX_READ_CHARS],
        "truncated": len(content) > _MAX_READ_CHARS,
    }


def search_files(pattern: str, path: str | None = None) -> dict:
    """Glob for files under the workspace (optionally under a sub-path)."""
    root = require_workspace()
    base = resolve_in_workspace(path) if path else root
    if not base.is_dir():
        return {"ok": False, "error": f"Not a directory in the workspace: {path or '.'}"}
    try:
        matches = sorted(str(p.relative_to(root)) for p in base.glob(pattern) if p.is_file())
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"Search failed: {exc}"}
    return {"ok": True, "count": len(matches), "files": matches[:2000]}


def run_tests() -> dict:
    """Run pytest in the workspace root; returns status + output tail."""
    root = require_workspace()
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "-q", "--no-cov"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Test run timed out after {_CMD_TIMEOUT_SECONDS}s."}
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "output": output[-_MAX_READ_CHARS:],
    }


__all__ = [
    "IDEUnconfiguredError",
    "OutsideWorkspaceError",
    "execute_command",
    "open_file",
    "require_workspace",
    "resolve_in_workspace",
    "run_tests",
    "search_files",
    "workspace_root",
]