"""Tests for runtime_diagnostics.

Mocks Ollama HTTP + `ollama ps` + `nvidia-smi`. Never requires a real GPU or
running Ollama server.
"""
from __future__ import annotations

import io
from unittest.mock import patch

import httpx
import pytest

from jarvis.models.runtime_diagnostics import (
    classify_processor,
    get_ollama_running_models,
    get_ollama_process_info,
    get_gpu_info,
    get_runtime_snapshot,
)
from jarvis.config.settings import settings


# ---------------------------------------------------------------------------
# classify_processor
# ---------------------------------------------------------------------------


def test_classify_100_gpu():
    assert classify_processor("100% GPU") == "100% GPU"


def test_classify_partial_cpu_gpu():
    assert classify_processor("40%/60% CPU/GPU") == "Partial CPU/GPU"


def test_classify_100_cpu():
    assert classify_processor("100% CPU") == "100% CPU"


def test_classify_empty_is_unknown():
    assert classify_processor("") == "Unknown"


def test_classify_garbage_is_unknown():
    assert classify_processor("banana") == "Unknown"


# ---------------------------------------------------------------------------
# check_ollama_reachable / running models (HTTP mocked)
# ---------------------------------------------------------------------------


def _mock_resp(status=200, json_body=None):
    r = httpx.Response(status_code=status, json=json_body or {})
    return r


