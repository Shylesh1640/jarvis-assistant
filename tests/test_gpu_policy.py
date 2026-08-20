"""Tests for the Phase 6 safe-GPU execution policy (models/gpu_policy.py)."""
from __future__ import annotations

import pytest

from jarvis.models import gpu_policy as gp
from jarvis.models.gpu_policy import (
    GPURequiredError,
    decide_execution_plan,
    estimate_model_vram_mb,
    vram_status,
)


@pytest.fixture(autouse=True)
def _reset_estimate_cache():
    with gp._ESTIMATE_LOCK:
        gp._ESTIMATE_CACHE.clear()
    yield
    with gp._ESTIMATE_LOCK:
        gp._ESTIMATE_CACHE.clear()


def _gpu(**overrides):
    base = {"gpu_name": "NVIDIA RTX 5050", "vram_total_mb": 8192, "vram_used_mb": 1024}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# vram_status
# ---------------------------------------------------------------------------


def test_vram_status_unknown_when_no_info():
    status = vram_status(None)
    assert status["ok"] is None
    assert status["reason"] == "gpu_diagnostics_unavailable"


def test_vram_status_ok_when_healthy(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_max_vram_percent", 95)
    monkeypatch.setattr(gp.settings, "gpu_min_free_vram_mb", 512)
    status = vram_status(_gpu(vram_used_mb=2048))
    assert status["ok"] is True
    assert status["free_mb"] == 8192 - 2048
    assert status["reason"] is None


def test_vram_status_fails_when_percent_exceeded(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_max_vram_percent", 95)
    monkeypatch.setattr(gp.settings, "gpu_min_free_vram_mb", 512)
    status = vram_status(_gpu(vram_used_mb=7900))
    assert status["ok"] is False
    assert "GPU_MAX_VRAM_PERCENT" in status["reason"]


def test_vram_status_fails_when_free_too_low(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_max_vram_percent", 95)
    monkeypatch.setattr(gp.settings, "gpu_min_free_vram_mb", 512)
    status = vram_status(_gpu(vram_used_mb=7800))
    assert status["ok"] is False
    assert "GPU_MIN_FREE_VRAM_MB" in status["reason"]


# ---------------------------------------------------------------------------
# policy decisions
# ---------------------------------------------------------------------------


def test_prefer_gpu_defaults_to_full_offload():
    plan = decide_execution_plan("qwen3:8b", context_length=8192)
    assert plan.gpu_policy == "prefer_gpu"
    assert plan.num_gpu == -1
    assert plan.full_offload is True
    assert plan.processor_split == "100% GPU"
    assert plan.blocked is False
    assert plan.fallback_model is None


def test_allow_cpu_attempts_gpu_first_with_visible_fallback():
    plan = decide_execution_plan("qwen3:8b", context_length=8192)
    assert plan.num_gpu == -1


def test_require_gpu_blocked_when_no_gpu_diagnostics(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "require_gpu")
    plan = decide_execution_plan("qwen3:8b", context_length=8192, gpu_info=None)
    assert plan.blocked is True
    assert "require_gpu" in plan.blocked_reason
    with pytest.raises(GPURequiredError):
        raise GPURequiredError(plan.blocked_reason, plan.suggested_action)


def test_require_gpu_blocked_when_vram_full(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "require_gpu")
    plan = decide_execution_plan(
        "qwen3:8b", context_length=8192, gpu_info=_gpu(vram_used_mb=7900)
    )
    assert plan.blocked is True
    assert plan.blocked_reason


def test_require_gpu_allows_healthy_gpu(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "require_gpu")
    monkeypatch.setattr(gp.settings, "gpu_require_full_offload", True)
    plan = decide_execution_plan(
        "qwen3:8b", context_length=8192, gpu_info=_gpu(vram_used_mb=2048)
    )
    assert plan.blocked is False
    assert plan.num_gpu == -1
    assert plan.full_offload is True


def test_require_gpu_skips_verification_when_checks_disabled(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "require_gpu")
    monkeypatch.setattr(gp.settings, "gpu_runtime_check_enabled", False)
    plan = decide_execution_plan("qwen3:8b", context_length=8192, gpu_info=None)
    assert plan.blocked is False
    assert plan.num_gpu == -1


# ---------------------------------------------------------------------------
# strong-model routing under prefer_gpu
# ---------------------------------------------------------------------------


def test_strong_model_exceeds_routes_to_fallback(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "prefer_gpu")
    monkeypatch.setattr(gp.settings, "general_model", "qwen3:8b")
    monkeypatch.setattr(gp.settings, "strong_local_model", "qwen3:14b")
    monkeypatch.setattr(gp.settings, "gpu_allow_cpu_fallback", True)
    monkeypatch.setattr(gp.settings, "gpu_strong_model_allow_partial_offload", False)
    # free VRAM tiny, estimate large
    monkeypatch.setattr(gp, "estimate_model_vram_mb", lambda *a, **k: 9000)
    plan = decide_execution_plan(
        "qwen3:14b",
        is_strong_model=True,
        context_length=8192,
        gpu_info=_gpu(vram_used_mb=7800),
    )
    assert plan.fallback_model == "qwen3:8b"
    assert plan.gpu_fallback_used is True
    assert plan.blocked is False
    assert "fallback" in plan.runtime_warning.lower()


def test_strong_model_partial_offload_when_configured(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "prefer_gpu")
    monkeypatch.setattr(gp.settings, "gpu_strong_model_allow_partial_offload", True)
    monkeypatch.setattr(gp, "estimate_model_vram_mb", lambda *a, **k: 9000)
    plan = decide_execution_plan(
        "qwen3:14b",
        is_strong_model=True,
        context_length=8192,
        gpu_info=_gpu(vram_used_mb=7800),
    )
    assert plan.partial_offload is True
    assert plan.processor_split == "Partial CPU/GPU"
    assert plan.fallback_model is None
    assert plan.runtime_warning


def test_strong_model_exceeds_without_fallback_warns(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "prefer_gpu")
    monkeypatch.setattr(gp.settings, "general_model", "qwen3:14b")
    monkeypatch.setattr(gp.settings, "strong_local_model", "qwen3:14b")
    monkeypatch.setattr(gp, "estimate_model_vram_mb", lambda *a, **k: 9000)
    plan = decide_execution_plan(
        "qwen3:14b",
        is_strong_model=True,
        context_length=8192,
        gpu_info=_gpu(vram_used_mb=7800),
    )
    assert plan.blocked is False
    assert plan.fallback_model is None
    assert plan.runtime_warning


def test_strong_model_fits_stays_full_gpu(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "prefer_gpu")
    monkeypatch.setattr(gp, "estimate_model_vram_mb", lambda *a, **k: 2000)
    plan = decide_execution_plan(
        "qwen3:14b",
        is_strong_model=True,
        context_length=8192,
        gpu_info=_gpu(vram_used_mb=1024),
    )
    assert plan.fallback_model is None
    assert plan.full_offload is True
    assert plan.processor_split == "100% GPU"


def test_strong_model_unknown_estimate_not_assumed_to_fit(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "prefer_gpu")
    monkeypatch.setattr(gp, "estimate_model_vram_mb", lambda *a, **k: None)
    # free VRAM above the floor: cannot verify -> proceed, never claim "fits".
    plan = decide_execution_plan(
        "qwen3:14b",
        is_strong_model=True,
        context_length=8192,
        gpu_info=_gpu(vram_used_mb=1024),
    )
    assert plan.blocked is False
    assert plan.runtime_warning is None


# ---------------------------------------------------------------------------
# VRAM estimate helper
# ---------------------------------------------------------------------------


def test_estimate_returns_none_when_show_fails(monkeypatch):
    import httpx

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("no server")

    monkeypatch.setattr(httpx, "post", _boom)
    assert estimate_model_vram_mb("qwen3:8b", 8192) is None


def test_estimate_returns_none_on_non_200(monkeypatch):
    import httpx

    class _Resp:
        status_code = 404

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    assert estimate_model_vram_mb("qwen3:8b", 8192) is None


def test_estimate_computes_mb_from_model_size(monkeypatch):
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "model_info": {
                    "general": {"size": 8 * 1024 * 1024 * 1024},  # 8 GiB
                }
            }

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    estimate = estimate_model_vram_mb("qwen3:8b", 8192)
    assert estimate is not None
    assert estimate == 8 * 1024


def test_estimate_uses_parameter_count_fallback(monkeypatch):
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {"model_info": {"general": {"parameter_count": 8_000_000_000}}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    estimate = estimate_model_vram_mb("qwen3:8b", 8192)
    assert estimate is not None
    assert estimate > 0


def test_estimate_adds_kv_cache_geometry(monkeypatch):
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "model_info": {
                    "general": {"size": 4 * 1024 * 1024 * 1024},
                    "llama": {
                        "layer_count": 32,
                        "head_count": 8,
                        "kv_head_count": 8,
                        "embedding_length": 4096,
                    },
                }
            }

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    estimate = estimate_model_vram_mb("qwen3:8b", 4096)
    # model size (4096 MB) + KV (4096 * 32 * 8 * 512 * 2 bytes = 4096*32*8*512*2/(1024^2))
    assert estimate is not None
    assert estimate > 4096


def test_estimate_cached_across_calls(monkeypatch):
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {"model_info": {"general": {"size": 1024 * 1024 * 1024}}}

    calls = []

    def _post(*a, **k):
        calls.append(a)
        return _Resp()

    monkeypatch.setattr(httpx, "post", _post)
    first = estimate_model_vram_mb("qwen3:8b", 4096)
    second = estimate_model_vram_mb("qwen3:8b", 4096)
    assert first == second
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def test_policy_names_constant():
    assert set(gp.GPU_POLICIES) == {"prefer_gpu", "require_gpu", "allow_cpu"}


def test_plan_to_dict_contains_no_secrets():
    plan = decide_execution_plan("qwen3:8b", context_length=8192)
    dumped = repr(plan.to_dict())
    assert "api_key" not in dumped.lower()
    assert "sk-" not in dumped


def test_invalid_policy_falls_back_to_prefer_gpu(monkeypatch):
    monkeypatch.setattr(gp.settings, "gpu_policy", "bogus")
    plan = decide_execution_plan("qwen3:8b", context_length=8192)
    assert plan.gpu_policy == "prefer_gpu"