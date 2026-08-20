"""Tests for the Phase 6 benchmark framework (jarvis.benchmark + jarvis-benchmark CLI).

Everything is mocked — no real Ollama, GPU, or cloud is ever contacted.
"""
from __future__ import annotations

import json

from jarvis.benchmark import (
    BenchmarkReport,
    resolve_benchmark_models,
    run_benchmark_suite,
)
from jarvis.benchmark.cli import main as benchmark_main
from jarvis.config.settings import settings


def _no_gpu() -> tuple[None, list[str]]:
    return None, ["nvidia-smi not found on PATH (GPU VRAM metrics unavailable)."]


def _no_process() -> tuple[list[dict], list[str]]:
    return [], ["`ollama` CLI not found on PATH."]


def _metrics() -> dict:
    return {"system_ram_used_mb": 6000, "cpu_percent": 20.0}


def _ok_invoke(name: str):
    return lambda prompt: "This is a fake benchmark response with enough words to estimate."


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


def test_result_schema_fields_present():
    report = run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    r = report.results[0].to_dict()
    for key in (
        "model", "prompt_type", "context_length", "prompt_tokens_estimate",
        "response_tokens_estimate", "latency_ms", "tokens_per_second",
        "processor_split", "gpu_name", "gpu_vram_total_mb", "gpu_vram_used_before_mb",
        "gpu_vram_used_peak_mb", "system_ram_before_mb", "system_ram_peak_mb",
        "cpu_percent_peak", "success", "error_category", "warning",
    ):
        assert key in r, f"missing schema field {key}"
    assert r["model"] == "qwen3:8b"
    assert r["context_length"] == 4096
    assert r["success"] is True
    assert r["prompt_tokens_estimate"] > 0


def test_report_json_schema():
    report = run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096, 6144],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    assert isinstance(report, BenchmarkReport)
    data = report.to_dict()
    for key in ("schema_version", "generated_at", "session_id", "models_tested",
                "context_sizes", "thresholds", "summary", "results"):
        assert key in data
    assert len(data["results"]) == 2
    assert data["summary"]["runs"] == 2


# ---------------------------------------------------------------------------
# Diagnostics degradation (never fail on missing hardware)
# ---------------------------------------------------------------------------


