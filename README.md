# Jarvis Assistant

Local-first hybrid AI assistant: FastAPI backend, LangGraph orchestration,
Ollama for local models, OpenRouter for complex-task fallback, and a
Streamlit chat UI with a "select text → ask follow-up" workflow.

Requires **Python ≥ 3.14**. Package management and running is done with [`uv`](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
```

Copy `.env.example` to `.env` and adjust model names to match `ollama list`.

## Run backend

```bash
uv run uvicorn jarvis.api.main:app --reload --app-dir src
```

API endpoints: `GET /health`, `GET /models`, `GET /documents/count`,
`POST /documents/upload`, `POST /documents/ingest-folder`, `POST /chat`,
`POST /tasks`, `GET /tasks/{task_id}`, `GET /runtime`,
`GET /sessions/{session_id}/token` (per-session bearer token), and
`GET /traces/recent` (recent trace registry entries). Approval responses
(«Approve / Deny») are submitted by re-posting the pending message to
`POST /chat` with the `approved` field set.

Phase 5+ management endpoints: `GET/DELETE /documents` (list / delete with
`?confirm=1`, plus `POST /documents/reindex`), `GET/DELETE /memory` and
`DELETE /memory/{id}` (conversation memory controls, destructive ops require
`confirm=1`), `POST/GET/DELETE /feedback` (rate replies; `jarvis-evaluate`
summarises them), and `GET /cost` (estimated cloud spend vs
`CLOUD_DAILY_BUDGET_USD`).

## Run frontend

```bash
uv run streamlit run streamlit_app.py
```

The sidebar shows live backend health, the currently configured local and
cloud models, the current size of the RAG store, a GPU runtime panel, a live
traces panel, and an "Export conversation (.md)" download button. First-message suggestion pills
help you get started. Each assistant reply is annotated with badges for the
branch path and the model that produced it, plus the list of tools used,
expandable citation / debug sections, and a "Copy answer" popover. After an
assistant reply you can paste a snippet from it, then ask a follow-up question
framed around that selection, or hit "Retry last message". The toolbar
exposes "Show reasoning", an answer style (default / concise / detailed /
code / teaching / architecture / research), "Run as background task", and "Show debug
info" toggles. Tool actions flagged medium/high risk surface an inline
Approve / Deny card before execution. Long-running questions can be
dispatched as background tasks (`Background` toggle), which the UI submits
to `POST /tasks` and polls to `GET /tasks/{id}`. The UI uses a dark theme
configured in `.streamlit/config.toml`.

## Background tasks

```bash
# submit a long task (runs on a bounded in-process thread pool)
curl -X POST localhost:8000/tasks -H 'Content-Type: application/json' \
  -d '{"description": "Review the design", "session_id": "abc123"}'

# poll for status / result
curl localhost:8000/tasks/<task_id>
```

Each task persists through `queued → running → completed/failed/cancelled`
in the DB, pausing in `waiting_for_approval` when a risky tool call needs
human sign-off (resolved via `POST /tasks/{id}/approve`, `/deny`, or
`/cancel`).

## Security, errors and observability

### Durable approvals & sessions

Approvals are **durable**: a pending tool call is written to the DB together
with its TTL, so an interrupted run (browser refresh, backend restart) never
loses the pending action. The UI shows an inline Approve / Deny card with a
live countdown; your choice is sent back to `POST /chat` with the `approved`
field set, and only the *exact* captured tool call executes. **Deny** marks
the durable row `denied` server-side, so a later approve cannot replay the
cancelled action. If the TTL has expired the server answers **410 Gone** so
a stale approval can never fire. A periodic maintenance sweeper marks
expired approvals `expired`, hard-deletes them after
`EXPIRED_APPROVAL_RETENTION_HOURS`, and drops sessions inactive past
`SESSION_TTL_DAYS`.

Sessions are persisted too — a fresh message after a restart carries the old
session forward instead of starting from scratch. Each session exposes
metadata (`created_at`, `last_active_at`, `message_count`) via
`GET /sessions/{session_id}`.

### Per-session bearer tokens

With `REQUIRE_SESSION_TOKEN=true`, `POST /chat` and `POST /tasks` require a
token obtained from `GET /sessions/{session_id}/token`. Tokens are per-session
(they cannot be replayed against another session), persisted in the DB, and
stable for the life of the session. When disabled (default) the token is
optional and only used to label the session.

#### Token security at rest (production hardening)

Tokens are stored **hashed** (never in plaintext). The hash algorithm is
configurable (`SESSION_TOKEN_HASH_SCHEME`): `argon2` (default), `bcrypt`, or
`pbkdf2`. Inside the process the issued token is cached in a small bounded
in-memory map so the same session returns the *same* token for its lifetime —
after a backend restart the plaintext is gone and the token **rotates**
automatically on next use, so a leaked database file exposes only hashes.

- `POST /sessions/{session_id}/rotate-token` — explicitly rotate the token
  (immediately invalidates the old one).
- `POST /sessions/{session_id}/revoke` — revoke the token; a revoked token is
  rejected with **403** until a new one is issued.
- Tokens expire after `SESSION_TOKEN_TTL_HOURS` (absolute, no sliding) and
  rotate passively at `SESSION_TOKEN_ROTATION_HOURS`; expiry/rotation timestamps
  are reported in `GET /sessions/{session_id}`.

### Rate limiting

`RATE_LIMIT_PER_MINUTE` (default 300, `0` disables) throttles requests per
session / client IP. When exceeded the API returns **429 Too Many Requests**
with a `Retry-After` hint. The Streamlit UI never trips it under normal use.

### Structured errors

Every error response follows one shape:

```json
{
  "error": "ollama_unavailable",
  "message": "Ollama is not reachable. Check if the Ollama service is running.",
  "suggested_action": "Start Ollama (`ollama serve`) and retry, or run as a background task."
}
```

Common codes: `invalid_input` (400), `no_pending_approval` (400),
`task_not_found` (404), `session_not_found` (404),
`task_not_awaiting_approval` (409), `approval_expired` (410),
`invalid_session_token` (403), `rate_limited` (429),
`ollama_unavailable` (503), `model_not_found` (502),
`request_timeout` (504), `out_of_memory` (507, with optional CPU-retry
hint), `internal_error` (500). The exact code is stable for
programmatic handling; `suggested_action` is a human-readable fix.

### Retry & GPU fallback

Transient Ollama failures (server restart, request timeout) are retried up to
`RETRY_MAX_ATTEMPTS` (default 3) with linear backoff. OOM under full GPU
offload is retried once on CPU (`GPU_FALLBACK_TO_CPU=true`); the response
then carries a warning so you know the run was CPU-only.

### Observability

Each request receives a **trace id** propagated to LangGraph runs, background
tasks and errors; the id is logged and recorded in the bounded in-memory trace
registry of the most recent runs (`GET /traces/recent`) with per-node timing
and durations. Every finished trace records GPU policy / processor split /
cloud usage / estimated cost alongside the duration. `TRACE_RETENTION_LIMIT`
(default 256) caps how many traces are kept in memory. The Streamlit sidebar
shows a "Traces" panel that refreshes these live; `JSON_LOGS_ENABLED=true`
switches the log format to JSON for parsing.

### Cloud cost guardrails

Cloud (complex-branch) calls are protected by `CostGuard` (`src/jarvis/models/cost_guard.py`):

- **Per-request cap** — `CLOUD_MAX_REQUEST_COST_USD` (default 0.25): the prompt
  is priced against `config/model_pricing.json` before any API call; oversized
  requests are refused with a typed error and the branch falls back to local.
- **Per-session cap** — `CLOUD_MAX_SESSION_COST_USD` (default 2.0): once a
  session's cumulative estimate passes the cap, further cloud calls are refused.
- **Daily budget** — `CLOUD_DAILY_BUDGET_USD` (`0` = unlimited): cloud calls
  fall back to local once the day's spend is reached.
- **Approval gate** — `CLOUD_REQUIRE_COST_APPROVAL=true` (default): the complex
  branch pauses and shows an estimated-cost approval card; it resumes only when
  you approve (`approved=true` on `POST /chat`), and the estimate is recorded.
- **Usage persistence** — `CLOUD_COST_TRACKING_ENABLED=true`: every cloud call
  writes a `cloud_usage` row (model, session, tokens, estimated cost) surfaced
  through `GET /cost` (`spend_today`, per-session totals, recent calls).
- Pricing comes from `config/model_pricing.json` (exact + substring model
  rules with sensible defaults for unknown models); edit it or point
  `CLOUD_PRICING_CONFIG_PATH` at your own table. Actual token usage from the
  provider is recorded when available.

### Secret-token redaction

Output guardrails redact high-entropy secret-like tokens (API keys, JWT
patterns, bearer tokens) from assistant replies before they reach the UI —
the raw secret never leaks into the chat or exported transcripts.

## Document upload

`.txt` / `.md` / `.pdf` / `.docx` files can be uploaded over HTTP. Text
files are read as UTF-8; PDFs are extracted per-page (with `pypdf`) and
DOCX via `docx2txt`, then chunked, embedded, and upserted into Chroma by
deterministic ID (re-uploading the same file is a no-op). Binary files
beyond 20 MB are rejected:

```bash
curl -X POST localhost:8000/documents/upload -F 'files=@notes.txt'
curl -X POST localhost:8000/documents/upload -F 'files=@manual.pdf'
curl localhost:8000/documents/count
curl -X POST localhost:8000/documents/ingest-folder -d 'folder=./data/docs'
```

Folders can also be ingested from a mounted path for containers.

## Retrieval (hybrid)

RAG context is produced by *hybrid retrieval*: Chroma cosine-similarity
vector search forms the base ranking, then an in-process BM25 keyword score
reranks the top candidates so chunks mentioning the exact query terms are
boosted. The blend is controlled by `RERANK_KEYWORD_WEIGHT` (0 = pure
vector, 1 = pure keyword, default 0.25). Each chunk is tagged with a `kind`
metadata field so queries can be restricted to a logical collection
(`docs` / `memory` / `code` / `conversations`). PDF chunks carry
`page` + `section` metadata that appears in citations as `(p.3) [page-3]`.

## Ingest documents into the RAG store

```bash
# default folder (settings.docs_folder → ./data/docs)
uv run jarvis-ingest

# or invoke as a module
uv run python -m jarvis.cli.ingest --folder path/to/notes --ext txt --ext md

# preview without writing
uv run jarvis.cli.ingest --dry-run -v
```

`.txt` / `.md` / `.pdf` / `.docx` files are chunked, embedded with the
configured Ollama embedding model, and upserted into Chroma by deterministic
ID, so this is safe to run repeatedly. The same ingestion is available over
HTTP via `POST /documents/upload`.

## GPU / Ollama runtime optimization

This section describes how Jarvis tunes the **local Ollama runtime to maximise
GPU VRAM usage and minimise unnecessary CPU / system-RAM usage** — **without
changing, quantizing, downgrading, renaming, or replacing your current model.**

### What it does

- Passes request-level options (`num_ctx`, `num_gpu`, `keep_alive`,
  `temperature`) to every local `ChatOllama` call from settings.
  (`num_batch`, `flash_attention` and `kv_cache_type` are **not** applied
  per-request — ChatOllama has no constructor field for them; configure them
  server-side via `OLLAMA_NUM_BATCH` / `OLLAMA_FLASH_ATTENTION` /
  `OLLAMA_KV_CACHE_TYPE` on `ollama serve`.)
- **Forces full GPU offload** with `num_gpu=-1`. Every layer is placed on the
  GPU, so the model runs entirely in VRAM and **never spills into system RAM**;
  if it cannot fit in VRAM, Ollama refuses to load it instead of falling back
  to a partially-CPU execution.
- Builds all model clients **lazily** (no models load at import/startup; at
  most one local generation model is active per request).
- **Bounded context**: history is capped by `HISTORY_MAX_TURNS` +
  `CONTEXT_TOKEN_BUDGET`; retrieved RAG content is capped by
  `RAG_CONTEXT_TOKEN_CAP`; selected-text snippets are capped by
  `SELECTED_TEXT_TOKEN_CAP`; older turns are truncated first; the system +
  current user message are always preserved.
- **Single-model loading**: `OLLAMA_NUM_PARALLEL=1` and
  `OLLAMA_MAX_LOADED_MODELS=1` keep one local generation model in VRAM.

### This does NOT change the model

This optimization is **runtime-only**. It does not:

- create, rename, delete, or quantize any model,
- pull a new model,
- modify model weights,
- change which model is selected for a task (`select_model` is untouched).

### Dedicated VRAM vs system RAM

- **Dedicated VRAM** (GPU memory, e.g. 8 / 12 / 24 GB on the GPU) is where the
  model weights + KV cache live for GPU-only execution.
- **System RAM** is fallback memory used when the model doesn't fully fit in
  VRAM — the CPU then runs some layers, which is slower.
- A model **larger than available dedicated VRAM** may still require CPU/RAM
  (partial offload). Jarvis reports this honestly via `GET /runtime` and the
  Streamlit "GPU Runtime" sidebar, and recommends keeping a single model loaded
  when that happens.

### Expected behavior when the strong local model exceeds VRAM

`STRONG_LOCAL_MODEL` (e.g. `qwen3:14b`, ~9.3 GB) is used for
medium/difficult general questions. With `OLLAMA_NUM_GPU=-1` Jarvis asks
Ollama for *full* GPU offload: if the model + KV cache does **not** fit in
dedicated VRAM (e.g. an 8 GB laptop GPU), Ollama refuses to load it, Jarvis
catches the OOM (`507`) and, when `GPU_FALLBACK_TO_CPU=true`, retries once
with `num_gpu=0` — so the request still completes but runs **100% on CPU**
(noticeably slower, and the response carries a `warning` + `fallback_used:
gpu_to_cpu`). This is by design, not a config error.

- Smaller models that fit (e.g. `qwen3:8b`, ~5.2 GB) run 100% GPU.
- Set `USE_STRONG_LOCAL=false` to keep medium/difficult general questions on
  `GENERAL_MODEL` if you'd rather avoid the CPU fallback entirely.
- `GET /runtime` reports the honest split (`100% GPU` / `Partial CPU/GPU` /
  `100% CPU` / `Unknown`) based on what Ollama actually reports; in the
  Docker backend it shows `Unknown` because the container lacks `ollama` /
  `nvidia-smi` (see the Terminal Test Suite section).

### GPU fallback policy (`GPU_POLICY`)

`GPU_POLICY` controls what happens when the requested local model cannot run
fully on the GPU:

| Policy | Behavior |
|---|---|
| `require_gpu` | The request **refuses to run on CPU**. If the model does not fit VRAM (or the split would be partial when `GPU_REQUIRE_FULL_OFFLOAD=true`), the API returns a typed **507 `gpu_required`** error with a `suggested_action` — never a silent CPU fallback. |
| `prefer_gpu` (default) | Use GPU offload when it fits; on OOM / VRAM pressure, retry once on CPU (`GPU_ALLOW_CPU_FALLBACK=true`) and mark the response `fallback_used` + a warning. |
| `allow_cpu` | Never block on GPU availability; CPU execution is always permitted. |

Additional knobs: `GPU_RUNTIME_CHECK_ENABLED` probes VRAM headroom at request
time (using Ollama's `/api/show` — the probe is only fired for `require_gpu`
or for a strong model under `prefer_gpu`, never on every request);
`GPU_MAX_VRAM_PERCENT` / `GPU_MIN_FREE_VRAM_MB` bound when fallback is
considered; `GPU_STRONG_MODEL_ALLOW_PARTIAL_OFFLOAD=false` routes a
too-large strong model to `FALLBACK_MODEL` (or the general model) instead of
splitting layers. Every decision is recorded on the response and in the trace
(`gpu_policy`, `processor_split`, `gpu_fallback_used`, `cpu_fallback_used`,
`runtime_warning`).

### Verification commands (Windows PowerShell)

```powershell
# Is Ollama reachable + which model is currently loaded (PROCESSOR column)?
ollama ps

# GPU + VRAM usage in real time
nvidia-smi

# Jarvis runtime diagnostics endpoint
uv run uvicorn jarvis.api.main:app --reload --app-dir src
# in another window:
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/runtime

# Startup validation (reachable + model exists + test chat + GPU avail)
uv run jarvis-validate-runtime
```

## Benchmarking & performance regression

### `jarvis-benchmark` — GPU/CPU performance runs

A safe benchmark that runs a prompt at several context sizes against the
configured local model and reports honest latency / token / processor-split
figures. Baselines are stored in `reports/baseline.json` (no prompts,
responses, or secrets — only safe metadata + metrics):

```bash
# run a benchmark
uv run jarvis-benchmark --model qwen3:8b

# persist the run as the regression baseline
uv run jarvis-benchmark --model qwen3:8b --save-baseline

# compare the latest run against the saved baseline
uv run jarvis-benchmark --model qwen3:8b --compare-baseline
```

### `jarvis-evaluate-performance` — scenario regression suite

A deterministic scenario suite over the routing branches (general / coding /
RAG / tool-call / background-planning). It uses a **mock runner by default**
(no LLM, GPU, or cloud involved), and touches local Ollama only with `--live`.
The cloud is never called — `--allow-cloud` exists only for CLI parity:

```bash
uv run jarvis-evaluate-performance                    # mock, all scenarios
uv run jarvis-evaluate-performance --scenario coding  # one scenario
uv run jarvis-evaluate-performance --live             # opt-in local Ollama
uv run jarvis-evaluate-performance --output eval.json --markdown eval.md
```

Both CLIs are covered by `tests/test_performance_eval.py` and the benchmark
tests in `tests/test_benchmark.py`.

### Interpreting the processor split

`GET /runtime`'s `processor` field (and `ollama ps`'s PROCESSOR column) reports
one of:

| Value | Meaning |
|---|---|
| `100% GPU` | Model fully fits in dedicated VRAM — all layers on GPU. Best speed. |
| `Partial CPU/GPU` | Some layers on GPU, some on CPU. Slower; **the model is larger than available dedicated VRAM or the context allocation is too large.** The app uses the maximum available GPU offload; complete GPU-only execution is not possible without more VRAM or a smaller model. |
| `100% CPU` | No GPU offload at all. Slowest. Check `nvidia-smi` and that Ollama can see the GPU. |
| `Unknown` | Ollama unreachable / no model loaded / `nvidia-smi` missing. The app still runs. |

### How to change context size safely

Edit `.env`:

```
OLLAMA_CONTEXT_LENGTH=4096      # conservative default
```

Raise it **only** if your model/context genuinely needs more and VRAM allows.
Lowering it **frees KV cache memory** — useful when you see
`Partial CPU/GPU`. After changing it, restart the backend (and Ollama if you
also set the server-side `OLLAMA_*` env vars).

### Restarting Ollama on Windows

```powershell
# stop
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
# start (desktop app path may differ)
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
# verify
ollama ps
```

### How to roll back the optimization settings

To disable all runtime tuning and revert to Ollama's server defaults, set in
`.env`:

```
GPU_OPTIMIZATION_ENABLED=false
```

That makes `_runtime_options()` return an empty dict — no `num_ctx` /
`num_gpu` / `keep_alive` request overrides are sent. To fully roll back all
of the runtime variables to their defaults, delete (or comment out) the
`--- GPU / Ollama runtime optimization ---` block from `.env`. None of these
changes touched your model; no roll-back step reinstalls or re-pulls a model.

## Run tests

```bash
uv run pytest
```

## Terminal Test Suite (API E2E)

Run a full terminal-based end-to-end API suite (no browser/Streamlit needed):

```powershell
.\tests\terminal_test_suite.ps1
.\tests\terminal_test_suite.ps1 -VerboseMode
.\tests\terminal_test_suite.ps1 --verbose
```

Optional flags:

- `-BaseUrl <url>` — backend base URL (default `http://127.0.0.1:8000`).
- `-WorkspaceRoot <path>` — host directory mapped to the backend `WORKSPACE_DIR`,
  used for direct filesystem effect assertions when running non-containerized.
- `-SimulateOllamaDown` — stop the local Ollama process, verify a structured 503,
  restart it. Runs in isolation (no other tests execute).
- `-AllowRestart -RestartCommand "<cmd>"` — enable restart-based persistence checks.
- `-SimplePromptMs / -ToolPromptMs / -RagPromptMs <ms>` — performance latency budgets.
  Defaults are generous (60s / 90s / 120s) because local models on CPU are slow;
  tighten them on fast GPU hardware to make the Performance tests real regression guards.

What it validates:

- Health and diagnostics (`/health`, `/models`, `/documents/count`, `/runtime`)
- Model routing behavior through `POST /chat`
- Tool execution coverage (`calculator`, `search_code`, `read_file`, `list_directory`, `git_diff`)
- Human approval flow for risky tools (approve/deny/expiry where feasible)
- Background task lifecycle (`POST /tasks`, `GET /tasks/{id}`, cancel path)
- Structured error handling and guardrail responses
- Security controls (sensitive-file/path blocking, dangerous shell rejection, session isolation, rate-limit probe)
- Persistence checks (optional restart-based validation)
- Performance thresholds and GPU/runtime checks

Expected output:

- Per-test `[PASS]`, `[FAIL]`, or `[SKIP]` lines with category and reason
- A final summary with pass/fail/skip totals
- Exit code `0` when all required tests pass
- Exit code `1` when any required test fails

Behavior notes:

- Model-dependent tests (tool execution, approval generation, guardrail refusals)
  run each case in its **own fresh session** and retry up to three times. This is
  deliberate: small local models degrade on long shared-session history and
  occasionally answer without calling a tool. When the model does not cooperate
  (no tool call / no refusal wording), the case reports `[SKIP]` with the reason
  rather than a false `[FAIL]` — the backend path simply could not be exercised.
- The suite runs against a containerized backend (Docker) when the model base URL
  contains `host.docker.internal` / `docker.internal`. In that mode file side-effects
  are verified via API read-back (a fresh-session `read_file` call) because the
  backend writes into its own `workspace` volume, not the host directory. Cleanup
  of residual container-workspace files is best-effort; see the INFO line printed at
  the end.
- `/runtime` reports `processor=Unknown` when the container lacks the `ollama` CLI /
  `nvidia-smi`; that is explained by `warnings` and reported as a skip, not a failure.

Troubleshooting:

- Ensure backend is running at `http://127.0.0.1:8000` or pass `-BaseUrl`.
- If complex cloud routing is unconfigured (`/models.complex.configured=false`), cloud-specific checks are skipped.
- If Ollama is unavailable, tests requiring live local inference may fail or be skipped depending on category intent.
- Rate-limit tests depend on `RATE_LIMIT_PER_MINUTE`; high limits produce a skip instead of fail.
- Persistence restart checks are opt-in:
  - `-AllowRestart -RestartCommand "<your restart command>"`
  - Use a command that restarts only the backend process for your environment.


## Configuration

All settings live in `src/jarvis/config/settings.py` and are loaded from
`.env` (see `.env.example`). Notable knobs:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local model server |
| `GENERAL_MODEL` | `qwen3:8b` | General chat (Q4_K_M) |
| `STRONG_LOCAL_MODEL` | `qwen3:14b` | Medium/difficult general (Q4_K_M) |
| `CODING_MODEL` / `CODING_MODEL_SMALL` | `qwen2.5-coder:7b-q5_K_M` / `qwen2.5-coder:7b-q5_K_M` | Coding branch (5-bit for syntax integrity) |

> **Coding model must support native tool calling.** Both coding-branch models are
> bound to the coding tools via Ollama function calling, so they must emit structured
> `tool_calls` — not just reply in prose. `qwen2.5-coder:7b` does **not** reliably do
> this in Ollama (it returns tool calls as JSON text in `content`), so set
> `CODING_MODEL` / `CODING_MODEL_SMALL` to a tool-call-capable model such as
> `qwen3:8b` or `qwen3-coder:30b` when the coding branch needs tools.
| `EMBEDDING_MODEL` | `qwen3-embedding:latest` | Chroma embeddings |
| `USE_STRONG_LOCAL` | `true` | Set `false` to keep general branch on `GENERAL_MODEL` |
| `OPENROUTER_API_KEY` | _empty_ | Optional cloud fallback for the complex branch |
| `COMPLEX_MODEL_CHAIN` | `claude-opus-4.1,gpt-5.5,gemini-2.5-pro` | Models tried in order on the complex branch |
| `VECTOR_DB_PATH` | `./data/vector_store` | Chroma persistent store |
| `DOCS_FOLDER` | `./data/docs` | Default folder for `jarvis-ingest` |
| `HISTORY_MAX_TURNS` | `20` | Max (user, assistant) turns kept in the prompt |
| `CONTEXT_TOKEN_BUDGET` | `12000` | Word-count-proxy budget for history truncation |
| `RETRIEVAL_TOP_K` | `5` | Default RAG chunk count |
| `RAG_CONTEXT_TOKEN_CAP` | `2048` | Max tokens (word proxy) for retrieved RAG context block |
| `SELECTED_TEXT_TOKEN_CAP` | `1024` | Max tokens for highlighted selected-text snippet |
| `RAG_RELEVANCE_THRESHOLD` | `0.5` | Cosine-distance gate: only on-topic RAG chunks are auto-injected |
| `RAG_ENABLED` | `true` | Master switch for the whole RAG pipeline |
| `RAG_MIN_RELEVANCE_SCORE` | `0.5` | Similarity-score gate; takes precedence over the legacy distance threshold when set |
| `RAG_VECTOR_WEIGHT` / `RAG_KEYWORD_WEIGHT` | _empty_ | Phase 5 hybrid-rerank weights; fall back to `RERANK_KEYWORD_WEIGHT` |
| `RAG_RERANK_ENABLED` | `true` | Set `false` to disable keyword (BM25) reranking |
| `RETRIEVAL_PER_SOURCE_LIMIT` | `0` | Max chunks pulled from one source per query (`0` = unlimited) |
| `MAX_PLAN_STEPS` | `8` | Cap on the complex-branch planning node (`0` = no planning) |
| `MAX_TASK_DURATION_SECONDS` | `0` | Hard wall-clock cap for a background task (`0` = unlimited) |
| `CLOUD_MAX_PROMPT_TOKENS` | `0` | Refuse cloud calls whose prompt exceeds this estimate (`0` = unlimited) |
| `CLOUD_DAILY_BUDGET_USD` | `0` | Rough daily cloud-spend budget; cloud falls back to local once reached |
| `CLOUD_MAX_REQUEST_COST_USD` | `0.25` | Refuse a cloud call whose estimated prompt cost exceeds this (USD) |
| `CLOUD_MAX_SESSION_COST_USD` | `2.0` | Refuse further cloud calls in a session once its cumulative estimate passes this (USD) |
| `CLOUD_REQUIRE_COST_APPROVAL` | `true` | Pause for explicit human approval (with estimated cost) before any cloud call |
| `CLOUD_COST_TRACKING_ENABLED` | `true` | Persist each cloud call's estimated cost to the `cloud_usage` table (`GET /cost`) |
| `CLOUD_PRICING_CONFIG_PATH` | `./config/model_pricing.json` | JSON pricing table (models + per-1M-token USD rates) used for estimates |
| `GPU_OPTIMIZATION_ENABLED` | `true` | Master switch for request-level Ollama runtime options |
| `OLLAMA_NUM_GPU` | `-1` | Offload ALL layers to GPU (100% GPU, no system-RAM spill) |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | `num_ctx` sent per request |
| `OLLAMA_NUM_BATCH` | `512` | `num_batch` prompt batch size — **server-side only** (not applied per-request by ChatOllama) |
| `OLLAMA_FLASH_ATTENTION` | `1` | Flash attention — **server-side only** (requires Ollama >= 0.5; not applied per-request) |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | KV cache quantization (q8_0 halves KV memory) — **server-side only** (not applied per-request) |
| `OLLAMA_KEEP_ALIVE` | `5m` | How long a model stays loaded in VRAM after last request |
| `OLLAMA_NUM_PARALLEL` | `1` | Server-side: max concurrent requests Ollama serves |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Server-side: max models loaded simultaneously |
| `RERANK_KEYWORD_WEIGHT` | `0.25` | Hybrid retrieval keyword-vs-vector blend (0 = pure vector) |
| `AUTO_REINDEX_ENABLED` | `false` | Off by default; enables incremental re-ingest semantics |
| `AUTO_REINDEX_INTERVAL` | `300` | Polling interval (s) for a future background file watcher |
| `WORKSPACE_DIR` | `./workspace` | Root that write/edit/shell coding tools are confined to |
| `SHELL_ALLOWED_COMMANDS` | `pwd,ls,cat,...` | Allowlist prefix for `run_shell` |
| `TOOL_SUBPROCESS_TIMEOUT` | `60` | Seconds before a shell subprocess is killed |
| `RUNTIME_MODE` | `local` | `local` (SQLite + embedded store, no Docker) / `docker` (Postgres via compose) / `auto` (prefer local) |
| `POSTGRES_DSN` | _empty_ | `postgresql+psycopg://…`; empty → local SQLite fallback |
| `SQLITE_PATH` | `./data/jarvis.db` | Fallback DB file when no Postgres DSN |
| `SUMMARY_EVERY_TURNS` | `10` | Periodic summarization cadence (pairs of turns) |
| `DEFAULT_ANSWER_STYLE` | _empty_ | Default answer-style suffix |
| `DEFAULT_SHOW_REASONING` | `false` | Show reasoning by default |
| `RETRY_MAX_ATTEMPTS` | `3` | Retries for transient Ollama failures (1 = off) |
| `RETRY_BACKOFF_SECONDS` | `1.0` | Base backoff between retries (linear growth) |
| `GPU_FALLBACK_TO_CPU` | `true` | Retry OOM once on CPU instead of failing |
| `RATE_LIMIT_PER_MINUTE` | `300` | Requests/minute per session/IP; `0` disables |
| `REQUIRE_SESSION_TOKEN` | `false` | Require per-session bearer token on `/chat` + `/tasks` |
| `JSON_LOGS_ENABLED` | `false` | Emit JSON-formatted logs for parsing |
| `SESSION_TTL_DAYS` | `7` | Delete sessions inactive for this many days (`0` disables) |
| `EXPIRED_APPROVAL_RETENTION_HOURS` | `24` | Hard-delete expired approval rows after this many hours |
| `MAINTENANCE_SWEEP_INTERVAL` | `300` | Periodic maintenance sweep interval, seconds (`0` disables) |
| `GPU_POLICY` | `prefer_gpu` | `require_gpu` / `prefer_gpu` / `allow_cpu` — how GPU unavailability is handled (see GPU fallback policy) |
| `GPU_REQUIRE_FULL_OFFLOAD` | `false` | `require_gpu`: treat a partial CPU/GPU split as a policy violation |
| `GPU_MAX_VRAM_PERCENT` | `95` | Estimated max VRAM a model may use (0-100) before fallback is considered |
| `GPU_MIN_FREE_VRAM_MB` | `512` | Minimum free VRAM (MB) required before loading a strong model on GPU |
| `GPU_RUNTIME_CHECK_ENABLED` | `true` | Probe VRAM headroom at request time for `require_gpu` / `prefer_gpu` |
| `GPU_STRONG_MODEL_ALLOW_PARTIAL_OFFLOAD` | `false` | When the strong local model exceeds VRAM, route to the fallback model instead of partial offload |
| `GPU_ALLOW_CPU_FALLBACK` | `true` | `prefer_gpu` / `allow_cpu`: retry once on CPU instead of failing |
| `SESSION_TOKEN_HASH_SCHEME` | `argon2` | Hash algorithm for session tokens at rest: `argon2` / `bcrypt` / `pbkdf2` |
| `SESSION_TOKEN_TTL_HOURS` | `168` | Token validity window before a rotation is forced on next use |
| `SESSION_TOKEN_ROTATION_HOURS` | `72` | Passive token rotation cadence (hours) |
| `TRACE_RETENTION_LIMIT` | `256` | Max in-memory request traces retained for `GET /traces/recent` |

## Project structure

```text
src/jarvis/
├── api/                # FastAPI app, routes/, schemas/, errors.py
│   ├── main.py        # app + lifespan + /health + /models + /runtime
│   └── routes/        # chat.py, documents.py, tasks.py, runtime.py, sessions.py,
│                      #   traces.py, memory.py, feedback.py, cost.py
├── security/          # session_auth.py (tokens), ratelimit.py
├── observability/     # trace.py (trace ids + bounded registry)
├── orchestration/     # LangGraph state, router, branches, graph, approval gate
│   ├── graph.py       # compiled graph (InMemorySaver checkpointer) wiring nodes
│   ├── state.py       # JarvisState TypedDict
│   ├── router_node.py # intent classification (general/coding/complex)
│   ├── planning_node.py # Phase 5 plan generation for complex requests (capped)
│   ├── branches.py    # run_general/coding/complex branch nodes (typed errors)
│   ├── context_node.py, context_window.py  # RAG + sliding-window context + caps
│   ├── model_selector.py # picks model per intent × complexity
│   └── approval_node.py # check_risk + approval_gate + TTL (human-in-the-loop)
├── models/            # ollama_client.py, openrouter_client.py, cost_guard.py, runtime_diagnostics.py
├── tools/             # general + coding (write/edit/shell/run_tests/git_diff/list_directory) + registry.py
├── persistence/       # SQLAlchemy engine, models, repos (Postgres SQLite)
├── memory/            # ChromaDB store.py (multi-format ingest) + retrieve.py (hybrid)
│                      #   + summaries.py + memory_store.py + document_manager.py + query_quality.py
├── guardrails/        # input_guard, output_guard (PII + secret tokens), risk classification
├── cli/               # ingest.py, validate_runtime.py, evaluate.py
│                      #   (jarvis-ingest / jarvis-validate-runtime / jarvis-evaluate)
└── config/            # settings loaded from .env
```

## Architecture

```
classify_intent → plan_task → build_context → route
 ├── general  → general_llm → check_risk ─┬─ approval_gate → END
 │                                          ├─ execute_tools → record_tools → branch (loop)
 │                                          └─ END
 ├── coding   → coding_llm → check_risk ─┬─ approval_gate → END
 │                                         ├─ execute_tools → record_tools → coding_llm (loop)
 │                                         └─ END
 └── complex  → complex_branch → END
                 └─ on failure, fall back to general branch
```

- **Planning** (`planning_node.py`): when a request routes to `complex` and
  `MAX_PLAN_STEPS > 0`, a small local model produces a short ordered plan
  that is injected into the context window before the complex branch runs.
  The plan is capped at `MAX_PLAN_STEPS` and any failure degrades to no
  plan (the request still succeeds).

- **Routing** is conditional on `intent` (`general` / `coding` / `complex`).
  `classify_intent` is hybrid: rules (word-count length + keyword boosts) run
  first; for **borderline** prompts (medium-length, no decisive keyword) a
  small router model (`ROUTER_MODEL`, default `GENERAL_MODEL`) is asked in
  JSON mode to return `{"intent", "complexity"}`. Any router failure or
  malformed reply falls back to the rules — `ROUTER_LLM_ENABLED=false`
  disables the extra call for predictable latency.
- **Tool registry** (`tools/registry.py`) is the single source of truth: each
  branch's LLM is bound to its own tool set (`GENERAL_BOUND_TOOLS` /
  `CODING_BOUND_TOOLS` = safe read-only tools + approval-gated write/exec
  tools), while the graph's **shared** `ToolNode(all_tools())` executes
  whatever the risk layer allows. A tool an LLM requests always resolves to
  exactly one registered implementation.
