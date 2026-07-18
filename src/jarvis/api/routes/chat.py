"""Chat route: entrypoint into the orchestration graph."""
import logging

from fastapi import APIRouter, HTTPException

from jarvis.api.schemas.chat import ChatRequest, ChatResponse
from jarvis.guardrails.input_guard import validate_input
from jarvis.guardrails.output_guard import redact_output
from jarvis.orchestration.graph import jarvis_graph

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory session store  (short-term memory per session_id)
# ---------------------------------------------------------------------------
_sessions: dict[str, list[dict[str, str]]] = {}
# Holds full graph state for approval resume
_pending_approvals: dict[str, dict] = {}


def _get_history(session_id: str, client_history: list[dict[str, str]]) -> list[dict[str, str]]:
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
        payload.session_id, payload.approved, len(payload.message),
    )

    # --- Approval resume ---
    if payload.approved:
        prev_state = _pending_approvals.pop(payload.session_id, None)
        if prev_state is None:
            raise HTTPException(status_code=400, detail="No pending approval for this session")
        prev_state["approved"] = True
        result = jarvis_graph.invoke(prev_state)
        _update_history(payload.session_id, prev_state.get("user_input", ""), result)
        return _build_response(payload.session_id, result)

    # --- Normal request ---
    is_valid, error = validate_input(payload.message)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    history = _get_history(payload.session_id, payload.history)

    initial_state = {
        "user_input": payload.message,
        "session_id": payload.session_id,
        "history": history,
        "fallback_count": 0,
    }

    result = jarvis_graph.invoke(initial_state)

    # --- If approval is needed, store state for resume ---
    if result.get("approval_required"):
        logger.info("Storing pending approval for session %s", payload.session_id)
        _pending_approvals[payload.session_id] = result
    else:
        _update_history(payload.session_id, payload.message, result)

    return _build_response(payload.session_id, result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_history(session_id: str, user_message: str, result: dict) -> None:
    safe_response = redact_output(result.get("final_response", ""))
    session = _sessions.setdefault(session_id, [])
    session.append({"role": "user", "content": user_message})
    session.append({"role": "assistant", "content": safe_response})


def _build_response(session_id: str, result: dict) -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        response=redact_output(result.get("final_response", "")),
        path_used=result.get("selected_path", "unknown"),
        model_used=result.get("selected_model"),
        approval_required=result.get("approval_required", False),
        pending_action=result.get("pending_action"),
    )
