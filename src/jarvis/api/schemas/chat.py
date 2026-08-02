"""Request/response schemas for the chat endpoint."""
from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str = ""
    history: list[dict[str, str]] = []
    # Optional snippet the user highlighted in a previous assistant message.
    # When present, branches will frame the user's question as being about
    # this specific text.
    selected_text: str | None = None
    approved: bool = False


class ChatResponse(BaseModel):
    session_id: str
    response: str
    path_used: str
    model_used: str | None = None
    approval_required: bool = False
    pending_action: str | None = None
