"""Execution nodes for each routing branch."""
import logging
import time

from langchain_core.messages import ToolMessage

from jarvis.config.settings import settings
from jarvis.models.cost_guard import estimate_prompt_cost_usd
from jarvis.models.gpu_policy import GPUPlan, GPURequiredError, decide_execution_plan
from jarvis.models.ollama_client import get_model_named
from jarvis.models.openrouter_client import run_complex_with_fallback
from jarvis.orchestration.context_window import (
    build_final_chat_dicts,
    build_final_messages,
)
from jarvis.orchestration.model_selector import select_model
from jarvis.orchestration.state import JarvisState
from jarvis.tools.registry import CODING_BOUND_TOOLS, GENERAL_BOUND_TOOLS

logger = logging.getLogger(__name__)


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
    attempt: int | None = None,
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
        "num_ctx=%d num_batch=%d duration_ms=%.0f partial_offload=unknown fallback=%s "
        "attempt=%s error=%s",
        branch, model_name, intent, complexity, est_ctx,
        num_ctx, num_batch, duration_ms,
        fallback, attempt if attempt is not None else "-", error_category or "none",
    )


def _invoke_branch_llm(
    state: JarvisState,
    *,
    branch: str,
    model_name: str,
    llm,
    messages: list,
    bound_tools: list,
) -> object:
    """Invoke *llm* with bounded retry + graceful GPU→CPU fallback.

    * Transient failures (server unreachable, request timeout) are retried
      up to ``settings.retry_max_attempts`` with linear backoff.
    * An out-of-memory error under full GPU offload retries once on CPU
      (``num_gpu=0``) when ``settings.gpu_fallback_to_cpu`` is enabled, and
      marks ``state["fallback_used"]`` / ``state["warning"]`` so the UI can
      tell the user generation is running on CPU.
    * Permanent errors (missing model, non-retryable) raise immediately as
      their typed error.
    """
    attempts = max(1, settings.retry_max_attempts)
    backoff = max(0.0, settings.retry_backoff_seconds)
    started = time.monotonic()

    for attempt in range(1, attempts + 1):
        try:
            response = llm.invoke(messages)
            _structured_request_log(
                branch=branch, model_name=model_name,
                intent=state.get("intent", branch),
                complexity=state.get("complexity", "easy"),
                messages=messages, duration_ms=(time.monotonic() - started) * 1000,
                num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
                attempt=attempt,
            )
            return response
        except Exception as exc:  # noqa: BLE001 — classify + re-raise a typed error
            category, err_type = _classify_ollama_error(exc)
            retryable = err_type in (OllamaUnavailableError, OllamaRequestTimeoutError)
            _structured_request_log(
                branch=branch, model_name=model_name,
                intent=state.get("intent", branch),
                complexity=state.get("complexity", "easy"),
                messages=messages, duration_ms=(time.monotonic() - started) * 1000,
                num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
                error_category=category, attempt=attempt,
            )
            if retryable and attempt < attempts:
                delay = backoff * attempt
                logger.warning(
                    "%s branch attempt %d/%d failed (%s) — retrying in %.1fs",
                    branch, attempt, attempts, category, delay,
                )
                time.sleep(delay)
                continue

            # Graceful GPU -> CPU degradation for OOM under full GPU offload.
            if (
                err_type is OllamaOutOfMemoryError
                and settings.gpu_fallback_to_cpu
                and not getattr(llm, "_force_cpu", False)
            ):
                logger.warning("%s branch OOM — retrying once on CPU", branch)
                cpu_llm = get_model_named(model_name, intent=branch, force_cpu=True)
                cpu_llm._force_cpu = True
                cpu_bound = cpu_llm.bind_tools(bound_tools)
                try:
                    response = cpu_bound.invoke(messages)
                    state["fallback_used"] = "gpu_to_cpu"
                    state["warning"] = (
                        "GPU offload failed (model + context too large for VRAM) — "
                        "fell back to CPU. Expect slower generation."
                    )
                    _structured_request_log(
                        branch=branch, model_name=model_name,
                        intent=state.get("intent", branch),
                        complexity=state.get("complexity", "easy"),
                        messages=messages, duration_ms=(time.monotonic() - started) * 1000,
                        num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
                        fallback=True,
                    )
                    return response
                except Exception as exc2:  # noqa: BLE001
                    category2, err_type2 = _classify_ollama_error(exc2)
                    _structured_request_log(
                        branch=branch, model_name=model_name,
                        intent=state.get("intent", branch),
                        complexity=state.get("complexity", "easy"),
                        messages=messages, duration_ms=(time.monotonic() - started) * 1000,
                        num_ctx=settings.ollama_context_length, num_batch=settings.ollama_num_batch,
                        error_category=category2, fallback=True,
                    )
                    raise err_type2(f"{category2}: {exc2}") from exc2

            raise err_type(f"{category}: {exc}") from exc
    raise RuntimeError("unreachable: retry loop exhausted")  # pragma: no cover


