"""Request/response schemas for the chat endpoint."""
from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str = "default"
    session_token: str | None = None
    message: str = ""
    history: list[dict[str, str]] = []
    # Optional snippet the user highlighted in a previous assistant message.
    # When present, branches will frame the user's question as being about
    # this specific text.
    selected_text: str | None = None
    approved: bool = False
    # When true, cancels a pending approval for the session (marks the
    # durable row ``denied``) instead of resuming it. Backward-compatible:
    # existing clients never set it.
    deny: bool = False
    # UI toggles plumbed end-to-end through the graph state.
    show_reasoning: bool = False
    answer_style: str | None = None  # "concise" | "detailed" | "code"
    # Deep thinking / reasoning
    deep_thinking: bool = False
    reasoning_strategy: str | None = None  # "auto" | "cot" | "tot" | "self_consistency" | "reflexion" | "fast_and_slow"
    show_reasoning_chain: bool = False


class ChatResponse(BaseModel):
    session_id: str
    response: str
    path_used: str
    model_used: str | None = None
    approval_required: bool = False
    pending_action: str | None = None
    # Structured view of the exact tool calls awaiting approval:
    # [{"name": "write_file", "args": {"file_path": "..."}}].
    pending_tool_calls: list[dict] = []
    # Approval lifecycle: short id + ISO-8601 UTC expiry so the UI can render
    # a countdown and disable approval once the TTL has passed.
    approval_id: str | None = None
    approval_expires_at: str | None = None
    # Names of tools executed while producing this reply (e.g. ["calculator"]).
    tools_used: list[str] = []
    # Citations for RAG replies: [{"source": "docs/x.md", "chunk_id": "..."}].
    sources: list[dict] = []
    # Raw retrieved-context block, exposed for the UI debug view.
    retrieved_context: str | None = None
    fallback_used: bool = False
    warning: str | None = None
    # Performance metadata: wall-clock time spent producing this reply
    # (excluding any time the request spent waiting for approval), in seconds.
    elapsed_seconds: float | None = None
    # Phase 6 GPU policy metadata: how this reply actually executed.
    gpu_policy: str | None = None
    processor_split: str | None = None
    gpu_fallback_used: bool = False
    cpu_fallback_used: bool = False
    # Phase 6 cloud cost metadata: whether the cloud was used and the
    # estimated cost (USD) of the prompt for this reply.
    cloud_used: bool = False
    estimated_cost_usd: float | None = None
    runtime_warning: str | None = None
    # Phase 13 :: Deep thinking / reasoning metadata
    deep_thinking_used: bool = False
    reasoning_strategy: str | None = None
    reasoning_chain_visible: bool = False
    reasoning_steps: int = 0
    tokens_used_reasoning: int = 0
    tokens_used_answer: int = 0
    total_tokens: int = 0
    latency_ms_reasoning: int = 0
    latency_ms_answer: int = 0
    total_latency_ms: int = 0


class TaskCreateRequest(BaseModel):
    description: str
    session_id: str | None = None
    session_token: str | None = None


class TaskApprovalRequest(BaseModel):
    """Decision body for POST /tasks/{task_id}/approve|deny."""

    approved: bool = True


class TaskStatusResponse(BaseModel):
    id: str
    status: str
    description: str
    stage: str | None = None
    result: str | None = None
    error: str | None = None
    approval_id: str | None = None
    pending_action: str | None = None
    pending_tool_calls: list[dict] = []
    session_id: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None