- **Branches are tool-calling loops**: the LLM may request
  `calculator`, `rag_search`, `search_code`, `read_file`, etc., which
  execute via the shared tool node, then the LLM is re-invoked until it
  produces a final answer or reaches `max_tool_iterations` (default 5). The
  tools used and their results/errors are recorded into state and surfaced
  in the response (`tools_used`, `tool_results`, `tool_errors`, `sources`,
  `retrieved_context`).
- **Risk + approval**: every LLM tool call is classified low/medium/high
  (`guardrails/risk.py`). Low-risk, read-only calls (`calculator`,
  `rag_search`, `search_code`, `read_file`, `list_directory`, `git_diff`)
  execute automatically. Medium/high requests pause the graph at
  `approval_gate`, which records the *exact* pending tool calls (name +
  args), stamps an approval id + expiry (`approval_id`,
  `approval_expires_at`, TTL from `approval_ttl_seconds`, default 600 s), and
  emits a message for the UI. The API keeps the pending state in-memory and
  resumes on the next request with `approved=true`. On resume the stored
  call(s) execute exactly as captured — never an arbitrary action. If the
  TTL has passed, the API rejects the resume with **410 Gone** so a stale
  approval can never fire.
- **Coding tools** (workspace-guarded): `write_file`, `edit_file`,
  `run_shell`, `run_tests`, `git_diff`, `list_directory`, and `search_code`
  live in `tools/coding` and `tools/general`. File writes are confined under
  `WORKSPACE_DIR`, `run_shell` only allows commands from
  `SHELL_ALLOWED_COMMANDS`, and subprocesses time out after
  `TOOL_SUBPROCESS_TIMEOUT` seconds. `git_diff` is read-only (flags
  restricted to a safe allowlist; output capped at 8 KB) and
  `list_directory` is read-only (max 200 entries; refuses paths that escape
  the workspace). `read_file` is read-only and low risk by default, but
  still opens a workspace-confined path and refuses secrets/sensitive files;
  `read_file` against a sensitive path escalates to approval. Write/exec
  tools are classified medium/high risk so they go through approval.
