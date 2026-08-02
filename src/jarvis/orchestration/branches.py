"""Execution nodes for each routing branch."""
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.config.settings import settings
from jarvis.models.ollama_client import get_model_named
from jarvis.models.openrouter_client import run_complex_with_fallback
from jarvis.orchestration.model_selector import select_model
from jarvis.orchestration.state import JarvisState
from jarvis.tools.coding.file_ops import read_file
from jarvis.tools.general.calculator import calculator
from jarvis.tools.general.rag_search import rag_search

logger = logging.getLogger(__name__)

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
        logger.info("Building initial messages for general branch")
        state["messages"] = _build_initial_messages(state)
    elif state.get("approved"):
        logger.info("Skipping LLM call — approval resume, tools pending")
        return state

    model_name = select_model(state, settings)
    state["selected_path"] = "general"
    state["selected_model"] = model_name
    state["selection_reason"] = f"general branch using {model_name}"

    llm = get_model_named(model_name, intent="general").bind_tools(_GENERAL_TOOLS)
    response = llm.invoke(state["messages"])
    state["messages"].append(response)

    if not response.tool_calls:
        state["final_response"] = response.content
        logger.info("General branch final response (no tool calls) using %s", model_name)
    else:
        logger.info(
            "General branch LLM (%s) requested %d tool call(s)",
            model_name, len(response.tool_calls),
        )
        tool_names = [tc.get("name", "?") for tc in response.tool_calls]
        logger.info("Tool calls: %s", tool_names)
    return state


# ---------------------------------------------------------------------------
# Coding branch
# ---------------------------------------------------------------------------

def run_coding_branch(state: JarvisState) -> JarvisState:
    logger.info("Coding branch selected")
    history = _format_history(state.get("history", []))
    prompt = state["user_input"]
    if history:
        prompt = f"Previous conversation:\n{history}\n\nCurrent request:\n{prompt}"

    model_name = select_model(state, settings)
    state["selected_path"] = "coding"
    state["selected_model"] = model_name
    state["selection_reason"] = f"coding branch using {model_name}"

    llm = get_model_named(model_name, intent="coding").bind_tools([read_file])
    response = llm.invoke(prompt)
    state["final_response"] = response.content
    logger.info("Coding branch complete using %s", model_name)
    return state


# ---------------------------------------------------------------------------
# Complex branch
# ---------------------------------------------------------------------------

def run_complex_branch(state: JarvisState) -> JarvisState:
    logger.info("Complex branch selected")
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

    primary = select_model(state, settings)
    state["selected_path"] = "complex"
    state["selected_model"] = primary
    state["selection_reason"] = "complex branch -> cloud chain"

    try:
        text, model_used = run_complex_with_fallback(messages)
        state["selected_model"] = model_used
        state["final_response"] = text
        logger.info("Complex branch completed with model: %s", model_used)
    except Exception:  # noqa: BLE001
        state["fallback_count"] = state.get("fallback_count", 0) + 1
        logger.warning("Complex branch failed, falling back to general")
        # Reset intent/complexity so select_model() picks a local general
        # model rather than re-selecting the cloud chain we just failed on.
        state["intent"] = "general"
        state["complexity"] = "easy"
        return run_general_branch(state)

    return state
