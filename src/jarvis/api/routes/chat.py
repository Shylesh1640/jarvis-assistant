"""Chat route: entrypoint into the orchestration graph."""
from fastapi import APIRouter, HTTPException

from jarvis.api.schemas.chat import ChatRequest, ChatResponse
from jarvis.guardrails.input_guard import validate_input
from jarvis.guardrails.output_guard import redact_output
from jarvis.orchestration.graph import jarvis_graph

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory session store  (short-term memory per session_id)
# ---------------------------------------------------------------------------
_sessions: dict[str, list[dict[str, str]]] = {}


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
    is_valid, error = validate_input(payload.message)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    history = _get_history(payload.session_id, payload.history)

    result = jarvis_graph.invoke(
        {
            "user_input": payload.message,
            "session_id": payload.session_id,
            "history": history,
            "fallback_count": 0,
        }
    )

    safe_response = redact_output(result.get("final_response", ""))

    # Persist the completed exchange in the session store.
    session = _sessions.setdefault(payload.session_id, [])
    session.append({"role": "user", "content": payload.message})
    session.append({"role": "assistant", "content": safe_response})

    return ChatResponse(
        session_id=payload.session_id,
        response=safe_response,
        path_used=result.get("selected_path", "unknown"),
        model_used=result.get("selected_model"),
    )
