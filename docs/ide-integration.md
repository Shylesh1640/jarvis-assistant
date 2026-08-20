# IDE Integration

Workspace-scoped operations for an IDE (or the assistant) — running
commands, opening files, searching files, and running tests.

## Workspace confinement

Everything runs inside `IDE_WORKSPACE_ROOT`:

* `execute_command` runs with the workspace as the working directory;
* `open_file` / `search_files` resolve paths relative to the workspace and
  **reject any path that escapes it** (`403 outside_workspace`);
* `run_tests` runs pytest in the workspace root;
* all subprocess calls have a 60s timeout and capture output (capped at
  20 KB of the tail).

## Settings

```
IDE_INTEGRATION_ENABLED=true
IDE_WORKSPACE_ROOT=C:/path/to/your/project
```

Until both are set, `/ide` routes return a structured
`503 ide_not_configured` response and never touch the filesystem.

## API

Every endpoint requires `?confirm=1` — nothing runs without an explicit
human action.

| Method | Path | Body |
|---|---|---|
| POST | `/ide/execute-command?confirm=1` | `{"command": "git status"}` |
| POST | `/ide/open-file?confirm=1` | `{"path": "src/main.py"}` |
| POST | `/ide/search-files?confirm=1` | `{"pattern": "**/*.py", "path": "src"}` |
| POST | `/ide/run-tests?confirm=1` | `{}` |

## Safety

* Off by default.
* Every action is approval-gated (`confirm=1`).
* Path traversal outside the workspace is rejected before touching disk.
* Command output is truncated; nothing sensitive is logged.