- **Complex branch** tries the configured OpenRouter model chain in order;
  if all fail it transparently degrades to the local general branch.
- **Memory / summarization**: ChromaDB cosine collection seeded via the CLI
  or HTTP upload; `rag_search` injects a formatted context block into the
  prompt. After `SUMMARY_EVERY_TURNS` turns in a session, `maybe_summarize`
  asks the general model for a recap, stores it, and ingests it so older
  context stays reachable after the sliding window drops it.
- **Persistence**: sessions, messages, summaries, and tasks are stored via
  SQLAlchemy in Postgres (when `POSTGRES_DSN` is set — see `docker-compose.yml`)
  or a local SQLite file. Background tasks run on a small in-process
  `ThreadPoolExecutor` and update rows through `pending → running →
  completed/failed`.
- **Context window**: conversation history is truncated to
  `HISTORY_MAX_TURNS` and a `CONTEXT_TOKEN_BUDGET` word-count proxy before
  each LLM call. Retrieved RAG context and selected text are capped at
  `RAG_CONTEXT_TOKEN_CAP` / `SELECTED_TEXT_TOKEN_CAP`.
- **Checkpointer**: the compiled graph runs with a LangGraph `InMemorySaver`
  checkpointer, so interrupted runs can be resumed and tool-loop state is
  preserved across requests in-process.

