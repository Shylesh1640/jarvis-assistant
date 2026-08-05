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

API endpoints: `GET /health`, `GET /models`, `POST /chat`.

## Run frontend

```bash
uv run streamlit run streamlit_app.py
```

The sidebar shows the currently configured local and cloud models. After an
assistant reply you can paste a snippet from it, then ask a follow-up
question framed around that selection. Tool actions flagged medium/high risk
surface an Approve / Deny prompt before execution.

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
safe to run repeatedly. An HTTP upload endpoint is not yet implemented.

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

## Project structure

```text
src/jarvis/
├── api/                # FastAPI app, routes/, schemas/
│   ├── main.py        # app + /health + /models
│   └── routes/chat.py # POST /chat (history, approval resume, selected_text)
├── orchestration/     # LangGraph state, router, branches, graph, approval gate
│   ├── graph.py       # compiled graph wiring all nodes together
│   ├── state.py       # JarvisState TypedDict
│   ├── router_node.py # intent classification (general/coding/complex)
│   ├── branches.py    # run_general/coding/complex branch nodes
│   ├── context_node.py, context_window.py  # RAG + sliding-window context
│   ├── model_selector.py # picks model per intent × complexity
│   └── approval_node.py # check_risk + approval_gate (human-in-the-loop)
├── models/            # ollama_client.py, openrouter_client.py (fallback chain)
├── tools/
│   ├── general/       # calculator, rag_search
│   └── coding/        # file_ops.read_file (write/edit/shell pending)
├── memory/            # ChromaDB: store.py (ingestion) + retrieve.py (similarity search)
├── guardrails/        # input_guard, output_guard (PII redaction), risk (tool risk classification)
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
  `calculator` or `rag_search`, which execute via `ToolNode`, then the LLM
  is re-invoked until it produces a final answer.
- **Risk + approval**: every LLM tool call is classified low/medium/high
  (`guardrails/risk.py`). Medium/high requests pause the graph at
  `approval_gate`; the API stores the pending state and resumes on the next
  request with `approved=true`.
- **Complex branch** tries the configured OpenRouter model chain in order;
  if all fail it transparently degrades to the local general branch.
- **Memory**: ChromaDB cosine collection seeded via the CLI; `rag_search`
  injects a formatted context block into the prompt.
- **Context window**: conversation history is truncated to
  `HISTORY_MAX_TURNS` and a `CONTEXT_TOKEN_BUDGET` word-count proxy before
  each LLM call.

## Current status

✅ **Phase 1–2 (foundation + backend skeleton)** — Complete
✅ **Phase 3 (orchestration graph)** — three branches (general / coding /
complex) with conditional routing, tool-calling loop, and complex → general fallback
✅ **Tools** — calculator, RAG search, `read_file` (all LangChain tools)
⏳ **Write/exec tools** — only `read_file` exists; write, edit, and shell
tools pending
✅ **Memory / RAG** — ChromaDB store with recursive chunking, similarity
search, and `jarvis-ingest` CLI for `.txt` / `.md` documents
⏳ **Document upload** — store + CLI exist; no HTTP upload endpoint yet
✅ **Guardrails** — input validation, PII output redaction, tool risk
classification + human-in-the-loop approval gate wired into the graph
✅ **Streamlit UI** — chat, model-config sidebar, selected-text follow-ups,
and Approve / Deny for risky tool actions
