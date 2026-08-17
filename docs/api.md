# Jarvis Assistant — API reference

Base URL: `http://localhost:8000`. The API is a FastAPI app; interactive
docs are available at `/docs` (Swagger UI) and `/openapi.json`.

Every request/response below uses `application/json`. Unless noted, all
endpoints are unauthenticated; see [Authentication](#authentication) for
session-token mode.

## Health & capability

### `GET /health`

Liveness probe plus Ollama reachability.

```json
{ "status": "ok", "ollama_reachable": true }
```

### `GET /models`

Configured local + cloud models.

```json
{
  "general": { "provider": "ollama", "model": "qwen3:8b", "base_url": "http://localhost:11434" },
  "coding": { "provider": "ollama", "model": "qwen3:8b", "model_small": "qwen3:8b", "base_url": "http://localhost:11434" },
  "strong_local": { "provider": "ollama", "model": "qwen3:14b", "base_url": "http://localhost:11434" },
  "complex": { "provider": "openrouter", "models": ["claude-opus-4.1", "gpt-5.5", "gemini-2.5-pro"], "base_url": "https://openrouter.ai/api/v1", "configured": true }
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
  "fallback_used": false,
  "warning": null
}
```

Note: the response does **not** carry a `trace_id` field; trace ids are
recorded in logs and surfaced via `GET /traces/recent`.

#### Approval flow

When `approval_required` is `true`, the exact tool calls are captured in
`pending_tool_calls` and the pause is **durable** (written to the DB with a
TTL). The client shows an Approve / Deny card.

- **Approve**: re-POST the same payload to `/chat` with `approved: true`
  (the `message`/`session_id` must be consistent). Only the captured calls
  execute — never an arbitrary action. A fresh `approved: false` message also
  cancels any lingering pending approval for the session.
- **Deny**: POST to `/chat` with `deny: true` (and the same `session_id`).
  The durable approval row flips to `denied`, so a later `approved: true`
  resume cannot replay the cancelled action; the response text is
  "Action cancelled by user.". A missing pending approval yields
  `400 no_pending_approval`.
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

Session metadata:

```json
{
  "session_id": "abc123",
  "user_id": null,
  "created_at": "…",
  "last_active_at": "…",
  "has_token": true,
  "message_count": 12
}
```

Errors: `404 session_not_found`.

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
  "error": "ollama_unavailable",
  "message": "Ollama is not reachable. Check if the Ollama service is running.",
  "retry_after_seconds": 10,
  "suggested_action": "Start Ollama (`ollama serve`) and retry, or run as a background task."
}
```

### Error codes

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_input` | Guardrails rejected the prompt |
| 400 | `no_pending_approval` | `approved: true` but nothing pending |
| 403 | `invalid_session_token` | Missing/invalid session token |
| 404 | `task_not_found` | Unknown task id |
| 404 | `session_not_found` | Session API missing row |
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
`403 invalid_session_token`. Tokens come from `GET /sessions/{session_id}/token`
and are per-session only.

## Rate limiting

`POST /chat` and `/tasks` writes are rate-limited per session/IP using a
sliding 60 s window (`RATE_LIMIT_PER_MINUTE`, default 300; `0` disables).
Excess requests get `429 rate_limited` with a `Retry-After` header.

## Trace ids

Each request is assigned a `trace_id` recorded in the logs and propagated to
LangGraph runs, background tasks, and the in-memory trace registry —
`GET /traces/recent` surfaces recent ids for debugging. The id is **not**
included in chat/task response bodies or error bodies.