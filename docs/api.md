# Jarvis Assistant — API reference

Base URL: `http://localhost:8000`. The API is a FastAPI app; interactive
docs are available at `/docs` (Swagger UI) and `/openapi.json`.

Every request/response below uses `application/json`. Unless noted, all
endpoints are unauthenticated; see [Authentication](#authentication) for
session-token mode.

## Health & capability

### `GET /health`

Liveness probe.

```json
{ "status": "ok" }
```

### `GET /models`

Configured local + cloud models.

```json
{
  "local": { "general": "qwen3:8b", "coding": "qwen2.5-coder:7b-q5_K_M" },
  "cloud": { "chain": ["claude-opus-4.1", "gpt-5.5", "gemini-2.5-pro"] }
}
```

### `GET /runtime`

GPU / Ollama runtime diagnostics. Returns processor split, VRAM usage,
and whether validation found the runtime healthy. See the README's
"GPU / Ollama runtime optimization" for the field meanings.

## Chat

### `POST /chat`

Body:

```json
{
  "session_id": "abc123",
  "session_token": "optional-bearer",
  "message": "Summarise the workspace",
  "history": [],
  "selected_text": "optional snippet",
  "approved": false,
  "show_reasoning": false,
  "answer_style": "concise"
}
```

Response `200`:

```json
{
  "session_id": "abc123",
  "response": "…",
  "path_used": "general|coding|complex",
  "model_used": "qwen3:8b",
  "approval_required": false,
  "pending_action": "execute_tools",
  "pending_tool_calls": [{"name": "write_file", "args": {"file_path": "…"}}],
  "approval_id": "ap-…",
  "approval_expires_at": "2026-08-14T12:00:00Z",
  "tools_used": ["calculator"],
  "sources": [{"source": "docs/x.md", "chunk_id": "…"}],
  "retrieved_context": "…",
  "trace_id": "tr-…",
  "warning": null
}
```

#### Approval flow

When `approval_required` is `true`, the exact tool calls are captured in
`pending_tool_calls` and the pause is **durable** (written to the DB with a
TTL). The client shows an Approve / Deny card.

- **Approve**: re-POST the same payload to `/chat` with `approved: true`
  (the `message`/`session_id` must be consistent). Only the captured calls
  execute — never an arbitrary action. A fresh `approved: false` message also
  cancels any lingering pending approval for the session.
- **Deny**: client-side only — the UI clears its pending card and inserts a
  "cancelled" message; no backend call is made. (For background tasks use
  `POST /tasks/{id}/deny` instead.)
- **Expiry**: if the TTL has passed the server answers
  `410 approval_expired`; ask the question again to restart.

## Background tasks

### `POST /tasks`

Submit a long-running job (runs in a bounded in-process thread pool on the
backend machine — see the README on the machine boundary).

```json
{ "description": "Review the design", "session_id": "abc123", "session_token": "optional" }
```

Returns a `TaskStatusResponse` (same shape as `GET /tasks/{id}` below),
usually with `status: "queued"` or `"running"`.

### `GET /tasks/{task_id}`

```json
{
  "id": "t-…",
  "status": "running",
  "description": "Review the design",
  "stage": "coding",
  "result": null,
  "error": null,
  "approval_id": null,
  "pending_action": null,
  "pending_tool_calls": [],
  "session_id": "abc123",
  "created_at": "…",
  "started_at": "…",
  "finished_at": null
}
```

`status` is one of `queued | running | waiting_for_approval | completed |
failed | cancelled`. Tasks awaiting approval stop in
`waiting_for_approval` until resolved.

### `POST /tasks/{task_id}/approve`

Resolve a task that paused for approval. Body optional (reserved for future
"always allow" semantics). Emits `409 task_not_awaiting_approval` if the
task is not waiting.

### `POST /tasks/{task_id}/deny`

Deny the pending tool call(s); the task finishes without executing them.

### `POST /tasks/{task_id}/cancel`

Best-effort cancellation of a queued/running task.

Errors: `404 task_not_found`.

## Sessions & tokens

### `GET /sessions/{session_id}/token`

Look up (creating if needed) the session and return its bearer token:

```json
{ "session_id": "abc123", "session_token": "tok-…" }
```

The token is per-session and stable across restarts (persisted in the DB).

### `GET /sessions/{session_id}`

Session metadata (`created_at`, `last_active_at`, `has_token`). Errors:
`404 session_not_found`.

### `GET /sessions?limit=50`

List recent sessions.

## Documents

### `GET /documents/count`

`{ "count": 42 }`.

### `POST /documents/upload`

`multipart/form-data`, field `files` (repeatable). Accepted: `.txt`, `.md`,
`.pdf`, `.docx`. Binary beyond 20 MB rejected. Same content (deterministic
chunk IDs) is a no-op.

### `POST /documents/ingest-folder`

Body `{ "folder": "./data/docs" }` (optional — defaults to `DOCS_FOLDER`).
Wraps the CLI ingest over HTTP. Errors:
`400 invalid_input`/`folder not found/readable`.

## Observability

### `GET /traces/recent?limit=50`

Most recent entries from the in-memory trace registry:

```json
{ "traces": [ { "trace_id": "tr-…", "session_id": "abc123", "events": […], "durations_ms": {…} } ] }
```

The registry is bounded and in-memory — restarting the backend clears it.

## Errors

Every error is a stable JSON shape. `message` and `suggested_action` are
human-readable; `error` is a stable machine-readable code;

`retry_after_seconds` appears on transient failures and is also emitted as
the HTTP `Retry-After` header.

```json
{
  "error": "model_unavailable",
  "message": "…",
  "retry_after_seconds": 10,
  "suggested_action": "…"
}
```

### Error codes

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_input` | Guardrails rejected the prompt |
| 400 | `no_pending_approval` | `approved: true` but nothing pending |
| 400 | `session_not_found` | Session API missing row |
| 401 | `unauthorized` | Missing/invalid session token |
| 404 | `task_not_found` | Unknown task id |
| 409 | `task_not_awaiting_approval` | approve/deny on a non-waiting task |
| 410 | `approval_expired` | Approval TTL elapsed; re-ask to restart |
| 429 | `rate_limited` | Too many requests; retry after `Retry-After` |
| 500 | `internal_error` | Unexpected server error |
| 502 | `model_not_found` | Model missing on Ollama (`model_request_failed` for untyped model failures) |
| 503 | `ollama_unavailable` | Ollama unreachable |
| 504 | `request_timeout` | Local model timed out |
| 507 | `out_of_memory` | Model + context > VRAM/RAM |

## Authentication

With `REQUIRE_SESSION_TOKEN=false` (default) `session_token` is optional —
used only to label the session. With it `true`, `POST /chat` and `POST /tasks`
require a valid token for the given `session_id`; cross-session replay yields
`401 unauthorized`. Tokens come from `GET /sessions/{session_id}/token` and
are per-session only.

## Rate limiting

`POST /chat` and `/tasks` writes are rate-limited per session/IP using a
sliding 60 s window (`RATE_LIMIT_PER_MINUTE`, default 300; `0` disables).
Excess requests get `429 rate_limited` with a `Retry-After` header.

## Trace ids

Chat/task responses and errors carry `trace_id`. The same id is propagated
to LangGraph runs, background tasks, and the trace registry —
`GET /traces/recent` surfaces recent ids for debugging.