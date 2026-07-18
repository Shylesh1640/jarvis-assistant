# Jarvis Assistant

Local-first hybrid AI assistant: FastAPI backend, LangGraph orchestration,
Ollama for local models, OpenRouter for complex-task fallback, and a Streamlit UI.

## Setup

```bash
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
├── tools/          # general/ (calculator, RAG search) and coding/ (file read) tools
├── memory/         # ChromaDB vector store, ingestion, retrieval
├── guardrails/     # input validation, output redaction, risk classification
└── config/         # settings loaded from .env
```

## Current status

✅ **Phase 1–2 (foundation + backend skeleton)** — Complete  
✅ **Phase 3 (orchestration graph)** — Three branches (general / coding / complex)
with conditional routing, tool calling loops, and fallback from complex → general  
✅ **Tools** — Calculator, RAG search, file reader (all implemented as LangChain tools)  
✅ **Memory/RAG** — ChromaDB vector store with text ingestion and similarity search  
✅ **Guardrails** — Input validation, PII output redaction, tool risk classification  
⏳ **Approval gates** — Nodes implemented but not yet wired into the graph  
⏳ **Write/exec tools** — Only `read_file` exists; write, edit, and shell tools pending  
⏳ **Document ingestion** — Store API exists but no upload endpoint or CLI
