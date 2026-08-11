"""Shared state schema for the LangGraph orchestration graph."""
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import add_messages


class JarvisState(TypedDict, total=False):
    user_input: str
    session_id: str
    history: list[dict[str, str]]

    # Text the user highlighted in a previous assistant message and is now
    # asking a follow-up question about. Empty string means no selection.
    selected_text: str

    # UI toggles plumbed from the request schema.
    show_reasoning: bool
    answer_style: str  # "concise" | "detailed" | "code"

    messages: Annotated[list[Any], add_messages]

    intent: Literal["general", "coding", "complex"]
    complexity: Literal["easy", "medium", "difficult"]
    complexity_score: int  # word-count based raw score, useful for debug
    selected_path: str
    selected_model: str
    selection_reason: str  # human-readable explanation of model pick

    retrieved_context: str
    sources: list[dict[str, str]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    tool_errors: list[str]
    tools_used: list[str]
    # Number of LLM rounds in the current turn that requested tool calls.
    # Used to cap the tool loop (see MAX_TOOL_ITERATIONS).
    tool_call_count: int

    risk_level: Literal["low", "medium", "high"]
    approval_required: bool
    approved: bool

    pending_action: str | None
    # Structured view of the tool calls awaiting approval: [{name, args}].
    pending_tool_calls: list[dict[str, Any]]
    # Approval lifecycle fields (populated when approval_required=true).
    approval_id: str | None
    approval_expires_at: str | None

    fallback_count: int
    error_state: str | None
    final_response: str

    # When True, this turn runs as a background /tasks job. Branches use it
    # to record status instead of streaming a reply.
    as_background_task: bool
