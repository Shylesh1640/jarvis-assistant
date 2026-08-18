"""Chat route: entrypoint into the orchestration graph.

History / summaries / tasks / approvals live in the persistence layer
(Postgres via Docker, or a local SQLite file when no DSN is set).

* **Approvals are durable**: when the graph pauses for permission the full
  paused state is written to the ``approvals`` table, so a backend restart
  between the approval request and the user's response no longer drops it.
  A bounded in-memory cache keeps the fast path (no DB read on resume).
* **Sessions are durable**: session rows carry a per-session token and are
  touched on every request; client-less history falls back to the DB so a
  restarted backend can rebuild a conversation.
* **Errors are structured**: Ollama failures map to a stable error code,
  message, ``retry_after_seconds`` and ``suggested_action``.
"""
import logging
import threading
import time

from fastapi import APIRouter, Request

from jarvis.api.errors import APIError
from jarvis.api.schemas.chat import ChatRequest, ChatResponse
from jarvis.guardrails.input_guard import validate_input
from jarvis.guardrails.output_guard import redact_output
from jarvis.memory.summaries import maybe_summarize, maybe_summarize_evicted
from jarvis.orchestration.approval_node import approval_is_expired
from jarvis.orchestration.branches import (
    OllamaModelLoadError,
    OllamaOutOfMemoryError,
    OllamaRequestTimeoutError,
    OllamaUnavailableError,
)
from jarvis.orchestration.graph import jarvis_graph
from jarvis.observability.trace import finish_trace, new_trace, trace_event
from jarvis.security.ratelimit import rate_limited
from jarvis.security.session_auth import ensure_session_context

logger = logging.getLogger(__name__)

router = APIRouter()

from jarvis.persistence import create_all as _db_create_all  # noqa: E402
from jarvis.persistence import repos as _repos  # noqa: E402
from jarvis.persistence.state_codec import state_from_json, state_to_json  # noqa: E402

# In-memory cache for the client-is-source-of-truth path (Streamlit sends
# its own history). The DB holds the durable record.
_sessions: dict[str, list[dict[str, str]]] = {}

# Holds full *live* LangGraph state for approval resume (transient fast path).
# The durable copy lives in the ``approvals`` table and survives restarts.
_pending_approvals: dict[str, dict] = {}

_db_ready = False
_db_lock = threading.Lock()


def _thread_id(session_id: str) -> str:
    """Stable LangGraph thread id per session so the checkpointer can group runs.

    Sessions can be supplied by clients (Streamlit generates a uuid4), so we
    namespace them to avoid accidental collisions with other apps sharing the
    same in-memory checkpointer.
    """
    return f"jarvis-session:{session_id}"


def _ensure_db() -> None:
    """Create tables on first use. Failures fall back to in-memory mode."""
    global _db_ready
    if _db_ready:
        return
    with _db_lock:
        if not _db_ready:
            try:
                _db_create_all()
                _purge_expired_approvals()
                _db_ready = True
                logger.info("Persistence layer ready")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Persistence unavailable, using in-memory mode: %s", exc)
                _db_ready = False


