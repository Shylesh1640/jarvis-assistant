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
    # UI toggles plumbed end-to-end through the graph state.
    show_reasoning: bool = False
    answer_style: str | None = None  # "concise" | "detailed" | "code"


class ChatResponse(BaseModel):
    session_id: str
    response: str
    path_used: str
    model_used: str | None = None
    approval_required: bool = False
    pending_action: str | None = None
    # Names of tools executed while producing this reply (e.g. ["calculator"]).
    tools_used: list[str] = []
    # Citations for RAG replies: [{"source": "docs/x.md", "chunk_id": "..."}].
    sources: list[dict] = []
    # Raw retrieved-context block, exposed for the UI debug view.
    retrieved_context: str | None = None


class TaskCreateRequest(BaseModel):
    description: str
    session_id: str | None = None


class TaskStatusResponse(BaseModel):
    id: str
    status: str
    description: str
    result: str | None = None
    error: str | None = None
