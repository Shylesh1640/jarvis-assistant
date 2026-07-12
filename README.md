# Jarvis Assistant — Starter Project

Local-first hybrid AI assistant scaffold: FastAPI backend, LangGraph orchestration,
Ollama for local models, OpenRouter for complex-task fallback, and a Streamlit UI.

## Setup

```bash
uv init --no-workspace   # skip if pyproject.toml already exists
uv sync
```

Copy `.env.example` to `.env` and adjust model names to match `ollama list`.

## Run backend

```bash
uv run uvicorn jarvis.api.main:app --reload --app-dir src
```

## Run frontend

```bash
uv run streamlit run streamlit_app.py
```

## Run tests

```bash
uv run pytest
```

## Project structure

```text
src/jarvis/
├── api/            # FastAPI app, routes, schemas
├── orchestration/  # LangGraph state, router, branches, graph
├── models/         # Ollama + OpenRouter clients
├── tools/          # general/ and coding/ tool implementations (to be filled in)
├── memory/         # short-term/long-term memory + vector store (to be filled in)
├── guardrails/      # input/output validation, risk classification
└── config/         # settings loaded from .env
```

## Current status

This is Phase 1–3 of the task list: foundation, backend skeleton, and a working
routing graph with three branches (general / coding / complex) and a basic
fallback from complex → general when cloud models fail. Tools, memory/RAG,
and approval gates are stubbed and ready to be filled in next.
