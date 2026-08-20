# Backup & Restore

Phase 7 adds backup tooling. Backups are timestamped folders under
`BACKUP_DIR` (default `./backups`) and are **never deleted automatically**.

## What a backup contains

* `manifest.json` — metadata + sha256 checksums (no secrets, ever)
* `jarvis.db` — a consistent SQLite snapshot (taken via the sqlite backup API);
  when `POSTGRES_DSN` is set, `pg_dump` is used instead
* `vector_store/` — the embedded ChromaDB directory tree
* `docs/` — source document files, **only** when `--include-documents` is set
  (vector metadata is always backed up)

## Commands

```bash
jarvis-backup create [--dir ROOT] [--include-documents]
jarvis-backup list [--dir ROOT]
jarvis-backup verify [--dir ROOT] [BACKUP_ID]
jarvis-backup delete [--dir ROOT] BACKUP_ID
jarvis-verify-backup [PATH]          # verify the newest backup
```

Examples:

```bash
uv run jarvis-backup create --include-documents
uv run jarvis-backup list
uv run jarvis-backup verify
uv run jarvis-backup delete backup_20260820_120000
```

## Restore

Restoring is a manual, out-of-band operation (the tooling never overwrites
live data):

1. Stop the backend.
2. Replace `SQLITE_PATH` (or the vector store at `VECTOR_DB_PATH`) with the
   contents of the backup's `jarvis.db` / `vector_store/`.
3. Restore `docs/` too if you backed it up.
4. Start the backend — schema migrations run automatically on startup, so an
   older backup is brought up to date by `jarvis-db migrate` (see
   docs/operations.md).

## Verification

`jarvis-backup verify` checks the manifest, file presence, sha256 checksums,
and runs `PRAGMA integrity_check` on the SQLite snapshot. Schedule it after
every backup (e.g. a cron job) so a corrupt backup is caught early.

## Retention

`BACKUP_RETENTION_DAYS` is advisory guidance surfaced by
`jarvis-admin status`; the tooling never prunes old backups. Prune manually
with `jarvis-backup delete`, which refuses to touch anything outside
`BACKUP_DIR` or any folder that is not a jarvis backup.

## Safety

* No automatic deletion of any kind.
* Manifests contain metadata + checksums only — DSNs, API keys, tokens and
  passwords are never written to a backup or listed anywhere.
* `--include-documents` must be passed explicitly (or
  `BACKUP_INCLUDE_DOCUMENTS=true`) to copy source document files.