"""Chat route: entrypoint into the orchestration graph.

History / summaries / tasks live in the persistence layer (Postgres via
Docker, or a local SQLite file when no DSN is set). Pending-approval
state stays in-process (it carries LangChain message objects that aren't
trivially JSON-serialisable) so a server restart drops a waiting
approval — that's intentional and matches the existing contract.
"""
import logging

from fastapi import APIRouter, HTTPException

from jarvis.api.schemas.chat import ChatRequest, ChatResponse
from jarvis.guardrails.input_guard import validate_input
from jarvis.guardrails.output_guard import redact_output
from jarvis.memory.summaries import maybe_summarize
from jarvis.orchestration.branches import (
    OllamaModelLoadError,
    OllamaOutOfMemoryError,
    OllamaRequestTimeoutError,
    OllamaUnavailableError,
)
from jarvis.orchestration.graph import jarvis_graph
from jarvis.observability.trace import finish_trace, new_trace, trace_event

logger = logging.getLogger(__name__)

router = APIRouter()

import threading as _threading  # noqa: E402

from jarvis.persistence import create_all as _db_create_all  # noqa: E402
from jarvis.persistence import repos as _repos  # noqa: E402

# In-memory cache for the client-is-source-of-truth path (Streamlit sends
# its own history). The DB holds the durable record.
_sessions: dict[str, list[dict[str, str]]] = {}

# Holds full *live* LangGraph state for approval resume (transient).
_pending_approvals: dict[str, dict] = {}

_db_ready = False
_db_lock = _threading.Lock()


def _ensure_db() -> None:
    """Create tables on first use. Failures fall back to in-memory mode."""
    global _db_ready
    if _db_ready:
        return
    with _db_lock:
        if not _db_ready:
            try:
                _db_create_all()
                _db_ready = True
                logger.info("Persistence layer ready")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Persistence unavailable, using in-memory mode: %s", exc)
                _db_ready = False


