"""Execution nodes for each routing branch."""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.models.ollama_client import get_coding_model, get_general_model
from jarvis.models.openrouter_client import run_complex_with_fallback
from jarvis.orchestration.state import JarvisState
from jarvis.tools.coding.file_ops import read_file
from jarvis.tools.general.calculator import calculator
from jarvis.tools.general.rag_search import rag_search

_GENERAL_TOOLS = [calculator, rag_search]


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _format_history(history: list[dict[str, str]]) -> str:
    """Turn the conversation log into a plain-text block."""
    if not history:
        return ""
    lines: list[str] = []
    for msg in history:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _build_history_messages(history: list[dict[str, str]]) -> list:
    """Convert the conversation log into LangChain message objects."""
    messages: list = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


# ---------------------------------------------------------------------------
# General branch  (tool-calling LLM)
# ---------------------------------------------------------------------------

def _build_initial_messages(state: JarvisState) -> list:
    """Create the message list that starts a general-branch conversation."""
    messages: list = []

    messages.extend(_build_history_messages(state.get("history", [])))

    if state.get("retrieved_context"):
        messages.append(
            SystemMessage(
                content=f"Here is context that may be relevant:\n{state['retrieved_context']}"
            )
        )

    messages.append(HumanMessage(content=state["user_input"]))
    return messages


def run_general_branch(state: JarvisState) -> JarvisState:
    if not state.get("messages"):
        state["messages"] = _build_initial_messages(state)

    llm = get_general_model().bind_tools(_GENERAL_TOOLS)
    response = llm.invoke(state["messages"])
    state["messages"].append(response)

    state["selected_path"] = "general"

    if not response.tool_calls:
        state["final_response"] = response.content
    return state


# ---------------------------------------------------------------------------
# Coding branch
# ---------------------------------------------------------------------------

def run_coding_branch(state: JarvisState) -> JarvisState:
    history = _format_history(state.get("history", []))
    prompt = state["user_input"]
    if history:
        prompt = f"Previous conversation:\n{history}\n\nCurrent request:\n{prompt}"

    llm = get_coding_model().bind_tools([read_file])
    response = llm.invoke(prompt)
    state["selected_path"] = "coding"
    state["final_response"] = response.content
    return state


# ---------------------------------------------------------------------------
# Complex branch
# ---------------------------------------------------------------------------

def run_complex_branch(state: JarvisState) -> JarvisState:
    history = _format_history(state.get("history", []))
    messages = [{"role": "user", "content": state["user_input"]}]
    if history:
        messages.insert(
            0,
            {
                "role": "user",
                "content": f"Previous conversation:\n{history}",
            },
        )

    try:
        text, model_used = run_complex_with_fallback(messages)
        state["selected_path"] = "complex"
        state["selected_model"] = model_used
        state["final_response"] = text
    except Exception:  # noqa: BLE001
        state["fallback_count"] = state.get("fallback_count", 0) + 1
        return run_general_branch(state)

    return state