# ---------------------------------------------------------------------------
# General branch  (tool-calling LLM with sliding-window context)
# ---------------------------------------------------------------------------

def _apply_gpu_plan(
    state: JarvisState,
    model_name: str,
    *,
    is_strong_model: bool,
    context_length: int,
) -> tuple[str, GPUPlan]:
    """Decide how *model_name* runs on the GPU and record the decision.

    Returns (effective_model, plan). The effective model differs from
    *model_name* only when the strong local model cannot run on GPU and the
    policy routes to a configured fallback. Raises ``GPURequiredError`` when
    ``GPU_POLICY=require_gpu`` cannot be satisfied (never silent CPU).
    """
    cfg = settings
    if not cfg.gpu_runtime_check_enabled:
        plan = decide_execution_plan(
            model_name,
            is_strong_model=is_strong_model,
            context_length=context_length,
            gpu_info=None,
        )
    else:
        need_probe = cfg.gpu_policy == "require_gpu" or (
            cfg.gpu_policy == "prefer_gpu" and is_strong_model
        )
        gpu_info = None
        if need_probe:
            try:
                from jarvis.models.runtime_diagnostics import get_gpu_info

                gpu_info, _warnings = get_gpu_info()
            except Exception:  # noqa: BLE001
                gpu_info = None
        plan = decide_execution_plan(
            model_name,
            is_strong_model=is_strong_model,
            context_length=context_length,
            gpu_info=gpu_info,
        )

    if plan.blocked:
        raise GPURequiredError(
            plan.blocked_reason or "GPU execution required but unavailable.",
            plan.suggested_action,
        )

    state["gpu_policy"] = plan.gpu_policy
    state["processor_split"] = plan.processor_split
    state["gpu_fallback_used"] = plan.gpu_fallback_used
    state["cpu_fallback_used"] = plan.cpu_fallback_used
    if plan.runtime_warning and not state.get("runtime_warning"):
        state["runtime_warning"] = plan.runtime_warning
    if plan.runtime_warning and not state.get("warning"):
        state["warning"] = plan.runtime_warning

    if plan.fallback_model:
        logger.warning(
            "GPU policy routed %s -> fallback %s (%s)",
            model_name, plan.fallback_model, plan.processor_split,
        )
        state["selected_model"] = plan.fallback_model
        state["selection_reason"] = (
            f"GPU policy: {model_name} cannot run on GPU; using {plan.fallback_model}"
        )
        return plan.fallback_model, plan
    return model_name, plan


def _tool_loop_capped(state: JarvisState) -> bool:
    """Return True (and set a final response) when the tool loop is at its cap.

    Called at the top of a branch after the ToolNode has added a ToolMessage:
    instead of invoking the LLM yet again we stop and report the cap so the
    graph can reach END safely.
    """
    max_iterations = max(1, getattr(settings, "max_tool_iterations", 5))
    if state.get("tool_call_count", 0) >= max_iterations:
        messages = state.get("messages", [])
        if messages and isinstance(messages[-1], ToolMessage):
            state["final_response"] = (
                f"I stopped after reaching the maximum of {max_iterations} tool "
                f"iterations. Ask me to continue if you'd like me to keep going."
            )
            logger.warning("Tool loop reached cap (%s iterations)", max_iterations)
            return True
    return False


def run_general_branch(state: JarvisState) -> JarvisState:
    if not state.get("messages"):
        logger.info("Building final messages for general branch (with context window)")
        state["messages"] = build_final_messages(state, settings)
    elif state.get("approved"):
        logger.info("Skipping LLM call — approval resume, tools pending")
        return state

    if _tool_loop_capped(state):
        return state

    model_name = select_model(state, settings)
    state["selected_path"] = "general"
    state["selected_model"] = model_name
    state["selection_reason"] = f"general branch using {model_name}"

    model_name, plan = _apply_gpu_plan(
        state,
        model_name,
        is_strong_model=(model_name == settings.strong_local_model),
        context_length=settings.ollama_context_length,
    )

    llm = get_model_named(model_name, intent="general", num_gpu=plan.num_gpu).bind_tools(GENERAL_BOUND_TOOLS)
    response = _invoke_branch_llm(
        state,
        branch="general",
        model_name=model_name,
        llm=llm,
        messages=state["messages"],
        bound_tools=GENERAL_BOUND_TOOLS,
    )
    state["messages"].append(response)

    if not response.tool_calls:
        state["final_response"] = response.content
        logger.info("General branch final response (no tool calls) using %s", model_name)
    else:
        state["tool_call_count"] = state.get("tool_call_count", 0) + 1
        logger.info(
            "General branch LLM (%s) requested %d tool call(s)",
            model_name, len(response.tool_calls),
        )
        tool_names = [tc.get("name", "?") for tc in response.tool_calls]
        logger.info("Tool calls: %s", tool_names)
    return state