def _purge_expired_approvals() -> None:
    try:
        n = _repos.approvals.purge_expired()
        if n:
            logger.info("Purged %d expired approval(s)", n)
    except Exception as exc:  # noqa: BLE001
        logger.debug("approval purge failed: %s", exc)


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
    UI-driven clients such as Streamlit).  Falls back to the in-memory
    session cache, then to the durable message store so a conversation can
    be rebuilt after a backend restart.
    """
    if client_history:
        _sessions[session_id] = list(client_history)
        return client_history
    cached = _sessions.get(session_id)
    if cached is not None:
        return cached
    if _db_ready:
        try:
            db_history = _repos.messages.history(session_id)
            if db_history:
                cached = [
                    {"role": m["role"], "content": m["content"]}
                    for m in db_history
                    if m.get("role") in ("user", "assistant")
                ]
                _sessions[session_id] = cached
                return cached
        except Exception as exc:  # noqa: BLE001
            logger.warning("Loading DB history failed: %s", exc)
    return []


def _persist_approval(session_id: str, result: dict) -> None:
    """Write a pending approval to the durable store (best-effort)."""
    if not _db_ready:
        return
    approval_id = result.get("approval_id")
    if not approval_id:
        return
    tool_calls = list(result.get("pending_tool_calls", []) or [])
    try:
        _repos.approvals.create(
            approval_id,
            session_id=session_id,
            state=state_to_json(result),
            expires_at=result.get("approval_expires_at") or _default_expiry(),
            tool_name=tool_calls[0].get("name") if tool_calls else None,
            arguments=tool_calls[0].get("args") if tool_calls else None,
            tool_calls=tool_calls,
            risk_level=result.get("risk_level", "medium"),
            pending_action=result.get("pending_action"),
        )
        logger.info("Persisted pending approval %s for session %s", approval_id, session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persisting approval failed for session %s: %s", session_id, exc)


def _load_pending_approval(session_id: str) -> dict | None:
    """Load a pending approval from the durable store (restart recovery).

    Also surfaces a row a TTL sweep already flipped to ``expired`` so the
    resume can report ``approval_expired`` (410) instead of the generic
    "no pending approval" (400).
    """
    if not _db_ready:
        return None
    try:
        row = _repos.approvals.get_pending(session_id)
        if row is None:
            row = _repos.approvals.get_expired(session_id)
        if row is None:
            return None
        state = state_from_json(row.state)
        state["_approval_status_row"] = row.status
        return state
    except Exception as exc:  # noqa: BLE001
        logger.warning("Loading pending approval failed for %s: %s", session_id, exc)
        return None


def _default_expiry() -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()


def _invoke_graph(state: dict, thread_id: str) -> dict:
    """Run the graph and stamp wall-clock timing onto the result.

    ``result["elapsed_seconds"]`` measures the time to produce this reply
    (model + tool rounds). It is exposed on ChatResponse so the UI can
    surface latency without parsing logs.
    """
    started = time.perf_counter()
    result = jarvis_graph.invoke(
        state,
        config={"configurable": {"thread_id": thread_id}},
    )
    result = dict(result)
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return result


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    logger.info(
        "Chat request | session=%s | approved=%s | msg_len=%d",
        payload.session_id,
        payload.approved,
        len(payload.message),
    )
    _ensure_db()
    rate_limited(request, payload.session_id)
    ensure_session_context(payload.session_id, payload.session_token)

    tr = new_trace(session_id=payload.session_id, approved=payload.approved)

    # --- Approval deny ---
    if payload.deny:
        # Cancel any pending approval for this session — both the in-memory
        # fast path and the durable row — and mark it ``denied`` so a later
        # "approved" resume can never fire it.
        prev_state = _pending_approvals.pop(payload.session_id, None)
        if prev_state is None:
            prev_state = _load_pending_approval(payload.session_id)
        if prev_state is None:
            trace_event(tr, "approval_deny_missing")
            finish_trace(tr)
            raise APIError(
                400, "no_pending_approval", "No pending approval for this session."
            )
        approval_id = prev_state.get("approval_id")
        if approval_id:
            try:
                _repos.approvals.set_status(approval_id, "denied")
            except Exception as exc:  # noqa: BLE001
                logger.debug("marking denied approval failed: %s", exc)
        trace_event(tr, "approval_denied")
        finish_trace(tr)
        return ChatResponse(
            session_id=payload.session_id,
            response="Action cancelled by user.",
            path_used="approval",
            approval_required=False,
        )

    # --- Approval resume ---
    if payload.approved:
        prev_state = _pending_approvals.pop(payload.session_id, None)
        if prev_state is None:
            prev_state = _load_pending_approval(payload.session_id)
        if prev_state is None:
            trace_event(tr, "approval_resume_missing")
            finish_trace(tr)
            raise APIError(
                400, "no_pending_approval", "No pending approval for this session."
            )
        if approval_is_expired(prev_state):
            trace_event(tr, "approval_resume_expired")
            finish_trace(tr)
            raise APIError(
                410,
                "approval_expired",
                "This approval has expired. Ask again to restart the action.",
                suggested_action="Re-ask Jarvis to restart the action.",
            )
        trace_event(tr, "approval_resume")
        prev_state["approved"] = True
        try:
            result = _invoke_graph(
                prev_state, _thread_id(payload.session_id)
            )
        except Exception as exc:  # noqa: BLE001
            raise _error_from_exception(exc, tr) from exc
        _mark_approval_resolved(payload.session_id, prev_state)
        _update_history(payload.session_id, prev_state.get("user_input", ""), result)
        finish_trace(tr, result=result)
        return _build_response(payload.session_id, result)

    # --- Normal request ---
    is_valid, error = validate_input(payload.message)
    if not is_valid:
        trace_event(tr, f"input_rejected: {error}")
        finish_trace(tr)
        raise APIError(400, "invalid_input", error or "Invalid input.")

    # A fresh, non-approved message supersedes any lingering approval
    # waiting on this session — the user has moved on to a new question.
    if _pending_approvals.pop(payload.session_id, None) is not None:
        logger.info("Cleared stale pending approval for session %s", payload.session_id)
        trace_event(tr, "cleared_stale_approval")
    # Durable pending approvals for this session are superseded too
    # (they may survive only in the DB after a restart).
    _cancel_durable_approval(payload.session_id)

    _purge_expired_approvals()
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
        result = _invoke_graph(initial_state, _thread_id(payload.session_id))
    except Exception as exc:  # noqa: BLE001
        raise _error_from_exception(exc, tr) from exc

    trace_event(tr, "graph_completed")
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
        _persist_approval(payload.session_id, result)
    else:
        _update_history(payload.session_id, payload.message, result)

    finish_trace(tr, result=result)
    return _build_response(payload.session_id, result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mark_approval_resolved(session_id: str, prev_state: dict) -> None:
    """Finalise the durable approval row after a successful resume."""
    if not _db_ready:
        return
    approval_id = prev_state.get("approval_id")
    if not approval_id:
        return
    try:
        _repos.approvals.set_status(approval_id, "approved")
    except Exception as exc:  # noqa: BLE001
        logger.debug("marking approval resolved failed: %s", exc)


def _cancel_durable_approval(session_id: str) -> None:
    if not _db_ready:
        return
    try:
        _repos.approvals.cancel_all_for_session(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cancelling durable approval failed: %s", exc)


def _error_from_exception(exc: Exception, tr) -> APIError:
    """Map a typed Ollama/graph error to a structured API error."""
    if isinstance(exc, OllamaUnavailableError):
        trace_event(tr, "error", category="ollama_unavailable")
        finish_trace(tr)
        logger.warning("Ollama unavailable: %s", exc)
        return APIError(
            503,
            "ollama_unavailable",
            "Ollama is not reachable. Check if the Ollama service is running.",
            retry_after_seconds=10,
            suggested_action="Start Ollama (`ollama serve`) and retry, or run as a background task.",
        )
    if isinstance(exc, OllamaModelLoadError):
        trace_event(tr, "error", category="model_not_found")
        finish_trace(tr)
        logger.warning("Model load failed: %s", exc)
        return APIError(
            502,
            "model_not_found",
            "The configured model could not be loaded.",
            suggested_action="Run `ollama list` to confirm the model is available.",
        )
    if isinstance(exc, OllamaOutOfMemoryError):
        trace_event(tr, "error", category="out_of_memory")
        finish_trace(tr)
        logger.warning("Out of memory: %s", exc)
        return APIError(
            507,
            "out_of_memory",
            "The model + context does not fit in available VRAM/RAM.",
            suggested_action="Lower OLLAMA_CONTEXT_LENGTH, reduce history, or disable GPU fallback.",
        )
    if isinstance(exc, OllamaRequestTimeoutError):
        trace_event(tr, "error", category="request_timeout")
        finish_trace(tr)
        logger.warning("Request timeout: %s", exc)
        return APIError(
            504,
            "request_timeout",
            "The local model took too long to respond.",
            retry_after_seconds=10,
            suggested_action="Try a shorter prompt or run it as a background task.",
        )
    trace_event(tr, "error", category="unknown")
    finish_trace(tr)
    logger.exception("Graph invocation failed")
    return APIError(
        502,
        "model_request_failed",
        f"Local model request failed: {exc.__class__.__name__}.",
        suggested_action="Check the backend logs and retry.",
    )


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
    try:
        maybe_summarize_evicted(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("maybe_summarize_evicted failed: %s", exc)


def _build_response(session_id: str, result: dict) -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        response=redact_output(result.get("final_response", "")),
        path_used=result.get("selected_path", "unknown"),
        model_used=result.get("selected_model"),
        approval_required=result.get("approval_required", False),
        pending_action=result.get("pending_action"),
        pending_tool_calls=list(result.get("pending_tool_calls", [])),
        approval_id=result.get("approval_id"),
        approval_expires_at=result.get("approval_expires_at"),
        tools_used=list(result.get("tools_used", [])),
        sources=list(result.get("sources", [])),
        retrieved_context=result.get("retrieved_context", "") or None,
        fallback_used=bool(result.get("fallback_used")),
        warning=result.get("warning"),
        elapsed_seconds=result.get("elapsed_seconds"),
    )
