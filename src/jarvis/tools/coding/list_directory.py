"""List-directory tool — safe read-only directory listing.

Lists files and subdirectories relative to the workspace root. Low risk,
no approval required. Output is truncated to avoid flooding the model
context.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from jarvis.tools.coding.paths import WorkspaceError, resolve_in_workspace

logger = logging.getLogger("jarvis.tools.list_directory")

_MAX_ENTRIES = 200


@tool
def list_directory(path: str = ".") -> str:
    """List files and subdirectories under *path* inside the workspace.

    Each entry is prefixed with ``d/`` for a directory or ``f `` for a file,
    followed by the name. Returns up to 200 entries (then truncates with an
    ellipsis). Paths that escape the workspace are refused.
    """
    try:
        target = resolve_in_workspace(path, must_exist=True)
    except WorkspaceError as exc:
        return f"Error: {exc}"

    if not target.is_dir():
        return f"Error: {target} is not a directory."

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            prefix = "d/" if child.is_dir() else "f "
            entries.append(f"{prefix}{child.name}")
            if len(entries) >= _MAX_ENTRIES:
                entries.append("... (truncated)")
                break
    except OSError as exc:
        return f"Error listing directory: {exc}"

    if not entries:
        return f"(empty directory: {path})"
    return "\n".join(entries)
