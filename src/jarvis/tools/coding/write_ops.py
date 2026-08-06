"""File-write tools: write_file and edit_file.

Both are workspace-scoped (via :mod:`jarvis.tools.coding.paths`) and
logged. Risk classification lives in :mod:`jarvis.guardrails.risk`;
``write_file``/``edit_file`` are pre-classified as medium risk so they
briefly via the graph's check_risk node.
"""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import tool

from jarvis.tools.coding.paths import WorkspaceError, resolve_in_workspace

logger = logging.getLogger("jarvis.tools.write_ops")


def _log_write(op: str, path: Path, *, nbytes: int) -> None:
    logger.info("coding tool %s -> %s (%d bytes)", op, path, nbytes)


@tool
def write_file(file_path: str, content: str) -> str:
    """Create or overwrite a file inside the workspace with *content*.

    The path must resolve inside the configured workspace root; absolute
    paths and ``..`` traversal are refused. Parent directories are
    created as needed. Returns a short confirmation, or an error string
    beginning with "Error:" on failure.
    """
    try:
        resolved = resolve_in_workspace(file_path)
    except WorkspaceError as exc:
        return f"Error: {exc}"

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        _log_write("write_file", resolved, nbytes=len(content))
        return f"Wrote {resolved} ({len(content)} bytes)."
    except OSError as exc:
        return f"Error writing {file_path}: {exc}"


@tool
def edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """Replace the first occurrence of *old_string* with *new_string* in a file.

    The file must exist and be inside the workspace. If *old_string* is not
    found, or appears more than once, the edit is refused (the caller must
    provide a unique anchor or use ``write_file`` instead). Returns a short
    confirmation, or an error string beginning with "Error:" on failure.
    """
    if old_string == new_string:
        return "Error: old_string and new_string are identical."

    try:
        resolved = resolve_in_workspace(file_path, must_exist=True)
    except WorkspaceError as exc:
        return f"Error: {exc}"

    try:
        original = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Error reading {file_path}: {exc}"

    occurrences = original.count(old_string)
    if occurrences == 0:
        return f"Error: old_string not found in {file_path}."
    if occurrences > 1:
        return (
            f"Error: old_string appears {occurrences} times in {file_path}; "
            "provide a more specific anchor."
        )

    new_content = original.replace(old_string, new_string, 1)
    resolved.write_text(new_content, encoding="utf-8")
    _log_write("edit_file", resolved, nbytes=len(new_content))
    return f"Edited {resolved}: 1 replacement."
