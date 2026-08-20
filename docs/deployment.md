# Deployment

Jarvis supports three deployment profiles, selected with `DEPLOYMENT_PROFILE`
(also settable as `JARVIS_DEPLOYMENT_PROFILE`). The profile drives safe
defaults and startup validation — it does **not** change your models.

| Profile        | Intended use                              | Defaults |
|----------------|-------------------------------------------|----------|
| `local`        | Loopback-only development on one machine  | `JARVIS_HOST=127.0.0.1`, no CORS, restricted trusted hosts |
| `single_host`  | One private machine on a LAN / intranet   | requires explicit CORS origins |
| `production`   | Hardened public-facing deployment         | requires HTTPS, session tokens, backups, no trace exposure |

## Profile validation

At startup (and via `jarvis-admin status`) `validate_deployment()` reports
warnings for the active profile:

* `production`: binding to `0.0.0.0`, loopback host, no allowed origins,
  wildcard CORS (`*`), no trusted hosts, `REQUIRE_SESSION_TOKEN=false`,
  `JARVIS_DEBUG=true`, `JARVIS_EXPOSE_TRACES=true`, backups disabled, or
  cloud configured without an enforced budget.
* `single_host`: same checks, minus the strict HTTPS/token requirements.

Warnings are advisory and never block startup, but a `production` deployment
is only considered ready when the validation output is clean — `GET /ready`
reports `not_ready` (HTTP 503) while any required check fails.

## Network surface (Phase 7)

* **Bind**: `JARVIS_HOST` / `JARVIS_PORT` control where uvicorn binds
  (`uv run uvicorn jarvis.api.main:app --app-dir src --host $JARVIS_HOST --port $JARVIS_PORT`).
  `local` defaults to `127.0.0.1`.
* **CORS**: `JARVIS_ALLOWED_ORIGINS` is a comma-separated list of explicit
  origins. Empty = same-origin only (CORS middleware is **not** installed).
  Wildcard origins are rejected for `single_host`/`production`.
* **Trusted hosts**: `JARVIS_TRUSTED_HOSTS` is enforced by a trusted-host
  middleware that rejects requests with an unlisted `Host` header (HTTP 400).
* **Security headers**: every response gets `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` and
  `Permissions-Policy`. HSTS is only advertised when
  `JARVIS_FORCE_HTTPS=true`.
* **Reverse proxy**: set `JARVIS_BEHIND_REVERSE_PROXY=true` only when the
  backend sits behind a trusted reverse proxy; `X-Forwarded-For` is then
  honoured so rate limiting keys on the real client IP.

## Readiness

`GET /ready` reports dependency health for the selected profile and returns:

* `200 {"status":"ready"}` — all required checks pass;
* `200 {"status":"degraded"}` — required checks pass, informational warn;
* `503 {"status":"not_ready"}` — a required dependency failed.

Checks: `database` (required), `deployment` (required), `ollama` (required for
`local`/`single_host`), `cloud` (informational). It never exposes secrets.

## Backend

```bash
uv sync
uv run uvicorn jarvis.api.main:app --app-dir src --host $JARVIS_HOST --port $JARVIS_PORT
```

## Frontend

```bash
uv run streamlit run streamlit_app.py
```

The Streamlit app talks to the backend server-side; with a browser running on
the same machine you can keep `JARVIS_ALLOWED_ORIGINS` empty. Only set origins
when the browser and backend are on different hosts/ports.

## Docker

The compose stack (`docker-compose.yml`) exposes the backend on `0.0.0.0` by
design (the container must accept connections from the host). For a hardened
deployment set `DEPLOYMENT_PROFILE=production` in `.env`, configure
`JARVIS_ALLOWED_ORIGINS`, `JARVIS_TRUSTED_HOSTS`, `JARVIS_FORCE_HTTPS` and
`JARVIS_EXPOSE_TRACES=false`, and place the stack behind a TLS-terminating
reverse proxy. See `.env.docker.example`.

## Checklist before going public

1. `DEPLOYMENT_PROFILE=production`
2. `JARVIS_ALLOWED_ORIGINS` set to your exact UI origin (no `*`)
3. `JARVIS_TRUSTED_HOSTS` lists your real hostname(s)
4. `REQUIRE_SESSION_TOKEN=true`
5. `JARVIS_FORCE_HTTPS=true` behind TLS
6. `JARVIS_EXPOSE_TRACES=false`
7. `JARVIS_BACKUP_ENABLED=true` (+ schedule `jarvis-backup create`)
8. `jarvis-admin status` shows no warnings
9. `GET /ready` returns `ready`