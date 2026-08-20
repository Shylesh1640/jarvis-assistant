# Jarvis Assistant — troubleshooting

Diagnostic order: check `/health`, then `/runtime`, then the specific error
shape from the API, then the backend logs. Every error returns a stable
`error` code plus a `suggested_action`; start from those.

## Model / Ollama problems

### `503 ollama_unavailable` — "Ollama is not reachable"

Ollama is down or not listening on `OLLAMA_BASE_URL`.

```powershell
ollama ps
# restart if needed
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
```

The server retries transient failures up to `RETRY_MAX_ATTEMPTS` (3 by
default) with linear backoff before returning 503, so a service that is
coming back up often recovers on its own.

### `502 model_not_found` — "model could not be loaded"

The configured model isn't in `ollama list`.

```powershell
ollama list
ollama pull qwen3:8b   # or whatever GENERAL_MODEL is set to
```

Check `GENERAL_MODEL` / `CODING_MODEL` / `EMBEDDING_MODEL` in `.env`
match `ollama list`.

### `507 out_of_memory` — "does not fit in available VRAM/RAM"

The model + context exceeds available memory. The app first retries once
with `num_gpu=0` (CPU) if `GPU_FALLBACK_TO_CPU=true` — the response then
carries a `warning`. If it still fails:

- lower `OLLAMA_CONTEXT_LENGTH` (frees KV cache),
- reduce `HISTORY_MAX_TURNS` / `CONTEXT_TOKEN_BUDGET`,
- free memory: `ollama ps` to confirm only one model is loaded
  (`OLLAMA_MAX_LOADED_MODELS=1` keeps it that way).

### `507 gpu_required` — "GPU policy refuses CPU execution"

With `GPU_POLICY=require_gpu` the request intentionally refuses to run on
CPU when the model cannot run fully on GPU. `suggested_action` tells you the
fix — typically: use a smaller model that fits VRAM, enable
`GPU_STRONG_MODEL_ALLOW_PARTIAL_OFFLOAD=true`, or switch to
`GPU_POLICY=prefer_gpu` / `allow_cpu` (which permit CPU execution). This is
the policy working as designed — the app never silently downgrades to CPU.

### `504 request_timeout` — "took too long to respond"

Long prompts / slow hardware. Retry works for transient stalls; for stable
slowness:

- run the question as a **background task** (`POST /tasks`) — the UI's
  "Background" toggle,
- shorten the prompt / selected text (`SELECTED_TEXT_TOKEN_CAP`),
- lower context length.

### Partial CPU/GPU or 100% CPU in `/runtime`

The model is larger than dedicated VRAM, or `OLLAMA_NUM_GPU` isn't `-1`.
Lower context, or use a smaller/quantized model. See the README's GPU section.

## Approval problems

### `410 approval_expired`

An approval card sat open past `APPROVAL_TTL_SECONDS` (default 600 s). The
stale approval can never fire — this is by design. Re-ask the question to
restart the action.

### `400 no_pending_approval` — approved but nothing pending

The client sent `approved: true` but no pending approval exists for the
session. Causes:
- the pause already resolved (e.g. a fresh message superseded it),
- the backend restarted **and** the durable row was purged,
- wrong `session_id`.

### Approval card disappeared / is stale

The UI clears the card when a new message is sent. If the backend restarted
between the pause and your click, the durable approval was reloaded from the
DB — Approve still works unless the TTL elapsed (410).

## Sessions & tokens

### `401 unauthorized` with `REQUIRE_SESSION_TOKEN=true`

`POST /chat`/`POST /tasks` need a valid per-session token. Get one via:

```
GET /sessions/{session_id}/token
```

send it as `session_token`. Tokens are per-session — a token from session A
cannot be used against session B. The Streamlit UI fetches and sends it
automatically (show **Trace/debug** for the sent token).

Tokens are stored **hashed** at rest, so after a backend restart the
previously issued token is gone and a **new** token is generated — if an old
token stops working, re-fetch it via `GET /sessions/{id}/token`. Tokens also
expire after `SESSION_TOKEN_TTL_HOURS` (default 168) and rotate at
`SESSION_TOKEN_ROTATION_HOURS` (default 72).

### `404 session_not_found`

From `GET /sessions/{id}` only (the token endpoint creates the session on
demand). The session row was never created, or the DB was wiped. Sessions
inactive past `SESSION_TTL_DAYS` (default 7) are also deleted by the
periodic maintenance sweep — a session you haven't touched in a week is
expected to disappear.

## Rate limiting

### `429 rate_limited`

Over `RATE_LIMIT_PER_MINUTE` requests (default 300) within a 60 s window for
the same session/IP. Back off; the response carries `Retry-After`. Interactive
use never hits it — it protects against runaway loops. Set `0` to disable.

## Errors that don't fit an error code

### `500 internal_error`

Unexpected server bug. The body hides details by design; read the backend
log. Grep for the `trace_id` from the response:

```powershell
uv run streamlit run streamlit_app.py   # other window:
uv run uvicorn jarvis.api.main:app --reload --app-dir src --log-level debug
```

## The UI reports Backend offline

`GET /health` failed. Check `BASE_URL` in `streamlit_app.py` (default
`http://localhost:8000`) matches the running backend, and that the backend
is actually up:

```powershell
curl http://localhost:8000/health
```

## DB / persistence issues

- **SQLite**: `SQLITE_PATH` (default `./data/jarvis.db`). If approvals or
  sessions vanish on restart, confirm the file isn't a temp/cached copy and
  the process can write to that path.
- **Postgres**: set `POSTGRES_DSN` (see `docker-compose.yml`):
  `postgresql+psycopg://jarvis:jarvis@localhost:5432/jarvis`.
- Expanding `.env` settings requires a backend restart.

## Background tasks appear stuck in `running`

Tasks use a bounded in-process `ThreadPoolExecutor`. If the queue is full or
a task hangs (e.g. a model call), later tasks wait. `Cancel` works
best-effort; a hung model call resolves via
`RETRY_MAX_ATTEMPTS`/`request_timeout`. Restart the backend to re-queue
in-flight tasks (their rows remain in the DB).

## Export / copy drop secrets

`Copy answer` and *Export (`.md`)* run on the already-redacted reply.
Guardrails redact high-entropy secret-like tokens (API keys, JWTs, bearer
tokens) from assistant output, so redaction is expected, not data loss.

## Still stuck?

- Get the exact `error` code + message from the API — it's stable.
- Run `uv run pytest` to confirm your install isn't broken.
- Open an issue with the backend log snippet (trace id, error code, stack).