## Runtime modes & Docker usage

The assistant runs in one of two runtime modes (see `RUNTIME_MODE` in
`.env`; example profiles in `.env.local.example` and `.env.docker.example`):

* **local** (default) — SQLite persistence, embedded ChromaDB vector store,
  in-process task executor. **No Docker is required or contacted.** Ollama
  runs on the host. This is the mode most people should use.
* **docker** — adds optional Postgres persistence via `docker-compose.yml`.
  Everything else (Chroma, tasks, Ollama) stays on the host / in-process.
* **auto** — prefers local, and only switches to Docker when `POSTGRES_DSN`
  is set *and* the Docker daemon is reachable.

`GET /runtime` reports the resolved mode plus Docker/WSL status:
`runtime` (capabilities), `docker` (daemon reachable, running containers,
disk usage), and `wsl` (WSL2, default distro, which `.wslconfig` tuning keys
are present — never their values).

### Is Docker required?

No. Everything works without Docker:
| Feature | Local (no Docker) | Docker mode |
|---|---|---|
| Chat / routing / tools | ✅ | ✅ |
| RAG (ChromaDB) | ✅ embedded store | ✅ embedded store |
| Sessions / messages / approvals | ✅ SQLite | ✅ Postgres (compose) |
| Background tasks | ✅ in-process | ✅ in-process |
| Ollama (models) | ✅ host | ✅ host |

