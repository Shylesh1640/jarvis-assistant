"""Chat route: entrypoint into the orchestration graph."""
from fastapi import APIRouter, HTTPException

from jarvis.api.schemas.chat import ChatRequest, ChatResponse
from jarvis.guardrails.input_guard import validate_input
from jarvis.guardrails.output_guard import redact_output
from jarvis.orchestration.graph import jarvis_graph

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    is_valid, error = validate_input(payload.message)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    result = jarvis_graph.invoke(
        {
            "user_input": payload.message,
            "session_id": payload.session_id,
            "history": [],
            "fallback_count": 0,
        }
    )

    safe_response = redact_output(result.get("final_response", ""))

    return ChatResponse(
        session_id=payload.session_id,
        response=safe_response,
        path_used=result.get("selected_path", "unknown"),
        model_used=result.get("selected_model"),
    )