# ---------------------------------------------------------------------------
# Coding branch (full tool loop)
# ---------------------------------------------------------------------------

def run_coding_branch(state: JarvisState) -> JarvisState:
    """Coding branch with a tool loop.

    Flow: build messages → coding_llm → (tool call → ToolNode → coding_llm)
    → final answer, using the coding model already chosen by
    ``select_model`` — the configured coding model is never changed.
    """
    if not state.get("messages"):
        logger.info("Building final messages for coding branch (with context window)")
        state["messages"] = build_final_messages(state, settings)
    elif state.get("approved"):
        logger.info("Skipping LLM call — approval resume, tools pending")
        return state

    if _tool_loop_capped(state):
        return state

    model_name = select_model(state, settings)
    state["selected_path"] = "coding"
    state["selected_model"] = model_name
    state["selection_reason"] = f"coding branch using {model_name}"

    model_name, plan = _apply_gpu_plan(
        state,
        model_name,
        is_strong_model=(model_name == settings.strong_local_model),
        context_length=settings.ollama_context_length,
    )

    llm = get_model_named(model_name, intent="coding", num_gpu=plan.num_gpu).bind_tools(CODING_BOUND_TOOLS)
    response = _invoke_branch_llm(
        state,
        branch="coding",
        model_name=model_name,
        llm=llm,
        messages=state["messages"],
        bound_tools=CODING_BOUND_TOOLS,
    )
    state["messages"].append(response)

    if not response.tool_calls:
        state["final_response"] = response.content
        logger.info("Coding branch final response (no tool calls) using %s", model_name)
    else:
        state["tool_call_count"] = state.get("tool_call_count", 0) + 1
        logger.info(
            "Coding branch LLM (%s) requested %d tool call(s)",
            model_name, len(response.tool_calls),
        )
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

    # Phase 6 :: cloud-cost approval gate. When CLOUD_REQUIRE_COST_APPROVAL is
    # on (and the cloud is actually configured), the branch pauses for
    # explicit permission before spending. On resume (approved=True) the gate
    # is skipped and the call proceeds.
    if _cloud_approval_needed(state, messages, primary):
        _stamp_cloud_approval(state, messages, primary)
        logger.warning(
            "Cloud-cost approval required for %s on session %s",
            primary, state.get("session_id", "?"),
        )
        return state

    try:
        text, model_used = run_complex_with_fallback(
            messages, session_id=state.get("session_id")
        )
        state["selected_model"] = model_used
        state["final_response"] = text
        state["cloud_used"] = True
        state["estimated_cost_usd"] = (
            estimate_prompt_cost_usd(model_used, messages)
        )
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


def _cloud_approval_needed(state: JarvisState, messages: list, model: str) -> bool:
    """True when the cloud call must pause for explicit cost approval."""
    if state.get("approved"):
        return False
    if not settings.cloud_require_cost_approval:
        return False
    if not settings.cloud_cost_tracking_enabled:
        return False
    if not settings.openrouter_api_key or not settings.complex_models:
        return False
    return True


def _stamp_cloud_approval(state: JarvisState, messages: list, model: str) -> None:
    """Set the approval fields so the API layer pauses and can resume."""
    import uuid
    from datetime import datetime, timedelta, timezone

    est = estimate_prompt_cost_usd(model, messages)
    state["approval_required"] = True
    state["pending_action"] = (
        f"cloud_call: {model} (est. ${est:.4f})"
    )
    state["pending_tool_calls"] = [
        {"name": "cloud_call", "args": {"model": model, "estimated_cost_usd": round(est, 6)}}
    ]
    state["estimated_cost_usd"] = est
    state["cloud_used"] = True
    state["approval_id"] = uuid.uuid4().hex
    ttl = max(60, getattr(settings, "approval_ttl_seconds", 600))
    state["approval_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=ttl)
    ).isoformat()
    state["final_response"] = (
        f"I'd like to make a cloud API call to `{model}` (est. ${est:.4f}).\n"
        f"Approval '{(state['approval_id'])[:8]}…' expires in {ttl}s. "
        "Reply with approval to continue."
    )
