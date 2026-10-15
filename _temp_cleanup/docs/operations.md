# Operations

Everyday operational commands for Jarvis.

## Status

```bash
uv run jarvis-admin status
```

Read-only. Shows deployment profile, runtime mode, schema version vs. code
target, deployment warnings, backup status, and running compose containers.
Never prints secrets.

```bash
uv run jarvis-admin check     # deployment validation summary (non-zero on warnings)
```

## Database

Schema changes are additive and versioned (a `schema_version` table records
what has been applied). Normal startup runs migrations automatically; run them
manually when you need to control when an upgrade happens:

```bash
uv run jarvis-db status       # current vs. target schema version
uv run jarvis-db migrate      # apply pending migrations (idempotent)
uv run jarvis-db check        # validate schema consistency
```

Nothing here drops, truncates or rewrites user data.

## Backups

See docs/backup-and-restore.md.

```bash
uv run jarvis-backup create
uv run jarvis-backup verify
uv run jarvis-backup list
```

## Runtime validation

```bash
uv run jarvis-validate-runtime [--mode local|docker|auto]
```

Best-effort checks that Ollama is reachable, the configured model exists and
can answer, GPU diagnostics are available, and runtime settings are valid.
Exits non-zero only when Ollama is unreachable or the model is missing.

## Maintenance

* Inactive sessions (`SESSION_TTL_DAYS`) and expired approvals
  (`EXPIRED_APPROVAL_RETENTION_HOURS`) are cleaned by the periodic sweep
  (`MAINTENANCE_SWEEP_INTERVAL`).
* Trace retention is bounded by `TRACE_RETENTION_LIMIT`.

## Upgrades

1. `git pull` (or update the container image).
2. Run `uv run jarvis-db migrate` (or just start the backend — it migrates
   automatically).
3. Verify with `uv run jarvis-admin status` and `GET /ready`.
4. If a backup of the previous version exists, verify it:
   `uv run jarvis-verify-backup`.

## Health endpoints

* `GET /health` — liveness (Ollama reachability is reported, not required).
* `GET /ready` — readiness for the selected deployment profile (HTTP 503 when
  not ready).

## Logging

`JSON_LOGS_ENABLED=true` emits parseable JSON lines. Logs and diagnostics
never include secrets (API keys, DSNs, tokens or passwords).