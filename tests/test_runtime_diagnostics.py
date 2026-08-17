"""Tests for runtime_diagnostics.

Mocks Ollama HTTP + `ollama ps` + `nvidia-smi`. Never requires a real GPU or
running Ollama server.
"""
from __future__ import annotations

import httpx
import pytest

from jarvis.models.runtime_diagnostics import (
    classify_processor,
    get_ollama_running_models,
    get_gpu_info,
    get_runtime_snapshot,
)
from jarvis.config.settings import settings


@pytest.fixture(autouse=True)
def _no_platform_probes(monkeypatch):
    """Keep snapshot tests hermetic: never call the real docker/wsl CLI.

    Snapshot tests exercise the Ollama/GPU path; Docker/WSL platform probes
    are replaced with a benign empty result so no subprocess ever runs.
    """
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(
        rd,
        "get_docker_wsl_diagnostics",
        lambda: {
            "docker": {
                "cli_available": False,
                "daemon_reachable": False,
                "containers": [],
                "disk_usage": {},
                "warnings": [],
            },
            "wsl": {
                "available": False,
                "wsl2_enabled": False,
                "default_distro": None,
                "distributions": [],
                "config_present": False,
                "config_keys": {},
                "warnings": [],
            },
        },
    )
    import jarvis.config.runtime_capabilities as rc

    monkeypatch.setattr(rc.settings, "runtime_mode", "local")
    monkeypatch.setattr(rc.settings, "postgres_dsn", "")


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


def test_classify_ninety_gpu_is_honestly_partial():
    # We must never claim 100% GPU unless the source confirms it.
    assert classify_processor("90% GPU") == "Partial CPU/GPU"


# ---------------------------------------------------------------------------
# check_ollama_reachable / running models (HTTP mocked)
# ---------------------------------------------------------------------------


def _mock_resp(status=200, json_body=None):
    r = httpx.Response(status_code=status, json=json_body or {})
    return r


def test_running_models_parse(monkeypatch):
    monkeypatch.setattr(settings, "ollama_max_loaded_models", 1)
    body = {"models": [{"name": "qwen3:8b", "size": 5000000000}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _mock_resp(200, body))
    out, warns = get_ollama_running_models(base_url="http://x")
    assert len(out) == 1
    assert out[0]["name"] == "qwen3:8b"


def test_running_models_too_many_warns(monkeypatch):
    monkeypatch.setattr(settings, "ollama_max_loaded_models", 1)
    body = {"models": [{"name": "a"}, {"name": "b"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _mock_resp(200, body))
    out, warns = get_ollama_running_models(base_url="http://x")
    assert len(out) == 2
    assert any("2 models loaded" in w for w in warns)


def test_running_models_uses_get_request(monkeypatch):
    """Ollama 0.31 serves /api/ps on GET, so GET must be the primary call."""
    calls: list[tuple[str, str]] = []

    def _recording_get(url, **kwargs):
        calls.append(("get", url))
        return _mock_resp(404)

    monkeypatch.setattr(httpx, "get", _recording_get)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _mock_resp(200, {"models": []}))
    get_ollama_running_models(base_url="http://x")
    assert calls, "GET /api/ps must be attempted first"
    assert calls[0][1].endswith("/api/ps")


def test_running_models_falls_back_to_post_for_old_builds(monkeypatch):
    """Older Ollama carries errors only on GET; POST should rescue us."""
    def _boom_get(url, **kwargs):
        raise ConnectionError("method not allowed")

    monkeypatch.setattr(httpx, "get", _boom_get)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _mock_resp(200, {"models": [{"name": "old:1"}]}))
    out, warns = get_ollama_running_models(base_url="http://x")
    assert len(out) == 1
    assert out[0]["name"] == "old:1"


def test_running_models_non_200_warns(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _mock_resp(500))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _mock_resp(500))
    out, warns = get_ollama_running_models(base_url="http://x")
    assert out == []
    assert any("HTTP 500" in w for w in warns)


def test_running_models_malformed_body(monkeypatch):
    """Ollama returns junk shape → safe empty list, no exception."""
    def _malformed(url, **kwargs):
        r = httpx.Response(status_code=200)
        r._content = b"{not valid json"
        return r

    monkeypatch.setattr(httpx, "get", _malformed)
    out, warns = get_ollama_running_models(base_url="http://x")
    assert out == []


def test_ollama_unreachable_returns_false():
    import jarvis.models.runtime_diagnostics as rd

    res, warns = rd.check_ollama_reachable(base_url="http://127.0.0.1:1")
    assert res is False
    assert isinstance(warns, list)


# ---------------------------------------------------------------------------
# ollama ps parser
# ---------------------------------------------------------------------------


def _call_parser(stdout: str):
    """Parse `ollama ps` stdout directly via the module-level parser."""
    import jarvis.models.runtime_diagnostics as rd

    return rd._parse_ollama_ps(stdout), []


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


