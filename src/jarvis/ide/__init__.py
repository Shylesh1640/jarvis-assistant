"""IDE integration (Phase 8)."""
from jarvis.ide.executor import (
    IDEUnconfiguredError,
    OutsideWorkspaceError,
    execute_command,
    open_file,
    require_workspace,
    resolve_in_workspace,
    run_tests,
    search_files,
    workspace_root,
)

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