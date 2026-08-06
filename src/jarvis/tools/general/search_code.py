"""Grep-like code search tool."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from langchain_core.tools import tool

from jarvis.tools.coding.paths import WorkspaceError, resolve_in_workspace

logger = logging.getLogger("jarvis.tools.search_code")

_MAX_MATCHES = 50
_MAX_FILE_BYTES = 1_500_000


def _iter_text_files(root: Path, *, extensions: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower().lstrip(".") in extensions:
            out.append(path)
    return out


@tool
def search_code(pattern: str, path: str = ".") -> str:
    """Search files under the workspace for *pattern* (a regex).

    Returns up to 50 hits as ``file:line: snippet`` lines. Defaults to
    a case-sensitive Python regex search; pass ``(?i:...)`` for
    case-insensitive matching. Binary and very large files are skipped.
    """
    if not pattern:
        return "Error: pattern is empty."

    try:
        root = resolve_in_workspace(path, must_exist=True)
        workspace_prefix = resolve_in_workspace(".")
        if root.is_file():
            files: list[Path] = [root]
        else:
            files = _iter_text_files(
                root,
                extensions=("py", "js", "ts", "tsx", "jsx", "md", "txt", "json", "yaml", "yml"),
            )
    except WorkspaceError as exc:
        return f"Error: {exc}"
    except re.error as exc:
        return f"Error: invalid regex: {exc}"

    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"

    hits: list[str] = []
    for f in files:
        try:
            if f.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                try:
                    rel = f.relative_to(workspace_prefix).as_posix()
                except ValueError:
                    rel = f.name
                snippet = line.strip()
                if len(snippet) > 200:
                    snippet = snippet[:197] + "..."
                hits.append(f"{rel}:{lineno}: {snippet}")
                if len(hits) >= _MAX_MATCHES:
                    hits.append("... (truncated)")
                    return "\n".join(hits)
    if not hits:
        return f"No matches for /{pattern}/ under {path}."
    return "\n".join(hits)