def test_nvidia_smi_missing_degrades_gracefully():
    report = run_benchmark_suite(
        models=[{"name": "m", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    r = report.results[0]
    assert r.success is True
    assert r.gpu_name is None
    assert r.gpu_vram_total_mb is None
    assert r.gpu_vram_used_before_mb is None
    assert r.gpu_vram_used_peak_mb is None


def test_malformed_ollama_ps_degrades_gracefully():
    def _malformed() -> tuple[list[dict], list[str]]:
        raise RuntimeError("malformed output")

    report = run_benchmark_suite(
        models=[{"name": "m", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_malformed,
        system_metrics_fn=_metrics,
    )
    r = report.results[0]
    assert r.success is True
    assert r.processor_split == "unknown"


def test_processor_split_honest_from_ollama_ps():
    def _process() -> tuple[list[dict], list[str]]:
        return [{"name": "qwen3:8b", "processor": "100% GPU", "size": "5.2 GB"}], []

    report = run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_process,
        system_metrics_fn=_metrics,
    )
    assert report.results[0].processor_split == "100% GPU"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_timeout_marks_failure(monkeypatch):
    monkeypatch.setattr(settings, "benchmark_max_latency_seconds", 1)
    monkeypatch.setattr(settings, "benchmark_min_gpu_utilization_percent", 0)
    monkeypatch.setattr(settings, "benchmark_max_cpu_ram_mb", 0)
    monkeypatch.setattr(settings, "benchmark_max_vram_percent", 0)

    def _slow(name):
        import time

        return lambda prompt: time.sleep(5) or "late"

    report = run_benchmark_suite(
        models=[{"name": "m", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_slow,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    r = report.results[0]
    assert r.success is False
    assert r.error_category == "timeout"


def test_model_failure_categorised():
    def _boom(name):
        def invoke(prompt):
            raise RuntimeError("model 'qwen3:8b' not found, try pulling it first")

        return invoke

    report = run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_boom,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    r = report.results[0]
    assert r.success is False
    assert r.error_category == "model_not_found"


def test_ollama_unavailable_categorised():
    def _boom(name):
        return lambda prompt: (_ for _ in ()).throw(
            ConnectionError("connection refused: http://localhost:11434")
        )

    report = run_benchmark_suite(
        models=[{"name": "m", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_boom,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    r = report.results[0]
    assert r.success is False
    assert r.error_category == "ollama_unavailable"


# ---------------------------------------------------------------------------
# Cloud safety
# ---------------------------------------------------------------------------


def test_cloud_blocked_by_default():
    models = resolve_benchmark_models()
    names = [m["name"] for m in models]
    # Only configured local models appear — never a cloud chain id.
    assert names, "expected at least the general model"
    for m in models:
        assert "claude" not in m["name"]
        assert "openai" not in m["name"]
        assert "gemini" not in m["name"]


def test_benchmark_module_never_imports_openrouter():
    """The benchmark pipeline has no cloud code path anywhere in its source."""
    import inspect

    from jarvis.benchmark import cli as cli_mod
    from jarvis.benchmark import core as core_mod
    from jarvis.benchmark import performance as perf_mod

    for module in (core_mod, cli_mod, perf_mod):
        src = inspect.getsource(module)
        assert "openrouter_client" not in src
        assert "openrouter_api_key" not in src


def test_allow_cloud_flag_is_noop_for_suite(monkeypatch):
    # run_benchmark_suite has no cloud code path at all; the CLI flag only
    # exists for parity. We assert a normal local suite still runs when the
    # flag is on through the CLI path (reachable Ollama mocked).
    import jarvis.benchmark.cli as cli

    def _fake_suite(**kwargs):
        return run_benchmark_suite(
            models=[{"name": "qwen3:8b", "prompt_type": "general"}],
            context_sizes=[4096],
            invoke_factory=_ok_invoke,
            gpu_info_fn=_no_gpu,
            process_info_fn=_no_process,
            system_metrics_fn=_metrics,
        )

    monkeypatch.setattr(cli, "ollama_available", lambda base_url=None: (True, []))
    monkeypatch.setattr(cli, "run_benchmark_suite", _fake_suite)
    code = benchmark_main(["--quick", "--allow-cloud"])
    assert code == 0


# ---------------------------------------------------------------------------
# Report output formats
# ---------------------------------------------------------------------------


def test_report_json_output():
    report = run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    parsed = json.loads(report.to_json())
    assert parsed["schema_version"] == "1.0"
    assert len(parsed["results"]) == 1


def test_report_markdown_output():
    report = run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    md = report.to_markdown()
    assert "qwen3:8b" in md
    assert "| model | prompt_type" in md
    assert "hardware-specific" in md.lower()


# ---------------------------------------------------------------------------
# Temporary session isolation + no production data changes
# ---------------------------------------------------------------------------


def test_session_id_is_temporary():
    from jarvis.benchmark.core import new_benchmark_session_id

    a = new_benchmark_session_id()
    b = new_benchmark_session_id()
    assert a != b
    assert a.startswith("bench-")


def test_no_production_data_changes():
    from jarvis.persistence import create_all
    from jarvis.persistence.engine import reset_engine_for_tests
    from jarvis.persistence.repo import repos

    reset_engine_for_tests()
    create_all()
    run_benchmark_suite(
        models=[{"name": "qwen3:8b", "prompt_type": "general"}],
        context_sizes=[4096],
        invoke_factory=_ok_invoke,
        gpu_info_fn=_no_gpu,
        process_info_fn=_no_process,
        system_metrics_fn=_metrics,
    )
    assert repos.sessions.list() == []
    assert repos.tasks.list_for_session("benchmark") == []
    assert repos.approvals.get_pending("benchmark") is None


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def test_cli_fails_structurally_when_ollama_down(monkeypatch, capsys):
    import jarvis.benchmark.cli as cli

    monkeypatch.setattr(cli, "ollama_available", lambda base_url=None: (False, ["Ollama unreachable"]))
    code = benchmark_main(["--quick"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Ollama is not reachable" in out
    assert "Suggested action" in out


def test_cli_quick_writes_json_and_markdown(monkeypatch, tmp_path):
    import jarvis.benchmark.cli as cli

    monkeypatch.setattr(cli, "ollama_available", lambda base_url=None: (True, []))
    monkeypatch.setattr(
        cli,
        "run_benchmark_suite",
        lambda **kw: run_benchmark_suite(
            models=[{"name": "qwen3:8b", "prompt_type": "general"}],
            context_sizes=[4096],
            invoke_factory=_ok_invoke,
            gpu_info_fn=_no_gpu,
            process_info_fn=_no_process,
            system_metrics_fn=_metrics,
        ),
    )
    out_json = tmp_path / "benchmark.json"
    out_md = tmp_path / "benchmark.md"
    code = benchmark_main(
        ["--quick", "--output", str(out_json), "--markdown", str(out_md)]
    )
    assert code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert json.loads(out_json.read_text(encoding="utf-8"))["summary"]["runs"] == 1


def test_resolve_models_respects_strong_local_flag(monkeypatch):
    monkeypatch.setattr(settings, "general_model", "qwen3:8b")
    monkeypatch.setattr(settings, "coding_model", "qwen2.5-coder:7b")
    monkeypatch.setattr(settings, "strong_local_model", "qwen3:14b")
    monkeypatch.setattr(settings, "use_strong_local", True)
    names = [m["name"] for m in resolve_benchmark_models()]
    assert names == ["qwen3:8b", "qwen2.5-coder:7b", "qwen3:14b"]

    monkeypatch.setattr(settings, "use_strong_local", False)
    names = [m["name"] for m in resolve_benchmark_models()]
    assert "qwen3:14b" not in names


def test_resolve_models_dedupes_identical_names(monkeypatch):
    monkeypatch.setattr(settings, "general_model", "qwen3:8b")
    monkeypatch.setattr(settings, "coding_model", "qwen3:8b")
    monkeypatch.setattr(settings, "strong_local_model", "qwen3:8b")
    monkeypatch.setattr(settings, "use_strong_local", True)
    names = [m["name"] for m in resolve_benchmark_models()]
    assert names == ["qwen3:8b"]