def test_parse_ollama_ps_no_status_column():
    """Ollama 0.31 drops STATUS but adds CONTEXT/UNTIL — must still parse."""
    stdout = (
        "NAME            ID              SIZE      PROCESSOR   UNTIL             CONTEXT\n"
        "qwen2.5-coder:7b  4c5a2f1b9e0d   5.2 GB     100% GPU    4 minutes ago     4096\n"
    )
    rows, _ = _call_parser(stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "qwen2.5-coder:7b"
    assert rows[0]["processor"] == "100% GPU"
    assert rows[0]["context"] == "4096"
    assert rows[0]["until"] == "4 minutes ago"
    # No STATUS column → safe empty value, not an exception.
    assert rows[0]["status"] == ""


def test_parse_ollama_ps_partial_offload_with_content():
    stdout = (
        "NAME          ID         SIZE    PROCESSOR       CONTEXT        UNTIL\n"
        "qwen3:8b      aaa111     5.2 GB  40%/60% CPU/GPU 4096/8192      2 minutes ago\n"
    )
    rows, _ = _call_parser(stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "qwen3:8b"
    assert rows[0]["processor"] == "40%/60% CPU/GPU"
    assert rows[0]["context"] == "4096/8192"
    assert rows[0]["until"] == "2 minutes ago"


def test_parse_ollama_ps_stray_line_skipped_with_minimal_columns():
    """A caption-like line after the header is not a data row."""
    stdout = "NAME            ID\nnot a model row\nqwen3:8b        abc123\n"
    rows, _ = _call_parser(stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "qwen3:8b"
    assert rows[0]["model_id"] == "abc123"


def test_parse_ollama_ps_no_header_returns_empty():
    rows, _ = _call_parser("not a header line\nqwen3:8b abc\n")
    assert rows == []


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


def test_snapshot_has_runtime_and_platform_blocks():
    snap = get_runtime_snapshot()
    assert snap["runtime"]["runtime_mode"] in ("local", "docker")
    assert snap["runtime"]["database_backend"] in ("sqlite", "postgresql")
    assert snap["runtime"]["vector_store_backend"] == "chroma_embedded"
    assert snap["runtime"]["task_backend"] == "in_process"
    assert snap["runtime"]["docker_detected"] is False
    assert snap["docker"]["daemon_reachable"] is False
    assert snap["wsl"]["available"] is False


def test_snapshot_reports_active_model_and_names_and_context(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd, "check_ollama_reachable", lambda base_url=None: (True, []))
    monkeypatch.setattr(rd, "get_ollama_running_models", lambda base_url=None: ([], []))
    monkeypatch.setattr(rd, "get_ollama_process_info", lambda: (
        [{"name": "qwen2.5-coder:7b", "size": "5.2 GB", "processor": "100% GPU",
          "context": "4096", "until": "4 minutes ago", "status": "", "model_id": "x"}], []
    ))
    monkeypatch.setattr(rd, "get_gpu_info", lambda: (None, []))
    snap = get_runtime_snapshot()
    assert snap["active_model"] == "qwen2.5-coder:7b"
    assert snap["model"] == "qwen2.5-coder:7b"
    assert snap["running_model_names"] == ["qwen2.5-coder:7b"]
    assert snap["context_length"] == 4096
    assert snap["running_models"][0]["name"] == "qwen2.5-coder:7b"


def test_snapshot_context_length_falls_back_to_setting(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd.settings, "ollama_context_length", 2048)
    monkeypatch.setattr(rd, "check_ollama_reachable", lambda base_url=None: (True, []))
    monkeypatch.setattr(rd, "get_ollama_running_models", lambda base_url=None: ([], []))
    monkeypatch.setattr(rd, "get_ollama_process_info", lambda: ([], []))
    monkeypatch.setattr(rd, "get_gpu_info", lambda: (None, []))
    snap = get_runtime_snapshot()
    assert snap["context_length"] == 2048
    assert snap["running_model_names"] == []


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
    assert snap["processor"] == "Partial CPU/GPU"
    assert any("larger than available dedicated VRAM" in r for r in snap["recommendations"])


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


# ---------------------------------------------------------------------------
# Container diagnostic warning (Docker honesty)
# ---------------------------------------------------------------------------


def test_probe_unavailable_detects_missing_tool():
    import jarvis.models.runtime_diagnostics as rd

    assert rd._probe_unavailable(["`ollama` CLI not found on PATH."], "ollama") is True
    assert rd._probe_unavailable(["`ollama` CLI not found on PATH."], "nvidia-smi") is False
    assert rd._probe_unavailable([], "ollama") is False


def test_container_warning_only_when_missing_tool(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd, "_in_container", lambda: True)
    warns: list[str] = []
    rd._append_container_diagnostic_warning(warns, cli_missing=False, gpu_missing=False)
    assert warns == []

    rd._append_container_diagnostic_warning(warns, cli_missing=True, gpu_missing=False)
    assert len(warns) == 1
    assert "`ollama` CLI" in warns[0]
    assert "container" in warns[0]


def test_container_warning_skipped_outside_container(monkeypatch):
    import jarvis.models.runtime_diagnostics as rd

    monkeypatch.setattr(rd, "_in_container", lambda: False)
    warns: list[str] = []
    rd._append_container_diagnostic_warning(warns, cli_missing=True, gpu_missing=True)
    assert warns == []
