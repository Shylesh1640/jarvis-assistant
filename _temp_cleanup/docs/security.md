# Security

Jarvis ships security controls in layers. This document describes what is
enforced, what is advisory, and what you must configure for a public-facing
deployment. Nothing here claims the system is "secure" by default — see the
checklist at the end.

## Network layer

See docs/deployment.md for wiring. In short:

* **Loopback default** — `local` profile binds `127.0.0.1` only.
* **CORS** — explicit origin allowlist; empty means same-origin only; wildcard
  origins are rejected for `single_host`/`production`.
* **Trusted hosts** — requests with an unlisted `Host` header get HTTP 400.
* **Security headers** — nosniff, frame denial, strict CSP, referrer policy,
  permissions policy on every response; HSTS only when
  `JARVIS_FORCE_HTTPS=true`.
* **Reverse proxy** — proxy headers are honoured only when
  `JARVIS_BEHIND_REVERSE_PROXY=true`.

## Session tokens

Per-session bearer tokens (`REQUIRE_SESSION_TOKEN=true`) block cross-session
access on `/chat` and `/tasks`. Tokens are hashed at rest
(`SESSION_TOKEN_HASH_SCHEME`, argon2 by default), rotated passively, and
expire. See `GET /sessions/{session_id}/token`.

## Rate limiting

`RATE_LIMIT_PER_MINUTE` applies a sliding window per session (or client IP).
429 responses carry `Retry-After`.

## Tool / approval gates

* **Workspace confinement** — write/edit/shell tools refuse to escape
  `WORKSPACE_DIR` (absolute paths and `..` are rejected).
* **Shell allowlist** — `run_shell` only runs commands starting with an
  allowlisted prefix (`SHELL_ALLOWED_COMMANDS`).
* **Approvals** — pending tool actions require explicit approval and expire
  (`APPROVAL_TTL_SECONDS`).
* **Task caps** — `MAX_PLAN_STEPS` and `MAX_TASK_DURATION_SECONDS` bound
  background tasks.

## Cloud cost guardrails

* Every cloud call is priced before it runs (`CLOUD_PRICING_CONFIG_PATH`).
* `CLOUD_REQUIRE_COST_APPROVAL` pauses the complex branch with an
  estimated-cost card instead of spending automatically.
* Per-request, per-session and per-day budgets are enforced when set; hitting
  a budget falls back to local models. Spending is recorded in
  `cloud_usage` and surfaced by `GET /cost`.

## Secret hygiene

* Logs, diagnostics (`/runtime`), readiness (`/ready`), backups, manifests and
  CLI output **never** include API keys, DSNs, session tokens or passwords.
* `.env` and `backups/` are git-ignored.
* Backups store checksums + metadata only.

## Data integrity

* Backups are checksummed and verifiable (`jarvis-verify-backup`).
* Schema changes are additive and versioned; a failing migration never blocks
  startup.
* The tooling never deletes user data or old backups automatically; deletions
  require explicit commands (`jarvis-backup delete`, `DELETE /documents?confirm=1`,
  `DELETE /memory?confirm=1`) and path-traversal attempts are refused.

## Deployment checklist (public exposure)

1. `DEPLOYMENT_PROFILE=production`
2. `REQUIRE_SESSION_TOKEN=true`
3. `JARVIS_ALLOWED_ORIGINS` set to exact origins (no `*`)
4. `JARVIS_TRUSTED_HOSTS` lists your hostnames
5. `JARVIS_FORCE_HTTPS=true` behind TLS
6. `JARVIS_EXPOSE_TRACES=false`
7. `JARVIS_BACKUP_ENABLED=true` with scheduled `jarvis-backup create`
8. `jarvis-admin status` reports no warnings
9. `GET /ready` returns `ready`
10. Run `uv run jarvis-validate-runtime` after deploy