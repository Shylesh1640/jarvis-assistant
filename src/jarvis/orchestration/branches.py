"""Execution nodes for each routing branch."""
import logging
import time

from jarvis.config.settings import settings
from jarvis.models.ollama_client import get_model_named
from jarvis.models.openrouter_client import run_complex_with_fallback
from jarvis.orchestration.context_window import (
    build_final_chat_dicts,
    build_final_messages,
    build_final_prompt,
)
from jarvis.orchestration.model_selector import select_model
from jarvis.orchestration.state import JarvisState
from jarvis.tools.coding.file_ops import read_file
from jarvis.tools.general.calculator import calculator
from jarvis.tools.general.rag_search import rag_search

logger = logging.getLogger(__name__)

_GENERAL_TOOLS = [calculator, rag_search]


# ---------------------------------------------------------------------------
# Exception classification (so the API can return friendly categories)
# ---------------------------------------------------------------------------


class OllamaUnavailableError(RuntimeError):
    """Ollama server unreachable / refused connection."""


class OllamaModelLoadError(RuntimeError):
    """Model failed to load (missing, OOM during load, etc.)."""


class OllamaRequestTimeoutError(RuntimeError):
    """Local generation exceeded the configured request timeout."""


class OllamaOutOfMemoryError(RuntimeError):
    """Model + context doesn't fit in available VRAM/RAM."""


def _classify_ollama_error(exc: Exception) -> tuple[str, type]:
    """Map a low-level exception to a friendly category + re-raised type."""
    msg = str(exc).lower()
    if any(k in msg for k in ("connection", "refused", "unreachable", "max retries", "axioserror")):
        return "ollama_unavailable", OllamaUnavailableError
    if any(k in msg for k in ("model not found", "model '", "not found", "does not exist")):
        return "model_not_found", OllamaModelLoadError
    if any(k in msg for k in ("timeout", "timed out", "deadline exceeded")):
        return "request_timeout", OllamaRequestTimeoutError
    if any(k in msg for k in ("oom", "out of memory", "memory", "cuda", "blastohm", "llm.load_tensors")):
        return "out_of_memory", OllamaOutOfMemoryError
    return "unknown_error", RuntimeError


def _structured_request_log(
    *,
    branch: str,
    model_name: str,
    intent: str,
    complexity: str,
    messages: list,
    duration_ms: float,
    num_ctx: int,
    num_batch: int,
    fallback: bool = False,
    error_category: str | None = None,
) -> None:
    """Emit one structured INFO line per local model request.

    Never logs API keys, full prompts, or private documents. Only logs the
    estimated context size (token count) so operators can correlate
    performance with context pressure.
    """
    from jarvis.orchestration.context_window import estimate_tokens

    est_ctx = sum(estimate_tokens(getattr(m, "content", "") if hasattr(m, "content") else str(m)) for m in (messages or []))
    logger.info(
        "model_request | branch=%s model=%s intent=%s complexity=%s est_ctx_tokens=%d "
        "num_ctx=%d num_batch=%d duration_ms=%.0f partial_offload=unknown fallback=%s error=%s",
        branch, model_name, intent, complexity, est_ctx,
        num_ctx, num_batch, duration_ms,
        fallback, error_category or "none",
    )


# ---------------------------------------------------------------------------
# General branch  (tool-calling LLM with sliding-window context)
# ---------------------------------------------------------------------------

def run_general_branch(state: JarvisState) -> JarvisState:
    if not state.get("messages"):
        logger.info("Building final messages for general branch (with context window)")
        state["messages"] = build_final_messages(state, settings)
    elif state.get("approved"):
        logger.info("Skipping LLM call — approval resume, tools pending")
        return state

    model_name = select_model(state, settings)
    state["selected_path"] = "general"
    state["selected_model"] = model_name
    state["selection_reason"] = f"general branch using {model_name}"

    llm = get_model_named(model_name, intent="general").bind_tools(_GENERAL_TOOLS)
    started = time.monotonic()
    try:
        response = llm.invoke(state["messages"])
    except Exception as exc:  # noqa: BLE001 — classify + re-raise a typed error
        category, err_type = _classify_ollama_error(exc)
        _structured_request_log(
            branch="general", model_name=model_name,
            intent=state.get("intent", "general"), complexity=state.get("complexity", "easy"),
            messages=state["messages"], duration_ms=(time.monotonic() - started) * 1000,
            num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
            error_category=category,
        )
        raise err_type(f"{category}: {exc}") from exc
    duration_ms = (time.monotonic() - started) * 1000
    _structured_request_log(
        branch="general", model_name=model_name,
        intent=state.get("intent", "general"), complexity=state.get("complexity", "easy"),
        messages=state["messages"], duration_ms=duration_ms,
        num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
    )
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
    prompt = build_final_prompt(state, settings)

    model_name = select_model(state, settings)
    state["selected_path"] = "coding"
    state["selected_model"] = model_name
    state["selection_reason"] = f"coding branch using {model_name}"

    llm = get_model_named(model_name, intent="coding").bind_tools([read_file])
    started = time.monotonic()
    try:
        response = llm.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        category, err_type = _classify_ollama_error(exc)
        _structured_request_log(
            branch="coding", model_name=model_name,
            intent=state.get("intent", "coding"), complexity=state.get("complexity", "easy"),
            messages=[], duration_ms=(time.monotonic() - started) * 1000,
            num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
            error_category=category,
        )
        raise err_type(f"{category}: {exc}") from exc
    duration_ms = (time.monotonic() - started) * 1000
    _structured_request_log(
        branch="coding", model_name=model_name,
        intent=state.get("intent", "coding"), complexity=state.get("complexity", "easy"),
        messages=[], duration_ms=duration_ms,
        num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
    )
    state["final_response"] = response.content
    logger.info("Coding branch complete using %s", model_name)
    return state


# ---------------------------------------------------------------------------
# Complex branch
# ---------------------------------------------------------------------------

def run_complex_branch(state: JarvisState) -> JarvisState:
    logger.info("Complex branch selected")
    messages = build_final_chat_dicts(state, settings)

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
        _structured_request_log(
            branch="complex", model_name=primary,
            intent=state.get("intent", "complex"), complexity=state.get("complexity", "difficult"),
            messages=messages, duration_ms=0.0,
            num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
            fallback=True, error_category="cloud_fallback",
        )
        # Reset intent/complexity so select_model() picks a local general
        # model rather than re-selecting the cloud chain we just failed on.
        # We also drop any prior messages so build_final_messages runs again
        # with the corrected intent and a fresh windowed context.
        state["intent"] = "general"
        state["complexity"] = "easy"
        state["messages"] = []
        return run_general_branch(state)

    return state
