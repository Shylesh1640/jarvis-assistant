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
`POST /tasks`, `GET /tasks/{task_id}`.

## Run frontend

```bash
uv run streamlit run streamlit_app.py
```

The sidebar shows live backend health, the currently configured local and
cloud models, and the current size of the RAG store. First-message
suggestion pills help you get started. Each assistant reply is annotated
with badges for the branch path and the model that produced it, plus the
list of tools used and expandable citation / debug sections. After an
assistant reply you can paste a snippet from it, then ask a follow-up
question framed around that selection. Tool actions flagged medium/high
risk surface an inline Approve / Deny card before execution. Long-running
questions can be dispatched as background tasks (`Background` toggle),
which the UI submits to `POST /tasks` and polls to `GET /tasks/{id}`. The
UI uses a dark theme configured in `.streamlit/config.toml`.

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

`.txt` / `.md` files can be uploaded over HTTP (chunked, embedded, and
upserted into Chroma by deterministic ID; re-uploading the same file is a
no-op):

```bash
curl -X POST localhost:8000/documents/upload -F 'files=@notes.txt'
curl localhost:8000/documents/count
curl -X POST localhost:8000/documents/ingest-folder -d 'folder=./data/docs'
```

Folders can also be ingested from a mounted path for containers.

## Ingest documents into the RAG store

```bash
# default folder (settings.docs_folder → ./data/docs)
uv run jarvis-ingest

# or invoke as a module
uv run python -m jarvis.cli.ingest --folder path/to/notes --ext txt --ext md

# preview without writing
uv run jarvis.cli.ingest --dry-run -v
```

`.txt` / `.md` files are chunked, embedded with the configured Ollama
embedding model, and upserted into Chroma by deterministic ID, so this is
safe to run repeatedly. The same ingestion is available over HTTP via
`POST /documents/upload`.

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
│   ├── main.py        # app + /health + /models
│   └── routes/        # chat.py, documents.py, tasks.py
├── orchestration/     # LangGraph state, router, branches, graph, approval gate
│   ├── graph.py       # compiled graph wiring all nodes together
│   ├── state.py       # JarvisState TypedDict
│   ├── router_node.py # intent classification (general/coding/complex)
│   ├── branches.py    # run_general/coding/complex branch nodes
│   ├── context_node.py, context_window.py  # RAG + sliding-window context
│   ├── model_selector.py # picks model per intent × complexity
│   └── approval_node.py # check_risk + approval_gate (human-in-the-loop)
├── models/            # ollama_client.py, openrouter_client.py (fallback chain)
├── tools/             # general (calculator/rag_search/search_code), coding (write/edit/shell/run_tests)
├── persistence/       # SQLAlchemy engine, models, repos (Postgres SQLite)
├── memory/            # ChromaDB store.py + retrieve.py + summaries.py
├── guardrails/        # input_guard, output_guard (PII), risk classification
├── cli/               # ingest.py — `jarvis-ingest` CLI for the RAG store
└── config/            # settings loaded from .env
```

## Architecture

```
classify_intent → build_context → route
 ├── general  → general_llm → check_risk ─┬─ approval_gate → END
 │                                          ├─ general_tools → general_llm (loop)
 │                                          └─ END
 ├── coding   → coding_branch → END
 └── complex  → complex_branch → END
                 └─ on failure, fall back to general branch
```

- **Routing** is conditional on `intent` (`general` / `coding` / `complex`).
- The **general branch** is a tool-calling loop: the LLM may request
  `calculator`, `rag_search`, or `search_code`, which execute via the
  tool node, then the LLM is re-invoked until it produces a final answer.
  The tools actually used are recorded into state and surfaced in the
  response (`tools_used`, `sources`, `retrieved_context`).
- **Risk + approval**: every LLM tool call is classified low/medium/high
  (`guardrails/risk.py`). Medium/high requests pause the graph at
  `approval_gate`; the API keeps the pending state in-memory and resumes on
  the next request with `approved=true`.
- **Coding tools** (workspace-guarded): `write_file`, `edit_file`,
  `run_shell`, `run_tests`, and `search_code` live in `tools/coding` and
  `tools/general`. File writes are confined under `WORKSPACE_DIR`, `run_shell`
  only allows commands from `SHELL_ALLOWED_COMMANDS`, and subprocesses time out
  after `TOOL_SUBPROCESS_TIMEOUT` seconds. Write/exec tools are classified
  medium/high risk so they go through approval.
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
  each LLM call.

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
workspace-scoped write/edit, allowlisted shell, and `run_tests` tools
✅ **Memory / RAG** — ChromaDB store with recursive chunking, similarity
search, `jarvis-ingest` CLI for `.txt` / `.md`, plus periodic conversation
summarization
✅ **Document upload** — HTTP `POST /documents/upload`,
`GET /documents/count`, `POST /documents/ingest-folder`
✅ **Transparency** — responses carry `tools_used`, `sources`, and
`retrieved_context`; the Streamlit UI renders badges, tool lists, and
collapsible citation / debug sections
✅ **Background tasks** — `POST /tasks` / `GET /tasks/{id}` with a bounded
in-process executor; UI toggle to run long prompts in the background
✅ **Persistence** — sessions/messages/summaries/tasks via SQLAlchemy on
Postgres (Docker) or a local SQLite fallback
✅ **Guardrails** — input validation, PII output redaction, tool risk
classification + human-in-the-loop approval gate wired into the graph
✅ **Streamlit UI** — chat, model-config sidebar, selected-text follow-ups,
task offload, and Approve / Deny for risky tool actions
