"""Wrapper around local Ollama models via LangChain.

Single source of truth for building ``ChatOllama`` clients. The model name
is always taken from settings (or explicitly chosen by the model selector)
and is never overridden by runtime options — runtime options only tune
*how* the model runs (context window, batch, keep-alive), not *which* model.

GPU execution is forced, never optional: we pass ``num_gpu=-1`` so Ollama
offloads **every** layer to the GPU. The model therefore runs entirely in
VRAM and never spills into system RAM; if it cannot fit in VRAM, Ollama
refuses to load it rather than falling back to a partially-CPU execution.

Lazy creation: each helper builds a fresh ``ChatOllama`` on demand. There is
no module-level instantiation, so importing this module does NOT load any
model into VRAM. The branches call these helpers *after* the model selector
has chosen a single model, so at most one local generation model is built
(and thus loaded) per request.
"""
import logging
from functools import lru_cache
from typing import Optional

from langchain_ollama import ChatOllama

from jarvis.config.settings import settings

logger = logging.getLogger(__name__)

# Cache for model clients to avoid redundant initialization
_model_cache: dict[tuple[str, float, bool, Optional[int]], ChatOllama] = {}


def _runtime_options() -> dict:
    """Build constructor kwargs for ChatOllama from runtime settings.

    Only fields ChatOllama actually supports (verified against the installed
    langchain_ollama model_fields) are emitted. Unsupported keys are dropped
    with a warning so we never silently mis-apply a setting.

    Supported direct fields: num_ctx, num_gpu, num_thread, num_predict,
    temperature, keep_alive, num_batch (NOT a direct field — see note).
    """
    if not settings.gpu_optimization_enabled:
        return {}
    opts: dict = {
        "num_ctx": settings.ollama_context_length,
        "num_gpu": settings.ollama_num_gpu,  # -1 = offload ALL layers -> pure GPU, no RAM spill
        "temperature": 0.4,  # placeholder; caller overrides per-intent
        "keep_alive": settings.ollama_keep_alive,
    }
    # Filter to fields ChatOllama actually supports so we never pass an
    # unknown kwarg that pydantic would silently drop (extra='ignore').
    # `model_fields` exists on real ChatOllama (pydantic v2 model); the
    # test fake has none, in which case we pass everything (the fake
    # absorbs via **kwargs).
    if hasattr(ChatOllama, "model_fields"):
        supported = set(ChatOllama.model_fields.keys())
        out: dict = {}
        for k, v in opts.items():
            if k in supported:
                out[k] = v
            else:
                logger.warning(
                    "Omitting runtime option '%s' — not a supported ChatOllama field.", k,
                )
        return out
    return opts


def _build(
    model_name: str,
    temperature: float,
    *,
    force_cpu: bool = False,
    json_mode: bool = False,
    num_gpu: int | None = None,
) -> ChatOllama:
    """Construct a ChatOllama with runtime options; model name authoritative.

    ``num_gpu`` overrides ``settings.ollama_num_gpu`` when provided (used by
    the Phase 6 GPU policy to honour require/prefer/allow semantics). The
    model name is never replaced by runtime options.

    Validates Ollama connectivity and model availability before creating client.
    Results are cached to avoid redundant initialization.
    """
    # Validate Ollama is available
    from jarvis.models.ollama_connection import check_ollama_available

    is_available, error_msg = check_ollama_available()
    if not is_available:
        logger.error("Ollama unavailable when building model %s: %s", model_name, error_msg)
        raise ConnectionError(
            error_msg or f"Ollama is not reachable at {settings.ollama_base_url}"
        )

    # Build cache key (json_mode doesn't affect client, only opts)
    cache_key = (model_name, temperature, force_cpu, num_gpu)

    # Return cached client if available
    if cache_key in _model_cache:
        logger.debug("Using cached ChatOllama client for %s", model_name)
        return _model_cache[cache_key]

    opts = _runtime_options()
    opts["model"] = model_name
    opts["base_url"] = settings.ollama_base_url
    opts["temperature"] = temperature
    if json_mode:
        opts["format"] = "json"
    if force_cpu:
        # num_gpu=0 disables GPU offload entirely (pure CPU fallback).
        opts["num_gpu"] = 0
    elif num_gpu is not None:
        opts["num_gpu"] = num_gpu

    try:
        client = ChatOllama(**opts)
        # Cache the client for reuse
        _model_cache[cache_key] = client
        logger.debug("Created and cached new ChatOllama client for %s", model_name)
        return client
    except Exception as exc:
        logger.error(
            "Failed to create ChatOllama client for model %s: %s",
            model_name,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to initialize model {model_name}: {exc}") from exc


def get_general_model(temperature: float = 0.4, *, force_cpu: bool = False) -> ChatOllama:
    return _build(settings.general_model, temperature, force_cpu=force_cpu)


def get_router_model() -> ChatOllama:
    """Build the small intent-classification model in JSON mode.

    Uses ``router_model`` when set, else falls back to ``general_model``.
    Runs at temperature 0 for deterministic labels. Fired only for
    borderline prompts (see router_node), so latency stays predictable.
    """
    name = settings.router_model or settings.general_model
    return _build(name, 0.0, json_mode=True)


# Per-intent default temperatures for dynamic model selection.
_TEMPERATURE_BY_INTENT = {
    "general": 0.4,
    "coding": 0.2,
    "complex": 0.3,
}


def get_model_named(
    model_name: str,
    intent: str = "general",
    temperature: float | None = None,
    *,
    force_cpu: bool = False,
    num_gpu: int | None = None,
) -> ChatOllama:
    """Build a ChatOllama for an explicitly-chosen model name.

    Used by the branches after `select_model(state, settings)` has decided
    which model to run. If `temperature` is None we pick a sane default
    based on the branch intent.

    Pass ``force_cpu=True`` to run with ``num_gpu=0`` (used by the graceful
    GPU→CPU degradation path when the model doesn't fit in VRAM), or
    ``num_gpu`` from a Phase 6 ``GPUPlan`` to honour require/prefer/allow
    semantics.

    The model name is the single source of truth for *which* model loads;
    runtime options only tune context/batch/keep-alive and never replace
    the model.
    """
    temp = temperature if temperature is not None else _TEMPERATURE_BY_INTENT.get(intent, 0.4)
    return _build(model_name, temp, force_cpu=force_cpu, num_gpu=num_gpu)


def clear_model_cache():
    """Clear the model client cache.

    Useful when switching models or after connection issues to force
    recreation of clients.
    """
    global _model_cache
    _model_cache.clear()
    logger.info("Cleared ChatOllama client cache")


__all__ = [
    "get_general_model",
    "get_model_named",
    "get_router_model",
    "clear_model_cache",
]