Docker only replaces SQLite with Postgres and can containerize the
backend/frontend for deployment. If you don't need Postgres, stay in local
mode — the app never probes or requires Docker there.

### Running with Docker (optional Postgres persistence)

```bash
# Docker mode (Postgres persistence)
copy .env.docker.example .env     # sets RUNTIME_MODE=docker + POSTGRES_DSN
docker compose up -d postgres
uv run uvicorn jarvis.api.main:app --host 0.0.0.0 --port 8000

# Full containerized stack (backend + frontend in containers)
docker compose up -d --build
```

Local mode needs none of that:

```bash
# Local mode (SQLite + embedded Chroma, no Docker)
copy .env.local.example .env      # sets RUNTIME_MODE=local (or omit entirely)
uv run uvicorn jarvis.api.main:app --host 0.0.0.0 --port 8000
```

### Validating the runtime

```bash
uv run jarvis-validate-runtime                 # resolved mode
uv run jarvis-validate-runtime --mode local    # SQLite + Chroma writability
uv run jarvis-validate-runtime --mode docker   # compose services reachable
```

The validation CLI is read-only: it never starts, stops, prunes, pulls, or
modifies anything. Docker-mode checks confirm the CLI exists, the daemon is
reachable, the compose services are running, and the configured Postgres
endpoint answers.