def _persist_message(
    session_id: str,
    *,
    role: str,
    content: str,
    result: dict | None = None,
) -> None:
    """Append a turn to the durable store; no-op if DB is unavailable."""
    _ensure_db()
    if not _db_ready:
        return
    try:
        _repos.sessions.get_or_create(session_id)
        _repos.messages.add(
            session_id,
            role=role,
            content=content,
            path_used=result.get("selected_path") if result else None,
            model_used=result.get("selected_model") if result else None,
            tools_used=result.get("tools_used") if result else None,
            sources=result.get("sources") if result else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persisting message failed: %s", exc)


def _get_history(
    session_id: str, client_history: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Return the conversation history to use for this request.

    Prefers the client-supplied *client_history* (the source of truth for
    UI-driven clients such as Streamlit).  Falls back to the server-side
    session store when the client sends an empty list.
    """
    if client_history:
        _sessions[session_id] = list(client_history)
        return client_history
    return _sessions.get(session_id, [])


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    logger.info(
        "Chat request | session=%s | approved=%s | msg_len=%d",
        payload.session_id,
        payload.approved,
        len(payload.message),
    )
    tr = new_trace(session_id=payload.session_id, approved=payload.approved)

    # --- Approval resume ---
    if payload.approved:
        prev_state = _pending_approvals.pop(payload.session_id, None)
        if prev_state is None:
            trace_event(tr, "approval_resume_missing")
            finish_trace(tr)
            raise HTTPException(
                status_code=400, detail="No pending approval for this session"
            )
        trace_event(tr, "approval_resume")
        prev_state["approved"] = True
        result = jarvis_graph.invoke(prev_state)
        _update_history(payload.session_id, prev_state.get("user_input", ""), result)
        finish_trace(tr, result=result)
        return _build_response(payload.session_id, result)

    # --- Normal request ---
    is_valid, error = validate_input(payload.message)
    if not is_valid:
        trace_event(tr, f"input_rejected: {error}")
        finish_trace(tr)
        raise HTTPException(status_code=400, detail=error)

    # A fresh, non-approved message supersedes any lingering approval
    # waiting on this session — the user has moved on to a new question.
    if _pending_approvals.pop(payload.session_id, None) is not None:
        logger.info("Cleared stale pending approval for session %s", payload.session_id)
        trace_event(tr, "cleared_stale_approval")

    history = _get_history(payload.session_id, payload.history)

    selected_text = (payload.selected_text or "").strip()

    initial_state = {
        "user_input": payload.message,
        "session_id": payload.session_id,
        "history": history,
        "selected_text": selected_text,
        "fallback_count": 0,
        # UI toggles plumbed end-to-end through the graph state.
        "show_reasoning": payload.show_reasoning,
        "answer_style": payload.answer_style,
        "as_background_task": False,
    }

    if selected_text:
        logger.info(
            "Follow-up about selected text (%d chars) for session %s",
            len(selected_text),
            payload.session_id,
        )

    try:
        result = jarvis_graph.invoke(initial_state)
    except OllamaUnavailableError as exc:
        trace_event(tr, "error", category="ollama_unavailable")
        finish_trace(tr)
        logger.warning("Ollama unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="The local model server (Ollama) is not running. Start it with `ollama serve` and retry.",
        ) from exc
    except OllamaModelLoadError as exc:
        trace_event(tr, "error", category="model_not_found")
        finish_trace(tr)
        logger.warning("Model load failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="The configured model could not be loaded. Run `ollama list` to confirm it is available.",
        ) from exc
    except OllamaOutOfMemoryError as exc:
        trace_event(tr, "error", category="out_of_memory")
        finish_trace(tr)
        logger.warning("Out of memory: %s", exc)
        raise HTTPException(
            status_code=507,
            detail="The model + context does not fit in available VRAM/RAM. Lower OLLAMA_CONTEXT_LENGTH or reduce history.",
        ) from exc
    except OllamaRequestTimeoutError as exc:
        trace_event(tr, "error", category="request_timeout")
        finish_trace(tr)
        logger.warning("Request timeout: %s", exc)
        raise HTTPException(
            status_code=504,
            detail="The local model took too long to respond. Try a shorter prompt or run it as a background task.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        trace_event(tr, "error", category="unknown")
        finish_trace(tr)
        logger.exception("Graph invocation failed")
        raise HTTPException(
            status_code=502,
            detail=f"Local model request failed: {exc.__class__.__name__}. Check the server logs.",
        ) from exc
    trace_event(tr, "graph_completed")
    trace_event(
        tr,
        "selected",
        intent=result.get("intent"),
        complexity=result.get("complexity"),
        model=result.get("selected_model"),
        path=result.get("selected_path"),
    )
    trace_event(
        tr,
        "selected",
        intent=result.get("intent"),
        complexity=result.get("complexity"),
        model=result.get("selected_model"),
        path=result.get("selected_path"),
    )

    # --- If approval is needed, store state for resume ---
    if result.get("approval_required"):
        logger.info("Storing pending approval for session %s", payload.session_id)
        _pending_approvals[payload.session_id] = result
    else:
        _update_history(payload.session_id, payload.message, result)

    finish_trace(tr, result=result)
    return _build_response(payload.session_id, result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_history(session_id: str, user_message: str, result: dict) -> None:
    safe_response = redact_output(result.get("final_response", ""))
    session = _sessions.setdefault(session_id, [])
    session.append({"role": "user", "content": user_message})
    session.append({"role": "assistant", "content": safe_response})
    _persist_message(session_id, role="user", content=user_message)
    _persist_message(session_id, role="assistant", content=safe_response, result=result)
    try:
        maybe_summarize(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("maybe_summarize failed: %s", exc)


def _build_response(session_id: str, result: dict) -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        response=redact_output(result.get("final_response", "")),
        path_used=result.get("selected_path", "unknown"),
        model_used=result.get("selected_model"),
        approval_required=result.get("approval_required", False),
        pending_action=result.get("pending_action"),
        tools_used=list(result.get("tools_used", [])),
        sources=list(result.get("sources", [])),
        retrieved_context=result.get("retrieved_context", "") or None,
    )

