"""Coding-oriented tools for file system operations."""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from jarvis.config.settings import settings
from jarvis.tools.coding.paths import (
    WorkspaceError,
    is_sensitive_filename,
    resolve_in_workspace,
)

logger = logging.getLogger("jarvis.tools.read_file")


@tool
def read_file(file_path: str) -> str:
    """Read a text file inside the configured workspace.

    The path must resolve inside ``WORKSPACE_DIR``; absolute paths outside
    it, ``..`` traversal, and symlinks escaping the workspace are refused.
    Sensitive files (``.env``, private keys, credentials/secrets) are never
    read. Files larger than ``MAX_READ_FILE_BYTES`` are refused, and the
    returned content is capped at ``MAX_READ_FILE_CHARS`` characters.
    Returns an ``Error:`` string on any failure; file contents are never
    logged.
    """
    try:
        resolved = resolve_in_workspace(file_path, must_exist=True)
    except WorkspaceError as exc:
        return f"Error: {exc}"

    if is_sensitive_filename(str(resolved)):
        return f"Error: reading sensitive file {resolved.name} is not allowed."

    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return f"Error: cannot stat {file_path}: {exc}"

    if size > settings.max_read_file_bytes:
        return (
            f"Error: file is {size} bytes, exceeding the "
            f"{settings.max_read_file_bytes}-byte limit."
        )

    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: {resolved.name} is not readable as UTF-8 text."
    except OSError as exc:
        return f"Error reading {file_path}: {exc}"

    if len(text) > settings.max_read_file_chars:
        text = (
            text[: settings.max_read_file_chars]
            + f"\n... (truncated — output limited to {settings.max_read_file_chars} characters)"
        )
    return text