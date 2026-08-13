"""Tests for the GET /runtime route."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from jarvis.api.main import app


def _fake_snapshot(**overrides):
    base = {
        "ollama_reachable": True,
        "ollama_version": "0.31.1",
        "model": "qwen3:8b",
        "processor": "100% GPU",
        "processor_raw": "100% GPU",
        "gpu_name": "NVIDIA RTX 4090",
        "vram_total_mb": 24564,
        "vram_used_mb": 5123,
        "system_ram_used_mb": None,
        "warnings": [],
        "recommendations": [],
        "running_models": [{"name": "qwen3:8b", "size": 5_000_000_000, "expires_at": ""}],
        "configured_models": {
            "general": "qwen3:8b",
            "strong_local": "qwen3:14b",
            "coding": "qwen2.5-coder:7b-q5_K_M",
            "coding_small": "qwen2.5-coder:7b-q5_K_M",
            "embedding": "qwen3-embedding:latest",
        },
        "context": {
            "num_ctx": 8192, "num_batch": 512,
            "history_max_turns": 20, "context_token_budget": 12000,
            "rag_context_token_cap": 2048, "selected_text_token_cap": 1024,
            "retrieval_top_k": 5,
        },
        "parallel": {
            "num_parallel": 1, "max_loaded_models": 1,
            "gpu_optimization_enabled": True, "flash_attention": 1,
            "kv_cache_type": "q8_0", "keep_alive": "5m",
        },
    }
    base.update(overrides)
    return base


def test_runtime_returns_200():
    with patch("jarvis.api.routes.runtime.get_runtime_snapshot", lambda: _fake_snapshot()):
        c = TestClient(app)
        r = c.get("/runtime")
        assert r.status_code == 200
        body = r.json()
        assert body["ollama_reachable"] is True
        assert body["processor"] == "100% GPU"


def test_runtime_never_exposes_secrets():
    with patch("jarvis.api.routes.runtime.get_runtime_snapshot",
               lambda: _fake_snapshot()):
        c = TestClient(app)
        r = c.get("/runtime")
        dumped = repr(r.json())
        # No api keys / auth strings should ever appear.
        assert "openrouter_api_key" not in dumped.lower()
        assert "sk-" not in dumped


def test_runtime_reports_unknown_when_unreachable():
    with patch("jarvis.api.routes.runtime.get_runtime_snapshot",
               lambda: _fake_snapshot(ollama_reachable=False, processor="Unknown", model="")):
        c = TestClient(app)
        r = c.get("/runtime")
        assert r.status_code == 200
        assert r.json()["processor"] == "Unknown"


def test_runtime_reports_partial_offload():
    with patch("jarvis.api.routes.runtime.get_runtime_snapshot",
               lambda: _fake_snapshot(processor="Partial CPU/GPU")):
        c = TestClient(app)
        r = c.get("/runtime")
        assert r.json()["processor"] == "Partial CPU/GPU"