def test_running_models_parse(monkeypatch):
    monkeypatch.setattr(settings, "ollama_max_loaded_models", 1)
    body = {"models": [{"name": "qwen3:8b", "size": 5000000000}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _mock_resp(200, body))
    out, warns = get_ollama_running_models(base_url="http://x")
    assert len(out) == 1
    assert out[0]["name"] == "qwen3:8b"


def test_running_models_too_many_warns(monkeypatch):
    monkeypatch.setattr(settings, "ollama_max_loaded_models", 1)
    body = {"models": [{"name": "a"}, {"name": "b"}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _mock_resp(200, body))
    out, warns = get_ollama_running_models(base_url="http://x")
    assert len(out) == 2
    assert any("2 models loaded" in w for w in warns)


def test_ollama_unreachable_returns_false():
    def boom(*a, **k):
        raise httpx.ConnectError("boom")
    import jarvis.models.runtime_diagnostics as rd

    orig = rd.check_ollama_reachable
    import io
    res, warns = rd.check_ollama_reachable(base_url="http://127.0.0.1:1")  # fast fail
    assert res is False
    assert isinstance(warns, list)


# ---------------------------------------------------------------------------
# ollama ps parser
# ---------------------------------------------------------------------------


def test_parse_ollama_ps_100_gpu():
    stdout = (
        "NAME           ID           SIZE      PROCESSOR       STATUS\n"
        "qwen3:8b       abc123       5.2 GB    100% GPU        loaded\n"
    )
    rows, warns = _call_parser(stdout)
    assert rows[0]["name"] == "qwen3:8b"
    assert rows[0]["processor"] == "100% GPU"
    assert rows[0]["status"] == "loaded"


def test_parse_ollama_ps_partial_offload():
    stdout = (
        "NAME           ID           SIZE      PROCESSOR       STATUS\n"
        "qwen3:8b       abc123       5.2 GB    40%/60% CPU/GPU loaded\n"
    )
    rows, _ = _call_parser(stdout)
    assert rows[0]["processor"] == "40%/60% CPU/GPU"


def test_parse_ollama_ps_empty():
    rows, _ = _call_parser("")
    assert rows == []


def test_parse_ollama_ps_invalid_lines_skipped():
    stdout = (
        "NAME           ID           SIZE      PROCESSOR       STATUS\n"
        "garbage line\n"
        "qwen3:8b       abc123       5.2 GB    100% GPU        loaded\n"
    )
    rows, _ = _call_parser(stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "qwen3:8b"


def _call_parser(stdout: str):
    """Parse `ollama ps` stdout directly via the module-level parser."""
    import jarvis.models.runtime_diagnostics as rd

    return rd._parse_ollama_ps(stdout), []


# ---------------------------------------------------------------------------
# nvidia-smi
# ---------------------------------------------------------------------------


def test_nvidia_smi_missing_returns_none(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd.shutil, "which", lambda x: None)
    info, warns = get_gpu_info()
    assert info is None
    assert any("nvidia-smi not found" in w for w in warns)


def test_nvidia_smi_parses(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd.shutil, "which", lambda x: "/usr/bin/nvidia-smi")

    class _P:
        returncode = 0
        stdout = "NVIDIA GeForce RTX 4090, 24564, 5123\n"
        stderr = ""

    monkeypatch.setattr(rd.subprocess, "run", lambda *a, **k: _P())
    info, warns = get_gpu_info()
    assert info is not None
    assert "RTX 4090" in info["gpu_name"]
    assert info["vram_total_mb"] == 24564
    assert info["vram_used_mb"] == 5123


def test_nvidia_smi_bad_output(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd.shutil, "which", lambda x: "/usr/bin/nvidia-smi")

    class _P:
        returncode = 0
        stdout = "garbage line that won't parse\n"
        stderr = ""

    monkeypatch.setattr(rd.subprocess, "run", lambda *a, **k: _P())
    info, warns = get_gpu_info()
    assert info is None
    assert any("unreadable" in w or "parse" in w.lower() for w in warns)


# ---------------------------------------------------------------------------
# Full snapshot — secrets safety + shape
# ---------------------------------------------------------------------------


def test_snapshot_does_not_expose_api_keys(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-supersecret-123")
    snap = get_runtime_snapshot()
    dumped = repr(snap)
    assert "sk-supersecret-123" not in dumped
    assert "openrouter_api_key" not in snap


def test_snapshot_shape_has_required_fields():
    snap = get_runtime_snapshot()
    for key in (
        "ollama_reachable", "model", "processor", "gpu_name",
        "vram_total_mb", "vram_used_mb", "system_ram_used_mb", "warnings",
        "recommendations", "running_models", "configured_models",
        "context", "parallel",
    ):
        assert key in snap, f"missing {key}"


def test_snapshot_configured_models_has_no_cloud_chain():
    snap = get_runtime_snapshot()
    cfg = snap["configured_models"]
    # Only local ollama model names exposed — no complex cloud ids.
    assert set(cfg.keys()) == {"general", "strong_local", "coding", "coding_small", "embedding"}


def test_snapshot_processor_unknown_when_unreachable(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd
    monkeypatch.setattr(rd, "check_ollama_reachable", lambda base_url=None: (False, ["unreachable"]))
    monkeypatch.setattr(rd, "get_ollama_running_models", lambda base_url=None: ([], []))
    monkeypatch.setattr(rd, "get_ollama_process_info", lambda: ([], ["cli missing"]))
    monkeypatch.setattr(rd, "get_gpu_info", lambda: (None, ["no nvidia-smi"]))
    snap = get_runtime_snapshot()
    assert snap["ollama_reachable"] is False
    assert snap["processor"] == "Unknown"


def test_snapshot_partial_offload_recommendation(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd
    monkeypatch.setattr(rd, "check_ollama_reachable", lambda base_url=None: (True, []))
    monkeypatch.setattr(rd, "get_ollama_running_models", lambda base_url=None: ([], []))
    monkeypatch.setattr(rd, "get_ollama_process_info", lambda: (
        [{"name": "qwen3:8b", "size": "5.2 GB", "processor": "40%/60% CPU/GPU", "status": "loaded"}], []
    ))
    monkeypatch.setattr(rd, "get_gpu_info", lambda: ({"gpu_name": "X", "vram_total_mb": 8000, "vram_used_mb": 4000}, []))
    snap = get_runtime_snapshot()
    assert any("Partial CPU/GPU" in r for r in snap["recommendations"])
    assert snap["processor"] == "Partial CPU/GPU"


def test_snapshot_multiple_models_recommendation(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd
    monkeypatch.setattr(rd, "check_ollama_reachable", lambda base_url=None: (True, []))
    monkeypatch.setattr(rd, "get_ollama_running_models", lambda base_url=None: (
        [{"name": "a", "size": 1, "expires_at": ""}, {"name": "b", "size": 1, "expires_at": ""}], []
    ))
    monkeypatch.setattr(rd, "get_ollama_process_info", lambda: ([], []))
    monkeypatch.setattr(rd, "get_gpu_info", lambda: (None, []))
    snap = get_runtime_snapshot()
    assert any("OLLAMA_MAX_LOADED_MODELS=1" in r for r in snap["recommendations"])
