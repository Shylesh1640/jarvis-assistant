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
`POST /tasks`, `GET /tasks/{task_id}`, `GET /runtime`.

## Run frontend

```bash
uv run streamlit run streamlit_app.py
```

The sidebar shows live backend health, the currently configured local and
cloud models, the current size of the RAG store, a GPU runtime panel, and an
"Export conversation (.md)" download button. First-message suggestion pills
help you get started. Each assistant reply is annotated with badges for the
branch path and the model that produced it, plus the list of tools used,
expandable citation / debug sections, and a "Copy answer" popover. After an
assistant reply you can paste a snippet from it, then ask a follow-up question
framed around that selection, or hit "Retry last message". The toolbar
exposes "Show reasoning", an answer style (default / concise / detailed /
code / teaching / architecture), "Run as background task", and "Show debug
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

Each task persists through `pending → running → completed/failed` in the
DB. Tasks run with approvals auto-approved (there is no interactive user).

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

- Passes request-level options (`num_ctx`, `num_batch`, `keep_alive`,
  `temperature`) to every local `ChatOllama` call from settings.
- Leaves **GPU offload to Ollama** — never hard-codes `num_gpu`. Ollama picks
  the maximum valid GPU offload automatically.
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
`num_batch` / `keep_alive` request overrides are sent. To fully roll back all
of the runtime variables to their defaults, delete (or comment out) the
`--- GPU / Ollama runtime optimization ---` block from `.env`. None of these
changes touched your model; no roll-back step reinstalls or re-pulls a model.

## Run tests

```bash
uv run pytest
```

## Configuration

All settings live in `src/jarvis/config/settings.py` and are loaded from
`.env` (see `.env.example`). Notable knobs:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local model server |
| `GENERAL_MODEL` | `qwen3:8b` | General chat |
| `STRONG_LOCAL_MODEL` | `qwen3:14b` | Upgraded local model for medium/difficult tasks |
| `CODING_MODEL` / `CODING_MODEL_SMALL` | `qwen3-coder:30b` / `qwen2.5-coder:7b` | Coding branch (hard / easy) |
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
| `GPU_OPTIMIZATION_ENABLED` | `true` | Master switch for request-level Ollama runtime options |
| `OLLAMA_CONTEXT_LENGTH` | `4096` | `num_ctx` sent per request (conservative) |
| `OLLAMA_NUM_BATCH` | `512` | `num_batch` prompt processing batch size |
| `OLLAMA_FLASH_ATTENTION` | `1` | Request flash attention (requires Ollama >= 0.5) |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | KV cache quantization (q8_0 halves KV memory) |
| `OLLAMA_KEEP_ALIVE` | `5m` | How long a model stays loaded in VRAM after last request |
| `OLLAMA_NUM_PARALLEL` | `1` | Server-side: max concurrent requests Ollama serves |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Server-side: max models loaded simultaneously |
| `RERANK_KEYWORD_WEIGHT` | `0.25` | Hybrid retrieval keyword-vs-vector blend (0 = pure vector) |
| `AUTO_REINDEX_ENABLED` | `false` | Off by default; enables incremental re-ingest semantics |
| `AUTO_REINDEX_INTERVAL` | `300` | Polling interval (s) for a future background file watcher |
| `WORKSPACE_DIR` | `./workspace` | Root that write/edit/shell coding tools are confined to |
| `SHELL_ALLOWED_COMMANDS` | `pwd,ls,cat,...` | Allowlist prefix for `run_shell` |
| `TOOL_SUBPROCESS_TIMEOUT` | `60` | Seconds before a shell subprocess is killed |
| `POSTGRES_DSN` | _empty_ | `postgresql+psycopg://…`; empty → local SQLite fallback |
| `SQLITE_PATH` | `./data/jarvis.db` | Fallback DB file when no Postgres DSN |
| `SUMMARY_EVERY_TURNS` | `10` | Periodic summarization cadence (pairs of turns) |
| `DEFAULT_ANSWER_STYLE` | _empty_ | Default answer-style suffix |
| `DEFAULT_SHOW_REASONING` | `false` | Show reasoning by default |

## Project structure

```text
src/jarvis/
├── api/                # FastAPI app, routes/, schemas/
│   ├── main.py        # app + /health + /models + /runtime
│   └── routes/        # chat.py, documents.py, tasks.py, runtime.py
├── orchestration/     # LangGraph state, router, branches, graph, approval gate
│   ├── graph.py       # compiled graph (InMemorySaver checkpointer) wiring nodes
│   ├── state.py       # JarvisState TypedDict
│   ├── router_node.py # intent classification (general/coding/complex)
│   ├── branches.py    # run_general/coding/complex branch nodes (typed errors)
│   ├── context_node.py, context_window.py  # RAG + sliding-window context + caps
│   ├── model_selector.py # picks model per intent × complexity
│   └── approval_node.py # check_risk + approval_gate + TTL (human-in-the-loop)
├── models/            # ollama_client.py, openrouter_client.py, runtime_diagnostics.py
├── tools/             # general + coding (write/edit/shell/run_tests/git_diff/list_directory) + registry.py
├── persistence/       # SQLAlchemy engine, models, repos (Postgres SQLite)
├── memory/            # ChromaDB store.py (multi-format ingest) + retrieve.py (hybrid) + summaries.py
├── guardrails/        # input_guard, output_guard (PII), risk classification
├── cli/               # ingest.py, validate_runtime.py (`jarvis-validate-runtime`)
└── config/            # settings loaded from .env
```

## Architecture

```
classify_intent → build_context → route
 ├── general  → general_llm → check_risk ─┬─ approval_gate → END
 │                                          ├─ execute_tools → record_tools → branch (loop)
 │                                          └─ END
 ├── coding   → coding_llm → check_risk ─┬─ approval_gate → END
 │                                         ├─ execute_tools → record_tools → coding_llm (loop)
 │                                         └─ END
 └── complex  → complex_branch → END
                 └─ on failure, fall back to general branch
```

- **Routing** is conditional on `intent` (`general` / `coding` / `complex`).
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

## Run with Docker (persistence)

```bash
docker compose up -d postgres
# then start the backend as usual — with POSTGRES_DSN set it uses Postgres,
# otherwise it falls back to SQLite automatically.
```

## Tests

```bash
uv run pytest
uv run ruff check .
```

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
