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

from langchain_ollama import ChatOllama

from jarvis.config.settings import settings

logger = logging.getLogger(__name__)


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
    # num_batch / flash_attention / kv_cache_type are real Ollama request
    # options but ChatOllama has no constructor field for them (extra='ignore'
    # would silently drop them). We surface this so the user knows they must
    # be set as server-side OLLAMA_* env vars on `ollama serve` instead.
    for opt_name in ("num_batch", "flash_attention", "kv_cache_type"):
        if opt_name == "num_batch" and settings.ollama_num_batch:
            if "num_batch" not in supported:
                logger.info(
                    "OLLAMA_NUM_BATCH=%s is a server-side / request option not exposed by ChatOllama; set it via the Ollama server env or OLLAMA_NUM_BATCH.",
                    settings.ollama_num_batch,
                )
        if opt_name in ("flash_attention", "kv_cache_type"):
            val = getattr(settings, f"ollama_{opt_name}", None)
            if val and opt_name not in supported:
                logger.info(
                    "OLLAMA_%s is not a direct ChatOllama field; configure it on the Ollama server (OLLAMA_%s) for it to take effect.",
                    opt_name.upper(), opt_name.upper(),
                )
    return out


def _build(model_name: str, temperature: float, *, force_cpu: bool = False) -> ChatOllama:
    """Construct a ChatOllama with runtime options; model name authoritative."""
    opts = _runtime_options()
    opts["model"] = model_name
    opts["base_url"] = settings.ollama_base_url
    opts["temperature"] = temperature
    if force_cpu:
        # num_gpu=0 disables GPU offload entirely (pure CPU fallback).
        opts["num_gpu"] = 0
    return ChatOllama(**opts)


def get_general_model(temperature: float = 0.4, *, force_cpu: bool = False) -> ChatOllama:
    return _build(settings.general_model, temperature, force_cpu=force_cpu)


def get_strong_local_model(temperature: float = 0.3, *, force_cpu: bool = False) -> ChatOllama:
    return _build(settings.strong_local_model, temperature, force_cpu=force_cpu)


def get_coding_model(temperature: float = 0.2, *, force_cpu: bool = False) -> ChatOllama:
    return _build(settings.coding_model, temperature, force_cpu=force_cpu)


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
) -> ChatOllama:
    """Build a ChatOllama for an explicitly-chosen model name.

    Used by the branches after `select_model(state, settings)` has decided
    which model to run. If `temperature` is None we pick a sane default
    based on the branch intent.

    Pass ``force_cpu=True`` to run with ``num_gpu=0`` (used by the graceful
    GPU→CPU degradation path when the model doesn't fit in VRAM).

    The model name is the single source of truth for *which* model loads;
    runtime options only tune context/batch/keep-alive and never replace
    the model.
    """
    temp = temperature if temperature is not None else _TEMPERATURE_BY_INTENT.get(intent, 0.4)
    return _build(model_name, temp, force_cpu=force_cpu)


__all__ = [
    "get_general_model",
    "get_strong_local_model",
    "get_coding_model",
    "get_model_named",
]