### Docker / WSL resource guidance (read-only tips)

Docker Desktop + WSL2 use RAM on your machine. These tips are informational —
the app never adjusts your system:

* WSL2 memory defaults to a share of host RAM. To cap it, create/edit
  `%USERPROFILE%\.wslconfig` (Windows) — e.g. `memory=8GB`,
  `processors=4`, `swap=2GB`, `[wsl2] autoMemoryReclaim=gradual` — then
  restart WSL (`wsl --shutdown`, Docker Desktop will restart with it).
* Docker Desktop has a "Resources → Memory" slider; raise it if Postgres
  container stalls, lower it if your host (or Ollama VRAM budget) is tight.
* The `/runtime` endpoint's `wsl` block shows whether those tuning keys are
  present, so you can verify `.wslconfig` took effect — it never reads the
  values.
* Check disk usage any time with `docker system df` (safe, read-only).

## Tests

```bash
uv run pytest
uv run ruff check .
```

The suite measures coverage with `pytest-cov` (currently ~83% line coverage
across `src/jarvis`). `--cov-fail-under=0` means coverage is reported, not
enforced; raise it as coverage improves if you want a gate.

## Current status

✅ **Phase 1–2 (foundation + backend skeleton)** — Complete
✅ **Phase 3 (orchestration graph)** — three branches (general / coding /
complex) with conditional routing, tool-calling loop, and complex → general fallback
✅ **Tools** — calculator, RAG search, code search, `read_file`,
workspace-scoped write/edit, allowlisted shell, `run_tests`, plus read-only
`git_diff` and `list_directory`
✅ **Memory / RAG** — ChromaDB store with recursive chunking, hybrid
retrieval (vector + BM25 rerank, per-collection `kind` filter), multi-format
ingest (`.txt` / `.md` / `.pdf` / `.docx` with page/section metadata),
`jarvis-ingest` CLI, plus periodic conversation summarization
✅ **Document upload** — HTTP `POST /documents/upload` (text + PDF + DOCX),
`GET /documents/count`, `POST /documents/ingest-folder`
✅ **Transparency** — responses carry `tools_used`, `sources`, and
`retrieved_context`; the Streamlit UI renders badges, tool lists, and
collapsible citation / debug sections
✅ **Background tasks** — `POST /tasks` / `GET /tasks/{id}` with a bounded
in-process executor; UI toggle to run long prompts in the background
✅ **Persistence** — sessions/messages/summaries/tasks via SQLAlchemy on
Postgres (Docker) or a local SQLite fallback
✅ **Guardrails** — input validation, PII output redaction, tool risk
classification + human-in-the-loop approval gate wired into the graph.
Risky tool calls pause with a live TTL countdown; the exact pending tool
call is captured and only that call executes on approval, and expired
approvals are rejected (HTTP 410)
✅ **Tool loops** — a central tool registry binds per-branch tool sets and
feeds a shared `ToolNode`; both general and coding branches loop (LLM →
tool → LLM) with a `max_tool_iterations` cap and `tool_results`/`tool_errors`
recorded per turn
✅ **Streamlit UI** — chat, model-config sidebar, GPU runtime panel,
answer styles, selected-text follow-ups, "Retry last message",
"Copy answer", conversation export (`.md`), task offload, and a
pending-action card (exact tool calls + expiry countdown) with
Approve / Deny for risky tool actions
✅ **Resilience** — LangGraph `InMemorySaver` checkpointer for interrupted
run recovery; typed Ollama errors surface as clean HTTP statuses
(503 / 502 / 507 / 504)
✅ **Phase 4B hardening** — durable approvals/sessions (TTL + SQL-backed,
expired resumes rejected with 410), structured error bodies
(`error`/`message`/`suggested_action`), retry with backoff + OOM→CPU fallback,
end-to-end trace ids + bounded trace registry (`GET /traces/recent`),
per-session bearer tokens (`GET /sessions/{id}/token`), rate limiting (429),
and secret-token redaction in guardrails — all covered by permanent
regression suites (491 tests passing, ruff clean)
✅ **Phase 4C — runtime modes & operational hardening** — explicit
`RUNTIME_MODE` (local/docker/auto) with capabilities reporting on `/runtime`,
read-only Docker + WSL diagnostics (daemon, containers, disk, `.wslconfig`
key *presence* only — never values), `jarvis-validate-runtime --mode
local|docker`, `env.local/docker.example` profiles, Streamlit runtime-mode
panel, and safe Docker/WSL resource guidance — all covered by unit tests
that run without Docker, WSL, GPU, or Ollama installed
✅ **Phase 5 — RAG quality** — query rewriting + small-talk detection
(skips wasted embedding calls), hybrid rerank weights
(`RAG_VECTOR_WEIGHT` / `RAG_KEYWORD_WEIGHT`, backward-compatible with
`RERANK_KEYWORD_WEIGHT`), relevance gate via `RAG_MIN_RELEVANCE_SCORE`,
per-source retrieval caps, source dedup + page/section enrichment — all
fail-open and covered by `tests/test_rag_quality.py`
✅ **Phase 5 — conversation memory** — deterministic single-chunk summary
mirrors into Chroma (`kind=memory`), secret redaction before summarising,
evicted-window-turn summarization (`maybe_summarize_evicted`), memory
controls (`GET/DELETE /memory`, `/memory/export`, `confirm=1` required on
destructive ops), and memory-context injection into build_context —
covered by `tests/test_memory_controls.py`
✅ **Phase 5 — document management** — `GET /documents`, `GET/DELETE
/documents/{source}`, `DELETE /documents`, `POST /documents/reindex` (all
with `confirm=1` on destructive ops) + a Streamlit "Indexed documents"
panel — covered by `tests/test_document_manager.py`
✅ **Phase 5 — planning** — `planning_node` generates a capped step plan
(`MAX_PLAN_STEPS`) for complex requests and injects it into the context
window; background tasks honour a hard duration cap
(`MAX_TASK_DURATION_SECONDS`) — covered by `tests/test_planning_and_duration.py`
✅ **Phase 6 — feedback** — `POST/GET/DELETE /feedback` (thumbs up/down +
comment) with durable storage, thumbs buttons in the Streamlit chat, and a
`jarvis-evaluate` CLI that summarises ratings — covered by
`tests/test_feedback.py`
✅ **Phase 7 — cost guardrails** — `CostGuard` refuses oversized prompts
(`CLOUD_MAX_PROMPT_TOKENS`) and pauses cloud calls past a daily budget
(`CLOUD_DAILY_BUDGET_USD`), with `GET /cost` diagnostics and automatic
fallback to local models — covered by `tests/test_cost_guardrails.py`
✅ **Production hardening** — GPU fallback policy (`GPU_POLICY`:
`require_gpu`/`prefer_gpu`/`allow_cpu`, typed 507 `gpu_required`, honest
processor-split metadata, strong-model routing) in `tests/test_gpu_policy.py`;
session tokens hashed at rest (argon2/bcrypt/pbkdf2) with passive rotation +
`rotate-token`/`revoke` endpoints in `tests/test_token_security.py`; cloud
cost pricing + per-request/per-session caps + approval gate + `cloud_usage`
persistence in `tests/test_cloud_cost.py`; `jarvis-benchmark` +
`jarvis-evaluate-performance` regression CLIs in
`tests/test_performance_eval.py`; observability traces with GPU/cost/latency
fields and configurable `TRACE_RETENTION_LIMIT` in `tests/test_trace.py`

## Docs

See `docs/api.md` for the full endpoint reference and `docs/troubleshooting.md`
for common issues and fixes.
