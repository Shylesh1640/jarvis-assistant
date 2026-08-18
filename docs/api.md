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
  "warning": null,
  "elapsed_seconds": 3.42
}
```

`elapsed_seconds` is the wall-clock time spent producing this reply
(excluding any time the request waited for approval).

Note: the response does **not** carry a `trace_id` field; trace ids are
recorded in logs and surfaced via `GET /traces/recent`.

#### Answer styles

`answer_style` accepts: `concise`, `detailed`, `code`, `teaching`,
`architecture`, `research`. `research` structures the reply as a short
research brief (Summary / Key Findings / Sources) grounded in retrieved
context when available.

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

### `GET /documents`

List distinct indexed sources with chunk counts:

```json
{ "documents": [ { "source": "notes.md", "filename": "notes.md", "chunk_count": 3, "timestamp": "…" } ] }
```

### `GET /documents/{source}`

One source's chunks for inspection:

```json
{ "source": "notes.md", "chunk_count": 3, "chunks": [ { "chunk_id": "…", "text": "…", "page": 1, "section": "file", "timestamp": "…" } ] }
```

Errors: `404 document_not_found`.

### `DELETE /documents/{source}?confirm=1`

Remove one source. The `confirm=1` flag is **required**; without it you get
`400 confirmation_required`. Errors: `404 document_not_found`.

### `DELETE /documents?confirm=1`

Remove the whole document corpus (memory chunks are unaffected).

### `POST /documents/reindex`

Rebuild the index from the configured (or explicit) folder:

```json
{ "folder": "./data/docs" }
```

Returns `{ "files": 2, "chunks": 4, "skipped": 0 }`. Errors:
`404` when the folder is missing.

## Conversation memory

### `GET /memory`

List stored summaries for a session:

```json
{ "session_id": "abc123", "items": [ { "id": 1, "session_id": "abc123", "summary": "…", "from_message_id": 1, "to_message_id": 20, "created_at": "…" } ] }
```

### `GET /memory/{id}`

One summary. Errors: `404 memory_not_found`.

### `GET /memory/export`

Render the session's memory as Markdown for download:

```json
{ "session_id": "abc123", "markdown": "# Conversation memory\n\n## Summary #1 …" }
```

### `DELETE /memory/{id}?confirm=1`

Delete one summary. `confirm=1` is **required** (`400 confirmation_required`).
Errors: `404 memory_not_found`.

### `DELETE /memory?confirm=1`

Clear all memory for a session. Returns `{ "cleared": 2 }`.

## Feedback

### `POST /feedback`

Rate an assistant reply. Body:

```json
{ "session_id": "abc123", "session_token": "optional", "question": "what is RAG?", "answer": "Retrieval-augmented…", "score": "good", "comment": "optional", "model_used": "qwen3:8b" }
```

`score` is one of `good | bad | unclear`. Errors:
`422 invalid_score`, `400 missing_answer`. Returns `{ "stored": true, "id": 1 }`
(or `stored: false` when the DB is unavailable).

### `GET /feedback?session_id=…`

Recent feedback (all sessions by default):

```json
{ "items": [ { "id": 1, "session_id": "abc123", "question": "…", "answer": "…", "score": "good", "comment": null, "path_used": "general", "model_used": "qwen3:8b", "created_at": "…" } ], "count": 1 }
```

### `DELETE /feedback/{id}?confirm=1`

Delete one entry (`400 confirmation_required` without the flag;
`404 feedback_not_found` for a missing id).

### `DELETE /feedback?confirm=1`

Clear all feedback. Returns `{ "cleared": 3 }`.

## Cloud cost guardrails

### `GET /cost`

Estimated cloud-spend snapshot (prompt-only estimate, not an invoice):

```json
{ "day": "2026-08-18", "spent_today_usd": 0.0042, "daily_budget_usd": 1.0, "max_prompt_tokens": 0, "calls_today": 1, "recent_calls": [ { "day": "2026-08-18", "model": "openai/gpt-5.5", "cost_usd": 0.0042 } ] }
```

The guard refuses cloud calls when `CLOUD_MAX_PROMPT_TOKENS` or
`CLOUD_DAILY_BUDGET_USD` is exceeded; the complex branch falls back to
local models.

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
| 400 | `confirmation_required` | Destructive op without `?confirm=1` |
| 403 | `invalid_session_token` | Missing/invalid session token |
| 404 | `task_not_found` | Unknown task id |
| 404 | `session_not_found` | Session API missing row |
| 404 | `document_not_found` | No such indexed document/source |
| 404 | `memory_not_found` | Unknown summary id |
| 404 | `feedback_not_found` | Unknown feedback id |
| 409 | `task_not_awaiting_approval` | approve/deny on a non-waiting task |
| 410 | `approval_expired` | Approval TTL elapsed; re-ask to restart |
| 422 | `invalid_score` | Feedback score not good/bad/unclear |
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