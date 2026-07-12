"""Execution nodes for each routing branch."""
from jarvis.models.ollama_client import get_coding_model, get_general_model
from jarvis.models.openrouter_client import run_complex_with_fallback
from jarvis.orchestration.state import JarvisState


def run_general_branch(state: JarvisState) -> JarvisState:
    llm = get_general_model()
    prompt = state["user_input"]
    if state.get("retrieved_context"):
        prompt = f"Context:\n{state['retrieved_context']}\n\nQuestion:\n{prompt}"

    result = llm.invoke(prompt)
    state["selected_path"] = "general"
    state["final_response"] = result.content
    return state


def run_coding_branch(state: JarvisState) -> JarvisState:
    llm = get_coding_model()
    result = llm.invoke(state["user_input"])
    state["selected_path"] = "coding"
    state["final_response"] = result.content
    return state


def run_complex_branch(state: JarvisState) -> JarvisState:
    try:
        text, model_used = run_complex_with_fallback(
            [{"role": "user", "content": state["user_input"]}]
        )
        state["selected_path"] = "complex"
        state["selected_model"] = model_used
        state["final_response"] = text
    except Exception:  # noqa: BLE001
        # Fall back to local general model if all cloud models fail.
        state["fallback_count"] = state.get("fallback_count", 0) + 1
        return run_general_branch(state)

    return state
