"""Shared state schema for the LangGraph orchestration graph."""
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import add_messages


class JarvisState(TypedDict, total=False):
    user_input: str
    session_id: str
    history: list[dict[str, str]]

    messages: Annotated[list[Any], add_messages]

    intent: Literal["general", "coding", "complex"]
    selected_path: str
    selected_model: str

    retrieved_context: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]

    risk_level: Literal["low", "medium", "high"]
    approval_required: bool
    approved: bool

    pending_action: str | None

    fallback_count: int
    error_state: str | None
    final_response: str
