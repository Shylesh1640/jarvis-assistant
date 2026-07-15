"""Coding-oriented tools for file system operations."""
from pathlib import Path

from langchain_core.tools import tool


@tool
def read_file(file_path: str) -> str:
    """Read the full contents of a text file from disk.

    Returns the file contents on success, or an error message string if
    the file does not exist, is a directory, or cannot be read as text.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return f"Error: file not found — {file_path}"
    if not path.is_file():
        return f"Error: not a file — {file_path}"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading {file_path}: {exc}"
