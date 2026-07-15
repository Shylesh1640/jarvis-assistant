"""Request/response schemas for the chat endpoint."""
from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str
    history: list[dict[str, str]] = []


class ChatResponse(BaseModel):
    session_id: str
    response: str
    path_used: str
    model_used: str | None = None
