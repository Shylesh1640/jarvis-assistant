"""Safe GPU execution policy (Phase 6).

Decides *how* a local model should run — full GPU offload, partial CPU/GPU,
or explicit CPU fallback — and never misreports the outcome. The policy is
driven by ``GPU_POLICY`` (``prefer_gpu`` / ``require_gpu`` / ``allow_cpu``)
plus the ``GPU_*`` toggles in settings:

* ``require_gpu`` — a model that cannot run on the GPU is **blocked** with a
  structured ``GPURequiredError`` (never silently run on CPU).
* ``prefer_gpu`` — full GPU preferred; a strong local model that exceeds
  VRAM is routed to a configured fallback / flagged, or (only when
  explicitly allowed) run partially on CPU or on CPU with a visible warning.
* ``allow_cpu`` — CPU fallback allowed, always surfaced in metadata/logs.

No model name is ever changed; the policy only chooses *how* the existing
model runs. All probes are best-effort and never raise.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from jarvis.config.settings import Settings, settings

logger = logging.getLogger(__name__)

GPU_POLICIES = ("prefer_gpu", "require_gpu", "allow_cpu")

_SPLIT_FULL_GPU = "100% GPU"
_SPLIT_PARTIAL = "Partial CPU/GPU"
_SPLIT_CPU = "100% CPU"
_SPLIT_UNKNOWN = "unknown"


class GPURequiredError(RuntimeError):
    """GPU execution was required but could not be satisfied."""

    def __init__(self, reason: str, suggested_action: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.suggested_action = suggested_action


@dataclass
class GPUPlan:
    """The decided execution plan for one local model request."""

    gpu_policy: str
    num_gpu: int | None = None  # -1 all layers / 0 CPU / positive layer count / None default
    full_offload: bool = False
    partial_offload: bool = False
    processor_split: str = _SPLIT_UNKNOWN
    gpu_fallback_used: bool = False
    cpu_fallback_used: bool = False
    runtime_warning: str | None = None
    # When set, the branch should run *fallback_model* instead of the
    # requested model (used when a strong local model cannot run on GPU).
    fallback_model: str | None = None
    blocked: bool = False
    blocked_reason: str | None = None
    suggested_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu_policy": self.gpu_policy,
            "processor_split": self.processor_split,
            "gpu_fallback_used": self.gpu_fallback_used,
            "cpu_fallback_used": self.cpu_fallback_used,
            "runtime_warning": self.runtime_warning,
        }


def vram_status(gpu_info: dict | None, s: Settings | None = None) -> dict[str, Any]:
    """Evaluate a nvidia-smi snapshot against the GPU thresholds.

    Returns ``ok`` = None when *gpu_info* is unavailable (cannot verify),
    True when thresholds are comfortably met, False when the GPU looks too
    full to load another model.
    """
    cfg = s or settings
    if not gpu_info or not gpu_info.get("vram_total_mb"):
        return {
            "available": False,
            "ok": None,
            "used_mb": None,
            "total_mb": None,
            "free_mb": None,
            "reason": "gpu_diagnostics_unavailable",
        }
    total = int(gpu_info["vram_total_mb"])
    used = int(gpu_info.get("vram_used_mb") or 0)
    free = total - used
    reasons: list[str] = []
    ok = True
    if cfg.gpu_max_vram_percent > 0 and used / total * 100 > cfg.gpu_max_vram_percent:
        ok = False
        reasons.append(
            f"VRAM usage {used}/{total} MB exceeds GPU_MAX_VRAM_PERCENT={cfg.gpu_max_vram_percent}"
        )
    if cfg.gpu_min_free_vram_mb > 0 and free < cfg.gpu_min_free_vram_mb:
        ok = False
        reasons.append(
            f"free VRAM {free} MB below GPU_MIN_FREE_VRAM_MB={cfg.gpu_min_free_vram_mb}"
        )
    return {
        "available": True,
        "ok": ok,
        "used_mb": used,
        "total_mb": total,
        "free_mb": free,
        "reason": "; ".join(reasons) if reasons else None,
    }


# ---------------------------------------------------------------------------
# Strong-model VRAM estimate (best-effort via Ollama /api/show)
# ---------------------------------------------------------------------------

_ESTIMATE_CACHE: dict[tuple[str, int], int | None] = {}
_ESTIMATE_LOCK = threading.Lock()


def estimate_model_vram_mb(
    model_name: str,
    context_length: int,
    base_url: str | None = None,
) -> int | None:
    """Estimate the VRAM a model needs at *context_length* (MB), or None.

    Uses Ollama ``/api/show`` for the model file size and, when available,
    layer/head geometry to approximate the KV cache. Best-effort and cached
    per (model, context); a missing /api/show yields None (callers treat
    "cannot verify" conservatively, never as "fits").
    """
    key = (model_name, context_length)
    with _ESTIMATE_LOCK:
        if key in _ESTIMATE_CACHE:
            return _ESTIMATE_CACHE[key]

    from jarvis.config.settings import settings as _s

    url = (base_url or _s.ollama_base_url).rstrip("/")
    try:
        import httpx

        r = httpx.post(
            f"{url}/api/show",
            json={"model": model_name},
            timeout=5.0,
        )
        if r.status_code != 200:
            with _ESTIMATE_LOCK:
                _ESTIMATE_CACHE[key] = None
            return None
        info = (r.json() or {}).get("model_info") or {}
        general = info.get("general") or {}
        size_bytes = general.get("size") or 0
        if not size_bytes:
            params = general.get("parameter_count") or 0
            size_bytes = int(params) * 2  # ~2 bytes/param lower-bound estimate
        kv_bytes = _kv_cache_bytes(info, context_length)
        total_mb = (int(size_bytes) + kv_bytes) // (1024 * 1024)
        with _ESTIMATE_LOCK:
            _ESTIMATE_CACHE[key] = total_mb
        return total_mb
    except Exception as exc:  # noqa: BLE001
        logger.debug("estimate_model_vram_mb failed for %s: %s", model_name, exc)
        with _ESTIMATE_LOCK:
            _ESTIMATE_CACHE[key] = None
        return None


def _kv_cache_bytes(model_info: dict, context_length: int) -> int:
    """Approximate KV-cache bytes for Q8_0 quantisation at *context_length*."""
    llama = model_info.get("llama") or {}
    layers = llama.get("layer_count") or llama.get("n_layer") or 0
    heads_kv = llama.get("kv_head_count") or llama.get("head_count") or 0
    embed = llama.get("embedding_length") or 0
    heads = llama.get("head_count") or 0
    if not layers or not heads_kv or not embed or not heads:
        return 0
    head_dim = embed // heads
    # per token: layers * kv_heads * head_dim * (K+V) * bytes-per-element(Q8~1)
    per_token = layers * heads_kv * head_dim * 2
    return per_token * context_length


def _strong_model_fallback(cfg: Settings) -> str | None:
    """Return the fallback model for a declined strong local model."""
    if cfg.general_model and cfg.general_model != cfg.strong_local_model:
        return cfg.general_model
    return None


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide_execution_plan(
    model_name: str,
    *,
    is_strong_model: bool = False,
    context_length: int,
    gpu_info: dict | None = None,
    s: Settings | None = None,
) -> GPUPlan:
    """Compute the GPUPlan for *model_name* at *context_length*.

    *gpu_info* is a nvidia-smi snapshot (``None`` = unavailable). The plan
    may set ``fallback_model`` (strong model routed away from an impossible
    GPU run) or ``blocked`` (require_gpu unsatisfiable) — callers must not
    ignore those.
    """
    cfg = s or settings
    policy = cfg.gpu_policy if cfg.gpu_policy in GPU_POLICIES else "prefer_gpu"
    checks_enabled = cfg.gpu_runtime_check_enabled

    def _blocked(reason: str, action: str) -> GPUPlan:
        return GPUPlan(
            gpu_policy=policy,
            blocked=True,
            blocked_reason=reason,
            suggested_action=action,
        )

    if policy == "require_gpu":
        return _plan_require_gpu(model_name, gpu_info, checks_enabled, cfg, policy)

    if policy == "allow_cpu":
        return GPUPlan(
            gpu_policy=policy,
            num_gpu=-1,
            full_offload=True,
            processor_split=_SPLIT_FULL_GPU,
            cpu_fallback_used=False,
        )

    # prefer_gpu
    if checks_enabled and is_strong_model:
        status = vram_status(gpu_info, cfg)
        estimated = estimate_model_vram_mb(model_name, context_length)
        exceeds = _strong_model_exceeds(status, estimated)
        if exceeds:
            if cfg.gpu_strong_model_allow_partial_offload:
                return GPUPlan(
                    gpu_policy=policy,
                    num_gpu=-1,
                    partial_offload=True,
                    processor_split=_SPLIT_PARTIAL,
                    runtime_warning=(
                        "The selected strong model exceeds available dedicated VRAM at "
                        "the current context length; it will run partially on CPU/GPU "
                        "because GPU_STRONG_MODEL_ALLOW_PARTIAL_OFFLOAD=true."
                    ),
                )
            fallback = _strong_model_fallback(cfg)
            if fallback and cfg.gpu_allow_cpu_fallback:
                return GPUPlan(
                    gpu_policy=policy,
                    num_gpu=-1,
                    full_offload=True,
                    processor_split=_SPLIT_FULL_GPU,
                    gpu_fallback_used=True,
                    fallback_model=fallback,
                    runtime_warning=(
                        "The selected strong model exceeds available dedicated VRAM at "
                        "the current context length; routing to the configured local "
                        "fallback model instead."
                    ),
                )
            if fallback:
                return GPUPlan(
                    gpu_policy=policy,
                    gpu_fallback_used=True,
                    fallback_model=fallback,
                    runtime_warning=(
                        "The selected strong model exceeds available dedicated VRAM at "
                        "the current context length; routed to the configured fallback."
                    ),
                )
            return GPUPlan(
                gpu_policy=policy,
                blocked=False,
                runtime_warning=(
                    "The selected strong model exceeds available dedicated VRAM at the "
                    "current context length and cannot be run here. Use a background "
                    "task, a smaller context length, or enable partial offload."
                ),
            )
    return GPUPlan(
        gpu_policy=policy,
        num_gpu=-1,
        full_offload=True,
        processor_split=_SPLIT_FULL_GPU,
    )


def _strong_model_exceeds(status: dict[str, Any], estimated_mb: int | None) -> bool:
    """True when the strong model is expected to exceed available VRAM.

    Conservative: if the estimate is unknown we rely on the free-VRAM
    threshold (status.ok). Never claims "fits" without evidence.
    """
    if status.get("ok") is False:
        return True
    if estimated_mb is not None and status.get("free_mb") is not None:
        if estimated_mb > status["free_mb"]:
            return True
    return False


def _plan_require_gpu(
    model_name: str,
    gpu_info: dict | None,
    checks_enabled: bool,
    cfg: Settings,
    policy: str,
) -> GPUPlan:
    if not checks_enabled:
        return GPUPlan(gpu_policy=policy, num_gpu=-1, full_offload=True, processor_split=_SPLIT_FULL_GPU)
    if gpu_info is None:
        return GPUPlan(
            gpu_policy=policy,
            blocked=True,
            blocked_reason=(
                "GPU_POLICY=require_gpu but GPU diagnostics are unavailable "
                "(nvidia-smi not found or failed)."
            ),
            suggested_action=(
                "Install/configure nvidia-smi, or switch GPU_POLICY to prefer_gpu or allow_cpu."
            ),
        )
    status = vram_status(gpu_info, cfg)
    if status.get("ok") is False:
        reason = status.get("reason") or "insufficient VRAM"
        return GPUPlan(
            gpu_policy=policy,
            blocked=True,
            blocked_reason=f"GPU_POLICY=require_gpu cannot be satisfied: {reason}.",
            suggested_action=(
                "Free GPU memory, reduce OLLAMA_CONTEXT_LENGTH, use a background "
                "task, or switch GPU_POLICY to prefer_gpu."
            ),
        )
    if cfg.gpu_require_full_offload:
        return GPUPlan(gpu_policy=policy, num_gpu=-1, full_offload=True, processor_split=_SPLIT_FULL_GPU)
    # require_gpu without full-offload: any GPU involvement satisfies it.
    return GPUPlan(gpu_policy=policy, num_gpu=-1, full_offload=True, processor_split=_SPLIT_FULL_GPU)


__all__ = [
    "GPUPlan",
    "GPURequiredError",
    "GPU_POLICIES",
    "decide_execution_plan",
    "estimate_model_vram_mb",
    "vram_status",
]